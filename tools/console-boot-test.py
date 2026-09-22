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


def write_city(path, brick_hex, with_bind):
    bind = " bind=/dev/ttyS1" if with_bind else ""
    with open(path, "w") as f:
        f.write(
            f"house console /bin/wrap kind=oneshot budget=3 "
            f"lids=newns,landlock,seccomp brick={brick_hex} "
            f"layer=console{bind}\n")


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
    Returns (ttyS0_log_text, ttyS1_bytes_received)."""
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
        if want_response:
            sock.sendall(f"echo {TOKEN}\n".encode())
        end = time.time() + 4
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
        sock.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return open(log0).read() if os.path.exists(log0) else "", received


def main():
    try:
        check_environment()
    except Unavailable as e:
        print(f"SKIP: console-boot-test ({e})")
        return 0

    workroot = tempfile.mkdtemp(prefix="nw-console-boot-")
    try:
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
    finally:
        shutil.rmtree(workroot, ignore_errors=True)

    print("ALL CONSOLE BOOT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
