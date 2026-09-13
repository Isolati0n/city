#!/usr/bin/env python3
"""Self-test for fold. Exit 0 all pass, 1 any fail, 2 any skip and none fail.
A skip is not a pass."""
import json, os, shutil, stat, subprocess, sys, tempfile, traceback
import fold as fd
import mkbrick

class SelfTestFailure(Exception): pass
class Skip(Exception): pass
def expect(c, m):
    if not c: raise SelfTestFailure(m)
CHECKS = []
def check(fn): CHECKS.append(fn); return fn

class Tree:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="fold-test-")
        self.lower = os.path.join(self.root, "lower")
        self.layer = os.path.join(self.root, "layer")
        self.upper = os.path.join(self.layer, "upper")
        self.merged = os.path.join(self.root, "merged")
        self.out = os.path.join(self.root, "out")
        for d in (self.lower, self.upper, self.merged, self.out):
            os.makedirs(d)
    def file(self, base, rel, content=b"x"):
        p = os.path.join(base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(content); return p
    def dir(self, base, rel):
        p = os.path.join(base, rel); os.makedirs(p, exist_ok=True); return p
    def whiteout(self, base, rel):
        p = os.path.join(base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        os.mknod(p, stat.S_IFCHR | 0o000, os.makedev(0, 0)); return p
    def opaque(self, base, rel):
        p = self.dir(base, rel)
        os.setxattr(p, "trusted.overlay.opaque", b"y"); return p
    def cleanup(self): shutil.rmtree(self.root, ignore_errors=True)

def merge(t, opaque_check=True):
    plan = fd.plan_merge(t.upper, opaque_check=opaque_check)
    fd.apply_merge(t.lower, plan, t.merged)
    return plan

def ls(p):
    try: return sorted(os.listdir(p))
    except FileNotFoundError: return None

# --- whiteout deletes rather than becoming a device node ---------------
@check
def check_whiteout_deletes_and_is_not_a_device_node():
    t = Tree()
    try:
        t.file(t.lower, "gone", b"lower"); t.file(t.lower, "kept", b"lower")
        t.whiteout(t.upper, "gone")
        merge(t)
        expect(ls(t.merged) == ["kept"], f"merged holds {ls(t.merged)}")
        p = os.path.join(t.merged, "gone")
        expect(not os.path.lexists(p), "the whiteout itself was copied in")
    finally: t.cleanup()

@check
def check_whiteout_control_without_the_predicate_it_becomes_a_node():
    """Neutralise is_whiteout: the char device is planned as a copy, the
    base's file survives, and a device node appears in the merge."""
    t = Tree()
    try:
        t.file(t.lower, "gone", b"lower"); t.whiteout(t.upper, "gone")
        orig = fd.is_whiteout; fd.is_whiteout = lambda st: False
        try:
            plan = fd.plan_merge(t.upper)
            expect(plan.deleted == set(), f"still planned a delete: {plan.deleted}")
            raised = False
            try: fd.apply_merge(t.lower, plan, t.merged)
            except fd.MergeError: raised = True
            expect(raised, "control: a char device in copied must not be silently copied")
        finally: fd.is_whiteout = orig
    finally: t.cleanup()

# --- opaque replaces wholesale -----------------------------------------
@check
def check_opaque_directory_replaces_wholesale():
    t = Tree()
    try:
        t.file(t.lower, "etc/a", b"lower-a"); t.file(t.lower, "etc/b", b"lower-b")
        t.opaque(t.upper, "etc"); t.file(t.upper, "etc/new", b"upper-new")
        merge(t)
        expect(ls(os.path.join(t.merged, "etc")) == ["new"],
               f"etc holds {ls(os.path.join(t.merged,'etc'))}, expected only ['new']")
    finally: t.cleanup()

@check
def check_opaque_control_without_the_xattr_it_merges():
    t = Tree()
    try:
        t.file(t.lower, "etc/a", b"lower-a")
        t.dir(t.upper, "etc"); t.file(t.upper, "etc/new", b"upper-new")
        merge(t)
        expect(ls(os.path.join(t.merged, "etc")) == ["a", "new"],
               f"control: a non-opaque dir must merge, got "
               f"{ls(os.path.join(t.merged,'etc'))}")
    finally: t.cleanup()

# --- non-opaque merge keeps the base's other children ------------------
@check
def check_non_opaque_directory_keeps_base_children():
    """The case a naive copy-upper-over-lower gets right by accident and a
    replace-wholesale implementation gets wrong."""
    t = Tree()
    try:
        t.file(t.lower, "usr/keep1", b"1"); t.file(t.lower, "usr/keep2", b"2")
        t.dir(t.upper, "usr"); t.file(t.upper, "usr/added", b"3")
        merge(t)
        expect(ls(os.path.join(t.merged, "usr")) == ["added", "keep1", "keep2"],
               f"usr holds {ls(os.path.join(t.merged,'usr'))}")
    finally: t.cleanup()

@check
def check_non_opaque_control_marking_it_opaque_drops_them():
    t = Tree()
    try:
        t.file(t.lower, "usr/keep1", b"1")
        t.opaque(t.upper, "usr"); t.file(t.upper, "usr/added", b"3")
        merge(t)
        expect(ls(os.path.join(t.merged, "usr")) == ["added"],
               f"control: opaque must drop them, got "
               f"{ls(os.path.join(t.merged,'usr'))}")
    finally: t.cleanup()

# --- empty opaque directory exists and is empty ------------------------
@check
def check_empty_opaque_directory_exists_and_is_empty():
    """A house that emptied /etc meant /etc to exist, not to be absent."""
    t = Tree()
    try:
        t.file(t.lower, "etc/a", b"lower-a"); t.file(t.lower, "etc/b", b"lower-b")
        # A LOWER FILE OUTSIDE etc, asserted below. Without it this
        # check's positive ("the emptied directory is absent") is
        # supplied by step 3's own `if not isdir: makedirs`, not by the
        # mechanism under test: with step 1 neutralised entirely the
        # check still passed, because /etc was empty from having never
        # been copied rather than from having been emptied. `control`
        # called this one FIX, not ack.
        t.file(t.lower, "outside", b"o")
        t.opaque(t.upper, "etc")
        merge(t)
        p = os.path.join(t.merged, "etc")
        expect(os.path.isdir(p), "the emptied directory is absent from the merge")
        expect(ls(p) == [], f"etc holds {ls(p)}, expected empty")
        expect(ls(t.merged) == ["etc", "outside"],
               f"step 1 did not run, so 'emptied' is vacuous: {ls(t.merged)}")
    finally: t.cleanup()

# --- layer's version of a file wins ------------------------------------
@check
def check_file_in_both_takes_the_layer_version():
    t = Tree()
    try:
        t.file(t.lower, "conf", b"lower"); t.file(t.upper, "conf", b"upper")
        merge(t)
        got = open(os.path.join(t.merged, "conf"), "rb").read()
        expect(got == b"upper", f"conf holds {got!r}")
    finally: t.cleanup()

@check
def check_layer_wins_control_without_step_four_lower_survives():
    t = Tree()
    try:
        t.file(t.lower, "conf", b"lower"); t.file(t.upper, "conf", b"upper")
        plan = fd.plan_merge(t.upper); plan.copied = []
        fd.apply_merge(t.lower, plan, t.merged)
        got = open(os.path.join(t.merged, "conf"), "rb").read()
        expect(got == b"lower", f"control: without copies, {got!r}")
    finally: t.cleanup()

# --- environment-level ----------------------------------------------------

def _erofs_ok():
    if not shutil.which("mkfs.erofs"): return False, "mkfs.erofs is not installed"
    try:
        if "erofs" not in open("/proc/filesystems").read():
            return False, "the kernel has no erofs driver"
    except OSError as e: return False, str(e)
    return True, None

def make_base(t, entries):
    src = os.path.join(t.root, "basesrc"); os.makedirs(src, exist_ok=True)
    for rel, content in entries:
        p = os.path.join(src, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(content)
    d = os.path.join(t.root, "basepack"); os.makedirs(d, exist_ok=True)
    _, img = mkbrick.pack(src, d, quiet=True); return img

# --- two folds of the same inputs are byte-identical -------------------
@check
def check_two_folds_are_byte_identical():
    ok, why = _erofs_ok()
    if not ok: raise Skip(why)
    can, w = fd.can_trust_overlay_markers("/tmp")
    if not can: raise Skip(f"cannot read trusted.overlay.* ({w}); see "
                           f"check_capability_probe_reports_the_environment")
    t = Tree()
    try:
        img = make_base(t, [("usr/f", b"base")])
        t.file(t.upper, "added", b"upper")
        r1 = fd.fold(img, t.layer, t.out, "n1")
        r2 = fd.fold(img, t.layer, t.out, "n2")
        expect(r1.image_hash == r2.image_hash,
               f"two folds differ: {r1.image_hash[:12]} vs {r2.image_hash[:12]}")
        expect(os.path.isfile(r1.out_path), "image not placed")
    finally: t.cleanup()

@check
def check_determinism_control_the_packer_flags_are_the_mechanism():
    """An earlier version of this control neutralised force_attrs and
    expected the hashes to diverge. They did not, and force_attrs is
    gone: mkfs.erofs is given -T 0 and --force-uid/--force-gid and
    overrides both on the way in, so force_attrs never reached the
    image and the redundancy it claimed to provide did not exist --
    -T 0 flattens the erofs SUPERBLOCK build time, which no utime on
    the tree can reach. This drops -T 0, which is the mechanism.

    The flag list is mkbrick's `EROFS_FLAGS`, passed as a parameter for
    exactly this reason, so the control removes a flag from the list the
    fold really uses rather than from a second copy of it."""
    ok, why = _erofs_ok()
    if not ok: raise Skip(why)
    t = Tree()
    try:
        src = os.path.join(t.root, "s"); os.makedirs(src)
        open(os.path.join(src, "f"), "wb").write(b"x")
        d = os.path.join(t.root, "packs"); os.makedirs(d)
        ha, a = mkbrick.pack(src, d, quiet=True)
        os.utime(os.path.join(src, "f"), (123456, 123456))
        hb, _ = mkbrick.pack(src, d, quiet=True)
        expect(ha == hb, "with -T 0 the packer must flatten a varying mtime")

        no_T = list(mkbrick.EROFS_FLAGS)
        i = no_T.index("-T"); del no_T[i:i + 2]
        expect("-T" not in no_T, "the control did not remove -T")
        os.utime(os.path.join(src, "f"), (999999, 999999))
        hc, _ = mkbrick.pack(src, d, flags=no_T, quiet=True)
        os.utime(os.path.join(src, "f"), (111111, 111111))
        hd, _ = mkbrick.pack(src, d, flags=no_T, quiet=True)
        expect(hc != hd,
               "control: without -T 0 a varying mtime must change the "
               "image, or the packer is not what makes the fold "
               "deterministic either")
    finally: t.cleanup()

# --- refuse when the capability is absent ------------------------------
@check
def check_fold_refuses_when_the_xattr_cannot_be_read():
    t = Tree()
    try:
        orig = fd.can_trust_overlay_markers
        fd.can_trust_overlay_markers = lambda p: (False, "EPERM")
        try:
            raised = None
            base = t.file(t.root, "fake.img", b"not-an-image")
            try: fd.fold(base, t.layer, t.out, "n")
            except fd.MergeError as e: raised = str(e)
            expect(raised is not None, "fold did not refuse")
            expect("EPERM" in raised, f"refusal names the wrong cause: {raised}")
            expect("unreadable" in raised or "marker" in raised,
                   f"refusal does not say why it matters: {raised}")
        finally: fd.can_trust_overlay_markers = orig
    finally: t.cleanup()

@check
def check_refusal_precedes_reading_the_base():
    """The only input where the probe's position is observable: the xattr
    is unreadable AND the base does not exist. With the probe after
    hash_file the caller gets FileNotFoundError where the real answer is
    EPERM, and every other refusal case still passes because their base
    exists. Reverting the order leaves the rest of this suite green."""
    t = Tree()
    try:
        orig = fd.can_trust_overlay_markers
        fd.can_trust_overlay_markers = lambda p: (False, "EPERM")
        try:
            raised = None
            try:
                fd.fold(os.path.join(t.root, "does-not-exist.img"),
                        t.layer, t.out, "n")
            except fd.MergeError as e:
                raised = str(e)
            except FileNotFoundError as e:
                raise SelfTestFailure(
                    f"the base was read before the capability was probed, so "
                    f"the caller sees {type(e).__name__} where the answer is "
                    f"EPERM")
            expect(raised is not None, "fold did not refuse")
            expect("EPERM" in raised,
                   f"refusal names the wrong cause: {raised}")
            expect("does-not-exist" not in raised,
                   f"refusal blames the missing base rather than the "
                   f"capability: {raised}")
        finally:
            fd.can_trust_overlay_markers = orig
    finally: t.cleanup()

@check
def check_refusal_control_capable_probe_does_not_refuse():
    ok, why = _erofs_ok()
    if not ok: raise Skip(why)
    can, w = fd.can_trust_overlay_markers("/tmp")
    if not can: raise Skip(f"cannot read trusted.overlay.* ({w})")
    t = Tree()
    try:
        img = make_base(t, [("f", b"base")])
        r = fd.fold(img, t.layer, t.out, "n")
        expect(r.opaque_check_ran is True, "capable environment recorded as skipped")
    finally: t.cleanup()

# --- the capability probe's positive half ------------------------------
@check
def check_capability_probe_reports_the_environment():
    """Without this, a probe that never returns True is indistinguishable
    from a machine that lacks the capability."""
    d = tempfile.mkdtemp()
    try:
        can, why = fd.can_trust_overlay_markers(d)
        try: os.setxattr(d, "trusted.overlay.opaque", b"y"); capable = True
        except OSError: capable = False
        if capable:
            expect(can is True,
                   f"this machine can set trusted.overlay.* but the probe "
                   f"returned False ({why}) -- the probe is broken, not the "
                   f"environment")
        else:
            expect(can is False, "probe returned True on an incapable machine")
            raise Skip("this machine cannot set trusted.overlay.*")
    finally: shutil.rmtree(d, ignore_errors=True)

# --- sidecar records derived_from as the base's hash -------------------
@check
def check_sidecar_records_derived_from_and_layer_id():
    ok, why = _erofs_ok()
    if not ok: raise Skip(why)
    can, w = fd.can_trust_overlay_markers("/tmp")
    if not can: raise Skip(f"cannot read trusted.overlay.* ({w})")
    t = Tree()
    try:
        img = make_base(t, [("f", b"base")])
        base_hash = mkbrick.sha256_file(img)
        t.file(t.upper, "added", b"upper")
        r = fd.fold(img, t.layer + "/", t.out, "the-name")   # trailing slash
        doc = json.load(open(r.sidecar_path))
        expect(doc["derived_from"] == base_hash,
               f"derived_from is {doc['derived_from'][:12]}, base is {base_hash[:12]}")
        expect(doc["layer_id"] == "layer",
               f"layer_id is {doc['layer_id']!r}; a trailing slash must not "
               f"make it 'upper'")
        expect(doc["image_hash"] == r.image_hash, "sidecar hash disagrees")
    finally: t.cleanup()

# --- CLI exits non-zero on a MergeError -------------------------------
@check
def check_cli_exits_nonzero_with_the_reason_on_stderr():
    t = Tree()
    try:
        shutil.rmtree(t.upper)
        r = subprocess.run([sys.executable, fd.__file__, "--base", "/nope.img",
                            "--layer", t.layer, "--out", t.out, "--name", "n"],
                           capture_output=True, text=True)
        expect(r.returncode == 1, f"exit {r.returncode}, expected 1")
        expect("no upper directory" in r.stderr, f"stderr={r.stderr!r}")
        expect("Traceback" not in r.stderr, f"crashed: {r.stderr[:200]!r}")
    finally: t.cleanup()

@check
def check_cli_control_success_exits_zero():
    ok, why = _erofs_ok()
    if not ok: raise Skip(why)
    can, w = fd.can_trust_overlay_markers("/tmp")
    if not can: raise Skip(f"cannot read trusted.overlay.* ({w})")
    t = Tree()
    try:
        img = make_base(t, [("f", b"base")])
        r = subprocess.run([sys.executable, fd.__file__, "--base", img,
                            "--layer", t.layer, "--out", t.out, "--name", "n"],
                           capture_output=True, text=True)
        expect(r.returncode == 0, f"exit {r.returncode}: {r.stderr[:200]}")
        expect(len(r.stdout.strip()) == 64, f"stdout={r.stdout!r}")
    finally: t.cleanup()

# --- THE ORACLE: a folded image against the kernel's own overlay view -

def _xattrs(path):
    """EVERY namespace but the overlay's own, which is bookkeeping the
    kernel hides from the merged view anyway.

    This compared `user.*` only, for one round -- and `user.*` was
    itself the property found after a list of four was called measured.
    Filtering to it meant `security.capability` was invisible here in
    exactly the way it had been invisible there: a `setcap` binary folds
    to an unprivileged one, valid image, exit 0. A deny-list of one
    namespace cannot repeat that; an allow-list did, twice."""
    try:
        return tuple(sorted((n, os.getxattr(path, n))
                            for n in os.listxattr(path)
                            if not n.startswith("trusted.overlay.")))
    except OSError:
        return ()


def _census(root):
    """WHAT THIS COMPARES, named rather than claimed exhaustive: the
    root's own mode and xattrs; symlink target; directory mode and
    xattrs; regular-file mode, content and xattrs; the file TYPE of
    anything else; and hard links as equivalence classes of paths.
    Xattrs means every namespace but trusted.overlay.*.

    It said "every property erofs can carry" for one round and xattrs
    were not in it -- a claim of exhaustiveness standing beside the very
    omission, which is the shape this repository is about. Name the
    fields; the next property is found by adding one, not by re-reading
    the sentence.

    Hard links are classes of paths and never inode numbers: those
    differ between two filesystems by construction, so comparing them
    would make the check always fail.

    Deliberately NOT compared: uid, gid and timestamps. mkbrick.pack
    flattens them with --force-uid/--force-gid and -T 0, so the kernel's
    view and the image's view differ for a correct fold."""
    # THE ROOT ITSELF, which os.walk never emits. Deleting the two
    # statements that set the merged root's mode and xattrs left the
    # whole suite green, identity included -- two live statements with
    # nothing pinning them, in the file whose thesis is that the oracle
    # pins everything. `claims`.
    out = {"": ("dir", stat.S_IMODE(os.lstat(root).st_mode), None,
                _xattrs(root))}
    inodes = {}
    for dp, dn, fn in os.walk(root):
        for name in sorted(dn) + sorted(fn):
            full = os.path.join(dp, name)
            rel = os.path.relpath(full, root)
            st = os.lstat(full)
            if stat.S_ISLNK(st.st_mode):
                out[rel] = ("symlink", os.readlink(full), None)
                continue
            if stat.S_ISDIR(st.st_mode):
                out[rel] = ("dir", stat.S_IMODE(st.st_mode), None,
                            _xattrs(full))
                continue
            if not stat.S_ISREG(st.st_mode):
                # NEVER open() a fifo here. The first extended census
                # blocked forever on one. Unreachable while the fold
                # refuses them, and the next person to widen the fixture
                # would get a hang instead of a failure.
                out[rel] = ("other", stat.S_IFMT(st.st_mode), None)
                continue
            out[rel] = ("file", stat.S_IMODE(st.st_mode),
                        open(full, "rb").read(), _xattrs(full))
            inodes.setdefault((st.st_dev, st.st_ino), []).append(rel)
        dn[:] = [d for d in sorted(dn)
                 if not os.path.islink(os.path.join(dp, d))]
    classes = sorted(tuple(sorted(v)) for v in inodes.values() if len(v) > 1)
    return out, classes


@check
def check_folded_image_matches_the_kernels_own_overlay_view():
    """THE CHECK THAT MAKES THE KERNEL THE ORACLE.

    The fold-level checks written before this one compare a hash to
    another hash, a file to its own existence, or JSON to itself, so
    none of them reads a byte inside an image the fold produced. That single gap hid four
    defects at once -- file modes dropped (a 0755 binary packed 0644 and
    a folded brick could not exec its own /bin), a symlink to a DIRECTORY
    silently deleted, an empty directory the house created lost, and hard
    links split into separate inodes.

    This mounts the same lower and upper as a real overlay, mounts the
    folded image, and requires the two trees to agree. It cannot be
    satisfied by my model of what a fold should do, which is what wrote
    the four defects; it can only be satisfied by matching the kernel.

    Not a substitute for the case-by-case checks above -- those say WHICH
    rule produced a difference, and this one only says there is one."""
    ok, why = _erofs_ok()
    if not ok: raise Skip(why)
    if "overlay" not in open("/proc/filesystems").read():
        raise Skip("the kernel has no overlay driver, so there is no oracle "
                   "to compare against; modes, symlink-to-directory, "
                   "house-created empty directories and hard links go "
                   "untested, and so does any future difference between a "
                   "fold and what the house actually saw")
    can, w = fd.can_trust_overlay_markers("/tmp")
    if not can:
        raise Skip(f"cannot read trusted.overlay.* ({w}); the same "
                   f"properties as above go untested")
    t = Tree()
    mounts = []
    try:
        # A base shaped like a real brick: an executable, a restricted
        # file, a symlink to a directory AND to a file, a hard-linked
        # pair, and a directory whose mode is not the default.
        src = os.path.join(t.root, "brick"); os.makedirs(src + "/usr/bin")
        open(src + "/usr/bin/prog", "wb").write(b"#!/bin/sh\nexit 0\n")
        os.chmod(src + "/usr/bin/prog", 0o755)
        os.symlink("usr/bin", src + "/bin")
        os.symlink("usr/bin/prog", src + "/prog-link")
        open(src + "/secret", "wb").write(b"k")
        os.chmod(src + "/secret", 0o600)
        open(src + "/linked-a", "wb").write(b"shared")
        os.link(src + "/linked-a", src + "/linked-b")
        os.makedirs(src + "/private"); os.chmod(src + "/private", 0o700)
        # A SURVIVING distinctively-moded directory. `/private` above is
        # the one the house deletes, so it was the only directory-mode
        # input and the whiteout consumed it: three separate call sites
        # that preserve directory modes were removable with the suite
        # green. `control`.
        os.makedirs(src + "/kept-mode"); os.chmod(src + "/kept-mode", 0o701)
        os.makedirs(src + "/etc"); open(src + "/etc/keep", "wb").write(b"k")
        os.makedirs(src + "/etc/gone")
        # user.* xattrs, from the brick side.
        os.setxattr(src + "/secret", "user.brick", b"v1")
        os.setxattr(src + "/etc", "user.dirattr", b"dv")
        # A SECOND NAMESPACE. With the census filtered to user.* this
        # was invisible, and a `setcap` binary folded to an unprivileged
        # one: security.capability survives the pack, and the fold
        # stripped it. security.* rather than a real capability blob so
        # the check does not need setcap on the machine.
        os.setxattr(src + "/usr/bin/prog", "security.brickcap", b"c1")
        # A BASE-ONLY directory with an xattr. `etc` carries one too, but
        # the house copies `etc` up, so step 1 and step 4a each supply it
        # and either site was deletable green -- a conjunction pinning
        # neither. This one the house never touches. `control`.
        os.makedirs(src + "/baseonly")
        os.setxattr(src + "/baseonly", "user.baseonly", b"B")
        # A dangling symlink is ordinary in a brick (/etc/localtime and
        # every unit link pointing outside it).
        os.symlink("nowhere-at-all", src + "/dangling")
        # A file the house will turn INTO a directory, and a moded file
        # the house will replace WITH a symlink -- the input that made the
        # mode pass chmod through the link.
        open(src + "/becomes-a-dir", "wb").write(b"f")
        open(src + "/zzz", "wb").write(b"z"); os.chmod(src + "/zzz", 0o600)
        open(src + "/aaa", "wb").write(b"a"); os.chmod(src + "/aaa", 0o755)
        packdir = os.path.join(t.root, "p"); os.makedirs(packdir)
        _, img = mkbrick.pack(src, packdir, quiet=True)

        # The house runs against a REAL overlay, so the upper is whatever
        # the kernel decides to put there rather than what I think it
        # should. That is the whole point: the encodings under test are
        # the kernel's, and a hand-built upper tests my reading of them.
        lo = os.path.join(t.root, "lo"); os.makedirs(lo)
        mm = os.path.join(t.root, "mm"); os.makedirs(mm)
        wk = os.path.join(t.layer, "work"); os.makedirs(wk, exist_ok=True)
        subprocess.run(["mount", "-o", "ro,loop", img, lo], check=True)
        mounts.append(lo)
        subprocess.run(["mount", "-t", "overlay", "overlay", "-o",
                        f"lowerdir={lo},upperdir={t.upper},workdir={wk}", mm],
                       check=True)
        mounts.append(mm)
        os.makedirs(mm + "/var/lib/app")            # empty, house-created
        open(mm + "/usr/bin/added", "wb").write(b"#!/bin/sh\n")
        os.chmod(mm + "/usr/bin/added", 0o750)      # a mode of its own
        os.remove(mm + "/etc/keep")                 # a whiteout
        shutil.rmtree(mm + "/private")              # a whiteout over a dir
        open(mm + "/secret", "wb").write(b"rewritten")
        os.setxattr(mm + "/secret", "user.house", b"hv")   # house-set xattr
        os.setxattr(mm + "/usr/bin/added", "security.housecap", b"c2")
        os.makedirs(mm + "/houseonly")              # and a HOUSE-only one,
        os.setxattr(mm + "/houseonly", "user.houseonly", b"H")  # step 4a
        os.chmod(mm + "/var/lib/app", 0o711)        # a house dir's own mode
        os.remove(mm + "/becomes-a-dir")            # a DIRECTORY replacing a
        os.makedirs(mm + "/becomes-a-dir")          #   file: it crashed
        open(mm + "/becomes-a-dir/inner", "wb").write(b"i")
        os.remove(mm + "/zzz")                      # a SYMLINK replacing a
        os.symlink("aaa", mm + "/zzz")              #   moded file: the mode
                                                    #   pass chmod'd through
        open(mm + "/hl-a", "wb").write(b"pair")     # a house-created hard
        os.link(mm + "/hl-a", mm + "/hl-b")         #   link pair
        os.chmod(mm, 0o751)                         # the ROOT's own mode
        os.setxattr(mm, "user.rootattr", b"rv")     #   and its xattrs
        want, want_links = _census(mm)
        subprocess.run(["umount", mm], check=True); mounts.remove(mm)

        r = fd.fold(img, t.layer, t.out, "oracle")

        # Content addressing, asserted where it is cheapest: the name a
        # reader trusts must be the digest of the bytes under it.
        expect(os.path.basename(r.out_path) == r.image_hash + ".img",
               f"image not at its own hash: {os.path.basename(r.out_path)}")
        expect(mkbrick.sha256_file(r.out_path) == r.image_hash,
               "the stored name is not the hash of the stored bytes")

        got_mnt = os.path.join(t.root, "got"); os.makedirs(got_mnt)
        subprocess.run(["mount", "-o", "ro,loop", r.out_path, got_mnt],
                       check=True)
        mounts.append(got_mnt)
        got, got_links = _census(got_mnt)

        missing = sorted(set(want) - set(got))
        extra = sorted(set(got) - set(want))
        expect(not missing, f"the fold lost {missing}; the house saw them")
        expect(not extra, f"the fold invented {extra}")
        differ = [(k, want[k][:2], got[k][:2]) for k in sorted(want)
                  if want[k] != got[k]]
        expect(not differ, f"folded image disagrees with the kernel: {differ}")
        expect(want_links == got_links,
               f"hard links differ: kernel {want_links}, fold {got_links}")

        # The paired positive: this ran against a tree that HAS the
        # properties, so an all-empty census cannot pass it.
        built = {
            "file mode":     want.get("usr/bin/prog", (0, 0))[1] == 0o755,
            "dir mode":      want.get("kept-mode", (0, 0))[1] == 0o701,
            "symlink->dir":  want.get("bin", (0,))[0] == "symlink",
            "dangling link": want.get("dangling", (0,))[0] == "symlink",
            "empty new dir": "var/lib/app" in want,
            "dir over file": "becomes-a-dir/inner" in want,
            "link over file": want.get("zzz", (0,))[0] == "symlink",
            "hard links":    bool(want_links),
            "user xattr":    any(n.startswith("user.")
                                 for v in want.values() if len(v) > 3
                                 for n, _ in v[3]),
            "root mode":     want.get("", (0, 0))[1] == 0o751,
            "security xattr": any(n.startswith("security.")
                                  for v in want.values() if len(v) > 3
                                  for n, _ in v[3]),
            # Named separately because one key for two sites is the
            # half-covered-claim shape: the file half satisfied it while
            # both directory sites stayed deletable green.
            "base dir xattr":  want.get("baseonly", (0,)*4)[3]
                               == (("user.baseonly", b"B"),),
            "house dir xattr": want.get("houseonly", (0,)*4)[3]
                               == (("user.houseonly", b"H"),),
        }
        expect(all(built.values()),
               f"the fixture did not build the properties under test: "
               f"{sorted(k for k, v in built.items() if not v)}")
    finally:
        for m in reversed(mounts):
            subprocess.run(["umount", m], check=False)
        t.cleanup()


# --- the refusal, through the real function rather than a stand-in ----
@check
def check_the_real_probe_refuses_where_the_xattr_is_unreadable():
    """Every other refusal check replaces can_trust_overlay_markers with a
    lambda, so the function's own refusing branch is never executed and
    `control` could neutralise it -- returning (True, None) for EPERM --
    with all checks still passing. That branch is the entire argument for
    there being no --force, so it is the one that must be exercised for
    real.

    An unprivileged user namespace is what makes trusted.* unreadable
    without changing anything else about the tree."""
    if not os.path.exists("/proc/self/ns/user"):
        raise Skip("no user namespaces, so trusted.* cannot be made "
                   "unreadable without dropping privilege for real; the "
                   "refusing branch of can_trust_overlay_markers goes untested")
    t = Tree()
    try:
        probe = (f"import sys; sys.path.insert(0, {os.path.dirname(fd.__file__)!r}); "
                 f"import fold; print(fold.can_trust_overlay_markers({t.layer!r}))")
        r = subprocess.run(["unshare", "-U", "-r", sys.executable, "-c", probe],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise Skip(f"unshare -U -r unavailable: {r.stderr.strip()[:120]}")
        expect("False" in r.stdout and "EPERM" in r.stdout,
               f"the real probe did not refuse in a user namespace: "
               f"{r.stdout.strip()!r}")
        # PAIRED: the same call outside the namespace must succeed, or
        # "refuses" would be satisfied by a probe that always refuses.
        can, why = fd.can_trust_overlay_markers(t.layer)
        expect(can, f"the same probe refused with privilege too ({why}); "
                    f"the refusal above is not about the capability")
        # And the refusal must reach fold() rather than stopping at the
        # probe: run the whole tool under the same namespace.
        r2 = subprocess.run(["unshare", "-U", "-r", sys.executable,
                             fd.__file__, "--base", "/nope.img", "--layer",
                             t.layer, "--out", t.out, "--name", "n"],
                            capture_output=True, text=True)
        expect(r2.returncode == 1, f"exit {r2.returncode}")
        expect("cannot read trusted.overlay" in r2.stderr,
               f"refusal names the wrong cause: {r2.stderr.strip()[:200]!r}")
    finally: t.cleanup()


# --- an errno that is neither ENODATA nor EPERM is still an error ----
@check
def check_a_filesystem_that_cannot_carry_the_marker_is_refused():
    """plan_merge raised only on "EPERM" and ignored every other error
    is_opaque returned, so an upper on a filesystem with no trusted-xattr
    support read as "no directory is opaque" and the fold merged every
    directory the house meant to replace -- silently, which is the exact
    artifact this tool refuses to produce.

    /proc answers ENOTSUP for trusted.*, so this is the kernel's own
    errno rather than a stand-in. That matters here more than usual:
    every other refusal check in this file monkeypatches the function it
    is about, and a monkeypatch cannot tell you which errno the kernel
    actually picks -- getting that wrong is what left the branch
    unreachable in the first place."""
    import errno as _errno
    ns = "/proc/self/ns"
    if not os.path.isdir(ns):
        raise Skip("no /proc/self/ns, so no filesystem here reports ENOTSUP "
                   "for trusted.* and the non-EPERM error path goes untested")
    # THE ENVIRONMENT QUESTION IS ASKED OF THE KERNEL, NOT OF is_opaque.
    # It was asked of is_opaque, and that let a defect IN is_opaque flip
    # this check from FAIL to SKIP while printing a reason that blamed
    # the kernel -- and `make test`'s guard reads a skip as success, so
    # the target stayed green on a real code defect. A test must never
    # let the function under test decide whether the test runs.
    # `claims`.
    try:
        os.getxattr(ns, "trusted.overlay.opaque")
        raise Skip(f"{ns} carries trusted.* on this kernel, so it cannot "
                   f"stand in for a filesystem that does not")
    except OSError as e:
        if e.errno not in (_errno.ENOTSUP, _errno.EOPNOTSUPP):
            raise Skip(f"{ns} answers {_errno.errorcode.get(e.errno, e.errno)} "
                       f"rather than ENOTSUP here; the non-EPERM error path "
                       f"goes untested")
    opaque, err = fd.is_opaque(ns)
    expect(err is not None,
           f"is_opaque reported no error for a path the kernel answers "
           f"ENOTSUP for; every directory on such a filesystem would read "
           f"as non-opaque. Got ({opaque}, {err!r})")
    expect("ENOTSUP" in err or "EOPNOTSUPP" in err,
           f"expected the kernel's ENOTSUP, got {err!r}")
    try:
        fd.plan_merge(ns)
        raise SelfTestFailure(
            "plan_merge accepted an upper whose opaque markers cannot be "
            "read at all; every directory would merge as non-opaque")
    except fd.MergeError as e:
        expect("ENOTSUP" in str(e) or "EOPNOTSUPP" in str(e),
               f"refused, but not for the errno: {e}")
    # PAIRED: the same call on a filesystem that DOES carry the marker
    # must be accepted, or "refuses" is satisfied by refusing everything.
    t = Tree()
    try:
        t.file(t.upper, "f", b"x")
        plan = fd.plan_merge(t.upper)
        expect([r for _, r in plan.copied] == ["f"],
               f"the paired positive did not plan: {plan.copied}")
    finally: t.cleanup()


# --- the identity: a house that wrote nothing folds to its own base --
@check
def check_folding_an_empty_layer_reproduces_the_base_exactly():
    """The cheapest whole-fold assertion there is, and the one that would
    have caught the lost user xattrs for free. It needs no case analysis:
    if the fold changes anything it was not asked to change, the hash
    moves.

    THE BASE MUST BE PACKED BY mkbrick, and that qualifier is part of the
    check rather than a caveat on it. `tests/run.py`'s make_brick packs
    with `-zlz4` while the fold packs with mkbrick's EROFS_FLAGS, so an
    empty-layer fold of a SUITE brick differs from its base under a
    perfectly correct fold -- same contents, different flags. A reader
    who took the identity as a property of any base would read that
    difference as a defect. `claims`."""
    ok, why = _erofs_ok()
    if not ok: raise Skip(why)
    can, w = fd.can_trust_overlay_markers("/tmp")
    if not can: raise Skip(f"cannot use trusted.overlay.* ({w})")
    t = Tree()
    try:
        src = os.path.join(t.root, "id"); os.makedirs(src + "/usr/bin")
        open(src + "/usr/bin/prog", "wb").write(b"p")
        os.chmod(src + "/usr/bin/prog", 0o755)
        os.symlink("usr/bin", src + "/bin")
        open(src + "/a", "wb").write(b"a"); os.link(src + "/a", src + "/b")
        os.makedirs(src + "/d"); os.chmod(src + "/d", 0o701)
        os.setxattr(src + "/a", "user.k", b"v")
        os.setxattr(src + "/usr/bin/prog", "security.k", b"c")
        packdir = os.path.join(t.root, "p"); os.makedirs(packdir)
        base_hash, img = mkbrick.pack(src, packdir, quiet=True)
        # WHAT THE BASE ACTUALLY CONTAINS, asserted. Without this the
        # check passes against an empty directory: its entire coverage
        # is a property of a fixture nothing checks, and this file has
        # already had a fixture silently stop being built once. The
        # oracle got a `built` block for that; the identity standing
        # beside it did not. `control`.
        holds = sorted(os.path.relpath(os.path.join(dp, n), src)
                       for dp, dn, fn in os.walk(src) for n in dn + fn)
        expect(holds == ["a", "b", "bin", "d", "usr", "usr/bin",
                         "usr/bin/prog"],
               f"the identity base is not the tree this check describes: "
               f"{holds}")
        expect(os.path.islink(src + "/bin")
               and os.lstat(src + "/a").st_nlink == 2
               and stat.S_IMODE(os.lstat(src + "/usr/bin/prog").st_mode) == 0o755
               and stat.S_IMODE(os.lstat(src + "/d").st_mode) == 0o701
               and os.listxattr(src + "/a") == ["user.k"],
               "the identity base lost one of the properties it exists to "
               "carry over")
        r = fd.fold(img, t.layer, t.out, "identity")   # upper is empty
        expect(r.image_hash == base_hash,
               f"a fold of a house that wrote nothing is not its own base: "
               f"base {base_hash[:12]} folded {r.image_hash[:12]}")
        # PAIRED: the same fold with one byte written must move the hash,
        # or "reproduces the base" is satisfied by a fold that ignores
        # the layer entirely.
        t.file(t.upper, "added", b"x")
        r2 = fd.fold(img, t.layer, t.out, "identity2")
        expect(r2.image_hash != base_hash,
               "a fold that WROTE something still reproduced the base; "
               "the identity above is vacuous")
    finally: t.cleanup()


# --- the markers the fold does not implement are refused ------------
@check
def check_unhandled_overlay_markers_are_refused():
    """`fold` read trusted.overlay.opaque and treated it as the whole
    encoding. It is not: a renamed directory carries .redirect and a
    chmod'd file carries .metacopy with a body of zeros. Both folded to
    exit 0 and a valid content-addressed image disagreeing with what the
    house saw -- 4096 bytes of real content replaced by 4096 zeros in the
    metacopy case. `control` measured both, and found the redirect case
    is honoured by nw-sup's own mount options on this machine
    (redirect_always_follow=Y), so a layer that acquired the marker
    anywhere is honoured here afterwards.

    The markers are set directly rather than provoked out of the kernel:
    reaching them needs redirect_dir=on / metacopy=on, which this kernel
    defaults off, and the property under test is what `fold` does when it
    MEETS one, not which mount option writes it."""
    can, w = fd.can_trust_overlay_markers("/tmp")
    if not can:
        raise Skip(f"cannot set trusted.overlay.* ({w}); the refusal for "
                   f"redirect and metacopy markers goes untested")
    for marker, where in (("trusted.overlay.redirect", "dir"),
                          ("trusted.overlay.metacopy", "file")):
        t = Tree()
        try:
            target = (t.dir(t.upper, "renamed") if where == "dir"
                      else t.file(t.upper, "shrunk", b""))
            os.setxattr(target, marker, b"old")
            try:
                fd.plan_merge(t.upper)
                raise SelfTestFailure(
                    f"plan_merge accepted an upper carrying {marker}; the "
                    f"image would mount, boot and be wrong")
            except fd.MergeError as e:
                expect(marker in str(e), f"refused, but not for {marker}: {e}")
        finally: t.cleanup()
    # PAIRED: the same shapes WITHOUT a marker must be accepted, or the
    # refusal is satisfied by refusing every directory and every file.
    t = Tree()
    try:
        t.dir(t.upper, "renamed"); t.file(t.upper, "shrunk", b"")
        plan = fd.plan_merge(t.upper)
        expect([r for _, r in plan.dirs] == ["renamed"]
               and [r for _, r in plan.copied] == ["shrunk"],
               f"the paired positive did not plan: dirs={plan.dirs} "
               f"copied={plan.copied}")
    finally: t.cleanup()


# --- the probe's non-EPERM branch, on a filesystem with no xattrs ---
@check
def check_the_probe_refuses_a_filesystem_that_cannot_hold_the_marker():
    """The old read-probe's EPERM branch was unreachable. The write
    probe's non-EPERM branch IS reachable -- ramfs answers ENOTSUP -- and
    was untested: turning it into `return True, None` left the suite
    green. The replaced defect one step over, which is what a fix
    inherits if nobody looks. `control`."""
    if os.geteuid() != 0:
        raise Skip("not root, so no ramfs mount; the probe's non-EPERM "
                   "branch goes untested")
    t = Tree()
    m = os.path.join(t.root, "ramfs"); os.makedirs(m)
    r = subprocess.run(["mount", "-t", "ramfs", "ramfs", m],
                       capture_output=True, text=True)
    if r.returncode != 0:
        t.cleanup()
        raise Skip(f"no ramfs ({r.stderr.strip()[:80]}); the probe's "
                   f"non-EPERM branch goes untested")
    try:
        can, why = fd.can_trust_overlay_markers(m)
        expect(not can, f"the probe accepted a filesystem with no xattrs: "
                        f"({can}, {why})")
        expect("ENOTSUP" in (why or "") or "EOPNOTSUPP" in (why or ""),
               f"refused, but not for the errno: {why!r}")
        expect(os.listdir(m) == [], f"the probe left {os.listdir(m)} behind")
        # PAIRED: on a filesystem that CAN hold it, the same probe passes.
        can2, why2 = fd.can_trust_overlay_markers(t.root)
        expect(can2, f"the probe refused a capable filesystem too ({why2})")
    finally:
        subprocess.run(["umount", m], check=False)
        t.cleanup()


# --- a whiteout is 0:0; a real device node is not a deletion --------
@check
def check_only_a_zero_zero_char_device_is_a_whiteout():
    """`is_whiteout` reduced to "any character device" left the suite
    green, so nothing distinguished a whiteout from a device node in a
    layer -- which would read as a deletion. `control`."""
    if os.geteuid() != 0:
        raise Skip("mknod needs root; the whiteout device numbers go "
                   "untested")
    t = Tree()
    try:
        t.file(t.lower, "gone", b"l"); t.file(t.lower, "kept", b"k")
        t.whiteout(t.upper, "gone")
        os.mknod(os.path.join(t.upper, "realdev"),
                 stat.S_IFCHR | 0o600, os.makedev(1, 3))
        plan = fd.plan_merge(t.upper)
        expect(plan.deleted == {"gone"},
               f"deleted={plan.deleted}; a 1:3 device node is not a "
               f"whiteout and must not be read as one")
        expect([r for _, r in plan.copied] == ["realdev"],
               f"the 1:3 node was not planned as an entry: {plan.copied}")
    finally: t.cleanup()


# --- an opaque UPPER ROOT replaces everything -----------------------
@check
def check_an_opaque_upper_root_replaces_everything():
    """`if opaque_root:` -> `if False:` left the suite green: the "house
    replaced its entire root" case was planned and never exercised.
    `control`."""
    t = Tree()
    try:
        t.file(t.lower, "old", b"l"); t.file(t.lower, "sub/deep", b"l")
        try:
            os.setxattr(t.upper, "trusted.overlay.opaque", b"y")
        except OSError as e:
            raise Skip(f"cannot set trusted.overlay.* here ({e}); the "
                       f"opaque upper root goes untested")
        t.file(t.upper, "new", b"u")
        plan = fd.plan_merge(t.upper)
        expect("" in plan.replaced,
               f"an opaque upper root was not planned as a replace: "
               f"{plan.replaced}")
        fd.apply_merge(t.lower, plan, t.merged)
        expect(ls(t.merged) == ["new"],
               f"merged holds {ls(t.merged)}, expected only ['new']")
    finally: t.cleanup()


# --- nothing the fold writes may land outside the merged tree ----------
@check
def check_the_fold_never_writes_outside_the_merged_tree():
    """THREE ESCAPES, each found by deleting one guard and each landing
    on the fold HOST, because the fold runs as root in the bakery and a
    layer chooses its own paths.

    A symlink in the base plus a layer entry at the same name: step 3's
    `os.path.islink` emptied and wrote into the link's TARGET; step 4b's
    `_remove(dst)` let `open(dst,"wb")` follow it; step 4a's missing
    guard let `makedirs(exist_ok=True)` succeed THROUGH it. All three
    are green against the oracle, whose `becomes-a-dir` is a regular
    file and so takes the same branch either way.

    The victim directory is outside `merged` on purpose. An assertion
    that the merged tree is right cannot see this; only an assertion
    about what did NOT move can. `control`."""
    for shape in ("replaced-dir", "written-file", "new-dir"):
        t = Tree()
        try:
            outside = os.path.join(t.root, "OUTSIDE"); os.makedirs(outside)
            open(os.path.join(outside, "victim"), "wb").write(b"host")
            os.symlink(outside, os.path.join(t.lower, "x"))
            t.file(t.lower, "keep", b"k")
            if shape == "replaced-dir":
                d = t.opaque(t.upper, "x"); t.file(t.upper, "x/new", b"u")
            elif shape == "written-file":
                t.file(t.upper, "x", b"u")
            else:
                t.dir(t.upper, "x"); t.file(t.upper, "x/new", b"u")
            merge(t)
            expect(sorted(os.listdir(outside)) == ["victim"],
                   f"{shape}: the fold wrote outside the merged tree; "
                   f"OUTSIDE holds {sorted(os.listdir(outside))}")
            expect(open(os.path.join(outside, "victim"), "rb").read() == b"host",
                   f"{shape}: the fold overwrote a file on the host")
        finally: t.cleanup()
    # PAIRED: the same shapes must still produce the right merged tree,
    # or "wrote nothing outside" is satisfied by a fold that does
    # nothing at all.
    t = Tree()
    try:
        outside = os.path.join(t.root, "OUTSIDE"); os.makedirs(outside)
        os.symlink(outside, os.path.join(t.lower, "x"))
        t.dir(t.upper, "x"); t.file(t.upper, "x/new", b"u")
        merge(t)
        expect(ls(os.path.join(t.merged, "x")) == ["new"],
               f"the merged tree is wrong, so the check above is vacuous: "
               f"{ls(os.path.join(t.merged, 'x'))}")
        expect(not os.path.islink(os.path.join(t.merged, "x")),
               "merged/x is still a symlink; the layer's directory did not "
               "replace it")
    finally: t.cleanup()
    # AN EMPTY house-created directory over a base symlink is the shape
    # where step 4a's guard stands alone: with a file inside, step 4b's
    # parent guard removes the symlink first and 4a's removal is
    # redundant -- correct redundancy, and it made 4a deletable green.
    # Empty, step 4b never touches the parent, so only 4a can fix it.
    t = Tree()
    try:
        outside = os.path.join(t.root, "OUTSIDE"); os.makedirs(outside)
        open(os.path.join(outside, "victim"), "wb").write(b"host")
        os.symlink(outside, os.path.join(t.lower, "x"))
        t.dir(t.upper, "x")
        merge(t)
        m = os.path.join(t.merged, "x")
        expect(not os.path.islink(m),
               "an empty house-created directory left the base's symlink in "
               "place; the folded image shows the link's target, not the "
               "empty directory the house saw")
        expect(os.path.isdir(m) and ls(m) == [],
               f"merged/x is not the empty directory the house created: "
               f"isdir={os.path.isdir(m)} holds={ls(m)}")
        expect(sorted(os.listdir(outside)) == ["victim"],
               f"and it reached the host: OUTSIDE holds "
               f"{sorted(os.listdir(outside))}")
    finally: t.cleanup()


# --- a whiteout over a symlink-to-a-directory is still a deletion -------
@check
def check_a_whiteout_removes_a_symlink_to_a_directory():
    """`_remove`'s `os.path.islink` test was deletable green:
    `shutil.rmtree(<symlink>, ignore_errors=True)` swallows its own
    refusal, so a house that deleted `/bin` from a brick where `/bin` is
    a symlink got it back in the folded image. Silent, and no check
    built a whiteout over one. `control`."""
    t = Tree()
    try:
        os.makedirs(os.path.join(t.lower, "usr/bin"))
        t.file(t.lower, "usr/bin/prog", b"p")
        os.symlink("usr/bin", os.path.join(t.lower, "bin"))
        t.whiteout(t.upper, "bin")
        merge(t)
        expect(not os.path.lexists(os.path.join(t.merged, "bin")),
               "the whiteout did not remove a symlink-to-a-directory")
        # PAIRED: the target survives, so "removed" is not satisfied by
        # a merge that removed everything.
        expect(ls(os.path.join(t.merged, "usr/bin")) == ["prog"],
               f"the symlink's target went too: "
               f"{ls(os.path.join(t.merged, 'usr/bin'))}")
    finally: t.cleanup()


# --- the second marker namespace, which needs no privilege --------------
@check
def check_user_overlay_markers_are_refused_too():
    """`user.overlay.*` is what a `userxattr` overlay writes, needs no
    privilege, and is not escaped by the kernel the way a house-set
    `trusted.overlay.opaque` is. Measured on a real userxattr mount: the
    house replaced /etc, the fold resurrected its base children and
    baked the marker into the sealed image. Refused in both namespaces
    now. `control`."""
    t = Tree()
    try:
        d = t.dir(t.upper, "etc")
        os.setxattr(d, "user.overlay.opaque", b"y")
        try:
            fd.plan_merge(t.upper)
            raise SelfTestFailure(
                "plan_merge accepted an upper carrying user.overlay.opaque; "
                "a userxattr layer would merge a directory the house replaced")
        except fd.MergeError as e:
            expect("user.overlay.opaque" in str(e),
                   f"refused, but not for the marker: {e}")
    finally: t.cleanup()
    # And it must not be CARRIED into the image either, which is a
    # separate mechanism from the refusal.
    t = Tree()
    try:
        f = t.file(t.upper, "f", b"x")
        os.setxattr(f, "user.overlay.somethingelse", b"v")
        os.setxattr(f, "user.keepme", b"k")
        fd.apply_merge(t.lower, fd.plan_merge(t.upper, opaque_check=False),
                       t.merged)
        got = [n for n in os.listxattr(os.path.join(t.merged, "f"))]
        expect("user.overlay.somethingelse" not in got,
               f"an overlay marker was carried into the image: {got}")
        expect("user.keepme" in got,
               f"the deny-list ate an ordinary user xattr too: {got}")
    finally: t.cleanup()


def main():
    # THE SECTION HEADINGS ABOVE CARRY NO NUMBERS, and they did until
    # this round: eighteen hand-written ordinals over a set that grew by
    # eight, sitting directly above this comment's argument that a
    # derived roster cannot go stale the way a written-down number does.
    # `claims` read both. Name the instance; do not number it.
    #
    # EVERY check_* MUST BE REGISTERED. Dropping @check from the oracle
    # left `make test` green at one check fewer, and the roster size is
    # printed and read by nobody. Derived rather than counted, so it
    # cannot go stale the way a written-down number would. `control`.
    declared = sorted(k for k, v in globals().items()
                      if k.startswith("check_") and callable(v))
    registered = sorted(f.__name__ for f in CHECKS)
    if declared != registered or len(CHECKS) != len(set(registered)):
        # BOTH DIRECTIONS AND THE LENGTH. It printed only the first, so
        # a check registered twice, or registered under a name that is
        # not check_*, went red with `declared but not registered: []`
        # and sent the reader looking for a name that is not there.
        # `control`. A callable named check_* that is not a test is a
        # false positive here and is meant to be: the gate is the naming
        # convention, so a helper wants a different name. What no
        # name-based roster can see is a test named neither check_* nor
        # registered -- read the roster, not this comment, for coverage.
        print(f"FAIL  roster: declared but not registered: "
              f"{sorted(set(declared) - set(registered))}; "
              f"registered but not declared: "
              f"{sorted(set(registered) - set(declared))}; "
              f"registered {len(CHECKS)} for {len(set(registered))} names")
        return 1
    fails, skips = [], []
    for fn in CHECKS:
        n = fn.__name__
        try: fn(); print(f"PASS  {n}")
        except Skip as e: skips.append((n, str(e))); print(f"SKIP  {n}  {e}")
        except SelfTestFailure as e: fails.append(n); print(f"FAIL  {n}  {e}")
        except BaseException as e:
            fails.append(n)
            print(f"FAIL  {n}  unexpected {type(e).__name__}: {e}")
    print()
    print(f"{len(CHECKS)} checks: {len(CHECKS)-len(fails)-len(skips)} pass, "
          f"{len(fails)} fail, {len(skips)} skip")
    if skips:
        print("A skipped check is not a passing check:")
        for n, w in skips: print(f"  {n}: {w}")
    return 1 if fails else (2 if skips else 0)

sys.exit(main())
