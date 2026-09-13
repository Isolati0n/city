#!/usr/bin/env python3
"""fold: apply a house's overlayfs upper directory to a base image and
pack the result as a new content-addressed brick.

A fold is a privileged operation: it mounts the base read-only and reads
trusted.overlay.*, both needing CAP_SYS_ADMIN. That is the shape of the
operation, not an incidental cost of one check, and it settles where a
fold runs -- the bakery, the only privileged non-TCB context.

The refusal when the capability is absent is not caution. A fold that
merged a directory the house meant to replace produces an image that
mounts, boots and behaves subtly wrong, and nothing in the bytes records
that a marker was unreadable. The operation destroys the evidence of its
own defect. There is no --force.

WHAT THE FOLD MUST PRESERVE, measured rather than assumed by packing a
tree holding each and reading it back off the mount: file modes,
directory modes, symlinks (to files AND to directories), hard links,
and xattrs in every namespace but the overlay's own. Any of these
missing from a folded image is the fold losing it, never the format
declining to carry it.

DO NOT READ THE LIST AS COMPLETE. It has grown twice after being
written down as measured -- user.* xattrs, then security.* -- each time
found by a reviewer and not by the list. `test_fold.py`'s oracle
compares a named set of fields against the kernel's own overlay view,
which is stronger than a list and is still not the same as "everything":
a property in neither the list nor the census is invisible to both.
When you add one, add it to the census first.

Deliberately outside it: uid, gid and timestamps, which `mkbrick.pack`
flattens with --force-uid/--force-gid and -T 0. A fold that preserved
them would produce an image disagreeing with every other brick.

The first version of this tool lost every one of them except symlinks
to files, and its suite was green, because nothing read a byte inside
an image it produced.

THE PRECONDITION IS THE CALLER'S, AND NOTHING HERE IMPLEMENTS IT. A
fold of a LIVE layer is the silent-wrong-artifact case -- the image
mounts, boots and holds a half-written file, and nothing in the bytes
says so. `CLAUDE.md` records the rule (a fold establishes that no
SUPERVISOR exists for the unit, not that no house process is running)
as waiting on the helper that does not exist yet. This module is the
fold engine; it does not check, and an operator pointing it at a
running house gets no warning. Said here because the argument for
having no --force is the same argument, and a reader meeting one
without the other would reasonably assume this file makes the check.
"""
from __future__ import annotations
import argparse, errno, json, os, shutil, stat, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mkbrick


class MergeError(Exception):
    pass


# --- the overlay encodings ------------------------------------------------

def is_whiteout(st):
    return (stat.S_ISCHR(st.st_mode)
            and os.major(st.st_rdev) == 0
            and os.minor(st.st_rdev) == 0)


def is_opaque(path):
    """(opaque, error). Linux spells the no-attribute case ENODATA;
    ENOATTR is the BSD name and does not exist in CPython here.

    ANY error is an error, not just EPERM. The earlier version raised
    only on EPERM and returned (False, str(e)) otherwise, which
    plan_merge then ignored -- so an upper on a filesystem with no
    trusted-xattr support (EOPNOTSUPP) read as "no directory is opaque"
    and the fold merged every directory the house meant to replace,
    silently. That is the exact outcome this module's docstring says
    the refusal exists to prevent, reachable through the errno the
    refusal did not name."""
    try:
        v = os.getxattr(path, "trusted.overlay.opaque")
    except OSError as e:
        if e.errno == errno.ENODATA:
            return False, None
        if e.errno in (errno.EPERM, errno.EACCES):
            return False, "EPERM"
        return False, f"{errno.errorcode.get(e.errno, e.errno)}: {e.strerror}"
    return v == b"y", None


