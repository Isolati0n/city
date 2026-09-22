#!/usr/bin/env python3
"""tools/console-boot-test.py -- the console house's proof, per
docs/options/10-console-house.md's test design.

Not TCB. Boots a real QEMU guest (via tools/mkboot.sh's image-building
step, reused rather than duplicated) with one house: the console house,
on its own dedicated serial line (ttyS1) that nothing else in the guest
ever writes to -- the kernel's own console stays on ttyS0. Connects to
that line, sends a distinguishing command, and requires the exact string
back. Then reruns with the SAME plan minus the one thing under test --
the console house's `bind=/dev/ttyS1` -- and requires silence, so the
positive result is pinned to the bind mechanism specifically and not to
"the guest booted" in general.

Needs: qemu-system-x86_64, mkfs.erofs (via bakery/mkbrick.py), root (loop
mounts), and a static busybox at /bin/busybox (`apt-get install
busybox-static`; /usr/lib/initramfs-tools/bin/busybox is dynamically
linked and will not run inside a brick with no loader). Missing any of
these is a named skip, not a crash -- the same discipline
.claude/rules/harness.md requires of tests/run.py, applied here even
though this script sits outside that suite.

Usage: sudo python3 tools/console-boot-test.py
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = "NWCONSOLE-8f2c1a"


class Unavailable(Exception):
    pass


def need(tool):
    if shutil.which(tool) is None:
        raise Unavailable(f"missing tool: {tool}")


def check_environment():
    need("qemu-system-x86_64")
    need("mkfs.erofs")
    if os.geteuid() != 0:
        raise Unavailable("must run as root (loop mounts in mkboot.sh)")
    bb = "/bin/busybox"
    if not os.path.exists(bb):
        raise Unavailable(
            f"{bb} not found -- `apt-get install busybox-static`")
    out = subprocess.run(["file", bb], capture_output=True, text=True).stdout
    if "statically linked" not in out:
        raise Unavailable(
            f"{bb} is not statically linked ({out.strip()}); a brick "
            f"carries no loader and no libc, so a dynamic busybox would "
            f"fail execve inside it with ENOENT -- the exact trap "
            f".claude/rules/harness.md names for dawn-real-boot")


def build_wrapper(build_dir):
    wrap = os.path.join(build_dir, "unit-console-wrap")
    r = subprocess.run(
        ["gcc", "-Wall", "-Wextra", "-O2", "-std=gnu11", "-static",
         "-o", wrap, os.path.join(ROOT, "houses/console-wrap.c")],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL: building console-wrap\n{r.stdout}{r.stderr}")
    return wrap


def build_brick(wrap_path, brick_out_dir):
    """Assemble the console house's brick tree and pack it. Returns the hex
    hash bakery/mkbrick.py names the image by."""
    tree = tempfile.mkdtemp(prefix="nw-console-brick-")
    os.makedirs(f"{tree}/bin")
    os.makedirs(f"{tree}/dev")
    shutil.copy(wrap_path, f"{tree}/bin/wrap")
    os.chmod(f"{tree}/bin/wrap", 0o755)
    shutil.copy("/bin/busybox", f"{tree}/bin/busybox")
    os.chmod(f"{tree}/bin/busybox", 0o755)
    # The bind target must already exist inside the brick -- nw-sup will
    # not mkdir into one (plan.md's hard rules). An empty regular file:
    # the bind loop's `mount(binds[i], tgt, NULL, MS_BIND|MS_REC, NULL)`
    # needs a same-type target, and a plain file is what the negative
    # control below relies on being harmless to open() on its own.
    open(f"{tree}/dev/ttyS1", "wb").close()

    os.makedirs(brick_out_dir, exist_ok=True)
    r = subprocess.run(
        ["python3", os.path.join(ROOT, "bakery/mkbrick.py"), tree,
         "--out-dir", brick_out_dir, "--quiet"],
        capture_output=True, text=True)
    shutil.rmtree(tree, ignore_errors=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL: mkbrick\n{r.stdout}{r.stderr}")
    hexd = r.stdout.strip()
    if not os.path.exists(f"{brick_out_dir}/{hexd}.img"):
        raise SystemExit(f"FAIL: mkbrick printed {hexd!r} but wrote no image")
    return hexd


# No seccomp. lids are per-house, not a single global choice -- the
# operator's correction to this design's first attempt, which reached
# for the one shared seccomp filter and then had to widen it by ten
# syscalls busybox needs. newns + landlock + newnet costs nothing to any
# OTHER house (lids.c is untouched) and the cost this house itself pays
# is stated rather than hidden: uid 0, the full syscall surface, confined
# only by its own mount namespace, Landlock, and a net namespace with no
# interfaces. Whether that confinement actually holds -- can this house
# mknod a device, or mount its way out of the brick -- is measured in
# measure_escape() below, not assumed from reading invariant 6.
CONSOLE_LIDS = "newns,landlock,newnet"


def write_city(path, brick_hex, with_bind, lids=CONSOLE_LIDS):
    bind = " bind=/dev/ttyS1" if with_bind else ""
    with open(path, "w") as f:
        f.write(
            f"house console /bin/wrap kind=oneshot budget=3 "
            f"lids={lids} brick={brick_hex} "
            f"layer=console{bind}\n")


def build_probe(build_dir):
    probe = os.path.join(build_dir, "unit-console-escape-probe")
    r = subprocess.run(
        ["gcc", "-Wall", "-Wextra", "-O2", "-std=gnu11", "-static",
         "-o", probe,
         os.path.join(ROOT, "houses/console-escape-probe.c")],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL: building console-escape-probe\n{r.stdout}{r.stderr}")
    return probe


def build_probe_brick(probe_path, brick_out_dir):
    """The measurement-only brick: the probe binary and an empty /mnt for
    it to target, nothing else. No wrapper, no busybox, no tty bind --
    this house needs neither a shell nor a terminal, only its own root
    to try mounting into and out of."""
    tree = tempfile.mkdtemp(prefix="nw-console-probe-brick-")
    os.makedirs(f"{tree}/bin")
    os.makedirs(f"{tree}/mnt")
    shutil.copy(probe_path, f"{tree}/bin/probe")
    os.chmod(f"{tree}/bin/probe", 0o755)

    os.makedirs(brick_out_dir, exist_ok=True)
    r = subprocess.run(
        ["python3", os.path.join(ROOT, "bakery/mkbrick.py"), tree,
         "--out-dir", brick_out_dir, "--quiet"],
        capture_output=True, text=True)
    shutil.rmtree(tree, ignore_errors=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL: mkbrick (probe)\n{r.stdout}{r.stderr}")
    hexd = r.stdout.strip()
    if not os.path.exists(f"{brick_out_dir}/{hexd}.img"):
        raise SystemExit(f"FAIL: mkbrick printed {hexd!r} but wrote no image")
    return hexd


def write_probe_city(path, brick_hex, lids=CONSOLE_LIDS, with_victim=False):
    lines = [
        f"house probe /bin/probe kind=oneshot budget=1 "
        f"lids={lids} brick={brick_hex} layer=probe\n"
    ]
    if with_victim:
        # Brickless, staged straight onto the root image (NW_EXTRA_BIN),
        # the same way unit-probe is -- an ordinary house, not sealed,
        # for the process-reach measurement. budget=0: if the probe's
        # SIGKILL against it lands, that is the finding; nothing should
        # bring it back and mask that.
        lines.append(
            "house victim /nw/bin/unit-reach-victim kind=oneshot "
            "budget=0 lids=seccomp\n")
    with open(path, "w") as f:
        f.writelines(lines)


def build_victim(build_dir):
    victim = os.path.join(build_dir, "unit-reach-victim")
    r = subprocess.run(
        ["gcc", "-Wall", "-Wextra", "-O2", "-std=gnu11", "-static",
         "-o", victim, os.path.join(ROOT, "houses/reach-victim.c")],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL: building reach-victim\n{r.stdout}{r.stderr}")
    return victim


def build_images(out_dir, city_path, brick_dir):
    env = dict(os.environ)
    env["OUT"] = out_dir
    env["NW_CITY"] = city_path
    env["NW_EXTRA_BRICKS"] = brick_dir
    r = subprocess.run(
        ["sh", os.path.join(ROOT, "tools/mkboot.sh")],
        cwd=ROOT, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL: mkboot.sh\n{r.stdout}{r.stderr}")
    return r.stdout


def boot_and_probe(out_dir, want_response):
    """Boot the images in out_dir with a second serial chardev on ttyS1.
    Returns (ttyS0_log_text, ttyS1_bytes_received).

    `want_response` is True for the original single-token echo, False for
    the no-bind control (no commands, just confirm silence), or a list of
    shell command strings sent in order -- each command's raw output is
    read back before the next is sent, so a probe reports what actually
    happened for each escape attempt rather than one merged blob."""
    kernel = os.environ.get("KERNEL", "/boot/vmlinuz")
    accel = os.environ.get("ACCEL", "tcg")
    append = ("console=ttyS0,115200n8 ignore_loglevel NW_ROOT=/dev/vda "
              "NW_ROOT_FSTYPE=ext4 NW_ESP=/dev/vdb NW_ESP_FSTYPE=vfat")
    log0 = os.path.join(out_dir, "console.log")
    sock_path = os.path.join(out_dir, "ttyS1.sock")
    for p in (log0, sock_path):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass

    cmd = [
        "qemu-system-x86_64",
        "-machine", f"q35,accel={accel}", "-cpu", "max", "-m", "512M",
        "-nographic", "-no-reboot", "-display", "none",
        "-serial", f"file:{log0}",
        "-chardev", f"socket,id=consock,path={sock_path},server=on,wait=off",
        "-serial", "chardev:consock",
        "-monitor", "none",
        "-kernel", kernel,
        "-initrd", os.path.join(out_dir, "initrd.cpio"),
        "-append", append,
        "-drive", f"file={out_dir}/root.img,if=virtio,format=raw,cache=writeback",
        "-drive", f"file={out_dir}/esp.img,if=virtio,format=raw,cache=writeback",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    received = b""
    try:
        # Wait for city open before touching ttyS1, so a probe that finds
        # nothing there is not confused with a boot that never got that far.
        deadline = time.time() + 30
        opened = False
        while time.time() < deadline:
            if os.path.exists(log0) and "city open" in open(log0).read():
                opened = True
                break
            time.sleep(0.5)
        if not opened:
            return (open(log0).read() if os.path.exists(log0) else ""), b""

        deadline = time.time() + 10
        sock = None
        while time.time() < deadline:
            if os.path.exists(sock_path):
                try:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.connect(sock_path)
                    break
                except OSError:
                    sock = None
            time.sleep(0.3)
        if sock is None:
            return open(log0).read(), b""

        sock.settimeout(0.5)
        end = time.time() + 4
        while time.time() < end:
            try:
                r = sock.recv(4096)
                if not r:
                    break
                received += r
            except socket.timeout:
                pass
        commands = [f"echo {TOKEN}"] if want_response is True else (want_response or [])
        for line in commands:
            sock.sendall(f"{line}\n".encode())
            end = time.time() + 3
            while time.time() < end:
                try:
                    r = sock.recv(4096)
                    if not r:
                        break
                    received += r
                except socket.timeout:
                    pass
        try:
            sock.sendall(b"exit\n")
        except OSError:
            pass
        end = time.time() + 2
        while time.time() < end:
            try:
                r = sock.recv(4096)
                if not r:
                    break
                received += r
            except socket.timeout:
                pass
        sock.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return open(log0).read() if os.path.exists(log0) else "", received


def boot_and_wait(out_dir, wait_s):
    """Boot the images in out_dir and just read the ttyS0 log for wait_s
    seconds after city open -- no ttyS1 interaction at all. For a
    measurement like process-reach, where the probe's own battery (a PID
    sweep plus per-pid kill/ptrace/process_vm_readv) and the victim
    house's lifetime both take longer than the short exchanges the
    other boot_and_probe() callers need. Returns the ttyS0 log text."""
    kernel = os.environ.get("KERNEL", "/boot/vmlinuz")
    accel = os.environ.get("ACCEL", "tcg")
    append = ("console=ttyS0,115200n8 ignore_loglevel NW_ROOT=/dev/vda "
              "NW_ROOT_FSTYPE=ext4 NW_ESP=/dev/vdb NW_ESP_FSTYPE=vfat")
    log0 = os.path.join(out_dir, "console.log")
    try:
        os.remove(log0)
    except FileNotFoundError:
        pass

    cmd = [
        "qemu-system-x86_64",
        "-machine", f"q35,accel={accel}", "-cpu", "max", "-m", "512M",
        "-nographic", "-no-reboot", "-display", "none",
        "-serial", f"file:{log0}",
        "-monitor", "none",
        "-kernel", kernel,
        "-initrd", os.path.join(out_dir, "initrd.cpio"),
        "-append", append,
        "-drive", f"file={out_dir}/root.img,if=virtio,format=raw,cache=writeback",
        "-drive", f"file={out_dir}/esp.img,if=virtio,format=raw,cache=writeback",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 30
        opened = False
        while time.time() < deadline:
            if os.path.exists(log0) and "city open" in open(log0).read():
                opened = True
                break
            time.sleep(0.5)
        if not opened:
            return open(log0).read() if os.path.exists(log0) else ""
        time.sleep(wait_s)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return open(log0).read() if os.path.exists(log0) else ""


def measure_process_reach(workroot):
    """What an unconfined (no seccomp) console house can do to ANOTHER
    process in the city -- kill it, ptrace it, read its memory -- per
    the operator's follow-up to measure_escape() below, which covered
    the filesystem only. Boots the probe alongside houses/reach-
    victim.c, an ordinary lids=seccomp house with no brick, and reads
    every reach_* line the probe's PID sweep produced."""
    probe = build_probe(workroot)
    victim = build_victim(workroot)
    brick_dir = os.path.join(workroot, "reach-bricks")
    brick_hex = build_probe_brick(probe, brick_dir)
    bin_dir = os.path.join(workroot, "reach-bin")
    os.makedirs(bin_dir, exist_ok=True)
    shutil.copy(victim, os.path.join(bin_dir, "unit-reach-victim"))

    city = os.path.join(workroot, "reach.city")
    write_probe_city(city, brick_hex, with_victim=True)
    out = os.path.join(workroot, "boot-reach")

    env = dict(os.environ)
    env["OUT"] = out
    env["NW_CITY"] = city
    env["NW_EXTRA_BRICKS"] = brick_dir
    env["NW_EXTRA_BIN"] = bin_dir
    r = subprocess.run(["sh", os.path.join(ROOT, "tools/mkboot.sh")],
                        cwd=ROOT, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"FAIL: mkboot.sh (reach)\n{r.stdout}{r.stderr}")

    log = boot_and_wait(out, wait_s=10)
    if "city open" not in log:
        print(f"MEASUREMENT FAILED: boot never reached city open\n{log}")
        return None
    lines = [ln for ln in log.splitlines() if "PROBE reach_" in ln]
    print("=== process-reach measurement: probe results (direct syscalls) ===")
    for ln in lines:
        print(ln)
    print("=== end ===")
    if not lines:
        print(f"MEASUREMENT FAILED: no PROBE reach_ lines in the log\n{log}")
    return lines


