#!/usr/bin/env python3
"""nw-cc stand-in (Haskell/OCaml baker). Not in the TCB.

Refuses what nwcheck.c refuses, and refuses it independently: the derived fd
budget, the closed lid set, a brick forcing newns, a bind requiring a brick,
and unique names. The baker refuses; it does not repair.

This said "encodes the Alloy assertions: unique names, derived fd budget,
closed lid set" until 2026-09-11. Two of the three are in plan.als
(`fdNeed`, the lid set); **unique names is not, and never was** -- `grep` for
"name" in plan.als returns a comment and `fact namesAreHouses`, which despite
its identifier is a nonemptiness fact about `#House`. `House` has no name
field, so there is nothing there for a uniqueness fact to be about. Found by
`drift`. Rewritten to describe what this file does rather than to claim
provenance it does not have.

Lockfile = the blob. Never rebuild-switch.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
import zlib

NAME_LEN, PATH_LEN, BRICK_HASH = 32, 128, 32
# Derived, the way blob.h derives NW_BRICK_HEX: the hex spelling is the same
# hash, so a literal 64 beside a literal 32 is the second copy the header's
# own comment argues against, on the one side its _Static_asserts cannot
# reach. `drift`.
BRICK_HEX = BRICK_HASH * 2
MAX_UNITS, MAX_BINDS, FD_RESERVED, MAX_FDS = 64, 128, 8, 1024
KIND_ONESHOT, KIND_LONGRUN = 0, 1
KINDS = {"oneshot": KIND_ONESHOT, "longrun": KIND_LONGRUN}
LID_SECCOMP, LID_LANDLOCK, LID_NEWNS, LID_NEWNET = 1, 2, 4, 8
KNOWN_LIDS = LID_SECCOMP | LID_LANDLOCK | LID_NEWNS | LID_NEWNET
LID_NAMES = {
    "none": 0, "seccomp": LID_SECCOMP, "landlock": LID_LANDLOCK,
    "newns": LID_NEWNS, "newnet": LID_NEWNET,
}


def pad(s: str, n: int) -> bytes:
    b = s.encode("ascii")
    if len(b) >= n:
        raise SystemExit(f"too long: {s}")
    return b + b"\x00" * (n - len(b))


def path_clean(p: str) -> bool:
    """Absolute, with no '..' component. Same rule as path_ok_len in
    nwcheck.c, which enforces it independently -- the baker is not in the
    TCB. Closes traversal only; a symlink escapes it. See docs/options/07."""
    return p.startswith("/") and ".." not in p.split("/")


def check(houses, binds):
    names = [h["name"] for h in houses]
    if len(names) != len(set(names)):
        raise SystemExit("duplicate name")
    if not (1 <= len(houses) <= MAX_UNITS):
        raise SystemExit("unit count")
    if len(binds) > MAX_BINDS:
        raise SystemExit("bind count")
    idx = {n: i for i, n in enumerate(names)}
    need = FD_RESERVED + len(houses) * 2
    if need > MAX_FDS:
        raise SystemExit("fd budget")
    for h in houses:
        if h["kind"] not in (KIND_ONESHOT, KIND_LONGRUN):
            raise SystemExit("kind")
        if h["lids"] & ~KNOWN_LIDS:
            raise SystemExit("lids")
        if not path_clean(h["exec"]):
            raise SystemExit(
                f"house {h['name']}: exec path must be absolute with no '..' "
                "component")
        if not h["name"] or not h["name"].replace("-", "x").replace("_", "x").isalnum():
            raise SystemExit("name")
        # Landlock grants beneath the house's root; that is only a
        # restriction when the root is a brick.
        if (h["lids"] & LID_LANDLOCK) and not h["brick"]:
            raise SystemExit(
                f"house {h['name']}: lids=...,landlock needs brick=; on the "
                "machine root the lid grants read and execute beneath / and "
                "confines nothing")
        if h["brick"]:
            # PHASE 3: brick= IS A HASH. The `..` check that was here is gone
            # because the thing it defended against cannot be written any
            # more -- 64 hex characters have no separator and no relative
            # component. The check below is a shape check, not a safety one:
            # a wrong hash names a file that is not there and the house dies
            # at `open brick image`, loudly, which is a different failure
            # from a house rooted somewhere it should not be.
            if not _is_hex64(h["brick"]):
                raise SystemExit(
                    f"house {h['name']}: brick= must be {BRICK_HEX} hex "
                    f"characters (the sha256 of the image, as mkbrick "
                    f"prints it), not a path. Phase 3 moved the plan from "
                    f"a path to a hash; nw-sup composes the path itself.")
            if h["brick"] == "0" * BRICK_HEX:
                # ALL-ZERO IS THE ONE VALUE THAT IS NOT A HASH. blob.h spends
                # the field's all-zero state on "no brick", so this plan
                # declares a brick and gets a house on the machine root: the
                # baker accepted it, the checker read it as brickless, and
                # the city booted with no `lid brick` line while every reader
                # reported success. Invariant 6's "the plan lying", reached
                # through the one value the 2^256 argument does not cover.
                # Found by `tcb-review`.
                #
                # THIS CAN ONLY LIVE HERE, and it is the exception that shows
                # what plan.md's "any rule the runtime relies on must be in
                # nwcheck.c too" is actually about. By the time the blob
                # exists the distinction is GONE -- 32 zero bytes IS the
                # no-brick encoding, and no checker can tell a plan that
                # meant it from one that did not. The representation is the
                # enforcement; the baker is the last place the intent still
                # exists.
                raise SystemExit(
                    f"house {h['name']}: brick= is all zeros, which is how "
                    f"the blob spells NO brick -- so this plan would boot a "
                    f"house on the machine root while saying it is in a "
                    f"brick. No image hashes to zero; mkbrick never prints "
                    f"this.")
            if not (h["lids"] & LID_NEWNS):
                raise SystemExit(
                    f"house {h['name']}: brick= needs lids=...,newns; a brick "
                    "is a root and pivoting without a private mount namespace "
                    "would repoint the machine's root")
            if not h["layer"]:
                raise SystemExit(
                    f"house {h['name']}: brick= needs layer=. Every house "
                    f"with a brick has exactly one writable layer -- the "
                    f"brick is what it can see and the layer is what it can "
                    f"keep. Without one its writes vanish at exit while the "
                    f"plan says it has data, and nothing errors.")
        elif h["layer"]:
            raise SystemExit(
                f"house {h['name']}: layer= without brick=; there is no "
                f"lower to overlay, so the layer would name a directory "
                f"nothing ever mounts")
        elif h["binds"]:
            raise SystemExit(
                f"house {h['name']}: bind= without brick=; there is no root "
                "to bind into")
    for unit, path in binds:
        if not path_clean(path):
            raise SystemExit(
                f"bind path must be absolute with no '..' component: {path}")
        if len(path.encode("ascii")) >= PATH_LEN:
            raise SystemExit(f"bind path too long: {path}")
    return idx


def bake(path, houses):
    binds = [(i, p) for i, h in enumerate(houses) for p in h["binds"]]
    check(houses, binds)
    unit = b""
    for h in houses:
        unit += pad(h["name"], NAME_LEN) + pad(h["exec"], PATH_LEN)
        unit += (bytes.fromhex(h["brick"]) if h["brick"]
                 else b"\0" * BRICK_HASH)
        unit += pad(h["layer"], NAME_LEN)
        # kind (the byte that was "critical" until 2026-09-10), then _pad,
        # which must stay zero -- nwcheck.c rejects a nonzero spare.
        unit += struct.pack("<BBBB", h["kind"], h["budget"], h["lids"], 0)
    table = b""
    for u, p in binds:
        table += struct.pack("<H", u) + pad(p, PATH_LEN)
    # Must equal NW_MAGIC in blob.h. tests/run.py asserts that agreement;
    # the version moves when the layout moves -- see the comment there.
    prefix = b"NWPLAN08" + struct.pack("<II", len(houses), len(binds))
    crc = zlib.crc32(prefix + struct.pack("<I", 0) + unit + table) & 0xFFFFFFFF
    blob = prefix + struct.pack("<I", crc) + unit + table
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "wb").write(blob)
    digest = hashlib.sha256(blob).hexdigest()
    open(path + ".sha256", "w").write(digest + "\n")
    # THE LAYER SIDECAR, so that whatever stages a plan does not need a
    # second copy of the blob layout to find out which directories to
    # create. Staging reads this; nw-sup does NOT create layer dirs, and
    # adding a creator there is what this file exists to avoid.
    open(path + ".layers", "w").write(
        "".join(h["layer"] + "\n" for h in houses if h["layer"]))
    print(f"wrote {path} units={len(houses)} binds={len(binds)} "
          f"crc=0x{crc:08x} bytes={len(blob)} sha256={digest}")


def _is_layer_id(v):
    """A layer-id is a NAME, with the same closed alphabet as a house name.

    Not a path, for the reason phase 3 retired the brick path: nw-sup
    composes NW_LAYER_DIR "/" <id> "/" upper itself, and a fixed alphabet
    with no separator cannot express a traversal. The length bound is
    NAME_LEN because that is the field's width."""
    return (isinstance(v, str) and 0 < len(v) < NAME_LEN
            and all(c.isalnum() or c in "_-" for c in v)
            and all(ord(c) < 128 for c in v))


