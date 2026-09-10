#!/usr/bin/env python3
"""nw-cc stand-in (Haskell/OCaml baker). Not in the TCB.

Encodes the Alloy assertions: unique names, derived fd budget,
closed lid set. Lockfile = the blob. Never rebuild-switch.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
import zlib

NAME_LEN, PATH_LEN, BRICK_LEN = 32, 128, 96
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
            if not path_clean(h["brick"]):
                raise SystemExit(
                    f"house {h['name']}: brick= must be absolute with no '..' "
                    "component; a brick that traverses out is a house rooted "
                    "on the machine")
            if len(h["brick"].encode("ascii")) >= BRICK_LEN:
                raise SystemExit(f"house {h['name']}: brick= too long")
            # A house cannot pivot into its own root without a private mount
            # namespace. The lid is not added here on the plan's behalf: a
            # lid nobody asked for is a lid nobody reviewed.
            if not (h["lids"] & LID_NEWNS):
                raise SystemExit(
                    f"house {h['name']}: brick= needs lids=...,newns; a brick "
                    "is a root and pivoting without a private mount namespace "
                    "would repoint the machine's")
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
        unit += pad(h["brick"], BRICK_LEN)
        # kind (the byte that was "critical" until 2026-09-10), then _pad,
        # which must stay zero -- nwcheck.c rejects a nonzero spare.
        unit += struct.pack("<BBHBB", h["kind"], h["budget"], h["window"],
                            h["lids"], 0)
    table = b""
    for u, p in binds:
        table += struct.pack("<H", u) + pad(p, PATH_LEN)
    prefix = b"NWPLAN05" + struct.pack("<II", len(houses), len(binds))
    crc = zlib.crc32(prefix + struct.pack("<I", 0) + unit + table) & 0xFFFFFFFF
    blob = prefix + struct.pack("<I", crc) + unit + table
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "wb").write(blob)
    digest = hashlib.sha256(blob).hexdigest()
    open(path + ".sha256", "w").write(digest + "\n")
    print(f"wrote {path} units={len(houses)} binds={len(binds)} "
          f"crc=0x{crc:08x} bytes={len(blob)} sha256={digest}")


def house(name, exe, kind, budget, window, lids, brick="", binds=()):
    return {"name": name, "exec": exe, "kind": kind, "budget": budget,
            "window": window, "lids": lids, "brick": brick,
            "binds": list(binds)}


def default_city(probe: str, lids: int):
    return [
        house("alpha", probe, KIND_ONESHOT, 3, 2, lids),
        house("beta", probe, KIND_ONESHOT, 3, 2, lids),
        house("gamma", probe, KIND_ONESHOT, 3, 2, lids),
        house("delta", probe, KIND_ONESHOT, 1, 2, lids),
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
            budget, window, lids = 3, 2, 0
            kind = None
            brick, binds = "", []
            for kv in parts[3:]:
                k, _, v = kv.partition("=")
                if k == "critical":
                    raise SystemExit(
                        "critical= was removed on 2026-09-10: nothing a house "
                        "does halts the city")
                elif k == "budget":
                    budget = int(v)
                elif k == "window":
                    window = int(v)
                elif k == "lids":
                    lids = parse_lids(v)
                elif k == "brick":
                    brick = v
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
            houses.append(house(name, exe, kind, budget, window, lids,
                                brick, binds))
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
