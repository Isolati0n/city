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
itself -- the switch is the operator's. (That is the whole argument. An
earlier version cited invariant 7 and over-reached: writing `current`
IS the A/B switch that invariant prescribes, not an in-place rewrite,
and it takes effect only at the next boot. `claims`.)

THE RULE THIS SITS UNDER is not "nothing privileged runs live". It is
**nothing changes what is running without going through validation and a
boot**. That is why `nw-check` runs here on the candidate before any of
it is visible. What it produces is a slot nobody is booted from AND the
layer directories that slot's plan names, which are on the machine root
-- idempotent, and read by no running house, but "only a slot" was an
absolute this file's own first line disproves. `claims`.

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
import errno
import hashlib
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


def _layer_bricks(path):
    """`{layer-id: brick}` from a `.layers` sidecar.

    A ONE-FIELD LINE IS FATAL, and that is not symmetry with
    `tools/stage-layers.py`, which accepts one. That tool asks which
    directories to create and field 0 answers it completely. This one
    asks whether a shared id sits over a different brick, and without
    field 1 there is no answer -- so it refuses rather than guessing,
    which is the same rule as the unreadable sidecars either side of
    this.
    """
    out = {}
    for n, line in enumerate(open(path), 1):
        if not line.strip():
            continue
        f = line.split()
        if len(f) != 2:
            raise SystemExit(
                f"stage-candidate: {path}:{n} has {len(f)} field(s), not "
                f"an id and a brick. A sidecar the baker wrote pairs "
                f"them; this tool needs the brick to tell a reused layer "
                f"from a folded one, and will not guess. Re-bake the "
                f"plan.")
        # FIELD 1 IS SHAPE-CHECKED. Removing this turns the
        # reversed-sidecar case in `test_candidate_stager_never_touches_
        # the_live_slot` red, which is the evidence; the paragraph below
        # is why it was added.
        #
        # WHAT THAT PINS IS THE FUNCTION, NOT BOTH CALL SITES. The suite
        # reaches this through the LIVE sidecar read; the candidate read
        # above it is unexercised, because `stage()` bakes that sidecar
        # itself moments earlier and no input can hand it a reversed
        # one. `control` skipped the check on the candidate site alone
        # and the suite stayed green. The candidate-side call is
        # defence against a baker that drifts, and its only evidence is
        # this comment -- which under this project's rule makes it a
        # hypothesis, said here rather than left to read as covered.
        #
        # The alphabet half is narrower still: `NW_NAME_LEN` is 32, so
        # no legal layer id reaches this length, and a length-only
        # check would pass every case the suite has. It is there for a
        # producer that is not the baker.
        #
        # `drift` reversed the baker's fields and this tool read the
        # sidecar as {brick: id}, which INVERTED the guard: the case it
        # exists for -- one live layer over a different brick -- came
        # back `clash=[]` and was accepted. The run still failed, but
        # further down and by a coincidence: `tools/stage-layers.py`
        # rejects a 64-character string as a layer id because
        # NW_NAME_LEN is 32. Shorten a brick hash below that, or relax
        # that id check, and the reversal is silent AND the guard is
        # disarmed. Checked here so the guard establishes its own input.
        #
        # The baker's `_is_hex64` spells the same closed lowercase
        # alphabet; a layer id cannot match it, because `[A-Za-z0-9_-]`
        # of length 64 is over NW_NAME_LEN and the baker refuses it.
        # WIDTH FROM blob.h, not spelled. `_blob_h`'s own docstring two
        # functions up says a second copy of a limit is the drift class
        # invariant 3 is about, and the first version of this check
        # wrote `64` twice -- a fourth hand-written copy of the brick
        # width, added by a change whose entire subject is that drift
        # class. `claims` found it under the docstring forbidding it.
        hexlen = int(_blob_h("NW_BRICK_HASH")) * 2
        if len(f[1]) != hexlen or any(c not in "0123456789abcdef"
                                      for c in f[1]):
            raise SystemExit(
                f"stage-candidate: {path}:{n} pairs {f[0]!r} with "
                f"{f[1]!r}, which is not a {hexlen}-character lowercase "
                f"hex brick. The baker writes the id first; a sidecar with "
                f"the fields the other way round reads as {{brick: id}} "
                f"here and inverts the reuse check rather than failing "
                f"it.")
        out[f[0]] = f[1]
    return out


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
    """The slot `<slots>/current` names, read STRICTLY -- not the way
    PID 1 reads it, which is the thing to know before relaxing it.

    `slot_from_current` reads `sizeof nm - 1` bytes and TRUNCATES: a
    `current` holding forty `A`s boots the slot named by thirty-one of
    them, no diagnostic. It trims only trailing `\n\r` and space where
    `.strip()` here also removes leading whitespace. Every divergence
    makes this tool stricter, so it refuses where it would otherwise
    guess which prefix is running -- but the sentence that used to be
    here, that PID 1 "would refuse" such a file, is false. `claims` and
    `control`, independently.

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
            f"stage-candidate: {p} holds {nm!r}. pid1.c would not refuse "
            f"it -- it truncates and boots a prefix -- so this tool "
            f"refuses rather than guess which prefix is running.")
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
    # THE GUARD, and its ORDER is the property rather than its
    # existence: it must run before anything is written. `control` kept
    # this block verbatim and moved it below the renames, and the suite
    # stayed green while the running machine's plan was replaced by the
    # candidate -- the test pinned that the guard prints, not that it
    # runs first. It is pinned now, by re-reading the live slot after
    # every refusal.
    #
    # Nor is it the only guard, which this comment used to say:
    # `slot_name_ok` and the nw-check gate are guards too, and the
    # alphabet is the guard UNDER this one -- `target == live` is a
    # string comparison, sound only because no two spellings name one
    # slot -- and a symlink IS another spelling. `control` pointed a
    # slot at the live one, `pick_candidate` followed it through
    # `os.path.isdir`, and the tool DERIVED it and replaced the running
    # plan while printing "untouched". The string compare stays because
    # it catches the common case before any path exists; `samefile`
    # below catches the rest.
    if target == live:
        raise SystemExit(
            f"stage-candidate: {target!r} is the live slot. Saving is "
            f"immediate and switching is deferred; writing the running "
            f"plan is the line that deferral exists to keep. Stage a "
            f"candidate and switch by writing {slots}/current yourself.")

    tdir = os.path.join(slots, target)
    ldir = os.path.join(slots, live)
    if (os.path.exists(tdir) and os.path.exists(ldir)
            and os.path.samefile(tdir, ldir)):
        raise SystemExit(
            f"stage-candidate: {target!r} and the live slot {live!r} are "
            f"the same directory. A slot name is a spelling and two "
            f"spellings can name one slot; this tool's guard is that it "
            f"does not write the running plan.")

    # WE create the slot directory, so a refusal can undo it. It used to
    # appear as a side effect of `os.makedirs(work)` creating parents,
    # and `control` showed the cost: one typo in `--slot` on a run that
    # then refused left `<slots>/ZZZ` behind for good, and the tool can
    # never derive a candidate again. "A refusal leaves the machine
    # exactly as it was" was false for that path.
    made_tdir = not os.path.isdir(tdir)
    if made_tdir:
        os.makedirs(tdir)

    # Bake into a scratch directory INSIDE the slot, because os.replace
    # is rename(2) and rename needs one filesystem. A temp dir elsewhere
    # does NOT open a partial-file window -- it raises EXDEV and copies
    # nothing, loudly, before anything is renamed. (This said os.replace
    # "falls back to a copy across a mount point". That is shutil.move;
    # `claims` and `control` each ran it. The choice is right and the
    # hazard named for it was invented.)
    #
    # THE LEADING DOT DOES NOT MAKE THIS UNBOOTABLE and no longer
    # claims to. `control` measured it: the scratch dir is a GRANDCHILD
    # of <slots>, and `slot_from_current` composes <slots>/<name>, so no
    # value of `current` can name it whatever it is called -- a
    # non-dotted leftover is equally unreachable that way, and equally
    # bootable through `--slot`, which names a directory directly.
    # (Such a leftover needs a signal whose default action terminates
    # without unwinding -- SIGTERM, SIGHUP and SIGQUIT all do, not only
    # SIGKILL, because Python installs no handler for them and
    # `finally` does not run. SIGINT is the exception that makes the
    # narrower claim feel true. A plan nothing validated, in a slot, is
    # what that costs. `control` measured rc=143.)
    #
    # The dot's real consumer is the suite: `_no_scratch` keys on it.
    # Said plainly because the first version of this comment called the
    # dot load-bearing for bootability, and dropping it left the suite
    # green while blinding the detector.
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

        # A CANDIDATE MUST NOT REUSE A LAYER THE LIVE PLAN NAMES,
        # checked before the layer directories and before the renames.
        # NOT "before anything is created": by this line the slot
        # directory and a complete, validated candidate already exist
        # inside the scratch dir, and what removes them is the
        # `finally` -- which a SIGTERM skips (above) and which can
        # itself fail (below). The stronger sentence was in this file
        # sixty lines under its own retraction. `control`.
        #
        # Not hygiene. `control` staged a candidate whose layer id was
        # the running plan's, and this tool printed "live slot A,
        # untouched" while wiring the candidate to the running house's
        # writable area -- a message true of the slot and false of the
        # machine. After a fold it is worse than sharing: the folded
        # brick already contains that layer's contents, so the candidate
        # stacks them over themselves and the old whiteouts re-delete
        # files now baked in.
        #
        # OVER THE SAME BRICK IT IS PERMITTED, and that is a narrowing
        # of what this refused until 2026-09-13. Both reasons above are
        # about a house whose brick CHANGED -- the fold reason says so,
        # and the wiring reason is about the next boot, where exactly
        # one plan runs and an unchanged house keeping its id is the
        # whole purpose of keying a layer by a declared id rather than
        # by the house name. Refusing every shared id meant a two-house
        # city could never fold one house without orphaning the other's
        # data, which is the failure the keying exists to prevent.
        #
        # It was invisible from the rejection side: a refusal test is
        # satisfied by a refusal for any reason, so the case that found
        # it is a legal candidate that MUST be accepted --
        # `.claude/rules/plan.md`'s missing-DIRECTION lesson, arriving
        # in the stager. The instrument is the same-brick carry-over
        # case in `test_candidate_stager_never_touches_the_live_slot`,
        # which stages a candidate sharing a live id and requires exit
        # 0; the two-house fixture in the fold-house test is what found
        # it, from further away. The refusal case beside the carry-over
        # is the other direction, and neither substitutes.
        #
        # ESTABLISHED HERE, not taken from the caller. The fold helper
        # knows which ids it did not fold, and passing that in would be
        # an override -- the shape this guard exists to resist. The
        # brick in the sidecar lets this tool answer for itself.
        #
        # What is NOT distinguished: a fold from an ordinary brick
        # upgrade that keeps its data. Both change the brick and both
        # are refused, so there is no upgrade-with-data path through
        # this tool. That is NOT a cost of this narrowing -- the
        # previous guard refused every shared id, so it refused that
        # too. This change only widens what is accepted, and the
        # remaining refusal is the conservative half of a distinction
        # nothing here can draw. An open design question, and older
        # than this line.
        #
        # Both sides come from the sidecar the baker writes, so neither
        # needs a second copy of the blob layout. A live plan with no
        # sidecar is a refusal for the reason an unreadable `current`
        # is: the tool cannot establish the property, and guessing is
        # how it goes wrong quietly. If two plans ever need to share a
        # layer, that is a design decision and this is where it is made.
        want = _layer_bricks(blob + ".layers")
        live_side = os.path.join(slots, live, "plan.blob.layers")
        # THE LIVE SIDECAR MUST DESCRIBE THE LIVE BLOB, and this tool is
        # the reason it might not. The renames below put the sidecars in
        # first and the blob last, which is invisible to a boot -- and
        # NOT invisible here, because this check reads a sidecar. A
        # SIGKILL in that window leaves new sidecars beside an old blob,
        # and `control` walked it all the way through: switch to that
        # slot, stage against it, and the clash check reads layer ids
        # the running plan does not use and says nothing.
        #
        # AND THIS TOOL IS NOT THE ONLY READER. `tools/stage-layers.py`
        # reads `.layers` too, and `.claude/rules/runtime.md`'s THE
        # RECOVERY tells the operator to run exactly that on a slot's
        # blob. In the window it stages the layers of a plan that is
        # not there, and the slot boots into `FAIL mount layer`. It
        # carries the same sha check now; the comment that said the
        # window was visible only here enumerated two of three
        # readers.
        #
        # Checked through `.sha256` rather than by parsing the blob,
        # which would be the third copy of the unit layout. The baker
        # writes both sidecars together, so one of them matching the
        # blob is what says the set is current.
        live_blob = os.path.join(slots, live, "plan.blob")
        live_sha = live_blob + ".sha256"
        try:
            want_sha = open(live_sha).read().strip()
            got_sha = hashlib.sha256(open(live_blob, "rb").read()).hexdigest()
            live_ids = _layer_bricks(live_side)
        except OSError as e:
            raise SystemExit(
                f"stage-candidate: cannot read the live plan's sidecars "
                f"({e}), so this tool cannot tell whether the candidate "
                f"reuses a layer the running plan is using. Refusing.")
        if want_sha != got_sha:
            raise SystemExit(
                f"stage-candidate: {live_sha} does not describe "
                f"{live_blob}, so the live plan's sidecars are stale -- "
                f"an interrupted stage leaves exactly this. The layer "
                f"list beside them cannot be trusted, and it is what "
                f"says whether this candidate reuses a running layer. "
                f"Refusing. Re-stage the live slot, or write the "
                f"hexdigest alone into that file -- `sha256sum` emits "
                f"'<hash>  <name>', which this compares whole and "
                f"would reject forever.")
        clash = sorted(i for i in (want.keys() & live_ids.keys())
                       if want[i] != live_ids[i])
        if clash:
            raise SystemExit(
                f"stage-candidate: the candidate reuses layer(s) the live "
                f"plan is using, over a DIFFERENT brick: "
                f"{' '.join(clash)}. The folded brick already contains "
                f"that layer's contents, so reusing the id stacks them "
                f"over themselves and the old whiteouts re-delete files "
                f"now baked in. A folded house gets a new layer id. "
                f"(Reusing an id over the SAME brick is how an unchanged "
                f"house keeps its data across a plan change, and is "
                f"permitted.)")

        # The layer directories this plan names, through the one creator.
        #
        # BEFORE THE RENAMES, and that order matters as much as the one
        # below: a candidate whose blob is live but whose layers were
        # never created boots into `FAIL mount layer`. `control` moved
        # this after the renames, injected a failure in `stage()`, and
        # got exactly that -- a new candidate in the slot with no layer
        # for it -- while the suite stayed green. Like the sidecar
        # ordering, argued here and not tested: the suite has no way to
        # make `stage()` fail.
        ids = stage_layers.stage(blob, root)

        # `.sha256` FIRST, AND THAT IS NOT COSMETIC: the clash check
        # above infers "the .layers beside it is current" from ".sha256
        # matches the blob", and that inference holds only because
        # .sha256 can never be older than .layers. Swap this tuple and
        # an interruption between them leaves .layers new, .sha256 old
        # and the blob old -- the sha matches, the check passes, and a
        # stale layer list is trusted. `control` swapped it, the suite
        # stayed green, and walked the reopened defect end to end.
        # Pinned now by a source-level assertion in the suite, because
        # the order is not observable after the fact.
        #
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
        # And the slot directory itself, if we made it and nothing
        # landed in it. `os.rmdir` refusing a non-empty directory is
        # the check rather than a second one.
        #
        # ENOTEMPTY IS THE ONLY ONE SWALLOWED. A bare `except OSError`
        # was two stacked silences: `ignore_errors=True` above turns
        # any failure to remove the scratch into an ENOTEMPTY here, so
        # every other errno -- EACCES, EBUSY, ENOTDIR -- reported
        # "could not clean up" as nothing at all, and the slot stayed
        # behind to poison the next derivation. `control` forced it
        # with a tmpfs inside the scratch.
        if made_tdir:
            try:
                os.rmdir(tdir)
            except OSError as e:
                # ENOENT means it is already gone, which is success, and
                # raising from a `finally` DISCARDS the SystemExit on
                # its way out -- so the tool would answer a refusal with
                # a traceback about cleanup. `control` reached it by
                # removing the slot under a slow --nw-check.
                if e.errno not in (errno.ENOTEMPTY, errno.ENOENT):
                    raise
                print(f"stage-candidate: left {tdir} behind: it is not "
                      f"empty, which means the scratch directory could "
                      f"not be removed. The next derived run will call "
                      f"this slot ambiguous.", file=sys.stderr)

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
