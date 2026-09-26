# 13 — Crash-and-relaunch

Status: **built and landed.** This note answers the six questions that
were asked and the COMMITMENT-5 check. It started as design-only, was
`claims`-reviewed before any code was written, and the mechanism
described below is what actually shipped -- including one correction
the build itself forced, recorded where it happened rather than
smoothed over (see "What changed between the design and the build",
after question 3).

## What this depends on, confirmed by reading the code

**A unit CAN be run completely standalone, outside the boot chain --
`nw-sup`'s `main()` (`nwsup.c`) takes `argv[1]` = exec path, `argv[2]` =
unit name, and reads every other input from the environment
(`NW_LIDS`, `NW_BUDGET`, `NW_KIND`, `NW_BRICK`, `NW_LAYER`,
`NW_LAYER_BYTES`, `NW_NBINDS` + `NW_BIND_<i>`), and `tests/run.py`'s own
ctl-channel tests already invoke it exactly that way. This is true, and
it is NOT what this feature ends up using** -- see the correction below.
What it establishes is narrower but still load-bearing: every input
`nw-sup` needs is a small, closed, already-known set, derived by
`nwspawn.c` from one `struct nw_unit` (a strict superset -- `nwspawn.c`
also sets `NW_UNIT`/`NW_HOUSE`, which `nw-sup` never reads). That is
what makes the rest of this feature possible without a new TCB
mechanism, whichever way the throwaway unit is actually launched.

**Getting those inputs safely means not re-deriving the blob format a
third time.** `tools/stage-layers.py`'s own docstring states the rule
this note is bound by: "this tool does not parse the blob: a third copy
of the unit layout... is the drift class invariant 3 is about." So this
feature needs a way to read one unit's fields out of a sealed blob
without a second, hand-written struct layout in Python -- `tools/unit-info.c`,
a new, small, non-TCB C tool that `#include`s `blob.h` directly and
walks `nw_units()`/`nw_binds()` -- the exact same accessors `nwcheck.c`
and `nwsup.c` already use -- after running `nw_check()` first (bug 1's
own lesson: verify the seal, never merely read it). **Not TCB** -- no
boot-time caller links it in, the same classification `tools/initrd-init.c`
already has despite also being C: `CLAUDE.md`'s TCB table is the
specific files in the boot chain, not "anything written in C." It is in
`all:` in the `Makefile` so `make`/`make test` actually compiles it,
rather than repeating `tools/initrd-init.c`'s own history (nothing
gated that file's compile for as long as it was built only inside
`mkboot.sh`, and a syntax error shipped undetected).

## 1. What "same starting state" means

**The brick is trivial and needs no decision: it is content-addressed
and immutable, so the relaunch reuses the exact same hash.** Nothing to
copy, nothing to reset -- and this needed its own check rather than
borrowing `test_many_brick_houses_all_start`'s evidence, which mounts
eight *distinct* content-addressed images concurrently (each house's
own `/id` differs, so each hashes to a different brick), not the same
hash twice. Verified directly instead: two independent `nw-sup`
processes invoked with the identical `NW_BRICK` hash and separate
layers both mount, pivot into, and run from it concurrently --
`[nw-sup] lid brick` and a clean `exit 0` from both, no interference,
no `EBUSY`, no shared state observed between them.

**The layer is the one real decision, so it is stated here plainly
rather than left implicit, per the brief's own instruction.** Two
things a relaunch could mean for a house that has a `layer=`:

- Use the layer **as it was at the moment of death** -- a byte-for-byte
  copy of the real `/nw/layers/<id>` (or `<id>.img`, if sized) into a
  **new, throwaway id**, never the real one.
- Use a **fresh, empty layer** -- the same starting point `layer=`
  would give the house on its very first-ever boot.

**Decided: copy-as-it-was-at-death is the default, and a fresh layer is
an explicit, secondary option (`--fresh-layer`).** The brief itself
raised "or is this a choice the operator makes" as a live alternative,
and it costs little to offer both rather than silently picking one: a
crash that depends on accumulated on-disk state (a corrupted file, a
partially-written index, a specific directory listing) will not
reproduce against a fresh layer, and an operator investigating "does
this crash on a clean slate too" is asking a real, different question
from "does this crash reproduce at all." Default to the state-preserving
copy because it is the more faithful reproduction of what actually
happened, and because "isolated" (question 3) already guarantees the
copy can never corrupt the real layer even if the relaunch attempt
writes to it exactly as destructively as whatever caused the original
crash.

