#!/usr/bin/env python3
"""Put-together suite. Not in the TCB."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STAGE = os.environ.get("NW_STAGE", "/tmp/nw-init-run")

def _blob_str(name):
    """A string #define from blob.h, quotes stripped."""
    for line in open(os.path.join(ROOT, "blob.h")):
        f = line.split()
        if len(f) >= 3 and f[0] == "#define" and f[1] == name:
            return f[2].strip('"')
    raise SystemExit(f"blob.h has no {name}")


def _brick_dir():
    return _blob_str("NW_BRICK_DIR")


def _layer_dir():
    """NW_LAYER_DIR from blob.h -- the same constant nw-sup composes from
    and tools/stage-layers.py creates under. Read, never spelled."""
    return _blob_str("NW_LAYER_DIR")


def _brick_suffix():
    """NW_BRICK_SUFFIX from blob.h -- the same constant mkbrick.py reads.

    Through _blob_str, which is the same parse _brick_dir uses. It was an
    open-coded copy of that loop for half a day: two readers of blob.h in
    one file, differing only in the name they look for, which is the shape
    that lets one be fixed and the other left."""
    return _blob_str("NW_BRICK_SUFFIX")


def _stage_limit():
    """How long NW_STAGE may be.

    PHASE 3 REMOVED THE REASON THIS EXISTED. The limit was the slack left
    in `brick[96]` after "/nw/bricks/" and a 64-character hash, because
    make_brick built "{STAGE}/nw/bricks/{hash}" and a long stage overflowed
    the field -- surfacing at bake time as `brick= too long`, which names
    brick= and says nothing about the stage. The plan carries 32 raw bytes
    now and no stage path reaches it at all.

    What still bounds the stage is `exec_path[128]`, which the suite fills
    with "{STAGE}/nw/bin/<fixture>". Derived from that instead, with the
    longest fixture name the tree actually has rather than a guess.

    (The old derivation also subtracted the hash and not ".img", coming out
    four characters too generous until 2026-09-12. Both the arithmetic and
    the field it was about are gone; this is a different bound.)

    THE FALLBACK WAS THE SAME DEFECT AGAIN, in the replacement. When the
    stage does not exist yet there is nothing to list, and a literal 16
    stood in under a comment saying it "is longer than any fixture name in
    the tree today, so it errs toward refusing a stage that would have
    worked". The longest is `unit-lastwordsmany`, 18 -- so it was two
    characters too GENEROUS, in the direction the sentence promised it
    could not be, and a 103-character stage passed the guard and then built
    a 129-byte exec_path against NW_PATH_LEN 128: the exact bake-time
    failure the guard exists to pre-empt. `fd-auditor` and `tcb-review`.

    So the names come from the Makefile's own `cp` list, which is what
    `make stage` copies -- one source of truth, staged or not. It refuses
    rather than guessing if that list cannot be read, because a guess here
    is what produced two wrong bounds in a row."""
    plen = None
    for line in open(os.path.join(ROOT, "blob.h")):
        f = line.split()
        if len(f) >= 3 and f[0] == "#define" and f[1] == "NW_PATH_LEN":
            plen = int(f[2])
    if plen is None:
        raise SystemExit("blob.h has no NW_PATH_LEN")
    # BIN is defined below this guard, so the path is spelled out here.
    binp = os.path.join(STAGE, "nw", "bin")
    if os.path.isdir(binp) and os.listdir(binp):
        longest = max(len(n) for n in os.listdir(binp))
    else:
        longest = _staged_names_from_makefile()
    return plen - len("/nw/bin/") - longest - 1


def _staged_names_from_makefile():
    """Length of the longest name `make stage` copies into nw/bin.

    Read off the Makefile's `cp -f ... $(STAGE)/nw/bin/` continuation, so a
    fixture added there raises the bound automatically and cannot be missed
    by someone editing a number here."""
    src = open(os.path.join(ROOT, "Makefile")).read()
    m = re.search(r"\n\tcp -f((?:[^\n]*\\\n)*[^\n]*)\$\(STAGE\)/nw/bin/",
                  src)
    if not m:
        raise SystemExit(
            "tests/run.py: cannot find the Makefile's nw/bin cp list, which "
            "is where the stage-length bound comes from. Do not replace this "
            "with a literal -- the two literals that stood here before were "
            "each wrong in the generous direction.")
    names = [t for t in m.group(1).replace("\\", " ").split() if t]
    if not names:
        raise SystemExit("tests/run.py: the Makefile nw/bin cp list is empty")
    return max(len(n) for n in names)


if len(STAGE) > _stage_limit():
    raise SystemExit(
        f"NW_STAGE is {len(STAGE)} characters and the limit is "
        f"{_stage_limit()} (derived from NW_PATH_LEN in blob.h): {STAGE}\n"
        f"A longer stage makes every staged exec_path overflow "
        f"exec_path[NW_PATH_LEN], and the suite fails at bake time naming "
        f"exec path, not the stage. (This message named brick[] until "
        f"2026-09-12, a field that has held no path since phase 3 -- the "
        f"docstring above was rewritten and the string under it was not.)")
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

def sha256_of(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# PID 1's shutdown grace, read from the source rather than written down:
# test_shutdown_does_not_restart can only observe a restart that lands
# inside it, and that margin belongs in the ok line.
def pid1_grace_ms():
    """Read PID 1's shutdown grace out of pid1.c. No fallback, deliberately.

    This derived nothing until 2026-09-11. It looked for `NW_GRACE_MS` and
    for the word "grace"; pid1.c contains neither -- the grace is the bare
    literal in `while (now_ms() - t0 < 400)` -- so both patterns missed on
    every run and the function returned its fallback, which was written as
    400 and was therefore right. `control` set pid1.c's loop to 1500 and
    the ok line still said 400ms, green: a derivation-shaped sentence with
    nothing deriving, which is this project's characteristic failure
    appearing inside a repair for it.

    So: match the loop that actually bounds the grace, and raise if it is
    not found. A default that silently equals the truth is exactly how the
    last one survived -- when this stops matching, it must stop the suite,
    not guess. (The regex is the reason this cannot live in pid1.c as a
    #define: that file belongs to another agent this week.)
    """
    import re
    src = open(os.path.join(ROOT, "pid1.c")).read()
    m = re.search(r"NW_GRACE_MS\s+(\d+)", src) or \
        re.search(r"now_ms\(\)\s*-\s*t0\s*<\s*(\d+)", src)
    if not m:
        raise SystemExit(
            "tests/run.py: cannot find PID 1's shutdown grace in pid1.c. "
            "It was `while (now_ms() - t0 < 400)`. If the shutdown loop "
            "changed shape, fix this pattern -- do NOT reintroduce a "
            "default, which is how this went undetected for its whole life.")
    return int(m.group(1))


PID1_GRACE_MS = pid1_grace_ms()

SKIPPED = []


class Unavailable(Exception):
    """This machine cannot exercise the test that is running.

    Raised from a HELPER rather than checked at each call site, and that is
    the whole point. `erofs_available()` existed and three of the four
    `make_brick()` callers did not consult it, so on a machine without
    `mkfs.erofs` the suite crashed instead of skipping -- while the
    environment block printed the correct warning two screens above. A guard
    that every caller must remember is the "correct and routed around" shape
    in CLAUDE.md: it worked perfectly and was simply not reached.

    main() turns this into a named skip using the TEST'S OWN name, which
    also makes a stray skip name structurally impossible: the name is
    derived from the function rather than typed.
    """

    def __init__(self, why):
        super().__init__(why)
        self.why = why


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
    # test_brick_image_reproducible skips on exactly this, and a skip
    # condition that is not in the environment block is the gap that let
    # the Landlock lid run green for its whole life without executing.
    # `claims` noticed it was missing while erofs was listed as mountable
    # -- two different questions, and only one of them was being reported.
    erofs_mk = run(["sh", "-c", "command -v mkfs.erofs"]).returncode == 0
    print(f"  mkfs.erofs : {'present' if erofs_mk else 'ABSENT'}"
          f"{'' if erofs_mk else '  -- brick-image tests will SKIP, not pass'}")
    print()


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, **kw)
    p.out = (p.stdout or b"").decode("utf-8", "replace")
    p.err = (p.stderr or b"").decode("utf-8", "replace")
    return p


def stage_layers(blob):
    """Create the writable layers a plan declares, through the one tool
    that creates them.

    The harness stands in for a stager that does not exist yet, and it
    calls `tools/stage-layers.py` rather than making the directories
    itself, so there is ONE creator. nw-sup deliberately makes none: a
    supervisor that mkdir'd a missing layer would turn "nothing staged
    this plan" into "the house silently got an empty layer", which is the
    orphaned-data failure the layer-id exists to prevent.

    No root prefix: NW_LAYER_DIR is absolute and nw-sup composes it that
    way, so in the lab it lands on the host root exactly as /nw/mnt and
    /nw/bricks do."""
    if not os.path.exists(blob + ".layers"):
        return
    # RESET FIRST, and this is not hygiene -- it is what makes the suite
    # deterministic. A layer is DURABLE by design and keyed by an id, so
    # without this a test inherits whatever a previous suite run left
    # under the same id. Measured the hard way: a buggy probe wrote one
    # byte over /id, that write copied up into the layer, and every
    # later run of test_brick_is_a_root read `id=xrick-one` from a
    # correct brick -- the masking failure runtime.md records, arriving
    # inside the harness before anyone hit it in production.
    #
    # No test wants cross-RUN persistence. layer-survives-a-restart is
    # about surviving a restart WITHIN one boot, and it hand-rolled this
    # same rmtree for itself; one mechanism instead, so a new brick test
    # cannot forget it.
    for lid in (l.strip() for l in open(blob + ".layers") if l.strip()):
        shutil.rmtree(os.path.join(_layer_dir(), lid), ignore_errors=True)
    r = run(["python3", os.path.join(ROOT, "tools", "stage-layers.py"),
             blob, "--quiet"])
    expect(r.returncode == 0, f"stage-layers failed\n{r.out}{r.err}")


def boot(slot=None, plan=None, extra=None, hold=800):
    # Staged here so no test can forget it, and so the suite exercises the
    # production ordering: layers exist BEFORE the boot that needs them.
    if plan:
        stage_layers(plan)
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


def _blank(span):
    """Spaces for a span, KEEPING its newlines so line numbers survive."""
    return "".join(ch if ch == "\n" else " " for ch in span)