def can_trust_overlay_markers(probe_dir):
    """(usable, why). WRITES a marker and reads it back, in a temporary
    directory it creates under probe_dir and removes.

    IT WAS A READ-PROBE AND THE READ-PROBE CANNOT WORK. Measured: the
    kernel hides trusted.* from a reader without CAP_SYS_ADMIN by
    reporting them ABSENT, not forbidden -- a directory carrying
    trusted.overlay.opaque=y answers ENODATA rather than EPERM. So
    getxattr on an absent marker returns ENODATA whatever the caller's
    capability, the old probe mapped that to "yes, I can read", and its
    EPERM branch was unreachable on Linux. It had never once refused for
    a real reason; every check that saw it refuse had replaced the
    function with a lambda.

    Worse than unreachable: the same errno collapse means is_opaque
    reports "not opaque" for a marker it merely cannot see, which is the
    silent wrong artifact the whole tool is built to refuse.

    setxattr is the discriminating operation -- EPERM without the
    capability, and it also fails on a filesystem that cannot carry
    trusted.* at all, which is the other way this can go wrong. The
    write lands in a temporary directory and never in `upper`: a probe
    must not write where an assertion reads.

    What was refusing until now is the base mount, which needs the same
    capability and fails first. A claim standing beside a mechanism
    already doing the work is this repository's characteristic defect,
    and this one was inside the check written to prevent it."""
    try:
        d = tempfile.mkdtemp(prefix=".fold-probe-", dir=probe_dir)
    except OSError as e:
        return False, f"cannot create a probe directory in {probe_dir}: {e}"
    try:
        os.setxattr(d, "trusted.overlay.opaque", b"y")
        if os.getxattr(d, "trusted.overlay.opaque") != b"y":
            return False, "a trusted.overlay.* marker did not read back"
    except OSError as e:
        if e.errno in (errno.EPERM, errno.EACCES):
            return False, "EPERM"
        return False, f"{errno.errorcode.get(e.errno, e.errno)}: {e.strerror}"
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return True, None


# Overlay bookkeeping, which belongs to the layer and not to the image.
# Everything else is the brick's and is carried.
_OVERLAY_XATTR_PREFIXES = ("trusted.overlay.", "user.overlay.")


def _copy_xattrs(src, dst):
    """EVERY xattr except the overlay's own, and the namespace is the
    whole lesson here.

    This carried `user.*` only, for one round, after `user.*` was itself
    found as a property a hand-written list of four had missed. The next
    round found `security.capability` the same way: `setcap
    cap_net_raw+ep` on a binary survives mkbrick.pack, is stripped by
    the fold, and the folded image is valid, content-addressed and
    silently unprivileged. Any distro rootfs ships that on ping and
    newuidmap; security.selinux and security.SMACK64 are the same class.

    So the filter is a DENY-list of the namespaces that must not cross
    -- trusted.overlay.* AND user.overlay.*, both of which describe the
    layer rather than the image -- instead of an allow-list of the
    namespaces somebody happened to think of. An allow-list here was
    wrong twice; the first deny-list named one namespace and let
    user.overlay.opaque be baked into a sealed brick. Each found by a
    reviewer, none by the list."""
    try:
        names = os.listxattr(src)
    except OSError as e:
        if e.errno in (errno.ENOTSUP, errno.EOPNOTSUPP):
            return
        raise MergeError(f"cannot list xattrs on {src}: {e}")
    for n in names:
        if n.startswith(_OVERLAY_XATTR_PREFIXES):
            continue
        try:
            os.setxattr(dst, n, os.getxattr(src, n))
        except OSError as e:
            # LOUD. A fold that silently drops an xattr produces exactly
            # the artifact this tool refuses: an image that mounts, boots
            # and is wrong in a way nothing in the bytes records.
            raise MergeError(f"cannot carry {n} from {src} to {dst}: {e}")


