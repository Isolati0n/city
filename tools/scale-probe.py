#!/usr/bin/env python3
"""Boot cities larger than NW_MAX_UNITS and report where they break.

THE RECORDED GAP. harness.md has said for as long as it has existed that
nothing tests scale, that defects here have been correct at 4 units and
wrong at 4,000, and that a large-N boot is the most valuable thing nobody
has written. This is that, and it is a measurement harness rather than a
suite test: it rebuilds the tree at a raised NW_MAX_UNITS, which is a
four-place change (invariant 3) and far too slow for `make test`.

What it does per size N:

  1. copies the tree, raises NW_MAX_UNITS (and NW_MAX_FDS when the
     derived budget assert would refuse N), builds and stages it;
  2. bakes a city of N units and boots it under
     `unshare --pid --fork --mount-proc`, so nw-root is a real PID 1;
  3. checks the properties that a small city cannot distinguish:
     every unit ran, every unit's output arrived under ITS OWN name,
     every unit was reaped, no orphans, and no unit holds a descriptor
     it was not granted.

Point 3 is the reason this exists. Bugs 4, 9 and 13 were all silently
wrong ROUTING -- a house's output arriving on another house's channel --
and none of them returned an error. A 4-unit city has almost no room to
misroute in; a 4,000-unit one has 4,000 places to put a line.

Usage:
    python3 tools/scale-probe.py                 # the default ladder
    python3 tools/scale-probe.py 64 256 1024     # explicit sizes

Prints one line per size and a summary naming the first failure and why.
Not in the TCB. Nothing on a running machine calls this.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _blob_int(name, src=None):
    """Read a #define out of blob.h. This tool rewrites two of the four
    places a limit lives, so it must not hold a third copy of one --
    `FD_RESERVED = 8` was a module constant here, used for the fd
    arithmetic AND substituted into the baker's limit tuple, while
    blob.h's NW_FD_RESERVED was never read. Setting it to 10 in blob.h
    gave a clean build with the C code reserving 10 and the baker
    checking against 8: the baker accepts a city the C budget cannot
    hold, silently, on every rung. Invariant 3's drift class,
    reintroduced by the tool whose job is changing that limit.
    `control`.

    THIS REPAIRS THAT DRIFT, IT DOES NOT DETECT IT. build_at() rewrites
    the baker's whole limit tuple from blob.h, so a wrong FD_RESERVED
    already sitting in bakery/nw-cc.py is overwritten on every rung and
    the ladder stays green through a genuine invariant-3 disagreement.
    `control` verified it: blob.h at 10, the repo baker at 99, and the
    built tree read 10. Reconciling is right for a measurement harness --
    the ladder is not a drift check. `drift` is, and the paragraph above
    should not be read as saying otherwise."""
    src = src or open(os.path.join(ROOT, "blob.h")).read()
    m = re.search(rf"^[ \t]*#[ \t]*define[ \t]+{name}[ \t]+(\S+)[ \t]*$",
                  src, re.M)
    if not m:
        raise SystemExit(f"scale-probe: {name} not found in blob.h")
    return int(re.sub(r"[uUlL]+$", "", m.group(1)), 0)


def _children(pid):
    """PIDs `unshare --fork` forked, or [] if it is already gone."""
    try:
        return [int(x) for x in
                open(f"/proc/{pid}/task/{pid}/children").read().split()]
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return []


def sh(cmd, cwd=None, timeout=900, env=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout,
                       env=env)
    return (p.returncode,
            p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def build_at(n_units, work):
    """Copy the tree, raise the limits enough to admit n_units, build."""
    tree = os.path.join(work, f"tree{n_units}")
    stage = os.path.join(work, f"stage{n_units}")
    # `nw-*` as a glob also matches bakery/nw-cc.py, which the build
    # needs. Skip compiled products by shape -- a built binary has no
    # extension -- rather than by a prefix the sources share.
    def skip(d, names):
        out = set()
        for nm in names:
            if nm in (".git", "boot-out", "coverage", ".reviews",
                      "__pycache__", "specs"):
                out.add(nm)
            elif nm.endswith((".o", ".gcda", ".gcno", ".gcov")):
                out.add(nm)
            elif ("." not in nm
                  and (nm.startswith("nw-") or nm.startswith("unit-"))
                  and os.path.isfile(os.path.join(d, nm))):
                out.add(nm)
        return out

    shutil.copytree(ROOT, tree, symlinks=True, ignore=skip)

    # blob.h's own assert: reserved + 2 per unit must fit the fd budget.
    # Raise the budget only as far as N actually needs, so the probe does
    # not quietly hide the limit it is supposed to be measuring.
    blob = open(os.path.join(tree, "blob.h")).read()
    reserved = _blob_int("NW_FD_RESERVED", blob)
    need = reserved + 2 * n_units
    cur_fds = int(re.search(r"#define NW_MAX_FDS\s+(\d+)", blob).group(1))
    new_fds = max(cur_fds, need)
    blob = re.sub(r"(#define NW_MAX_UNITS\s+)\d+", rf"\g<1>{n_units}", blob)
    blob = re.sub(r"(#define NW_MAX_FDS\s+)\d+", rf"\g<1>{new_fds}", blob)
    # NW_DUP_SLOTS must stay a power of two strictly above NW_MAX_UNITS.
    slots = 1
    while slots <= n_units:
        slots *= 2
    blob = re.sub(r"(#define NW_DUP_SLOTS\s+)\d+", rf"\g<1>{slots}", blob)
    open(os.path.join(tree, "blob.h"), "w").write(blob)

    # The baker declares all four limits in ONE tuple assignment:
    #   MAX_UNITS, MAX_BINDS, FD_RESERVED, MAX_FDS = 64, 128, 8, 1024
    # Per-name regexes do not work on that and do not fail either --
    # `MAX_FDS\s*=\s*\d+` matches at "MAX_FDS = 64", so substituting
    # there rewrote MAX_UNITS with the fd value and left MAX_FDS alone.
    # Sizes 256 and 508 then "passed" with the wrong constants set, and
    # 1024 failed the baker's fd check for a reason the probe invented.
    # Rewrite the whole line, and assert it was found.
    cc = os.path.join(tree, "bakery", "nw-cc.py")
    src = open(cc).read()
    pat = (r"^MAX_UNITS, MAX_BINDS, FD_RESERVED, MAX_FDS = "
           r"\d+, (\d+), \d+, \d+$")
    m = re.search(pat, src, re.M)
    if not m:
        raise SystemExit(
            "scale-probe: the baker's limit line is not the shape this "
            "probe rewrites. Fix the pattern rather than letting a "
            "partial substitution set the wrong constant -- that is how "
            "sizes 256 and 508 once passed with MAX_UNITS holding an fd "
            "count.")
    src = re.sub(pat,
                 f"MAX_UNITS, MAX_BINDS, FD_RESERVED, MAX_FDS = "
                 f"{n_units}, {m.group(1)}, {reserved}, {new_fds}",
                 src, count=1, flags=re.M)
    open(cc, "w").write(src)
    # And check the build actually got the numbers, rather than trusting
    # two regexes over two files.
    chk = open(cc).read()
    assert f"= {n_units}, " in chk and f", {new_fds}" in chk, chk[:200]

    rc, out, err = sh(["make", f"STAGE={stage}", "-j4", "stage"], cwd=tree)
    return tree, stage, new_fds, slots, rc, out + err


def probe(n_units, work, hold_ms=None):
    t0 = time.time()
    tree, stage, fds, slots, rc, log = build_at(n_units, work)
    if rc != 0:
        return dict(n=n_units, phase="build", ok=False,
                    why=log.strip().splitlines()[-1][:200] if log.strip()
                        else f"make exited {rc}")

    binp = os.path.join(stage, "nw", "bin")
    probe_bin = os.path.join(binp, "unit-probe")
    city = os.path.join(work, f"c{n_units}.city")
    # Names are fixed-width and unique; the router has to keep 4-digit
    # indices apart, which a 4-unit city never asks of it.
    # Width from n, not fixed at 4. `{:04d}` pads and does not truncate,
    # so at n >= 10000 the names go to five digits while the readers
    # below matched `\d{4}` exactly -- every unit from index 10000 up
    # would be counted as never reported. The direction is safe (a false
    # FAIL) but the documented break is n ~ 9996 and the next rung after
    # 8192 is 10240, so the format breaks inside the interval this tool
    # exists to characterise. `control`.
    w = max(4, len(str(n_units - 1)))
    open(city, "w").write("".join(
        f"house u{i:0{w}d} {probe_bin} kind=oneshot lids=none\n"
        for i in range(n_units)))
    blob = os.path.join(work, f"c{n_units}.blob")
    rc, out, err = sh(["python3", os.path.join(tree, "bakery", "nw-cc.py"),
                       "--city", city, "--out", blob])
    if rc != 0:
        return dict(n=n_units, phase="bake", ok=False,
                    why=(out + err).strip()[-200:])

    rc, out, err = sh([os.path.join(binp, "nw-check"), blob])
    if rc != 0:
        return dict(n=n_units, phase="nw-check", ok=False,
                    why=(out + err).strip()[-200:])

    # TIME THE CITY, NOT THE HOLD. The first version passed
    # --hold-ms max(4000, 40*n) and reported the wall time, which at
    # n=1024 was a 41s hold measured as a 44s "boot" -- a straight line
    # of 43ms per unit that was entirely the harness timing itself. Poll
    # the output instead: record when the city opens and when the last
    # house is reaped, then TERM it. hold_ms stays 0 (production), so
    # nothing closes the city but this probe.
    log = os.path.join(work, f"c{n_units}.log")
    t_open = t_reaped = None
    completed = died = False
    deadline = time.time() + max(120, 0.5 * n_units)
    with open(log, "wb") as fh:
        p = subprocess.Popen(
            ["unshare", "--pid", "--fork", "--mount-proc", "--",
             os.path.join(binp, "nw-root"), blob],
            stdout=fh, stderr=subprocess.STDOUT)
        try:
            t1 = time.time()
            while time.time() < deadline:
                if p.poll() is not None:
                    died = True
                    break
                try:
                    seen = open(log, "rb").read().decode("utf-8", "replace")
                except FileNotFoundError:
                    seen = ""
                if t_open is None and f"houses={n_units}" in seen:
                    t_open = time.time() - t1
                if t_reaped is None and f"houses_reaped={n_units}" in seen:
                    t_reaped = time.time() - t1
                # Every house is oneshot, so "all reported" is the real
                # end of work; the reap line only appears at shutdown.
                # `w`, NOT a literal 4. This line was the one survivor
                # of the width fix at 186 -- the readers at 312/313 were
                # parameterised and this one was not, so from 10001 units
                # up the set never reached n_units, the loop always ran
                # to the deadline, and the tool reported a timeout at
                # every rung above the width boundary. That is inside the
                # interval this tool exists to characterise.
                if t_open is not None and \
                        len(set(re.findall(rf"house=(u\d{{{w}}})", seen))) \
                        == n_units:
                    completed = True
                    break
                time.sleep(0.05)
            # NOT `and t_open is None`. That clause meant a city which
            # opened and was then too slow to finish reporting fell
            # through to the content checks and read as "N units never
            # reported" -- the exact conflation the timeout branch was
            # written against, surviving on the far side of t_open.
            # `control`. But the loop has THREE exits, not two, and
            # `not completed` alone made the third one lie: PID 1
            # exiting on its own is how the documented break at ~10k
            # presents (`HALT: log pipe`), and calling that "the city
            # did not open within Ns" would have relabelled this tool's
            # own headline result as a timeout. A death falls through to
            # the content checks, which quote the halt line.
            timed_out = not completed and not died
            work_s = time.time() - t1
            # The city may already be gone -- at sizes past a real limit
            # it dies on its own, and reading /proc for a pid that has
            # exited raised FileNotFoundError and took the probe down
            # INSTEAD OF REPORTING THE BREAK. A harness whose job is to
            # find the breaking point must not crash when it finds one.
            kid = _children(p.pid)
            if kid:
                try:
                    os.kill(kid[0], 15)
                except ProcessLookupError:
                    pass
            try:
                p.wait(timeout=60)
            except subprocess.TimeoutExpired:
                pass
        finally:
            for x in _children(p.pid):
                try:
                    os.kill(x, 9)
                except ProcessLookupError:
                    pass
            if p.poll() is None:
                p.kill()
            p.wait()
    o = open(log, "rb").read().decode("utf-8", "replace")
    rc = p.returncode

    res = dict(n=n_units, phase="boot", ok=True, fds=fds, slots=slots,
               open_s=round(t_open, 2) if t_open is not None else None,
               work_s=round(work_s, 2),
               total_s=round(time.time() - t0, 1), rc=rc)

    # A deadline expiry used to fall straight through to the content
    # checks with a partial log, so a city that was merely SLOW reported
    # as "N units never reported" -- indistinguishable from real loss,
    # and "slow" is the expected behaviour near the break given this
    # tool's own quadratic finding. `control`, from reading.
    if timed_out:
        res.update(ok=False,
                   why=f"the city did not open within "
                       f"{max(120, 0.5 * n_units):.0f}s. This is a "
                       f"TIMEOUT, not a content failure -- the log is "
                       f"partial and the counts below would be about "
                       f"nothing.")
        return res

    if f"houses={n_units}" not in o:
        res.update(ok=False, why=f"city did not open with {n_units} houses; "
                                 f"last: {o.strip().splitlines()[-1][:160] if o.strip() else '(no output)'}")
        return res

    # PRESENCE and ROUTING need different evidence, and conflating them
    # fails at n=64 on a correct city. PID 1's logger prefixes the start
    # of a write CHUNK, not every line in it (harness.md, the log-chunk
    # trap), so an unprefixed line is normal and reads as a missing unit
    # if you match on the prefix. Measured: requiring "[uNNNN] house=..."
    # reported 4 of 64 units missing on a tree that is fine.
    #
    # WORSE, AND THE FINDING THIS PROBE EXISTS TO PRODUCE: a prefix that
    # disagrees with the self-tag is NOT evidence of misrouting either.
    # spawn_logger emits each chunk as THREE separate write(2) calls --
    # prefix, buffer, newline (pid1.c) -- and every logger shares fd 2,
    # so another logger's write can land between the prefix and the data.
    # Measured over three runs each: mismatches per run were [0,1,0] at
    # n=16, [0,0,0] at n=64, [0,0,2] at n=128 -- nondeterministic, which
    # a routing defect would not be, while missing and duplicate units
    # were 0 everywhere.
    #
    # So the merged console CANNOT distinguish wrong routing from
    # interleaving, which means the bug 4/9/13 class is not observable
    # on this channel at any N. Separating them needs per-unit capture,
    # which is the logging pass's problem. What this probe can assert
    # soundly is that every unit's output arrives EXACTLY ONCE: that
    # catches loss and duplication, and it is deterministic.
    reports = re.findall(rf"house=(u\d{{{w}}}) fds_ge3=(-?\d+)", o)
    prefixed = re.findall(rf"\[(u\d{{{w}}})\] house=(u\d{{{w}}})", o)
    named = [h for h, _ in reports]
    missing = [f"u{i:0{w}d}" for i in range(n_units)
               if f"u{i:0{w}d}" not in set(named)]
    dupes = sorted({h for h in named if named.count(h) > 1})
    interleaved = [(p, h) for p, h in prefixed if p != h]
    dirty = [(h, v) for h, v in reports if v != "0"]

    res["reported"] = len(set(named))
    res["interleaved"] = len(interleaved)
    if missing:
        res.update(ok=False,
                   why=f"{len(missing)} units never reported "
                       f"(first: {missing[:4]})")
    elif dupes:
        res.update(ok=False,
                   why=f"{len(dupes)} units reported more than once "
                       f"(first: {dupes[:4]}) -- output duplicated across "
                       f"channels, which is the bug 4/9/13 class and is "
                       f"deterministic, unlike interleaving")
    elif dirty:
        res.update(ok=False,
                   why=f"{len(dirty)} units hold ungranted descriptors "
                       f"(first: {dirty[:3]})")
    elif f"houses_reaped={n_units}" not in o:
        m = re.search(r"houses_reaped=(\d+)", o)
        res.update(ok=False,
                   why=f"reaped {m.group(1) if m else '?'} of {n_units}")
    elif "orphans=0" not in o:
        m = re.search(r"orphans=(\d+)", o)
        res.update(ok=False, why=f"orphans={m.group(1) if m else '?'}")
    return res


def main():
    sizes = [int(a) for a in sys.argv[1:]] or [64, 128, 256, 508, 1024, 2048]
    work = tempfile.mkdtemp(prefix="nw-scale-")
    print(f"scale-probe: work={work}")
    print(f"  ulimit -n={os.sysconf('SC_OPEN_MAX')} "
          f"pid_max={open('/proc/sys/kernel/pid_max').read().strip()} "
          f"cpus={os.cpu_count()}")
    print()
    first_fail = None
    for n in sizes:
        r = probe(n, work)
        mark = "ok  " if r["ok"] else "FAIL"
        extra = ""
        if r.get("work_s") is not None:
            extra = (f" open={r['open_s']}s all-ran={r['work_s']}s "
                     f"fds={r.get('fds')} dupslots={r.get('slots')} "
                     f"reported={r.get('reported')} "
                     f"interleaved={r.get('interleaved')}")
        print(f"  {mark} n={r['n']:<5} phase={r['phase']:<8}{extra}")
        if not r["ok"]:
            print(f"       why: {r['why']}")
            if first_fail is None:
                first_fail = r
            break
    print()
    if first_fail:
        print(f"scale-probe: first failure at n={first_fail['n']} in "
              f"{first_fail['phase']}: {first_fail['why']}")
    else:
        print("scale-probe: every size passed. TREAT THAT WITH SUSPICION --")
        print("  a ladder that never breaks is usually a ladder that is not")
        print("  reaching anything, not a system without a limit. Check that")
        print("  the boot really ran N houses and that the assertions above")
        print("  can fail at all before believing it.")
    shutil.rmtree(work, ignore_errors=True)
    return 0 if not first_fail else 1


if __name__ == "__main__":
    sys.exit(main())
