# 22 — lock/unlock

Status: **design note. No code in this round.** Answers
`docs/OPERATOR-BRIEF.md` Section 2 item 1b's ten questions from the
CURRENT code, with file:line citations, and states where the code does
not settle a question. The feature itself — a house that starts idle
and only runs when explicitly `START`'d, going back to idle on any
exit with no restart-budget consumption — is one of the "earlier
decisions" the brief already treats as final ("unset = locked; unlocked
houses start idle, any exit returns to idle, no budget use, a crash
writes a record and a clean exit 0 does not"). This note is the
implementation design underneath that decision, not a re-litigation of
it.

Every claim below was checked against the tree at the time this note
was written; grep the cited lines rather than trusting the prose if
this note and the code ever disagree.

## 1. Mount lifetime

**Nothing changes here, because unlocked and locked houses already
share the exact mechanism this question asks about.**

`lid_brick()` runs once per fork, inside the forked child
(`nwsup.c:1414`, inside `if (p == 0)`), never in `nw-sup`'s own
top-level process. `nw-sup` itself never unshares its own mount
namespace — the two `unshare()` sites (`lid_netns()` at `nwsup.c:68-73`,
`lid_newns()` at `nwsup.c:75-80`) are both called only inside the child
branch (`nwsup.c:1396-1397`). So every restart today already mounts
fresh and tears down at exit: the brick's erofs mount, the sized
layer's ext4 mount, the overlay, and every bind all live inside the
child's own private mount namespace and vanish when that namespace's
last reference goes, which is when the process exits. No `umount2()`
call exists anywhere in `nwsup.c` for any of them, and none is needed —
namespace teardown is the mechanism.

The one thing that could in principle outlive the namespace — the loop
devices `lid_brick()` attaches for the brick and, if declared, the
sized layer — do not, because `loop_attach()` unconditionally sets
`LO_FLAGS_AUTOCLEAR` (`nwsup.c:145`), and the comment beside it
(`nwsup.c:217-224`) records that this was verified both ways: dropping
the flag leaks the device (confirmed via `losetup -a` after the house
exits), keeping it frees the device automatically when the mount that
holds it goes, which is exactly when the namespace dies. **AUTOCLEAR's
trigger and the namespace's own teardown happen together**, so there is
no window where a loop device sits attached independent of the
namespace's lifetime, per this code and the measurement its own
comment records.

**Decision: mount per START, tear down at exit — and this needs no new
code, only the idle gate in §6.** An unlocked house between START calls
has no forked child, therefore no `lid_brick()` call, therefore no
mounts and no loop devices at all — the mount lifetime IS the child
process's lifetime already, for every house, locked or not. START
causes exactly the fork the STOP→START cycle already causes today
(`nwsup.c:1346`, reached via the `stopped` branch's `continue` at
`nwsup.c:1336`). Making a house start in that same idle state instead
of forking immediately at boot (§6) is the only change this design
needs; the mount mechanics underneath it are unmodified.

**Measuring the 50-cycle leak check.** Bake a single unlocked house,
`START` it, wait for it to exit on its own (or `STOP` it if it does
not), repeat 50 times, and compare four numbers before the first cycle
and after the last:

- **Loop devices**: `losetup -a | wc -l` — must return to baseline.
  This is the number AUTOCLEAR's own claim is about; a regression here
  means the flag stopped taking effect, or something opens a loop
  device outside `lid_brick()`.
- **File descriptors held by `nw-sup` itself**: `ls /proc/<nw-sup pid>/fd
  | wc -l` — must return to baseline. This is a property of the idle
  loop and the restart loop, not of `lid_brick()`, and it is the one
  this note's own idle-state change (§6) could plausibly regress if the
  idle branch's `poll()` setup or the `accept()`/`close()` pairing in
  `handle_ctl_live()` leaked a connection fd across cycles.
