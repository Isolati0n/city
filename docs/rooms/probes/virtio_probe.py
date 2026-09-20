#!/usr/bin/env python3
"""Does QEMU expose the virtio device status register outside the guest?

Addenda 5 and 6 of NW-UNCONSTRAINED-DESIGN name this as the one fact that
must be checked before district attestation rests on it. The virtio spec
documents the ACKNOWLEDGE -> DRIVER -> FEATURES_OK -> DRIVER_OK
progression; whether the *host* can read it was inferred from mechanism
and never verified.

Method: boot the ordinary 4-house image with a QMP socket, poll the
device status of virtio-blk from outside the guest, and record the value
at two moments -- before the guest kernel has probed the device, and
after PID 1 reports the city open. A constant value proves nothing; the
transition is the evidence.
"""
import json, os, socket, subprocess, time, sys

SOCK = "/tmp/qmp.sock"
LOG = "/tmp/virtio-boot.log"
for p in (SOCK, LOG):
    try: os.unlink(p)
    except FileNotFoundError: pass

qemu = subprocess.Popen([
    "qemu-system-x86_64", "-machine", "q35,accel=tcg", "-cpu", "max",
    "-m", "512M", "-nographic", "-no-reboot", "-display", "none",
    "-serial", f"file:{LOG}", "-monitor", "none",
    "-qmp", f"unix:{SOCK},server,nowait",
    "-kernel", "/tmp/kx/boot/vmlinuz-6.8.0-139-generic",
    "-initrd", "/tmp/bo/initrd.cpio",
    "-append", "console=ttyS0,115200n8 ignore_loglevel NW_ROOT=/dev/vda "
               "NW_ROOT_FSTYPE=ext4 NW_ESP=/dev/vdb NW_ESP_FSTYPE=vfat",
    "-drive", "file=/tmp/bo/root.img,if=virtio,format=raw,cache=writeback",
    "-drive", "file=/tmp/bo/esp.img,if=virtio,format=raw,cache=writeback",
])

class Qmp:
    def __init__(self, path):
        for _ in range(100):
            try:
                self.s = socket.socket(socket.AF_UNIX); self.s.connect(path); break
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.1)
        else:
            raise SystemExit("no QMP socket")
        self.f = self.s.makefile("rw")
        self.f.readline()                      # greeting
        self.cmd("qmp_capabilities")
    def cmd(self, name, **args):
        msg = {"execute": name}
        if args: msg["arguments"] = args
        self.f.write(json.dumps(msg) + "\n"); self.f.flush()
        while True:
            line = self.f.readline()
            if not line: raise SystemExit("QMP closed")
            d = json.loads(line)
            if "event" in d: continue
            return d

q = Qmp(SOCK)

print("=== does this QEMU have the virtio introspection commands? ===")
r = q.cmd("x-query-virtio")
if "error" in r:
    print("x-query-virtio:", r["error"]); qemu.kill(); sys.exit(0)
devs = r["return"]
print(json.dumps(devs, indent=2)[:600])

paths = [d["path"] for d in (devs if isinstance(devs, list) else devs.get("devices", []))]
blk = [p for p in paths if "blk" in p] or paths
if not blk:
    print("no virtio devices listed"); qemu.kill(); sys.exit(0)
target = blk[0]
print("\ntarget device:", target)

def status(path):
    r = q.cmd("x-query-virtio-status", path=path)
    if "error" in r: return {"error": r["error"]}
    ret = r["return"]
    keep = {k: v for k, v in ret.items()
            if "status" in k.lower() or k in ("name", "device-id")}
    return keep or ret

print("\n=== EARLY: sampled immediately, before the guest has probed ===")
print(json.dumps(status(target), indent=2))

print("\n=== waiting for the city to open ===")
opened = False
for i in range(150):
    time.sleep(2)
    try:
        t = open(LOG, "rb").read().decode("utf8", "replace")
    except FileNotFoundError:
        continue
    if "city open houses=" in t:
        opened = True
        print(f"  city open seen after ~{i*2}s")
        break
print("  opened:", opened)

print("\n=== LATE: sampled after the guest is up ===")
print(json.dumps(status(target), indent=2))

qemu.kill()