def strip_c_comments(src, dashdash=False):
    """Blank out comments and string literals, keeping line structure.

    A source-level assertion that matches raw text is an assertion about
    prose as well as code. `control` turned the suite red with one added
    comment containing "at fork time (" and another containing
    "deaths = 0" -- both describing history, neither changing behaviour,
    and one of them is the re-filing CLAUDE.md explicitly asks for.

    LINE STRUCTURE IS A CONTRACT, not a convenience. The caller at the
    spec test matches command lines in the blanked text and then edits
    the RAW text by those line numbers, so a branch that drops a newline
    silently shifts every index after it. The block-comment branch
    always preserved newlines; the quote branch did not, and `control`
    collapsed the file with one apostrophe -- `Alloy's` in a line
    comment -- which made the probe stripper delete the wrong raw lines,
    unterminate a comment, and report "the must-fail probe for Sealed
    did not solve". A legal spec, a red suite, and a message naming the
    wrong file. Every branch goes through _blank now.

    `dashdash` adds Alloy's `--` line comment. It is off by default
    because `--` is a decrement in C, and this function also reads
    nwsup.c. Without it, a `/*` written inside an Alloy `--` comment
    blanked everything to the next `*/` -- 33 lines in `control`'s case,
    hiding two of three commands. `control`, both.
    """
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(_blank(src[i:j]))
            i = j
        elif (c == "/" and i + 1 < n and src[i + 1] == "/") or \
             (dashdash and c == "-" and i + 1 < n and src[i + 1] == "-"):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(_blank(src[i:j]))
            i = j
        elif c in "\"'":
            j, q = i + 1, c
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(_blank(src[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def nested_init(unshare_pid):
    """PID 1 of the namespace `unshare --pid --fork` made, or None."""
    try:
        kids = open(
            f"/proc/{unshare_pid}/task/{unshare_pid}/children").read().split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    return int(kids[0]) if kids else None


def reap_nested(p):
    """Tear down an `unshare --pid --fork` child, inner process first.

    KILLING THE PARENT DOES NOT KILL WHAT IT FORKED. `p.kill()` on the
    `unshare` process leaves the nested PID 1 running, reparented to init
    and still holding the loop-device mounts inside its own namespace --
    so the `finally` below that runs `losetup -d` silently fails to
    detach, the next run's `rm -rf lab` deletes the image out from under
    a live mount, and the machine accumulates
    `/dev/loopN: ... (deleted)` entries. Found by fd-auditor, with four
    of them already on this machine from earlier failed runs, and three
    orphaned `nw-root`s at ppid 1.

    Neither shortcut works: `unshare --kill-child` does not fire for a
    pid-namespace init, and `os.killpg` does not reach it either -- both
    measured. Signalling the nested init directly does work.

    Called from a `finally` so no branch can skip it, including the
    timeout path, the path where the child is already gone, and the path
    where the test is about to raise.
    """
    k = nested_init(p.pid)
    if k is not None:
        try:
            os.kill(k, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if p.poll() is None:
        p.kill()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


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
    # A distinctive substring of the D18 message, not the key echoed back:
    # deleting the `elif k == "window"` branch entirely falls through to
    # the generic "unknown key window=" and `"window=" in out` is still
    # satisfied, so the reasoning could be deleted from the baker and
    # nothing would notice. `control` ran that mutant.
    expect("hard total" in (p.out + p.err),
           f"window= was refused, but not by the D18 branch -- the message "
           f"does not explain why\n{p.out}{p.err}")
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
        for d in ("nw/bin", _brick_dir().lstrip("/"),
                  _layer_dir().lstrip("/"), "efi",
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

        def boot_dawn(expect_open=True, timeout=25):
            """Boot dawn for real and close it the way production closes.

            This used to pass `NW_HOLD_MS=800` in the environment and let
            dawn forward `--hold-ms` to PID 1, so the city shut itself
            down on a timer and the test read the exit status. That timer
            was a production control surface: NW_HOLD_MS reached PID 1
            through the KERNEL COMMAND LINE, and putting NW_HOLD_MS=800
            there made a real unattended boot power itself off. dawn no
            longer reads it, so this helper has to close the city itself.

            SIGTERM to PID 1 of the nested pid namespace is the production
            close path -- the same signal `shutdown_city` exists to
            handle. `unshare --fork` does not forward signals, so the
            child is found through /proc rather than signalled by proxy.

            The timeout is a failure, not a close: without `city_closed`
            paired against a line proving TERM was delivered, a hang that
            got killed here would present exactly like a clean shutdown.
            """
            cmd = ["unshare", "--mount", "--pid", "--fork", "--",
                   "env", f"NW_ROOT={rootdev}", "NW_ROOT_FSTYPE=ext4",
                   f"NW_ESP={espdev}", f"NW_ESP_FSTYPE={esp_fs}",
                   f"{BIN}/nw-dawn"]
            log = f"{lab}/console.log"
            with open(log, "wb") as f:
                p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT)
                try:
                    deadline = time.time() + timeout
                    signalled = False
                    argv = []
                    t_term = []
                    while time.time() < deadline:
                        if p.poll() is not None:
                            break
                        if expect_open and not signalled:
                            try:
                                seen = open(log, "rb").read().decode(
                                    "utf-8", "replace")
                            except FileNotFoundError:
                                seen = ""
                            if "city open" in seen:
                                k = nested_init(p.pid)
                                if k:
                                    # What dawn actually exec'd. A timer on
                                    # PRODUCTION argv is the defect finding 3
                                    # removed, and it is invisible to a log
                                    # snapshot if it is longer than the
                                    # window: `control` put `--hold-ms 3000`
                                    # here and the whole suite stayed green
                                    # on a machine that powers itself off
                                    # three seconds after boot. Read the
                                    # argv instead of timing it.
                                    argv.append(open(
                                        f"/proc/{k}/cmdline", "rb"
                                    ).read().decode().split("\0"))
                                    os.kill(k, signal.SIGTERM)
                                    signalled = True
                                    t_term.append(time.time())
                        time.sleep(0.05)
                    else:
                        out = open(log, "rb").read().decode(
                            "utf-8", "replace")
                        raise SystemExit(
                            f"FAIL: dawn boot did not finish within "
                            f"{timeout}s (city open seen: "
                            f"{'city open' in out}, TERM sent: {signalled}). "
                            f"A hang is not a shutdown.\n{out[-1500:]}")
                finally:
                    reap_nested(p)
            after = (time.time() - t_term[0]) if t_term else None
            return (p.returncode,
                    open(log, "rb").read().decode("utf-8", "replace"),
                    signalled, argv[0] if argv else None, after)

        rc, out, signalled, argv, after = boot_dawn()
        expect(city_closed(rc, out), f"dawn boot rc={rc}\n{out}")
        # Pair the close with proof THIS TEST's TERM is what ended it.
        #
        # `shutdown TERM houses` alone does not establish that: the line is
        # printed by shutdown_city however it was entered, so a PID 1 that
        # ignores TERM and closes on a timer prints it too. `control` built
        # exactly that and the whole suite stayed green, with `signalled`
        # computed here and asserted nowhere. The three assertions below
        # are what make this a claim about signal handling: we sent it, the
        # city was still running when we did, and it closed afterwards.
        expect(signalled,
               f"this test never sent SIGTERM -- the city ended on its own, "
               f"so nothing here is evidence that PID 1 answers a signal"
               f"\n{out}")
        expect("shutdown TERM houses" in out,
               f"the city closed without shutdown_city having been entered "
               f"by signal -- something other than the production close "
               f"path ended this boot\n{out}")
        # And dawn must not have handed PID 1 a deadline. A timer longer
        # than any window this test watches is invisible to the log; the
        # argv is not.
        expect(argv is not None and "--hold-ms" not in argv,
               f"dawn exec'd nw-root with a deadline on production argv: "
               f"{argv}. That is a machine which powers itself off without "
               f"being asked, which is the defect NW_HOLD_MS's removal was "
               f"for -- reached by a different route.")
        # And it must close BECAUSE of the signal, which means promptly
        # after it. `control` compiled a 3-second deadline into main() as
        # the default for hold_ms and made PID 1 ignore TERM: argv is
        # clean, we did send a TERM, the city did close, and every
        # assertion above passed. What separates that from the real thing
        # is that its close came 2.9s after the signal rather than one
        # grace period.
        #
        # The bound is derived from pid1.c's own grace, not picked: four
        # times it. A real close is grace + reboot, measured at ~0.4s.
        # RESIDUAL, stated because a bound is not a proof: a compiled-in
        # deadline SHORTER than this bound still passes here, and only the
        # NW_HOLD_MS boot below -- which signals nothing at all -- bounds
        # that case.
        bound = 4 * PID1_GRACE_MS / 1000.0
        expect(after is not None and after < bound,
               f"the city took {after:.2f}s to close after SIGTERM, bound is "
               f"{bound:.2f}s (4x pid1.c's {PID1_GRACE_MS}ms grace). It did "
               f"not close because of the signal -- something else ended it "
               f"and the TERM was ignored.\n{out}")
        expect("mounted /sysroot" in out, f"root not mounted\n{out}")
        expect("mounted /sysroot/efi" in out, f"esp not mounted\n{out}")
        # WHICH branch, not just "a pivot happened". `"pivoted" in out`
        # matches `pivoted MS_MOVE` too, so `control` disabled pivot_root
        # entirely, sent dawn down the fallback, and this stayed green --
        # while the message said "no pivot_root". The harness is not on
        # rootfs, so pivot_root is the branch it must take; production is
        # on rootfs and takes the other one, which harness.md records as
        # having no lab coverage. Saying which ran is the rule for a
        # branch the environment picks.
        expect("pivoted pivot_root" in out,
               f"dawn did not take the pivot_root branch. If this says "
               f"`pivoted MS_MOVE`, the lab has silently started "
               f"exercising the fallback instead -- that is a finding to "
               f"report, not a baseline to accept.\n{out}")
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
        rc, out, signalled, _, _ = boot_dawn()
        expect(city_closed(rc, out), f"slot B rc={rc}\n{out}")
        expect(signalled, f"slot B ended without this test signalling it\n{out}")
        expect("shutdown TERM houses" in out,
               f"slot B closed without the production shutdown path\n{out}")
        expect("live slot /efi/slots/B" in out, f"B not selected\n{out}")
        expect("houses=1" in out, f"slot B should hold 1 unit\n{out}")

        # A name that would escape the slots directory must be refused.
        subprocess.run(["mount", espdev, f"{lab}/me"], check=True)
        open(f"{lab}/me/slots/current", "w").write("../../etc\n")
        subprocess.run(["sync"], check=True)
        subprocess.run(["umount", f"{lab}/me"], check=True)
        rc, out, _, _, _ = boot_dawn(expect_open=False)
        expect("HALT: slots/current" in out, f"traversal not refused\n{out}")

        # More than one shape, because one string is not the check.
        # `control` replaced the [A-Za-z0-9_-] loop with a length bound --
        # `if (n > 2) return -1;` -- and this test stayed green, because
        # `../../etc` is nine bytes. A bare `..` is two, so it passes any
        # length bound and is refused only by a charset check. Measured on
        # that mutant: `live slot /tmp/.../slots/..` and the city booted.
        for bad in ("..", "A/../../etc", "a b"):
            subprocess.run(["mount", espdev, f"{lab}/me"], check=True)
            open(f"{lab}/me/slots/current", "w").write(bad + "\n")
            subprocess.run(["sync"], check=True)
            subprocess.run(["umount", f"{lab}/me"], check=True)
            rc, out, _, _, _ = boot_dawn(expect_open=False)
            expect("HALT: slots/current" in out,
                   f"slots/current = {bad!r} was not refused -- a name that "
                   f"escapes the slots directory must be rejected for its "
                   f"CHARSET, and a check that only refuses long names or "
                   f"one literal is not that\n{out}")

        # NW_HOLD_MS MUST BE INERT IN PRODUCTION. This is the property the
        # variable's removal was for, and dropping it from the boot above
        # does not assert it -- a test that stops setting a variable cannot
        # notice it working again. Measured: re-adding the forwarding to
        # dawn.c left this whole test green until this case existed.
        #
        # NW_HOLD_MS reached PID 1 through the kernel command line, so a
        # bootloader could make a real unattended machine power itself off
        # mid-boot. The city must open and STAY open with it set.
        subprocess.run(["mount", espdev, f"{lab}/me"], check=True)
        open(f"{lab}/me/slots/current", "w").write("A\n")
        subprocess.run(["sync"], check=True)
        subprocess.run(["umount", f"{lab}/me"], check=True)
        cmd = ["unshare", "--mount", "--pid", "--fork", "--",
               "env", f"NW_ROOT={rootdev}", "NW_ROOT_FSTYPE=ext4",
               f"NW_ESP={espdev}", f"NW_ESP_FSTYPE={esp_fs}",
               "NW_HOLD_MS=400", f"{BIN}/nw-dawn"]
        log = f"{lab}/inert.log"
        with open(log, "wb") as f:
            p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT)
            # Four times the timer it must be ignoring. THE SNAPSHOT IS
            # TAKEN BEFORE THE TERM, and that ordering is the test: the
            # first version of this read the log after signalling and
            # asserted `closed` was absent, which fails against a correct
            # tree because the signal is what closes it. Assert on what
            # happened while nothing was being asked of it.
            try:
                p.wait(timeout=1.6)
                exited_on_its_own = True
            except subprocess.TimeoutExpired:
                exited_on_its_own = False
            try:
                snap = open(log, "rb").read().decode("utf-8", "replace")
                if not exited_on_its_own:
                    k = nested_init(p.pid)
                    if k is not None:
                        os.kill(k, signal.SIGTERM)
                    try:
                        p.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                # Unconditionally, and inner-first. The single-shot
                # `if kids:` that used to be here had no else: an empty
                # read fell through to killing the parent only, orphaning
                # the nested init -- while this test still PASSED, because
                # both assertions below were already evaluated from `snap`.
                # A cleanup that leaks on a path the test calls green is
                # the unpaired-absence shape wearing a teardown costume.
                reap_nested(p)
        # Paired: the city must have OPENED, or "did not close" is also
        # satisfied by a boot that never got anywhere.
        expect("city open" in snap,
               f"the NW_HOLD_MS boot never opened a city within 1.6s, so its "
               f"not having closed proves nothing\n{snap}")
        expect(not exited_on_its_own and
               "closed" not in snap.split("city open", 1)[1],
               f"NW_HOLD_MS=400 in the environment shut the city down within "
               f"1.6s and nothing had signalled it: the lab timer is "
               f"reachable from the kernel command line again, which is how "
               f"an unattended boot powers itself off\n{snap}")
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
    # The house must actually DIE, not merely receive TERM. A restart can
    # only follow an exit, so asserting only that the handler ran leaves
    # the test green against a fixture whose handler prints and keeps
    # looping -- `control` built exactly that and watched this test pass
    # with the branch it exists for deleted.
    expect("exiting cleanly after TERM" in out,
           f"the house took TERM but never exited, so nothing could have "
           f"restarted and this test proved nothing\n{out[-1200:]}")
    expect("SIGTERM handler ran" in out, f"handler never ran\n{out}")
    expect("exiting cleanly after TERM" in out, f"no clean exit\n{out}")
    expect("houses_reaped=1" in out, f"house was killed, not reaped\n{out}")
    print("ok term-signal")


def test_last_words_survive_group_term():
    """A house's FINAL output must survive shutdown, including a shutdown
    that arrives as a signal to the whole process group.

    THIS IS A PRECONDITION OF A DECISION, not a nicety. The restart budget
    is a hard total (CLAUDE.md invariant 4), so a house that exhausts it
    stays dead until reboot -- accepted only because the death is visible.
    Discard the lines a house writes on its way out and an operator gets a
    black screen with no explanation, which is the property that made a
    hard total unsafe in the first place.

    The regression: `spawn_logger` unblocks TERM/INT so that a future drain
    pass can TERM the loggers rather than sitting pending forever (D11's
    shape). That is right, and it also made every logger die on a
    GROUP-directed TERM, where the default action is terminate -- before it
    had drained the pipe. Measured on this machine before the fix, six runs
    each: TERM to PID 1 alone relayed 5 of 5; the same TERM to the process
    group relayed 0 of 5. The house wrote them all either way; its reader
    was gone.

    Both directions are asserted here, in one boot each, because the pair
    is the test: the PID-1 case alone passes against a tree where nothing
    works at shutdown, and the group case alone cannot distinguish "the
    lines survived" from "the city never started".
    """
    lw = f"{BIN}/unit-lastwords"
    city = f"{WORK}/lastwords.city"
    open(city, "w").write(f"house lw {lw} kind=longrun lids=none\n")
    blob = f"{WORK}/lastwords.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")

    def shutdown_by(mode):
        """Boot, wait until the house is up, then TERM it one of two ways.

        start_new_session=True is NOT tidiness. Without it the city shares
        THIS PROCESS'S group, and the group-directed TERM below kills the
        test runner -- measured, exit 143, while writing the reproduction.
        The city gets its own session so the signal reaches the city and
        nothing else.

        The TERM goes to the NESTED PID 1, never to `unshare`: signalling
        the parent leaves the namespace's init running and the city never
        shuts down at all, which reads as "no lines relayed" and is the
        wrong answer for the right reason. Both mistakes were made while
        building this test.
        """
        p = subprocess.Popen(
            ["unshare", "--pid", "--fork", "--mount-proc", "--",
             f"{BIN}/nw-root", blob],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=True)
        init = None
        try:
            os.set_blocking(p.stdout.fileno(), False)
            buf = b""
            t0 = time.time()
            while b"waiting for TERM" not in buf and time.time() - t0 < 20:
                try:
                    buf += p.stdout.read() or b""
                except Exception:
                    pass
                time.sleep(0.02)
            init = nested_init(p.pid)
            expect(init is not None,
                   f"no nested PID 1; the city never booted\n"
                   f"{buf.decode('utf-8', 'replace')}")
            time.sleep(0.3)
            if mode == "group":
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            else:
                os.kill(init, signal.SIGTERM)
            t0 = time.time()
            while time.time() - t0 < 6:
                try:
                    c = p.stdout.read()
                    if c:
                        buf += c
                except Exception:
                    pass
                time.sleep(0.02)
            return buf.decode("utf-8", "replace")
        finally:
            # reap_nested, NOT a hand-rolled kill pair. On the path where
            # nested_init returns None this rolled its own teardown,
            # killed only the `unshare` parent, and left PID 1, its
            # logger and the supervisor alive at ppid 1 with hold_ms=0
            # -- i.e. forever. reap_nested's own docstring is the
            # sentence that says why: KILLING THE PARENT DOES NOT KILL
            # WHAT IT FORKED. `control` forced the path and caught three
            # strays, and found a live one on this machine from the
            # session that wrote this test.
            p.stdout.close()
            reap_nested(p)

    # THE SIZE IS THE TEST. Five lines is 320 bytes and survived an
    # unconditional SIGKILL of the loggers by luck of scheduling;
    # `tcb-review` took it to 2500 lines (~160 KiB) and three runs
    # relayed 2500, 2433 and 2451, losing a CONTIGUOUS TAIL -- the end
    # of the output, which is the part that says why the machine is
    # going down. A property pinned only at the size where it happens to
    # hold is the characteristic failure with a test attached.
    for label, binary, lines in (("small", "unit-lastwords", 5),
                                 ("many", "unit-lastwordsmany", 2500)):
      open(city, "w").write(f"house lw {BIN}/{binary} kind=longrun lids=none\n")
      b = run(["python3", CC, "--city", city, "--out", blob])
      expect(b.returncode == 0, f"bake {label}\n{b.out}{b.err}")
      for mode in ("init", "group"):
        out = shutdown_by(mode)
        # PAIRED. "all five lines are present" is satisfied by the drain
        # working AND by a fixture that never ran at all, and those are
        # opposite outcomes. The house announcing itself is what makes the
        # count below a claim about the drain.
        expect("waiting for TERM" in out,
               f"[{label}/{mode}] the house never started, so the line "
               f"count below would be about nothing\n{out[-1200:]}")
        # RECONSTITUTE THE BYTE STREAM BEFORE COUNTING. The logger
        # appends a newline when a read does not end in one and prefixes
        # each chunk, so above one chunk a line arrives split mid-token
        # and a naive count reads as loss. `tcb-review`'s first pass
        # reported 185 of 200 for exactly that reason and was wrong;
        # padding makes the split impossible here, and stripping makes
        # the assertion independent of that still being true.
        flat = out.replace("[lw] ", "").replace("\n", "")
        got = set(int(m) for m in
                  re.findall(rf"\[lastwords\] bye (\d+) of {lines}", flat))
        missing = sorted(set(range(1, lines + 1)) - got)
        expect(not missing,
               f"[{label}/{mode}] the house wrote {lines} final lines and "
               f"{len(missing)} never reached the console (first missing "
               f"{missing[0] if missing else None}, "
               f"contiguous tail: {missing == list(range(missing[0], lines + 1)) if missing else False}). "
               f"A house whose death count is a hard total stays down "
               f"until reboot; that is only safe while the death is "
               f"visible, and a lost TAIL is the part that says "
               f"why.\n{out[-1500:]}")
    print("ok last-words-survive (TERM to PID 1 and to the whole group, "
          "at 5 lines and at 2500 -- ~160 KiB, more than the log pipe "
          "holds, so the loggers must drain and not be killed mid-pipe)")


def test_orphans_across_restarts():
    """Orphan reaping, driven through a restart cycle for the first time.

    PID 1 reaps with `waitpid(-1)` and calls anything that is neither a
    known house nor a known logger an orphan. Until this test nothing in
    the suite ever produced one: `test_happy` asserts `orphans=0`, which
    is the happy path, so the counter and the branch that increments it
    had never been exercised. `.claude/rules/runtime.md` carried that as
    a known-open gap; this is its subject.

    `unit-orphan` forks three children that outlive it and exits nonzero,
    so `nw-sup` restarts it and the next run orphans again. The children
    sleep first: a child that has already exited when its parent dies is
    reaped by the kernel through the parent and never reaches PID 1.

    TWO CASES, because the second is the one that was undefined.
    """
    orph = f"{BIN}/unit-orphan"
    mark = "/tmp/nw-orphan.mark"
    city = f"{WORK}/orphan.city"
    open(city, "w").write(
        f"house orph {orph} kind=longrun budget=3 lids=none\n")
    blob = f"{WORK}/orphan.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")

    # CASE A: the children die while the city is up. Every orphan is
    # reaped, and the count is the one the fixture created -- 4 runs
    # (budget=3 allows three restarts) x 3 children.
    for f in (mark,):
        try:
            os.unlink(f)
        except FileNotFoundError:
            pass
    rc, out = boot(plan=blob, hold=2500)
    expect(city_closed(rc, out), f"orphan-A rc={rc}\n{out[-1500:]}")
    # PAIRED, AND ON THE EFFECT. "orphans=12" is a claim about reaping
    # only if the orphans were actually made. The first version of this
    # counted "leaving 3 behind" -- a line the fixture prints whether or
    # not the fork succeeded -- and `control` neutered the fork loop and
    # watched this fail with "the fixture left 12 behind", naming a
    # reaping defect for a fixture that made nothing. Assert on the
    # effect, not the announcement: `child=N pid=` is printed once per
    # fork that returned a pid.
    runs = out.count("leaving")
    forked = len(re.findall(r"\[orphan\] run=\d+ child=\d+ pid=\d+", out))
    expect(runs == 4,
           f"the fixture ran {runs} times, expected 4 (one start plus "
           f"budget=3 restarts); the orphan count below would be about a "
           f"different number of orphans\n{out[-1500:]}")
    expect(forked == runs * 3,
           f"the fixture reported {forked} successful forks across "
           f"{runs} runs, expected {runs * 3}. Nothing below is about "
           f"reaping until the orphans exist.\n{out[-1500:]}")
    # PROMPT, not merely eventual. HISTORY 48 states "reaped promptly"
    # as a finding and nothing tested it: `control` disabled the SIGCHLD
    # reap for the whole life of the city, so twelve zombies were held
    # until the final sweep at shutdown, and this test passed
    # identically. The assertion's own message says "a zombie held for
    # the life of the machine, and at scale that is pids" -- so assert
    # it. An orphan line before the shutdown line is that claim.
    #
    # SCOPE: this detects "nothing reaped during the city's life", not
    # "the SIGCHLD branch is gone". There are TWO reap sites in the main
    # loop -- the SIGCHLD branch and the poll-timeout branch -- and
    # either one keeps reaping prompt, so disabling only the first
    # leaves this green. Verified both ways. Do not read a pass here as
    # covering the SIGCHLD path specifically.
    shut = out.find("shutdown TERM houses")
    first_orphan = out.find("orphan pid=")
    expect(first_orphan != -1 and shut != -1 and first_orphan < shut,
           f"no orphan was reaped before shutdown began, so they were "
           f"swept at the end rather than as they died. A zombie held "
           f"for the life of the machine is a pid held, and at scale "
           f"that is the resource that runs out.\n{out[-1500:]}")
    # And the marker has to earn its keep: runs 1..4, each exactly once.
    # `control` deleted the marker logic and planted a stale marker, and
    # the test passed three times each way, because nothing read the run
    # number the marker exists to produce.
    for k in range(1, 5):
        got = len(re.findall(rf"\[orphan\] run={k} leaving", out))
        expect(got == 1,
               f"run={k} appears {got} times, expected exactly once. "
               f"Either a restart was missed or /tmp/nw-orphan.mark was "
               f"stale -- which used to be silent, because no assertion "
               f"read the run number.\n{out[-1500:]}")
    m = re.search(r"orphans=(\d+)", out)
    expect(m and int(m.group(1)) == forked,
           f"PID 1 reaped {m.group(1) if m else '?'} orphans; the fixture "
           f"left {forked} behind. An unreaped orphan is a zombie held "
           f"for the life of the machine, and at scale that is pids.\n"
           f"{out[-1500:]}")

    # CASE B: the children are still alive when shutdown starts. PID 1
    # does NOT wait for them -- shutdown is bounded by the grace period,
    # and waiting on an orphan is unbounded by construction. They die
    # with the machine at reboot.
    #
    # This behaviour was "undefined -- decide it explicitly rather than
    # letting the race pick" in runtime.md. It is decided here, and the
    # decision is *do not wait*: the alternative is a shutdown a stuck
    # orphan can hang forever, which is the class this project refuses.
    #
    # Measured at the boundary, three runs per rung: children dying
    # before the hold expires give 12 every time, children dying at or
    # after it give 0 every time. A sharp cutoff, not a flaky race.
    # A SLOWER CHILD, so "did shutdown wait?" is separable by a margin
    # no scheduler noise can close. With the 400ms child above, a
    # genuinely blocking drain closed in 0.41s against 0.16s for the
    # correct code -- `control` installed one and this assertion, bounded
    # at 2s, passed. A test that cannot fail for its stated property is
    # the thing this suite exists to catch, found in this suite.
    slow_city = f"{WORK}/orphanslow.city"
    open(slow_city, "w").write(
        f"house orph {BIN}/unit-orphanslow kind=longrun budget=3 lids=none\n")
    slow_blob = f"{WORK}/orphanslow.blob"
    b = run(["python3", CC, "--city", slow_city, "--out", slow_blob])
    expect(b.returncode == 0, f"bake slow\n{b.out}{b.err}")
    for f in (mark,):
        try:
            os.unlink(f)
        except FileNotFoundError:
            pass
    t0 = time.time()
    rc, out = boot(plan=slow_blob, hold=150)
    wall = time.time() - t0
    expect(city_closed(rc, out), f"orphan-B rc={rc}\n{out[-1500:]}")
    # PAIRED ON THE EFFECT, exactly as case A above -- which was fixed
    # first and left this one reading `leaving 3 behind`, a line the
    # fixture prints whether or not any fork returned. `control` made
    # only the slow fixture fork nothing and this stayed green, under an
    # ok line saying shutdown does not wait for orphans still alive.
    # Twenty lines below the comment explaining why that is wrong.
    slow_forked = len(re.findall(r"\[orphan\] run=\d+ child=\d+ pid=\d+",
                                 out))
    expect(slow_forked == 12,
           f"the slow fixture reported {slow_forked} successful forks, "
           f"expected 12. Nothing below is about orphans until they "
           f"exist.\n{out[-1500:]}")
    # AND THAT THEY WERE STILL ALIVE. `wall` bounds shutdown's duration
    # and says nothing about there being anything to wait for:
    # `control` set -DORPHAN_SLEEP_MS=0 -- one character in the Makefile
    # -- so every child exited at once, and the test stayed green with
    # the property vacuous. orphans=0 here is the positive evidence,
    # because the children outlive the hold: twelve were made, none was
    # reaped, so twelve were alive and deliberately left.
    mb = re.search(r"orphans=(\d+)", out)
    expect(mb and int(mb.group(1)) == 0,
           f"the closed line reports orphans={mb.group(1) if mb else '?'}, "
           f"expected 0. Nonzero means the children died before shutdown "
           f"and none was alive to be waited for, so the bound below "
           f"would be measuring nothing.\n{out[-1500:]}")
    # The property is that shutdown did not WAIT. The children sleep 3s
    # and the hold is 150ms, so a shutdown that waits cannot finish
    # before ~3s while one that does not closes in ~0.2s. The bound sits
    # an order of magnitude from both.
    expect(wall < 1.5,
           f"shutdown took {wall:.2f}s with orphans still alive. "
           f"Shutdown is bounded by the grace period; waiting on an "
           f"orphan is unbounded and a stuck one would hang the "
           f"machine.\n{out[-1500:]}")
    print(f"ok orphans-across-restarts ({forked} reaped across {runs} "
          f"runs; and shutdown does not wait for orphans still alive, "
          f"closing in {wall:.2f}s)")


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
    between deaths, so budget=3 never fired. Pairing is the restart lines
    that did happen, then the one that must not.

    THE NAME CHANGED on 2026-09-11, from budget-hard-total to
    budget-no-reset, because the old one claimed more than any boot can
    show. A 7s hold against 1.2s deaths pins "no reset inside 7s"; a
    30-second window is green against it, which `control` demonstrated.
    "Hard total" is a claim about all time and a timing test cannot
    reach it -- so the structural assertions at the end of this function
    carry that half: `deaths` is assigned exactly twice in nwsup.c (one
    init, one increment) and the file has no clock, so there is nowhere
    for a reset to live. Handed over by the agent who owns the restart
    loop, who was right that keeping the old name was the wrong answer.

    This docstring also used to end "Control: restore a window reset and
    this test sees death=4 inside the hold." It does not, and the correction had
    already been written into the body comment below while this sentence,
    eight lines above it, kept the false version -- reworded in one place
    and survived by being moved, which `claims` found by running the
    control it names. A window resets the tally to 0 before the increment,
    so every line reads `death=1` forever; `death=4` never appears. What
    catches a restored window is `death=2 in out` together with `n == 3`.
    A docstring is what help() and every summary tool shows, so a
    correction that lands only in a comment has not landed."""
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
    # 3 deaths * 1.2s plus spawn/shutdown slack.
    #
    # CORRECTION: this said a sliding 1s window "would log death=4 before
    # this deadline". It would not -- a window resets the tally to 0 before
    # the increment, so every line reads death=1 forever and
    # `death=4 not in out` is satisfied by the exact defect it names. The
    # assertions that catch a restored window are `death=2 in out` and
    # `n == 3`. `control` ran the mutant and read the log.
    #
    # STILL NOT PINNED, stated because the suite should not imply more than
    # it proves: a window LONGER than this hold is green here. `control`
    # restored a 30-second window and the whole suite passed, budget
    # unbounded again. What this test pins is "no reset inside a 7s hold",
    # which is a property of the fixture's 1.2s interval, not of the budget
    # being a hard total. Pinning that needs deaths spaced further apart
    # than any plausible window, which is a slow test and a design call for
    # whoever owns the restart loop.
    rc, out = boot(plan=blob, hold=7000)
    expect(city_closed(rc, out), f"city should survive, rc={rc}\n{out}")
    expect("restart drip death=1" in out, f"first restart missing\n{out}")
    expect("restart drip death=2" in out, f"second restart missing\n{out}")
    expect("restart drip death=3" in out, f"third restart missing\n{out}")
    expect("restart drip death=4" not in out,
           f"budget did not bound: death=4\n{out}")
    n = out.count("restart drip death=")
    expect(n == 3, f"expected 3 restarts, got {n}\n{out}")

    # THE STRUCTURAL HALF, which is the half the name claims and no boot
    # can establish. "A hard total" is a statement about all time; a 7s
    # hold cannot distinguish it from a 30s window, and `control` proved
    # that by restoring one and watching this test stay green.
    #
    # What makes it a hard total is that there is nowhere for a reset to
    # live: `deaths` is initialised once and incremented once, nothing
    # else assigns it, and nwsup.c has no clock at all. That is checkable
    # exactly, cheaply, and for all time -- so check it here instead of
    # pretending a longer hold would settle it. Handed over by the agent
    # who owns the restart loop; the file is ours, so the test is ours.
    # CODE ONLY. Matching raw source made this go red for a COMMENT:
    # `\btime\s*\(` hits "at fork time (", which is this repository's own
    # phrasing, and `deaths = 0` inside a note about D18 counted as an
    # assignment. CLAUDE.md requires a removed rule to be re-filed as a
    # comment rather than deleted, so the raw-source version punished the
    # thing the project asks for, and blamed a reset that did not exist.
    # `control` found both.
    sup = strip_c_comments(open(os.path.join(ROOT, "nwsup.c")).read())
    assigns = re.findall(r"\bdeaths\s*(?:=[^=]|\+\+|--|[-+*/]=)", sup)
    expect(len(assigns) == 2,
           f"nwsup.c assigns `deaths` {len(assigns)} times, expected exactly "
           f"two -- one initialisation and one increment. A third assignment "
           f"is where a window reset would live, and no boot-length test can "
           f"see one: {assigns}")
    # A reset can also be written through a pointer -- `int *dp = &deaths;
    # *dp = 0;` -- which the assignment count above cannot see. `control`
    # built exactly that, with times() as the clock, and both halves of
    # this test stayed green while the budget was unbounded again. Taking
    # the address is the step every such route needs (a pointer, a
    # memset, a helper, a read(2) straight into it), so refuse it: nothing
    # in this loop has any reason to.
    taken = re.findall(r"&\s*deaths\b", sup)
    expect(not taken,
           f"nwsup.c takes the address of `deaths` ({len(taken)}x). Every "
           f"route that resets it without assigning it by name goes through "
           f"&deaths, and the assignment count above cannot see any of them.")
    clocks = re.findall(
        r"\b(clock_gettime|now_ms|alarm|nanosleep|clock_nanosleep|usleep|"
        r"setitimer|timerfd_create|timer_create|gettimeofday|times|clock|"
        r"sysinfo|time)\s*\(", sup)
    expect(not clocks,
           f"nwsup.c has regained a timing primitive: {sorted(set(clocks))}. "
           f"A budget that can read a clock can reset on one, which is D18 "
           f"coming back, and this suite's timing assertions above would "
           f"stay green against any window longer than their hold.")

    # WHAT THIS DOES NOT CLOSE, said plainly because the previous wording
    # ("there is nowhere for a reset to live") claimed more than it
    # checks. A denylist of names cannot be exhaustive: `control` got a
    # working 30-second window past an earlier version of these lines
    # using times() and a pointer, and lists further misses -- sysinfo(),
    # a /proc/uptime read, a poll() timeout, or a parallel `forgiven`
    # counter that never writes `deaths` at all. The address check above
    # closes the pointer family; the clock list is a fence, not a proof.
    # The behavioural half catches any window SHORTER than the 7s hold.
    # A window longer than the hold and built from a name not listed here
    # is still invisible, and closing that needs deaths spaced further
    # apart than any plausible window -- the slow test the handoff names.
    print("ok budget-no-reset (3 restarts in a 7s hold; nwsup.c assigns "
          "deaths twice, never takes its address, and names no clock)")


def test_shutdown_does_not_restart():
    """A supervisor that sees SIGTERM must not restart the house.

    unit-term exits 0 after handling TERM. As a longrun that would
    otherwise be a restart. The same signal PID 1 already sends is the
    news; no extra channel. Control: delete `if (stopping) _exit` in
    nw-sup and this test sees `restart stay`."""
    term = f"{BIN}/unit-term"
    dieterm = f"{BIN}/unit-dieterm"
    city = f"{WORK}/shut.city"
    # TWO houses, and the second is the point. `stay` has never died, so its
    # supervisor's `deaths` is 0 -- which let `control` narrow the guard to
    # `if (stopping && deaths == 0)` and keep this test green while the
    # guard was broken for every house that had ever restarted. `dt` banks a
    # death before shutdown, so the guard is asserted on the state it exists
    # for. Measured on the mutant: `restart dt death=2` lands after
    # `shutdown TERM houses`.
    open(city, "w").write(
        f"house stay {term} kind=longrun budget=20 lids=none\n"
        f"house dt {dieterm} kind=longrun budget=20 lids=none\n")
    blob = f"{WORK}/shut.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    # A stale marker would make dt skip its death and silently turn this
    # back into the one-house test. Removing it means a leftover fails the
    # `restart dt death=1` assertion below rather than weakening it.
    try:
        os.unlink("/tmp/nw-dieterm.mark")
    except FileNotFoundError:
        pass
    # Long enough for dt to die, be restarted, and be waiting on TERM.
    rc, out = boot(plan=blob, hold=900)
    expect(city_closed(rc, out), f"shutdown rc={rc}\n{out}")
    expect("SIGTERM handler ran" in out, f"house never saw TERM\n{out}")
    # Pairing for dt: it must have died and been restarted BEFORE shutdown,
    # or `deaths` is 0 and this is the old test wearing two houses.
    expect("restart dt death=1" in out,
           f"dt never died before shutdown, so its supervisor's deaths is 0 "
           f"and the guard is not being asserted on a restarted house\n{out}")
    expect("[dieterm] restarted, now waiting for TERM" in out,
           f"dt died but never came back up\n{out}")
    # The house must actually DIE. A restart can only follow an exit, so
    # "took TERM" alone leaves this green against a fixture whose handler
    # prints and keeps looping -- `control` built that and watched the
    # test pass with the branch it exists for deleted.
    expect("exiting cleanly after TERM" in out,
           f"the house took TERM but never exited, so nothing could have "
           f"restarted and this test proved nothing\n{out}")
    expect("restart stay" not in out,
           f"supervisor restarted during shutdown\n{out}")
    expect("[dieterm] exiting cleanly after TERM" in out,
           f"dt took TERM but never exited, so nothing could have restarted "
           f"it and the deaths>0 half of this test proved nothing\n{out}")
    expect("restart dt death=2" not in out,
           f"supervisor restarted a house that had ALREADY died once, during "
           f"shutdown -- the guard is conditioned on deaths\n{out}")
    # The margin is not incidental: the restart has to land inside PID 1's
    # grace window to be observable at all. `control` measured the edge by
    # delaying the restart -- 100ms and 300ms fail the test, 450ms passes
    # it, because PID 1 SIGKILLs the supervisor before the line exists. So
    # a supervisor that restarts a SLOW-exiting house during shutdown still
    # reads green here, which is nearer to the QEMU repro than this test is.
    print(f"ok shutdown-no-restart (observed inside shutdown_city's "
          f"{PID1_GRACE_MS}ms grace; a restart later than that is invisible "
          f"to this test)")


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


def make_brick(ident, mirrors=(), exe="unit-brick"):
    """Build a content-addressed brick and return its HASH. The name is the
    sha256 of the tree's contents, so two bricks that differ only in the
    text of /id land at different paths on their own -- nothing assigns
    them.

    THE IMAGE LANDS ON THE MACHINE ROOT, not in the stage, and that is
    forced: phase 3 has nw-sup compose NW_BRICK_DIR/<hex> itself and
    NW_BRICK_DIR is absolute. Two consequences worth knowing before you
    debug them. Distinct stage paths no longer isolate brick tests from
    each other -- the filename is the content hash, so two runs of the same
    tree target the identical file and one run's `rm -f` can land under the
    other's open(). And the directory is created here rather than by `make
    stage`, so on a machine where the suite cannot write the machine root
    this raises, and it is turned into a named skip below rather than a
    crash. `fd-auditor`. (The docstring said "under {STAGE}/nw/bricks" and
    "return its path" until 2026-09-12; phase 3 falsified both clauses of
    the first sentence of the function the brick tests all start at.)

    `mirrors` are machine paths the brick must have mount points for. nw-sup
    will not mkdir into a brick, so the empty directories have to be baked in
    here, which is exactly the constraint a real baker works under."""
    import hashlib, shutil
    # THE GUARD LIVES HERE so no caller can forget it. Every brick is an
    # image as of phase 2, so every make_brick() needs all three erofs
    # capabilities; asking here means a new test gets the skip for free.
    why = erofs_available()
    if why:
        raise Unavailable(why)
    # /w is a writable-probe target: houses/brick.c writes a byte into an
    # EXISTING file to show the layer is writable, and it must not be /id,
    # which every other assertion reads.
    files = {"id": ident.encode() + b"\n", "w": b"-\n"}
    tmp = tempfile.mkdtemp(dir=WORK)
    os.makedirs(f"{tmp}/bin")
    # WHICH FIXTURE, because a house that pivots into its brick can only
    # exec something inside it. Defaults to unit-brick so every existing
    # caller is unchanged; the layer test needs its own, and passing the
    # name here beats a second copy of this function.
    shutil.copy(f"{BIN}/{exe}", f"{tmp}/bin/brick")
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
    # PHASE 2: pack the tree into an erofs IMAGE. `brick=` names a file
    # now, not a directory -- nw-sup loop-mounts it on NW_BRICK_MNT. The
    # hash is still over the tree's contents, so two bricks that differ
    # only in /id still land at different paths on their own.
    #
    # The image is what buys the seal, and the seal is the reason phase 2
    # exists: a directory brick is writable by the house that roots in it
    # unless a lid says otherwise. See test_brick_image_is_sealed.
    # PHASE 3 RETURNS THE HASH, not the path. nw-sup composes
    # NW_BRICK_DIR/<hex>NW_BRICK_SUFFIX itself, so the image has to land
    # at that exact name on the MACHINE root -- the same reason the
    # Makefile creates /nw/mnt there. The stage is not the machine root in
    # the lab, and NW_BRICK_DIR is absolute.
    hexd = h.hexdigest()
    brick = f"{_brick_dir()}/{hexd}{_brick_suffix()}"
    # A named skip, not a traceback: not being able to write the machine
    # root is an ENVIRONMENT difference, and harness.md's rule is that one
    # is announced by name rather than presenting as a code failure. It
    # reached main()'s unhandled-exception path as `FAIL: ... raised an
    # unhandled exception` before this -- loud, which is right, and
    # attributed to the wrong thing, which is not.
    try:
        os.makedirs(_brick_dir(), exist_ok=True)
    except OSError as e:
        raise Unavailable(
            f"cannot create {_brick_dir()} ({e.strerror}); nw-sup composes "
            f"an absolute brick path, so the image must land on the machine "
            f"root and this suite cannot write there")
    subprocess.run(["rm", "-f", brick], check=False)
    r = run(["mkfs.erofs", "-zlz4", brick, tmp])
    expect(r.returncode == 0 and os.path.exists(brick),
           f"mkfs.erofs failed packing {ident}; erofs_available() should "
           f"have skipped this test before here\n{r.out}{r.err}")
    subprocess.run(["rm", "-rf", tmp], check=False)
    return hexd


_EROFS = None


def erofs_available():
    """Can this machine PACK, ATTACH and MOUNT an erofs image?

    All three, because they are three different capabilities and a machine
    can have any subset. This one has all three; a clone of it can mount
    erofs with no mkfs.erofs; another environment has neither. If phase 2
    were verified in exactly one environment that would be the position
    Landlock was in immediately before it turned out never to have worked
    anywhere it applied -- so this asks the question the code under test
    asks, and a missing capability is a NAMED SKIP, never a green line.
    """
    global _EROFS
    if _EROFS is not None:
        return _EROFS
    if not shutil.which("mkfs.erofs"):
        _EROFS = "mkfs.erofs is not installed, so no brick image can be packed"
        return _EROFS
    if "erofs" not in open("/proc/filesystems").read():
        _EROFS = "the kernel has no erofs driver (/proc/filesystems)"
        return _EROFS
    if not os.path.exists("/dev/loop-control"):
        _EROFS = "/dev/loop-control is absent, so no image can be attached"
        return _EROFS
    # Ask by doing: pack, attach, mount. Reading /proc/filesystems says the
    # driver is present, not that this container may use it.
    d = tempfile.mkdtemp(dir=WORK)
    try:
        os.makedirs(f"{d}/t")
        open(f"{d}/t/id", "w").write("probe\n")
        if run(["mkfs.erofs", "-zlz4", f"{d}/i.img", f"{d}/t"]).returncode != 0:
            _EROFS = "mkfs.erofs is installed but failed on a trivial tree"
            return _EROFS
        os.makedirs(f"{d}/m")
        m = run(["mount", "-t", "erofs", "-o", "ro,nodev,loop",
                 f"{d}/i.img", f"{d}/m"])
        if m.returncode != 0:
            _EROFS = ("an erofs image could be packed but not mounted: "
                      + (m.err or m.out).strip()[:120])
            return _EROFS
        run(["umount", f"{d}/m"])
        _EROFS = False          # False means "no reason to skip"
        return _EROFS
    finally:
        subprocess.run(["rm", "-rf", d], check=False)


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
        f"brick={one} layer=l-one bind={shared} bind=/proc\n"
        f"house two /bin/brick kind=oneshot lids=newns,seccomp brick={two} layer=l-two\n"
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
    # THE CENSUS, not the count. `fds_ge3=0` above survives dropping
    # O_CLOEXEC and survives dropping the close() calls -- it only fails
    # when BOTH go, so neither single change is pinned, and phase 2 took
    # that conjunction from two descriptors to five. `fd-auditor`.
    #
    # Naming each descriptor makes one change visible: an inherited image
    # fd appears as fd3=/nw/bricks/<hash>.img. And asserting the pipe
    # identity is the bug 4/9/13 check done properly -- those were wrong
    # ROUTING, so which fd is where is the question, not how many.
    # The link target is the identity: a pipe reads back as "pipe:[INODE]".
    # No fstat -- glibc routes it through statx, which is not in the seccomp
    # allow-list, and widening that for a fixture is what runtime.md
    # forbids. The house was killed by SIGSYS before printing a line, which
    # nw-sup reported as status 18176 -- 71 << 8, its "not a normal exit"
    # code -- and read as a mysterious early death.
    cen = {}
    for unit, fd, tgt in re.findall(r"(\w+) fd(\d+)=(\S+)", out):
        cen.setdefault(unit, {})[int(fd)] = tgt
    # HOUSE ONE ONLY, and that is a real limit of this test rather than an
    # oversight: a house can only inspect /proc/self/fd if /proc is bound
    # into its brick, and house two deliberately has no /proc so that the
    # honest `noproc` path is exercised. So the census covers the house
    # that can be measured, and house two is asserted to say so rather
    # than to report a zero nobody measured.
    #
    # What this does NOT cover, stated so it is not assumed:
    #
    # 1. The cross-house property that each house gets a DISTINCT pipe.
    #    That is the bug 4/9/13 shape and it needs /proc in both houses,
    #    which would cost the noproc branch above. `fd-auditor` verified
    #    it by hand -- fd 1 and 2 were one inode in one house and another
    #    in the other -- and nothing in the suite pins it.
    #
    # 2. IT DOES NOT BREAK THE O_CLOEXEC/close() CONJUNCTION, and
    #    `fd-auditor` said it would. I ran both controls against the
    #    census: dropping O_CLOEXEC from all three new descriptors and
    #    keeping the close() calls still passes, and dropping the three
    #    close() calls while keeping O_CLOEXEC still passes. Of course it
    #    does -- either mechanism alone keeps the table clean, so no
    #    observation of the table can tell them apart. That is correct
    #    redundancy in lid_brick rather than an untested conjunction, and
    #    the honest statement is that it cannot be pinned from here, not
    #    that a better assertion would do it.
    #
    # What the census DOES buy over `fds_ge3` is naming what is present:
    # a leaked image fd reads as fd3=/nw/bricks/<hash>.img rather than as
    # a count going 0 to 1, and wrong routing is visible at all.
    fds = cen.get("one", {})
    expect(sorted(fds) == [0, 1, 2],
           f"house one was born holding descriptors {sorted(fds)}, "
           f"expected exactly [0, 1, 2]. Invariant 5: /dev/null on 0 and "
           f"its own log pipe on 1 and 2, and no third thing. A brick "
           f"house now opens five descriptors on the way in, so a stray "
           f"one here is an image or a loop device that outlived "
           f"execv.\n{fds}\n{out[-1200:]}")
    expect(fds[0] == "/dev/null",
           f"house one has {fds[0]!r} on fd 0, not /dev/null -- a count "
           f"cannot see this and wrong routing is what bugs 4, 9 and 13 "
           f"were")
    expect(fds[1] == fds[2] and fds[1].startswith("pipe:"),
           f"house one's fd 1 and fd 2 are not the same pipe ({fds[1]} vs "
           f"{fds[2]}); they must be the one log pipe")
    expect("two census=noproc" in out,
           f"house two has no /proc bound and must say so; a census that "
           f"silently reported nothing would read as zero descriptors\n"
           f"{out[-1200:]}")
    print("ok brick-is-a-root (census on the house with /proc: exactly "
          "/dev/null on 0 and one pipe on 1 and 2, named not counted; "
          "house two honestly reports noproc)")


def test_path_traversal_refused():
    """A path in the plan is a string that nw-sup hands straight to mount(2)
    and open(2). Before this was checked, a brick of `<brick>/../..` baked
    clean, passed nw-check, and gave the house a root of /tmp/nw-init-run/nw
    -- every brick on the machine and the store -- while still logging
    `lid brick` and exiting 0. No error anywhere.

    Refused now at path_ok_len, the one site every path in a plan passes
    through, so exec_path, brick and bind are all covered by one check. The
    baker refuses too, independently: it is not in the TCB.

    This closes traversal and NOT symlinks -- see docs/options/07.

    PHASE 3 DELETED THE BRICK CASE, and that is a narrowing of the INPUT,
    not of the check. `brick` is 32 raw bytes of sha256 now; there is no
    value of those bytes that means "../..", because there is no separator
    and no relative component to write. exec_path and bind are still paths
    and are still checked here. HISTORY.md records the deletion, because a
    removed security check reads as a regression to anyone who finds it
    without the reason."""
    esc = f"{WORK}/esc.city"
    # A REAL HASH, because brick= is one now. The traversal cases that used
    # to live here for `brick` are gone; see the note in the docstring.
    brick = "de" * 32

    for line, why in (
        (f"house one /bin/brick kind=oneshot lids=newns,seccomp "
         f"brick={brick} layer=l-trav bind=/etc/../etc\n", "bind"),
        (f"house one /bin/../bin/brick kind=oneshot lids=newns,seccomp "
         f"brick={brick} layer=l-trav\n", "exec_path"),
    ):
        open(esc, "w").write(line)
        p = run(["python3", CC, "--city", esc, "--out", f"{WORK}/nope.blob"])
        expect(p.returncode != 0, f"baker accepted .. in {why}\n{p.out}{p.err}")
        expect("no '..' component" in (p.out + p.err),
               f"{why} reason\n{p.out}{p.err}")

    # THE BRICK CASE IS NOT MISSING, IT IS INEXPRESSIBLE. Phase 3 made the
    # field 32 raw bytes, so there is no byte string to write into it that
    # means "../..". Asserted from the other side, because "we deleted a
    # test" is not evidence: the baker must refuse a brick= that is a path
    # at all, which is the input the old case was built from.
    # BOTH SHAPES, and the CLEAN one is the load-bearing case. A traversing
    # path is refused by any check that looks at `..`, so on its own it
    # cannot tell a shape check from a traversal check: `control` widened
    # `_is_hex64` to accept a clean absolute path -- phase-2 behaviour, with
    # bake() padding it into the 32 bytes -- and the suite stayed green
    # while the baker emitted `brick=/nw/bricks/anything/at/all` and the TCB
    # validator accepted it. The `ok` line below claimed the baker's refusal
    # "stands in its place" for the deleted traversal case, and the test did
    # not check the thing the line claimed.
    # LENGTH AND ALPHABET SEPARATELY, because every case here used to fail
    # on LENGTH alone -- 36, 19 and 12 characters, none of them 64 -- so
    # the charset clause was never consulted and `control` deleted either
    # half of `_is_hex64` with the suite green. What each removal costs,
    # measured: without the charset, a 64-character traversing path dies
    # inside bake() at `bytes.fromhex` as an unhandled ValueError, which is
    # a refusal degraded to a traceback; without the length, the baker
    # SUCCEEDS and writes a short blob that nw-check rejects as `size` --
    # a true rejection under a false reason, the shape
    # old-magic-refused-as-magic exists to prevent.
    NBH = int(blob_h("NW_BRICK_HASH")) * 2
    for path, what in ((f"{STAGE}/nw/bricks/deadbeef/../..", "a traversing path"),
                       (f"{_brick_dir()}/deadbeef", "a clean absolute path"),
                       ("deadbeef.img", "a bare filename"),
                       ("/nw/bricks/" + "a" * (NBH - 11), "a path of exactly "
                        "the hash length, so only the alphabet can refuse it"),
                       ("dead", "a short all-hex value, so only the length "
                        "can refuse it")):
        open(esc, "w").write(
            f"house one /bin/brick kind=oneshot lids=newns,seccomp "
            f"brick={path} layer=l-shape\n")
        p = run(["python3", CC, "--city", esc, "--out", f"{WORK}/nope.blob"])
        expect(p.returncode != 0,
               f"baker accepted {what} as brick=\n{p.out}{p.err}")
        expect("hex characters" in (p.out + p.err),
               f"the baker must refuse {what} for being the wrong SHAPE, not "
               f"for containing '..' -- the traversal check is gone and this "
               f"is what replaced it\n{p.out}{p.err}")

    # The checker must refuse a crafted blob on its own, from one the baker
    # would not emit. exec_path is the field that is still a path, so it is
    # the one crafted here.
    good = f"{WORK}/esc-ok.blob"
    open(esc, "w").write(
        f"house one /bin/brick kind=oneshot lids=newns,seccomp "
        f"brick={brick} layer=l-esc\n")
    p = run(["python3", CC, "--city", esc, "--out", good])
    expect(p.returncode == 0, f"bake\n{p.out}{p.err}")

    EXEC_OFF = 20 + int(blob_h("NW_NAME_LEN"))     # hdr + name
    EXEC_LEN = int(blob_h("NW_PATH_LEN"))
    d = bytearray(open(good, "rb").read())
    expect(bytes(d[EXEC_OFF:EXEC_OFF + 10]) == b"/bin/brick",
           "exec_path is not where the layout says it is")
    evil = b"/bin/../../etc/x"
    d[EXEC_OFF:EXEC_OFF + EXEC_LEN] = evil + b"\x00" * (EXEC_LEN - len(evil))
    d[16:20] = b"\x00\x00\x00\x00"
    d[16:20] = struct.pack("<I", zlib.crc32(bytes(d)) & 0xFFFFFFFF)
    bad = f"{WORK}/esc.blob"
    open(bad, "wb").write(bytes(d))

    r = run([f"{BIN}/nw-check", bad])
    expect(r.returncode != 0, "nw-check accepted a traversing exec_path")
    expect("exec_path" in (r.out + r.err), f"reason\n{r.out}{r.err}")

    # And it must not merely fail later at exec: the city must not boot.
    rc, out = boot(plan=bad, hold=400)
    expect("nw-check reject: exec_path" in out and "HALT: plan" in out,
           f"a traversing plan must be refused by name, not merely halt on"
           f"\n{out}")
    print("ok path-traversal-refused (exec_path and bind; the brick "
          "case is gone because a 32-byte hash cannot express a "
          "traversal -- the baker refusing a path-shaped brick= is "
          "what stands in its place)")


def test_brick_image_is_sealed():
    """A house cannot write into its own brick, and NO LID IS DOING THAT.

    This is the test that justifies phase 2. Under the directory brick a
    house rooted in its own brick could write into it, and the only thing
    that stopped it was the Landlock lid -- which is opt-in, and which on
    this machine does not exist at all. An image is not writable, by
    anything, with no lid asked for.

    `lids=newns` is the whole lid set here. NEWNS is structurally required
    (a brick forces it -- nwcheck returns NW_E_BRICKNS without it, and
    nw-sup re-checks) and it confines nothing: it makes the mount private,
    it does not make it read-only. No Landlock, no seccomp. So a refusal
    here is the filesystem's, which is the property being bought.

    THE SEAL IS OVER-DETERMINED and the control does not run from the flag
    side. The kernel forces read-only when either fd is O_RDONLY and erofs
    has no write path, so removing MS_RDONLY or LO_FLAGS_READ_ONLY does not
    produce a successful write -- it fails at the mount, which is a
    different failure wearing the right result. The control that works is
    the other side: make the brick a directory bind-mounted onto itself,
    which is what nw-sup did before this phase, and the write succeeds.
    docs/plans/01 records both, measured.
    """
    # No erofs guard here: make_brick() raises Unavailable and main() turns
    # it into a named skip. One mechanism, not one per call site -- three of
    # the four callers forgot the explicit version.
    brick = make_brick("sealed-one")
    city = f"{WORK}/sealed.city"
    open(city, "w").write(
        f"house sealed /bin/brick kind=oneshot lids=newns brick={brick} layer=l-sealed\n")
    blob = f"{WORK}/sealed.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    # Hashed BEFORE the boot, so the comparison after it is a claim about
    # the boot. Comparing two reads taken afterwards is an identity, which
    # is what this line was for one revision -- a check that cannot fail,
    # in the test whose whole subject is a property that must not change.
    # COPIED ASIDE, not re-read. /nw/bricks is a shared machine-root path
    # and make_brick packs with bare mkfs.erofs, whose output is NOT a
    # function of the tree -- three packs of one tree give three different
    # images. So a concurrent suite regenerating the same <hash>.img makes
    # this comparison fail on an intact tree, with a message accusing the
    # overlay of writing through to the lower. `tcb-review` reproduced it
    # deliberately and also saw it once without interference. Comparing
    # against a private copy asks the question this test means to ask.
    img0 = f"{_brick_dir()}/{brick}{_brick_suffix()}"
    keep = f"{WORK}/sealed-before.img"
    shutil.copyfile(img0, keep)
    before = hashlib.sha256(open(keep, "rb").read()).hexdigest()
    rc, out = boot(plan=blob, hold=800)
    expect(city_closed(rc, out), f"sealed rc={rc}\n{out}")

    # PAIRED. "the write was refused" is satisfied by the refusal AND by a
    # house that never started, and those are opposite outcomes. The house
    # reading its own /id out of the image is what makes the refusal below
    # a claim about the seal rather than about a house that is not there.
    expect("sealed id=sealed-one" in out,
           f"the house did not read its own brick's /id, so it either never "
           f"ran or is not rooted in the image -- the refusal below would "
           f"be about nothing\n{out[-1500:]}")
    # THE WRITE NOW SUCCEEDS, AND THE SEAL IS STILL REAL. This asserted
    # `wr_root=denied(30)` until writable areas landed, and it was right:
    # under phase 2 a house's root WAS the brick and nothing else, so a
    # write had nowhere to go. A house's root is now the brick plus its
    # layer, so the write goes to the layer -- and the brick is as
    # unwritable as it ever was, which is what the control below shows.
    #
    # Re-filed rather than deleted, because deleting it would lose the
    # property: the EROFS refusal moved from the house's root to the brick
    # underneath it, and nothing else in the suite pins that the image is
    # read-only. The assertion changed direction; the claim did not.
    expect("sealed wr_root=ok" in out,
           f"the house could not write into its own root. Every house has "
           f"a writable layer over its brick -- if this is EROFS then the "
           f"overlay did not mount and the house is rooted on the bare "
           f"image\n{out[-1500:]}")
    # NO LOGGER PREFIX IN THE MATCH. PID 1 prefixes a write CHUNK, not a
    # line, so when `lid newns` and `lid layer` land in one read the
    # second arrives bare. Measured 5 unprefixed in 30 boots under load,
    # and the real test failed twice in 30 -- with a message saying the
    # house is not in a brick, which harness.md names as the shape to
    # avoid. This city has one house, so the prefix carries nothing.
    expect("[nw-sup] lid layer" in out,
           f"no `lid layer` line: the write above may have succeeded "
           f"because the house is NOT in a brick at all, which is the same "
           f"observation from the failure side\n{out[-1500:]}")
    # And the seal itself, from outside: the image file the house rooted in
    # is byte-identical after the boot. This is what "sealed" means now --
    # not that writes fail, but that they never reach the brick.
    expect(hashlib.sha256(open(img0, "rb").read()).hexdigest() == before,
           f"the brick image changed over the boot. The house's write is "
           f"supposed to land in its layer; if the image moved then the "
           f"overlay is writing through to the lower and the seal is gone")
    print("ok brick-image-is-sealed (lids=newns only -- no landlock, no "
          "seccomp; the house read its own /id out of the image, its write "
          "into its root SUCCEEDED into the layer, and the image is "
          "untouched -- the seal moved under the overlay, it did not go)")


def test_layer_survives_a_restart():
    """A house's write into its own root is still there after it dies.

    THE TEST THAT JUSTIFIES WRITABLE AREAS. A house is its sealed brick
    plus one writable layer; the brick is what it can see and the layer is
    what it can keep. Nothing else in the suite can show the keeping part:
    every other brick fixture is oneshot, and the write assertions that
    existed asserted a REFUSAL, which was correct when a house's root was
    the bare image.

    The fixture reads /id from the brick and /state from the layer every
    run, appends a mark, and exits nonzero so nw-sup restarts it. So run 2
    reports what run 1 wrote. PERSISTENCE ACROSS A DEATH, not within a
    life -- only the first needs a layer at all.

    PAIRED THREE WAYS, because each alone is satisfied by the wrong
    arrangement:
      - `state=absent` on the first run and non-absent later, or the
        layer is being reset (or was never empty, which would mean this
        test is reading a previous run's data);
      - `id=` from the brick every run, or the house is writing somewhere
        that is not over its brick;
      - the marks ACCUMULATE, or "persisted" cannot be told from
        "rewritten from scratch each time".

    BOTH DIRECTIONS, per HISTORY.md section 53. This is the acceptance
    direction -- a legal plan whose write must survive. The rejection
    direction is test_brick_image_is_sealed, which now asserts the image
    itself is byte-identical after the boot: writes reach the layer and
    never the brick. Neither substitutes for the other."""
    brick = make_brick("layer-one", exe="unit-layer")
    lid = "l-survive"
    # The fresh-layer reset lives in stage_layers() now, so every brick
    # test gets it and none can forget it. This test had the only copy.
    city = f"{WORK}/layer.city"
    open(city, "w").write(
        f"house keeper /bin/brick kind=longrun budget=3 "
        f"lids=newns brick={brick} layer={lid}\n")
    blob = f"{WORK}/layer.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out = boot(plan=blob, hold=1600)
    expect(city_closed(rc, out), f"layer rc={rc}\n{out[-2000:]}")

    # SCOPED TO THE FIXTURE'S OWN TAG. A bare `id=(\S+)` also matches
    # `pid=5` in nw-spawn's line, which put a '5' at the head of the list
    # and failed the brick assertion for a reason that had nothing to do
    # with bricks. The fixture tags every line; use the tag.
    states = re.findall(r"\[layer\] state=(\S+)", out)
    ids = re.findall(r"\[layer\] id=(\S+)", out)
    expect(len(states) >= 2,
           f"the house ran fewer than twice, so nothing was read back "
           f"across a restart and this test is about nothing: "
           f"{states}\n{out[-2000:]}")
    expect(states[0] == "absent",
           f"the first run already found state -- the layer was not empty, "
           f"so a later read proves nothing: {states}\n{out[-2000:]}")
    expect(all(v.startswith("layer-one") for v in ids),
           f"the house did not read /id out of its brick on every run, so "
           f"it is not rooted in the image and whatever it wrote did not "
           f"go through the layer: {ids}\n{out[-2000:]}")
    later = [v for v in states[1:]]
    expect(later and all(v != "absent" for v in later),
           f"a later run found no state: the write did not survive the "
           f"restart, which is the whole property: {states}\n{out[-2000:]}")
    expect(all(len(later[i]) < len(later[i + 1])
               for i in range(len(later) - 1)) or len(later) < 2,
           f"the state did not GROW across runs, so it is being rewritten "
           f"rather than kept: {states}\n{out[-2000:]}")
    # AND IT IS THE DECLARED LAYER, checked from OUTSIDE the house. Every
    # assertion above observes the inside of the house, so all of them
    # hold for a supervisor that keys the layer by the HOUSE NAME -- which
    # is the exact failure the layer-id field exists to prevent.
    # `control` made nw-sup pass `name` instead of `layer`, created
    # /nw/layers/keeper by hand, and this test passed. What was protecting
    # the claim was a coincidence of the stager creating the declared id's
    # directory and nothing creating the name's, not anything asserted.
    kept = os.path.join(_layer_dir(), lid, _blob_str("NW_LAYER_UPPER"),
                        "state")
    expect(os.path.exists(kept),
           f"nothing was written under the DECLARED layer id: {kept} does "
           f"not exist. The house kept its data somewhere, since it read "
           f"it back -- but not where the plan says, so a rename would "
           f"orphan it and that is what the id is for")
    got = open(kept).read()
    expect(got == states[-1] + "r",
           f"the declared layer holds {got!r}, not the {states[-1] + 'r'!r} "
           f"the house's last run should have left")
    print(f"ok layer-survives-a-restart (state {states} across "
          f"{len(states)} runs, /id read from the brick every time, and "
          f"{kept} holds {got!r} -- the DECLARED id, checked from outside)")


def test_many_brick_houses_all_start():
    """Every brick house in a city starts. Concurrency is the test.

    LOOP_CTL_GET_FREE reports a free index; it does not reserve one. Every
    brick house runs lid_brick at the same time -- nw-spawn waits only on
    the double-fork intermediary -- so without a retry they all get the
    same index and all but one get EBUSY.

    Measured on the committed-before-this tree: two houses produced one
    `FAIL loop configure errno=16` on EVERY run, and the suite printed `ok`
    because the default restart budget absorbed it. At eight houses only
    five or six of eight ever ran, and the city still closed
    `houses_reaped=N orphans=0` -- a third of the city missing behind a
    healthy-looking close line. Found by `tcb-review` and `fd-auditor`
    independently; neither was looking for it.

    EIGHT IS THE SIZE, and it is not arbitrary: two collides but the budget
    hides it, and eight is where the budget runs out and a house is
    permanently lost. A test at two would have gone green against the
    broken tree.

    The assertions are three, because the failure has three distinguishable
    shapes and only one of them is what this pins:
      - every house ran (the outcome),
      - no house restarted (the budget was not silently spent), and
      - no EBUSY was logged at all (the retry worked, rather than the
        budget covering for it).
    The second and third are what stop this passing for the wrong reason:
    a tree that loses the race and recovers via restart satisfies the first
    on its own.
    """
    n = 8
    houses = []
    for i in range(n):
        houses.append(make_brick(f"many-{i:02d}"))
    city = f"{WORK}/many.city"
    open(city, "w").write("".join(
        f"house m{i:02d} /bin/brick kind=oneshot lids=newns brick={b} layer=l-m{i:02d}\n"
        for i, b in enumerate(houses)))
    blob = f"{WORK}/many.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out = boot(plan=blob, hold=2500)
    expect(city_closed(rc, out), f"many-bricks rc={rc}\n{out[-2000:]}")

    ran = sorted(re.findall(r"id=many-(\d+)", out))
    missing = [f"{i:02d}" for i in range(n) if f"{i:02d}" not in ran]
    expect(not missing,
           f"{len(missing)} of {n} brick houses never ran (missing "
           f"{missing}). The close line above says the city was healthy; "
           f"it is not.\n{out[-2000:]}")
    ebusy = out.count("FAIL loop configure")
    expect(ebusy == 0,
           f"{ebusy} house(s) lost the loop-device race. Every house "
           f"started, so the restart budget covered for it -- which spends "
           f"a hard total (invariant 4) on a kernel race and leaves a "
           f"longrun house with no budget for a real crash.\n{out[-2000:]}")
    restarts = len(re.findall(r"restart m\d+", out))
    expect(restarts == 0,
           f"{restarts} restart(s) in a city of oneshot houses that should "
           f"each run once.\n{out[-2000:]}")
    print(f"ok many-brick-houses-all-start ({n} concurrent brick houses, "
          f"all ran, zero loop-device contention, zero restarts)")


def test_brick_hash_revalidated_at_the_supervisor():
    """nw-sup re-validates the hash before it composes a path.

    THIS IS THE LOAD-BEARING STEP OF THE WHOLE PHASE-3 ARGUMENT and it had
    no test. The argument for deleting the brick case of
    test_path_traversal_refused is that a hash cannot express a traversal --
    but the hash becomes TEXT exactly once, in the NW_BRICK handoff from
    nw-spawn to nw-sup, and text is what the traversal class needs. nw-sup
    reads its unit from the environment, not from the sealed blob, so the
    baker and nw-check do not stand behind this value at all.

    `claims` deleted the length check and the hex loop from nwsup.c and the
    entire suite stayed green -- 36 ok lines, exit 0. A deleted security
    check whose replacement argument rests on an untested guard is the
    argument being a hypothesis, which is the one thing CLAUDE.md says a
    sentence is worth nothing without.

    Driven directly rather than through a plan, deliberately: a plan cannot
    carry these values (the baker refuses them and the blob has no room for
    them), and the point is that this guard must hold for a value no plan
    produced.

    PAIRED. The rejections alone are satisfied by an nw-sup that refuses
    every brick, or that never reaches this code -- so a well-formed hash
    must get PAST the guard, which is asserted by it failing later and
    elsewhere, at `open brick image` on the composed path."""
    hexok = "de" * int(blob_h("NW_BRICK_HASH"))
    bad = [
        ("../../etc", "brick hash length", "a traversal, which is what the "
         "deleted brick case of path-traversal-refused used to cover"),
        ("/nw/bricks/x.img", "brick hash length", "a path"),
        ("z" * len(hexok), "brick hash not hex", "the right length, wrong "
         "alphabet"),
        (hexok[:-1], "brick hash length", "one hex digit short"),
        (hexok + "d", "brick hash length", "one hex digit long"),
        (hexok[:-1] + "A", "brick hash not hex", "uppercase, which mkbrick "
         "never prints"),
    ]
    for val, reason, what in bad:
        r = run([f"{BIN}/nw-sup", f"{BIN}/unit-probe", "probe"],
                env=dict(os.environ, NW_BRICK=val, NW_LAYER="l-rv",
                         NW_LIDS="4", NW_KIND="0"))
        out = r.out + r.err
        expect(reason in out,
               f"nw-sup did not refuse {what} with `{reason}`\n{out}")

    # The pairing, and the positive evidence that the guard was reached and
    # passed rather than skipped: a well-formed hash gets through, and the
    # next thing that fails names the path nw-sup composed ITSELF, under
    # NW_BRICK_DIR, which is the property the argument actually needs.
    r = run([f"{BIN}/nw-sup", f"{BIN}/unit-probe", "probe"],
            # NW_LAYER too: nw-sup re-checks the brick/layer pairing now,
            # so a brick with no layer dies before it reaches the open and
            # the pairing below would be about the wrong refusal.
            env=dict(os.environ, NW_BRICK=hexok, NW_LAYER="l-rv",
                     NW_LIDS="4", NW_KIND="0"))
    out = r.out + r.err
    expect("brick hash" not in out,
           f"a well-formed hash was refused by the hash guard\n{out}")
    expect("open brick image" in out,
           f"a well-formed hash did not reach the open; the rejections "
           f"above may be an nw-sup that refuses everything\n{out}")
    # NOT the composed path: nwsup.c's die() prints `FAIL <what> errno=<n>`
    # and no path, so this pair shows the guard was REACHED AND PASSED and
    # says nothing about what was composed afterwards. The ok line below
    # claimed the path; `tcb-review` ran the binary and it does not print
    # one. The composition is covered by the brick tests that actually
    # mount an image.
    print("ok brick-hash-revalidated (a traversal, a path, a bad alphabet "
          "and both off-by-ones refused at nw-sup, which reads NW_BRICK "
          "from the environment and not from the sealed blob; a "
          "well-formed hash gets past the guard and fails at the open "
          "instead -- the path itself is not visible here, see the note)")


def test_leading_zero_hash_is_a_brick():
    """A hash that begins with a zero byte is still a brick.

    "No brick" is ALL-ZERO, so every reader of the field must scan all 32
    bytes. One image in 256 has a sha256 beginning 0x00, and a reader that
    tests brick[0] sees those as brickless -- which is not a relaxation, it
    is the opposite: nwcheck.c's BIND loop refused them as NW_E_BINDIDX,
    naming the bind table for a plan whose bind table was correct, so the
    machine would not boot until the brick's CONTENTS changed.

    Phase 3 converted the unit loop in that function and left the bind loop
    in the same function reading brick[0]. The CBMC caller proof asserted
    brick[0] as well, so it PINNED the defect: fixing the checker turned
    `make proof` red. Both found by `tcb-review`. Every site now goes
    through nw_unit_has_brick() in blob.h, which is why the fix is one
    function rather than a rule to remember at each new reader.

    THE PAIRING: the all-0xde plan must be accepted too. Alone, "the 00..
    plan is accepted" is satisfied by a checker that accepts everything and
    by one that never reached the bind rule -- so the same plan with a
    brickless unit must still be REFUSED, which is the third case here.
    All three run through the real baker; nothing is hand-crafted, because
    the defect was reachable from a plan anyone could write."""
    NB = int(blob_h("NW_BRICK_HASH"))
    # ONE ACCEPTANCE CASE PER BYTE POSITION, and it has to be ACCEPTANCE.
    #
    # The crafted-blob enumeration in test_checker_rejects_crafted_fields
    # pins the UNIT loop and cannot reach the bind loop at all: those cases
    # clear NEWNS to force a rejection, so nw_check returns NW_E_BRICKNS
    # from the unit loop and never gets as far as the binds. The round-1
    # HIGH was in the BIND loop and it made the checker over-REJECT --
    # which no rejection test can see, and no acceptance POSTCONDITION
    # either, so the CBMC harness is blind to it by construction
    # (proofs/README.md). A legal plan that must be ACCEPTED is the only
    # shape that catches it.
    #
    # `tcb-review` measured the gap left after the first fix: a bind-site
    # reader of brick[31] left the whole suite AND the proof green -- one
    # image in 256 again, same message naming a correct bind table. The two
    # hashes here were de*32 and 00+de*31, 31 nonzero bytes each, so even
    # the case named for byte 0 survived a reader that skipped byte 0.
    # Enumerated now, so there is no position left to be the unpinned one.
    cases = [("de" * NB, 0, "a dense hash with no zero byte")]
    cases += [("00" * k + "01" + "00" * (NB - k - 1), 0,
               f"a hash nonzero only at byte {k}") for k in range(NB)]
    for brick, want, what in cases:
        city = f"{WORK}/lz-{brick}.city"
        open(city, "w").write(
            f"house lz /bin/true kind=oneshot lids=newns,seccomp "
            f"brick={brick} layer=l-lz bind=/etc\n")
        b = run(["python3", CC, "--city", city, "--out", f"{WORK}/lz.blob"])
        expect(b.returncode == 0, f"bake {what}\n{b.out}{b.err}")
        r = run([f"{BIN}/nw-check", f"{WORK}/lz.blob"])
        expect(r.returncode == want,
               f"nw-check rejected {what}: every reader of brick[] must "
               f"scan all {int(blob_h('NW_BRICK_HASH'))} bytes\n{r.out}{r.err}")

    # The other side of the rule, so the two acceptances above cannot be
    # satisfied by a checker that stopped enforcing it. The baker refuses
    # first, which is where this one is pinned.
    city = f"{WORK}/lz-none.city"
    open(city, "w").write(
        "house lz /bin/true kind=oneshot lids=seccomp bind=/etc\n")
    b = run(["python3", CC, "--city", city, "--out", f"{WORK}/lz-none.blob"])
    expect(b.returncode != 0, "a bind with no brick should fail the bake")
    expect("bind= without brick=" in (b.out + b.err),
           f"wrong reason for a bind without a brick\n{b.out}{b.err}")
    # THE OTHER END OF THE FIELD, and the one value the "2^256 values all
    # name a file" argument does not cover: all-zero is how the blob spells
    # NO brick, so a plan that writes 64 zeros declares a brick and gets a
    # house on the machine root. It baked, validated `OK units=1`, and booted
    # with no `lid brick` line -- invariant 6's "the plan lying". `tcb-review`.
    #
    # Pinned at the BAKER because nothing else can pin it: by the time the
    # blob exists, 32 zero bytes IS the no-brick encoding and no checker can
    # tell the two apart. The reason string is asserted, not just the exit
    # code -- folding this into the shape check produced `must be 64 hex
    # characters ... not a path` for a value that is exactly that, which is
    # a true rejection under a false reason.
    city = f"{WORK}/lz-zero.city"
    open(city, "w").write(
        f"house lz /bin/true kind=oneshot lids=newns,seccomp "
        f"brick={'0' * int(blob_h('NW_BRICK_HASH')) * 2} layer=l-zero\n")
    b = run(["python3", CC, "--city", city, "--out", f"{WORK}/lz-zero.blob"])
    expect(b.returncode != 0, "an all-zero brick hash should fail the bake")
    expect("all zeros" in (b.out + b.err) and "machine root" in (b.out + b.err),
           f"wrong reason for an all-zero brick hash\n{b.out}{b.err}")
    print(f"ok leading-zero-hash-is-a-brick (accepted with a bind at every "
          f"one of the {NB} byte positions plus a dense hash -- the bind "
          f"loop over-rejects, which only acceptance can see; a bind with "
          f"no brick refused, and the all-zero hash refused at the baker)")


def test_baker_refuses_bad_layers():
    """Every layer refusal the baker makes, with the reason asserted.

    NONE OF THEM WAS PINNED. `control` replaced all three -- the
    brick-needs-layer refusal, the layer-needs-brick refusal, and the
    `_is_layer_id` guard -- with `pass`, and `make test` stayed green.
    `nw-check` caught the resulting blobs, so the two-place design held,
    but plan.md's rule is that both places enforce it and combined with
    the pairing gap BOTH could have been deleted unnoticed.

    The reason is asserted, not the exit code: a baker that exits nonzero
    for a different complaint is not proof it refused the thing meant.
    test_brick_needs_newns is the template."""
    brick = "cd" * int(blob_h("NW_BRICK_HASH"))
    city = f"{WORK}/badlayer.city"
    cases = [
        (f"house a /bin/brick kind=oneshot lids=newns brick={brick}",
         "brick= needs layer=", "a brick with no layer"),
        (f"house a /bin/true kind=oneshot lids=none layer=orphan",
         "layer= without brick=", "a layer with no brick"),
        (f"house a /bin/brick kind=oneshot lids=newns brick={brick} "
         f"layer=../../etc", "must be a name", "a path-shaped layer id"),
        (f"house a /bin/brick kind=oneshot lids=newns brick={brick} "
         f"layer={'x' * int(blob_h('NW_NAME_LEN'))}", "must be a name",
         "a layer id that fills the field with no room for the NUL"),
        (f"house a /bin/brick kind=oneshot lids=newns brick={brick} "
         f"layer=dup\nhouse b /bin/brick kind=oneshot lids=newns "
         f"brick={brick} layer=dup", "already used by house",
         "two houses sharing one layer id"),
    ]
    for line, reason, what in cases:
        open(city, "w").write(line + "\n")
        p = run(["python3", CC, "--city", city, "--out", f"{WORK}/bl.blob"])
        expect(p.returncode != 0, f"baker accepted {what}\n{p.out}{p.err}")
        expect(reason in (p.out + p.err),
               f"wrong reason for {what}: expected {reason!r}\n"
               f"{p.out}{p.err}")
    # The pairing, from the accepting side, or every refusal above is
    # satisfied by a baker that refuses every plan with a layer in it.
    open(city, "w").write(
        f"house a /bin/brick kind=oneshot lids=newns brick={brick} "
        f"layer=fine\nhouse b /bin/brick kind=oneshot lids=newns "
        f"brick={brick} layer=also-fine\n")
    p = run(["python3", CC, "--city", city, "--out", f"{WORK}/bl.blob"])
    expect(p.returncode == 0,
           f"the baker refused a legal pair of layered houses\n"
           f"{p.out}{p.err}")
    print("ok baker-refuses-bad-layers (no layer, no brick, a path, an "
          "unterminated id and a shared id, each by its own reason; two "
          "distinct layers still accepted)")


def test_brick_needs_newns():
    """A brick is a root, and pivoting into one without a private mount
    namespace would repoint the machine's. The baker refuses rather than
    adding the lid on the plan's behalf, and nw-check refuses independently
    -- checked here against a blob the baker would never emit."""
    city = f"{WORK}/brick-nons.city"
    open(city, "w").write(
        f"house solo /bin/brick kind=oneshot lids=seccomp brick=aa00bb11cc22dd33ee44ff5566778899aabbccddeeff00112233445566778899 layer=l-ns\n")
    p = run(["python3", CC, "--city", city, "--out", f"{WORK}/nope.blob"])
    expect(p.returncode != 0, "brick without newns should fail the bake")
    expect("needs lids=...,newns" in (p.out + p.err), f"reason\n{p.out}{p.err}")

    # Same rule, enforced independently in the TCB: clear the NEWNS bit in a
    # sealed blob and repair the crc, exactly as a hand-rolled baker would.
    good = f"{WORK}/brick-ok.blob"
    open(city, "w").write(
        f"house solo /bin/brick kind=oneshot lids=newns,seccomp "
        f"brick=aa00bb11cc22dd33ee44ff5566778899aabbccddeeff00112233445566778899 layer=l-ns\n")
    p = run(["python3", CC, "--city", city, "--out", good])
    expect(p.returncode == 0, f"bake\n{p.out}{p.err}")
    d = bytearray(open(good, "rb").read())
    # DERIVED, not written. This was `20 + 32 + 128 + 96 + 2` and the 96
    # was `brick`, which phase 3 made 32 -- so the offset walked off the
    # end of the blob and the test crashed with an IndexError instead of
    # failing. blob.h's own NW_AT asserts pin this in C; nothing pinned the
    # Python copy, which is invariant 3's drift class in the harness.
    # Through unit_layout() now -- this WAS that same hand-written sum,
    # and adding `layer` to the unit moved the trailer 32 bytes without
    # moving this line. It failed as "nw-check must reject a brick without
    # NEWNS", an offset error wearing a rule violation's message, which is
    # a true-looking failure about the wrong thing.
    lids_off = 20 + unit_layout()["lids"]
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
        f"brick={brick} layer=l-adv\n"
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
        f"brick={brick} layer=l-ll bind={shared}\n")
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
    # DENIED write: the house's own root.
    #
    # THE REASON, NOT JUST "denied", AND THE REASON IS NOW THE FINDING.
    # This asserted `startswith("denied")`, which cannot separate
    # denied(30) -- EROFS, a root that is a bare read-only image -- from
    # denied(13) -- EACCES, Landlock refusing a root that IS writable.
    # Writable areas made every brick house's root an overlay, and
    # lid_landlock() runs after the brick pivot, so a landlock house now
    # gets a layer it cannot write. The test would pass before and after
    # for DIFFERENT reasons, which harness.md says to report rather than
    # re-baseline.
    #
    # So this machine cannot settle it -- Landlock is absent here and
    # this test skips -- but the FIRST machine that runs it will say
    # which, in the ok line, instead of printing the same word for two
    # opposite situations. See CLAUDE.md invariant 6: the resolution is a
    # design decision and is not taken.
    # RESOLVED 2026-09-12, and the assertion reverses. It required
    # `wr_root` to be denied, and the first machine with both Landlock
    # and erofs reported `denied(13)` -- EACCES from the lid, not
    # `denied(30)` EROFS from the image -- which is the contradiction
    # confirmed live: a writable layer the house could not write.
    #
    # The lid grants write beneath the root now, and invariant 6 claims
    # less. So the three things below are what the lid IS, and each is
    # load-bearing:
    #
    #   1. writing a file the brick already contains SUCCEEDS -- the
    #      layer is writable, which is what the grant bought;
    #   2. a DEVICE NODE at the root is refused -- the MAKE_ rights stay
    #      withheld, which is what the lid still provides;
    #   3. creating a plain file is refused at the root and allowed in a
    #      declared bind -- the pair that shows the scoping still
    #      DISCRIMINATES. Without 3, a lid that granted everything would
    #      satisfy 1 and a lid that granted nothing would satisfy 2.
    #
    # MAKE_REG is one of the withheld rights, so 3's root half also means
    # a landlock house cannot create files in its layer, only modify what
    # it shipped with. That is narrower than a plain brick house and it
    # is deliberate; see the note in nwsup.c.
    wrx = field("wr_existing").get("sealed", "")
    expect(wrx.startswith("ok"),
           f"a landlock house could not write a file its brick already "
           f"contains: wr_existing={wrx}. The lid grants write beneath "
           f"the root since 2026-09-12; if this is denied(13) the grant "
           f"did not take, and if denied(30) the layer did not mount"
           f"\n{out}")
    mk = field("mknod_root").get("sealed", "")
    expect(mk.startswith("denied"),
           f"a landlock house created a DEVICE NODE at its root: "
           f"mknod_root={mk}. The MAKE_ rights are what the lid still "
           f"withholds, and without them it grants everything the "
           f"filesystem would have\n{out}")
    wr = field("wr_root").get("sealed", "")
    mkb = field("mk_bind").get("sealed", "")
    expect(wr.startswith("denied") and mkb.startswith("ok"),
           f"the lid no longer DISCRIMINATES: creating a file must be "
           f"refused at the root (MAKE_REG withheld) and allowed in a "
           f"declared bind. Got wr_root={wr} mk_bind={mkb}\n{out}")
    print(f"ok landlock-confines (ABI {abi}; wr_existing={wrx} into the "
          f"layer, mknod_root={mk}, and create refused at the root "
          f"({wr}) but allowed in a bind ({mkb}))")


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
                '    if (field_dup(&t, u, 0,\n'
                '                  offsetof(struct nw_unit, name)))\n'
                '        return -2;\n'
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
                         ("NW_NAME_LEN", "NW_PATH_LEN", "NW_BRICK_HASH"))
    HDR = 20
    USZ = unit_layout()["_size"]

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
                         ("NW_NAME_LEN", "NW_PATH_LEN", "NW_BRICK_HASH"))
    HDR = 20
    KIND_OFF = HDR + unit_layout()["kind"]    # kind, then budget, lids, _pad
    LIDS_OFF = KIND_OFF + 2                   # lids is the third byte of the trailer

    city = f"{WORK}/crafted.city"
    brick = "de" * 32   # a hash, not a path (phase 3); never booted, only checked
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
        f"brick={brick} layer=l-c{i:02d}\n" for i in range(NUNITS)))
    p = run(["python3", CC, "--city", city, "--out", good])
    expect(p.returncode == 0, f"bake\n{p.out}{p.err}")
    base = bytearray(open(good, "rb").read())
    USZ = unit_layout()["_size"]
    KIND_OFF += VICTIM * USZ
    LIDS_OFF += VICTIM * USZ
    expect(len(base) == HDR + NUNITS * USZ,
           f"layout: {len(base)} bytes for {NUNITS} units of {USZ}")
    expect(base[KIND_OFF] == 0 and base[LIDS_OFF] == (1 | 4),
           f"kind/lids are not where the layout says for unit {VICTIM}: "
           f"{base[KIND_OFF]} {base[LIDS_OFF]}")

    def craft(why, edits):
        # Every case is a plain list of (offset, byte). There WAS a setup
        # branch here keyed on `why.startswith("dirtyblank")`, and when the
        # cases it served were deleted in phase 3 it became a branch nothing
        # could enter -- while `hash-tail-only`, written to depend on
        # something like it, silently got the unmodified brick instead and
        # its control passed. A setup step selected by matching a test's
        # NAME is invisible when the name changes; an edit list is not.
        d = bytearray(base)
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
    BRICK_OFF = HDR + VICTIM * USZ + unit_layout()["brick"]
    LAYER_OFF = HDR + VICTIM * USZ + unit_layout()["layer"]
    LAYERW = int(blob_h("NW_NAME_LEN"))
    cases = ([
        # THE TWO "dirty blank" CASES ARE GONE, and this is what replaced
        # them. They asserted that a blank brick is zero to the field
        # width, because a NUL-terminated path left an unvalidated tail and
        # an old blob's garbage could be given meaning by a newer checker.
        # A 32-byte hash has no tail: every bit is significant, and a
        # nonzero byte does not mean "blank with garbage", it means a
        # different hash. The input class went, so the check went with it.
        #
        # What still matters, and is sharper: "no brick" is ALL-ZERO, so
        # the checker must look at EVERY byte. A `has_brick` that tested
        # brick[0] alone reads any hash beginning with a zero byte -- one in
        # 256 -- as "no brick", silently starting a house on the machine
        # root that the plan says is in a brick.
        #
        # ONE CASE PER BYTE POSITION, and that is not thoroughness for its
        # own sake. A single `hash-tail-only` case pinned byte 31 and
        # NOTHING ELSE: `control` mutated the shared predicate to read only
        # byte 31 -- the exact mirror of the brick[0] defect this all came
        # from, one byte over -- and the suite AND the proof both stayed
        # green, because every brick any test used had byte 31 nonzero.
        # Pinning one position is what produced the bug twice, so the
        # position is enumerated out of existence rather than chosen.
    ] + [
        (f"hash-only-byte{k}",
         [(BRICK_OFF + j, 0) for j in range(BRICK) if j != k]
         + [(BRICK_OFF + k, 1), (LIDS_OFF, 1)],
         "brick without NEWNS lid",
         f"a brick whose hash is nonzero only at byte {k}")
        for k in range(BRICK)
    ] + [
        # A LAYER FIELD WITH A DIRTY TAIL. layer[0] is zero, so the unit
        # declares no layer -- and a byte further in is garbage an older
        # blob could carry. An unvalidated field cannot be given meaning
        # later, which is the same argument that kept the rule on `name`
        # and `exec_path` when phase 3 lifted it off `brick`. Found by the
        # coverage floor: the branch existed and nothing reached it.
        # TWO HOUSES ON ONE LAYER. The baker refuses this by name, so no
        # blob it emits reaches the checker's duplicate pass -- which is
        # the arrangement plan.md forbids relying on, and the coverage
        # floor said so: NW_E_LAYERDUP was written and unreachable.
        # Crafted here by copying unit 0's id over the victim's.
        #
        # What it prevents: two houses sharing one upperdir and one
        # workdir, each appending to the other's files, while the kernel
        # logs "accessing files from both mounts will result in undefined
        # behavior" to a channel nothing reads and the city prints
        # `closed houses_reaped=2 orphans=0`. `tcb-review` measured it.
        ("layer-duplicate",
         [(LAYER_OFF + k, 0) for k in range(LAYERW)]
         + [(LAYER_OFF + j, ord(c)) for j, c in enumerate("l-c00")],
         "duplicate layer id",
         "two houses declaring the same layer id"),
        # THE PAIRING, BOTH DIRECTIONS. `drift` deleted
        # `if (has_layer != has_brick) return NW_E_LAYERPAIR;` from the TCB
        # and the whole suite stayed green at the 99% floor -- nothing
        # anywhere asserted the string. A brick with no layer is a house
        # whose writes vanish at exit while the plan says it has data; a
        # layer with no brick is an area nothing mounts. Neither errors at
        # runtime, which is why the rule is structural and why it needs a
        # crafted case rather than a plan (the baker refuses both, so no
        # blob it emits can reach this code).
        ("layer-without-brick",
         [(BRICK_OFF + k, 0) for k in range(BRICK)],
         "layer and brick must come together",
         "a layer on a unit with no brick"),
        ("brick-without-layer",
         [(LAYER_OFF + k, 0) for k in range(LAYERW)],
         "layer and brick must come together",
         "a brick on a unit with no layer"),
        # A LAYER ID THAT IS A PATH. nw-sup composes
        # NW_LAYER_DIR "/" <id> "/" upper, so a separator in the id is the
        # traversal class the brick hash was made immune to -- and unlike
        # the brick, a layer id IS text all the way through. The closed
        # alphabet is what makes it safe, and this is the case that pins
        # the alphabet rather than the pairing.
        ("layer-is-a-path",
         [(LAYER_OFF + k, 0) for k in range(LAYERW)]
         + [(LAYER_OFF, ord("/")), (LAYER_OFF + 1, ord("x"))],
         "layer id", "a layer id with a path separator in it"),
        ("layer-dirty-tail",
         [(LAYER_OFF + k, 0) for k in range(LAYERW)]
         + [(LAYER_OFF + 5, 1)], "layer id",
         "a layer field that is blank but not zero to its width"),
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
    ])
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


