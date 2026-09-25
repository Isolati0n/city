#!/usr/bin/env python3
"""Create the writable layer directories a plan declares.

THE ONLY THING THAT CREATES THEM, and that is the design rather than a
convenience. `nw-sup` deliberately does not: a supervisor that mkdir'd a
missing layer would turn "the stager never ran" into "the house silently
got an empty layer", which is the same defect as a house whose data sits
orphaned under a renamed key. A missing layer is a loud failure at
nw-sup's `mount layer` instead.

`tools/stage-candidate.py` calls this, and so does the harness. The
point is that there is one creator and it takes the layer ids from the
plan, not two creators that have to agree. (This said the candidate
stager did not exist yet, for a day after it landed -- and survived a
round that edited the next sentence of the same paragraph.)

The ids come from the `.layers` sidecar the baker writes beside the blob,
one per line, as `<layer-id> <brick> <layer_bytes>`; this tool reads the
id and, when layer_bytes is nonzero, the capacity, and ignores the
brick. That is why this tool does not parse the blob: a third copy
of the unit layout (after blob.h and the baker) is the drift class
invariant 3 is about, and the baker already knows every id (and every
capacity) it packed.

A DECLARED CAPACITY MAKES <id> A LOOP-MOUNT TARGET, not a plain
directory. `nw-sup` loop-mounts a fixed-size, ext4-formatted backing
file (`<id>.img`, a SIBLING of `<id>/` so the mountpoint stays an empty
directory) at `NW_LAYER_DIR/<id>` before it ever builds the overlay's
upper/work paths -- so `upper` and `work` are created HERE, baked into
that filesystem via `mkfs.ext4 -d`, rather than as plain host
directories. Project-quota enforcement was refused (docs/ENVIRONMENT.md
already records project quota off on this machine's root device);
capacity is a filesystem boundary instead, the same way a brick already
is one.

Idempotent the same way the plain-directory case already is: an
existing backing file of the SAME declared size is left alone. This
tool creates; it does not resize or repair -- and, as of 2026-09-25,
it does not silently switch an id's representation either. Redeclaring
`layer-bytes` for an id that already has a *different* on-disk
representation (a sized store at another size, or a plain directory
where a sized store is now declared, or the reverse) is refused rather
than acted on: `tcb-review` found that the earlier version of this
tool judged only by what the CURRENT plan asked for, not by what was
already on disk, so re-baking an unchanged (id, brick) pair with a
changed `layer-bytes=` silently built a second, disjoint
representation next to the first -- the sized case mounts OVER the
plain directory's contents at boot, and the reverse leaves a sized
store's data behind an unmounted mountpoint -- with nothing anywhere
reporting it. That is invariant 6's "renamed house, orphaned data"
failure reached through a second identity axis the keying design never
accounted for. Reported at stage time now, by name, rather than
reached at boot as an empty layer nobody explained.

    python3 tools/stage-layers.py <blob> [--root DIR]

--root prefixes the layer directory, for a staged tree whose root is not
the machine root. In production the root IS /, so it is not passed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile


def layer_dir(root=""):
    """NW_LAYER_DIR from blob.h, prefixed by root. Read, not spelled --
    the same rule mkbrick.py follows for NW_BRICK_DIR, and for the same
    reason: nw-sup composes the path from the constant, so a second copy
    here is a house that mounts nothing."""
    here = os.path.dirname(os.path.abspath(__file__))
    for line in open(os.path.join(here, "..", "blob.h")):
        f = line.split()
        if len(f) >= 3 and f[0] == "#define" and f[1] == "NW_LAYER_DIR":
            return root + f[2].strip('"')
    raise SystemExit("stage-layers: blob.h has no NW_LAYER_DIR")


def _names(name):
    here = os.path.dirname(os.path.abspath(__file__))
    for line in open(os.path.join(here, "..", "blob.h")):
        f = line.split()
        if len(f) >= 3 and f[0] == "#define" and f[1] == name:
            return f[2].strip('"')
    raise SystemExit(f"stage-layers: blob.h has no {name}")


def stage(blob_path, root=""):
    """Create <root><NW_LAYER_DIR>/<id>/{upper,work} for every id the plan
    declares. Returns the ids. Idempotent: staging twice keeps the data,
    which is the whole point of the layer."""
    side = blob_path + ".layers"
    # THE BLOB MUST BE THE ONE `.sha256` WAS WRITTEN FOR, which is
    # WEAKER than "the sidecar describes the blob" -- the sentence that
    # stood here, next to a check that does not make it. `.sha256` is
    # the hash of the BLOB, so it says the blob is unchanged since the
    # sidecars were written; it says nothing about the sidecar's own
    # contents. `drift` replaced this sidecar with garbage, left the
    # blob and `.sha256` alone, and this tool created the directories
    # the garbage named without a remark. What the check does buy is
    # the interrupted-stage case below, which is what it was added
    # for. This tool is the
    # one `.claude/rules/runtime.md`'s THE RECOVERY tells an operator to
    # run on a slot. `tools/stage-candidate.py` renames `.sha256`, then
    # `.layers`, then the blob, so an interrupted stage leaves new
    # sidecars beside an old blob -- and this tool would then create the
    # layers of a plan that is not there, after which the slot boots
    # into `FAIL mount layer`. The stager learned to check this and this
    # reader did not; `control` found the asymmetry by grepping for who
    # else reads the file.
    #
    # Through `.sha256` rather than by parsing the blob: a third copy of
    # the unit layout is the drift class this file's docstring is about.
    # ABSENT `.sha256` IS FATAL TOO, and the first version of this said
    # otherwise on a producer that does not exist: the one writer of
    # `.layers` is the baker, which writes `.sha256` beside it in the
    # same function, unconditionally, and `stage-candidate` renames
    # both. So a blob
    # "from somewhere that writes no sidecars" has no `.layers` either
    # and is refused below. The reachable way to have one without the
    # other is to have LOST the `.sha256`, which is the damage case.
    # It also made the two readers answer the same input differently:
    # the stager refuses an unreadable live `.sha256` and this one
    # proceeded. `control`.
    import hashlib
    sha = blob_path + ".sha256"
    if os.path.exists(side):
        try:
            want = open(sha).read().strip()
            got = hashlib.sha256(open(blob_path, "rb").read()).hexdigest()
        except OSError as e:
            raise SystemExit(
                f"stage-layers: there is a layer sidecar beside "
                f"{blob_path} but its hash sidecar cannot be read "
                f"({e}), so nothing says the layer list describes this "
                f"blob. Refusing.")
        if want != got:
            raise SystemExit(
                f"stage-layers: {sha} does not describe {blob_path}, so "
                f"the sidecars beside it are stale -- an interrupted "
                f"stage leaves exactly this. Staging the layers they "
                f"name would create the layers of a plan that is not "
                f"there. Refusing. Write the hexdigest alone into that "
                f"file if you are repairing it -- `sha256sum` emits "
                f"'<hash>  <name>', which this compares whole.")
    # FIELD 0 IS THE ID; field 1 is the brick it stacks over and field 2
    # is layer_bytes, and this tool has no use for the brick -- it
    # creates directories or a sized store, and the brick does not bear
    # on either. A one- or two-field sidecar (the format before the
    # brick, then before layer_bytes, was added) therefore still
    # answers this tool's question completely for the fields it has:
    # missing layer_bytes reads as 0 (unset), the plain-directory case,
    # which is what every such sidecar meant before this field existed.
    #
    # That is NOT the two-readers-disagreeing defect `control` found
    # above, which was this tool and the stager answering the SAME
    # question differently. `tools/stage-candidate.py` refuses a
    # short sidecar because the question it asks -- does this
    # candidate reuse a live layer over a DIFFERENT brick -- has no
    # answer without field 1. Different question, different verdict.
    try:
        rows = [l.split() for l in open(side) if l.strip()]
    except OSError as e:
        raise SystemExit(
            f"stage-layers: no layer sidecar beside {blob_path} ({e}). The "
            f"baker writes it; a blob baked by something else has to say "
            f"which layers it wants some other way.")
    ids = [r[0] for r in rows]
    sizes = {r[0]: (int(r[2]) if len(r) >= 3 else 0) for r in rows}
    base = layer_dir(root)
    upper, work = _names("NW_LAYER_UPPER"), _names("NW_LAYER_WORK")
    suffix = _names("NW_LAYER_STORE_SUFFIX")
    for i in ids:
        # The ids came from the baker, which validated them, and nw-check
        # and nw-sup each validate them again. Checked a fourth time here
        # because this one turns a string into a mkdir on the machine
        # root, and a sidecar is a plain file anyone can edit.
        # The width from blob.h, and the alphabet spelled out. This read
        # `len(i) >= 32` -- a literal, in the file whose own docstring
        # invokes invariant 3 -- and `c.isalnum()`, which accepts
        # 'caf\u00e9' and Cyrillic 'a' where the baker, nwcheck.c and
        # nw-sup all spell [A-Za-z0-9_-]. It was the loosest of the four
        # checks that exist for the same reason. `drift`, `tcb-review`.
        ok = (i and len(i) < int(_names("NW_NAME_LEN"))
              and all(("a" <= c <= "z") or ("A" <= c <= "Z")
                      or ("0" <= c <= "9") or c in "_-" for c in i))
        if not ok:
            raise SystemExit(
                f"stage-layers: {i!r} is not a layer id. It names a "
                f"directory this tool creates, so it is checked here too.")
        nbytes = sizes.get(i, 0)
        if nbytes < 0:
            raise SystemExit(
                f"stage-layers: {i!r} has a negative layer_bytes "
                f"({nbytes}) in the sidecar -- the baker never writes "
                f"one, so this is a hand-edited or corrupt file.")
        # The MOUNTPOINT is created either way -- a sized layer's own
        # <id>/ must exist and be empty for nw-sup to loop-mount onto,
        # exactly like the unsized case's plain directory. What differs
        # is whether upper/work are created HERE as plain subdirectories
        # (unsized) or baked into a sized backing file nw-sup mounts
        # there instead (sized) -- never both, or the sized mount would
        # hide stray plain-directory writes underneath it.
        os.makedirs(os.path.join(base, i), exist_ok=True)
        # WHICH REPRESENTATION IS ALREADY ON DISK, asked before acting --
        # not "what does this plan ask for", which is the question the
        # earlier version answered alone and got wrong. `<id>.img`
        # existing means a sized store was built here before; the plain
        # `upper` subdirectory existing (with no `.img` beside it) means
        # this id was staged unsized before. An id can be neither (never
        # staged) but never both -- the branch below never creates one
        # while the other is present.
        img = os.path.join(base, i + suffix)
        has_img = os.path.exists(img)
        upper_path = os.path.join(base, i, upper)
        has_plain = os.path.exists(upper_path)
        if nbytes:
            if has_img:
                existing = os.path.getsize(img)
                if existing != nbytes:
                    raise SystemExit(
                        f"stage-layers: {i!r} already has a sized store "
                        f"of {existing} bytes on disk; this plan declares "
                        f"layer-bytes={nbytes}. Refusing rather than "
                        f"silently keeping the old size or resizing in "
                        f"place -- this tool creates, it does not resize. "
                        f"Delete {img} first if the resize is intentional "
                        f"(this discards the layer's data; see "
                        f".claude/rules/runtime.md's THE RECOVERY).")
                # same size as what is already built: idempotent, as the
                # plain-directory case already is.
            elif has_plain:
                raise SystemExit(
                    f"stage-layers: {i!r} already has an unsized "
                    f"(plain-directory) store on disk; this plan declares "
                    f"layer-bytes={nbytes}. Refusing rather than silently "
                    f"orphaning the existing data under a new sized mount "
                    f"-- see .claude/rules/runtime.md's THE RECOVERY if "
                    f"converting it is intentional.")
            else:
                _make_sized_store(base, i, nbytes, upper, work)
        else:
            if has_img:
                raise SystemExit(
                    f"stage-layers: {i!r} already has a sized store "
                    f"({os.path.getsize(img)} bytes) on disk; this plan "
                    f"declares no layer-bytes (unsized). Refusing rather "
                    f"than silently stranding the sized store's data "
                    f"behind an unsized mountpoint -- delete {img} first "
                    f"if converting to unsized is intentional (this "
                    f"discards the layer's data). THE RECOVERY in "
                    f".claude/rules/runtime.md does not reach this file: "
                    f"it removes only the mountpoint directory, a "
                    f"SIBLING of this one, which is exactly why the two "
                    f"are separate paths.")
            else:
                for leaf in (upper, work):
                    os.makedirs(os.path.join(base, i, leaf), exist_ok=True)
    return ids


def _make_sized_store(base, layer_id, nbytes, upper, work):
    """Create <base>/<layer_id><suffix>, a fixed-size ext4 image
    pre-populated with empty upper/ and work/ directories.

    `stage()` is `_make_sized_store`'s only caller and calls it only when
    it has already established that nothing is on disk for this id yet
    -- a same-size existing store is left alone by `stage()` itself
    without calling this function at all, and a different-size or
    wrong-representation one is refused before reaching here. The
    early-return guard below is defence in depth against a second
    caller reappearing, not the mechanism that makes staging idempotent
    -- that mechanism moved to `stage()`, where the disk is actually
    inspected, after `tcb-review` found this function's own idempotency
    checked only its own output and not what else was already staged
    for the same id.

    Built exactly like bakery/mkbrick.py builds a brick image: into a
    temp name in the same directory, then os.replace()'d into place, so
    a half-written store can never occupy the path nw-sup will open.
    mkfs.ext4's own -d flag populates the new filesystem directly from
    a template directory tree, the same "build from a directory" shape
    mkfs.erofs already uses for bricks -- no mount/unmount needed here
    at all.
    """
    suffix = _names("NW_LAYER_STORE_SUFFIX")
    final = os.path.join(base, layer_id + suffix)
    if os.path.exists(final):
        return
    # BOTH questions, not the tool alone -- the same pairing
    # bakery/test_fold.py's _erofs_ok() already makes for mkfs.erofs
    # (harness.md's "detect the capability, not a tool that implies
    # it"). The tool can be installed on a kernel with no ext4 driver,
    # and a kernel can drive ext4 through a build with no mkfs.ext4
    # installed to format one; either alone answers only half the
    # question this function needs answered.
    if not shutil.which("mkfs.ext4"):
        raise SystemExit(
            "stage-layers: mkfs.ext4 is not installed. This is a SKIP, "
            "not a pass: no sized layer store was created.")
    try:
        if "ext4" not in open("/proc/filesystems").read():
            raise SystemExit(
                "stage-layers: the kernel has no ext4 driver. This is a "
                "SKIP, not a pass: no sized layer store was created.")
    except OSError as e:
        raise SystemExit(f"stage-layers: cannot read /proc/filesystems "
                          f"({e})")
    with tempfile.TemporaryDirectory(prefix=".stage-layers-tmpl-") as tmpl:
        os.makedirs(os.path.join(tmpl, upper))
        os.makedirs(os.path.join(tmpl, work))
        fd, tmp = tempfile.mkstemp(
            prefix=".stage-layers-", suffix=".img", dir=base)
        os.close(fd)
        try:
            os.truncate(tmp, nbytes)
            cmd = ["mkfs.ext4", "-q", "-F", "-d", tmpl, tmp]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit(
                    f"stage-layers: mkfs.ext4 failed ({r.returncode}) "
                    f"sizing {layer_id!r} to {nbytes} bytes\n"
                    f"  {' '.join(cmd)}\n{r.stdout}{r.stderr}")
            os.replace(tmp, final)
            tmp = None
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stage-layers")
    ap.add_argument("blob")
    ap.add_argument("--root", default="")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    ids = stage(a.blob, a.root)
    if not a.quiet:
        print(f"stage-layers: {len(ids)} layer(s) under {layer_dir(a.root)}"
              + (": " + " ".join(ids) if ids else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