def _is_hex64(v):
    """Exactly BRICK_HEX lowercase hex characters. A SHAPE check only.

    Closed alphabet and fixed length, so no separator and no relative
    component can appear -- that is the whole phase-3 argument, and it is
    checked here as well as in nwcheck.c because the baker is not in the TCB
    and a blob can arrive from anywhere.

    All-zero passes this and is refused separately at the call site, with
    its own reason. Folding it in here was tried first and produced
    `brick= must be 64 hex characters ... not a path` for a value that IS 64
    hex characters -- a true rejection under a false reason, which is this
    project's characteristic failure wearing the fix's clothes."""
    return (isinstance(v, str) and len(v) == BRICK_HEX
            and all(c in "0123456789abcdef" for c in v))


def house(name, exe, kind, budget, lids, brick="", binds=(), layer=""):
    return {"name": name, "exec": exe, "kind": kind, "budget": budget,
            "lids": lids, "brick": brick, "layer": layer,
            "binds": list(binds)}


def default_city(probe: str, lids: int):
    return [
        house("alpha", probe, KIND_ONESHOT, 3, lids),
        house("beta", probe, KIND_ONESHOT, 3, lids),
        house("gamma", probe, KIND_ONESHOT, 3, lids),
        house("delta", probe, KIND_ONESHOT, 1, lids),
    ]


