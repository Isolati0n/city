#!/usr/bin/env python3
"""Same city, three electrician spellings. C is TCB; others are twins."""
from __future__ import annotations

import os
import re
import subprocess
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STAGE = "/tmp/nw-init-run"
ZIG = "/tmp/zig/zig"


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, **kw)
    p.out = (p.stdout or b"").decode("utf-8", "replace")
    p.err = (p.stderr or b"").decode("utf-8", "replace")
    return p


def build():
    os.chdir(ROOT)
    subprocess.check_call(["make", "stage"])
    subprocess.check_call(
        ["gcc", "-O2", "-o", f"{STAGE}/elec-c", "electrician.c", "nwcheck.c"]
    )
    r = subprocess.run(
        [
            "rustc",
            "-O",
            "-o",
            f"{STAGE}/elec-rs",
            "electrician.rs",
        ],
        capture_output=True,
        text=True,
    )
    rust_ok = r.returncode == 0
    if not rust_ok:
        print("rust build failed:\n", r.stderr[-1500:])
    zig_ok = False
    if os.path.isfile(ZIG):
        z = subprocess.run(
            [
                ZIG,
                "build-exe",
                "electrician.zig",
                "nwcheck.c",
                "-lc",
                "-I.",
                "-OReleaseSafe",
                "--name",
                "elec-zig",
                "--cache-dir",
                "/tmp/zig-cache",
            ],
            capture_output=True,
            text=True,
        )
        if z.returncode == 0 and os.path.isfile(os.path.join(ROOT, "elec-zig")):
            subprocess.check_call(["cp", "-f", os.path.join(ROOT, "elec-zig"), f"{STAGE}/elec-zig"])
            zig_ok = True
        else:
            print("zig build failed:\n", (z.stderr or z.stdout)[-1500:])
    for name in ("elec-c", "elec-rs", "elec-zig"):
        p = f"{STAGE}/{name}"
        if os.path.isfile(p):
            os.chmod(p, 0o755)
    return rust_ok, zig_ok


def boot(elec, hold=700):
    os.replace(elec, f"{STAGE}/nw-electrician") if False else None
    subprocess.check_call(["cp", "-f", elec, f"{STAGE}/nw-electrician"])
    os.chmod(f"{STAGE}/nw-electrician", 0o755)
    t0 = time.perf_counter()
    p = run(
        [
            "unshare",
            "--pid",
            "--fork",
            "--mount-proc",
            "--",
            f"{STAGE}/nw-root",
            "--slot",
            f"{STAGE}/slots/A",
            "--hold-ms",
            str(hold),
        ]
    )
    dt = time.perf_counter() - t0
    out = p.out + p.err
    kits = re.findall(r"kit=(\d+)", out)
    filled = "kits filled" in out
    opened = "city open" in out
    return {
        "rc": p.returncode,
        "s": dt,
        "kits": kits,
        "filled": filled,
        "opened": opened,
        "inert": "inert" in out,
        "out": out,
    }


def main():
    rust_ok, zig_ok = build()
    langs = [("C", f"{STAGE}/elec-c")]
    if zig_ok:
        langs.append(("Zig", f"{STAGE}/elec-zig"))
    if rust_ok:
        langs.append(("Rust", f"{STAGE}/elec-rs"))

    print("== electrician bake-off (same plan, 4 houses) ==")
    print(f"{'lang':<8} {'rc':>4} {'ms':>8} {'kits':<12} filled open inert size")
    best = None
    for name, path in langs:
        sz = os.path.getsize(path)
        times = []
        last = None
        ok = True
        for _ in range(3):
            last = boot(path)
            times.append(last["s"])
            if last["rc"] != 0 or not last["filled"]:
                ok = False
                break
        ms = 1000 * (sum(times) / len(times))
        kits = ",".join(last["kits"]) if last else ""
        print(
            f"{name:<8} {last['rc'] if last else -1:>4} {ms:8.1f} {kits:<12} "
            f"{int(last['filled'])} {int(last['opened'])} {int(last['inert'])} {sz}"
        )
        if not ok:
            print("--- log ---")
            print(last["out"][-1200:] if last else "")
        if ok and (best is None or ms < best[1]):
            best = (name, ms)

    print()
    print("Go: not built. fork-without-exec is undefined in the Go runtime.")
    print("Python: not built. Same walk would be os.fork + GC in the TCB.")
    if best:
        print(f"fastest wall clock on this box: {best[0]} ({best[1]:.1f} ms city hold)")
        print("That number is almost all fork + hold-ms, not the compiler.")
    print("TCB remains C. Twins are evidence, not mayors.")


if __name__ == "__main__":
    main()