- **Zombies**: `ps -o stat= -p <every pid nw-sup forked>` (or a
  `waitpid(-1, ..., WNOHANG)` sweep from outside) — none should remain
  reapable; every generation's `wait_house()` call must have collected
  its exit status (`nwsup.c:1437`).
- **Mounts visible from the machine root**: `findmnt` (or
  `/proc/1/mountinfo`) should show no NEW entries — everything
  `lid_brick()` mounts lives in a namespace nothing outside the dying
  child can see, so a leak here would mean a mount escaped its
  namespace, which would be a much larger defect than this feature.

What is *expected* to persist and is not a leak: the layer directory
under `NW_LAYER_DIR` (durable by design) and its content — 50 cycles of
the same house writing to the same declared layer is exactly what
"survives a restart" (`test_layer_survives_a_restart`) already commits
to.

## 2. Output tail across runs

**Not solved by any existing code today, and it needs a run-boundary
marker in the log stream itself, because the ring buffer is process-
lifetime, not run-lifetime.**

`spawn_logger()` runs once per unit at PID 1's boot-time setup
(`pid1.c:267-451`, called once per house at `pid1.c:770-776`), and its
in-memory ring (`pid1.c:364-366`) and the pipe it reads from
(`pid1.c:772`, created once, before any house has ever forked) are both
scoped to the logger's whole life — one boot, every restart of that
unit. The tail *file* is truncated exactly once, at logger startup
(`pid1.c:382-383`), to keep one boot's tail from inheriting a *previous
boot's* stale content — it says nothing about separating restarts
*within* a boot, and nothing else in the file resets it. `write_evidence()`
(`nwsup.c:994-1100`) reads whatever is currently in the tail file at
the moment of a death with no run-boundary logic at all
(`nwsup.c:1067`).

Consequence, stated because it is the actual bug this question exists
to prevent: an unlocked house that is `START`'d, exits cleanly, is
`START`'d again, and crashes on its second run would today capture a
tail that can still contain the FIRST run's output ahead of the
crash's own — because nothing marks where the second run's bytes begin.

**Decision: `nw-sup` writes a distinctive, greppable marker line into
the same log stream immediately before every fork, and `write_evidence()`
trims the tail it captures to start after the LAST such marker.** This
needs no new channel and no cross-process coordination: `nw-sup`
already writes lines into this same pipe via `say()` (that is how
`restart`/`spent` lines reach the log today), so the marker is just
another `say()`-shaped line, e.g. `run gen=<n>` where `<n>` is the
in-process restart counter `nw-sup` already tracks. `write_evidence()`
already has the whole tail buffer in memory when it composes a
package; searching it backward for the last occurrence of the marker
and starting the captured region there is a string search over bytes
already in hand, not a new mechanism. The marker is written for every
fork, locked or unlocked, so this closes the same gap for a
LOCKED house's crash-after-N-restarts case too — the ring's lack of a
run boundary was never specific to lock/unlock, it just did not matter
until a house could restart quickly and repeatedly on an operator's
own command rather than only on a crash-driven budget countdown.

**What this does not do:** it does not shrink or reset the on-disk
tail file itself per run — the file stays one rolling window per this
note's own reading of `pid1.c:372-381`'s existing reasoning, and
changing that is out of scope here. The marker only changes what a
LATER read of that window is willing to treat as "this run's" content.

## 3. Edges and unlocked houses

**Refuse edges on unlocked houses in this version, at bake time and at
boot, per the brief's own recommendation — the code confirms why.**