def test_leading_zero_hash_reaches_the_supervisor():
    """A house whose brick hash begins 0x00 must still be given its brick.

    THE CHECKER IS NOT THE ONLY READER. nwspawn.c decides what goes into
    NW_BRICK, and it asked the same "does this unit have a brick" question
    -- so the round-1 defect had a third site, and that one decides
    whether a house boots in its brick or on the machine root. It is
    behind the shared predicate now, but nothing BOOTED a leading-zero
    hash: `control` open-coded that site back to brick[0] and the whole
    suite stayed green, because no brick image in the tree happened to
    hash to a leading zero. A 255-in-256 pass, seeded by the build.

    Deterministic here because the hash need not name a real image. The
    baker accepts any 64 hex characters that are not all zero, so a
    00-leading hash that matches nothing is a legal plan, and what it
    proves is the handoff rather than the mount:

      correct   -- NW_BRICK is set, nw-sup composes the path, the image is
                   not there, the house DIES at `open brick image`
      brick[0]  -- NW_BRICK is "", nw-sup sees no brick, and the house
                   RUNS, unconfined, on the machine root, exiting 0

    PAIRED, and the pairing is the whole test: asserting the death alone
    is satisfied by a city that never booted. So the probe's own line must
    be ABSENT and the death must be PRESENT, and a second house with a
    dense hash must fail the same way -- otherwise a supervisor that
    refuses every brick would pass."""
    NB = int(blob_h("NW_BRICK_HASH"))
    city = f"{WORK}/lzboot.city"
    blob = f"{WORK}/lzboot.blob"
    open(city, "w").write(
        f"house zerolead {BIN}/unit-probe kind=oneshot "
        f"lids=newns brick={'00' + 'ab' * (NB - 1)} layer=l-zl\n"
        f"house denselead {BIN}/unit-probe kind=oneshot "
        f"lids=newns brick={'ab' * NB} layer=l-dl\n")
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    rc, out = boot(plan=blob, hold=900)
    expect(city_closed(rc, out), f"lzboot rc={rc}\n{out[-2000:]}")
    for name in ("zerolead", "denselead"):
        expect(f"house={name}" not in out,
               f"{name} RAN. Its plan declares a brick, so a house that "
               f"reaches its exec path is a house on the machine root "
               f"while the plan says it is in a brick\n{out[-2000:]}")
    expect(out.count("open brick image") >= 2,
           f"both houses must die composing a path to an image that is "
           f"not there -- without this the absences above are satisfied "
           f"by a city that never booted\n{out[-2000:]}")
    print("ok leading-zero-hash-reaches-the-supervisor (a 00-leading and "
          "a dense hash both reach nw-sup as NW_BRICK and both die at the "
          "open; neither house runs unbricked)")


