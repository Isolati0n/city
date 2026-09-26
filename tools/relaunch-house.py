#!/usr/bin/env python3
"""Crash-and-relaunch: reproduce a house's death in an isolated,
throwaway attempt. docs/options/13-crash-relaunch.md.

    python3 tools/relaunch-house.py --evidence PATH.evt --slots DIR \
        [--fresh-layer] [--hold-ms N] [--bin DIR] [--keep-layer]

Reads the evidence package #8 already produces (unit, reason, value),
resolves that unit's declared fields from the CURRENTLY LIVE slot's
sealed blob via `unit-info` (a separate, non-TCB tool -- no second blob
parser here), prepares an isolated, throwaway layer (a copy of the real
one as it was at death, by default; `--fresh-layer` for an empty one
instead), and bakes a brand-new, ONE-UNIT plan for a throwaway house:
same exec path, same lids, same brick hash (safe to share; immutable),
the throwaway layer id, `budget=0` (one attempt, never the real restart
budget -- question 6), under a random throwaway name. That plan is
booted through the REAL boot chain -- `nw-root` (PID 1) under
`unshare --pid --fork --mount-proc`, the same invocation
`tests/run.py`'s own `boot()` helper uses -- rather than by invoking
`nw-sup` standalone: a standalone `nw-sup` has no PID-1-spawned logger
process, so its evidence packages always have an empty tail (measured
directly while building this tool -- an early design invoked `nw-sup`
directly and every tail came back empty, which a real boot's own
per-unit logger does not have). Booting through `nw-root` costs nothing
this feature does not already have -- `nw-check` validates the
throwaway plan for real, and the tail comes back genuine.

`--hold-ms` bounds the observation window the same way it bounds any
other boot in this tree -- PID 1's own hold, not a second, tool-side
clock. Three verdicts, per question 4's exact-match rule (same
`reason`, same `value` as the original):

  reproduced        -- the throwaway attempt died the same way.
  did-not-reproduce -- it exited cleanly on its own, or died differently.
  inconclusive       -- still running when the hold ended (PID 1 had to
                        kill it at shutdown, not on its own account).

Nothing about the real running city is touched: not its control
sockets, not its real supervisor processes, not its real layers -- see
question 3 in the design note for why. COMMITMENT-5: this only ever
runs when a person runs it; nothing here is wired to fire on its own.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util
import os
import secrets
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    """Import another tool in this tree by path, the same
    `importlib.util.spec_from_file_location` pattern `tests/run.py`
    already uses for every hyphenated tool name it needs. Not a second
    copy of what these tools do -- their functions are called, not
    re-derived."""
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, rel))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_stage_candidate = _load("relaunch_stage_candidate", "tools/stage-candidate.py")
_stage_layers = _load("relaunch_stage_layers", "tools/stage-layers.py")
_mkbrick = _load("relaunch_mkbrick", "bakery/mkbrick.py")
_nwcc = _load("relaunch_nwcc", "bakery/nw-cc.py")

# Inverse of bakery/nw-cc.py's own LID_NAMES/KINDS -- read from the same
# dicts the baker parses city files with, not a second, hand-typed
# mapping that could drift from them.
_LID_TOKENS = {v: k for k, v in _nwcc.LID_NAMES.items() if k != "none"}
_KIND_TOKENS = {v: k for k, v in _nwcc.KINDS.items()}


def read_evidence(path):
    """Parse a #8 `.evt` package: (fields dict, raw tail bytes). The
    same shape `tests/run.py`'s own `_read_evidence` parses, written
    again here rather than imported from a test-only module -- this is
    production-adjacent tooling, not a test, and `tests/run.py` is not
    meant to be imported by anything but itself."""
    data = open(path, "rb").read()
    sep = b"\n--\n"
    i = data.find(sep)
    if i < 0:
        raise SystemExit(f"relaunch-house: {path} has no '--' separator; "
                          f"not an NWEVT1 package")
    header = data[:i].decode("utf-8", "replace")
    tail = data[i + len(sep):]
    lines = header.split("\n")
    if not lines or lines[0] != "NWEVT1":
        raise SystemExit(f"relaunch-house: {path} bad magic line "
                          f"{lines[0] if lines else ''!r}")
    fields = {}
    for line in lines[1:]:
        if not line:
            continue
        k, _, v = line.partition("=")
        fields[k] = v
    return fields, tail


def run_unit_info(unit_info_bin, blob, unit_name):
    """Invoke the `unit-info` tool and parse its KEY=VALUE output into
    (fields dict, ordered bind-path list). Refuses (raises) exactly
    when `unit-info` itself refuses -- an invalid blob or an unknown
    unit name -- rather than guessing at a default."""
    r = subprocess.run([unit_info_bin, blob, unit_name],
                        capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(
            f"relaunch-house: unit-info could not read {unit_name!r} "
            f"from {blob}: {r.stderr.strip()}")
    fields = {}
    binds = []
    for line in r.stdout.splitlines():
        k, _, v = line.partition("=")
        if k.startswith("BIND_"):
            binds.append(v)
        else:
            fields[k] = v
    return fields, binds


def lids_token(lids_int):
    """A city-file `lids=` value from the integer bitmask unit-info
    reports -- `none` if no bit is set, a comma-joined token list
    otherwise. Order is fixed (dict insertion order of LID_NAMES,
    minus `none`) so this is deterministic, not that the baker cares
    about order."""
    bits = int(lids_int)
    toks = [name for bit, name in _LID_TOKENS.items() if bits & bit]
    return ",".join(toks) if toks else "none"


def prepare_layer(layer_base, orig_id, new_id, layer_bytes, fresh):
    """Create the throwaway layer `new_id` under `layer_base`
    (NW_LAYER_DIR), either as a copy of `orig_id` AS IT STANDS ON DISK
    NOW (question 1's default) or freshly empty (`--fresh-layer`).
    Reuses `tools/stage-layers.py`'s own naming (`_names`) and its own
    sized-store builder (`_make_sized_store`) rather than re-deriving
    either -- there is one creator of a FRESH layer's shape, and this
    calls it instead of duplicating it; only the disk-to-disk COPY step
    for the as-it-was case is new here, because copying an EXISTING
    layer is not a case stage-layers.py's own job (creating what a
    plan declares) ever needed."""
    upper = _stage_layers._names("NW_LAYER_UPPER")
    work = _stage_layers._names("NW_LAYER_WORK")
    suffix = _stage_layers._names("NW_LAYER_STORE_SUFFIX")

    new_mount = os.path.join(layer_base, new_id)
    os.makedirs(new_mount, exist_ok=True)

    if layer_bytes:
        if fresh:
            _stage_layers._make_sized_store(
                layer_base, new_id, layer_bytes, upper, work)
            return
        orig_img = os.path.join(layer_base, orig_id + suffix)
        if not os.path.exists(orig_img):
            raise SystemExit(
                f"relaunch-house: {orig_id!r} declares layer_bytes="
                f"{layer_bytes} but {orig_img} does not exist on this "
                f"machine -- nothing to copy. (Same-machine only: this "
                f"tool reads the live blob and the live layer store, "
                f"neither of which travels with an evidence package.)")
        new_img = os.path.join(layer_base, new_id + suffix)
        shutil.copyfile(orig_img, new_img)
        return

    if fresh:
        for leaf in (upper, work):
            os.makedirs(os.path.join(new_mount, leaf), exist_ok=True)
        return
    orig_upper = os.path.join(layer_base, orig_id, upper)
    if not os.path.exists(orig_upper):
        raise SystemExit(
            f"relaunch-house: {orig_id!r} has no {orig_upper} on this "
            f"machine -- nothing to copy.")
    # symlinks=True: a landlock-confined brick house can create a
    # symlink in its own upper (MAKE_SYM is not among the withheld
    # Landlock rights, per runtime.md's Bricks section), and the
    # default (False) FOLLOWS it, copying whatever it points at --
    # which could be a host path outside this layer entirely, into the
    # throwaway copy. Recreating the symlink as a symlink, instead, is
    # also the more faithful "as it was at death" copy: the target is
    # resolved fresh from the throwaway's own root, exactly as it would
    # be for the original. `tcb-review`.
    shutil.copytree(orig_upper, os.path.join(new_mount, upper), symlinks=True)
    # WORK IS NEVER COPIED, deliberately -- it is overlayfs's own
    # transient, internal scratch space, never meant to be interpreted
    # by anything else, and a copy of a workdir left dirty by an
    # unclean unmount is not a state anyone is asking to reproduce.
    # Question 1's "as it was at death" is about the house's own data
    # (upper), not the kernel's mounting bookkeeping (work).
    os.makedirs(os.path.join(new_mount, work), exist_ok=True)


def remove_layer(layer_base, layer_id, layer_bytes):
    suffix = _stage_layers._names("NW_LAYER_STORE_SUFFIX")
    shutil.rmtree(os.path.join(layer_base, layer_id), ignore_errors=True)
    if layer_bytes:
        try:
            os.unlink(os.path.join(layer_base, layer_id + suffix))
        except FileNotFoundError:
            pass


def _refuse_if_unsafe_for_city_line(label, value):
    """`bakery/nw-cc.py`'s city-file grammar is unescaped and
    whitespace/`#`-delimited (`load_city()`: `line.split("#", 1)[0]`
    then `line.split()`) -- the same grammar this function's own
    caller re-serializes a validated blob's fields THROUGH. `nwcheck.c`'s
    `path_ok_len()` rejects control bytes, DEL and `..` components, but
    NOT space or `#`, so a blob that legitimately passes `nw_check()`
    (which is the only guarantee this tool has -- `plan.md`: "a blob
    can arrive from anywhere") can carry an `exec_path` or `bind=` value
    that would silently truncate the line at a `#` (turning `budget=0`
    and everything after it into a dropped comment) or split into extra
    tokens at a space (the same effect, or the baker refusing loudly,
    depending on exactly where the split lands) -- either way this
    tool's own `budget=0`/`kind=`/`lids=` guarantees for the THROWAWAY
    plan would be silently overridden by bytes that came from the unit
    being investigated, not from this tool. `tcb-review` reproduced
    both shapes directly (a silent budget/kind/lids override, and a
    loud baker refusal, depending on where the injection landed).

    Refused here, at the boundary, the same way `layer=` is already
    restricted to `name_ok`'s closed alphabet specifically so it cannot
    reach this class of bug -- not patched by escaping or quoting the
    join, which this project's own record treats as the weaker fix."""
    if any(c.isspace() for c in value) or "#" in value:
        raise SystemExit(
            f"relaunch-house: {label} {value!r} contains whitespace or "
            f"'#', which the city-file grammar cannot represent safely. "
            f"Refusing rather than silently truncating or misparsing "
            f"the throwaway plan.")


def bake_throwaway(name, fields, binds, layer_id, layer_bytes, out_blob):
    """Write a one-line city file for the throwaway unit and bake it
    with the real baker (a subprocess, the same way `tests/run.py`
    always invokes it -- not by importing bake() and calling it
    in-process, which nothing else in this tree does either)."""
    _refuse_if_unsafe_for_city_line("EXEC_PATH", fields["EXEC_PATH"])
    for b in binds:
        _refuse_if_unsafe_for_city_line("a bind path", b)

    parts = [
        "house", name, fields["EXEC_PATH"],
        f"kind={_KIND_TOKENS[int(fields['KIND'])]}",
        "budget=0",
        f"lids={lids_token(fields['LIDS'])}",
    ]
    if fields.get("BRICK"):
        parts.append(f"brick={fields['BRICK']}")
        parts.append(f"layer={layer_id}")
        if layer_bytes:
            parts.append(f"layer-bytes={layer_bytes}")
    for b in binds:
        parts.append(f"bind={b}")
    line = " ".join(parts) + "\n"

    with tempfile.NamedTemporaryFile(
            mode="w", suffix=".city", delete=False) as fh:
        fh.write(line)
        city_path = fh.name
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bakery", "nw-cc.py"),
             "--city", city_path, "--out", out_blob],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(
                f"relaunch-house: the baker refused the throwaway plan "
                f"it was given for {name!r}:\n{line}{r.stdout}{r.stderr}")
    finally:
        os.unlink(city_path)


