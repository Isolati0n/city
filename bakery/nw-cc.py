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

NAME_LEN, PATH_LEN = 32, 128
MAX_UNITS, FD_RESERVED, MAX_FDS = 64, 8, 1024
LID_SECCOMP, LID_LANDLOCK, LID_NEWNS, LID_NEWNET = 1, 2, 4, 8
KNOWN_LIDS = LID_SECCOMP | LID_LANDLOCK | LID_NEWNS | LID_NEWNET


def pad(s: str, n: int) -> bytes:
    b = s.encode("ascii")
    if len(b) >= n:
        raise SystemExit(f"too long: {s}")
    return b + b"\x00" * (n - len(b))


def check(houses):
    names = [h[0] for h in houses]
    if len(names) != len(set(names)):
        raise SystemExit("duplicate name")
    if not (1 <= len(houses) <= MAX_UNITS):
        raise SystemExit("unit count")
    idx = {n: i for i, n in enumerate(names)}
    need = FD_RESERVED + len(houses) * 2
    if need > MAX_FDS:
        raise SystemExit("fd budget")
    for name, path, budget, window, lids in houses:
        if lids & ~KNOWN_LIDS:
            raise SystemExit("lids")
        if not path.startswith("/"):
            raise SystemExit("exec_path")
        if not name or not name.replace("-", "x").replace("_", "x").isalnum():
            raise SystemExit("name")
    return idx


def bake(path, houses):
    idx = check(houses)
    unit = b""
    for name, exe, budget, window, lids in houses:
        unit += pad(name, NAME_LEN) + pad(exe, PATH_LEN)
        # _rsv0 (was "critical", removed 2026-09-10) and _pad both zero;
        # nwcheck.c rejects either being nonzero.
        unit += struct.pack("<BBHBB", 0, budget, window, lids, 0)
    prefix = b"NWPLAN03" + struct.pack("<I", len(houses))
    crc = zlib.crc32(prefix + struct.pack("<I", 0) + unit) & 0xFFFFFFFF
    blob = prefix + struct.pack("<I", crc) + unit
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "wb").write(blob)
    digest = hashlib.sha256(blob).hexdigest()
    open(path + ".sha256", "w").write(digest + "\n")
    print(f"wrote {path} units={len(houses)} crc=0x{crc:08x} bytes={len(blob)} sha256={digest}")


def default_city(probe: str, lids: int):
    return [
        ("alpha", probe, 3, 2, lids),
        ("beta", probe, 3, 2, lids),
        ("gamma", probe, 3, 2, lids),
        ("delta", probe, 1, 2, lids),
    ]


def parse_lids(s: str) -> int:
    lids = 0
    for tok in s.split(","):
        tok = tok.strip().lower()
        lids |= {
            "none": 0, "seccomp": LID_SECCOMP, "landlock": LID_LANDLOCK,
            "newns": LID_NEWNS, "newnet": LID_NEWNET,
        }.get(tok, 0)
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
            houses.append((name, os.path.abspath(exe), budget, window, lids))
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
