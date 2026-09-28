#!/usr/bin/env python3
"""tools/fdorder-sweep.py -- descriptor-shaping sweep for pack_kit()'s
batch-before-place ordering, invariant 2's discipline in nwspawn.c.

Not part of `make test`. Run by hand:

    make stage
    python3 tools/fdorder-sweep.py --sweep

against the tree as staged. To exercise the mutation the sweep exists to
try to break -- reverting pack_kit()'s scratch allocation from batched
(allocate every wire's scratch fd first, then place them all) to
interleaved (allocate one wire's scratch fd, place it, repeat) -- build a
separate mutated nw-spawn and point at it:

    <make the interleaved edit to pack_kit() in a scratch checkout, build>
    python3 tools/fdorder-sweep.py --sweep --nwspawn /path/to/mutated/nw-spawn

Bypasses pid1.c entirely and invokes nw-spawn directly, with a precisely
shaped inherited descriptor table, so the exact fd numbers nw-spawn's
scratch allocations and target placements land on are under the sweep's
control rather than whatever the ambient process happened to hold.

METHODOLOGY. Each configuration is (n_leaves, filler_count,
gap_positions): bakes a star-topology city (one hub house wired to
n_leaves leaf houses, all houses `unit-wire` -- houses/wire.c), then
before invoking nw-spawn, opens `filler_count` inheritable placeholder
descriptors filling the low fd range (occupying roughly 3..3+filler_count
-1, ahead of anything nw-spawn itself would open), then closes the
fillers at `gap_positions` to punch holes back open in the low range --
exactly where a scratch allocation and an upcoming target placement
compete for the same numbers. The default `--sweep` grid varies n_leaves
up to 30 (wire counts up to 30 on the hub), filler_count, and both single-
and multi-gap patterns (up to "every filler closed").

VERIFICATION is on content, not just exit status: `unit-wire` in echo
mode writes "hello from <name>\n" on each of its wires and logs what it
read back. For the star topology this sweep bakes, a leaf must read back
exactly "hello from hub" on its one wire, and the hub's combined log
output must contain exactly one "hello from leafN" per leaf, for every
leaf. Anything else -- a leaf hearing from the wrong peer, the hub
missing a leaf or hearing from one twice, a wire count that doesn't match
the plan -- is a misrouted or leaked wire and fails the configuration.

WHAT THIS SWEEP COVERS AND WHAT IT DOES NOT, per nwspawn.c's pack_kit()
comment (read it for the full three-axis account): this sweep's filler
descriptors are disposable placeholders, so they can only ever land a
wire's SCRATCH allocation on a target -- axis 2 there, which is
impossible regardless of ordering (F_DUPFD_CLOEXEC never returns an open
fd) and which this sweep's clean 783-configuration result is consistent
with rather than proof of anything new. A filler can never stand in for
a SIBLING WIRE'S OWN SOURCE descriptor (axis 3, found by `tcb-review` in
an isolated harness: a later wire's real source fd, not a scratch copy,
numerically equal to an earlier wire's already-placed target -- a
genuine defect in pack_kit() taken alone). This sweep's star topology
(one multi-wire hub) happens to exercise axis 3 as a side effect --
the hub's wire_fd[] spans the full edge table, the widest range any
single unit's wires can cover -- and passed clean up to 30 wires under
both the real code and the interleaved mutation, which is what the
algebra in nwspawn.c's comment says must happen for any legal plan
(wire_fd[] is strictly increasing with floor 3, which precludes axis 3
structurally, given main()'s own edge-creation loop). That algebra, not
this sweep's technique, is why axis 3 does not need a dedicated test
here. Not in the TCB. A diagnostic tool, not a regression test.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import select

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
NWCC = os.path.join(ROOT, "bakery", "nw-cc.py")


def run_direct(nwspawn, nwsup, blob, n_units, filler_count, gap_positions):
    fillers = [os.open("/dev/null", os.O_RDONLY) for _ in range(filler_count)]
    for f in fillers:
        os.set_inheritable(f, True)
    for idx in gap_positions:
        if 0 <= idx < len(fillers) and fillers[idx] >= 0:
            os.close(fillers[idx])
            fillers[idx] = -1

    r, w = os.pipe()
    os.set_inheritable(r, True)
    os.set_inheritable(w, True)
    logws, logrs = [], []
    for _ in range(n_units):
        lr, lw = os.pipe()
        os.set_inheritable(lw, True)
        logws.append(lw)
        logrs.append(lr)

    pid = os.fork()
    if pid == 0:
        os.environ["NW_SUP"] = nwsup
        argv = [nwspawn, blob, str(w), str(n_units)] + [str(x) for x in logws]
        try:
            os.execv(nwspawn, argv)
        except Exception:
            os._exit(126)
    os.close(w)
    for lw in logws:
        os.close(lw)
    for f in fillers:
        if f >= 0:
            os.close(f)

    outputs = {i: b"" for i in range(n_units)}
    open_rs = set(logrs)
    report_buf = b""
    report_done = False
    while open_rs or not report_done:
        rlist = list(open_rs) + ([r] if not report_done else [])
        if not rlist:
            break
        ready, _, _ = select.select(rlist, [], [], 10)
        if not ready:
            break
        for fd in ready:
            if fd == r:
                chunk = os.read(fd, 65536)
                if not chunk:
                    report_done = True
                else:
                    report_buf += chunk
            else:
                idx = logrs.index(fd)
                chunk = os.read(fd, 65536)
                if not chunk:
                    open_rs.discard(fd)
                else:
                    outputs[idx] += chunk
    _, status = os.waitpid(pid, 0)
    for fd in logrs:
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        os.close(r)
    except OSError:
        pass
    return status, report_buf, outputs


def check_star(n_leaves, outs):
    """Verify content, not just exit status. unit 0 is the hub, units
    1..n_leaves are leaf0..leaf(n-1) -- the order bake_star() writes
    them in and the order nw-cc.py assigns unit indices in. Returns a
    list of failure strings; empty means the wiring was correct."""
    fails = []
    hub_out = outs.get(0, b"").decode(errors="replace")
    m = re.search(r"wires=(\d+)", hub_out)
    hub_wires = int(m.group(1)) if m else -1
    if hub_wires != n_leaves:
        fails.append("hub wires={} want={}".format(hub_wires, n_leaves))
    heard = set(re.findall(r"hello from (leaf\d+)", hub_out))
    want = {"leaf{}".format(i) for i in range(n_leaves)}
    if heard != want:
        fails.append("hub heard {} want {}".format(sorted(heard), sorted(want)))
    for i in range(n_leaves):
        leaf_out = outs.get(i + 1, b"").decode(errors="replace")
        lm = re.search(r"wires=(\d+)", leaf_out)
        if not lm or lm.group(1) != "1":
            fails.append("leaf{} wires={}".format(i, lm.group(1) if lm else "?"))
        if "hello from hub" not in leaf_out:
            fails.append("leaf{} did not read 'hello from hub'".format(i))
    return fails


def one_config(stage, nwspawn, nwsup, nwcc, n_leaves, filler_count, gaps):
    workdir = tempfile.mkdtemp(prefix="fdorder-")
    try:
        city = os.path.join(workdir, "star.city")
        lines = ["house hub {}/nw/bin/unit-wire kind=oneshot lids=none\n".format(stage)]
        for i in range(n_leaves):
            lines.append("house leaf{} {}/nw/bin/unit-wire kind=oneshot lids=none\n"
                          .format(i, stage))
        for i in range(n_leaves):
            lines.append("edge=hub,leaf{}\n".format(i))
        open(city, "w").writelines(lines)
        blob = os.path.join(workdir, "star.blob")
        r = subprocess.run(["python3", nwcc, "--city", city, "--out", blob],
                            capture_output=True, text=True)
        if r.returncode != 0:
            return False, ["bake failed: {} {}".format(r.stdout, r.stderr)]
        n_units = n_leaves + 1
        status, _report, outs = run_direct(nwspawn, nwsup, blob, n_units,
                                            filler_count, gaps)
        if not (os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0):
            return False, ["nw-spawn exit status {}".format(status)]
        fails = check_star(n_leaves, outs)
        return (len(fails) == 0), fails
    finally:
        subprocess.run(["rm", "-rf", workdir])


def default_grid():
    """(n_leaves, filler_count, gap_positions) configurations. Wire
    counts up to 30 on the hub; filler density varied around the
    scratch/target boundary that competition would need; single- and
    multi-gap patterns up to "every filler closed"."""
    configs = []
    for n_leaves in (1, 2, 3, 5, 8, 12, 16, 20, 25, 30):
        for filler_count in (0, 1, 2, 4, 8, n_leaves, n_leaves + 2, 2 * n_leaves):
            if filler_count == 0:
                configs.append((n_leaves, filler_count, ()))
                continue
            # single gap at every position
            for gap in range(filler_count):
                configs.append((n_leaves, filler_count, (gap,)))
            # every filler closed (maximal multi-gap)
            configs.append((n_leaves, filler_count, tuple(range(filler_count))))
            # alternating gaps, a distinct multi-gap pattern
            if filler_count >= 4:
                configs.append((n_leaves, filler_count,
                                 tuple(range(0, filler_count, 2))))
    return configs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default=os.environ.get("NW_STAGE", "/tmp/nw-init-run"))
    ap.add_argument("--nwspawn", default=None,
                     help="override nw-spawn binary (default: <stage>/nw/bin/nw-spawn)")
    ap.add_argument("--sweep", action="store_true", help="run the default grid")
    ap.add_argument("n_leaves", nargs="?", type=int)
    ap.add_argument("filler_count", nargs="?", type=int)
    ap.add_argument("gaps", nargs="?", default="")
    args = ap.parse_args()

    nwspawn = args.nwspawn or os.path.join(args.stage, "nw", "bin", "nw-spawn")
    nwsup = os.path.join(args.stage, "nw", "bin", "nw-sup")
    for p in (nwspawn, nwsup, NWCC):
        if not os.path.exists(p):
            print("missing: {} -- run `make stage` first".format(p), file=sys.stderr)
            sys.exit(2)

    if args.sweep:
        grid = default_grid()
        n_fail = 0
        for (n_leaves, filler_count, gaps) in grid:
            ok, fails = one_config(args.stage, nwspawn, nwsup, NWCC,
                                    n_leaves, filler_count, gaps)
            if not ok:
                n_fail += 1
                print("FAIL n_leaves={} filler_count={} gaps={}: {}"
                      .format(n_leaves, filler_count, gaps, fails))
        print("{} configurations, {} failed, nwspawn={}"
              .format(len(grid), n_fail, nwspawn))
        sys.exit(1 if n_fail else 0)

    n_leaves = args.n_leaves if args.n_leaves is not None else 20
    filler_count = args.filler_count if args.filler_count is not None else 40
    gaps = tuple(int(x) for x in args.gaps.split(",")) if args.gaps else ()
    ok, fails = one_config(args.stage, nwspawn, nwsup, NWCC,
                            n_leaves, filler_count, gaps)
    print("PASS" if ok else "FAIL: {}".format(fails))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