def test_checker_rejects_crafted_binds():
    """Each bind rule in nw_check, crafted, with the reason asserted.

    NEITHER WAS PINNED BY ANYTHING IN make test. `control` deleted the
    bind-implies-brick rule and the suite was green; deleted the
    `b[i].unit >= h->n_units` BOUNDS CHECK -- a TCB out-of-bounds read
    guard -- and the suite was green. `grep` for `bind unit index` across
    the suite returned one hit, in a docstring. The CBMC harness catches
    both, but `make proof` is not part of `make test` and is tens of
    minutes, so a change deleting either line ships green.

    Crafted rather than baked because the baker refuses both at bake time,
    which is the arrangement plan.md forbids relying on: a blob can arrive
    from anywhere and the runtime rule has to be in nwcheck.c too.

    The out-of-range case asserts the REASON, not just a non-zero exit.
    With the bounds check gone, nw_check reads u[0xFFFF] -- a wild pointer
    -- and whatever happens then (a crash, a garbage verdict) will not
    print `bind unit index`, so the reason assertion is what makes the
    case bite rather than the exit code."""
    NAME, PATH, BRICK = (int(blob_h(x)) for x in
                         ("NW_NAME_LEN", "NW_PATH_LEN", "NW_BRICK_HASH"))
    HDR, USZ = 20, unit_layout()["_size"]
    BSZ = 2 + PATH
    city = f"{WORK}/cbind.city"
    good = f"{WORK}/cbind-ok.blob"
    # Unit 0 has a brick and the bind; unit 1 has none. Both legal.
    open(city, "w").write(
        f"house b0 /bin/true kind=oneshot lids=newns,seccomp "
        f"brick={'ab' * BRICK} layer=l-b0 bind=/etc\n"
        f"house b1 /bin/true kind=oneshot lids=seccomp\n")
    p = run(["python3", CC, "--city", city, "--out", good])
    expect(p.returncode == 0, f"bake\n{p.out}{p.err}")
    base = bytearray(open(good, "rb").read())
    NUNITS, NBINDS = 2, 1
    expect(len(base) == HDR + NUNITS * USZ + NBINDS * BSZ,
           f"layout: {len(base)} bytes")
    BIND0 = HDR + NUNITS * USZ
    expect(struct.unpack_from("<H", base, BIND0)[0] == 0,
           "the bind does not name unit 0 where the layout says")

    def craft(why, edits):
        d = bytearray(base)
        for off, val in edits:
            d[off] = val
        d[16:20] = b"\x00\x00\x00\x00"
        d[16:20] = struct.pack("<I", zlib.crc32(bytes(d)) & 0xFFFFFFFF)
        path = f"{WORK}/cbind-{why}.blob"
        open(path, "wb").write(bytes(d))
        return path

    cases = [
        # Out of range. u[0xFFFF] is a wild read without the bounds check.
        ("unit-oob", [(BIND0, 0xFF), (BIND0 + 1, 0xFF)],
         "a bind naming a unit that does not exist"),
        # In range, but that unit has no brick -- so there is no root to
        # bind into. Repointed at unit 1, whose brick is all-zero.
        ("unit-brickless", [(BIND0, 1), (BIND0 + 1, 0)],
         "a bind on a unit with no brick"),
    ]
    for why, edits, what in cases:
        r = run([f"{BIN}/nw-check", craft(why, edits)])
        expect(r.returncode != 0, f"nw-check accepted {what}\n{r.out}{r.err}")
        expect("bind unit index" in (r.out + r.err),
               f"wrong reason for {what} -- a rejection for another reason "
               f"would satisfy a returncode check and pin nothing"
               f"\n{r.out}{r.err}")

    # The pairing: unmodified, both rules satisfied, must be ACCEPTED --
    # otherwise both rejections are satisfied by a checker that refuses
    # every blob carrying a bind.
    r = run([f"{BIN}/nw-check", good])
    expect(r.returncode == 0,
           f"nw-check rejected a legal plan with a bind\n{r.out}{r.err}")
    print("ok checker-rejects-crafted-binds (an out-of-range unit and a "
          "bind on a brickless unit, each refused naming the bind index; "
          "the legal plan with a bind still accepted)")


