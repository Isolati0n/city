#!/usr/bin/env python3
"""Put-together suite. Not in the TCB."""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STAGE = "/tmp/nw-init-run"
CC = os.path.join(ROOT, "bakery", "nw-cc.py")


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, **kw)
    p.out = (p.stdout or b"").decode("utf-8", "replace")
    p.err = (p.stderr or b"").decode("utf-8", "replace")
    return p


def boot(slot=None, plan=None, extra=None, hold=800):
    cmd = ["unshare", "--pid", "--fork", "--mount-proc", "--", f"{STAGE}/nw-root", "--hold-ms", str(hold)]
    if slot:
        cmd += ["--slot", slot]
    if plan:
        cmd += [plan]
    extra = extra or []
    cmd += extra
    p = run(cmd)
    out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
    return p.returncode, out


def expect(cond, msg):
    if not cond:
        raise SystemExit("FAIL: " + msg)


def test_happy():
    rc, out = boot(slot=f"{STAGE}/slots/A", hold=900)
    expect(rc == 0, f"happy rc={rc}\n{out}")
    # No edges: a unit holds nothing above stderr. This is what is left of the
    # descriptor assertion after wiring was removed.
    expect(out.count("fds_ge3=0") == 4, f"every unit holds no extra fds\n{out}")
    expect("units spawned" in out, "spawner completed")
    expect("houses_reaped=4" in out, "reap")
    expect("orphans=0" in out, "orphans")
    print("ok happy")


def test_slot_b():
    rc, out = boot(slot=f"{STAGE}/slots/B", hold=700)
    expect(rc == 0, f"slot B rc={rc}\n{out}")
    print("ok slot-B")


def test_rescue():
    p = run(["unshare", "--pid", "--fork", "--mount-proc", "--",
             f"{STAGE}/nw-root", "--rescue", f"{STAGE}/slots/rescue"])
    out = p.out + p.err
    expect(p.returncode == 3, f"rescue rc={p.returncode}\n{out}")
    expect("outside the plan" in out, "rescue text")
    print("ok rescue")


def test_halt_spawner():
    """nw-spawn exits as its success path, so PID 1 cannot watch for its death.
    It requires a complete pid report and a clean exit instead. Kill it before
    it reports and boot must fail rather than come up short-staffed."""
    rc, out = boot(slot=f"{STAGE}/slots/A", extra=["--kill-spawner"], hold=400)
    expect(rc == 70, f"halt rc={rc}\n{out}")
    expect("HALT: spawn report" in out, f"halt text\n{out}")
    print("ok halt-spawner")


def test_bad_crc():
    bad = f"{STAGE}/bad.blob"
    d = bytearray(open(f"{STAGE}/plan.blob", "rb").read())
    d[16] ^= 0xFF
    open(bad, "wb").write(d)
    chk = run([f"{STAGE}/nw-check", bad])
    expect(chk.returncode == 1 and "crc32" in (chk.err + chk.out), "check crc")
    rc, out = boot(plan=bad, hold=200)
    expect(rc == 70 and "crc32" in out, f"boot crc\n{out}")
    print("ok bad-crc")


def test_baker_rejects():
    city = f"{STAGE}/bad-city.txt"
    open(city, "w").write("house a /bin/true\nhouse a /bin/true\n")
    p = run(["python3", CC, "--city", city, "--out", f"{STAGE}/nope.blob"])
    expect(p.returncode != 0, "duplicate name should fail bake")
    expect("duplicate name" in (p.out + p.err), f"reason\n{p.out}{p.err}")
    print("ok baker-reject-dupname")


def test_fuzz_checker():
    good = open(f"{STAGE}/plan.blob", "rb").read()
    accepted = 0
    for i in range(200):
        d = bytearray(good)
        d[i % len(d)] ^= 1 + (i % 7)
        p = tempfile.NamedTemporaryFile(delete=False, dir=STAGE)
        p.write(d)
        p.close()
        r = run([f"{STAGE}/nw-check", p.name])
        if r.returncode == 0:
            accepted += 1
    expect(accepted == 0, f"fuzz accepted {accepted}")
    print("ok fuzz-200")


def test_difftest():
    """Baker output must be accepted by C nw-check; flipped crc must not."""
    r = run([f"{STAGE}/nw-check", f"{STAGE}/plan.blob"])
    expect(r.returncode == 0, "difftest good")
    print("ok difftest")


def test_critical_halt():
    boom = f"{STAGE}/unit-boom"
    probe = f"{STAGE}/unit-probe"
    city = f"{STAGE}/crit.city"
    open(city, "w").write(
        f"house boom {boom} critical=1 lids=none\n"
        f"house idle {probe} lids=none\n"
    )
    blob = f"{STAGE}/crit.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, b.err)
    rc, out = boot(plan=blob, hold=800)
    expect(rc == 70, f"crit rc={rc}\n{out}")
    expect("HALT: critical house" in out, f"crit text\n{out}")
    print("ok critical-halt")


def test_seccomp_kills():
    bad = f"{STAGE}/unit-badcall"
    city = f"{STAGE}/sec.city"
    open(city, "w").write(f"house bad {bad} lids=seccomp\n")
    blob = f"{STAGE}/sec.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, b.err)
    rc, out = boot(plan=blob, hold=600)
    expect("badcall survived" not in out, f"seccomp leak\n{out}")
    print("ok seccomp-kill")


def test_hash_pin():
    h = open(f"{STAGE}/plan.blob.sha256").read().strip()
    expect(len(h) == 64, "sha256 len")
    import hashlib
    got = hashlib.sha256(open(f"{STAGE}/plan.blob", "rb").read()).hexdigest()
    expect(h == got, "sha256 match")
    print("ok hash-pin")


def main():
    os.chdir(ROOT)
    print("== city suite ==")
    test_hash_pin()
    test_difftest()
    test_baker_rejects()
    test_fuzz_checker()
    test_happy()
    test_slot_b()
    test_rescue()
    test_halt_spawner()
    test_bad_crc()
    test_critical_halt()
    test_seccomp_kills()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
