#!/usr/bin/env python3
"""Wire-order reproduction: does fd 3+k mean a *peer*, or just "the k-th edge
in file order that mentions me"?

Claim under test (electrician.c:189-192 + :216): a unit's wires are packed in
blob edge-declaration order and the unit is told only a count, so reordering
two edge declarations silently swaps which peer lands on which descriptor.

Method, for both a 3-unit plan and a 63-unit plan:
  - bake plan FWD and plan REV. Same units, same peer set, same edge count,
    same everything except the ORDER of the `wire` lines. CRC recomputed.
  - prove structurally that the two blobs differ only in the edge table
    ordering (identical header-minus-crc, identical unit table, identical
    multiset of edges).
  - boot each under `unshare --pid --fork --mount-proc` so nw-root is real
    PID 1, and read the hub's one-line fd->peer map.
  - compare the maps.

Exit 0 = fd->peer mapping is stable across edge reordering (claim wrong).
Exit 1 = mapping flipped (claim reproduced).
Not in the TCB.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STAGE = "/tmp/nw-init-run"
CC = os.path.join(ROOT, "bakery", "nw-cc.py")

HDR = struct.Struct("<8sIII")
UNIT_LEN = 32 + 128 + struct.calcsize("<BBHBB")
EDGE = struct.Struct("<HH")


def run(cmd, env=None):
    p = subprocess.run(cmd, capture_output=True, env=env)
    p.out = (p.stdout or b"").decode("utf-8", "replace")
    p.err = (p.stderr or b"").decode("utf-8", "replace")
    return p


def boot(plan, hold, expect=None):
    cmd = ["unshare", "--pid", "--fork", "--mount-proc", "--",
           f"{STAGE}/nw-root", "--hold-ms", str(hold), plan]
    env = None
    if expect:
        env = dict(os.environ)
        # Inherited down nw-root -> electrician -> nw-sup -> house.
        env["NW_HUB_EXPECT"] = ",".join(expect)
    p = run(cmd, env)
    return cmd, p.returncode, p.out + p.err


def hubbind(out):
    lines = [ln for ln in out.splitlines() if "hubbind " in ln]
    if len(lines) != 1:
        raise SystemExit(f"expected exactly one hubbind line, got {len(lines)}\n{out}")
    return lines[0][lines[0].index("hubbind "):].strip()


def bake(city_text, city_path, blob_path):
    open(city_path, "w").write(city_text)
    p = run(["python3", CC, "--city", city_path, "--out", blob_path])
    if p.returncode != 0:
        raise SystemExit(f"bake failed for {city_path}\n{p.out}{p.err}")
    return p.out.strip()


def parse(blob_path):
    b = open(blob_path, "rb").read()
    magic, nu, ne, crc = HDR.unpack_from(b, 0)
    off = HDR.size
    units = [b[off + i * UNIT_LEN: off + (i + 1) * UNIT_LEN] for i in range(nu)]
    off += nu * UNIT_LEN
    edges = [EDGE.unpack_from(b, off + i * EDGE.size) for i in range(ne)]
    names = [u[:32].split(b"\x00")[0].decode() for u in units]
    return dict(raw=b, nu=nu, ne=ne, crc=crc, units=units, edges=edges, names=names)


def fnv1a_tokens(tokens):
    """Same digest the hub computes: FNV-1a over tok0\\0tok1\\0..."""
    h = 2166136261
    for t in tokens:
        for c in t.encode() + b"\x00":
            h = ((h ^ c) * 16777619) & 0xFFFFFFFF
    return h


def hubmap(out):
    lines = [ln for ln in out.splitlines() if "hubmap " in ln]
    if len(lines) != 1:
        raise SystemExit(f"expected exactly one hubmap line, got {len(lines)}\n{out}")
    return lines[0][lines[0].index("hubmap "):].strip()


def digest_of(line):
    for tok in line.split():
        if tok.startswith("digest="):
            return tok.split("=", 1)[1]
    raise SystemExit(f"no digest in: {line}")


def structural_diff(fwd, rev, label):
    print(f"-- {label}: structural comparison of the two blobs")
    same_units = fwd["units"] == rev["units"]
    same_counts = (fwd["nu"], fwd["ne"]) == (rev["nu"], rev["ne"])
    same_multiset = sorted(tuple(sorted(e)) for e in fwd["edges"]) == \
                    sorted(tuple(sorted(e)) for e in rev["edges"])
    diff_order = fwd["edges"] != rev["edges"]
    print(f"   unit table byte-identical : {same_units}")
    print(f"   (n_units, n_edges) equal  : {same_counts} {(fwd['nu'], fwd['ne'])}")
    print(f"   edge multiset equal       : {same_multiset}")
    print(f"   edge ORDER differs        : {diff_order}")
    print(f"   crc32 fwd=0x{fwd['crc']:08x} rev=0x{rev['crc']:08x} (recomputed)")
    if not (same_units and same_counts and same_multiset and diff_order):
        raise SystemExit("plans are not 'identical but for edge order' -- test invalid")


def case(label, peers, hold, show_expect=True):
    hub = f"{STAGE}/unit-hub"
    ident = f"{STAGE}/unit-ident"
    houses = f"house hub {hub} lids=none\n" + \
             "".join(f"house {p} {ident} lids=none\n" for p in peers)
    fwd_wires = "".join(f"wire hub {p}\n" for p in peers)
    rev_wires = "".join(f"wire hub {p}\n" for p in reversed(peers))

    tag = label.replace(" ", "-")
    fb, rb = f"{STAGE}/{tag}-fwd.blob", f"{STAGE}/{tag}-rev.blob"
    print(f"== {label}: 1 hub with {len(peers)} edges to {len(peers)} distinct peers ==")
    print("   baked FWD:", bake(houses + fwd_wires, f"{STAGE}/{tag}-fwd.city", fb))
    print("   baked REV:", bake(houses + rev_wires, f"{STAGE}/{tag}-rev.city", rb))

    for b in (fb, rb):
        c = run([f"{STAGE}/nw-check", b])
        print(f"   nw-check {os.path.basename(b)} rc={c.returncode} {(c.out + c.err).strip()}")
        if c.returncode != 0:
            raise SystemExit("checker rejected a plan it should accept")

    pf, pr = parse(fb), parse(rb)
    structural_diff(pf, pr, label)
    print(f"   FWD edges: {pf['edges'][:4]}{' ...' if pf['ne'] > 4 else ''}")
    print(f"   REV edges: {pr['edges'][:4]}{' ...' if pr['ne'] > 4 else ''}")

    cmd, rc, out = boot(fb, hold, expect=peers)
    print("\n   $ NW_HUB_EXPECT=" + ",".join(peers[:4])
          + ("..." if len(peers) > 4 else "") + " \\\n     " + " ".join(cmd))
    fwd_line = hubmap(out)
    fwd_bind = hubbind(out)
    print(f"   rc={rc}")
    print("   " + fwd_line)
    print("   " + fwd_bind)

    cmd, rc, out = boot(rb, hold, expect=peers)
    print("\n   $ NW_HUB_EXPECT=" + ",".join(peers[:4])
          + ("..." if len(peers) > 4 else "") + " \\\n     " + " ".join(cmd))
    rev_line = hubmap(out)
    rev_bind = hubbind(out)
    print(f"   rc={rc}")
    print("   " + rev_line)
    print("   " + rev_bind)

    exp_fwd = f"0x{fnv1a_tokens(['IAM=' + p for p in peers]):08x}"
    exp_rev = f"0x{fnv1a_tokens(['IAM=' + p for p in reversed(peers)]):08x}"
    got_fwd, got_rev = digest_of(fwd_line), digest_of(rev_line)
    print(f"\n   digest if fd 3+k == k-th DECLARED edge : fwd={exp_fwd} rev={exp_rev}")
    print(f"   digest observed                        : fwd={got_fwd} rev={got_rev}")
    print(f"   observed matches declaration-order model: "
          f"{got_fwd == exp_fwd and got_rev == exp_rev}")

    flipped = fwd_line != rev_line
    print(f"   fd->peer map changed when only the edge ORDER changed: {flipped}")

    bind_ok = ("status=OK" in fwd_bind) and ("status=OK" in rev_bind)
    print(f"   name->fd binding resolved and asserted in BOTH orderings: {bind_ok}")
    print("   (binding is the real criterion: stability is not knowability)\n")
    return flipped, bind_ok


def main():
    for exe in ("nw-root", "nw-check", "unit-hub", "unit-ident"):
        if not os.path.exists(f"{STAGE}/{exe}"):
            raise SystemExit(f"missing {STAGE}/{exe} -- run `make stage` first")
    print("== wire-order: position vs name binding ==\n")
    small_flip, small_bind = case("small", ["north", "south"], 900)
    big_peers = [f"h{i:02d}" for i in range(1, 63)]   # 62 peers + hub = 63 units
    big_flip, big_bind = case("large-N", big_peers, 2500)
    print("=" * 60)

    # The criterion is BINDING, not stability. fd 3+k is still the k-th
    # declared edge -- the map is still expected to flip. What must hold is
    # that a unit naming a peer resolves it to the right descriptor in BOTH
    # orderings. A green run on stability alone would be the sorting trap.
    print(f"ordering: map flips with edge order   small={small_flip} largeN={big_flip}")
    print(f"binding : name->fd asserted OK        small={small_bind} largeN={big_bind}")
    if small_bind and big_bind:
        print("\nVERDICT: BINDING HOLDS at N=2 and N=62.")
        print("A unit names its peer and gets the right descriptor in both")
        print("orderings. Descriptor position carries no meaning.")
        return 0
    print("\nVERDICT: BINDING FAILED -- a named peer did not resolve to the")
    print("descriptor that delivered its identity. See hubbind lines above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
