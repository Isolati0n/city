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
# The staged tree mirrors the production layout and differs only in prefix:
# BIN is /nw/bin on a real machine, SLOTS is /efi/slots. Scratch files that
# have no production counterpart live in WORK.
BIN = f"{STAGE}/nw/bin"
SLOTS = f"{STAGE}/efi/slots"
WORK = f"{STAGE}/work"
os.makedirs(WORK, exist_ok=True)
CC = os.path.join(ROOT, "bakery", "nw-cc.py")


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, **kw)
    p.out = (p.stdout or b"").decode("utf-8", "replace")
    p.err = (p.stderr or b"").decode("utf-8", "replace")
    return p


def boot(slot=None, plan=None, extra=None, hold=800):
    cmd = ["unshare", "--pid", "--fork", "--mount-proc", "--", f"{BIN}/nw-root", "--hold-ms", str(hold)]
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
    rc, out = boot(slot=f"{SLOTS}/A", hold=900)
    expect(rc == 0, f"happy rc={rc}\n{out}")
    # No edges: a unit holds nothing above stderr. This is what is left of the
    # descriptor assertion after wiring was removed.
    expect(out.count("fds_ge3=0") == 4, f"every unit holds no extra fds\n{out}")
    expect("units spawned" in out, "spawner completed")
    expect("houses_reaped=4" in out, "reap")
    expect("orphans=0" in out, "orphans")
    print("ok happy")


def test_slot_b():
    """A and B hold genuinely different plans -- A the four-unit probe city,
    B a two-unit one. They were byte-identical copies until 2026-09-10, which
    meant this test could not have detected a slot-selection bug: booting the
    wrong slot produced the same output. The unit count is the assertion."""
    a_rc, a_out = boot(slot=f"{SLOTS}/A", hold=900)
    expect(a_rc == 0, f"slot A rc={a_rc}\n{a_out}")
    expect("houses=4" in a_out, f"slot A should hold four units\n{a_out}")

    rc, out = boot(slot=f"{SLOTS}/B", hold=700)
    expect(rc == 0, f"slot B rc={rc}\n{out}")
    expect("houses=2" in out, f"slot B should hold two units\n{out}")
    expect("solo" in out and "duo" in out, f"slot B unit names\n{out}")
    print("ok slot-B")


def test_rescue():
    p = run(["unshare", "--pid", "--fork", "--mount-proc", "--",
             f"{BIN}/nw-root", "--rescue", f"{BIN}"])
    out = p.out + p.err
    expect(p.returncode == 3, f"rescue rc={p.returncode}\n{out}")
    expect("outside the plan" in out, "rescue text")
    print("ok rescue")


def test_halt_spawner():
    """nw-spawn exits as its success path, so PID 1 cannot watch for its death.
    It requires a complete pid report and a clean exit instead. Kill it before
    it reports and boot must fail rather than come up short-staffed."""
    rc, out = boot(slot=f"{SLOTS}/A", extra=["--kill-spawner"], hold=400)
    expect(rc == 70, f"halt rc={rc}\n{out}")
    expect("HALT: spawn report" in out, f"halt text\n{out}")
    print("ok halt-spawner")


def test_bad_crc():
    bad = f"{WORK}/bad.blob"
    d = bytearray(open(f"{SLOTS}/A/plan.blob", "rb").read())
    d[16] ^= 0xFF
    open(bad, "wb").write(d)
    chk = run([f"{BIN}/nw-check", bad])
    expect(chk.returncode == 1 and "crc32" in (chk.err + chk.out), "check crc")
    rc, out = boot(plan=bad, hold=200)
    expect(rc == 70 and "crc32" in out, f"boot crc\n{out}")
    print("ok bad-crc")