# THE MARKERS THE FOLD DOES NOT UNDERSTAND. It read trusted.overlay.opaque
# and treated that as the whole encoding. It is not: with redirect_dir=on a
# renamed directory carries trusted.overlay.redirect naming its old path,
# and with metacopy=on a chmod'd file carries trusted.overlay.metacopy and
# a body of zeros whose real content lives in the lower. Measured by
# `control`: the redirect case drops the renamed directory's contents, and
# the metacopy case writes 4096 zeros over 4096 bytes of real file -- both
# exit 0 with a valid content-addressed image, which is this tool's own
# definition of the artifact it exists not to produce.
#
# REFUSED, not handled. Handling redirect means implementing the kernel's
# rename resolution and handling metacopy means reading through to the
# lower for content; both are real work and neither is this change. A
# refusal is loud and an unrecognised marker is exactly where silence
# costs most.
#
# This is not hypothetical on a kernel that defaults them off: a layer is
# durable and portable, and this machine reports redirect_always_follow=Y,
# so a marker acquired anywhere is honoured by a mount that never asked
# for it.
_UNHANDLED_MARKERS = ("trusted.overlay.redirect", "trusted.overlay.metacopy",
                      "trusted.overlay.whiteout")


def unhandled_markers(path):
    """THERE ARE TWO MARKER NAMESPACES, and this read one for a round.
    A `userxattr` overlay -- what every rootless overlay uses -- writes
    `user.overlay.opaque` instead of the trusted one. Measured on a real
    userxattr mount: the house replaced /etc, the fold resurrected its
    base children, exit 0, valid content-addressed image, and the marker
    was BAKED INTO the sealed brick because the xattr deny-list named
    only `trusted.overlay.`.

    Worse than portability: `user.overlay.*` needs no privilege, and the
    kernel does not escape it the way it escapes a house-set
    `trusted.overlay.opaque` (which becomes
    `trusted.overlay.overlay.opaque`). So under nw-sup's exact mount
    options a house can plant one in its own layer and have the fold
    carry it into a brick a later userxattr mount honours. Refused in
    both namespaces now, and neither is carried. `control`.

    `trusted.overlay.whiteout` joins them: a zero-byte file carrying it
    is a deletion to the kernel and a plain copy to the fold, so the
    base's file came back as zero bytes instead of being removed."""
    try:
        names = set(os.listxattr(path))
    except OSError:
        return []
    found = [n for n in _UNHANDLED_MARKERS if n in names]
    found += sorted(n for n in names if n.startswith("user.overlay."))
    return found


# --- the merge ------------------------------------------------------------

class MergePlan:
    def __init__(self):
        self.deleted = set()
        self.replaced = set()
        self.dirs = []      # (abs, rel) in walk order, parents before children
        self.copied = []    # (abs, rel) non-directory entries


def plan_merge(upper, opaque_check=True):
    plan = MergePlan()

    def walk(dir_abs, rel):
        try:
            entries = sorted(os.listdir(dir_abs))
        except OSError as e:
            raise MergeError(f"cannot list {dir_abs}: {e}")
        for name in entries:
            child_abs = os.path.join(dir_abs, name)
            child_rel = os.path.join(rel, name) if rel else name
            try:
                st = os.lstat(child_abs)
            except OSError as e:
                raise MergeError(f"cannot stat {child_abs}: {e}")
            if is_whiteout(st):
                plan.deleted.add(child_rel)
                continue
            if opaque_check and not stat.S_ISLNK(st.st_mode):
                bad = unhandled_markers(child_abs)
                if bad:
                    raise MergeError(
                        f"{child_abs} carries {', '.join(bad)}, which this "
                        f"fold does not implement. Folding it would produce "
                        f"an image that mounts and boots and is wrong -- a "
                        f"renamed directory losing its contents, or a file "
                        f"of zeros where the layer holds only its mode.")
            if stat.S_ISDIR(st.st_mode):
                opaque, err = is_opaque(child_abs)
                if err and opaque_check:
                    raise MergeError(
                        f"cannot read trusted.overlay.opaque on {child_abs} "
                        f"({err})")
                if opaque:
                    plan.replaced.add(child_rel)
                # RECORDED EVEN WHEN EMPTY. A directory used to reach the
                # plan only as the parent of a copied file, so `mkdir -p
                # /var/lib/app` and save produced an image without it and
                # the fold reported success. The kernel does NOT mark a
                # newly created directory opaque -- measured on a real
                # overlay -- so `replaced` was never going to carry it.
                plan.dirs.append((child_abs, child_rel))
                walk(child_abs, child_rel)
                continue
            plan.copied.append((child_abs, child_rel))

    opaque_root, err_root = is_opaque(upper)
    if err_root and opaque_check:
        raise MergeError(
            f"cannot read trusted.overlay.opaque on the upper root ({err_root})")
    if opaque_root:
        plan.replaced.add("")
    walk(upper, "")
    return plan


