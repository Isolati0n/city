#!/usr/bin/env python3
"""nw-cc stand-in (Haskell/OCaml baker). Not in the TCB.

Encodes the Alloy assertions: unique names, no self-wire, derived fd budget,
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
MAX_UNITS, MAX_EDGES, FD_RESERVED, MAX_FDS = 64, 128, 8, 1024
LID_SECCOMP, LID_LANDLOCK, LID_NEWNS, LID_NEWNET = 1, 2, 4, 8
KNOWN_LIDS = LID_SECCOMP | LID_LANDLOCK | LID_NEWNS | LID_NEWNET


def pad(s: str, n: int) -> bytes:
    b = s.encode("ascii")
    if len(b) >= n:
        raise SystemExit(f"too long: {s}")
    return b + b"\x00" * (n - len(b))


def check(houses, wires):
    names = [h[0] for h in houses]
    if len(names) != len(set(names)):
        raise SystemExit("duplicate name")
    if not (1 <= len(houses) <= MAX_UNITS):
        raise SystemExit("unit count")
    if len(wires) > MAX_EDGES:
        raise SystemExit("edge count")
    idx = {n: i for i, n in enumerate(names)}
    seen = set()
    for a, b in wires:
        if a not in idx or b not in idx:
            raise SystemExit("edge index")
        if a == b:
            raise SystemExit("self-edge")
        key = tuple(sorted((idx[a], idx[b])))
        if key in seen:
            raise SystemExit("duplicate edge")
        seen.add(key)
    need = FD_RESERVED + len(houses) * 2 + len(wires) * 2
    if need > MAX_FDS:
        raise SystemExit("fd budget")
    # Datalog-shaped: hold(H) if incident to a wire. Isolated houses are allowed
    # only as explicit empty kits — listed, never implicit.
    held = set()
    for a, b in wires:
        held.add(a)
        held.add(b)
    isolated = [h[0] for h in houses if h[0] not in held]
    if isolated:
        print("isolated-kits", ",".join(isolated))
    for name, path, crit, budget, window, lids in houses:
        if crit not in (0, 1):
            raise SystemExit("critical")
        if lids & ~KNOWN_LIDS:
            raise SystemExit("lids")
        if not path.startswith("/"):
            raise SystemExit("exec_path")
        if not name or not name.replace("-", "x").replace("_", "x").isalnum():
            raise SystemExit("name")
    return idx


def bake(path, houses, wires):
    idx = check(houses, wires)
    unit = b""
    for name, exe, crit, budget, window, lids in houses:
        unit += pad(name, NAME_LEN) + pad(exe, PATH_LEN)
        unit += struct.pack("<BBHBB", crit, budget, window, lids, 0)
    edge = b""
    for a, b in wires:
        edge += struct.pack("<HH", idx[a], idx[b])
    prefix = b"NWPLAN02" + struct.pack("<II", len(houses), len(wires))
    crc = zlib.crc32(prefix + struct.pack("<I", 0) + unit + edge) & 0xFFFFFFFF
    blob = prefix + struct.pack("<I", crc) + unit + edge
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "wb").write(blob)
    digest = hashlib.sha256(blob).hexdigest()
    open(path + ".sha256", "w").write(digest + "\n")
    print(f"wrote {path} units={len(houses)} edges={len(wires)} crc=0x{crc:08x} bytes={len(blob)} sha256={digest}")


def default_city(probe: str, lids: int):
    return [
        ("alpha", probe, 0, 3, 2, lids),
        ("beta",  probe, 0, 3, 2, lids),
        ("gamma", probe, 0, 3, 2, lids),
        ("delta", probe, 0, 1, 2, lids),
    ], [("alpha", "beta"), ("beta", "gamma")]


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
    houses, wires = [], []
    for raw in open(path):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] == "house":
            name, exe = parts[1], parts[2]
            crit, budget, window, lids = 0, 3, 2, 0
            for kv in parts[3:]:
                k, _, v = kv.partition("=")
                if k == "critical":
                    crit = int(v)
                elif k == "budget":
                    budget = int(v)
                elif k == "window":
                    window = int(v)
                elif k == "lids":
                    lids = parse_lids(v)
            houses.append((name, os.path.abspath(exe), crit, budget, window, lids))
        elif parts[0] == "wire":
            wires.append((parts[1], parts[2]))
        else:
            raise SystemExit(f"bad city line: {line}")
    return houses, wires


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="plan.blob")
    ap.add_argument("--probe", default="")
    ap.add_argument("--city", default="")
    ap.add_argument("--lids", default="seccomp")
    args = ap.parse_args()
    if args.city:
        houses, wires = load_city(args.city)
    else:
        if not args.probe:
            raise SystemExit("--probe or --city required")
        houses, wires = default_city(os.path.abspath(args.probe), parse_lids(args.lids))
    bake(args.out, houses, wires)


if __name__ == "__main__":
    main()