def test_baker_rejects():
    city = f"{WORK}/bad-city.txt"
    open(city, "w").write("house a /bin/true kind=oneshot\n"
                          "house a /bin/true kind=oneshot\n")
    p = run(["python3", CC, "--city", city, "--out", f"{WORK}/nope.blob"])
    expect(p.returncode != 0, "duplicate name should fail bake")
    expect("duplicate name" in (p.out + p.err), f"reason\n{p.out}{p.err}")
    print("ok baker-reject-dupname")


def test_fuzz_checker():
    good = open(f"{SLOTS}/A/plan.blob", "rb").read()
    accepted = 0
    for i in range(200):
        d = bytearray(good)
        d[i % len(d)] ^= 1 + (i % 7)
        p = tempfile.NamedTemporaryFile(delete=False, dir=WORK)
        p.write(d)
        p.close()
        r = run([f"{BIN}/nw-check", p.name])
        if r.returncode == 0:
            accepted += 1
    expect(accepted == 0, f"fuzz accepted {accepted}")
    print("ok fuzz-200")


def test_difftest():
    """Baker output must be accepted by C nw-check; flipped crc must not."""
    r = run([f"{BIN}/nw-check", f"{SLOTS}/A/plan.blob"])
    expect(r.returncode == 0, "difftest good")
    print("ok difftest")


def test_kind_required():
    """kind= is explicit or it is a bake error. No default, no inference --
    a silent default is the failure mode this project keeps designing out."""
    city = f"{WORK}/nokind.city"
    open(city, "w").write("house a /bin/true lids=none\n")
    p = run(["python3", CC, "--city", city, "--out", f"{WORK}/nope.blob"])
    expect(p.returncode != 0, "missing kind should fail the bake")
    expect("kind= is required" in (p.out + p.err), f"reason\n{p.out}{p.err}")
    open(city, "w").write("house a /bin/true kind=daemon lids=none\n")
    p = run(["python3", CC, "--city", city, "--out", f"{WORK}/nope.blob"])
    expect(p.returncode != 0, "bad kind should fail the bake")
    expect("must be oneshot or longrun" in (p.out + p.err), f"reason\n{p.out}{p.err}")
    print("ok kind-required")