def _copy_entry(src, dst, where, links=None, modes=None):
    """One entry, type-aware. `where` names the tree the entry came from,
    because the refusal used to say "in upper" for a path under the base
    mount and so blamed the house for what the brick shipped.

    `links` maps (st_dev, st_ino) -> the first path written, so a hard
    link in the source becomes a hard link in the output rather than two
    independent copies. `modes` collects (path, mode) to be applied after
    the whole tree exists: chmod'ing a 0500 directory on the way past
    makes its own children unwritable."""
    st = os.lstat(src)
    if stat.S_ISLNK(st.st_mode):
        if os.path.lexists(dst):
            os.unlink(dst)
        os.symlink(os.readlink(src), dst)
        if modes is not None:
            # A SYMLINK CANCELS ANY MODE RECORDED FOR THIS PATH. Step 1
            # may have recorded a regular file here; if the house then
            # replaced it with a symlink, the stale entry chmod'd THROUGH
            # the link -- a crash on a dangling one, a silent mode change
            # on the target, and, for an absolute link, a chmod of a file
            # outside the merged tree on the fold host. `control` found
            # all three, each one input away from the fixture.
            modes[dst] = None
        return
    if stat.S_ISDIR(st.st_mode):
        os.makedirs(dst, exist_ok=True)
        if modes is not None:
            modes[dst] = stat.S_IMODE(st.st_mode)
        _copy_xattrs(src, dst)
        return
    if not stat.S_ISREG(st.st_mode):
        raise MergeError(
            f"unhandled non-regular file in {where}: {src} "
            f"(mode {stat.S_IFMT(st.st_mode):#o}); fifos, sockets and device "
            f"nodes are not folded")
    key = (st.st_dev, st.st_ino)
    if links is not None and st.st_nlink > 1 and key in links:
        if os.path.lexists(dst):
            os.unlink(dst)
        os.link(links[key], dst)
        return
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        shutil.copyfileobj(fsrc, fdst, 1 << 20)
    _copy_xattrs(src, dst)
    if modes is not None:
        modes[dst] = stat.S_IMODE(st.st_mode)
    else:
        os.chmod(dst, stat.S_IMODE(st.st_mode))
    if links is not None and st.st_nlink > 1:
        links[key] = dst


def _remove(path):
    """Steps 2, 3 and 4b all call this. The side-effect-freedom below is
    about step 2 specifically, whose work is discarded under a replaced
    directory: it must stay free of side effects, or step 2 has to move
    after step 3. (This said "Called from step 2" while more than one
    caller existed -- a present-tense claim about the code beside it, in
    the file the diff adds. Its replacement then gave a COUNT of the
    callers, which another hunk of the same diff invalidated by adding
    one. `claims`, twice.)"""
    if os.path.islink(path) or os.path.isfile(path):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    elif os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def _copy_tree(lower, merged, modes):
    """Step 1, written as its own recursion rather than os.walk.

    os.walk puts a symlink-TO-A-DIRECTORY in `dirnames`, where the old
    step 1 skipped it with `if os.path.islink(src): continue` and nothing
    else copied it -- so `/bin -> usr/bin`, which every distro image has,
    vanished from the folded brick while a symlink to a FILE survived.
    Recursing by lstat type has no such category to fall through."""
    links = {}

    def walk(src_dir, dst_dir):
        for name in sorted(os.listdir(src_dir)):
            src = os.path.join(src_dir, name)
            dst = os.path.join(dst_dir, name)
            st = os.lstat(src)
            # No S_ISLNK guard here: S_ISDIR on an LSTAT is already false
            # for a symlink-to-a-directory. One stood here and could never
            # change the branch, while reading as the fix for the defect the
            # comment above describes -- the fix is recursing on lstat at
            # all, instead of os.walk, which puts such a symlink in
            # `dirnames` where step 1 skipped it. `control`.
            if stat.S_ISDIR(st.st_mode):
                os.makedirs(dst, exist_ok=True)
                modes[dst] = stat.S_IMODE(st.st_mode)
                _copy_xattrs(src, dst)
                walk(src, dst)
                continue
            _copy_entry(src, dst, "the base image", links=links, modes=modes)

    os.makedirs(merged, exist_ok=True)
    modes[merged] = stat.S_IMODE(os.lstat(lower).st_mode)
    _copy_xattrs(lower, merged)
    walk(lower, merged)