**A house with no `layer=` (no brick, or a brick with no declared
writable state) has nothing to copy, and the relaunch just reuses its
declared fields as-is** -- there is no third state to invent for it.

**A house with no brick at all cannot be isolated the way a brick house
can, and this is said here rather than glossed over.** A brick forces
`NW_LID_NEWNS` (`nwcheck.c`'s `NW_E_BRICKNS`), but the reverse does not
hold -- a brickless house can still legally declare `lids=...,newns` and
get its own private mount namespace (`lid_newns()` runs whenever the
bit is set, brick or not). What a brickless house categorically lacks
is a **layer**: `NW_E_LAYERPAIR` requires a layer only alongside a
brick, so there is nothing declared, nothing on disk, and nothing to
copy for it -- there is no writable, keyed, copyable *boundary* the way
`/nw/layers/<id>` is one, regardless of whether the house also happens
to have its own mount namespace. And a house that is *both* brickless
and without `NW_LID_NEWNS` (`lids=none` is the common case) has neither
a namespace nor a layer, and shares the machine's own root outright --
invariant 5 and `runtime.md` are both explicit about that narrower
case. Either way, a relaunch of such a house runs the same exec path
against state that is not a copyable, throwaway layer, with no
isolation better than whatever mount-namespace privacy the house's own
declared lids already gave the original. That is not a gap this
feature is introducing; it is the same gap invariant 5 already
documents, restated here because this is the first feature whose
usefulness partly depends on isolation actually existing.

## 2. Are the command-line/environment inputs already captured?

**Yes, entirely, in the sealed plan blob that is already live -- #8's
evidence package needs no new field for this.** Every input `nw-sup`
needs (see above) is derived from one `struct nw_unit` plus that unit's
`struct nw_bind` rows, and the evidence package's `unit=` field already
names which one. The one thing the evidence package does *not* carry,
and does not need to, is *which blob* was live at the time -- see the
scope boundary below.

**Reading those fields without a third blob parser: `tools/unit-info.c`,
described in "What this depends on" above** -- rather than a
hand-written Python `struct.unpack`, this reads the same header
through the same accessors `nwcheck.c` and `nwsup.c` already use, and
is not TCB for the reasons given there. Not repeated here a second
time; this section is about *what* gets read (confirmed above this
paragraph), that section is about *how*.

**Scope boundary, stated rather than solved:** this reads the
*currently live* slot's blob (`<slots>/current`, the same resolution
`tools/stage-candidate.py`'s `live_slot()` already does), not a
historical blob from whenever the death actually happened. If the plan
has been switched since (a new slot chosen at a later boot) and the
unit's declaration changed or the unit was removed, the relaunch either
runs against a different declaration than the one that crashed or is
refused for naming a unit that no longer exists -- both honest
outcomes, neither silently wrong. Pinning evidence to the exact blob
that produced it needs a blob identity the plan does not carry yet
(`plan.md`'s own "waiting on a prerequisite" entry for a plan hash) and
is out of scope here, not quietly assumed.

## 3. Where the relaunch happens

**Decided: a completely separate, isolated, throwaway attempt. Never
the real running house, and never through the start/stop channel.**
Two independent reasons, not one:

- **The ctl channel cannot reach the case this feature exists for.**
  `START` only works while the unit's `nw-sup` process is still alive
  waiting on its control socket; once a unit's budget is spent, that
  process has already `_exit()`ed and unlinked its socket
  (`test_ctl_start_relaunch_and_spent` pins exactly this: "START on
  spent cannot connect"). A budget-exhausted crash loop -- the most
  likely reason an operator reaches for this feature at all -- has no
  live channel left to restart through. Building on top of `START`
  would only cover the one case (a still-running or merely-stopped
  house) that already has its own, simpler, live tool.
- **Even where a live channel exists, reusing it would risk the real
  house.** `START`/`STOP` act on the *real* supervisor, the *real*
  child, the *real* layer. A reproduction attempt is deliberately trying
  to make the same crash happen again; if the crash is (or is suspected
  of being) a destructive one, doing that to the real layer is the
  opposite of what an investigation tool should risk.

So: a brand-new, throwaway unit name (never the original), a brand-new,
throwaway layer id if the original has a layer (a copy, per question 1
-- never the original id), the same brick hash (safe to share, being
immutable), with its own evidence package landing in the same
`NW_EVIDENCE_DIR` every other death's package already does. Nothing
about the real city -- its running houses, its control sockets, its
layers -- is touched, connected to, or even required to still exist.
Verified directly, not only reasoned about: a real, multi-death
`budget=3` house was booted in the background while a relaunch of the
same unit ran concurrently, and the real house's own console output
reached exactly `death=4/3` (its own budget's natural exhaustion) with
no trace of the relaunch in its count either way.

## What changed between the design and the build

**The design said "invoked directly the way the ctl tests already
invoke `nw-sup`" (see "What this depends on" above). That is wrong, and
it was found by running it, not by reasoning about it.** A standalone
`nw-sup` invocation has no PID-1-spawned logger process -- `spawn_logger()`
lives in `pid1.c`'s boot sequence, and nothing stands in for it when
`nw-sup` is exec'd directly -- so `write_evidence()`'s read of the
unit's `.tail` file always comes back empty, every time, regardless of
what the house actually wrote. Measured directly while building this
tool: an early version invoked `nw-sup` standalone exactly like the
ctl-channel tests do, and every resulting evidence package -- including
ones for a house that had genuinely printed real output -- had
`tail_bytes=0`.

**The fix: boot the throwaway unit through the real chain instead.**
The throwaway unit's fields are baked into a genuine, ONE-UNIT sealed
plan (via the same baker, `bakery/nw-cc.py`, as a subprocess -- the way
`tests/run.py` itself always invokes it, not by importing its
functions), and that plan is booted with `nw-root` (PID 1) under
`unshare --pid --fork --mount-proc`, the exact invocation
`tests/run.py`'s own `boot()` helper uses. This costs nothing the
feature doesn't already need: `nw-check` validates the throwaway plan
for real (bug 1's own argument, applied to a plan this tool composes
rather than one a person wrote), and the resulting evidence package's
tail is genuine, verified directly (`boom\n` for a real crash,
`[nw-sup] lid newns` / `lid layer` / `lid brick` for a brick house).
**Zero new TCB code either way** -- `nw-root`, `nw-spawn`, `nw-sup` and
`nw-check` are exactly the same binaries either mechanism uses; what
changed is which of this project's own existing entry points into them
this tool calls.

**The observation window is PID 1's own `--hold-ms`, not a second,
tool-side clock.** `nw-root --hold-ms N` under `unshare --pid --fork`
always terminates within a bounded, predictable time (the hold, plus
shutdown), so there is no separate timeout/kill loop in this tool
beyond a generous backstop against a genuine hang. Distinguishing
"exited on its own" from "still running when the hold ended" needed its
own real check, because the shutdown sequence prints the same
`shutdown TERM houses` line and the same `house exit <name> status=...`
line either way -- what differs, and is checked, is their ORDER: a
house that exits on its own account (whether cleanly or by crashing) is
reaped from the SIGCHLD handler DURING the hold loop, so its `house
exit` line prints BEFORE `shutdown TERM houses`; a house still running
is only reaped AFTER PID 1 sends TERM at shutdown, so its line prints
AFTER (or not at all). Verified against all three real shapes -- a
finished oneshot, a genuine crash, and a house still sleeping past the
hold -- before trusting the ordering as a signal, not merely reasoning
that it should work.

## 4. What counts as "reproduced"

**Same `reason` and same `value` as the evidence package that triggered
the investigation -- both fields, exact match, nothing fuzzier.**
`reason` is `exit` or `signal`; `value` is the exit code or signal
number. This is precise enough to automate and matches how every other
comparison in this tree already treats a death (`nwsup.c`'s own
`WIFEXITED(st) ? WEXITSTATUS(st) : WTERMSIG(st)`, the same pair
`write_evidence()` already records). The tail is reported to the
operator alongside the verdict, for their own reading, but is **not**
part of the reproduced/not-reproduced decision: a tail can differ
trivially between two genuinely-identical crashes (a timestamp, a pid,
an address printed in a panic message) without the crash itself being
different, and requiring an exact tail match would make "reproduced"
report false negatives for the exact same failure.

**Three outcomes, not two, and all three are distinguishable:**

- **Reproduced** -- a new evidence package exists for the throwaway
  unit, with the same `reason`/`value` as the original.
- **Did not reproduce** -- either the throwaway process exited cleanly
  (a `kind=oneshot` house's exit 0 is not a death at all -- D12 -- so no
  evidence package appears, which is the expected, successful shape,
  not a missing result) or died with a *different* `reason`/`value`.
- **Inconclusive (timed out)** -- the throwaway process is still running
  when the tool's observation window (a plain wall-clock bound, an
  ordinary tool default, not a TCB timing primitive -- this tool is
  Python, entirely outside `nwsup.c`'s no-clock constraint) elapses. The
  tool terminates the throwaway attempt and reports this distinctly from
  "did not reproduce," because a house that is still healthily running
  is a different fact from one that ran and exited cleanly.

## 5. COMMITMENT-5 check

**Operator-triggered only, and there is no code path that runs this
without an explicit, direct invocation.** It is a new command-line
tool, never a daemon, never a hook off `write_evidence()`, never wired
to fire automatically when a house dies or when its budget is spent.
Nothing in the TCB gains a subject here at all -- the check is trivial
in the strongest possible way, because **no TCB file changes**: `dawn.c`,
`pid1.c`, `nwspawn.c`, `nwcheck.c`, `nwsup.c` and `lids.c` are all
untouched by this feature. The only new code is the introspection tool
(question 2, reads and prints, decides nothing) and a Python
orchestrator that a person runs by hand and reads the verdict of. "Make
it happen again" stays a question the operator asks; nothing here turns
it into a policy the system runs on its own, which is exactly what
COMMITMENT-5 forbids and what an "auto-retry crashed houses" feature
would have been.

## 6. Interaction with the restart budget

**Does not count, and not merely as a policy choice checked at run
time -- structurally, by construction, because the throwaway attempt
and the real house share no state at all.** The relaunch is a wholly
separate `nw-sup` process, invoked with its own `NW_BUDGET` (forced to
`0` -- see the note below on why), its own `deaths` counter starting at
zero, its own pid, its own control socket path (under the throwaway
name). There is no shared variable, no shared file, no IPC between it
and the real unit's supervisor for this to leak through. This is the
same "prefer designing the problem out over checking for it" standard
`CLAUDE.md` asks for, and it is a stronger answer than the ctl
channel's own can be: the fact that `STOP` does not count today is a
property of the built code (`nwsup.c`'s `stop_requested` branch
`continue`s before `deaths++`, and `test_ctl_stop_does_not_count` pins
it) rather than of `docs/options/11`, which explicitly reserves that
question for the operator rather than deciding it -- that channel
*does* share the real supervisor process and has to track
requested-versus-unrequested internally to keep the two apart; here
there is no *real* process involved at all, so there is nothing to
track.

**`NW_BUDGET=0` for the throwaway attempt, regardless of the original
unit's declared budget, and this is a deliberate, stated deviation from
"identical inputs."** Budget is a property of the *supervisor's restart
policy*, not an input the *house* or its crash depends on -- nothing the
house execs, reads, or is confined by changes because the number
watching its restart count is different. Forcing it to zero makes the
throwaway attempt exactly one launch, one verdict, which is what
question 4's three outcomes assume and what makes the tool's timeout
handling simple and testable. A budget matching the original would only
buy a repeated crash loop nobody asked to watch.

## What this note is not proposing

No change to `write_evidence()`'s format, no new evidence-package field,
no daemon, no automatic retry policy, no generation token, no ordering
field. Everything this feature needs from the sealed plan and from #8's
evidence packages already exists; this is new tooling around existing,
unmodified TCB mechanisms, not a new TCB feature.

## What was built

`tools/unit-info.c` (the introspection tool, question 2),
`tools/relaunch-house.py` (the orchestrator: reads the evidence
package, resolves the unit via `unit-info`, prepares the throwaway
layer per question 1, bakes and boots the throwaway plan per question
3's corrected mechanism, and reports the question 4 verdict), and
`houses/firstfail.c` (a fixture that fails exactly once against a given
layer, for the control proving a transient failure does not falsely
report "reproduced"). Four tests in `tests/run.py`:
`test_relaunch_reproduces_a_real_crash`,
`test_relaunch_layer_copy_and_isolation` (question 1's copy-as-was
default AND `--fresh-layer`, both directions, plus the real layer's
content checked unchanged after each),
`test_relaunch_does_not_count_against_the_real_budget` (a real,
concurrently-running crash loop, unaffected), and
`test_relaunch_reports_inconclusive_when_still_running`.

## Review findings, and what changed because of them

**`tcb-review`, HIGH, reproduced: `bake_throwaway()` re-serialized a
validated blob's fields through an unescaped, space/`#`-delimited text
grammar, and a legal-but-unusual `exec_path` or `bind=` value could
silently override this tool's own `budget=0`/`kind=`/`lids=` fields.**
`nwcheck.c`'s `path_ok_len()` rejects control bytes, DEL and `..`
components -- not space or `#` -- and `plan.md`'s own standing
principle is that a blob need not come from this project's own baker
("a blob can arrive from anywhere"), so a blob that legitimately passes
`nw_check()` could still carry a field this tool's own city-file line
could not represent safely. `tcb-review` reproduced both failure
shapes: a silent override (the crafted bytes swallow `budget=0` and
following fields as part of the "path", then a `#` turns the rest into
a dropped comment) and a loud baker refusal, depending on exactly where
the injection landed relative to the fields this tool appends
afterward. Fixed at the boundary, not by escaping the join (the weaker
fix this project's own record argues against): `bake_throwaway()` now
refuses outright if `EXEC_PATH` or any bind path contains whitespace or
`#`, the same shape `layer=` is already restricted to `name_ok`'s
closed alphabet to prevent. None of the four tests exercised this path
before the fix; none needed to add one after, since the refusal is
unconditional and the fix itself was verified directly (the exact
crafted string `tcb-review` used is now refused with a named reason,
and an ordinary path is still accepted).

**`tcb-review`, LOW/HYPOTHESIS: `prepare_layer()`'s `shutil.copytree`
followed symlinks by default**, so a symlink a landlock-confined brick
house wrote into its own layer (`MAKE_SYM` is not among the withheld
Landlock rights) could pull an arbitrary target's content into the
throwaway copy rather than the symlink itself. Fixed with
`symlinks=True` -- also the more faithful "as it was at death" copy,
since the symlink is preserved rather than resolved once at copy time.

**`control` found the reason/value comparison in question 4's verdict
logic was never actually exercised: every path through the original
four tests reached a verdict via an earlier branch (no evidence at
all, or still running), never by comparing two evidence packages that
both exist and genuinely disagree.** A version that said "reproduced"
whenever anything died, regardless of whether it matched, passed all
four tests. Fixed by adding a case to `test_relaunch_reproduces_a_real_crash`:
a doctored copy of a real evidence package, with its `value` changed,
fed against a genuinely fresh (and therefore genuinely value=99)
relaunch of the same deterministic crash -- which must report
did-not-reproduce specifically because the comparison itself found a
mismatch, not because nothing was captured. Verified against the
mutation that exposed the gap: reverting the fix reproduces the
original failure (the doctored case now correctly reports
`did-not-reproduce`, and the mutation makes it wrongly report
`reproduced` again).

**`control` also found `test_relaunch_does_not_count_against_the_real_budget`
asserted only about the REAL house's own console output, never about
what the throwaway itself was actually given -- so removing the
`budget=0` override, or having the throwaway reuse the real unit's own
name, both left the test green.** The isolation the test's docstring
claims (question 6) held for an unrelated reason (separate processes,
separate captured output streams), not because either field was
checked. Fixed by asserting directly: `throwaway_name != name`, and the
throwaway's own evidence package reads `budget=0` regardless of the
real unit's declared budget. Verified against both mutations
`control` used: each now fails with a message naming exactly which
field leaked through.
