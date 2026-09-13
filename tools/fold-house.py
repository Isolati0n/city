#!/usr/bin/env python3
"""fold-house: save a closed house's writable layer into a new brick, and
stage a candidate slot that names it.

    python3 tools/fold-house.py --slots <slots> --city <live.city> --unit <name>

Saving is immediate and switching is deferred. The new brick exists when
this returns; the old one is untouched and both remain. The candidate slot
becomes the running plan at the next boot. **The operator does not reboot
to save. They reboot to switch.**

Both obvious simplifications destroy that. Deferring the fold to boot time
makes saving feel broken -- the operator asked to save and nothing was
saved. Editing the live plan in place removes the boot, which is the line
the deferral exists to keep.

THE NEW LAYER ID IS NOT OPTIONAL AND NOT COSMETIC. Reusing the old id
stacks the just-folded content over itself: the upper still holds every
file now baked into the brick, and it still holds the WHITEOUTS, which
would re-delete files the fold just saved. The candidate gets a fresh id
and the old layer is left alone, so the previous brick plus the previous
layer still boots -- which is what makes this reversible by switching
slots rather than by undoing anything.

THE EXCEPTION THIS SITS UNDER, and `tools/stage-candidate.py` made it
first: a privileged helper running on the LIVE machine and writing the
machine root. That tool's own docstring states the rule in these words
and this is the second helper under it, not the first. What keeps
either defensible is not that it is small. It is that it cannot change
what is running:

  - it folds only a house that is already closed, established below
    rather than trusted;
  - it produces only a brick and a candidate slot;
  - it never writes the live plan, and never writes <slots>/current --
    the stager refuses the live slot and re-reads it after every refusal;
  - nothing it produces takes effect without `nw-check` and a boot.

The rule that has held throughout this project is not "nothing privileged
runs live". It is **nothing changes what is running without going through
validation and a boot**, and this obeys it.
"""
from __future__ import annotations
import argparse, importlib.util, os, subprocess, sys, tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.join(_HERE, "..")
sys.path.insert(0, os.path.join(_ROOT, "bakery"))
import fold as foldmod                                    # noqa: E402
import mkbrick                                            # noqa: E402


def _by_path(name, rel):
    """`bakery/nw-cc.py` and `tools/stage-candidate.py` are not importable
    module names. Load them by path rather than copying what they do: a
    second city parser is a second copy of the plan language, and a second
    stager is a second copy of the guard that keeps the live slot alive."""
    p = os.path.join(_ROOT, rel)
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- establishing that the house is closed -------------------------------
#
# THE SUBJECT IS THE SUPERVISOR, NOT THE HOUSE. "No supervisor for this
# unit" is precisely "this unit will not run again before the next boot",
# which is the fold's actual precondition. "The house process is not
# running" is NOT: a longrun house between restarts satisfies it and is
# about to write again.
#
# THIS DEPENDS ON INVARIANT 1, AND THE DEPENDENCE IS SILENT. A supervisor
# that has exited cannot come back only because PID 1 has no respawn path.
# If PID 1 ever grows one, this check does not start failing -- it becomes
# a race that nothing reports, and the fold just occasionally captures a
# live layer. Nothing here can detect that. The guard is `pid1.c` staying
# as it is, pinned by `CLAUDE.md` invariant 1 and its `absent-in`
# annotations for `respawn` and `restart`.

class NotClosed(Exception):
    pass


def _pids():
    return [e for e in os.listdir("/proc") if e.isdigit()]


def scan_environ(layer_id):
    """Pids whose environment carries NW_LAYER=<id>.

    `nwspawn.c` sets NW_LAYER for the supervisor and the house inherits
    it, so one scan covers both. `clearenv()` does not defeat it: the
    kernel serves /proc/pid/environ from the original environment VMA,
    which a libc call does not touch."""
    hit = []
    want = f"NW_LAYER={layer_id}".encode()
    for pid in _pids():
        try:
            with open(f"/proc/{pid}/environ", "rb") as f:
                if want in f.read().split(b"\0"):
                    hit.append(int(pid))
        except (OSError, ValueError):
            continue          # exited between listdir and open, or not ours
    return hit