def _apply_root(upper, merged, modes):
    """THE MERGED ROOT'S OWN MODE AND XATTRS COME FROM THE UPPER, which
    is what the kernel shows the house: measured on a real overlay with
    lower 0751 and upper 0700, the merged root reads 0700 and carries
    the upper's xattrs, not the lower's. Step 1 seeded them from the
    LOWER and nothing compared them, so both statements were deletable
    with the whole suite green. `claims` found the gap; the direction is
    measured rather than reasoned.

    A CONSEQUENCE FOR THE STAGER, recorded rather than worked around:
    the upper root always exists, so `/`'s mode in a folded image is
    whatever `tools/stage-layers.py` created `upper` with, not whatever
    the brick was packed with. Those agree today because both are 0755.
    If they ever diverge, folding a house that touched nothing would
    change `/`'s mode -- and the fix belongs in the stager, which should
    create `upper` with the brick's root mode, not here, because here it
    would mean disagreeing with what the house saw."""
    modes[merged] = stat.S_IMODE(os.lstat(upper).st_mode)
    _copy_xattrs(upper, merged)


def apply_merge(lower, plan, merged, upper_root=None):
    # Modes are applied last, together: see _copy_entry.
    modes = {}

    # 1. Copy lower.
    _copy_tree(lower, merged, modes)

    # 2. Deletes. A whiteout under a directory step 3 will replace is
    #    applied here and discarded. Replace is total, so that is a
    #    no-op rather than composition; see _remove.
    for rel in sorted(plan.deleted, key=lambda p: p.count(os.sep), reverse=True):
        _remove(os.path.join(merged, rel))

    # 3. Replaces. The directory itself remains: a house that emptied
    #    /etc meant /etc to exist and be empty, not to be absent.
    #
    #    THE SORT ORDER IS UNPINNED AND MAY BE WRONG. `control` read the
    #    reverse as a defect on a hand-built input where a directory and
    #    its child were both opaque, which does give a different answer
    #    from the kernel's. But two sequences that should produce a
    #    nested marker on a real overlay produce only the outer one --
    #    the kernel needs none on a recreated child, since nothing below
    #    it is visible -- so no reachable input has two NESTED entries
    #    here and the order never applies. Siblings are routine and are
    #    not the claim: three directories each removed and recreated give
    #    replaced=['etc','opt','var'], and their order does not matter
    #    because none contains another. Three reviewers have now failed
    #    to produce a nested marker independently, one finding the
    #    mechanism (EXDEV: with redirect_dir off the kernel refuses a
    #    lower-backed directory rename) and one finding that a house
    #    cannot forge the marker either, since the kernel escapes a
    #    house-set trusted.overlay.opaque into the upper as
    #    trusted.overlay.overlay.opaque. Absence of a sequence is not
    #    proof and all three said so. Left as it stands rather than
    #    changed on a cause that is not the cause: the loss `control`
    #    demonstrated was `plan.dirs` not existing, fixed above.
    for rel in sorted(plan.replaced, key=lambda p: p.count(os.sep), reverse=True):
        p = os.path.join(merged, rel) if rel else merged
        if not os.path.isdir(p) or os.path.islink(p):
            # A DIRECTORY REPLACING A FILE. `rm f; mkdir f` in the house
            # leaves an opaque directory whose counterpart in the base is
            # a regular file, and os.makedirs(exist_ok=True) does not
            # tolerate a non-directory: it raised FileExistsError out of
            # main()'s `except MergeError` as a traceback. `control`.
            _remove(p)
            os.makedirs(p, exist_ok=True)
            continue
        for name in sorted(os.listdir(p)):
            _remove(os.path.join(p, name))

    # 4a. Directories the house has, created after the replaces so a
    #     replaced parent does not sweep its own children away.
    for src, rel in plan.dirs:
        dst = os.path.join(merged, rel)
        # THE SAME GUARD AS STEP 3, and it was missing here. Step 3 got
        # it when a FileExistsError escaped main(); step 4a has the
        # identical makedirs on a path step 1 may hold as a regular file
        # or a SYMLINK, and it is reachable whenever the upper's
        # directory is not opaque -- which the kernel only guarantees for
        # a layer matched to its own base. Nothing binds --base to
        # --layer, so an ordinary rebase reaches it: the crash on a
        # regular file, and worse, on a symlink-to-a-directory
        # makedirs(exist_ok=True) SUCCEEDS THROUGH the link and the
        # layer's file lands outside where the house put it, exit 0,
        # valid image. `control`.
        if os.path.lexists(dst) and (not os.path.isdir(dst)
                                     or os.path.islink(dst)):
            _remove(dst)
        os.makedirs(dst, exist_ok=True)
        modes[dst] = stat.S_IMODE(os.lstat(src).st_mode)
        _copy_xattrs(src, dst)

    # 4b. Files.
    links = {}
    _apply_root(upper_root, merged, modes) if upper_root else None
    for src, rel in plan.copied:
        dst = os.path.join(merged, rel)
        parent = os.path.dirname(dst)
        if parent:
            # Same as step 4a: a parent that is a symlink to a directory
            # would swallow this file into the link's target.
            if os.path.lexists(parent) and (not os.path.isdir(parent)
                                            or os.path.islink(parent)):
                _remove(parent)
            os.makedirs(parent, exist_ok=True)
        _remove(dst)
        _copy_entry(src, dst, "the layer", links=links, modes=modes)

    # Applied last, in the order recorded, so a step-4 file's mode wins
    # over the step-1 copy it replaced. A path recorded in step 1 and then
    # removed by a whiteout in step 2 or a replace in step 3 is simply
    # gone; its mode is not an error to skip.
    for path, mode in modes.items():
        if mode is None or not os.path.exists(path) or os.path.islink(path):
            continue
        os.chmod(path, mode)