An edge's socketpair end is never held by `nw-sup`: `nw-spawn` creates
every declared edge up front (`nwspawn.c:299-302`), hands each unit its
own ends via `dup2()` into fixed descriptor slots (`nwspawn.c:236-241`,
`pack_kit()`), and `nw-spawn` itself closes its own copies and exits
normally once every unit has forked (`nwspawn.c:440-443`, verified via
`fcntl(fd, F_GETFD)` at `nwspawn.c:488-503`) — there is no live broker
holding a third reference, by design (`docs/options/17-edges.md`). The
wire descriptors are non-`CLOEXEC` (`clear_cloexec()`,
`nwspawn.c:240`), so they survive `nw-sup`'s own `exec()` into the
`nw-sup` binary and then survive `nw-sup`'s subsequent `fork()` for the
house child the same way any inherited fd would, since nothing in
`nwsup.c` ever touches or closes an `NW_WIRE_*`-named descriptor
(confirmed: no edge/wire handling anywhere in `nwsup.c`, matching
`docs/options/17-edges.md`'s own "wiring is entirely a nw-spawn-side
concern").

That means `nw-sup`'s own inherited copy of a wire fd is not something
the *house* process controls, and it is not closed between the house's
own runs — the house process itself is what goes away and comes back;
`nw-sup`, the process actually holding the descriptor, does not restart
at all between START/STOP cycles. So an idle unlocked house's peer, if
it wrote to its end of a still-open wire, would have that write succeed
against `nw-sup`'s dangling copy of the descriptor and sit unread until
the house is `START`'d again and inherits it fresh — a "stale stream"
exactly as the brief's own framing of the question describes, and
exactly the failure mode refusing this combination at bake time avoids
having to define semantics for.

**Refusal text and where it is checked**, matching the existing
cross-field-rule pattern (`.claude/rules/plan.md`'s "Hard rules" —
every rule the runtime relies on lives in `nwcheck.c` too, not only the
baker): `unlocked house <name> cannot declare an edge — an idle
house's wire end would sit open across every idle period, and a peer
writing to it would go unread until the next START`. Refused by the
baker at parse time (the same place `lids=none` without `--lab` is
refused, per `bakery/nw-cc.py`'s existing per-field-check shape) and
independently by `nwcheck.c` with a new `NW_E_*` code, since a blob can
arrive from anywhere and nothing upstream stands behind it.

## 4. Contradictions to refuse at bake and again at boot

Two, found by checking every field lock/unlock's semantics touch
against every other declared field; a third was looked for and not
found — see the note at the end of this section rather than padding
the list to look more complete than it is.

1. **Unlocked plus a nonzero restart budget** (the brief's own given
   example, restated precisely). An unlocked house's exit never counts
   against budget (§6 traces exactly where that exemption sits in
   `nw-sup`'s control flow), so a declared nonzero `budget=` on an
   unlocked house is a number with no effect — refusing it, rather than
   silently ignoring it, keeps a plan saying only what it does.
   Refusal: `unlocked house <name>: budget= has no effect on an
   unlocked house and must be 0 or omitted`.
2. **Unlocked plus an edge** — §3, same shape.

**A third was checked for and not found to rise to this level.**
`kind=longrun`'s existing "any exit is unexpected" framing and
unlocked's "any exit returns to idle" are compatible, not
contradictory: unlocked's rule is a strict generalization of
`kind=oneshot`'s existing clean-exit exemption (every exit, not only a
clean one, skips the budget increment), so the two `kind` values
interact with `lock` the same way and neither needs a refusal.
Supervisor-death policy (`keep|die`, still undesigned — Phase 4)
interacts with `lock` only insofar as an unlocked unit's `nw-sup` never
reaches the budget-exhaustion exit at all (there is no "spent" for a
unit that never spends), so `keep|die`'s choice is moot for it rather
than contradictory with it; that is worth stating in whichever note
designs supervisor-death policy, not a bake-time refusal here.

## 5. Encoding

**A single byte, in the position `sched_ext` frees, landing in the
same bump that deletes it — size-neutral, so it costs nothing beyond
what the bump already spends.**

`sched_ext` is `struct nw_unit`'s only single-byte field with no
declared purpose predating it — it was itself the reused former spare
byte (`blob.h:450-464`'s own history: "WAS `_pad`... until
`docs/options/15` gave the reserved spare its first meaning"), pinned
at offset 227 (`blob.h:624`). `NW_UNIT_SIZE`'s literal `4` term
(`blob.h:614`) is exactly `kind`+`budget`+`lids`+`sched_ext`, with no
slack — there is currently no free byte for `lock` to occupy without
either growing the struct (a second magic bump, which the operator's
"ONE plan-format bump" decision rules out) or reusing one already
spoken for.

Operator decision 6 already deletes `sched_ext` in the same bump this
lands in ("io limits and the sched_ext field are DELETED in the one
plan-format bump"). Deleting one single-byte field and adding one
single-byte field in the same change leaves `NW_UNIT_SIZE`'s `4`-field
count unchanged — `lock` fits inside the single bump exactly because
its cost is paid by `sched_ext`'s removal, not by new struct growth.
The exact byte offset is a mechanical detail for whoever writes the
bump (following `blob.h`'s own `NW_AT`/`NW_TYPE` pinning convention for
every other field), not a design decision this note needs to make.

**Locked must be `0`.** Not for cross-magic backward compatibility —
the magic bump itself already invalidates every blob from before it,
so a byte's old meaning under the old magic is moot — but so that,
*within* the new format, a city file that does not declare `lock=` at
all bakes to locked, matching every existing plan's actual behavior
today. The alternative (requiring `lock=` explicitly, the way `lids=`
now does) would force every single existing city file to be rewritten
at the exact moment of the bump merely to keep its present behavior,
which is a much larger compatibility cost than the one `lids=` accepted
for a materially different reason (there, the byte's two meanings —
forgotten and deliberate — were genuinely indistinguishable and the
distinction was worth forcing; here, "not declared" and "locked" are
the same thing on purpose, so there is nothing to force a reader to
disambiguate).

## 6. The idle state

**Reuses the STOP'd idle branch verbatim; the only change is what
`stopped` is initialized to.**

Today, every house forks immediately at boot with no idle path at all:
`stopped` starts at `0` (`nwsup.c:1308`) and the loop falls straight
through to `fork()` (`nwsup.c:1346`) on its very first iteration. The
ONLY way to reach the idle (`stopped`) branch today is via a prior
`STOP` (`nwsup.c:1448-1451`). That branch already implements exactly
what an unlocked house's idle state needs: it blocks in `poll()` on
just the listening control socket (`nwsup.c:1319-1323`), replies `OK\n`
and falls through to a fresh fork on `START` (`nwsup.c:1332-1336`),
replies `OK\n` with no state change on `STOP` (idempotent,
`nwsup.c:1337-1338`), and refuses anything else.

**Decision:** initialize `stopped` from the plan's `lock` field instead
of unconditionally to `0` — `int stopped = !locked;` in effect. A
locked house's behavior is byte-for-byte unchanged (locked → `stopped =
0` → immediate fork, exactly today's code path). An unlocked house
starts in the idle branch directly, with no fork, no mount, and no
budget consumption until the first `START` arrives — the same
mechanism a STOP'd locked house already uses, not a new one.

**Cost per idle house, against today's always-running default:** one
`nw-sup` process blocked in `poll()` on its own listening socket — the
same footprint `nw-sup` already has while `stopped` after an explicit
`STOP`. No child process, no mounts, no loop devices (§1 — those only
exist inside a forked child), no descriptors beyond what `nw-sup`
itself already holds (its ctl socket, its own log-pipe fd inherited
from `nw-spawn`, and whatever else survives its own `exec()`). This is
strictly cheaper than an always-launched house, which is the entire
point of the feature.

**`STATUS`, a new verb on the same channel**, small and fixed:
`state=<idle|running> deaths=<n> last_exit=<none|code|crash>\n` — one
line, three fields, matching the existing line-based `OK\n`/`ERR
...\n` protocol (`docs/options/11`). `deaths` and `last_exit` are
already tracked in `nw-sup`'s own locals (`deaths` at `nwsup.c:1467`;
the exit status is already decoded at every `wait_house()` return) —
`STATUS` reads them, it does not compute anything new.

- **`STOP` on idle**: idempotent no-op, `OK\n`, matching `STOP` on an
  already-stopped locked house today — same code path, same reply.
- **`TERM` from PID 1 on idle (shutdown)**: needs no new code. The
  idle branch's own `poll()` call is the same one a STOP'd locked
  house already blocks in during shutdown, and the loop's top-of-
  iteration `stopping` check (`nwsup.c:1313`) already runs before
  falling into that branch on every re-entry. Whether `poll()` itself
  returns promptly on the incoming `SIGTERM` (EINTR, since `signal()`
  installs `on_term` at `nwsup.c:1244` with no explicit `SA_RESTART`)
  is worth a named test rather than an assumption (§9), but if it
  does, this is not new engineering, it is the existing shutdown-
  while-stopped path with a different entry reason.
- **`START` on an already-running house**: unchanged from today
  (`handle_ctl_live()`, `nwsup.c:806-807`) — idempotent `OK\n`, no
  second fork, for locked and unlocked alike once the house is
  actually running. What a launcher user sees: the same `OK\n` whether
  their game was already running or had to be launched just now — the
  reply does not distinguish the two, matching START's existing
  behavior for a locked house today.

## 7. Interplay

**`tools/relaunch-house.py` should force `locked` in its own throwaway
plan, ignoring the live unit's declared `lock`.** Relaunch exists to
answer "does this crash reproduce," and it bakes a single-house
throwaway city plan reusing the live unit's other fields (its own
docstring already frames the plan as "a re-serialization of a
validated blob's fields," item 1e's `--lab`-unconditional treatment of
it earlier this phase rests on the same framing). An idle-until-
`START` house would just sit idle under that throwaway plan unless the
tool ALSO issued a `START`, adding a step that buys nothing — the tool
wants the house running immediately so it can observe the
reproduction, which is exactly what forcing `locked=0` (or
equivalently `1`, whichever byte value means locked) in the throwaway
plan gives it, regardless of what the live unit declares.

**Profiles' RUN/STOP for an unlocked house: undecided, because
profiles do not exist.** Neither `nwctl` nor any "profile" struct,
plan field, or RUN/STOP verb pair distinct from the already-built
START/STOP channel exists anywhere in this tree — every mention is in
`docs/OPERATOR-BRIEF.md`/`docs/QUEUE.md` describing Phase 4 future
work, with no design note written for it yet. The natural mapping for
whoever writes that note — RUN≈`START`, STOP≈`STOP` against the
existing channel, which already works unmodified for locked and
unlocked units alike (§6) — is stated here so it is not lost, but it is
not binding on that future note.

## 8. The launch path

**This question has no subject yet, and answering it as though it did
would be inventing facts.** Per a full-repo search: `nwctl`, `driftwm`
and the compositor exist nowhere as code — no binary, no stub, no
source file. `docs/options/07-identifiers-not-paths.md:103-104` says so
of the compositor directly: "The compositor is not yet in the tree;
this row is a sketch, not a requirement." Every other mention is
`docs/OPERATOR-BRIEF.md`/`docs/QUEUE.md` listing `nwctl` as unbuilt
Phase 4 work.

What CAN be answered from the brief's own parenthetical, which is
already a statement about the existing mechanism rather than a new
one: the grant a launched game house needs to reach the compositor is
an ordinary declared `bind=` of driftwm's own listening socket path —
the same generic bind mechanism every other house already uses
(`nwsup.c:335-375`), not a new primitive. Everything else Q8 asks —
how the bakery includes a static `nwctl` in the compositor's brick,
where it lands on `PATH`, what else a game house needs for GPU and
input beyond the socket bind — has no code to check it against yet.
Flagging this as the gap it is: those questions belong to whichever
future work actually builds `nwctl` and the compositor brick, with
this note's one settled fact (the bind mechanism) as their starting
point, not answered speculatively here.

## 9. Tests, red then green

For each new mechanism above, a test pinning it plus the negative
control that shows it fail without the mechanism (`CLAUDE.md`'s
"definition of done" pattern applied here as everywhere else in this
tree):

- **Encoding**: a plan omitting `lock=` bakes to `locked` (byte 0);
  `lock=unlocked` bakes to the other value; `NW_MAGIC` still matches
  its declaration count (`magic-moves-with-layout`'s existing shape).
- **The two contradictions** (§4): each refused at bake time by name,
  and independently by `nwcheck.c` on a hand-crafted blob that clears
  the check the baker would have caught (the `test_brick_needs_newns`
  pattern).
- **The idle state** (§6): a fresh boot with an unlocked house shows
  no forked child and no mounts; `START` launches it; a second `START`
  while running is a no-op (one child pid throughout); the house
  exiting (clean or crashed) returns to idle with `deaths` unchanged
  either way; `STOP` while idle is idempotent; `STATUS` reports the
  right state at each point. Shutdown-while-idle needs its own case,
  named separately from shutdown-while-running, specifically to settle
  whether `poll()` actually wakes on the incoming TERM the way §6
  assumes rather than trusting the assumption.
- **The run-boundary marker** (§2): two runs of the same unlocked
  house, the first writing distinctive output and exiting cleanly, the
  second crashing — the crash's evidence package must contain only the
  second run's output, not the first's. Negative control: remove the
  marker-trim logic and confirm the package now (wrongly) contains
  both runs' output concatenated.
- **The 50-cycle leak check** (§1): the four measurements listed there,
  before and after 50 START/exit cycles of one unlocked house, each
  returned to baseline (loop devices, `nw-sup`'s own fd count,
  zombies, machine-visible mounts) with an explicit assertion per
  metric, not a single pass/fail verdict — so a regression names which
  resource leaked rather than only that something did.
- **A locked-house regression test**: every existing lock/unlock-
  unaware test (the whole current suite) must still pass unmodified
  against a baker that now understands `lock=` — run the full suite
  against a plan format that includes the field but never sets it away
  from `locked`, confirming the default truly reproduces today's
  behavior byte-for-byte rather than merely reading as though it
  should.

## 10. Where the shell gets its inputs

**Answer only, as asked: keep the environment-string pattern. Do not
switch to a pointer plus unit index into the blob.**

Every field `nw-spawn` currently hands `nw-sup` — `lids`, `kind`,
`budget`, `sched_ext`, `brick` (hex-encoded), `layer`, `layer_bytes`,
each bind — goes through `setenv()` of a decimal (or hex, or plain)
string, read back with `atoi`/`strtoull`/direct string compare
(`nwspawn.c:353-408`, `nwsup.c:1107-1230`). Every one of those read-
back sites carries the same explicit rationale, repeated rather than
factored out: `nw-sup` reads its unit from the environment, not the
sealed blob, so nothing the baker or `nw-check` did stands behind this
value, and it is re-validated as though it arrived from anywhere.

A pointer into the blob would not remove that obligation — `nw-sup`
would still have to treat the pointed-at bytes as unverified and
re-check them, which is the same work the environment-string pattern
already does, while adding a new one: `nwspawn.c` and `nwsup.c` would
now have to agree on `struct nw_unit`'s exact layout directly, which is
exactly the kind of drift invariant 3 exists to police elsewhere
(`blob.h`, the baker, and both specs already have to agree on the
format; adding `nw-sup` as a fifth direct reader of the struct layout,
rather than a reader of re-validated strings, widens that surface for
no gain). `lock` should follow the existing pattern: `setenv("NW_LOCK",
...)` in `nwspawn.c`, read and re-validated in `nw-sup`'s `main()`
alongside every other field, for the same reason each of them already
does.