def scan_mountinfo(upper_path):
    """Pids whose mount table shows an overlay with this upperdir.

    A different question from the one above, not a second opinion on it.
    A grandchild that execs with a fresh environment does not carry
    NW_LAYER and is invisible to the environ scan, while still holding
    the layer mounted. mountinfo is the kernel's own answer and is
    visible across mount namespaces by scanning every pid."""
    hit = []
    want = f"upperdir={upper_path}"
    for pid in _pids():
        try:
            with open(f"/proc/{pid}/mountinfo") as f:
                for line in f:
                    if want in line:
                        hit.append(int(pid))
                        break
        except (OSError, ValueError):
            continue
    return hit


def _paired_environ_probe(layer_id):
    """THE PAIRING, and it costs a fork -- which the design did not
    expect and the probe itself proved.

    An empty scan is satisfied by the house being closed AND by the scan
    being broken; unpaired, those are opposite outcomes reported
    identically, which `harness.md` names as the whole test. So something
    carrying NW_LAYER=<id> must exist while the scan runs, and the scan
    must find it.

    THE CHEAP VERSION DOES NOT WORK, MEASURED. The plan was for the
    helper to set NW_LAYER in its own environment and find itself. It
    cannot: `/proc/pid/environ` is served from the ORIGINAL exec-time
    environment VMA, so `os.environ[...] = ...` is invisible there --

        setenv visible in /proc/self/environ: False
        exec-time visible in /proc/self/environ: True
        still visible after environ.clear(): True

    The third line is the fact the SCAN rests on and it holds. The first
    is the fact the PAIRING rested on and it does not, which is the same
    asymmetry read from the other end. The probe caught this on its first
    run, before any test existed.

    Setting it would also have been harmful rather than merely useless:
    a child inherits libc's environment at exec, so every subprocess the
    fold spawns afterwards would carry NW_LAYER and read as a live house
    to any later scan.

    A child exec'd WITH the variable is the honest form, held open until
    the scan has seen it."""
    p = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.readline()"],
        env=dict(os.environ, NW_LAYER=layer_id),
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, text=True)
    try:
        seen = scan_environ(layer_id)
        if p.pid not in seen:
            raise NotClosed(
                f"the environ scan did not find a probe child exec'd with "
                f"NW_LAYER={layer_id} while the scan ran. The scan is "
                f"broken, so its emptiness says nothing about whether the "
                f"house is closed. Found: {seen}")
    finally:
        try:
            p.stdin.write("x\n"); p.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass
        p.stdin.close(); p.wait(timeout=10)
    return p.pid


