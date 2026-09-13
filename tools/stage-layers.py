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
one per line, as `<layer-id> <brick>`; this tool reads the id and ignores
the brick. That is why this tool does not parse the blob: a third copy
of the unit layout (after blob.h and the baker) is the drift class
invariant 3 is about, and the baker already knows every id it packed.

    python3 tools/stage-layers.py <blob> [--root DIR]

--root prefixes the layer directory, for a staged tree whose root is not
the machine root. In production the root IS /, so it is not passed.
"""
from __future__ import annotations

import argparse
import os
import sys


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
    # FIELD 0 IS THE ID; field 1 is the brick it stacks over, and this
    # tool has no use for it -- it creates directories. A one-field
    # sidecar (the format before the brick was added) therefore still
    # answers this tool's question completely, and is accepted.
    #
    # That is NOT the two-readers-disagreeing defect `control` found
    # above, which was this tool and the stager answering the SAME
    # question differently. `tools/stage-candidate.py` refuses a
    # one-field sidecar because the question it asks -- does this
    # candidate reuse a live layer over a DIFFERENT brick -- has no
    # answer without field 1. Different question, different verdict.
    try:
        ids = [l.split()[0] for l in open(side) if l.strip()]
    except OSError as e:
        raise SystemExit(
            f"stage-layers: no layer sidecar beside {blob_path} ({e}). The "
            f"baker writes it; a blob baked by something else has to say "
            f"which layers it wants some other way.")
    base = layer_dir(root)
    upper, work = _names("NW_LAYER_UPPER"), _names("NW_LAYER_WORK")
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
        for leaf in (upper, work):
            os.makedirs(os.path.join(base, i, leaf), exist_ok=True)
    return ids


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