def _city_closed(rc, out):
    """The same acceptance `tests/run.py`'s own `city_closed()` uses:
    `reboot(RB_POWER_OFF)` tears the pid namespace down inside
    `unshare --pid --fork`, so the parent sees 130 (or -2 from Python's
    own signal encoding) rather than PID 1 ever returning; if reboot()
    is denied, PID 1 prints `closed` and exits 0 instead. Either is a
    finished shutdown -- reimplemented here rather than imported,
    because `tests/run.py` is not meant to be imported by anything but
    itself."""
    if "Attempted to kill init" in out:
        return False
    if "[nw-root] closed" not in out:
        return False
    return rc in (0, 130, -2)


def still_running_at_shutdown(out, name):
    """True if the throwaway house was still alive when the hold
    elapsed -- PID 1 killed it as part of `shutdown_city()` rather than
    it exiting on its own account. Both cases print `house exit <name>
    status=...`, once PID 1 reaps it either way; what differs is WHEN,
    relative to `shutdown TERM houses`, which PID 1 prints only once,
    right before it signals whatever is still alive. A house that
    exits on its own (whether cleanly or by crashing) is reaped by
    `reap_all()` from the SIGCHLD handler DURING the hold loop, so its
    `house exit` line prints first; a house still running is only
    reaped AFTER the TERM, so its line prints second. Verified directly
    against all three real shapes while building this tool: a
    just-finished oneshot, a genuine crash, and a house still sleeping
    when the hold ran out -- the first two print `house exit` before
    `shutdown TERM houses`, only the third prints it after (or not at
    all, if PID 1 never gets that far)."""
    exit_marker = f"house exit {name} "
    shutdown_at = out.find("shutdown TERM houses")
    exit_at = out.find(exit_marker)
    if shutdown_at < 0:
        # Shutdown never even started printing -- can't have reaped a
        # still-running house normally either. Treat as still running
        # rather than guess.
        return True
    if exit_at < 0:
        return True
    return exit_at > shutdown_at


