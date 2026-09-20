#!/usr/bin/env python3
"""The case Addendum 5 actually names.

"A guest kernel that panics on its own initramfs leaves QEMU running
perfectly, so the record says the district is up while the machine inside
is at a panic screen."

The earlier probe showed empty-before-probe and DRIVER_OK-after-boot on a
HEALTHY guest. That establishes the field is not a constant. It does not
establish what the field says about a guest that came up far enough to
initialise a block driver and then died -- which is the only case where
attesting the wrapper and attesting the workload give different answers.

Method: boot the same disk with an initrd whose /init exits immediately.
The kernel probes virtio-blk during driver init, well before it execs
/init, so the driver reaches DRIVER_OK and the guest then panics. Sample
the status register from outside while the guest is at the panic screen.
"""
import json, os, socket, subprocess, time

SOCK = "/tmp/qmp2.sock"
LOG = "/tmp/panic-boot.log"
for p in (SOCK, LOG):
    try: os.unlink(p)
    except FileNotFoundError: pass

qemu = subprocess.Popen([
    "qemu-system-x86_64", "-machine", "q35,accel=tcg", "-cpu", "max",
    "-m", "512M", "-nographic", "-no-reboot", "-display", "none",
    "-serial", f"file:{LOG}", "-monitor", "none",
    "-qmp", f"unix:{SOCK},server,nowait",
    "-kernel", "/tmp/kx/boot/vmlinuz-6.8.0-139-generic",
    "-initrd", "/tmp/panic-initrd.cpio",
    "-append", "console=ttyS0,115200n8 ignore_loglevel",
    "-drive", "file=/tmp/bo/root.img,if=virtio,format=raw,cache=writeback",
])

class Qmp:
    def __init__(self, path):
        for _ in range(120):
            try:
                self.s = socket.socket(socket.AF_UNIX); self.s.connect(path); break
            except (FileNotFoundError, ConnectionRefusedError): time.sleep(0.1)
        else: raise SystemExit("no QMP socket")
        self.f = self.s.makefile("rw"); self.f.readline(); self.cmd("qmp_capabilities")
    def cmd(self, name, **a):
        m = {"execute": name}
        if a: m["arguments"] = a
        self.f.write(json.dumps(m) + "\n"); self.f.flush()
        while True:
            line = self.f.readline()
            if not line: raise SystemExit("QMP closed")
            d = json.loads(line)
            if "event" in d: continue
            return d

q = Qmp(SOCK)
dev = q.cmd("x-query-virtio")["return"][0]["path"]

def statuses():
    r = q.cmd("x-query-virtio-status", path=dev)
    if "error" in r: return ["ERROR: %s" % r["error"]]
    return r["return"].get("status", {}).get("statuses", [])

# Wait for the guest to reach its panic, read from the serial log.
panicked = False
for i in range(90):
    time.sleep(2)
    try: t = open(LOG, "rb").read().decode("utf8", "replace")
    except FileNotFoundError: continue
    if "Kernel panic" in t:
        panicked = True
        print(f"guest panicked after ~{i*2}s")
        break
print("panicked:", panicked)

for line in open(LOG, "rb").read().decode("utf8", "replace").splitlines():
    if "Kernel panic" in line or "Attempted to kill init" in line:
        print("  console:", line.strip()[:110])

print("\n=== is QEMU still running? ===")
print("  qemu poll:", qemu.poll(), "(None means still alive)")
print("  QMP query-status:", q.cmd("query-status")["return"].get("status"))

print("\n=== virtio-blk status register, guest at the panic screen ===")
for s in statuses() or ["(empty)"]:
    print("  " + s)

qemu.kill()