LAYOUT_DECL = re.compile(
    r"^\s*(?:NW_AT|NW_EXTENT|NW_TYPE|NW_ARR_TYPE)\s*\([^)]*\)\s*;"
    r"|^\s*_Static_assert\s*\(\s*sizeof\s*\(\s*struct\s+nw_\w+\s*\)[^;]*;"
    # AND THE SIZE MACRO ITSELF. nw_bind's size assert carries a literal
    # (== 130) so its size is hashed; nw_unit's reads == NW_UNIT_SIZE, a
    # macro reference, so redefining NW_UNIT_SIZE changed the wire format
    # while leaving every hashed line byte-identical. Measured by `drift`:
    # a field appended after _pad, the baker packing it, magic untouched,
    # and the ledger said `ok magic-moves-with-layout`. The blind spot was
    # exactly the trailing edge of the struct.
    r"|^\s*#\s*define\s+NW_UNIT_SIZE\b.*$",
    re.M)


UNIT_AT = re.compile(r"^\s*NW_AT\(nw_unit,\s*(\w+),\s*(\d+)\)\s*;", re.M)


def unit_layout():
    """struct nw_unit's member offsets and its size, from blob.h's own
    NW_AT declarations.

    ONE COPY. This arithmetic was written out as `NAME + PATH + BRICK + 4`
    at six call sites, so adding `layer` to the unit meant finding all six
    -- and the one that was missed produced a crafted blob whose lids byte
    was 32 bytes off, failing as "nw-check must reject a brick without
    NEWNS" rather than as an offset error. Derived from the declarations
    the C compiler already checks, so the next field cannot be missed.

    The size is offset(_pad) + 1 because _pad is the last member by
    construction and blob.h asserts the struct is exactly NW_UNIT_SIZE."""
    # FROM THE STAGE, NOT THE SOURCE TREE -- the same rule blob_h() states
    # and for the same reason. The binaries under test were built from
    # {STAGE}/src/blob.h; reading ROOT/blob.h after a bare `make` gives
    # offsets the staged nw-check does not use. `control` desynchronised
    # the two by swapping brick's and layer's declared offsets in the
    # source only and got `FAIL: nw-check accepted a layer id with a path
    # separator in it` -- an offset error wearing a rule violation's
    # message, which is the exact sentence this helper's docstring was
    # written to retire, surviving at the source/stage boundary.
    at = {m.group(1): int(m.group(2))
          for m in UNIT_AT.finditer(
              open(os.path.join(STAGE, "src", "blob.h")).read())}
    for want in ("name", "exec_path", "brick", "layer", "kind", "_pad"):
        expect(want in at,
               f"blob.h declares no NW_AT(nw_unit, {want}, ...) -- the "
               f"layout parse has stopped matching and every crafted-blob "
               f"test below would be reading the wrong bytes")
    # FROM NW_UNIT_SIZE, not from _pad + 1. The old form assumed _pad is
    # the last member and nothing pins that; with a field appended after
    # it, unit_layout() returned 228 while sizeof was 229. It degraded
    # loudly -- four tests red at once -- but every message was a size
    # error wearing a layout bug's clothes, which is the sentence this
    # helper exists to retire.
    src = open(os.path.join(STAGE, "src", "blob.h")).read()
    m = re.search(r"^\s*#\s*define\s+NW_UNIT_SIZE\s+\((.*)\)\s*$",
                  src, re.M)
    expect(m is not None, "blob.h has no NW_UNIT_SIZE as a parenthesised "
                          "expression; the size derivation has stopped "
                          "matching")
    consts = {k: int(blob_h(k)) for k in
              ("NW_NAME_LEN", "NW_PATH_LEN", "NW_BRICK_HASH")}
    at["_size"] = eval(m.group(1), {"__builtins__": {}}, consts)
    return at