def _paired_mountinfo_probe():
    """The same pairing for the second scan, and this one costs a fork.

    A child unshares a mount namespace, mounts a marker overlay whose
    upperdir is a path nothing else uses, and holds it while the parent
    scans. The scan must see it, which also proves the cross-namespace
    part: the mount exists only in the child's namespace.

    Cheaper pairings were available and each asserts something else.
    Checking that /proc/self/mountinfo opens tests the open. Checking
    that the scan finds ANY overlay tests the machine. Only a mount this
    probe made, at a path nothing else uses, tests the match."""
    tmp = tempfile.mkdtemp(prefix="fold-house-probe-")
    up = os.path.join(tmp, "upper")
    for d in ("upper", "lower", "work", "mnt"):
        os.makedirs(os.path.join(tmp, d))
    cmd = (f"mount -t overlay fold-house-probe -o "
           f"lowerdir={tmp}/lower,upperdir={up},workdir={tmp}/work "
           f"{tmp}/mnt || exit 1; echo ok; read _")
    p = subprocess.Popen(
        ["unshare", "-m", "--propagation", "private", "sh", "-c", cmd],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    try:
        ready = p.stdout.readline()
        if ready.strip() != "ok":
            raise NotClosed(
                f"the mountinfo probe could not mount a marker overlay, so "
                f"the scan below would be unpaired: an empty result is then "
                f"satisfied by a broken scan as much as by a closed house. "
                f"This needs CAP_SYS_ADMIN and an overlay driver. "
                f"({p.stderr.read().strip()[:200]})")
        if not scan_mountinfo(up):
            raise NotClosed(
                f"the mountinfo scan did not find a marker overlay that was "
                f"mounted with upperdir={up} while it ran. The scan is "
                f"broken, so its emptiness says nothing.")
    finally:
        try:
            p.stdin.write("x\n"); p.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass
        p.stdin.close(); p.wait(timeout=10)
        subprocess.run(["rm", "-rf", tmp], check=False)


def require_closed(layer_id, upper_path):
    """Both scans empty, both paired. Returns None; raises NotClosed."""
    probe = _paired_environ_probe(layer_id)
    _paired_mountinfo_probe()

    # The probe child has exited by now; excluding its pid anyway costs
    # nothing and removes a reuse window that would refuse a legal fold.
    live = [p for p in scan_environ(layer_id) if p != probe]
    if live:
        raise NotClosed(
            f"a process still carries NW_LAYER={layer_id}: pid(s) {live}. "
            f"That is the supervisor, the house, or a child of one, so the "
            f"unit can still run and write. Folding now captures a "
            f"half-written layer into an image that mounts, boots, and is "
            f"wrong, with nothing in the bytes recording it.")
    mounted = scan_mountinfo(upper_path)
    if mounted:
        raise NotClosed(
            f"a process has {upper_path} mounted as an overlay upperdir: "
            f"pid(s) {mounted}. It carries no NW_LAYER -- a grandchild that "
            f"exec'd with a fresh environment does not -- so only the "
            f"kernel's own mount table sees it.")


# --- the helper ----------------------------------------------------------

_LID_BITS = (("newns", 1), ("newnet", 2), ("seccomp", 4), ("landlock", 8))


def _verify_city_is_live(city, live_blob, quiet):
    """The city must be the one that is running, and this proves it by
    re-baking and comparing bytes.

    Without it the helper edits a plan that may not be the live one and
    stages a candidate derived from something else -- the
    silent-wrong-artifact case one level up from the fold's own. The
    baker is reproducible (measured: two bakes of one city more than a
    second apart are byte-identical), so a mismatch means the city is not
    what booted, rather than that the clock moved."""
    with tempfile.TemporaryDirectory(prefix="fold-house-verify-") as t:
        out = os.path.join(t, "check.blob")
        r = subprocess.run([sys.executable,
                            os.path.join(_ROOT, "bakery", "nw-cc.py"),
                            "--city", city, "--out", out],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"fold-house: {city} does not bake:\n"
                             f"{r.stdout}{r.stderr}")
        if open(out, "rb").read() != open(live_blob, "rb").read():
            raise SystemExit(
                f"fold-house: {city} does not bake to {live_blob}, so it is "
                f"not the plan this machine is running. Folding against it "
                f"would save a layer belonging to one plan into a candidate "
                f"derived from another. Point --city at the source of the "
                f"live plan.")
    if not quiet:
        print(f"fold-house: {city} re-bakes to the live plan, byte for byte")


def city_text(houses):
    """One house per line, in the syntax `load_city` reads.

    The output is re-parsed and re-baked by the stager, so a field this
    cannot express becomes a bake error rather than a quietly dropped
    one -- which is why nothing here tries to be clever about defaults."""
    out = []
    for h in houses:
        bits = [f"house {h['name']} {h['exec']}",
                f"kind={'oneshot' if h['kind'] == 0 else 'longrun'}",
                f"budget={h['budget']}"]
        names = [n for n, b in _LID_BITS if h["lids"] & b]
        bits.append("lids=" + (",".join(names) if names else "none"))
        if h["brick"]:
            bits.append(f"brick={h['brick']}")
        if h["layer"]:
            bits.append(f"layer={h['layer']}")
        for b in h["binds"]:
            bits.append(f"bind={b}")
        out.append(" ".join(bits))
    return "\n".join(out) + "\n"


def derive_layer_id(unit, image_hash, name_len):
    """A fresh id, keyed to the CONTENT rather than to the unit or the
    clock. Two folds producing the same bytes produce the same id, which
    is the same idempotence the brick has; two producing different bytes
    cannot collide.

    Not the unit name alone -- that is the id the old layer already has,
    and reusing it is the failure this tool exists to avoid."""
    stem = f"{unit}-{image_hash[:12]}"
    return stem[:name_len - 1]


def fold_house(slots, city, unit, root="", nw_check=None, new_layer=None,
               quiet=False, out_dir=None):
    baker = _by_path("nw_cc", "bakery/nw-cc.py")
    stager = _by_path("stage_candidate", "tools/stage-candidate.py")

    live = stager.live_slot(slots)
    live_blob = os.path.join(slots, live, "plan.blob")
    _verify_city_is_live(city, live_blob, quiet)

    houses = baker.load_city(city)
    target = next((h for h in houses if h["name"] == unit), None)
    if target is None:
        raise SystemExit(
            f"fold-house: no house named {unit!r} in {city} "
            f"(have: {', '.join(h['name'] for h in houses)})")
    if not target["brick"] or not target["layer"]:
        raise SystemExit(
            f"fold-house: house {unit} has no brick and layer, so there is "
            f"nothing to fold. Only a house with a writable layer over a "
            f"sealed image has content to save.")

    layer_id = target["layer"]
    name_len = int(mkbrick._define("NW_NAME_LEN"))
    brick_dir = out_dir or (root + mkbrick.brick_dir())
    base_img = os.path.join(brick_dir,
                            target["brick"] + mkbrick._define("NW_BRICK_SUFFIX"))
    layer_dir = os.path.join(root + mkbrick._define("NW_LAYER_DIR"), layer_id)
    upper = os.path.join(layer_dir, mkbrick._define("NW_LAYER_UPPER"))

    require_closed(layer_id, upper)
    if not quiet:
        print(f"fold-house: {unit} is closed (no NW_LAYER={layer_id} and no "
              f"upperdir={upper}, both scans paired)")

    r = foldmod.fold(base_img, layer_dir, brick_dir, unit)
    if not quiet:
        print(f"fold-house: folded {layer_id} onto {target['brick'][:12]} "
              f"-> {r.image_hash[:12]} ({r.out_path})")

    nid = new_layer or derive_layer_id(unit, r.image_hash, name_len)
    if nid == layer_id:
        raise SystemExit(
            f"fold-house: the candidate's layer id {nid!r} is the one the "
            f"live plan already uses. Reusing it stacks the just-folded "
            f"content over itself and re-applies its whiteouts, deleting "
            f"files the fold just saved.")
    target["brick"] = r.image_hash
    target["layer"] = nid

    with tempfile.TemporaryDirectory(prefix="fold-house-city-") as t:
        newcity = os.path.join(t, "candidate.city")
        open(newcity, "w").write(city_text(houses))
        slot, ids = stager.stage(slots, newcity, root=root,
                                 nw_check=nw_check, quiet=quiet)
    if not quiet:
        print(f"fold-house: saved. brick {r.image_hash} exists now and the "
              f"old one is untouched; slot {slot} boots it with layer {nid}. "
              f"Nothing switches until the next boot.")
    return r.image_hash, nid, slot


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fold-house")
    ap.add_argument("--slots", required=True)
    ap.add_argument("--city", required=True,
                    help="the source the LIVE plan was baked from; verified")
    ap.add_argument("--unit", required=True)
    ap.add_argument("--root", default="")
    ap.add_argument("--nw-check", default=None, dest="nw_check")
    ap.add_argument("--layer-id", default=None, dest="new_layer",
                    help="the candidate's layer id; derived if omitted")
    ap.add_argument("--out", default=None, dest="out_dir")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    try:
        fold_house(a.slots, a.city, a.unit, a.root, a.nw_check, a.new_layer,
                   a.quiet, a.out_dir)
    except NotClosed as e:
        print(f"fold-house: refusing, the house is not closed: {e}",
              file=sys.stderr)
        return 1
    except foldmod.MergeError as e:
        print(f"fold-house: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