def measure_escape(workroot):
    """What lids=newns,landlock,newnet actually lets this house do, with no
    seccomp lid at all -- the operator's own instruction: report what it
    CAN do, measured in QEMU, not argued from CLAUDE.md's invariant 6.

    A dedicated probe binary (houses/console-escape-probe.c), not
    busybox: an earlier attempt sent `mount`/`unshare` over the
    interactive shell and both came back "not found", which measured
    something real but not the thing asked -- ash's standalone-shell
    dispatch for those two applets goes through a re-exec path that
    reads something under /proc, and a house's own pivoted root never
    mounts /proc at all, so the ambiguity was "missing dependency", not
    "Landlock refused it". The probe calls each syscall directly and
    prints its own errno; nothing between the syscall and this
    function's output can misreport why something failed. It runs
    kind=oneshot, needs no bind and no tty, and its stdout goes to the
    ordinary log pipe -- so the measurement reads the ttyS0 boot log,
    not the ttyS1 socket."""
    probe = build_probe(workroot)
    brick_dir = os.path.join(workroot, "bricks")
    brick_hex = build_probe_brick(probe, brick_dir)
    city = os.path.join(workroot, "console-escape.city")
    write_probe_city(city, brick_hex)
    out = os.path.join(workroot, "boot-escape")
    build_images(out, city, brick_dir)
    log, _ = boot_and_probe(out, want_response=False)
    if "city open" not in log:
        print(f"MEASUREMENT FAILED: boot never reached city open\n{log}")
        return None
    lines = [ln for ln in log.splitlines() if "PROBE " in ln]
    print("=== escape measurement: probe results (direct syscalls) ===")
    for ln in lines:
        print(ln)
    print("=== end ===")
    if not lines:
        print(f"MEASUREMENT FAILED: no PROBE lines in the log\n{log}")
    return lines