def _layout_signature():
    """A hash over blob.h's declared layout: every NW_AT / NW_EXTENT /
    NW_TYPE / NW_ARR_TYPE and every struct-size assert, whitespace
    normalised and sorted. Comments and prose do not move it; an offset, a
    width, a member type or a struct size does."""
    src = open(os.path.join(ROOT, "blob.h")).read()
    decls = sorted(" ".join(m.group(0).split())
                   for m in LAYOUT_DECL.finditer(src))
    expect(len(decls) > 8,
           f"only {len(decls)} layout declarations found in blob.h -- the "
           f"pattern has stopped matching and this test would pass by "
           f"hashing almost nothing")
    return hashlib.sha256("\n".join(decls).encode()).hexdigest(), decls


def test_magic_moves_with_the_layout():
    """NW_MAGIC and the layout must move together, in BOTH directions.

    This is the rule the 05 -> 06 bump was made for and nothing enforced
    it. `control` demonstrated both failures against a green suite:

      - Retype `brick` from uint8_t[32] to char[32] -- a TCB field's
        declared MEANING changed, same bytes -- update blob.h's own
        NW_ARR_TYPE so the assert agrees (which is the obvious response to
        the build error, and is a second hand-written copy of the
        declaration in the same file), add the cast in nwspawn.c that a
        retyper would add, and `make test` is green with NW_MAGIC
        untouched.
      - Bump NWPLAN07 to NWPLAN08 with no layout change at all: green.
        test_old_magic_is_refused_as_magic derives `old = magic - 1`, so it
        SLIDES -- after the bump it certifies the refusal of the magic that
        was current a moment before. It passes for every value of the
        constant and every layout.

    A `_Static_assert` cannot pin this: it lives in the file the retyper is
    editing, and the fix for a failing one is to edit it. So the pin is a
    STORED ARTIFACT -- a ledger of (magic, layout signature) pairs checked
    into the tree, which a layout change cannot silently satisfy because
    the previous rows are already written down.

    Both directions are checked. A changed layout under a known magic is
    the defect the bump exists to prevent; an unchanged layout under a new
    magic is a version number that means nothing, which is the same lie
    from the other side."""
    magic = blob_h("NW_MAGIC").strip('"')
    sig, decls = _layout_signature()
    path = os.path.join(ROOT, "plan-formats.txt")
    rows = {}
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m, h = line.split()
        rows[m] = h

    if magic in rows and rows[magic] != sig:
        expect(False,
               f"THE LAYOUT MOVED AND {magic} DID NOT.\n"
               f"  plan-formats.txt records {magic} = {rows[magic][:16]}\n"
               f"  blob.h now declares        {sig[:16]}\n"
               f"A blob of the old layout and one of the new both claim to "
               f"be {magic}, and only the size check tells them apart -- "
               f"which fails the moment a change keeps the size, as a "
               f"retype does. Bump NW_MAGIC and add a row; do not edit the "
               f"existing one.\n"
               f"  layout now: " + "\n              ".join(decls))
    for other, h in rows.items():
        if h == sig and other != magic:
            expect(False,
                   f"{magic} IS A NEW NAME FOR THE {other} LAYOUT.\n"
                   f"The signature is identical, so nothing about a plan "
                   f"has changed and every {other} blob is byte-compatible. "
                   f"A version bump that means nothing teaches a reader "
                   f"that the version means nothing.")
    expect(magic in rows,
           f"{magic} IS NOT IN plan-formats.txt. A new magic has to be "
           f"recorded or the ledger stops covering the current format -- "
           f"and the next layout change under it goes unnoticed.\n"
           f"  append this line:\n    {magic}  {sig}")
    print(f"ok magic-moves-with-layout ({magic} matches its recorded "
          f"signature over {len(decls)} declarations; "
          f"{len(rows)} format(s) in the ledger)")


def test_old_magic_is_refused_as_magic():
    """An old-format blob must be refused for its MAGIC, not its size.

    This is the whole reason NW_MAGIC moved 05 -> 06 on 2026-09-11, and
    nothing in the suite pinned it. `drift` and `claims` each verified it
    by hand, in separate scratch trees, which is how a property comes to
    be believed without being tested.

    The failure it guards against is a diagnosis, not a rejection. Before
    the bump, a blob baked by the previous nw-cc was refused -- correctly
    -- as NW_E_SIZE, because the layout had changed and the magic had not.
    An operator reads "size" as a truncated or corrupt file and goes
    hunting for a bad copy; the truth was "this slot holds a plan the
    previous baker made". One channel carrying two meanings, separated
    only by which integer, which is bug 9's shape.

    So the assertion is on the reason string. `returncode != 0` is
    satisfied by every rejection there is, including the one this test
    exists to distinguish itself from -- and the pairing is a blob of the
    RIGHT size with the wrong magic, so size cannot be what refuses it."""
    good = f"{WORK}/magic-good.blob"
    city = f"{WORK}/magic.city"
    open(city, "w").write("house m /bin/true kind=oneshot lids=none\n")
    b = run(["python3", CC, "--city", city, "--out", good])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")
    d = bytearray(open(good, "rb").read())

    magic = blob_h("NW_MAGIC").strip('"')
    expect(bytes(d[:8]) == magic.encode(),
           f"the baker did not write {magic}: {bytes(d[:8])!r}")

    # The previous magic, on a blob that is otherwise exactly right --
    # same length, same units, CRC repaired. Nothing but the magic is
    # wrong, so nothing but the magic can be the reason.
    old = magic[:-2] + f"{int(magic[-2:]) - 1:02d}"
    d[:8] = old.encode()
    d[16:20] = b"\x00\x00\x00\x00"
    d[16:20] = struct.pack("<I", zlib.crc32(bytes(d)) & 0xFFFFFFFF)
    stale = f"{WORK}/magic-old.blob"
    open(stale, "wb").write(bytes(d))
    expect(os.path.getsize(stale) == os.path.getsize(good),
           "the crafted old-magic blob is not the same length as the good "
           "one, so a size rejection would be ambiguous")

    r = run([f"{BIN}/nw-check", stale])
    expect(r.returncode != 0,
           f"nw-check accepted a blob carrying {old}\n{r.out}{r.err}")
    expect("magic" in (r.out + r.err).lower(),
           f"nw-check refused a {old} blob, but not for its magic -- the "
           f"diagnosis is the property being tested, and this is the "
           f"reading an operator would chase in the wrong direction:\n"
           f"{r.out}{r.err}")
    # And the whole point is that it is NOT a size complaint.
    expect("size" not in (r.out + r.err).lower(),
           f"nw-check blamed the size of a correctly-sized blob\n"
           f"{r.out}{r.err}")

    print(f"ok old-magic-refused-as-magic ({old} at the right length is "
          f"refused for its magic, not its size)")


