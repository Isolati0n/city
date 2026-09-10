#!/usr/bin/env python3
"""Put-together suite. Not in the TCB."""
from __future__ import annotations

import os
import re
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


def blob_h(name):
    """Read a #define out of blob.h. Limits are derived, never declared
    twice -- that includes here: a test that hardcodes NW_MAX_UNITS stops
    testing the maximum the day the maximum moves."""
    for line in open(os.path.join(ROOT, "blob.h")):
        f = line.split()
        if len(f) >= 3 and f[0] == "#define" and f[1] == name:
            return f[2]
    raise SystemExit(f"blob.h has no {name}")


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
    """Baker output must be accepted by C nw-check; flipped crc must not.

    Also pins the magic across the two implementations. NW_MAGIC in blob.h is
    now what nw_check compares against, but the baker has its own literal and
    cannot include the header -- so the two are a place that must agree, and
    this is what makes disagreeing fail rather than produce a confusing
    NW_E_MAGIC at boot."""
    r = run([f"{BIN}/nw-check", f"{SLOTS}/A/plan.blob"])
    expect(r.returncode == 0, "difftest good")

    want = blob_h("NW_MAGIC").strip('"')
    src = open(CC).read()
    lit = re.findall(r'b"(NWPLAN\d\d)"', src)
    expect(lit, "no magic literal found in the baker")
    expect(all(m == want for m in lit),
           f"blob.h NW_MAGIC is {want!r}, baker emits {set(lit)!r}")
    expect(open(f"{SLOTS}/A/plan.blob", "rb").read(8) == want.encode(),
           "the staged blob does not carry NW_MAGIC")
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
    expect(rc == 0, f"brick city rc={rc}\n{out}")
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
    expect("HALT" in out, f"a traversing plan must not open the city\n{out}")
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
    lids_off = 20 + 32 + 128 + 96 + 4          # hdr + name + exec + brick + kind/budget/window
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

    lid_landlock used to say-and-continue on three paths -- Landlock absent,
    ruleset creation failed, restrict_self failed. On each, the house ran with
    no file restriction while the plan said it was confined, the boot
    succeeded, and nothing noticed. Invariant 6 says a lid decides what a
    house can do; a lid that decides nothing while claiming to is the same
    defect as a brick that roots on the machine while logging `lid brick`.

    Written to assert the *rule*, not this container: Landlock is compiled out
    here, but the target kernel has it. Either the lid goes on and the house
    runs, or it does not and the house does not -- and never a third outcome."""
    probe = f"{BIN}/unit-probe"
    city = f"{WORK}/lid-advisory.city"
    open(city, "w").write(
        f"house locked {probe} kind=oneshot lids=landlock\n"
        f"house plain {probe} kind=oneshot budget=0 lids=none\n"
    )
    blob = f"{WORK}/lid-advisory.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out = boot(plan=blob, hold=1500)

    # The say-and-continue form was "[nw-sup] landlock ..."; the fatal form is
    # "[nw-sup] FAIL landlock ...". If a soft line ever comes back, so does
    # the defect.
    expect("[nw-sup] landlock" not in out,
           f"a declared lid was skipped with a log line\n{out}")

    applied = "lid landlock" in out
    refused = "FAIL landlock" in out
    expect(applied != refused,
           f"exactly one of applied/refused must happen\n{out}")

    if refused:
        expect("house=locked" not in out,
               f"lid could not be applied and the house ran anyway\n{out}")
    else:
        expect("house=locked" in out,
               f"lid was applied but the house did not run\n{out}")

    # Nothing a house does halts the city: the other house boots either way.
    expect("house=plain" in out, f"an unrelated house must still run\n{out}")
    expect(rc == 0 and "HALT" not in out,
           f"a house that cannot wear its lid must not halt the city\n{out}")
    print("ok lids-not-advisory" + (" (landlock absent here)" if refused else ""))


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
    expect(rc == 0, f"max-unit city rc={rc}\n{out[-3000:]}")
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
    test_lids_are_not_advisory()
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
    test_brick_is_a_root()
    test_brick_needs_newns()
    test_path_traversal_refused()
    test_non_provision_at_max()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
