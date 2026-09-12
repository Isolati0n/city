#!/usr/bin/env python3
"""Create the writable layer directories a plan declares.

THE ONLY THING THAT CREATES THEM, and that is the design rather than a
convenience. `nw-sup` deliberately does not: a supervisor that mkdir'd a
missing layer would turn "the stager never ran" into "the house silently
got an empty layer", which is the same defect as a house whose data sits
orphaned under a renamed key. A missing layer is a loud failure at
nw-sup's `mount layer` instead.

Today the harness calls this, because nothing stages candidates yet. When
a real stager lands it calls this too -- the point is that there is one
creator and it takes the layer ids from the plan, not two creators that
have to agree.

The ids come from the `.layers` sidecar the baker writes beside the blob,
one per line. That is why this tool does not parse the blob: a third copy
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
    try:
        ids = [l.strip() for l in open(side) if l.strip()]
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