def test_specs_are_checked():
    """Run the two specs. Until 2026-09-11 nothing ever did.

    `plan.als` and `Plan.tla` were the one part of this repository that
    could not be verified by running, and the honest consequence was
    supposed to be that nobody cited them as evidence. They were cited
    anyway, by their own comments: "Change one, change all four" sat
    directly above an expression that was wrong.

    Running them found, immediately, that NEITHER PARSED:

    * Alloy refused `run sealed for 8 House` -- "You must specify a
      scope for sig this/Brick". The file had never been executed by its
      own tool.
    * TLC refused `Houses == 1..N`, defined above `N == n`: "Unknown
      operator: `N'". Also never executed, and `Houses` was used by
      nothing.

    And then that the fd formula did not add. Alloy's `+` on Int is set
    union, so `8 + 2.mul[#House]` was the SET {8, 2*#House} and `sealed`
    compared it against 1024. Measured with 5 houses: `fdNeed[] = 18`
    has a counterexample, `fdNeed[] = (8 + 10)` holds.

    The limits both specs use are generated from blob.h, so the
    four-place drift of invariant 3 is now unrepresentable for these two
    rather than checked -- there is no second copy left to go stale.

    Controls, all run:
      * restore the `+` union form            -> FdArithmetic counterexample
      * NW_MAX_FDS = 16 in the generated limits -> Sealed counterexample
      * NW_MAX_FDS = 100 in blob.h            -> FdBudgetCovers violated
      * NW_MAX_UNITS = 128 in blob.h          -> tracked, 128 states, clean
    """
    jars = os.path.join(ROOT, "tools", "jars")
    tla, alloy = (os.path.join(jars, "tla2tools.jar"),
                  os.path.join(jars, "alloy.jar"))
    have_java = run(["sh", "-c", "command -v java"]).returncode == 0
    missing = [n for n, p in (("tla2tools.jar", tla), ("alloy.jar", alloy))
               if not os.path.exists(p)]
    if not have_java or missing:
        why = "java is not installed" if not have_java else \
              f"missing {', '.join(missing)} (see tools/jars/README.md)"
        skip("specs-are-checked",
             f"{why}; plan.als and Plan.tla are NOT verified in this run "
             f"and must not be cited as evidence")
        return

    import importlib.util as _il
    g = _il.spec_from_file_location(
        "genlim", os.path.join(ROOT, "tools", "gen-spec-limits.py"))
    gen = _il.module_from_spec(g)
    g.loader.exec_module(gen)
    lab = f"{WORK}/specs"
    subprocess.run(["rm", "-rf", lab], check=False)
    os.makedirs(lab, exist_ok=True)
    vals, als_path, cfg_path = gen.generate(out_dir=lab)

    # TLC. Static model: Next is UNCHANGED, so this evaluates the limit
    # arithmetic at every legal unit count rather than exploring behaviour.
    # generate() already wrote limits.als and Plan.cfg into lab.
    assert cfg_path == f"{lab}/Plan.cfg" and als_path == f"{lab}/limits.als"
    shutil.copy(os.path.join(ROOT, "Plan.tla"), f"{lab}/Plan.tla")
    # The generated config must actually name the invariants, or TLC
    # explores the state space and checks nothing. `control` deleted the
    # INVARIANTS block and this test still printed "invariant holds".
    cfg_text = open(cfg_path).read()
    # TypeOK was generated into the cfg and left out of this tuple, so
    # deleting it from the generator passed -- and TypeOK is the only
    # invariant that mentions `kind`, `BindNeed` or the range of `n`.
    # `control`. Read the names out of the generated file's own
    # INVARIANTS block instead of retyping them, and require the set.
    # ANCHOR ON THE KEYWORD'S OWN LINE. A bare split matches the word
    # inside a comment, and the generated cfg's header is prose about
    # what the INVARIANTS block names -- `control` added one sentence
    # mentioning it and the check read the CONSTANTS block as the
    # invariant list. TLC accepted that config and checked all four.
    cfg_lines = cfg_text.splitlines()
    anchor = [i for i, l in enumerate(cfg_lines) if l.strip() == "INVARIANTS"]
    expect(len(anchor) == 1,
           f"the generated Plan.cfg does not have exactly one INVARIANTS "
           f"line, so TLC checks nothing or this check reads the wrong "
           f"block:\n{cfg_text}")
    inv_block = [None, "\n".join(cfg_lines[anchor[0] + 1:])]
    # Bare identifiers only. Reading to EOF meant any trailing line --
    # including a comment TLC ignores -- joined the set and failed the
    # assertion under a message claiming an invariant had been dropped.
    # `control` appended `\\* end of generated config` and watched it fire
    # on a config TLC checks completely.
    # STOP AT THE FIRST NON-NAME, rather than reading to EOF. Keeping
    # only bare identifiers fixed the trailing-comment case and left the
    # shape: `control` appended a two-line `CHECK_DEADLOCK` / `FALSE`
    # and both joined the set, failing under a message claiming an
    # invariant had been dropped from a config TLC checks completely.
    # INVARIANTS is emitted last today, which is why this is a trap for
    # the next generator edit rather than a live defect.
    named = set()
    for l in inv_block[1].splitlines():
        t = l.strip()
        if not t:
            continue
        if not re.fullmatch(r"[A-Za-z_]\w*", t):
            break
        named.add(t)
    expect(named == {"FdBudgetCovers", "FdNeedAgrees", "LargestCityFits",
                     "TypeOK"},
           f"the generated Plan.cfg names invariants {sorted(named)}. "
           f"TLC checks exactly what is listed there and nothing else, "
           f"so one dropped from the generator is one checked never.")

    t = run(["java", "-cp", tla, "tlc2.TLC", "-config", "Plan.cfg",
             "Plan.tla"], cwd=lab)
    tout = t.out + t.err
    # Exit code first. A solver that failed to run is not a solver that
    # agreed with you.
    expect(t.returncode == 0,
           f"TLC exited {t.returncode}.\n{tout[-2000:]}")
    expect("Model checking completed. No error has been found." in tout,
           f"TLC rejected Plan.tla against blob.h's limits "
           f"({vals}).\n{tout[-2000:]}")
    m = re.search(r"(\d+) distinct states", tout)
    expect(m and int(m.group(1)) == vals["nwMaxUnits"],
           f"TLC explored {m.group(1) if m else '?'} states, expected one "
           f"per legal unit count ({vals['nwMaxUnits']}). If Init stopped "
           f"ranging over n, the invariant is being checked at one size "
           f"and the run says nothing about the others.\n{tout[-1200:]}")

    # MUST-FAIL PROBES FOR THE TLC SIDE. The Alloy checks have had these
    # since the jars landed; the TLC invariants had only hand-controls in
    # HISTORY, and `control` showed what that costs: `LargestCityFits ==
    # TRUE` in Plan.tla leaves the whole suite green, and the ok line
    # still reads "4 invariants incl. the boundary". An announcement, not
    # an effect -- which is the defect this project is named after.
    #
    # Each probe breaks ONE invariant's subject and lists ONLY that
    # invariant in the cfg, so a neutered predicate cannot be covered by
    # a sibling firing on the same mutation. A TLC run here is under a
    # second, measured, which is why there are four rather than a note
    # saying this would be nice.
    #
    # LargestCityFits is a CONSTANT invariant, so TLC evaluates it before
    # exploring and says "The invariant of X is equal to FALSE" rather
    # than "Invariant X is violated". Two different sentences for the
    # same outcome; accept either, per name.
    tla_src = open(os.path.join(ROOT, "Plan.tla")).read()
    tight = vals["nwReserved"] + 2 * vals["nwMaxUnits"] - 1
    for nm, cfg_sub, tla_sub in (
            ("TypeOK", None,
             ("kind = [i \\in 1..n |-> 0]", "kind = [i \\in 1..n |-> 2]")),
            # NOT 16. Derived: the honest predicate misses by exactly
            # one at Reserved + 2*MaxUnits - 1, while every weakening
            # `control` found -- `n <= MaxFds`, `Reserved + MaxUnits <=
            # MaxFds`, `MaxUnits <= MaxFds` -- still holds there. At 16
            # all of them are false, so the probe certified the
            # invariant's NAME and a green suite came back from a halved
            # boundary. Derived rather than typed so it tracks the
            # header; 135 today.
            ("FdBudgetCovers", ("MaxFds", tight), None),
            ("LargestCityFits", ("MaxFds", tight), None),
            ("FdNeedAgrees", None,
             ("FdNeed == Reserved + 2 * n", "FdNeed == Reserved + n")),
    ):
        d = f"{lab}/mustfail-tlc-{nm}"
        os.makedirs(d, exist_ok=True)
        tt = tla_src
        if tla_sub:
            src, dst = tla_sub
            # str.replace, not re.subn: the TLA+ replacement carries a
            # backslash (`\in`) and re reads that as a template escape.
            k = tt.count(src)
            expect(k == 1,
                   f"the {nm} probe found {k} occurrences of `{src}` in "
                   f"Plan.tla, expected one. Fix the pattern; do not "
                   f"delete the probe, it is the only thing showing this "
                   f"invariant can fail.")
            tt = tt.replace(src, dst)
        # Only this invariant, so no sibling can answer for it.
        ct = "\n".join(cfg_lines[:anchor[0] + 1]) + f"\n    {nm}\n"
        if cfg_sub:
            key, val = cfg_sub
            ct, k = re.subn(rf"^(\s*{key} = )\d+$", rf"\g<1>{val}", ct,
                            flags=re.M)
            expect(k == 1,
                   f"the {nm} probe rewrote {k} cfg constants named "
                   f"{key}, expected one -- the generated cfg changed "
                   f"shape; fix the pattern, not the probe.")
        open(f"{d}/Plan.cfg", "w").write(ct)
        open(f"{d}/Plan.tla", "w").write(tt)
        v = run(["java", "-cp", tla, "tlc2.TLC", "-config", "Plan.cfg",
                 "Plan.tla"], cwd=d)
        vt = v.out + v.err
        expect(f"Invariant {nm} is violated" in vt or
               f"The invariant of {nm} is equal to FALSE" in vt,
               f"TLC did NOT report {nm} when its subject was broken. "
               f"The invariant passes above without being able to fail, "
               f"so it is evidence of nothing -- `control` set "
               f"LargestCityFits to TRUE and the whole suite stayed "
               f"green.\n{vt[-1200:]}")

    # PIN THE BOUNDARY FROM ABOVE TOO. The must-fail probes fix MaxFds at
    # `tight` and the honest run uses the real NW_MAX_FDS, so ANY
    # predicate whose threshold falls between the two passes both: every
    # weakening is excluded (that was the point) and every STRENGTHENING
    # is admitted. `control` got a green suite from
    # `Reserved + 2 * MaxUnits < MaxFds`, from `... + 1 <= MaxFds`, and
    # from `Reserved + 3 * n <= MaxFds` -- the last dropping FdNeed
    # entirely, so FdNeedAgrees no longer constrains it.
    #
    # `<` for `<=` is the one that matters rather than merely erring
    # safe: Plan.tla calls FdBudgetCovers "the same claim as the
    # _Static_assert in blob.h", and blob.h writes `<=`. A header sitting
    # exactly on the boundary would be accepted by C and rejected by TLC,
    # and nothing noticed the disagreement.
    #
    # One more run at `tight + 1`, where the honest predicate holds by
    # exactly one, pins the threshold to a single value from both sides.
    # TLC is under a second.
    for nm in ("FdBudgetCovers", "LargestCityFits"):
        d = f"{lab}/musthold-tlc-{nm}"
        os.makedirs(d, exist_ok=True)
        ct = "\n".join(cfg_lines[:anchor[0] + 1]) + f"\n    {nm}\n"
        ct, k = re.subn(r"^(\s*MaxFds = )\d+$", rf"\g<1>{tight + 1}", ct,
                        flags=re.M)
        expect(k == 1,
               f"the {nm} must-hold probe rewrote {k} cfg constants named "
               f"MaxFds, expected one.")
        open(f"{d}/Plan.cfg", "w").write(ct)
        shutil.copy(os.path.join(ROOT, "Plan.tla"), f"{d}/Plan.tla")
        v = run(["java", "-cp", tla, "tlc2.TLC", "-config", "Plan.cfg",
                 "Plan.tla"], cwd=d)
        vt = v.out + v.err
        expect("Model checking completed. No error has been found." in vt,
               f"{nm} does NOT hold at MaxFds = {tight + 1}, where the "
               f"budget covers the largest legal city by exactly one. "
               f"Paired with the must-fail probe at {tight}, this pins "
               f"the threshold to one value; alone, each admits every "
               f"predicate on its own side of it. A `<` where blob.h "
               f"writes `<=` lands here.\n{vt[-1200:]}")

    shutil.copy(os.path.join(ROOT, "plan.als"), f"{lab}/plan.als")

    # THE Int BITWIDTH IS THE ONE LIMIT STILL WRITTEN IN plan.als, so it
    # is the one place the drift class survives. Alloy's signed n-bit Int
    # spans -2^(n-1) .. 2^(n-1)-1; if nwMaxFds does not fit, it wraps and
    # the failure surfaces as "Sealed has a counterexample" plus "the
    # model is vacuous" -- two messages that both blame the spec for a
    # scope problem. Measured: at NW_MAX_FDS 2048 both appear. That is
    # the wrong-diagnosis shape Plan.tla's ASSUME note is about, so catch
    # it here by name before Alloy gets the chance.
    # COMMAND LINES ONLY. `re.findall` over the whole file also reads the
    # PROSE: plan.als explains `but 12 Int` in a comment, so raising the
    # bitwidth in the three commands -- which this assertion's own message
    # tells you to do -- reported "commands do not agree: ['13','13','12',
    # '13']" when they agreed perfectly. Worse in the other direction:
    # deleting `but 12 Int` from the commands alone left the comment
    # satisfying this guard while Alloy ran at its default 4-bit Int, and
    # the failure arrived as "the spec disagrees with blob.h's limits" --
    # the exact wrong-diagnosis this assertion exists to prevent,
    # reproduced while it was in place and green. `control`.
    # COMMENTS ARE NOT COMMANDS, and a prefix match on a raw line cannot
    # tell the difference -- in a file that is mostly prose about `check
    # Sealed` and `run sealed`. `control` added a comment line beginning
    # "check Sealed is the one carrying..." and got "commands do not all
    # fix the same Int bitwidth: ['12','12','12'] over 4 commands", three
    # identical values reported as a disagreement. Worse, the same
    # predicate drives the probes' stripper below: a comment line
    # starting `check ` that also closes the block comment was deleted
    # from every probe copy, unterminating the comment, and the failure
    # read "the must-fail probe for Sealed did not solve" -- pointing at
    # the probe for a defect in plan.als.
    #
    # So decide it once, on the source with comments blanked out, and
    # carry the line NUMBERS so the stripper below drops exactly these.
    #
    # dashdash=True: this is Alloy, where `--` opens a line comment. A
    # `/*` written inside one blanked everything to the next `*/` and
    # hid commands below it (how many depends on where it is written,
    # which is why no count is given), and the suite then reported
    # Sealed as vacuous -- a
    # message whose every clause was false, on a spec Alloy accepts.
    #
    # THE PROBES ARE BUILT FROM THE BLANKED TEXT, not the raw. A probe
    # needs the code, never the prose, and building from raw meant every
    # comment in the file was a way to break a probe: a command sharing
    # a line with a `*/` took the `*/` with it when the line was dropped,
    # unterminating the comment in the probe copy only. There is nothing
    # left to unterminate now. `control` found both.
    als_raw = open(f"{lab}/plan.als").read().splitlines()
    als_bare = strip_c_comments("\n".join(als_raw), dashdash=True).splitlines()
    cmd_ix = [i for i, l in enumerate(als_bare)
              if l.lstrip().startswith(("check ", "run "))]
    cmds = [als_bare[i] for i in cmd_ix]
    bits = [m.group(1) for m in
            (re.search(r"but (\d+) Int", l) for l in cmds) if m]
    scopes = [m.group(1) for m in
              (re.search(r"\bfor (\d+)\b", l) for l in cmds) if m]
    expect(len(bits) == len(cmds) and len(set(bits)) == 1,
           f"plan.als's Alloy commands do not all fix the same Int "
           f"bitwidth: {bits} over {len(cmds)} commands")
    # THE SCOPE IS A LIMIT TOO, and it was pinned by nothing: `control`
    # set every command to `for 1` and the test passed while
    # `.claude/rules/plan.md` and HISTORY 39/41 say the results are
    # bounded "at scope 8". The must-fail probes made it worse by
    # hardcoding their own `for 8`, so they kept finding counterexamples
    # at a scope the real checks had stopped using.
    expect(len(scopes) == len(cmds) and len(set(scopes)) == 1,
           f"plan.als's Alloy commands do not all use the same scope: "
           f"{scopes} over {len(cmds)} commands")
    scope = int(scopes[0])
    # A FLOOR, not an equality. `scope == 8` was a fourth copy of the
    # number (plan.als x3 and here), and its message said "three briefs"
    # when one brief says it -- a count, in the one place CLAUDE.md
    # permits one, but inside a MESSAGE rather than a condition, so being
    # wrong failed nothing. `control`. The floor keeps the control that
    # matters -- `control` set every command to `for 1`, and at scope 1
    # `#binds` cannot exceed 1, so the binds probe could not find its
    # counterexample -- without giving the bound another place to be
    # edited. The consistency check above is what stops the commands
    # disagreeing; plan.als is the single source of the value.
    expect(scope >= 2,
           f"plan.als runs Alloy at scope {scope}. Below 2 the binds "
           f"must-fail probe cannot reach a counterexample (#binds "
           f"cannot exceed the scope), so the bind half of Sealed would "
           f"be certified by a probe that cannot fail.")
    have = int(bits[0])
    need = 2
    while (1 << (need - 1)) - 1 < vals["nwMaxFds"]:
        need += 1
    expect(have >= need,
           f"plan.als runs Alloy at {have}-bit Int, which spans up to "
           f"{(1 << (have - 1)) - 1}, but blob.h's NW_MAX_FDS is "
           f"{vals['nwMaxFds']} and needs at least {need} bits. Left "
           f"alone this wraps and reports itself as a counterexample to "
           f"Sealed -- the right problem with the wrong name. Raise the "
           f"bitwidth in plan.als, and measure: Alloy's cost scales "
           f"badly with it.")

    # Alloy. -Xss512m because the existential `run` can overflow the
    # default JVM stack at 12-bit Int -- intermittently, which is why the
    # flag is not optional: `claims` measured 3 of 6 unflagged runs
    # succeeding, and a passing run without it proves nothing.
    a = run(["java", "-Xss512m", "-jar", alloy, "exec", "-f", "plan.als"],
            cwd=lab)
    aout = a.out + a.err
    # Alloy prints a command's error ON THE COMMAND'S OWN LINE, so a
    # check that could not be solved still matches the verdict regex
    # whenever the error text contains SAT or UNSAT -- and the error text
    # is the spec's absolute path. `control` built a lab directory named
    # UNSAT and every assertion below passed on a run where `Sealed` was
    # never solved and Alloy exited 1. The exit code was sitting there
    # unread the whole time.
    expect(a.returncode == 0,
           f"Alloy exited {a.returncode}; a command did not solve.\n"
           f"{aout[-1500:]}")
    checks = re.findall(r"\d+\.\s+check\s+(\w+)\s+.*?(SAT|UNSAT)", aout)
    runs = re.findall(r"\d+\.\s+run\s+(\w+)\s+.*?(SAT|UNSAT)", aout)
    expect(len(checks) == 2 and len(runs) == 1,
           f"expected two checks and one run from plan.als, parsed "
           f"checks={checks} runs={runs}. A command that stopped being "
           f"executed is a check that stopped happening.\n{aout[-1500:]}")
    # BY NAME. The arity guard pins how many checks ran, not which:
    # `control` renamed `check Sealed` to a second `check FdArithmetic`
    # and the count stayed 2 while the check carrying the whole blob.h
    # budget claim stopped running.
    expect({n for n, _ in checks} == {"FdArithmetic", "Sealed"},
           f"plan.als ran checks {sorted(n for n, _ in checks)}, expected "
           f"FdArithmetic and Sealed.\n{aout[-1500:]}")
    # For a `check`, SAT means a counterexample was FOUND.
    for name, verdict in checks:
        expect(verdict == "UNSAT",
               f"Alloy found a counterexample to {name} in plan.als -- the "
               f"spec disagrees with blob.h's limits ({vals}).\n{aout[-1500:]}")
    # For the `run`, UNSAT means no instance exists: the facts contradict
    # each other and every check above passed vacuously.
    expect({n for n, _ in runs} == {"sealed"},
           f"plan.als ran {sorted(n for n, _ in runs)}, expected the "
           f"`sealed` witness. `control` swapped it for `run anything "
           f"{{ some House }}` and the count guard did not notice -- at "
           f"which point the vacuity check establishes only that SOME "
           f"instance exists, not one satisfying the predicate the "
           f"checks are about.")
    for name, verdict in runs:
        expect(verdict == "SAT",
               f"Alloy found NO instance of {name}: plan.als's facts admit "
               f"no plan at all, so both checks above passed vacuously and "
               f"prove nothing.\n{aout[-1500:]}")

    # PER-ASSERTION VACUITY, which the `run` above does not establish.
    # Model-level consistency and assertion-level non-vacuity are
    # different properties: `control` changed `assert Sealed { sealed }`
    # to `{ #House > 8 => sealed }`, whose antecedent is unsatisfiable in
    # a scope of 8, and the check went vacuously UNSAT while the run
    # stayed SAT and the ok line still said "non-vacuous".
    #
    # NEGATING THE ASSERTION DOES NOT DETECT THIS, and the first version
    # of this code did exactly that and passed the control. `check ~A`
    # asks for an instance where A holds; a vacuously-true A holds
    # everywhere, so ~A is false everywhere and the counterexample is
    # found either way. SAT for both.
    #
    # What separates them is breaking the thing the assertion is about
    # and requiring it to notice -- which is the negative control this
    # project already asks for by hand, run every time instead. The
    # vacuous Sealed stayed UNSAT under a too-small budget, so this is
    # the probe that catches it.
    # Each probe rewrites the DEFINITION it is about, located by its
    # header, rather than replacing a literal string anywhere in the
    # file. Matching text bit `control` twice: a comment above fdNeed
    # quoting its own body -- the comment HISTORY 39 all but asks for --
    # absorbed the replacement, so fdNeed was untouched, the check
    # correctly held, and the probe reported it as vacuous. And a
    # semantically identical rewrite (`mul[2, #House]`) failed with
    # "cannot find", a red suite caused by a correct edit. Both are
    # edits a competent agent makes, and a probe that cries wolf on
    # them is a probe someone deletes.
    # Three probes, not two. `Sealed` is a conjunction and the budget
    # probe only exercises the fd half: at scope 8 `#binds` cannot exceed
    # 64 while nwMaxBinds is 128, so the bind conjunct admits no
    # counterexample at any legal header value. `control` deleted that
    # conjunct from `sealed` outright -- half the predicate the check is
    # named for -- and the test passed. Lowering nwMaxBinds below what
    # the scope can reach is what makes the other half fail.
    for name, kind, what in (
            ("Sealed", "budget", "a budget too small for the scope"),
            ("Sealed", "binds", "a bind table smaller than the scope"),
            ("FdArithmetic", "union", "the `+` set-union form of fdNeed"),
    ):
        d = f"{lab}/mustfail-{name}-{kind}"
        os.makedirs(d, exist_ok=True)
        # DROP THE COMMAND LINES FIRST, while cmd_ix still describes this
        # text. The union probe rewrites `fun fdNeed[]: Int {[^}]*}` and
        # `[^}]*` spans newlines, so a definition written across three
        # lines -- a cosmetic reformat Alloy accepts -- collapsed `pl` by
        # two lines AFTER the indices were computed. The drop then removed
        # three innocent lines and left every real command in the probe,
        # which found its counterexample and was reported as "FdArithmetic
        # did NOT find a counterexample" beside solver output showing SAT.
        # Round five's line-count assertion did not catch this and could
        # not: `_blank` preserves newlines on every branch and `als_bare`
        # comes from splitlines()-normalised text, so it was an identity
        # -- a guard that had never been seen failing because it cannot
        # fail. Deleted rather than kept as decoration; the ordering below
        # is what makes the carry sound, so there is nothing left to
        # assert. `control`.
        pl = "\n".join(l for i, l in enumerate(als_bare)
                        if i not in set(cmd_ix))
        lm = open(f"{lab}/limits.als").read()
        if kind == "budget":
            lm, nsub = re.subn(r"fun nwMaxFds\[\]: Int \{[^}]*\}",
                               "fun nwMaxFds[]: Int { 16 }", lm)
        elif kind == "binds":
            lm, nsub = re.subn(r"fun nwMaxBinds\[\]: Int \{[^}]*\}",
                               "fun nwMaxBinds[]: Int { 1 }", lm)
        else:
            pl, nsub = re.subn(
                r"fun fdNeed\[\]: Int \{[^}]*\}",
                "fun fdNeed[]: Int { nwReserved[] + 2.mul[#House] }", pl)
        expect(nsub == 1,
               f"the {name} probe rewrote {nsub} definitions, expected "
               f"exactly one. It locates the definition by its header, so "
               f"this means the header changed shape -- fix the pattern; "
               f"do not delete the probe, it is the only thing showing "
               f"this check can fail.")
        pl += f"\ncheck {name} for {scope} but {have} Int\n"
        open(f"{d}/plan.als", "w").write(pl)
        open(f"{d}/limits.als", "w").write(lm)
        v = run(["java", "-Xss512m", "-jar", alloy, "exec", "-f",
                 "plan.als"], cwd=d)
        vout = v.out + v.err
        expect(v.returncode == 0,
               f"the must-fail probe for {name} did not solve "
               f"(exit {v.returncode})\n{vout[-800:]}")
        got = re.findall(r"\d+\.\s+check\s+\w+\s+.*?(SAT|UNSAT)", vout)
        expect(got == ["SAT"],
               f"{name} did NOT find a counterexample when given {what} "
               f"({got}). The check passes above without being able to "
               f"fail, so it is evidence of nothing -- an assertion whose "
               f"antecedent is unsatisfiable in scope reads exactly like "
               f"one that holds.\n{vout[-800:]}")

    # "each shown failing" now covers BOTH solvers. It said that while
    # only the Alloy half had probes, and `control` neutered a TLC
    # invariant to a green suite under this exact line. An ok line that
    # overstates what ran is the announcement-not-effect defect on the
    # reporting side.
    print(f"ok specs-are-checked (TLC: {vals['nwMaxUnits']} states, "
          f"{len(named)} invariants incl. the boundary; Alloy: {len(checks)} checks "
          f"clean at {have}-bit Int; every invariant and every check on "
          f"both sides shown failing when its own subject is broken, "
          f"one probe per predicate; "
          f"limits generated from blob.h)")


