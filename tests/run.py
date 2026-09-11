#!/usr/bin/env python3
"""Put-together suite. Not in the TCB."""
from __future__ import annotations

import hashlib
import os
import re
import struct
import subprocess
import sys
import tempfile
import zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STAGE = os.environ.get("NW_STAGE", "/tmp/nw-init-run")

def _stage_limit():
    """How long NW_STAGE may be, derived rather than declared.

    make_brick builds "{STAGE}/nw/bricks/{64 hex}", and NW_BRICK_LEN is sized
    for the production path "/nw/bricks/" + 64 hex + NUL. Whatever is left is
    the slack a test stage may use. Past that, baking fails on the third test
    with `house locked: brick= too long`, which names brick= and says nothing
    about the stage -- so the reader looks at the plan. Stated as a
    precondition instead: found by the control agent, whose own documented
    recipe (mktemp -d) exceeded it."""
    for line in open(os.path.join(ROOT, "blob.h")):
        f = line.split()
        if len(f) >= 3 and f[0] == "#define" and f[1] == "NW_BRICK_LEN":
            return int(f[2]) - len("/nw/bricks/") - 64 - 1
    raise SystemExit("blob.h has no NW_BRICK_LEN")


if len(STAGE) > _stage_limit():
    raise SystemExit(
        f"NW_STAGE is {len(STAGE)} characters and the limit is "
        f"{_stage_limit()} (derived from NW_BRICK_LEN in blob.h): {STAGE}\n"
        f"A longer stage makes every brick path overflow brick[] and the "
        f"suite fails at bake time naming brick=, not the stage.")
# The staged tree mirrors the production layout and differs only in prefix:
# BIN is /nw/bin on a real machine, SLOTS is /efi/slots. Scratch files that
# have no production counterpart live in WORK.
BIN = f"{STAGE}/nw/bin"
SLOTS = f"{STAGE}/efi/slots"
WORK = f"{STAGE}/work"
os.makedirs(WORK, exist_ok=True)
CC = os.path.join(ROOT, "bakery", "nw-cc.py")


def blob_h(name):
    """Read a #define out of the STAGED blob.h. Limits are derived, never
    declared twice -- that includes here: a test that hardcodes
    NW_MAX_UNITS stops testing the maximum the day the maximum moves.

    From {STAGE}/src, not ROOT. The binaries under test were built from
    those bytes, so the number a test expects and the number the code was
    compiled with come from one file. Reading ROOT/blob.h was the staging
    trap for *values* -- the same one closed for nwcheck.c when the slot
    probe started compiling from the stage, still open one line above it.
    Found by fd-auditor. _stage_limit() below is the exception and must
    stay on ROOT: it runs at import to validate NW_STAGE itself, before
    there is a stage to read."""
    staged = os.path.join(STAGE, "src", "blob.h")
    if not os.path.exists(staged):
        raise SystemExit(
            f"{staged} is missing: the suite reads its limits from the "
            f"sources the staged binaries were built from. Run make stage.")
    for line in open(staged):
        f = line.split()
        if len(f) >= 3 and f[0] == "#define" and f[1] == name:
            return f[2]
    raise SystemExit(f"blob.h has no {name}")


# Flags for every throwaway that #includes a TCB source. -Werror because a
# warning in one of these is never acceptable noise: the slot probe kept
# calling name_dup(int *, ...) after the parameter became struct
# nw_dup_tab *, which built with a warning here and is a hard error on gcc
# 14 -- so the test that pins NW_DUP_SLOTS was one compiler upgrade from
# failing for a reason unrelated to the property it tests. Found by
# fd-auditor and tcb-review, independently.
PROBE_CFLAGS = ["-std=gnu11", "-Wall", "-Wextra", "-Werror"]

SKIPPED = []


def skip(name, why):
    """Record a test the environment cannot exercise. A skipped test is not a
    passing test: main() refuses to print a bare ALL TESTS PASSED when this
    list is non-empty. lid-landlock hid for its whole existence behind a
    green line produced by a kernel that could not run it."""
    SKIPPED.append((name, why))
    print(f"SKIP {name} -- {why}")


def fs_mountable(name):
    """Whether the kernel can mount this filesystem.

    Reads /proc/filesystems rather than checking for a mkfs tool. Those are
    different questions and getting them confused is how the FAT gap was
    mis-reported on 2026-09-10: mkfs.vfat is installed here and the kernel
    has no FAT driver at all, so `command -v mkfs.vfat` said the gap could be
    closed and mount(2) said otherwise."""
    for line in open("/proc/filesystems"):
        if line.split()[-1] == name:
            return True
    return False


_LANDLOCK = None


def landlock_abi():
    """The Landlock ABI version this kernel reports, or None.

    Detected by calling landlock_create_ruleset(NULL, 0, VERSION), which is
    exactly what nw-sup does, rather than by reading a config file or a
    securityfs path that may not be mounted."""
    global _LANDLOCK
    if _LANDLOCK is not None:
        return _LANDLOCK[0]
    src = f"{WORK}/llprobe.c"
    binp = f"{WORK}/llprobe"
    open(src, "w").write(
        "#define _GNU_SOURCE\n"
        "#include <linux/landlock.h>\n#include <stdio.h>\n"
        "#include <sys/syscall.h>\n#include <unistd.h>\n"
        "int main(void){long a=syscall(__NR_landlock_create_ruleset,(void*)0,0,"
        "LANDLOCK_CREATE_RULESET_VERSION);"
        "if(a<0)return 1;printf(\"%ld\\n\",a);return 0;}\n")
    c = run(["gcc", "-o", binp, src])
    if c.returncode != 0:
        _LANDLOCK = (None,)
        return None
    p = run([binp])
    _LANDLOCK = (int(p.out.strip()) if p.returncode == 0 else None,)
    return _LANDLOCK[0]


def print_environment():
    """Say what this machine can and cannot exercise, before any test runs.

    Added 2026-09-10 after a green suite was reported for a commit whose
    feature could not execute here at all. A suite result is evidence only
    against a stated environment."""
    print("== environment ==")
    abi = landlock_abi()
    print(f"  landlock   : {'ABI ' + str(abi) if abi else 'UNAVAILABLE'}"
          f"{'' if abi else '  -- lid-landlock tests will SKIP, not pass'}")
    mk = run(["sh", "-c", "command -v mkfs.vfat"]).returncode == 0
    mnt = fs_mountable("vfat")
    print(f"  vfat       : mkfs {'present' if mk else 'ABSENT'}, "
          f"kernel {'can' if mnt else 'CANNOT'} mount"
          f"{'' if mnt else '  -- the ESP is ext4 and FAT is unexercised'}")
    print("  mountable  : " + " ".join(
        f for f in ("ext4", "vfat", "squashfs", "erofs", "overlay")
        if fs_mountable(f)))
    loop = run(["sh", "-c", "command -v losetup"]).returncode == 0
    print(f"  losetup    : {'present' if loop else 'ABSENT'}")
    print()


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


def city_closed(rc, out):
    """Production PID 1 ends in reboot(RB_POWER_OFF), not _exit.

    Measured 2026-09-11 inside unshare --pid --fork: reboot() tears the
    pid namespace down and the unshare parent exits 130 (SIGINT). It
    does not return in the child. If reboot() is denied the code prints
    closed and _exit(0)s. Either is a finished shutdown. rc==0 alone
    used to mean 'PID 1 returned', which on hardware is
    Attempted to kill init."""
    if "Attempted to kill init" in out:
        return False
    if "[nw-root] closed" not in out:
        return False
    # Same SIGINT, two wait encodings. The shell reports 130
    # (WIFEXITED 128+2). Python subprocess reports -2
    # (WIFSIGNALED SIGINT) when unshare itself is the signaled
    # process. Measured both ways 2026-09-11.
    return rc in (0, 130, -2)


def expect(cond, msg):
    if not cond:
        raise SystemExit("FAIL: " + msg)