def main():
    try:
        check_environment()
    except Unavailable as e:
        print(f"SKIP: console-boot-test ({e})")
        return 0

    only_escape = "--measure-escape" in sys.argv[1:]
    only_reach = "--measure-reach" in sys.argv[1:]

    workroot = tempfile.mkdtemp(prefix="nw-console-boot-")
    try:
        if only_escape:
            measure_escape(workroot)
            return 0
        if only_reach:
            measure_process_reach(workroot)
            return 0

        wrap = build_wrapper(workroot)
        brick_dir = os.path.join(workroot, "bricks")
        brick_hex = build_brick(wrap, brick_dir)
        print(f"brick: {brick_hex}")

        # POSITIVE: the plan as designed, bind=/dev/ttyS1 declared.
        pos_city = os.path.join(workroot, "console.city")
        write_city(pos_city, brick_hex, with_bind=True)
        pos_out = os.path.join(workroot, "boot-pos")
        build_images(pos_out, pos_city, brick_dir)
        log, resp = boot_and_probe(pos_out, want_response=True)
        if "city open" not in log:
            print(f"FAIL: positive boot never reached city open\n{log}")
            return 1
        if TOKEN.encode() not in resp:
            print(f"FAIL: token not echoed back over ttyS1\n"
                  f"received: {resp!r}\nlog:\n{log}")
            return 1
        print(f"ok console-house-reachable (received {resp!r})")

        # CONTROL: identical plan, bind= removed. Per harness.md's "a test
        # you add must be shown failing when the thing it tests is
        # removed" -- here "failing" IS the desired outcome, since this
        # run is the negative control, not the test itself.
        neg_city = os.path.join(workroot, "console-nobind.city")
        write_city(neg_city, brick_hex, with_bind=False)
        neg_out = os.path.join(workroot, "boot-neg")
        build_images(neg_out, neg_city, brick_dir)
        log2, resp2 = boot_and_probe(neg_out, want_response=True)
        if "city open" not in log2:
            print(f"FAIL: control boot never reached city open\n{log2}")
            return 1
        if resp2:
            print(f"FAIL: control (no bind=) should be silent on ttyS1, "
                  f"got {resp2!r} -- the positive result is not pinned to "
                  f"the bind mechanism")
            return 1
        print("ok console-house-control (silent on ttyS1 with bind= removed, "
              "as required)")

        # CONTROL: same plan, seccomp ADDED to the lid set. Proves the lid
        # set is what makes this work -- not the brick, not the bind, not
        # the wrapper -- by showing the one thing this round changed
        # (dropping seccomp) is load-bearing: put it back and the house
        # dies, budget exhausted, the same way the first build attempt did
        # before the lid set was corrected.
        sec_city = os.path.join(workroot, "console-secc.city")
        write_city(sec_city, brick_hex, with_bind=True,
                   lids=CONSOLE_LIDS + ",seccomp")
        sec_out = os.path.join(workroot, "boot-secc")
        build_images(sec_out, sec_city, brick_dir)
        log3, resp3 = boot_and_probe(sec_out, want_response=True)
        if "city open" not in log3:
            print(f"FAIL: seccomp-control boot never reached city open\n{log3}")
            return 1
        if TOKEN.encode() in resp3:
            print(f"FAIL: adding seccomp back should have killed the house "
                  f"(it needs syscalls strict_allow[] does not have) -- "
                  f"instead the token came back: {resp3!r}\nlog:\n{log3}")
            return 1
        if "spent console" not in log3:
            print(f"FAIL: seccomp-control did not die the expected way -- "
                  f"expected the budget exhausted (\"spent console\"), got:"
                  f"\n{log3}")
            return 1
        print("ok console-house-seccomp-control (adding seccomp back kills "
              "the house, budget exhausted, as required)")

        # PIN: the baked blob's lids byte is exactly newns|landlock|newnet
        # (14) -- no seccomp bit, no stray bit either. Offsets from
        # blob.h: nw_hdr is 20 bytes, lids is nw_unit's byte 226, and this
        # plan bakes exactly one unit, so the byte is at 20 + 226 = 246.
        plan_blob_path = os.path.join(workroot, "lids-pin.blob")
        b = subprocess.run(
            ["python3", os.path.join(ROOT, "bakery/nw-cc.py"),
             "--city", pos_city, "--out", plan_blob_path],
            capture_output=True, text=True)
        if b.returncode != 0:
            print(f"FAIL: re-baking for the lids pin\n{b.stdout}{b.stderr}")
            return 1
        blob = open(plan_blob_path, "rb").read()
        NW_HDR_SIZE = 20
        LIDS_OFFSET_IN_UNIT = 226
        got_lids = blob[NW_HDR_SIZE + LIDS_OFFSET_IN_UNIT]
        want_lids = 0x02 | 0x04 | 0x08  # LANDLOCK | NEWNS | NEWNET, no SECCOMP
        if got_lids != want_lids:
            print(f"FAIL: lids byte is {got_lids:#04x}, expected "
                  f"{want_lids:#04x} (landlock|newns|newnet, no seccomp)")
            return 1
        print(f"ok console-house-lids-exact (blob lids byte = "
              f"{got_lids:#04x} = landlock|newns|newnet, no seccomp)")
    finally:
        shutil.rmtree(workroot, ignore_errors=True)

    print("ALL CONSOLE BOOT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