def test_baker_writes_the_declared_layout():
    """The baker writes the bytes. Nothing pinned where it writes them.

    blob.h's offset asserts pin the READER: swap two members of struct
    nw_unit and the build stops. They say nothing about `bakery/nw-cc.py`,
    which holds a second, independent statement of the same layout in its
    `struct.pack` calls -- and that is the side that decides what actually
    lands on disk.

    Swap `lids` and `budget` in the baker alone and a plan reading
    `lids=seccomp budget=4` bakes to lids=4, budget=1. `nw-check` says OK.
    The house runs with NW_LID_NEWNS and NO SECCOMP FILTER while the plan
    says it is confined -- invariant 6's "the plan lying", reached from the
    side the C asserts do not watch. Found by tcb-review and by drift, at
    944e9e7, independently.

    The suite caught that only by luck: the default city's `budget=3` is not
    a legal kind, so a kind/budget swap tripped NW_E_KIND. `budget=4` walks
    straight through, because 4 is a legal lid and 1 is a legal budget. A
    test that depends on which values a fixture happens to use is not
    pinning a layout.

    So: bake units with DISTINCT values in the trailer and read each byte
    back at the offset blob.h declares. It needs no crafted blob, because
    the baker's own output is the thing under test.

    Two details that are the difference between this test working and this
    test looking like it works, both found by running the control:

    * **The byte assertions come before the nw-check assertion.** With the
      first values tried (budget=2 lids=4), swapping the baker made lids=2
      -- landlock without a brick -- so nw-check rejected and the test
      failed on `nw-check refused it`. Red, for the value-luck reason this
      test exists to stop depending on. Assert position first and a reorder
      reports itself as a reorder.
    * **`lay2` uses values that stay legal under the swap.** kind=1,
      budget=0, lids=1 becomes kind=1, budget=1, lids=0: every field still
      valid, nw-check says OK, and the only thing wrong is that a plan
      declaring `lids=seccomp` produced a house with no filter. Nothing but
      the byte positions can catch that one."""
    NAME, PATH, BRICK = (int(blob_h(x)) for x in
                         ("NW_NAME_LEN", "NW_PATH_LEN", "NW_BRICK_HASH"))
    HDR = 20
    L = unit_layout()
    USZ = L["_size"]
    city = f"{WORK}/layout.city"
    open(city, "w").write(
        # distinct trailer: kind=1 budget=2 lids=4
        f"house lay /bin/true kind=longrun budget=2 lids=newns\n"
        # swap-legal trailer: kind=1 budget=0 lids=1
        f"house lay2 /bin/true kind=longrun budget=0 lids=seccomp\n")
    blob = f"{WORK}/layout.blob"
    b = run(["python3", CC, "--city", city, "--out", blob])
    expect(b.returncode == 0, f"bake\n{b.out}{b.err}")

    d = open(blob, "rb").read()
    for i, (nm, want) in enumerate((
            ("lay",  {"kind": 1, "budget": 2, "lids": 4, "_pad": 0}),
            ("lay2", {"kind": 1, "budget": 0, "lids": 1, "_pad": 0}))):
        base = HDR + i * USZ + unit_layout()["kind"]
        got = {"kind": d[base], "budget": d[base + 1],
               "lids": d[base + 2], "_pad": d[base + 3]}
        expect(got == want,
               f"the baker did not write {nm}'s trailer where blob.h "
               f"declares it.\n  blob.h says: {want}\n  baker wrote:  {got}\n"
               f"This is a POSITION mismatch, not a validation failure. A "
               f"plan that says one thing and a house that does another is "
               f"invariant 6's 'the plan lying', and nothing else in this "
               f"suite is looking at where the baker puts these bytes.")
        # The name and the two paths, same question, same answer. A baker
        # that emitted them in a different order would also bake clean.
        u = HDR + i * USZ
        expect(d[u:u + len(nm)] == nm.encode(),
               f"{nm}: name is not at offset 0 of the unit: {d[u:u + 8]!r}")
        expect(d[u + NAME:u + NAME + 9] == b"/bin/true",
               f"{nm}: exec_path is not at offset {NAME}: "
               f"{d[u + NAME:u + NAME + 16]!r}")
        expect(d[u + L["brick"]:u + L["brick"] + BRICK] == b"\x00" * BRICK,
               f"{nm}: brick is not at offset {L['brick']}, or a blank "
               f"brick is not zero to the field width")
        # The layer, same question. Blank here because these houses have
        # no brick and a layer without one is refused -- so what this pins
        # is that the field EXISTS at the declared offset and is zero to
        # its width, which is the property an unvalidated field loses.
        expect(d[u + L["layer"]:u + L["layer"] + NAME] == b"\x00" * NAME,
               f"{nm}: layer is not at offset {L['layer']}, or a blank "
               f"layer is not zero to the field width")

    # Last, so a reorder is reported as a reorder rather than as whatever
    # the shuffled values happen to violate.
    r = run([f"{BIN}/nw-check", blob])
    expect(r.returncode == 0,
           f"nw-check refused a blob whose bytes are all where blob.h says "
           f"they should be\n{r.out}{r.err}")

    print(f"ok baker-writes-declared-layout (name/exec_path/brick plus a "
          f"distinct 1/2/4 trailer and a swap-legal 1/0/1 one, each byte "
          f"read back at the offset blob.h declares)")


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
                         ("NW_NAME_LEN", "NW_PATH_LEN", "NW_BRICK_HASH"))
    nu, nb = int(blob_h("NW_MAX_UNITS")), int(blob_h("NW_MAX_BINDS"))
    HDR, USZ, BSZ = 20, unit_layout()["_size"], 2 + PATH
    biggest = HDR + nu * USZ + nb * BSZ

    # A maximal plan: every unit has a brick (a bind requires one) and the
    # bind table is full. The baker refuses a bind whose unit has no brick,
    # so this is the shape, not a crafted blob.
    # A hash, not a path -- phase 3. Any 64 hex chars: this plan is never
    # booted, only sized, so the image behind it need not exist.
    brick = "de" * 32
    city = f"{WORK}/maxblob.city"
    with open(city, "w") as f:
        for i in range(nu):
            binds = "".join(f" bind=/etc/hosts{'' if j == 0 else ''}"
                            for j in range(nb // nu + (1 if i < nb % nu else 0)))
            f.write(f"house m{i:02d} /bin/true kind=oneshot "
                    f"lids=newns brick={brick} layer=l-p{i:04d}{binds}\n")
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

    # And it must BOOT, not merely validate. nw-check accepting it says
    # nothing about the other two readers: changing pid1.c's ceiling test
    # from `>` to `>=` left this whole suite green while PID 1 answered
    # `HALT: plan size` to a plan nw-check had just called OK -- verbatim
    # the failure blob.h says NW_BLOB_MAX exists to prevent, reintroduced,
    # with the test written for it not seeing it. Found by `control`.
    rc, out = boot(plan=good, hold=900)
    expect(f"houses={nu}" in out,
           f"the largest legal blob validates but does not boot: some "
           f"reader is refusing a plan nw-check accepts -- PID 1 halts on "
           f"`plan size`, nw-spawn dies on `blob size`\n{out[-1200:]}")
    expect(f"houses_reaped={nu}" in out,
           f"the largest legal plan booted but did not reap\n{out[-1200:]}")

    # One byte over. Refused for its size by nw-check and by PID 1, and --
    # the half that regressed -- nw-spawn's recheck must not accept the
    # truncated prefix either.
    over = f"{WORK}/maxblob-plus.blob"
    # ONE byte. This was b"\\x00" -- four ASCII characters, not a NUL --
    # so the `ok` line said "+1 refused" about a +4 file, in the test whose
    # entire job is a one-byte boundary. `control` measured it at 33304.
    open(over, "wb").write(open(good, "rb").read() + b"\x00")
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
    # And PID 1, which this test used to skip for the +1 case: it was fed to
    # nw-check and nw-spawn only, so loosening pid1.c's ceiling by one byte
    # (NW_BLOB_MAX -> NW_BLOB_BUF) left the suite green with PID 1's limit
    # wrong. There are exactly three readers of a blob -- nwcheck_main.c,
    # pid1.c, nwspawn.c -- and the docstring says "every reader". `control`.
    rc, out = boot(plan=over, hold=400)
    expect("HALT: plan size" in out,
           f"PID 1 accepted a blob one byte over the ceiling: rc={rc}"
           f"\n{out[-800:]}")

    # The truncation window is [2^32, 2^32 + NW_BLOB_MAX]: any size in it
    # compares small through a uint32_t cast. 2^32 EXACTLY is the one point
    # in that window a `size <= 0` guard rescues, because it truncates to
    # precisely 0 -- so this case used to pass against the live defect.
    # `control` put the cast back on both operands and the entire suite was
    # green while a 4 GiB plan produced `*** buffer overflow detected ***`
    # and killed PID 1. Landing inside the window rather than on its edge is
    # the whole difference, and it is one addition.
    huge = f"{WORK}/huge.blob"
    with open(huge, "wb") as f:
        f.truncate((1 << 32) + biggest)
    r = run([f"{BIN}/nw-check", huge])
    expect(r.returncode == 1 and "blob size" in (r.out + r.err),
           f"a 4 GiB file must be refused for its size, not crash: "
           f"exit {r.returncode}\n{r.out}{r.err}")
    rc, out = boot(plan=huge, hold=400)
    expect("HALT: plan size" in out,
           f"PID 1 must halt on plan size, not die: rc={rc}\n{out[-800:]}")
    os.unlink(huge)

    print(f"ok blob-size-ceiling ({biggest} bytes: accepted by all three "
          f"readers, +1 refused by all three, 2^32+{biggest} refused)")


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

    pairs = re.findall(r"house=(\S+) fds_ge3=(-?\d+)", out)
    reported = dict(pairs)
    expect(len(reported) == n,
           f"only {len(reported)} of {n} units reported\n{out[-3000:]}")
    # EXACTLY once each. A dict silently collapses duplicates, so the
    # count above passes whether a unit reported once or five times --
    # and output arriving twice is the bug 4/9/13 class (a house's
    # channel carrying another house's data) seen from the receiving
    # end. tools/scale-probe.py checks this up to 8192 units; here is
    # where it costs nothing.
    names = [h for h, _ in pairs]
    dupes = sorted({h for h in names if names.count(h) > 1})
    expect(not dupes,
           f"{len(dupes)} units reported more than once (first: "
           f"{dupes[:5]}) -- a unit's output arrived on more than one "
           f"channel, which no count of distinct names can see")
    dirty = {h: v for h, v in reported.items() if v != "0"}
    expect(not dirty,
           f"units hold descriptors they were not granted: "
           f"{sorted(dirty.items())[:8]}")
    expect(f"houses_reaped={n}" in out, f"reap\n{out[-2000:]}")
    print(f"ok non-provision-at-max ({n} units)")


def test_brick_image_reproducible():
    """A brick is named by the sha256 of its image, so identical content
    must pack to identical bytes. Phase 1 of docs/plans/01.

    Reproducibility alone is not the claim. Packing the same tree twice and
    getting one hash is also what you would see if mkfs.erofs were simply
    deterministic with no flags at all -- so dropping `-U` and requiring the
    two packs to differ is what makes the agreement evidence about the flag
    set, which docs/options/08 argues IS the spec.

    Two more assertions arrived on 2026-09-11 because `control` found the
    test green against packers that cannot possibly be right:

    * **The image must depend on the tree.** Every assertion here survived a
      `pack()` that threw its `tree` argument away and packed an empty
      temporary directory instead -- a packer that ignores its input passing
      the test whose name is "content-addressed", because reproducibility,
      naming and the `-U` control are all satisfied by packing nothing at
      all, consistently. Two different trees must get two different names.
    * **--force-uid/--force-gid must be pinned.** Every tree here was owned
      by one user, so deleting both flags left the suite green while the
      sentence they carry -- the same tree packed by two different users is
      the same brick -- was false. Measured: without them, a 1000-owned copy
      packs to a different hash.

    The flag list is imported, not copied. A second copy here would drift
    from the one in the baker, and then the control would be dropping a
    flag from a list the packer does not use -- the shape this suite has
    produced repeatedly."""
    if not run(["sh", "-c", "command -v mkfs.erofs"]).returncode == 0:
        skip("brick-image-reproducible",
             "mkfs.erofs is not installed; no brick can be packed here")
        return

    import runpy
    mk = runpy.run_path(os.path.join(ROOT, "bakery", "mkbrick.py"))

    lab = f"{WORK}/brick"
    subprocess.run(["rm", "-rf", lab], check=False)
    tree, out = f"{lab}/tree", f"{lab}/out"
    os.makedirs(f"{tree}/bin"); os.makedirs(f"{tree}/etc"); os.makedirs(out)
    open(f"{tree}/etc/conf", "w").write("hello\n")
    shutil.copy(f"{BIN}/unit-probe", f"{tree}/bin/unit-probe")
    subprocess.run(["touch", "-d", "2001-02-03 04:05:06",
                    f"{tree}/etc/conf"], check=True)

    a, pa = mk["pack"](tree, out, quiet=True)
    b, _ = mk["pack"](tree, out, quiet=True)
    expect(a == b, f"the same tree packed twice gave two names:\n  {a}\n  {b}")
    expect(os.path.basename(pa) == a + ".img",
           f"the image is not named by its own hash: {pa}")
    expect(sha256_of(pa) == a,
           f"{pa} does not hash to the name it was given")

    # Same content, different path, every mtime rewritten.
    tree2 = f"{lab}/elsewhere/tree-renamed"
    os.makedirs(os.path.dirname(tree2))
    subprocess.run(["cp", "-a", tree, tree2], check=True)
    subprocess.run(["sh", "-c",
                    f"find {tree2} -exec touch -d '2020-12-25 11:22:33' {{}} +"],
                   check=True)
    c, _ = mk["pack"](tree2, out, quiet=True)
    expect(a == c,
           f"path or mtime leaked into the image name:\n  {a}\n  {c}")

    # The pairing: the flags are doing the work, not erofs's good manners.
    flags = list(mk["EROFS_FLAGS"])
    i = flags.index("-U")
    del flags[i:i + 2]
    d1, _ = mk["pack"](tree, out, flags=flags, quiet=True)
    d2, _ = mk["pack"](tree, out, flags=flags, quiet=True)
    expect(d1 != d2,
           f"without -U two packs of one tree still agreed ({d1}) -- either "
           f"this mkfs.erofs does not randomise the UUID, in which case the "
           f"reproducibility above is not evidence that the flag set works, "
           f"or -U is no longer the flag that carries it")

    # DIFFERENT content, different name. Without this every assertion above
    # is satisfied by a packer that ignores its input entirely: `control`
    # made pack() discard `tree` and pack an empty temporary directory, and
    # the test stayed green. This is the assertion that says the hash is of
    # the tree rather than of the act of packing.
    tree3 = f"{lab}/other"
    os.makedirs(f"{tree3}/etc")
    open(f"{tree3}/etc/conf", "w").write("goodbye\n")
    subprocess.run(["touch", "-d", "2001-02-03 04:05:06",
                    f"{tree3}/etc/conf"], check=True)
    e, _ = mk["pack"](tree3, out, quiet=True)
    expect(e != a,
           f"two trees with different content packed to the same name "
           f"({e}) -- the image does not depend on the tree, so every "
           f"reproducibility assertion above is vacuous")

    # Ownership is excluded from brick identity (docs/options/08 Q2), and
    # nothing pinned it: every tree above is owned by one user, so deleting
    # --force-uid/--force-gid left this test green with the claim false.
    # Needs the ability to chown, which is not a property of the kernel --
    # say which side ran, per the environment rule.
    tree4 = f"{lab}/owned"
    subprocess.run(["cp", "-a", tree, tree4], check=True)
    chowned = subprocess.run(["chown", "-R", "1000:1000", tree4],
                             capture_output=True).returncode == 0
    if chowned:
        f, _ = mk["pack"](tree4, out, quiet=True)
        expect(f == a,
               f"the same tree owned by a different user packed to a "
               f"different name:\n  root-owned {a}\n  1000-owned {f}\n"
               f"--force-uid/--force-gid are what exclude ownership from "
               f"brick identity")
        own = "and a 1000-owned copy"
    else:
        own = "(ownership unpinned here: chown is not permitted)"

    print(f"ok brick-image-reproducible (two packs, a moved and re-dated "
          f"copy, a different tree, {own}, and -U dropped to prove the "
          f"flags matter)")


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
        test_build_is_reproducible, test_brick_image_reproducible,
        test_harness_runs_fresh_binaries, test_coverage_accounting,
        test_hash_pin, test_difftest, test_lids_are_not_advisory,
        test_baker_rejects, test_fuzz_checker, test_happy, test_slot_b,
        test_rescue, test_halt_spawner, test_bad_crc,
        test_last_words_survive_group_term,
        test_orphans_across_restarts,
        test_crash_does_not_halt, test_budget_is_hard_total,
        test_shutdown_does_not_restart, test_term_signal, test_dawn_real_boot,
        test_kind_required, test_kind_exit0, test_seccomp_kills,
        test_brick_is_a_root, test_brick_image_is_sealed,
        test_layer_survives_a_restart,
        test_many_brick_houses_all_start, test_brick_needs_newns,
        test_baker_refuses_bad_layers,
        test_leading_zero_hash_is_a_brick,
        test_brick_hash_revalidated_at_the_supervisor,
        test_path_traversal_refused, test_dupname_refused,
        test_blob_size_ceiling,
        test_checker_rejects_crafted_fields,
        test_leading_zero_hash_reaches_the_supervisor,
        test_checker_rejects_crafted_binds,
        test_magic_moves_with_the_layout,
        test_old_magic_is_refused_as_magic,
        test_specs_are_checked,
        test_baker_writes_the_declared_layout,
        test_non_provision_at_max,
        test_landlock_confines,
    ]
    passed = []
    for t in tests:
        before = len(SKIPPED)
        name = t.__name__[len("test_"):].replace("_", "-")
        try:
            t()
        except Unavailable as u:
            # A capability this machine does not have. Named skip, using the
            # test's own name, so counts_as_passed() below excludes it and
            # the stray-name check cannot fire on it.
            skip(name, u.why)
        except SystemExit:
            # expect()'s FAIL path. Already loud, already non-zero; let it go.
            raise
        except BaseException:
            # ANY OTHER EXCEPTION IS A FAILURE, ANNOUNCED AS ONE. This used
            # to escape main() as a bare traceback. The interpreter does exit
            # non-zero on that -- measured here, 1 direct and 2 through make
            # -- but the run said nothing that reads as a failure, and
            # whether the code survives a wrapper is not a property this
            # suite should be inheriting. A crash is now indistinguishable
            # from a FAIL in both the output and the exit status, on purpose.
            #
            # It is the silence rule from CLAUDE.md applied to the runner
            # itself: the question is not "did it pass" but "what would this
            # look like if it had not run at all", and a traceback amid a
            # green-looking log is exactly that.
            print(f"FAIL: {name} raised an unhandled exception -- a crash "
                  f"is a failure, not a skip and not a pass:",
                  file=sys.stderr)
            traceback.print_exc()
            raise SystemExit(f"FAIL: {name} crashed")
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