def test_kind_exit0():
    """D12: exit 0 no longer means do-not-restart on its own. A longrun that
    exits 0 is restarted within budget; a oneshot that exits 0 is done. Same
    binary, same exit code, opposite handling -- decided by the plan."""
    probe = f"{BIN}/unit-probe"
    long_city = f"{WORK}/longrun.city"
    open(long_city, "w").write(
        f"house quitter /bin/true kind=longrun budget=2 window=9 lids=none\n"
        f"house idle {probe} kind=oneshot lids=none\n"
    )
    blob = f"{WORK}/longrun.blob"
    b = run(["python3", CC, "--city", long_city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out = boot(plan=blob, hold=1200)
    expect(rc == 0, f"city should survive, rc={rc}\n{out}")
    expect("HALT" not in out, f"nothing may halt the city\n{out}")
    expect("restart quitter" in out, f"longrun exit 0 must restart\n{out}")

    one_city = f"{WORK}/oneshot.city"
    open(one_city, "w").write(
        f"house quitter /bin/true kind=oneshot budget=2 window=9 lids=none\n"
        f"house idle {probe} kind=oneshot lids=none\n"
    )
    blob2 = f"{WORK}/oneshot.blob"
    b = run(["python3", CC, "--city", one_city, "--out", blob2])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out2 = boot(plan=blob2, hold=1200)
    expect(rc == 0, f"city should survive, rc={rc}\n{out2}")
    expect("restart quitter" not in out2, f"oneshot exit 0 must not restart\n{out2}")
    print("ok kind-exit0")


def test_dawn_real_boot():
    """The real boot path, not the staged one: dawn mounts genuine ext4
    filesystems on loop devices, pivot_roots into them, mounts the kernel
    filesystems plus tmpfs and cgroup2, and execs nw-root -- which reads
    /efi/slots/current to learn which slot is live.

    This is the only test that exercises mount(2), pivot_root(2) or the
    /nw and /efi layout at all. Everything else in this suite runs against
    the flat staged directory under /tmp."""
    lab = f"{WORK}/dawnlab"
    subprocess.run(["rm", "-rf", lab], check=False)
    os.makedirs(f"{lab}/mr"); os.makedirs(f"{lab}/me")
    loops = []
    try:
        for name, mb in (("root", 48), ("esp", 16)):
            img = f"{lab}/{name}.img"
            subprocess.run(["dd", "if=/dev/zero", f"of={img}", "bs=1M",
                            f"count={mb}"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["mkfs.ext4", "-q", img], check=True)
            dev = subprocess.run(["losetup", "--find", "--show", img],
                                 capture_output=True, text=True,
                                 check=True).stdout.strip()
            loops.append(dev)
        rootdev, espdev = loops

        subprocess.run(["mount", rootdev, f"{lab}/mr"], check=True)
        subprocess.run(["mount", espdev, f"{lab}/me"], check=True)

        # The layout dawn expects, built the way a real image would be.
        for d in ("nw/bin", "nw/bricks", "nw/stores", "efi",
                  "proc", "sys/fs/cgroup", "dev", "run", "tmp"):
            os.makedirs(f"{lab}/mr/{d}", exist_ok=True)
        for b in ("nw-root", "nw-spawn", "nw-sup", "nw-rescue", "unit-probe"):
            subprocess.run(["cp", f"{BIN}/{b}", f"{lab}/mr/nw/bin/"], check=True)
        # A real root filesystem carries the loader and libc; without them
        # execve returns ENOENT and the failure looks like a missing binary.
        ldd = subprocess.run(["ldd", f"{BIN}/nw-root"],
                             capture_output=True, text=True).stdout
        for tok in ldd.split():
            if tok.startswith("/") and ".so" in tok:
                os.makedirs(f"{lab}/mr{os.path.dirname(tok)}", exist_ok=True)
                subprocess.run(["cp", "-L", tok, f"{lab}/mr{tok}"], check=True)

        os.makedirs(f"{lab}/me/slots/A"); os.makedirs(f"{lab}/me/slots/B")
        for slot, names in (("A", ["alpha", "beta"]), ("B", ["solo"])):
            city = f"{lab}/city{slot}"
            open(city, "w").write("".join(
                f"house {n} /nw/bin/unit-probe kind=oneshot lids=none\n"
                for n in names))
            p = run(["python3", CC, "--city", city,
                     "--out", f"{lab}/me/slots/{slot}/plan.blob"])
            expect(p.returncode == 0, f"bake {slot}\n{p.out}{p.err}")
        open(f"{lab}/me/slots/current", "w").write("A\n")
        subprocess.run(["sync"], check=True)
        subprocess.run(["umount", f"{lab}/mr"], check=True)
        subprocess.run(["umount", f"{lab}/me"], check=True)

        def boot_dawn():
            cmd = ["unshare", "--mount", "--pid", "--fork", "--",
                   "env", f"NW_ROOT={rootdev}", "NW_ROOT_FSTYPE=ext4",
                   f"NW_ESP={espdev}", "NW_ESP_FSTYPE=ext4",
                   f"{BIN}/nw-dawn"]
            p = run(cmd)
            return p.returncode, p.out + p.err

        rc, out = boot_dawn()
        expect(rc == 0, f"dawn boot rc={rc}\n{out}")
        expect("mounted /sysroot" in out, f"root not mounted\n{out}")
        expect("mounted /sysroot/efi" in out, f"esp not mounted\n{out}")
        expect("pivoted" in out, f"no pivot_root\n{out}")
        expect("mounted /sys/fs/cgroup" in out, f"cgroup2 not mounted\n{out}")
        expect("mounted /run" in out and "mounted /tmp" in out,
               f"tmpfs missing\n{out}")
        expect("live slot /efi/slots/A" in out, f"current not read\n{out}")
        expect("houses=2" in out, f"slot A should hold 2 units\n{out}")

        # slots/current is authoritative: flip it and a different plan boots.
        subprocess.run(["mount", espdev, f"{lab}/me"], check=True)
        open(f"{lab}/me/slots/current", "w").write("B\n")
        subprocess.run(["sync"], check=True)
        subprocess.run(["umount", f"{lab}/me"], check=True)
        rc, out = boot_dawn()
        expect(rc == 0, f"slot B rc={rc}\n{out}")
        expect("live slot /efi/slots/B" in out, f"B not selected\n{out}")
        expect("houses=1" in out, f"slot B should hold 1 unit\n{out}")

        # A name that would escape the slots directory must be refused.
        subprocess.run(["mount", espdev, f"{lab}/me"], check=True)
        open(f"{lab}/me/slots/current", "w").write("../../etc\n")
        subprocess.run(["sync"], check=True)
        subprocess.run(["umount", f"{lab}/me"], check=True)
        rc, out = boot_dawn()
        expect("HALT: slots/current" in out, f"traversal not refused\n{out}")
    finally:
        for d in (f"{lab}/mr", f"{lab}/me"):
            subprocess.run(["umount", d], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for dev in loops:
            subprocess.run(["losetup", "-d", dev], check=False)
    print("ok dawn-real-boot")


def test_term_signal():
    """D11: nw-spawn blocks all signals before forking and the mask survives
    fork+exec, so houses used to start fully masked and TERM handlers never
    ran. Assert the handler is observably reached -- checking only that the
    process is gone proves nothing, since SIGKILL would do that too."""
    term = f"{BIN}/unit-term"
    city = f"{WORK}/term.city"
    open(city, "w").write(f"house term {term} kind=oneshot lids=none\n")
    blob = f"{WORK}/term.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out = boot(plan=blob, hold=500)
    expect(rc == 0, f"term rc={rc}\n{out}")
    expect("sigterm_blocked=0" in out, f"house inherited a blocked mask\n{out}")
    expect("SIGTERM handler ran" in out, f"handler never ran\n{out}")
    expect("exiting cleanly after TERM" in out, f"no clean exit\n{out}")
    expect("houses_reaped=1" in out, f"house was killed, not reaped\n{out}")
    print("ok term-signal")


def test_crash_does_not_halt():
    """Nothing a house does halts the city. A house that crashes past its
    budget stays dead; the city carries on and shuts down normally."""
    boom = f"{BIN}/unit-boom"
    probe = f"{BIN}/unit-probe"
    city = f"{WORK}/crash.city"
    open(city, "w").write(
        f"house boom {boom} kind=longrun budget=2 window=9 lids=none\n"
        f"house idle {probe} kind=oneshot lids=none\n"
    )
    blob = f"{WORK}/crash.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out = boot(plan=blob, hold=1200)
    expect(rc == 0, f"city should survive a crashing house, rc={rc}\n{out}")
    expect("HALT" not in out, f"nothing may halt the city\n{out}")
    expect("restart boom" in out, f"boom should have been restarted\n{out}")
    print("ok crash-does-not-halt")


def test_seccomp_kills():
    bad = f"{BIN}/unit-badcall"
    city = f"{WORK}/sec.city"
    open(city, "w").write(f"house bad {bad} kind=oneshot lids=seccomp\n")
    blob = f"{WORK}/sec.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, b.err)
    rc, out = boot(plan=blob, hold=600)
    expect("badcall survived" not in out, f"seccomp leak\n{out}")
    print("ok seccomp-kill")


def test_hash_pin():
    h = open(f"{SLOTS}/A/plan.blob.sha256").read().strip()
    expect(len(h) == 64, "sha256 len")
    import hashlib
    got = hashlib.sha256(open(f"{SLOTS}/A/plan.blob", "rb").read()).hexdigest()
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
    test_crash_does_not_halt()
    test_term_signal()
    test_dawn_real_boot()
    test_kind_required()
    test_kind_exit0()
    test_seccomp_kills()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