# --- packing --------------------------------------------------------------

def write_sidecar(path, image_hash, base_hash, name, layer_id, opaque_check_ran):
    doc = {"schema_version": 1, "image_hash": image_hash, "name": name,
           "derived_from": base_hash, "baked_by": "fold", "layer_id": layer_id,
           "opaque_check": "ran" if opaque_check_ran else "skipped"}
    with open(path, "w") as f:
        json.dump(doc, f, indent=2, sort_keys=True)


# --- the fold -------------------------------------------------------------

class FoldResult:
    def __init__(self):
        self.image_hash = None
        self.out_path = None
        self.sidecar_path = None
        self.opaque_check_ran = None
        self.base_hash = None


def _erofs_supported():
    try:
        with open("/proc/filesystems") as f:
            return "erofs" in f.read()
    except OSError:
        return False


def fold(base_img, layer_dir, out_dir, name, opaque_check=True):
    """layer_dir is the layer, not its upper. Deriving upper here removes
    the basename-of-dirname reconstruction, which yielded "upper" as the
    layer id whenever the caller passed a trailing slash."""
    r = FoldResult()
    layer_id = os.path.basename(os.path.normpath(layer_dir))
    upper = os.path.join(layer_dir, "upper")
    if not os.path.isdir(upper):
        raise MergeError(f"no upper directory in {layer_dir}")
    # The capability probe runs before anything reads the base. A fold
    # that is going to refuse should refuse before hashing a file it will
    # not use, and with the base missing as well the refusal was
    # unreachable -- hash_file raised first and the caller saw
    # FileNotFoundError where the real answer was EPERM.
    can, why = can_trust_overlay_markers(layer_dir)
    if not can and opaque_check:
        raise MergeError(
            f"cannot read trusted.overlay.* ({why}) and opaque_check is "
            f"enabled. The fold would merge a replaced directory without "
            f"error, and nothing in the resulting bytes would record that a "
            f"marker was unreadable.")
    r.opaque_check_ran = can

    try:
        r.base_hash = mkbrick.sha256_file(base_img)
    except OSError as e:
        raise MergeError(f"cannot read base image {base_img}: {e}")

    with tempfile.TemporaryDirectory(prefix="fold-") as tmp:
        mount = os.path.join(tmp, "base"); os.makedirs(mount)
        merged = os.path.join(tmp, "merged"); os.makedirs(merged)
        if not _erofs_supported():
            raise MergeError("the kernel has no erofs driver "
                             "(/proc/filesystems lists none)")
        mnt = subprocess.run(["mount", "-o", "ro,loop", base_img, mount],
                             capture_output=True, text=True)
        if mnt.returncode != 0:
            raise MergeError(f"cannot mount base image, and the kernel does "
                             f"have erofs, so this is most likely a missing "
                             f"CAP_SYS_ADMIN: {mnt.stderr.strip()}")
        try:
            plan = plan_merge(upper, opaque_check=opaque_check)
            apply_merge(mount, plan, merged, upper_root=upper)
        finally:
            subprocess.run(["umount", mount], check=False)

        # THE PACKER IS mkbrick's, NOT A SECOND COPY. It holds the one
        # EROFS_FLAGS list, the temp-then-rename, the 0600 and the named
        # refusal when mkfs.erofs is absent -- which fold's own pack()
        # reached as a bare FileNotFoundError. Content addressing comes
        # with it WITHIN mkbrick: the name it returns is the digest of the
        # bytes it just wrote. That is narrower than "unwritable", which
        # is what this comment claimed for one round -- `fold` can still
        # misreport the hash, and can still move the file afterwards, and
        # `claims` wrote both mutations. Both are CAUGHT, by the two
        # assertions in the oracle that compare the stored name to the
        # digest of the stored bytes. Which is a good outcome and not the
        # one claimed: "unwritable" is an argument for deleting those two
        # assertions, and they are what does the work.
        try:
            digest, out_path = mkbrick.pack(merged, out_dir, quiet=True)
        except SystemExit as e:
            raise MergeError(str(e))

        r.image_hash = digest
        r.out_path = out_path
        r.sidecar_path = os.path.join(out_dir, digest + ".meta")
        # A second fold under a different --name keeps the first name,
        # because the sidecar is not rewritten.
        if not os.path.exists(r.sidecar_path):
            write_sidecar(r.sidecar_path, digest, r.base_hash, name, layer_id,
                          r.opaque_check_ran)
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fold")
    ap.add_argument("--base", required=True)
    ap.add_argument("--layer", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True)
    # NO --no-opaque-check, AND NO OTHER WAY PAST THE REFUSAL. There was
    # one, in this same function, under a module docstring saying "There
    # is no --force", and it bypassed both refusals: `control` fed it the exact input
    # check_a_filesystem_that_cannot_carry_the_marker_is_refused exists
    # for and got an accepted plan with every directory read as
    # non-opaque. Neither direction of the flag was tested. `opaque_check`
    # survives as a PARAMETER because the tree-level checks call
    # plan_merge directly with no privileges at all, which is the property
    # worth keeping; nothing an operator can type reaches it.
    a = ap.parse_args(argv)
    try:
        r = fold(a.base, a.layer, a.out, a.name)
    except MergeError as e:
        print(f"fold: {e}", file=sys.stderr)
        return 1
    print(r.image_hash)
    if not r.opaque_check_ran:
        print("# opaque_check=skipped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