def parse_lids(s: str) -> int:
    lids = 0
    for tok in s.split(","):
        tok = tok.strip().lower()
        if tok not in LID_NAMES:
            raise SystemExit(
                f"unknown lid {tok!r}: one of {', '.join(sorted(LID_NAMES))}")
        lids |= LID_NAMES[tok]
    return lids


def load_city(path: str):
    houses = []
    for raw in open(path):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] == "house":
            name, exe = parts[1], parts[2]
            budget, lids = 3, 0
            kind = None
            brick, layer, binds = "", "", []
            for kv in parts[3:]:
                k, _, v = kv.partition("=")
                if k == "critical":
                    raise SystemExit(
                        "critical= was removed on 2026-09-10: nothing a house "
                        "does halts the city")
                elif k == "budget":
                    budget = int(v)
                elif k == "window":
                    raise SystemExit(
                        "window= was removed 2026-09-11 (D18): the restart "
                        "budget is a hard total for the life of nw-sup, "
                        "not a sliding window")
                elif k == "lids":
                    lids = parse_lids(v)
                elif k == "brick":
                    brick = v
                elif k == "layer":
                    if not _is_layer_id(v):
                        raise SystemExit(
                            f"house {name}: layer= must be a name of up to "
                            f"{NAME_LEN - 1} characters from "
                            f"[A-Za-z0-9_-], not a path -- nw-sup composes "
                            f"the directory itself: {v!r}")
                    layer = v
                elif k == "bind":
                    binds.append(v)
                elif k == "kind":
                    if v not in KINDS:
                        raise SystemExit(
                            f"kind={v}: must be oneshot or longrun")
                    kind = KINDS[v]
                else:
                    raise SystemExit(f"house {name}: unknown key {k}=")
            if kind is None:
                raise SystemExit(
                    f"house {name}: kind= is required and has no default. "
                    "Use kind=oneshot (exit 0 completes, never restarted) or "
                    "kind=longrun (any exit is unexpected, including 0).")
            # exec_path is resolved inside the brick, so it is already the
            # path the house will see and must not be rewritten against the
            # baker's cwd. Without a brick it names a machine path.
            if not brick:
                exe = os.path.abspath(exe)
            elif not exe.startswith("/"):
                raise SystemExit(
                    f"house {name}: exec path must be absolute inside the "
                    "brick")
            houses.append(house(name, exe, kind, budget, lids,
                                brick, binds, layer))
        else:
            raise SystemExit(f"bad city line: {line}")
    return houses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="plan.blob")
    ap.add_argument("--probe", default="")
    ap.add_argument("--city", default="")
    ap.add_argument("--lids", default="seccomp")
    args = ap.parse_args()
    if args.city:
        houses = load_city(args.city)
    else:
        if not args.probe:
            raise SystemExit("--probe or --city required")
        houses = default_city(os.path.abspath(args.probe), parse_lids(args.lids))
    bake(args.out, houses)


if __name__ == "__main__":
    main()
