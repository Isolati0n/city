#!/usr/bin/env python3
"""Stage a CANDIDATE slot: its plan, and the layer directories that plan
names. Never the live slot, and never `<slots>/current`.

WHAT THIS IS FOR, because the obvious simplification in either direction
destroys it. Saving is immediate and switching is deferred: the operator
does not reboot to save, they reboot to switch. This tool is the deferred
half. It writes a complete, validated candidate that takes effect at the
next boot and changes nothing about what is running now.

So the line it must not cross is writing the live plan, and the guard is
structural rather than a convention: it reads `<slots>/current` and
REFUSES when the target names that slot. It also never writes `current`
itself -- the switch is the operator's, and a tool that both stages and
switches is the in-place rewrite invariant 7 forbids, wearing two steps.

THE RULE THIS SITS UNDER is not "nothing privileged runs live". It is
**nothing changes what is running without going through validation and a
boot**. That is why `nw-check` runs here on the candidate before any of
it is visible, and why the only thing this produces is a slot nobody is
booted from.

    python3 tools/stage-candidate.py --slots DIR --city FILE \
        [--slot NAME] [--root DIR] [--nw-check PATH]

--slot names the candidate. Omitted, it is derived: the one slot
directory under `<slots>` that is not the live one. Ambiguous (none, or
more than one) is a refusal rather than a guess, because guessing which
slot to overwrite is the one mistake this tool must not make quietly.

--root prefixes the layer directory, for a staged tree whose root is not
the machine root; in production the root IS `/`, so it is not passed.
The same argument as `tools/stage-layers.py`, which this calls rather
than reimplementing -- there is ONE creator of layer directories and
this is not a second one.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "stage_layers",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "stage-layers.py"))
stage_layers = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(stage_layers)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _blob_h(name):
    """Read a #define out of blob.h rather than spelling it. Same rule as
    stage-layers.py and mkbrick.py: a second copy of a limit is the drift
    class invariant 3 is about."""
    for line in open(os.path.join(ROOT, "blob.h")):
        f = line.split()
        if len(f) >= 3 and f[0] == "#define" and f[1] == name:
            return f[2].strip('"')
    raise SystemExit(f"stage-candidate: blob.h has no {name}")


def slot_name_ok(nm):
    """The alphabet and bound PID 1 accepts for a slot name.

    `slot_from_current` in pid1.c reads at most NW_NAME_LEN bytes and
    requires every one to be [A-Za-z0-9_-], so a name with a slash or a
    dot cannot escape <slots>. Checked here for the same reason
    stage-layers checks a layer id a fourth time: this one turns a string
    into a directory, and a name PID 1 would refuse is a candidate that
    can never boot -- a failure at the next reboot instead of now.
    """
    return bool(nm) and len(nm) < int(_blob_h("NW_NAME_LEN")) and all(
        ("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9")
        or c in "_-" for c in nm)


def live_slot(slots):
    """The slot `<slots>/current` names, read the way PID 1 reads it.

    A refusal, not a default, when it cannot be read. The tool's one
    safety property is "not the live slot", and it cannot hold that
    property without knowing which slot is live. Defaulting to A here
    would make an unreadable `current` indistinguishable from a machine
    booted on B -- and the tool would then overwrite the running plan.
    """
    p = os.path.join(slots, "current")
    try:
        nm = open(p).read()
    except OSError as e:
        raise SystemExit(
            f"stage-candidate: cannot read {p} ({e}). Refusing: this tool's "
            f"whole guard is that it does not write the live slot, and it "
            f"cannot know which that is.")
    nm = nm.strip()
    if not slot_name_ok(nm):
        raise SystemExit(
            f"stage-candidate: {p} holds {nm!r}, which pid1.c would refuse. "
            f"Refusing rather than guessing what the machine will boot.")
    return nm


def pick_candidate(slots, live):
    """The one slot directory that is not the live one.

    Derived from the tree rather than hardcoded to A/B: pid1.c accepts
    any [A-Za-z0-9_-] name, so a tool that assumed two would be wrong the
    day a third appeared and would be wrong SILENTLY, by overwriting
    whichever one it guessed.
    """
    try:
        names = sorted(d for d in os.listdir(slots)
                       if os.path.isdir(os.path.join(slots, d)))
    except OSError as e:
        raise SystemExit(f"stage-candidate: cannot list {slots} ({e})")
    others = [d for d in names if d != live]
    if len(others) != 1:
        raise SystemExit(
            f"stage-candidate: cannot derive the candidate. Live slot is "
            f"{live!r} and the slot directories are {names!r}. Name one "
            f"with --slot; guessing which to overwrite is the one mistake "
            f"this tool must not make quietly.")
    return others[0]


def stage(slots, city, slot=None, root="", nw_check=None, quiet=False):
    live = live_slot(slots)
    target = slot if slot is not None else pick_candidate(slots, live)

    if not slot_name_ok(target):
        raise SystemExit(
            f"stage-candidate: {target!r} is not a slot name pid1.c would "
            f"accept, so a candidate written there could never boot.")
    # THE GUARD. Everything else in this file is plumbing.
    if target == live:
        raise SystemExit(
            f"stage-candidate: {target!r} is the live slot. Saving is "
            f"immediate and switching is deferred; writing the running "
            f"plan is the line that deferral exists to keep. Stage a "
            f"candidate and switch by writing {slots}/current yourself.")

    tdir = os.path.join(slots, target)
    os.makedirs(tdir, exist_ok=True)

    # Bake into a scratch directory INSIDE the slot, so the renames below
    # are same-filesystem and therefore atomic. A temp dir elsewhere
    # would make os.replace fall back to a copy across a mount point,
    # which is exactly the partial-file window this ordering avoids.
    # Named with a leading dot and the pid: pid1.c opens <slot>/plan.blob
    # and nothing else, so a leftover here cannot be booted.
    work = os.path.join(tdir, f".staging-{os.getpid()}")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    try:
        blob = os.path.join(work, "plan.blob")
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "bakery", "nw-cc.py"),
             "--city", city, "--out", blob],
            capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(
                "stage-candidate: the baker refused this city, so there is "
                "no candidate:\n" + r.stdout + r.stderr)

        # VALIDATE BEFORE ANYTHING IS VISIBLE. The rule is that nothing
        # takes effect without validation and a boot; this is the
        # validation half, and it runs on the bytes that will be renamed
        # rather than on a rebake of the same source.
        chk = nw_check or os.path.join(slots, "..", "..", "nw", "bin",
                                       "nw-check")
        chk = os.path.abspath(chk)
        if not os.path.exists(chk):
            raise SystemExit(
                f"stage-candidate: no nw-check at {chk}. Refusing to stage "
                f"a candidate nothing validated -- pass --nw-check.")
        v = subprocess.run([chk, blob], capture_output=True, text=True)
        if v.returncode != 0:
            raise SystemExit(
                "stage-candidate: nw-check refused the candidate, so it is "
                "not staged:\n" + v.stdout + v.stderr)

        # The layer directories this plan names, through the one creator.
        ids = stage_layers.stage(blob, root)

        # THE ORDER IS THE ATOMICITY, and it is one-directional: pid1.c
        # opens <slot>/plan.blob and reads nothing else, so the blob goes
        # LAST. A boot at any instant sees either the previous candidate
        # complete or this one complete. The window where the sidecars
        # are new and the blob is old is invisible to a boot; it is
        # visible only to this tool and the harness, which read the
        # sidecars and are not running during a boot.
        for suffix in (".sha256", ".layers"):
            os.replace(blob + suffix, os.path.join(tdir, "plan.blob" + suffix))
        os.replace(blob, os.path.join(tdir, "plan.blob"))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    if not quiet:
        print(f"stage-candidate: slot {target} staged and validated "
              f"(live slot is {live}, untouched; {slots}/current not "
              f"written). layers: " + (" ".join(ids) if ids else "none"))
    return target, ids


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stage-candidate")
    ap.add_argument("--slots", required=True)
    ap.add_argument("--city", required=True)
    ap.add_argument("--slot", default=None)
    ap.add_argument("--root", default="")
    ap.add_argument("--nw-check", default=None, dest="nw_check")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    stage(a.slots, a.city, a.slot, a.root, a.nw_check, a.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