def test_happy():
    rc, out = boot(slot=f"{SLOTS}/A", hold=900)
    expect(city_closed(rc, out), f"happy rc={rc}\n{out}")
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
    expect(city_closed(a_rc, a_out), f"slot A rc={a_rc}\n{a_out}")
    expect("houses=4" in a_out, f"slot A should hold four units\n{a_out}")

    rc, out = boot(slot=f"{SLOTS}/B", hold=700)
    expect(city_closed(rc, out), f"slot B rc={rc}\n{out}")
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
    open(city, "w").write("house a /bin/true kind=oneshot window=1 lids=none\n")
    p = run(["python3", CC, "--city", city, "--out", f"{WORK}/nope.blob"])
    expect(p.returncode != 0, "window= must fail the bake")
    expect("window=" in (p.out + p.err), f"reason\n{p.out}{p.err}")
    print("ok baker-reject-dupname+window")


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
    """The C and Python implementations must agree, checked as a difference
    rather than as a corpus.

    This is where the CRC's correctness is discharged, and that matters more
    than it looks: proofs/caller_nw_check.c leaves nw_crc32_split
    unconstrained on purpose -- nw_check must be right for any checksum,
    which is the stronger claim -- so nothing in the proof says the checksum
    is the right one. This test is the other half, and proofs/README.md
    names it as such.

    It did not do that until 2026-09-11. It ran nw-check on one staged blob,
    expected 0, and compared the magic literal: a corpus check on whatever
    the suite happened to bake, cited as a difftest against zlib.crc32.
    Its docstring also promised a flipped-crc case it did not contain.
    Found by `claims`. Now it drives nw_crc32_split across every length and
    every split point and compares each answer to zlib -- which is the
    function the baker actually calls, so this is the real cross-language
    agreement, not a restatement of it."""
    # nw_crc32_split against zlib.crc32, both regions, every split point.
    # Compiled from the staged sources so it answers for the binary under
    # test -- same reason as c_name_slots.
    srcdir = os.path.join(STAGE, "src")
    expect(os.path.exists(os.path.join(srcdir, "nwcheck.c")),
           f"{srcdir}/nwcheck.c is missing -- run make stage")
    csrc = f"{WORK}/crcdiff.c"
    # TWO buffers, separately allocated and deliberately misaligned. One
    # contiguous buffer means `p = b` and `p = (const unsigned char *)a + na`
    # are indistinguishable: that mutant -- which ignores the second region
    # entirely -- agreed with zlib at every length and every cut point.
    # nw_check's real call passes a 20-byte STACK tmp_hdr and the blob body,
    # which are not adjacent, so the contiguous version was not testing the
    # shape the TCB actually uses. Found by `control`.
    open(csrc, "w").write(
        '#include "nwcheck.c"\n#include <stdio.h>\n#include <stdlib.h>\n'
        'static unsigned char ra[4200], rb[4200];\n'
        'int main(int argc, char **argv)\n{\n'
        '    int n = atoi(argv[1]);\n'
        '    unsigned char *A = ra + 1, *B = rb + 8;   /* different '
        'alignments, different objects */\n'
        '    for (int cut = 0; cut <= n; cut++) {\n'
        '        for (int i = 0; i < cut; i++)\n'
        '            A[i] = (unsigned char)(i * 37 + 11);\n'
        '        for (int i = 0; i < n - cut; i++)\n'
        '            B[i] = (unsigned char)((cut + i) * 37 + 11);\n'
        '        printf("%u\\n", nw_crc32_split(A, (uint32_t)cut,\n'
        '                                      B, (uint32_t)(n - cut)));\n'
        '    }\n'
        '    (void)argc;\n    return 0;\n}\n')
    cexe = f"{WORK}/crcdiff"
    c = run(["gcc"] + PROBE_CFLAGS + [f"-I{srcdir}", "-o", cexe, csrc])
    expect(c.returncode == 0, f"crc difftest build\n{c.out}{c.err}")
    for n in (0, 1, 2, 19, 255, 256, 257, 1024, 4095):
        buf = bytes(((i * 37 + 11) & 0xFF) for i in range(n))
        want = [zlib.crc32(buf) & 0xFFFFFFFF] * (n + 1)
        p = run([cexe, str(n)])
        expect(p.returncode == 0, f"crc difftest n={n}\n{p.out}{p.err}")
        got = [int(x) for x in p.out.split()]
        expect(got == want,
               f"nw_crc32_split disagrees with zlib at n={n}: first "
               f"mismatch at cut="
               f"{next(i for i, (a, b) in enumerate(zip(got, want)) if a != b)}"
               if got != want and len(got) == len(want) else
               f"nw_crc32_split produced {len(got)} answers for {n + 1} "
               f"cut points" if len(got) != len(want) else "")
    # The NULL second region, passed as a literal NULL. This said it was
    # testing NULL while passing `buf + 0`, so adding `if (!p) return 0;`
    # before the second loop left it green -- an assertion naming a case it
    # did not exercise. Found by `control`.
    nsrc = f"{WORK}/crcnull.c"
    open(nsrc, "w").write(
        '#include "nwcheck.c"\n#include <stdio.h>\n'
        'int main(void)\n{\n'
        '    static unsigned char b[64];\n'
        '    for (int i = 0; i < 64; i++) b[i] = (unsigned char)(i * 7 + 3);\n'
        '    printf("%u %u %u\\n",\n'
        '           nw_crc32_split(b, 64, (void *)0, 0),\n'
        '           nw_crc32_split((void *)0, 0, b, 64),\n'
        '           nw_crc32_split((void *)0, 0, (void *)0, 0));\n'
        '    return 0;\n}\n')
    nexe = f"{WORK}/crcnull"
    c = run(["gcc"] + PROBE_CFLAGS + [f"-I{srcdir}", "-o", nexe, nsrc])
    expect(c.returncode == 0, f"crc NULL probe build\n{c.out}{c.err}")
    p = run([nexe])
    expect(p.returncode == 0, f"crc NULL probe\n{p.out}{p.err}")
    body = bytes(((i * 7 + 3) & 0xFF) for i in range(64))
    want = zlib.crc32(body) & 0xFFFFFFFF
    expect([int(x) for x in p.out.split()] == [want, want, 0],
           f"a NULL region of length 0 must contribute nothing: got "
           f"{p.out.strip()!r}, expected {want} {want} 0")

    r = run([f"{BIN}/nw-check", f"{SLOTS}/A/plan.blob"])
    expect(r.returncode == 0, "difftest good")

    # The flipped crc this docstring used to promise.
    d = bytearray(open(f"{SLOTS}/A/plan.blob", "rb").read())
    d[16] ^= 0x01
    flipped = f"{WORK}/difftest-flipped.blob"
    open(flipped, "wb").write(bytes(d))
    r = run([f"{BIN}/nw-check", flipped])
    expect(r.returncode != 0 and "crc32" in (r.out + r.err),
           f"a flipped crc must be refused as crc32\n{r.out}{r.err}")

    want = blob_h("NW_MAGIC").strip('"')
    src = open(CC).read()
    lit = re.findall(r'b"(NWPLAN\d\d)"', src)
    expect(lit, "no magic literal found in the baker")
    expect(all(m == want for m in lit),
           f"blob.h NW_MAGIC is {want!r}, baker emits {set(lit)!r}")
    expect(open(f"{SLOTS}/A/plan.blob", "rb").read(8) == want.encode(),
           "the staged blob does not carry NW_MAGIC")
    print("ok difftest (crc vs zlib at every split point, magic, flip)")


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
        f"house quitter /bin/true kind=longrun budget=2 lids=none\n"
        f"house idle {probe} kind=oneshot lids=none\n"
    )
    blob = f"{WORK}/longrun.blob"
    b = run(["python3", CC, "--city", long_city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out = boot(plan=blob, hold=1200)
    expect(city_closed(rc, out), f"city should survive, rc={rc}\n{out}")
    expect("HALT" not in out, f"nothing may halt the city\n{out}")
    expect("restart quitter" in out, f"longrun exit 0 must restart\n{out}")

    one_city = f"{WORK}/oneshot.city"
    open(one_city, "w").write(
        f"house quitter /bin/true kind=oneshot budget=2 lids=none\n"
        f"house idle {probe} kind=oneshot lids=none\n"
    )
    blob2 = f"{WORK}/oneshot.blob"
    b = run(["python3", CC, "--city", one_city, "--out", blob2])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out2 = boot(plan=blob2, hold=1200)
    expect(city_closed(rc, out2), f"city should survive, rc={rc}\n{out2}")
    # "restart quitter" is also absent when quitter never ran, so assert it
    # ran and exited before asserting it was not restarted. Same shape as the
    # seccomp test: a negative assertion alone passes on absence.
    expect("house exit quitter" in out2, f"quitter never ran\n{out2}")
    expect("restart quitter" not in out2, f"oneshot exit 0 must not restart\n{out2}")
    print("ok kind-exit0")


def test_dawn_real_boot():
    """The real boot path, not the staged one: dawn mounts genuine ext4
    filesystems on loop devices, pivot_roots into them, mounts the kernel
    filesystems plus tmpfs and cgroup2, and execs nw-root -- which reads
    /efi/slots/current to learn which slot is live.

    This is the only test that exercises mount(2), pivot_root(2) or the
    /nw and /efi layout at all. Everything else in this suite runs against
    the flat staged directory under /tmp.

    It is not a bootloader boot. unshare --mount gives a private namespace
    and a root that is not rootfs; both are required for pivot_root and
    neither is true of a kernel-handoff initramfs. See harness.md
    "The harness is more capable than the machine". NW_HOLD_MS is the
    lab timer forwarded by dawn so this process can exit; production
    dawn does not pass --hold-ms."""
    lab = f"{WORK}/dawnlab"
    subprocess.run(["rm", "-rf", lab], check=False)
    os.makedirs(f"{lab}/mr"); os.makedirs(f"{lab}/me")

    # A real ESP is FAT32. Use one where the kernel can mount it, and say so
    # loudly where it cannot: docs/options/06 rules the ESP out for bricks on
    # FAT's absent execute bit, absent ownership and 4 GiB cap, and none of
    # that is exercised by an ext4 stand-in.
    if fs_mountable("vfat"):
        esp_fs, esp_mb, esp_mkfs = "vfat", 64, ["mkfs.vfat", "-F", "32"]
    else:
        esp_fs, esp_mb, esp_mkfs = "ext4", 16, ["mkfs.ext4", "-q"]
        skip("dawn-real-boot:vfat-esp",
             "kernel has no FAT driver (/proc/filesystems lists none), so the "
             "ESP is ext4 and FAT's execute bit, ownership and 4 GiB cap stay "
             "untested -- which is what docs/options/06 reasons from")

    loops = []
    try:
        for name, mb, mkfs in (("root", 48, ["mkfs.ext4", "-q"]),
                               ("esp", esp_mb, esp_mkfs)):
            img = f"{lab}/{name}.img"
            subprocess.run(["dd", "if=/dev/zero", f"of={img}", "bs=1M",
                            f"count={mb}"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(mkfs + [img], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
                   f"NW_ESP={espdev}", f"NW_ESP_FSTYPE={esp_fs}",
                   "NW_HOLD_MS=800",
                   f"{BIN}/nw-dawn"]
            p = run(cmd)
            return p.returncode, p.out + p.err

        rc, out = boot_dawn()
        expect(city_closed(rc, out), f"dawn boot rc={rc}\n{out}")
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
        expect(city_closed(rc, out), f"slot B rc={rc}\n{out}")
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
    print(f"ok dawn-real-boot (ESP {esp_fs})")


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
    expect(city_closed(rc, out), f"term rc={rc}\n{out}")
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
        f"house boom {boom} kind=longrun budget=2 lids=none\n"
        f"house idle {probe} kind=oneshot lids=none\n"
    )
    blob = f"{WORK}/crash.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out = boot(plan=blob, hold=1200)
    expect(city_closed(rc, out), f"city should survive a crashing house, rc={rc}\n{out}")
    expect("HALT" not in out, f"nothing may halt the city\n{out}")
    expect("restart boom" in out, f"boom should have been restarted\n{out}")
    print("ok crash-does-not-halt")


def test_budget_is_hard_total():
    """D18: budget is deaths for the life of nw-sup, not a sliding window.

    unit-slowdie exits after 1.2s. The old window_s=1 reset the tally
    between deaths, so budget=3 never fired. Control: restore a window
    reset and this test sees death=4 inside the hold. Pairing is the
    restart lines that did happen, then the one that must not."""
    drip = f"{BIN}/unit-slowdie"
    probe = f"{BIN}/unit-probe"
    city = f"{WORK}/d18.city"
    open(city, "w").write(
        f"house drip {drip} kind=longrun budget=3 lids=none\n"
        f"house idle {probe} kind=oneshot lids=none\n"
    )
    blob = f"{WORK}/d18.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    # 3 deaths * 1.2s plus spawn/shutdown slack. A sliding 1s window
    # would log death=4 before this deadline.
    rc, out = boot(plan=blob, hold=7000)
    expect(city_closed(rc, out), f"city should survive, rc={rc}\n{out}")
    expect("restart drip death=1" in out, f"first restart missing\n{out}")
    expect("restart drip death=2" in out, f"second restart missing\n{out}")
    expect("restart drip death=3" in out, f"third restart missing\n{out}")
    expect("restart drip death=4" not in out,
           f"budget did not bound: death=4\n{out}")
    n = out.count("restart drip death=")
    expect(n == 3, f"expected 3 restarts, got {n}\n{out}")
    print("ok budget-hard-total")


def test_shutdown_does_not_restart():
    """A supervisor that sees SIGTERM must not restart the house.

    unit-term exits 0 after handling TERM. As a longrun that would
    otherwise be a restart. The same signal PID 1 already sends is the
    news; no extra channel. Control: delete `if (stopping) _exit` in
    nw-sup and this test sees `restart stay`."""
    term = f"{BIN}/unit-term"
    city = f"{WORK}/shut.city"
    open(city, "w").write(f"house stay {term} kind=longrun budget=20 lids=none\n")
    blob = f"{WORK}/shut.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out = boot(plan=blob, hold=500)
    expect(city_closed(rc, out), f"shutdown rc={rc}\n{out}")
    expect("SIGTERM handler ran" in out, f"house never saw TERM\n{out}")
    expect("restart stay" not in out,
           f"supervisor restarted during shutdown\n{out}")
    print("ok shutdown-no-restart")


def test_seccomp_kills():
    bad = f"{BIN}/unit-badcall"
    city = f"{WORK}/sec.city"
    open(city, "w").write(f"house bad {bad} kind=oneshot lids=seccomp\n")
    blob = f"{WORK}/sec.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, b.err)
    rc, out = boot(plan=blob, hold=600)
    # "survived" absent is not enough on its own: it is also absent when the
    # house never ran, which is how a test passes for the wrong reason. Assert
    # the house started, then that the call did not survive -- together those
    # mean the filter did the killing.
    expect("badcall started" in out, f"house never ran\n{out}")
    expect("badcall survived" not in out, f"seccomp leak\n{out}")
    print("ok seccomp-kill")


def make_brick(ident, mirrors=()):
    """Build a content-addressed brick under {STAGE}/nw/bricks and return its
    path. The name is the sha256 of the tree's contents, so two bricks that
    differ only in the text of /id land at different paths on their own --
    nothing assigns them.

    `mirrors` are machine paths the brick must have mount points for. nw-sup
    will not mkdir into a brick, so the empty directories have to be baked in
    here, which is exactly the constraint a real baker works under."""
    import hashlib, shutil
    files = {"id": ident.encode() + b"\n"}
    tmp = tempfile.mkdtemp(dir=WORK)
    os.makedirs(f"{tmp}/bin")
    shutil.copy(f"{BIN}/unit-brick", f"{tmp}/bin/brick")
    os.chmod(f"{tmp}/bin/brick", 0o755)
    for rel, data in files.items():
        open(f"{tmp}/{rel}", "wb").write(data)
    for m in mirrors:
        os.makedirs(f"{tmp}{m}", exist_ok=True)

    h = hashlib.sha256()
    for base, dnames, fnames in os.walk(tmp):
        dnames.sort()
        rel = os.path.relpath(base, tmp)
        h.update(b"D" + rel.encode() + b"\0")
        for f in sorted(fnames):
            full = os.path.join(base, f)
            h.update(b"F" + os.path.relpath(full, tmp).encode() + b"\0")
            h.update(str(os.stat(full).st_mode).encode() + b"\0")
            h.update(open(full, "rb").read())
    brick = f"{STAGE}/nw/bricks/{h.hexdigest()}"
    subprocess.run(["rm", "-rf", brick], check=False)
    os.rename(tmp, brick)
    return brick


def test_brick_is_a_root():
    """Two houses, two bricks, one path. Each house pivot_roots into its own
    brick and reads /id; the contents differ, so neither is reading the
    other's and neither is reading the machine's -- the machine has no /id at
    all. The root listing is printed rather than probed one path at a time,
    so a failure shows which root the house actually landed in.

    A declared bind is checked in the same boot: the same path inside and
    out, and the mount point already present in the brick."""
    shared = f"{WORK}/shared"
    os.makedirs(shared, exist_ok=True)
    open(f"{shared}/token", "w").write("token-from-the-machine\n")

    # /proc is bound into house one only, so it can count its own
    # descriptors: nw-sup opens two directory fds to pivot and both must be
    # gone by execv. House two has no /proc and reports fds=noproc, which is
    # the honest answer rather than a zero nobody measured.
    one = make_brick("brick-one", mirrors=(shared, "/proc"))
    two = make_brick("brick-two")
    expect(one != two, "two different bricks must content-address differently")

    city = f"{WORK}/brick.city"
    open(city, "w").write(
        f"house one /bin/brick kind=oneshot lids=newns,seccomp "
        f"brick={one} bind={shared} bind=/proc\n"
        f"house two /bin/brick kind=oneshot lids=newns,seccomp brick={two}\n"
    )
    blob = f"{WORK}/brick.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    expect("binds=2" in b.out, f"bind table not emitted\n{b.out}")
    chk = run([f"{BIN}/nw-check", blob])
    expect(chk.returncode == 0, f"nw-check\n{chk.out}{chk.err}")

    rc, out = boot(plan=blob, hold=1200)
    expect(city_closed(rc, out), f"brick city rc={rc}\n{out}")
    expect("lid brick" in out, f"no pivot happened\n{out}")

    # Each house tags its own lines: the logger prefixes a write chunk, not
    # every line inside one, and the fixture flushes all four at once.
    def field(k):
        return dict(re.findall(r"(\w+) " + k + r"=(\S+)", out))
    ids, roots, binds = field("id"), field("root"), field("bind")
    fds = field("fds_ge3")
    expect(ids.get("one") == "brick-one", f"house one id\n{out}")
    expect(ids.get("two") == "brick-two", f"house two id\n{out}")
    expect(ids["one"] != ids["two"],
           f"same path, same contents: the pivot did nothing\n{out}")

    # Nothing of the machine's root is reachable except what the plan asked
    # for. These exist on the machine and in no brick, so seeing one that was
    # not declared means the house never left. House one declared /proc and a
    # bind under /tmp, so those two are its to have; house two declared
    # nothing and must see neither.
    machine_only = {"etc", "usr", "proc", "root", "var", "tmp"}
    declared = {"one": {"proc", "tmp"}, "two": set()}
    for h in ("one", "two"):
        entries = set(roots[h].split(","))
        expect("id" in entries and "bin" in entries,
               f"house {h} is not in a brick: root={roots[h]}")
        leaked = entries & (machine_only - declared[h])
        expect(not leaked,
               f"house {h} can still see the machine root: {sorted(leaked)}")

    # Invariant 2's live check inside a brick: the two directory descriptors
    # nw-sup opens to pivot are O_CLOEXEC and closed before execv.
    expect(fds.get("one") == "0",
           f"the pivot leaked a descriptor into the house\n{out}")

    expect(binds.get("one") == "token-from-the-machine",
           f"declared bind did not land\n{out}")
    expect(binds.get("two") == "none",
           f"house two was given a bind it never declared\n{out}")
    print("ok brick-is-a-root")


def test_path_traversal_refused():
    """A path in the plan is a string that nw-sup hands straight to mount(2)
    and open(2). Before this was checked, a brick of `<brick>/../..` baked
    clean, passed nw-check, and gave the house a root of /tmp/nw-init-run/nw
    -- every brick on the machine and the store -- while still logging
    `lid brick` and exiting 0. No error anywhere.

    Refused now at path_ok_len, the one site every path in a plan passes
    through, so exec_path, brick and bind are all covered by one check. The
    baker refuses too, independently: it is not in the TCB.

    This closes traversal and NOT symlinks -- see docs/options/07."""
    esc = f"{WORK}/esc.city"
    brick = f"{STAGE}/nw/bricks/deadbeef"

    for line, why in (
        (f"house one /bin/brick kind=oneshot lids=newns,seccomp "
         f"brick={brick}/../..\n", "brick"),
        (f"house one /bin/brick kind=oneshot lids=newns,seccomp "
         f"brick={brick} bind=/etc/../etc\n", "bind"),
        (f"house one /bin/../bin/brick kind=oneshot lids=newns,seccomp "
         f"brick={brick}\n", "exec_path"),
    ):
        open(esc, "w").write(line)
        p = run(["python3", CC, "--city", esc, "--out", f"{WORK}/nope.blob"])
        expect(p.returncode != 0, f"baker accepted .. in {why}\n{p.out}{p.err}")
        expect("no '..' component" in (p.out + p.err),
               f"{why} reason\n{p.out}{p.err}")

    # The checker must refuse it on its own, from a blob the baker would not
    # emit: bake a clean one, write ".." into the brick field by hand, repair
    # the CRC exactly as a hand-rolled baker would.
    good = f"{WORK}/esc-ok.blob"
    open(esc, "w").write(
        f"house one /bin/brick kind=oneshot lids=newns,seccomp "
        f"brick={brick}\n")
    p = run(["python3", CC, "--city", esc, "--out", good])
    expect(p.returncode == 0, f"bake\n{p.out}{p.err}")

    BRICK_OFF, BRICK_LEN = 20 + 32 + 128, 96   # hdr + name + exec_path
    d = bytearray(open(good, "rb").read())
    expect(bytes(d[BRICK_OFF:BRICK_OFF + len(brick)]) == brick.encode(),
           "brick is not where the layout says it is")
    evil = (brick + "/../..").encode()
    expect(len(evil) < BRICK_LEN, "crafted brick too long for the field")
    d[BRICK_OFF:BRICK_OFF + BRICK_LEN] = evil + b"\x00" * (BRICK_LEN - len(evil))
    d[16:20] = b"\x00\x00\x00\x00"
    d[16:20] = struct.pack("<I", zlib.crc32(bytes(d)) & 0xFFFFFFFF)
    bad = f"{WORK}/esc.blob"
    open(bad, "wb").write(bytes(d))

    r = run([f"{BIN}/nw-check", bad])
    expect(r.returncode != 0, "nw-check accepted a traversing brick")
    expect("brick path" in (r.out + r.err), f"reason\n{r.out}{r.err}")

    # And it must not merely fail later at mount: the city must not boot.
    rc, out = boot(plan=bad, hold=400)
    # The reason, not just the halt. halt_now() has many call sites in
    # pid1.c -- "logger fork", "plan size", "signalfd" -- so bare "HALT" is
    # satisfied by any boot failure, including one that never validated the
    # plan. Found by `control`.
    expect("nw-check reject: brick path" in out and "HALT: plan" in out,
           f"a traversing plan must be refused by name, not merely halt on"
           f"\n{out}")
    print("ok path-traversal-refused")


def test_brick_needs_newns():
    """A brick is a root, and pivoting into one without a private mount
    namespace would repoint the machine's. The baker refuses rather than
    adding the lid on the plan's behalf, and nw-check refuses independently
    -- checked here against a blob the baker would never emit."""
    city = f"{WORK}/brick-nons.city"
    open(city, "w").write(
        f"house solo /bin/brick kind=oneshot lids=seccomp brick=/nw/bricks/x\n")
    p = run(["python3", CC, "--city", city, "--out", f"{WORK}/nope.blob"])
    expect(p.returncode != 0, "brick without newns should fail the bake")
    expect("needs lids=...,newns" in (p.out + p.err), f"reason\n{p.out}{p.err}")

    # Same rule, enforced independently in the TCB: clear the NEWNS bit in a
    # sealed blob and repair the crc, exactly as a hand-rolled baker would.
    good = f"{WORK}/brick-ok.blob"
    open(city, "w").write(
        f"house solo /bin/brick kind=oneshot lids=newns,seccomp "
        f"brick=/nw/bricks/x\n")
    p = run(["python3", CC, "--city", city, "--out", good])
    expect(p.returncode == 0, f"bake\n{p.out}{p.err}")
    d = bytearray(open(good, "rb").read())
    lids_off = 20 + 32 + 128 + 96 + 2          # hdr + name + exec + brick + kind + budget; lids follows
    expect(d[lids_off] & 4, "expected the NEWNS bit where the layout says")
    d[lids_off] &= ~4
    d[16:20] = b"\x00\x00\x00\x00"
    crc = zlib.crc32(bytes(d)) & 0xFFFFFFFF
    d[16:20] = struct.pack("<I", crc)
    bad = f"{WORK}/brick-nons.blob"
    open(bad, "wb").write(bytes(d))
    r = run([f"{BIN}/nw-check", bad])
    expect(r.returncode != 0, "nw-check must reject a brick without NEWNS")
    expect("brick without NEWNS" in (r.out + r.err), f"reason\n{r.out}{r.err}")
    print("ok brick-needs-newns")


def test_lids_are_not_advisory():
    """A declared lid that cannot be applied must stop that house starting.

    lid_landlock used to say-and-continue when Landlock was absent, so the
    house ran with no file restriction while the plan said it was confined.
    Invariant 6 says a lid decides what a house can do; a lid that decides
    nothing while claiming to is the same defect as a brick that roots on the
    machine while logging `lid brick`.

    Asserts the rule, not the environment: either the lid goes on and the
    house runs, or it does not and the house does not, and never a third
    outcome. Both branches are real, but only one runs on any given kernel --
    the environment banner says which, and test_landlock_confines is the one
    that actually exercises the lid."""
    brick = make_brick("advisory-brick")
    city = f"{WORK}/lid-advisory.city"
    open(city, "w").write(
        f"house locked /bin/brick kind=oneshot lids=newns,landlock "
        f"brick={brick}\n"
        f"house plain {BIN}/unit-probe kind=oneshot budget=0 lids=none\n"
    )
    blob = f"{WORK}/lid-advisory.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out = boot(plan=blob, hold=1500)

    # The say-and-continue form was "[nw-sup] landlock ..."; the fatal form is
    # "[nw-sup] FAIL landlock ...". If a soft line comes back, so does the bug.
    expect("[nw-sup] landlock" not in out,
           f"a declared lid was skipped with a log line\n{out}")

    applied = "lid landlock" in out
    refused = "FAIL landlock" in out
    expect(applied != refused,
           f"exactly one of applied/refused must happen\n{out}")
    if refused:
        expect("locked id=" not in out,
               f"lid could not be applied and the house ran anyway\n{out}")
        expect(landlock_abi() is None,
               "the lid was refused on a kernel that has Landlock")
    else:
        expect("locked id=" in out,
               f"lid was applied but the house did not start\n{out}")

    expect("house=plain" in out, f"an unrelated house must still run\n{out}")
    expect(city_closed(rc, out) and "HALT" not in out,
           f"a house that cannot wear its lid must not halt the city\n{out}")
    print("ok lids-not-advisory " +
          ("(refused branch: no landlock here)" if refused
           else "(applied branch)"))


def test_landlock_confines():
    """The lid must let a house start AND demonstrably restrict it.

    This is the test that did not exist, and its absence is why the lid could
    grant too little to execute anything for its whole life. It asserts three
    things in one boot:

      the house started            -- it can read and execute its own brick
      it can write a declared bind -- the plan said that path was its to write
      it CANNOT write its brick    -- nothing granted write beneath the root

    The third is the confinement. Without it this is a test that the house
    started, which proves nothing about what it can touch.

    Negative control: change the "/" grant in lid_landlock from `ro` to `rw`
    and wr_root becomes ok, failing this test."""
    abi = landlock_abi()
    if abi is None:
        skip("landlock-confines",
             "kernel has no Landlock (landlock_create_ruleset -> ENOSYS); "
             "the lid cannot be exercised here at all")
        return

    shared = f"{WORK}/ll-shared"
    os.makedirs(shared, exist_ok=True)
    open(f"{shared}/token", "w").write("token-from-the-machine\n")
    brick = make_brick("landlock-brick", mirrors=(shared,))

    city = f"{WORK}/ll.city"
    open(city, "w").write(
        f"house sealed /bin/brick kind=oneshot lids=newns,landlock "
        f"brick={brick} bind={shared}\n")
    blob = f"{WORK}/ll.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")

    rc, out = boot(plan=blob, hold=1500)
    expect(city_closed(rc, out), f"landlock city rc={rc}\n{out}")
    expect("lid landlock" in out, f"lid was not applied\n{out}")

    def field(k):
        return dict(re.findall(r"(\w+) " + k + r"=(\S+)", out))

    # Started at all: a dynamically linked house under the old ruleset died
    # here with EACCES from execv, because the loader was unreadable.
    expect(field("id").get("sealed") == "landlock-brick",
           f"house did not start under the lid\n{out}")
    # Allowed read: the bind the plan declared.
    expect(field("bind").get("sealed") == "token-from-the-machine",
           f"declared bind unreadable under the lid\n{out}")
    # Allowed write: same bind.
    expect(field("wr_bind").get("sealed") == "ok",
           f"declared bind not writable under the lid\n{out}")
    # DENIED write: the brick itself. This is the confinement.
    wr = field("wr_root").get("sealed", "")
    expect(wr.startswith("denied"),
           f"the house wrote into its own sealed brick: wr_root={wr}\n{out}")
    print(f"ok landlock-confines (ABI {abi})")


def c_name_slots(names):
    """Ask nwcheck.c itself which table slot each name occupies.

    Not "which slot does the hash give" -- which slot does `name_dup`
    actually record the unit in. A throwaway includes the translation unit
    (hash_name and name_dup are both static), builds a one-unit array per
    name, runs name_dup against a fresh table, and reports the index that
    stopped being -1. Two names collide exactly when that index is the same,
    which is the property the collision test needs, observed through the code
    under test.

    The first version asked `hash_name(name) & (NW_DUP_SLOTS - 1)` instead.
    That looks equivalent and is not: the mask is a *second copy* of an
    expression that also lives in name_dup, so changing name_dup's derivation
    to `(hv >> 16) & (NW_DUP_SLOTS - 1)` left this helper answering for the
    old one, the planted pair no longer collided, the collision case quietly
    became a second plain duplicate, and the suite still printed `a real
    collision on slot 80`. It survived the truncate-the-probe-chain control
    too, so the case that exists to be controlled stopped being controlled.
    Found by `control`. Invariant 3's failure mode, one layer up, inside the
    test written to catch it.

    Compiled from {STAGE}/src, NOT the source tree -- see the Makefile's
    stage rule. Taking an *algorithm* rather than a binary from ROOT is the
    staging trap one level in: against a stale stage the probe answers for
    code the binary under test does not contain. Found by fd-auditor."""
    slots = int(blob_h("NW_DUP_SLOTS"))
    srcdir = os.path.join(STAGE, "src")
    expect(os.path.exists(os.path.join(srcdir, "nwcheck.c")),
           f"{srcdir}/nwcheck.c is missing: this stage predates the rule that "
           f"stages the sources beside the binaries. Run make stage.")
    src = f"{WORK}/slotprobe.c"
    with open(src, "w") as f:
        f.write('#include "nwcheck.c"\n'
                '#include <stdio.h>\n'
                '#include <string.h>\n'
                'static int where(const char *nm)\n'
                '{\n'
                '    static struct nw_unit u[1];\n'
                '    struct nw_dup_tab t;\n'
                '    memset(u, 0, sizeof u);\n'
                '    strncpy(u[0].name, nm, NW_NAME_LEN - 1);\n'
                '    name_dup_init(&t);\n'
                '    if (name_dup(&t, u, 0)) return -2;\n'
                '    int seen = -1;\n'
                '    for (int i = 0; i < NW_DUP_SLOTS; i++)\n'
                '        if (t.slot[i] >= 0) { if (seen >= 0) return -3; seen = i; }\n'
                '    return seen;\n'
                '}\n'
                'int main(void){\n')
        for nm in names:
            expect(all(c.isalnum() or c in "_-" for c in nm) and nm,
                   f"probe name {nm!r} is not a legal unit name")
            f.write(f'  printf("%d\\n", where("{nm}"));\n')
        f.write("  return 0;\n}\n")
    exe = f"{WORK}/slotprobe"
    c = run(["gcc"] + PROBE_CFLAGS + [f"-I{srcdir}", "-o", exe, src])
    expect(c.returncode == 0, f"slot probe build\n{c.out}{c.err}")
    p = run([exe])
    expect(p.returncode == 0, f"slot probe\n{p.out}{p.err}")
    got = [int(x) for x in p.out.split()]
    expect(len(got) == len(names), f"slot probe output\n{p.out}")
    # -2 means name_dup called a single name in an empty table a duplicate;
    # -3 means it wrote more than one entry. Either would make every
    # collision below meaningless, so they fail here rather than downstream.
    expect(all(0 <= s < slots for s in got),
           f"name_dup did not record exactly one slot per name: "
           f"{[(n, s) for n, s in zip(names, got) if not 0 <= s < slots][:4]}")
    return got


def test_dupname_refused():
    """Two houses under one name, refused by the TCB rather than the baker.

    Until 2026-09-11 NW_E_DUPNAME had never been produced by nw-check in this
    suite's whole history: the only duplicate-name test bakes, and the baker
    rejects with a set comparison before the blob exists. So the checker's
    open-addressed table -- the part that has to be right when a blob arrives
    from somewhere other than the baker -- ran green every day without ever
    returning its own error code.

    Two crafted blobs, and the second is the one that pays. A plain duplicate
    is found in the slot it hashes to and says nothing about probing: it
    passes against a table truncated to one probe, which is how the first
    version of this test was wrong. The collision case plants two names that
    nwcheck.c's own hash puts in the same slot, so the second is displaced by
    one, and duplicates *that* -- detection then requires stepping past an
    occupied, non-matching slot. Truncating the chain fails it.

    Nothing here can reach a full table: blob.h asserts NW_MAX_UNITS <
    NW_DUP_SLOTS at compile time, which is the only place that case is
    checkable, since a blob big enough to fill the table is rejected for its
    unit count first."""
    n = int(blob_h("NW_MAX_UNITS"))
    expect(n >= 4, f"this test needs at least 4 units, blob.h says {n}")
    NAME, PATH, BRICK = (int(blob_h(x)) for x in
                         ("NW_NAME_LEN", "NW_PATH_LEN", "NW_BRICK_LEN"))
    HDR = 20
    USZ = NAME + PATH + BRICK + 4

    city = f"{WORK}/dup.city"
    open(city, "w").write("".join(
        f"house u{i:02d} /bin/true kind=oneshot lids=none\n" for i in range(n)))
    good = f"{WORK}/dup-ok.blob"
    b = run(["python3", CC, "--city", city, "--out", good])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    base = bytearray(open(good, "rb").read())
    expect(len(base) == HDR + n * USZ,
           f"layout: {len(base)} bytes for {n} units of {USZ}")

    def put(d, idx, name):
        off = HDR + idx * USZ
        d[off:off + NAME] = name.encode().ljust(NAME, b"\0")

    def seal(d, why):
        d[16:20] = b"\x00\x00\x00\x00"
        d[16:20] = struct.pack("<I", zlib.crc32(bytes(d)) & 0xFFFFFFFF)
        path = f"{WORK}/dup-{why}.blob"
        open(path, "wb").write(bytes(d))
        return path

    # Find a colliding pair by asking the C hash directly, in one batch.
    cand = [f"c{i:05d}" for i in range(256)]
    slots = c_name_slots(cand)
    # A cluster of THREE on one slot, not two. A 2-long cluster pins probe
    # depth 2 and nothing further: `for (p = 0; p < 2; p++)` left the whole
    # suite green while a name displaced by two was accepted as a
    # duplicate-free plan. With NW_MAX_UNITS names in NW_DUP_SLOTS slots a
    # 3-deep cluster is ordinary, not contrived -- `control` found one
    # among the first 256 candidates. The duplicate is planted on the LAST
    # of the three, so detecting it has to walk past both others.
    from collections import defaultdict
    by_slot = defaultdict(list)
    for nm, sl in zip(cand, slots):
        by_slot[sl].append(nm)
    trio = next((v for v in by_slot.values() if len(v) >= 3), None)
    expect(trio is not None,
           f"no three of {len(cand)} names share a slot -- widen the "
           f"candidate set; the probe-depth case needs a 3-deep cluster")
    first, pair = {}, (trio[0], trio[2], slots[cand.index(trio[0])])
    _unused = first
    a, bb, slot = pair

    # The pairing for the probe, and it is the assertion this test was
    # missing. Everything below asserts that a duplicate is refused, which
    # a degenerate probe also satisfies: make c_name_slots return a
    # constant and the pair becomes two adjacent names that do not collide,
    # the collision case silently degrades into a second plain duplicate,
    # and the ok line still says "a real collision on slot 0". tcb-review
    # ran exactly that control and the test passed. So assert the property
    # the case depends on, here, where it is cheap.
    expect(len(set(slots)) > 1,
           f"the slot probe answered the same slot for all {len(cand)} "
           f"names -- it is not reading name_dup's table, and the "
           f"collision case below is not a collision")
    expect(a != bb, "the colliding pair must be two different names")
    expect(slots.count(slot) >= 3,
           f"{a} and {bb} are supposed to share slot {slot} with a third "
           f"name between them, but only {slots.count(slot)} land there")

    cases = []
    d = bytearray(base)
    put(d, 1, f"u{0:02d}")
    cases.append((seal(d, "plain"), "plain", "units 0 and 1 share a name"))

    # a and bb hash to the same slot. Inserted first, a takes it and bb is
    # displaced to the next one; the copy of bb at the end must probe past a.
    d = bytearray(base)
    for j, nm in enumerate(trio):
        put(d, j, nm)
    put(d, n - 1, trio[2])
    cases.append((seal(d, "collision"), "collision",
                  f"{trio} all hash to slot {slot}; the duplicate of "
                  f"{trio[2]} must probe past the other two"))

    for path, why, what in cases:
        r = run([f"{BIN}/nw-check", path])
        expect(r.returncode != 0,
               f"nw-check accepted a duplicate name ({what})\n{r.out}{r.err}")
        expect("duplicate name" in (r.out + r.err),
               f"{why}: wrong reason ({what})\n{r.out}{r.err}")

        # Not merely a diagnostic: two houses under one name are
        # indistinguishable in the log and to nw-sup, which takes the name as
        # argv. The city must not open.
        rc, out = boot(plan=path, hold=400)
        expect("nw-check reject: duplicate name" in out
               and "HALT: plan" in out,
               f"a duplicate plan must be refused by name, not merely halt "
               f"on ({what})\n{out[-1500:]}")

    # The pairing. Every assertion above is a rejection, and a checker that
    # answers NW_E_DUPNAME to everything satisfies all of them: emptying the
    # name comparison so `same` stays 1 leaves this test green without it.
    # So the same two colliding names, distinct, must be accepted -- which is
    # also the only assertion here that says the table tolerates a collision
    # rather than merely detecting through one.
    d = bytearray(base)
    for j, nm in enumerate(trio):
        put(d, j, nm)
    okpath = seal(d, "collide-distinct")
    r = run([f"{BIN}/nw-check", okpath])
    expect(r.returncode == 0,
           f"nw-check rejected {trio}, which collide on slot {slot} "
           f"but are different names\n{r.out}{r.err}")

    # And the comparison must read the whole field, not a prefix. Every
    # name above is short, so `for (n = 0; n < 8; n++)` in name_dup left
    # the suite green while the TCB refused a legal city -- two distinct
    # names that share their first eight bytes were called duplicates.
    # Bug 12's class: a scan shorter than the field it validates.
    #
    # The pair must also COLLIDE, or name_dup never compares them at all --
    # it only compares against an occupied slot. A non-colliding pair made
    # this assertion pass against the truncated comparison, which is the
    # same "the code under test never ran" shape one more time.
    pre = "application-"
    stem = pre + "0" * (NAME - 1 - len(pre) - 4)
    longs = [stem + f"{i:04d}" for i in range(512)]
    lslots = c_name_slots(longs)
    lby = defaultdict(list)
    for nm, sl in zip(longs, lslots):
        lby[sl].append(nm)
    def firstdiff(x, y):
        return next((i for i in range(len(x)) if x[i] != y[i]), len(x))

    # Among the colliding pairs, the one that differs LATEST: the test can
    # only catch a comparison truncated at or before that index, so pushing
    # it as far right as the candidate set allows is free strength. The
    # residual is honest -- a comparison truncated between that index and
    # NW_NAME_LEN still passes, and the proof is what covers the rest.
    lcands = [(firstdiff(v[i], v[j]), v[i], v[j])
              for v in lby.values() if len(v) >= 2
              for i in range(len(v)) for j in range(i + 1, len(v))]
    expect(lcands,
           f"no two of {len(longs)} full-width names share a slot -- widen "
           f"the candidate set; the comparison-length case needs a "
           f"colliding pair that is long and shares a prefix")
    fd, long_a, long_b = max(lcands)
    expect(len(long_a) == NAME - 1 and long_a[:8] == long_b[:8]
           and long_a != long_b,
           f"the long-name pair must fill the field, share a prefix and "
           f"differ: {long_a!r} {long_b!r}")
    d = bytearray(base)
    put(d, 0, long_a)
    put(d, 1, long_b)
    longpath = seal(d, "long-prefix")
    r = run([f"{BIN}/nw-check", longpath])
    expect(r.returncode == 0,
           f"nw-check called {long_a} and {long_b} duplicates: they "
           f"collide, and first differ at byte {fd} of {NAME}, so the "
           f"comparison is not reading that far\n{r.out}{r.err}")

    print(f"ok dupname-refused (plain, a 3-deep cluster on slot {slot}, "
          f"the distinct trio accepted, and a colliding pair differing at "
          f"byte {fd})")


def test_checker_rejects_crafted_fields():
    """The three field checks in nw_check no test had ever reached.

    Coverage named them: NW_E_KIND, NW_E_LLBRICK and NW_E_LIDS were never
    executed by the suite, because the baker refuses all three at bake time
    and every blob the suite had came from the baker. That is the arrangement
    plan.md forbids -- "any rule the runtime relies on must be in nwcheck.c
    too... a blob can arrive from anywhere" -- and it had been true here the
    whole time for these three, which is a check nobody has ever seen work.

    Each case writes one byte into a sealed blob and repairs the CRC. The
    reason string is asserted, not just the exit code: a checker that
    rejected for a different reason would satisfy `returncode != 0`."""
    NAME, PATH, BRICK = (int(blob_h(x)) for x in
                         ("NW_NAME_LEN", "NW_PATH_LEN", "NW_BRICK_LEN"))
    HDR = 20
    KIND_OFF = HDR + NAME + PATH + BRICK      # kind, then budget, lids, _pad
    LIDS_OFF = KIND_OFF + 2                   # lids is the third byte of the trailer

    city = f"{WORK}/crafted.city"
    brick = f"{STAGE}/nw/bricks/deadbeef"
    good = f"{WORK}/crafted-ok.blob"
    # THREE units, and every case below is crafted on the LAST one. A
    # one-unit blob pins each check for u[0] only: changing `u[i].kind` to
    # `u[0].kind` and `u[i].lids` to `u[0].lids` left the whole suite green
    # -- and the caller proof too, which runs at one unit -- while the same
    # byte on any later unit validated. `control` measured it: unit 0
    # kind=255 rejected, unit 1 kind=255 `OK units=2`. A loop whose body is
    # only ever exercised at index 0 is not a loop as far as the test is
    # concerned.
    NUNITS = 3
    VICTIM = NUNITS - 1
    open(city, "w").write("".join(
        f"house c{i:02d} /bin/true kind=oneshot lids=newns,seccomp "
        f"brick={brick}\n" for i in range(NUNITS)))
    p = run(["python3", CC, "--city", city, "--out", good])
    expect(p.returncode == 0, f"bake\n{p.out}{p.err}")
    base = bytearray(open(good, "rb").read())
    USZ = NAME + PATH + BRICK + 4
    KIND_OFF += VICTIM * USZ
    LIDS_OFF += VICTIM * USZ
    expect(len(base) == HDR + NUNITS * USZ,
           f"layout: {len(base)} bytes for {NUNITS} units of {USZ}")
    expect(base[KIND_OFF] == 0 and base[LIDS_OFF] == (1 | 4),
           f"kind/lids are not where the layout says for unit {VICTIM}: "
           f"{base[KIND_OFF]} {base[LIDS_OFF]}")

    def craft(why, edits):
        d = bytearray(base)
        if why.startswith("dirtyblank"):
            # Clear EVERY unit's brick and the lid that requires one, so the
            # only thing wrong with this blob is the victim's dirty padding.
            # Clearing only the victim's left `u[0].brick[k]` in place of
            # `u[i].brick[k]` passing: unit 0 still had a brick, so the
            # wrong index rejected anyway and the test could not tell the
            # difference. A rejection for the right reason by accident is
            # the thing a control is for.
            for k in range(NUNITS):
                bo = HDR + k * USZ + NAME + PATH
                d[bo:bo + BRICK] = b"\x00" * BRICK
                d[HDR + k * USZ + NAME + PATH + BRICK + 2] = 1
        for off, val in edits:
            d[off] = val
        d[16:20] = b"\x00\x00\x00\x00"
        d[16:20] = struct.pack("<I", zlib.crc32(bytes(d)) & 0xFFFFFFFF)
        path = f"{WORK}/crafted-{why}.blob"
        open(path, "wb").write(bytes(d))
        return path

    # Every value outside each closed set, not one representative. A single
    # crafted kind of 2 is satisfied by `if (kind == 2) return NW_E_KIND;`,
    # and a single lid bit of 0x10 by `if (lids & 0x10)`: both leave the
    # other values accepted and the whole suite green. `control` produced
    # exactly those two mutants. A loop costs nothing here -- the crafted
    # blob is rebuilt either way -- and it is the difference between pinning
    # a closed set and pinning one member of it.
    LEGAL_LIDS = 1 | 2 | 4 | 8          # seccomp landlock newns newnet
    BRICK_OFF = HDR + VICTIM * USZ + NAME + PATH
    cases = [
        # A blank brick must be zero to the field width. Deleting that check
        # left the suite, the coverage floor AND the caller proof green --
        # gcov marks `if (...) return NW_E_BRICK;` covered on every unit
        # with a blank brick without the return ever being taken, and the
        # proof only ever asserted brick[0]. An unvalidated field cannot be
        # given meaning later: an old blob carrying garbage would be
        # accepted by a new checker that reads it. Bug 1's shape, found by
        # tcb-review, which also noted the incentive to delete it -- that
        # 96-iteration loop is most of the caller proof's runtime.
        ("dirtyblank", [(BRICK_OFF + 1, ord("x"))], "brick path",
         "a blank brick with a nonzero byte after it"),
        ("dirtyblank-last", [(BRICK_OFF + BRICK - 1, 1)], "brick path",
         "a blank brick with a nonzero byte in its last position"),
    ] + [
        (f"kind{k}", [(KIND_OFF, k)], "kind",
         f"kind={k}, outside the two the runtime knows")
        for k in (2, 3, 127, 255)
    ] + [
        (f"lid{bit:#04x}", [(LIDS_OFF, 1 | 4 | bit)], "lids",
         f"lid bit {bit:#04x}, outside the closed set")
        for bit in (0x10, 0x20, 0x40, 0x80)
    ] + [
        # Landlock grants read and execute beneath the house's root, which
        # restricts nothing when the root is the machine's. Clear the brick
        # and ask for the lid: NW_E_LLBRICK, not a house confined to /.
        ("llbrick",
         [(BRICK_OFF + k, 0) for k in range(BRICK)]
         + [(LIDS_OFF, 1 | 2)], "landlock without brick",
         "landlock on a house with no brick"),
    ]
    for why, edits, reason, what in cases:
        path = craft(why, edits)
        r = run([f"{BIN}/nw-check", path])
        expect(r.returncode != 0,
               f"nw-check accepted {what}\n{r.out}{r.err}")
        expect(reason in (r.out + r.err),
               f"{why}: wrong reason for {what}\n{r.out}{r.err}")

    # The pairing, from both sides of each closed set. The unmodified blob
    # must be accepted -- otherwise every rejection above is satisfied by a
    # checker that rejects everything, including one that never reaches
    # these checks at all. And every *legal* value must be accepted too, or
    # the rejections are satisfied by a checker whose set is narrower than
    # the plan language: dropping NW_LID_NEWNET from the allow-mask left the
    # entire suite green, because no test in it had ever declared newnet,
    # and the TCB could have rejected every lids=newnet plan unnoticed.
    # `control` found that; this is the half that was missing.
    ok_cases = [("unmodified", [])]
    ok_cases += [(f"kind={k}", [(KIND_OFF, k)]) for k in (0, 1)]
    ok_cases += [(f"lids={bit:#04x}", [(LIDS_OFF, bit | 4)])
                 for bit in (1, 2, 4, 8)]
    ok_cases += [("lids=all-legal", [(LIDS_OFF, LEGAL_LIDS)])]
    for why, edits in ok_cases:
        path = craft("ok-" + why.replace("=", ""), edits) if edits else good
        r = run([f"{BIN}/nw-check", path])
        expect(r.returncode == 0,
               f"nw-check rejected a legal plan ({why}): the closed set in "
               f"the TCB is narrower than the one the baker emits"
               f"\n{r.out}{r.err}")
    print(f"ok checker-rejects-crafted (every illegal kind and lid bit "
          f"refused on unit {VICTIM} of {NUNITS}, every legal one accepted)")


def test_blob_size_ceiling():
    """NW_BLOB_MAX must admit every legal blob and refuse everything larger,
    and nothing in the suite checked either half.

    The largest plan anything here booted was 64 units with no binds -- half
    the ceiling -- so when the ceiling arrived, both of its claims were
    asserted by no test that runs. It shipped with a `(uint32_t)st.st_size`
    comparison that truncates: a 4 GiB file compared small, was accepted,
    and aborted PID 1 inside read(). On a real boot that is `Attempted to
    kill init`, in place of the orderly `HALT: plan size` the code it
    replaced produced. tcb-review found it; this is the test that would
    have.

    Three cases, and the sizes are derived from blob.h so the test cannot
    drift away from the limit it is about:

    * the largest legal blob is accepted and boots;
    * one byte more is refused for its size, by every reader;
    * a file 4 GiB + 1 byte long -- the truncation window -- is refused,
      not aborted on.
    """
    NAME, PATH, BRICK = (int(blob_h(x)) for x in
                         ("NW_NAME_LEN", "NW_PATH_LEN", "NW_BRICK_LEN"))
    nu, nb = int(blob_h("NW_MAX_UNITS")), int(blob_h("NW_MAX_BINDS"))
    HDR, USZ, BSZ = 20, NAME + PATH + BRICK + 4, 2 + PATH
    biggest = HDR + nu * USZ + nb * BSZ

    # A maximal plan: every unit has a brick (a bind requires one) and the
    # bind table is full. The baker refuses a bind whose unit has no brick,
    # so this is the shape, not a crafted blob.
    brick = f"{STAGE}/nw/bricks/deadbeef"
    city = f"{WORK}/maxblob.city"
    with open(city, "w") as f:
        for i in range(nu):
            binds = "".join(f" bind=/etc/hosts{'' if j == 0 else ''}"
                            for j in range(nb // nu + (1 if i < nb % nu else 0)))
            f.write(f"house m{i:02d} /bin/true kind=oneshot "
                    f"lids=newns brick={brick}{binds}\n")
    good = f"{WORK}/maxblob.blob"
    b = run(["python3", CC, "--city", city, "--out", good])
    expect(b.returncode == 0, f"bake a maximal plan\n{b.out}{b.err}")
    got = os.path.getsize(good)
    expect(got == biggest,
           f"the maximal plan is {got} bytes, blob.h says the largest legal "
           f"blob is {biggest} -- this test is not testing the ceiling")

    r = run([f"{BIN}/nw-check", good])
    expect(r.returncode == 0,
           f"nw-check refused the largest legal blob\n{r.out}{r.err}")

    # One byte over. Refused for its size by nw-check and by PID 1, and --
    # the half that regressed -- nw-spawn's recheck must not accept the
    # truncated prefix either.
    over = f"{WORK}/maxblob-plus.blob"
    open(over, "wb").write(open(good, "rb").read() + b"\\x00")
    r = run([f"{BIN}/nw-check", over])
    expect(r.returncode != 0 and "blob size" in (r.out + r.err),
           f"nw-check accepted a blob one byte over the ceiling"
           f"\n{r.out}{r.err}")
    p = run([f"{BIN}/nw-spawn", over, "9", "1", "9"],
            env={**os.environ, "NW_SUP": "/bin/true"})
    expect(p.returncode != 0 and "blob size" in (p.out + p.err),
           f"nw-spawn did not refuse a maximal blob with bytes appended for "
           f"its SIZE -- a truncated read that lands exactly on a legal "
           f"length passes the recheck, and any other rejection reason here "
           f"means it got past the size guard"
           f"\n{p.out}{p.err}")

    # The truncation window: 2^32 compares as 0 through a uint32_t cast.
    huge = f"{WORK}/huge.blob"
    with open(huge, "wb") as f:
        f.truncate(1 << 32)
    r = run([f"{BIN}/nw-check", huge])
    expect(r.returncode == 1 and "blob size" in (r.out + r.err),
           f"a 4 GiB file must be refused for its size, not crash: "
           f"exit {r.returncode}\n{r.out}{r.err}")
    rc, out = boot(plan=huge, hold=400)
    expect("HALT: plan size" in out,
           f"PID 1 must halt on plan size, not die: rc={rc}\n{out[-800:]}")
    os.unlink(huge)

    print(f"ok blob-size-ceiling ({biggest} bytes: accepted, +1 refused, "
          f"4 GiB refused)")


def test_non_provision_at_max():
    """Non-provision, asserted at NW_MAX_UNITS rather than at four.

    unit-probe scanned fd 3..63 and unit i's log pipe lands on fd 5 + 2i, so
    it went blind at unit index 30 and reported fds_ge3=0 for every unit above
    it while they held whatever they held. The probe sweeps /proc/self/fd now;
    this is the test that exercises the range the fix exists to cover, since
    a leak that only appears at high unit indices is invisible to a 4-unit
    city by construction."""
    n = int(blob_h("NW_MAX_UNITS"))
    probe = f"{BIN}/unit-probe"
    city = f"{WORK}/maxunits.city"
    open(city, "w").write("".join(
        f"house u{i:02d} {probe} kind=oneshot lids=none\n" for i in range(n)))
    blob = f"{WORK}/maxunits.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")

    rc, out = boot(plan=blob, hold=3000)
    expect(city_closed(rc, out), f"max-unit city rc={rc}\n{out[-3000:]}")
    expect(f"houses={n}" in out, f"expected {n} units\n{out[-3000:]}")

    reported = dict(re.findall(r"house=(\S+) fds_ge3=(-?\d+)", out))
    expect(len(reported) == n,
           f"only {len(reported)} of {n} units reported\n{out[-3000:]}")
    dirty = {h: v for h, v in reported.items() if v != "0"}
    expect(not dirty,
           f"units hold descriptors they were not granted: "
           f"{sorted(dirty.items())[:8]}")
    expect(f"houses_reaped={n}" in out, f"reap\n{out[-2000:]}")
    print(f"ok non-provision-at-max ({n} units)")


def test_build_is_reproducible():
    """The same source must produce the same binaries from any directory.

    Two clean builds in one tree were already identical; a build of the same
    source in a different directory was not, because the absolute source path
    leaks into the binaries through debug info. That breaks the only claim
    that makes a reviewed artifact meaningful -- that what runs is what was
    read. `-ffile-prefix-map=$(CURDIR)=.` in the Makefile fixes it, and this
    is what stops the flag being dropped without anyone noticing.

    Builds in two paths of different lengths. That is a precaution, not a
    demonstrated property: with equal-length names the control fails
    identically, so nothing here evidences that the width matters. It is
    kept because it can only help.

    Note what this pins and what it does not. It pins the *property* --
    same source, same binaries, different directory -- not any particular
    flag: deleting `-g` outright, or using the weaker `-fdebug-prefix-map`,
    both leave it green today, because no C source here uses `__FILE__` or
    `assert()`. It also builds each directory once, so nondeterminism that
    is constant between two builds seconds apart (`__DATE__`, an embedded
    build id) would pass."""
    import shutil, tempfile
    srcs = [f for f in os.listdir(ROOT)
            if f.endswith((".c", ".h")) or f == "Makefile"]
    hashes = []
    with tempfile.TemporaryDirectory(dir=WORK) as base:
        for sub in ("a", "bbbbbbbbbbbb"):
            d = os.path.join(base, sub)
            os.makedirs(os.path.join(d, "houses"))
            for f in srcs:
                shutil.copy(os.path.join(ROOT, f), d)
            for f in os.listdir(os.path.join(ROOT, "houses")):
                shutil.copy(os.path.join(ROOT, "houses", f),
                            os.path.join(d, "houses"))
            b = run(["make", "-C", d, "-j4"])
            expect(b.returncode == 0, f"build in {sub} failed\n{b.err[-800:]}")
            got = {}
            for f in sorted(os.listdir(d)):
                p = os.path.join(d, f)
                if os.path.isfile(p) and os.access(p, os.X_OK) and "." not in f:
                    got[f] = hashlib.sha256(open(p, "rb").read()).hexdigest()
            expect(len(got) >= 8, f"only built {sorted(got)}")
            hashes.append(got)

    a, b = hashes
    expect(set(a) == set(b), f"different binaries built: {set(a) ^ set(b)}")
    differing = sorted(k for k in a if a[k] != b[k])
    expect(not differing,
           f"these binaries depend on the build directory: {differing} -- "
           f"something is embedding an absolute source path. Most likely "
           f"CFLAGS lost -ffile-prefix-map, but a new __FILE__ or assert() "
           f"in the C sources would do it too.")
    print(f"ok build-is-reproducible ({len(a)} binaries, two paths)")


def test_harness_runs_fresh_binaries():
    """The suite must execute what was just built.

    It runs staged binaries from STAGE, not the source tree, so `make` alone
    leaves it testing the previous build -- and a genuinely broken
    pivot_root then goes fully green. That has happened twice: once to a
    negative control that passed and read as success, and again when the
    control agent showed a skipped pivot_root is invisible with either half
    of the NW_STAGE plumbing removed. Both halves are a conjunction and
    neither was pinned. This pins the property they exist for, without
    caring how it is achieved."""
    # Every staged binary, not a hand-written list. The list omitted
    # unit-badcall, unit-boom and unit-term -- the fixtures that carry the
    # absence assertions -- so editing houses/badcall.c to drop its
    # socket() call and running `make` without `stage` left seccomp-kill
    # green against a fixture that was not the one just built. That is the
    # exact trap this test exists for, on the binaries where it matters
    # most. Found by `control`.
    staged_names = sorted(os.listdir(BIN))
    expect(len(staged_names) >= 8, f"only {len(staged_names)} staged binaries")
    stale = []
    for b in staged_names:
        src, staged = os.path.join(ROOT, b), f"{BIN}/{b}"
        expect(os.path.exists(src), f"{b} is staged but not in the tree")
        if open(src, "rb").read() != open(staged, "rb").read():
            stale.append(b)
    expect(not stale,
           f"the suite is running binaries that are not the ones just built: "
           f"{stale} -- run `make stage`, not `make`")

    # And the sources must not be newer than the binaries. The comparison
    # above is two binaries, so it is green by construction when neither was
    # rebuilt: editing nwcheck.c and running the suite with no make at all
    # passes it, and every source-reading test in this suite -- the staged
    # src/ probe, blob_h(), the layout offsets -- is then answering for code
    # that is not under test. Found by `control`, which reproduced it by
    # changing hash_name and running the suite with no build step.
    #
    # mtime, not content: there is no build output to compare against, and
    # the question is "was this edited after the build", which is what mtime
    # answers.
    # Walk, do not list: os.listdir(ROOT) does not see houses/*.c, which
    # is where the fixtures live.
    srcs = []
    for d, _, files in os.walk(ROOT):
        if os.path.basename(d) in (".git", ".reviews", "coverage"):
            continue
        srcs += [os.path.join(d, f) for f in files
                 if f.endswith((".c", ".h"))]
    expect(srcs, "no sources found under ROOT")
    newest = max((os.path.getmtime(f), os.path.relpath(f, ROOT))
                 for f in srcs)
    oldest = min((os.path.getmtime(f"{BIN}/{b}"), b)
                 for b in ("nw-root", "nw-check", "nw-sup"))
    expect(newest[0] <= oldest[0],
           f"{newest[1]} was modified after {oldest[1]} was staged: the "
           f"binaries under test do not contain that edit, and the tests "
           f"that read sources will answer for code nothing is running. "
           f"Run `make stage`.")
    print("ok harness-runs-fresh-binaries (content and mtime)")


def test_coverage_accounting():
    """The layer whose whole job is to stop the suite overstating what it
    verified. It had no test until 2026-09-10, and every control on it
    passed."""
    expect(counts_as_passed("a", []) is True, "a clean test passes")
    expect(counts_as_passed("landlock-confines",
                            [("landlock-confines", "no landlock")]) is False,
           "a test that skipped itself is not a passing test")
    expect(counts_as_passed("dawn-real-boot",
                            [("dawn-real-boot:vfat-esp", "no fat")]) is True,
           "a partial skip must not disqualify the test that raised it")
    expect(counts_as_passed("b", [("a", "x")]) is True,
           "another test's skip must not disqualify this one")

    # The record must actually be written, and its name must carry the
    # capability tag -- two machines on one kernel with different
    # capabilities must not collide onto one filename.
    import json, tempfile
    with tempfile.TemporaryDirectory(dir=WORK) as d:
        rel = write_coverage(["alpha"], outdir=d)
        files = os.listdir(d)
        expect(len(files) == 1, f"write_coverage wrote {files}")
        name = files[0][:-5]
        expect(re.search(r"-[0-9a-f]{8}$", name),
               f"coverage label carries no capability tag: {name}")
        rec = json.load(open(os.path.join(d, files[0])))
        expect(rec["passed"] == ["alpha"], f"passed not recorded: {rec}")
        expect("capabilities" in rec and "kernel" in rec, f"thin record: {rec}")
    print("ok coverage-accounting")


def test_hash_pin():
    h = open(f"{SLOTS}/A/plan.blob.sha256").read().strip()
    expect(len(h) == 64, "sha256 len")
    import hashlib
    got = hashlib.sha256(open(f"{SLOTS}/A/plan.blob", "rb").read()).hexdigest()
    expect(h == got, "sha256 match")
    print("ok hash-pin")


def capabilities():
    return {
        "landlock": landlock_abi() is not None,
        "vfat": fs_mountable("vfat"),
        "erofs": fs_mountable("erofs"),
        "squashfs": fs_mountable("squashfs"),
        "loop": run(["sh", "-c", "command -v losetup"]).returncode == 0,
    }


def counts_as_passed(name, new_skips):
    """Did a test that just returned actually pass?

    No, if it skipped itself: a skipped test is not a passing test, and
    counting one made coverage-merge report landlock-confines as "covered
    somewhere" on a kernel that returns ENOSYS. Yes, if the only skip it
    raised was a partial one -- "dawn-real-boot:vfat-esp" names a part of a
    test that otherwise ran.

    Split out so it can be tested. Both directions of this were unpinned
    until 2026-09-10, and deleting either reproduced a defect this
    repository had already written down."""
    return not any(n == name for n, _ in new_skips)


def write_coverage(passed, outdir=None):
    """Drop this environment's record so coverage can be merged across
    machines. No single environment has ever run every test here: Landlock is
    ABI 7 on one machine and ENOSYS on another, and neither has a FAT driver,
    so vfat-esp runs in none. That made the coverage claim a union of machines
    living only in prose. tools/coverage-merge.sh reads these."""
    import hashlib, platform, json
    caps = capabilities()
    kern = platform.release()
    tag = hashlib.sha256(
        (kern + json.dumps(caps, sort_keys=True)).encode()).hexdigest()[:8]
    label = re.sub(r"[^A-Za-z0-9._-]", "_", kern) + "-" + tag
    outdir = outdir or os.path.join(ROOT, "coverage")
    os.makedirs(outdir, exist_ok=True)
    rec = {"kernel": kern, "capabilities": caps,
           "passed": sorted(passed), "skipped": dict(SKIPPED)}
    path = os.path.join(outdir, label + ".json")
    with open(path, "w") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return os.path.relpath(path, ROOT)


def main():
    os.chdir(ROOT)
    print_environment()
    print("== city suite ==")
    tests = [
        test_build_is_reproducible,
        test_harness_runs_fresh_binaries, test_coverage_accounting,
        test_hash_pin, test_difftest, test_lids_are_not_advisory,
        test_baker_rejects, test_fuzz_checker, test_happy, test_slot_b,
        test_rescue, test_halt_spawner, test_bad_crc,
        test_crash_does_not_halt, test_budget_is_hard_total,
        test_shutdown_does_not_restart, test_term_signal, test_dawn_real_boot,
        test_kind_required, test_kind_exit0, test_seccomp_kills,
        test_brick_is_a_root, test_brick_needs_newns,
        test_path_traversal_refused, test_dupname_refused,
        test_blob_size_ceiling,
        test_checker_rejects_crafted_fields,
        test_non_provision_at_max,
        test_landlock_confines,
    ]
    passed = []
    for t in tests:
        before = len(SKIPPED)
        t()
        name = t.__name__[len("test_"):].replace("_", "-")
        # A test that skipped itself did not pass. Counting it as passed is
        # the same defect the skip machinery exists to prevent, one layer up:
        # tools/coverage-merge.sh reported landlock-confines as "covered
        # somewhere" on a kernel that cannot run it. A partial skip -- a name
        # like "dawn-real-boot:vfat-esp" -- does not disqualify the test that
        # raised it, only the part it names.
        if not counts_as_passed(name, SKIPPED[before:]):
            continue
        passed.append(name)

    # A full skip must name a test, or the guard above silently misses it:
    # `passed` names come from function names and skip names are typed by
    # hand, and they already disagree for several tests.
    known = {t.__name__[len("test_"):].replace("_", "-") for t in tests}
    stray = [n for n, _ in SKIPPED if ":" not in n and n not in known]
    if stray:
        raise SystemExit(
            f"FAIL: skip name(s) {stray} match no test; a full skip must use "
            f"the test's own name or it will be counted as passed")

    rec = write_coverage(passed)
    if SKIPPED:
        print("PASSED, WITH SKIPS -- this environment could not exercise:")
        for name, why in SKIPPED:
            print(f"  {name}: {why}")
        print("A skipped test is not a passing test. Do not report this run "
              "as evidence about the features named above.")
    else:
        print("ALL TESTS PASSED")
    print(f"coverage record: {rec}  "
          f"(merge across machines: sh tools/coverage-merge.sh)")


if __name__ == "__main__":
    main()