def find_new_evidence(evidence_dir, before, name):
    after = set(glob.glob(os.path.join(evidence_dir, "*.evt")))
    mine = []
    for path in after - before:
        try:
            fields, tail = read_evidence(path)
        except SystemExit:
            continue
        if fields.get("unit") == name:
            mine.append((path, fields, tail))
    return mine


def relaunch(evidence_path, slots, fresh_layer, hold_ms, bindir, keep_layer):
    orig_fields, _orig_tail = read_evidence(evidence_path)
    unit_name = orig_fields.get("unit")
    if not unit_name:
        raise SystemExit(f"relaunch-house: {evidence_path} has no unit= field")
    orig_reason = orig_fields.get("reason")
    orig_value = orig_fields.get("value")

    nw_root_bin = os.path.join(bindir, "nw-root")
    unit_info_bin = os.path.join(bindir, "unit-info")

    live = _stage_candidate.live_slot(slots)
    blob = os.path.join(slots, live, "plan.blob")
    if not os.path.exists(blob):
        raise SystemExit(
            f"relaunch-house: live slot {live!r} has no plan.blob at {blob}")

    fields, binds = run_unit_info(unit_info_bin, blob, unit_name)
    layer_bytes = int(fields.get("LAYER_BYTES") or "0")
    orig_layer = fields.get("LAYER") or ""

    throwaway_name = "rl" + secrets.token_hex(6)
    throwaway_layer = None
    layer_base = _stage_layers._names("NW_LAYER_DIR")
    if orig_layer:
        throwaway_layer = "rl" + secrets.token_hex(6)
        prepare_layer(layer_base, orig_layer, throwaway_layer,
                      layer_bytes, fresh_layer)

    evidence_dir = _mkbrick._define("NW_EVIDENCE_DIR")
    before = set(glob.glob(os.path.join(evidence_dir, "*.evt")))

    throwaway_blob = None
    try:
        with tempfile.TemporaryDirectory(prefix="relaunch-house-") as tmp:
            throwaway_blob = os.path.join(tmp, "throwaway.blob")
            bake_throwaway(throwaway_name, fields, binds, throwaway_layer,
                           layer_bytes, throwaway_blob)

            cmd = ["unshare", "--pid", "--fork", "--mount-proc", "--",
                   nw_root_bin, "--hold-ms", str(int(hold_ms)), throwaway_blob]
            r = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=hold_ms / 1000.0 + 30)
            out = (r.stdout or "") + (r.stderr or "")
            rc = r.returncode

        mine = find_new_evidence(evidence_dir, before, throwaway_name)

        if not _city_closed(rc, out):
            verdict = "inconclusive"
            new_fields, new_tail = None, None
        elif still_running_at_shutdown(out, throwaway_name):
            verdict = "inconclusive"
            new_fields, new_tail = None, None
        elif not mine:
            verdict = "did-not-reproduce"
            new_fields, new_tail = None, None
        else:
            # budget=0 caps this at one death for this throwaway unit,
            # so there is exactly one package to read, not a set to
            # pick from the way #8's own multi-death tests have to.
            _path, new_fields, new_tail = mine[0]
            if (new_fields.get("reason") == orig_reason and
                    new_fields.get("value") == orig_value):
                verdict = "reproduced"
            else:
                verdict = "did-not-reproduce"
    finally:
        if throwaway_layer and not keep_layer:
            remove_layer(layer_base, throwaway_layer, layer_bytes)

    return {
        "verdict": verdict,
        "unit": unit_name,
        "original": {"reason": orig_reason, "value": orig_value},
        "throwaway_name": throwaway_name,
        "throwaway_rc": rc,
        "throwaway_output": out,
        "throwaway_evidence": new_fields,
        "throwaway_tail": new_tail,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="relaunch-house",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evidence", required=True,
                     help="path to the #8 .evt package to investigate")
    ap.add_argument("--slots", required=True,
                     help="the plan's <slots> directory")
    ap.add_argument("--fresh-layer", action="store_true",
                     help="start the throwaway layer empty instead of copying "
                          "it as it was at death (question 1)")
    ap.add_argument("--hold-ms", type=int, default=10000,
                     help="milliseconds to observe the throwaway attempt "
                          "before reporting inconclusive (default: 10000)")
    ap.add_argument("--bin", default=ROOT,
                     help="directory holding nw-root and unit-info "
                          "(default: this repo's root)")
    ap.add_argument("--keep-layer", action="store_true",
                     help="do not delete the throwaway layer copy afterward "
                          "(for further manual inspection)")
    a = ap.parse_args(argv)

    result = relaunch(a.evidence, a.slots, a.fresh_layer, a.hold_ms, a.bin,
                       a.keep_layer)

    print(f"verdict: {result['verdict']}")
    print(f"unit: {result['unit']}")
    print(f"original: reason={result['original']['reason']} "
          f"value={result['original']['value']}")
    if result["throwaway_evidence"]:
        ev = result["throwaway_evidence"]
        print(f"throwaway: reason={ev.get('reason')} value={ev.get('value')}")
        print(f"throwaway tail: {result['throwaway_tail']!r}")
    else:
        print(f"throwaway: no evidence package "
              f"(boot rc={result['throwaway_rc']})")
    return 0 if result["verdict"] != "inconclusive" else 1


if __name__ == "__main__":
    sys.exit(main())
