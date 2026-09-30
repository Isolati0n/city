# 32 — `grace_period`: the stop-grace escalation applier

Status: design note, not yet reviewed. Base: `07ee9a3` on `origin/main`.
Scope: the brief's "stop-grace escalation" item
(`docs/OPERATOR-BRIEF.md` Section 4, `docs/QUEUE.md`'s Phase 4
follow-up list), which `docs/options/31-phase4-plan-bump.md` Section 5
deliberately refused to build inside the plan-format bump, reserving
only the byte. This note builds the mechanism that byte was reserved
for.

**Ownership note, recorded here because it decided the shape of this
note.** The mechanism lives in `nwsup.c`'s restart/wait loop
(`wait_house()`, `handle_ctl_live()`), which `CLAUDE.md`'s "Who owns
what, this week" now describes as operator-delegated per active phase
rather than a fixed Grok assignment — corrected this round, on the
operator's direct instruction, after three consecutive phases (Phase 2's
`decide()` rewrite, Phase 3's cgroup placement, Phase 4's
capabilities/`nofile`/`oom_score_adj` appliers) had already been built
there without objection. `pid1.c` and `dawn.c` remain untouched Grok
territory — that boundary was explicitly preserved, not loosened, and
it is the reason Section 6 below stops where it does.

## 1. What "today's behavior" is, measured rather than assumed

`grace_period=0` (unset) must mean exactly what happens today, per the
amendment's own framing ("unset = today's behavior"). Read directly
rather than assumed:

`wait_house()`'s ordinary loop blocks in `poll()` with timeout `-1` —
forever — once a stop signal has been sent, whether that signal came
from `on_term()`'s async handler (city-wide shutdown, `stopping` set)
or from `handle_ctl_live()`'s `STOP` branch (`stop_requested` set,
gated through `nw_decide()`'s `NW_DECIDE_TERM_CHILD`).
Neither path bounds the wait. `runtime.md`'s Liveness section states
this as deliberate: *"There is no field to put one in... every form of
detection needs a guessed constant, and the rule has been attempted and
wrong every time."* A house that ignores its stop signal is never
force-killed by anything in this tree today — `nw-sup` waits in
`waitpid`/`poll` indefinitely, and the only things that end that wait
are the house exiting on its own, or `pid1.c`'s `shutdown_city()`
SIGKILLing the *supervisor* directly after the city-wide `NW_GRACE_MS`
(400ms, `pid1.c:145`) during a full shutdown — which ends the wait by
force but does not give the house's own process any grace at all; it is
the supervisor that dies, and the house dies with it (pid-namespace
teardown, per `.claude/rules/runtime.md`'s Orphans-at-shutdown section).

**Why this is not the refused case, stated the way `runtime.md`'s
Liveness section requires ("propose the constant and say who chooses it
and what happens when it is wrong") rather than merely asserted.** The
refused class is detecting an *unrequested* silence in an otherwise
healthy house — there is no natural boundary between "slow" and "hung,"
and a wrong guess turns a performance problem into a restart loop. This
mechanism only ever arms after a stop signal has *already been sent*,
which is either an explicit operator action (`STOP` over the control
socket) or a city-wide shutdown already in progress — in both cases the
house's fate is already decided (it is going away), and the only
question this field answers is how long to wait for it to go away
gracefully before making that happen by force. The constant is chosen by
the plan author, per house, not guessed by this code. What happens when
it is wrong: too short, and a house that needed longer to flush state is
killed mid-cleanup — the same tradeoff every real init/supervisor makes
with a stop timeout (systemd's `TimeoutStopSec`, Docker's
`--stop-timeout`), an accepted operator choice, not a hidden defect. Too
long, shutdown takes longer than necessary but stays bounded — unlike
today's unconditional wait, which is unbounded whenever
`grace_period=0`.

## 2. Mechanism — no clock, because none is available and none is needed

**`nwsup.c` may not gain a timing primitive, and this is a live,
running test, not a style preference.** `test_budget_is_hard_total`
(`tests/run.py:3505-3513`) greps the whole file for
`clock_gettime|now_ms|alarm|nanosleep|clock_nanosleep|usleep|
setitimer|timerfd_create|timer_create|gettimeofday|times|clock|
sysinfo|time` and fails if any appears anywhere in the file, not only
near the budget. `decide.c` gets the identical sweep. Any grace-period
mechanism that reads a clock to compute "how long has it been since I
sent the signal" is refused by this test before it is refused by
review.

**`poll(2)`'s own kernel-side timeout is not on that list, and the tree
already relies on it** — the pidfd-open-failure fallback tier
(`poll(&pf, 1, 50)`) already waits up to a fixed
duration without `nwsup.c` itself reading a clock; the kernel does the
timing, `nwsup.c` only supplies an integer. `grace_period` is the same
shape: a plan-declared millisecond count handed straight to `poll()`'s
timeout parameter. `nwsup.c` never computes elapsed time; it asks the
kernel to wait up to a bound and tell it which happened first.

**The escalation wait is a single, isolated `poll()` call on the pidfd
(or its signalfd fallback) alone — the control socket is deliberately
NOT in that one call's fd set.** This is the answer to a real
correctness question the mechanism has to get right: `wait_house()`'s
ordinary loop polls the pidfd/signalfd *and* the control socket
together, and if the escalation timeout were simply substituted for
that loop's `-1` on every iteration, any control-socket activity during
the grace window (another `STOP`, a `START`, anything) would cause
`poll()` to return early for a reason unrelated to the timeout, and the
loop would recompute a *fresh* `grace_period`-length timeout on its next
iteration — silently extending the true wait past the plan's own
declared bound on every such wakeup, an unbounded-in-practice mechanism
wearing a bounded one's plan syntax. **Excluding the control socket for
this one bounded wait avoids that failure by construction rather than
by getting a decrement-and-track-remaining-time scheme right without a
clock to do it with** (there is no clock to compute "time remaining"
against in the first place). A control-socket connection attempted
during the grace window queues in the kernel's own accept backlog
(`listen(lfd, 4)` — four deep already, unrelated to
this note) and is serviced on the very next ordinary wait cycle, if
there is one; `STOP` is already idempotent while a stop is pending
(`handle_ctl_live`'s `!stop_requested` guard), so a
retried `STOP` queued this way changes nothing when it is eventually
serviced.

**A second correctness question this design missed, found by review
rather than worked through here, deserves the same treatment as the
control-socket exclusion above.** The control socket is not the only
thing that can interrupt the escalation `poll()`: `nw-sup` installs its
own handler for `SIGTERM`/`SIGINT` (`on_term()`, pre-existing), and a
plain `poll()` returns `EINTR` on *any* delivery of a signal being
caught, independent of `SA_RESTART`. A second `SIGTERM`/`SIGINT` sent
to the supervisor's own pid while the escalation wait is already in
flight — an operator re-sending `kill -TERM`, a monitoring tool,
anything with access to the pid namespace, which invariant 5 already
grants uid 0 — hits the same `if (errno == EINTR) continue` this whole
mechanism relies on elsewhere, and re-enters the branch to issue a
*fresh, full-length* wait. Repeated indefinitely, this extends the
"bounded" escalation past its declared window without limit, from
outside the plan entirely — the exact failure the control-socket
exclusion above was designed to prevent, arriving through a different
channel it did not consider. `tcb-review` and `fd-auditor` found this
independently against the implementation.

**Fixed with `ppoll(2)` rather than `poll(2)` for this one call,
blocking `SIGTERM`/`SIGINT` for its duration.** By the time this branch
runs, `stopping`/`stop_requested` are already known true, so a repeat
delivery of either signal carries no new information this wait needs
to re-observe — the same reasoning the control-socket exclusion already
uses, applied to a second channel. `ppoll` swaps the blocking mask in
atomically with entering the wait, so neither signal can generate an
`EINTR` here at all; it restores the prior (empty) mask on return, so a
signal that arrived during the wait is simply delivered — `on_term()`
runs, resending the stop signal to an already-stopping child — the
instant the call exits, never before. Still no clock: the bound handed
to the kernel is still a plain integer, unread and untouched by
`nwsup.c` itself.

**The fallback tier's cycle-counting approximation had the mirror-image
defect for the identical reason** — `armed_cycles` was incremented once
per pass through its `for(;;)` loop regardless of whether the
preceding fixed-width `poll(&pf, 1, 50)` actually ran its full 50ms or
returned early via `EINTR`, so the same signal flood that stalls the
primary tier forever instead **inflates this tier's cycle count faster
than real time**, escalating a house well before its declared
`grace_period` has actually elapsed. Fixed by only counting a cycle
when the preceding `poll()` returns `0` (a genuine, uninterrupted
timeout) rather than unconditionally at the top of the loop — the
tier's own read-not-run bound stays an approximation of the primary
tier's exact one, but errs long under interruption rather than firing
early.

**The sigmask-replacement defect, found in the SAME `ppoll(2)` fix and
by a second review round on it: the sigmask argument REPLACES the
calling process's blocked set for the call's duration, it does not
augment it.** The signalfd-fallback tier (engaged only when `pidfd_open`
itself fails) permanently blocks `SIGCHLD` earlier in `wait_house()`
specifically so its `signalfd` can see it. The escalation branch's
first version built its `ppoll` mask from empty plus
`SIGTERM`/`SIGINT`, which — in this tier only — unblocked `SIGCHLD` for
the entire duration of the call. `SIGCHLD` has no handler installed
(default disposition: Ignore), so a child dying during that exact
window was delivered under that default action and silently discarded
rather than remaining pending for `signalfd` to report — deterministic,
not a race, because the mask replacement covers the whole blocking
duration, not an instant. The escalation still eventually recovered the
correct exit status (a plain blocking `waitpid` follows regardless of
how this call returns), but only after running the FULL declared
`grace_period`, even for a house that stopped instantly on its own.
None of the tests added for the first two fixes could catch this: all
of them run on the machine's normal path, where `pidfd_open` succeeds
and this tier never engages. Fixed by reading the CURRENT blocked set
first (`sigprocmask(SIG_BLOCK, NULL, &block_own_signals)`) and adding
`SIGTERM`/`SIGINT` to it, rather than starting from empty — the pidfd
tier's ambient mask is empty, so this is exactly the prior behavior
there; the signalfd tier's ambient mask already has `SIGCHLD` in it,
and that now stays blocked throughout the call too. Pinned by
`test_grace_period_signalfd_tier_notices_a_prompt_death`
(`tests/run.py`), shown failing against the pre-fix, empty-mask
version.

**The atoi-overflow defect, unrelated, surfaced by the same review
round:** `nw_grace_period`'s own re-validation (`nw-sup` reads its unit from the
environment, not the sealed blob, so nothing upstream stands behind
this value) parsed `NW_GRACE_PERIOD` with `atoi()`, whose `(int)` cast
truncates a value that overflows `int` — and `strtol`, which `atoi` is
built on, does not itself overflow a 64-bit `long` until well past
`UINT32_MAX`, so a value like `2**32` or `2**32 + 1500` sailed past the
`> NW_GRACE_MAX_MS` check entirely, truncated on the cast to `0` or
`1500` respectively — silently accepted as a *legal, in-bound* value
from a string that plainly names an out-of-range one. Fixed with
`strtoul` and its own `errno == ERANGE` and end-pointer checks, closing
the class rather than only the one boundary (`GRACE_MAX_MS + 1`) the
original test happened to cover.

**The ctl-socket-flood defect, in the third (both-primitives-failed)
tier, found by the same review round that found the sigmask-replacement
defect above — and the same shape one level down.** That tier has no
pidfd/signalfd to fall back to at all, so its only OTHER readiness
source is the unit's own control socket. The signal-flood fix (only
counting a cycle on a genuine `poll() == 0`) closed the route through
repeated signals, but left this tier still polling the ctl socket
alongside the timeout while armed — so any ctl traffic at all (an
ordinary `START`, not even a malicious repeated `STOP`) keeps that
`poll()` returning `>0` instead of the `0` the cycle count depends on,
and `armed_cycles` never advances: the escalation can be extended
indefinitely by ordinary control traffic, the identical "outside the
plan" failure the whole feature exists to close, through a third
channel. Fixed by excluding the ctl socket from the poll entirely once
armed — `poll(NULL, 0, 50)` has no fd to become ready at all, so a
non-`EINTR` return is unconditionally a genuine, elapsed ~50ms cycle
regardless of ctl activity, the same exclusion principle the other two
tiers already apply to the control socket, extended to the one tier
that has nothing else to poll instead. A connection attempted while
armed queues in the kernel's own accept backlog, same as the other
tiers. Pinned by `test_grace_period_tier3_survives_a_control_socket_flood`,
shown failing against the pre-fix version.

**Two documentation-only findings from the same round, named because
CLAUDE.md's own rule is that a stated derivation must stay checkable:
`NW_GRACE_MAX_MS`'s comment in `blob.h` claimed the value was chosen
because `nw-sup` "hands it straight to `poll(2)`'s `int` timeout
parameter with no conversion in between" — which the primary tier's
own `ppoll`/`struct timespec` conversion (division and modulo) had
already made false in this same diff, and which `blob.h`'s **second**
copy of the identical claim, in the `NW_E_GRACERANGE` enum comment
written fresh in this same diff, still carried after the first copy
was fixed — a third review round found the leftover.** The
`NW_E_GRACERANGE` comment now points at the macro's own comment rather
than restating it, closing the exact class of drift a restatement
creates. The macro's own comment states both real reasons the bound is
`INT_MAX`: keeping the `struct timespec` conversion inside a sane
range, and keeping the double-failure tier's `armed_cycles * 50`
accumulator from overflowing `unsigned` before its comparison can fire
— **both generous, not tight**, which the same third round also
corrected: an earlier version of this same correction called the
second reason "tight," and it is not — the loop returns as soon as the
comparison holds, so the multiplication never exceeds
`nw_grace_period + 49`, nowhere near the overflow point. No code
changed for either finding; both corrections are textual, in `blob.h`
itself.

**A third finding from the same round was not documentation-only: the
tier-3 (double-failure) fallback's own ctl-flood fix, above, was itself
incomplete — it closed the fd-readiness route and left the SIGNAL
route open, the identical HIGH bug the primary tier's `ppoll` fix
closed, relocated to the one tier that had never been given the same
treatment.** `poll(NULL, 0, 50)` has no fd to become ready, but it is
still a plain `poll()`, and `SIGTERM`/`SIGINT` are caught (`on_term()`)
and unblocked in this tier — so a flood of either against the
supervisor's own pid, spaced faster than 50ms, interrupts every single
cycle with `EINTR` before it can return `0`, and `armed_cycles` never
advances. Reproduced directly by `tcb-review`: a house with a declared
900ms `grace_period` survived 3.92s under a SIGTERM flood and died
within ~1s of the flood stopping. Fixed identically to the earlier two
tiers: `ppoll(NULL, 0, &cycle_ts, &block_own_signals3)`, reading the
current mask first and adding `SIGTERM`/`SIGINT` to it, rather than a
plain `poll()`.

**Sequence, once a stop signal has been sent for the current child**
(`stopping` or `stop_requested` becomes true — both already tracked,
no new state needed to detect the transition) **and `grace_period !=
0`:**

1. Let the ordinary poll loop keep servicing the control socket exactly
   as it does today until the CURRENT iteration observes the signal has
   been sent (this may be the same iteration that sent it, via
   `handle_ctl_live`'s `STOP` branch, or a later one, if `on_term()`'s
   async handler fired between iterations).
2. From that point, issue one `poll()` on pidfd/signalfd alone, timeout
   `grace_period`.
3. If it returns because the pidfd/signalfd fired: the child exited
   within its grace period. Proceed exactly as the existing return path
   does today — no escalation, nothing else changes.
4. If it returns 0 (timeout, no fd ready): the child ignored its stop
   signal for the full declared window. `kill(p, SIGKILL)` on the
   child's own pid, then a plain blocking `waitpid(p, &st, 0)` — now
   fast, since the process is dying. **Nothing else is added here**:
   the caller's existing, unconditional `cg_kill_sweep(cgroup_path)`
   call, which already runs after every death regardless of cause
   (a defensive sweep for whatever the dying process forked and did
   not reap), cleans up any grandchildren the escalated house itself
   spawned, exactly as it already does for a natural death. This
   mechanism does not touch `cg_kill_sweep()` or extend what it is
   called for; escalation ends with the same `waitpid` return this
   function already produces on every path, and everything downstream
   of that return is unmodified.

**What this costs, stated rather than left implicit.** The true bound
on total wait is "grace_period ms since the LAST control-socket message
serviced before entering step 2," not "grace_period ms since the stop
signal was sent," if step 1 takes nonzero time servicing unrelated
traffic first. That gap is bounded by how long `handle_ctl_live()`
itself takes (a single `accept`/`read`/`write`/`close`, no I/O beyond
the socket), which is not something a flood of legitimate operator
traffic could meaningfully exploit — the control socket lives under
`NW_CTL_DIR`, `0700`, reachable only to a process with access to a
directory this same uid-0 house tree already trusts by invariant 5.
Worth naming rather than silently accepting: this is a real, if narrow,
imprecision, not a claim of an exact bound.

## 3. Interaction with `NW_GRACE_MS` — an open question, not assumed away

`pid1.c`'s own `NW_GRACE_MS` (400ms) is a **fixed, unrelated** constant
governing two things: how long `shutdown_city()` waits for loggers to
drain, and how long it waits for a supervisor before SIGKILLing the
supervisor process directly. It is not plan-configurable and this note
does not touch `pid1.c` (see Section 6). A house declaring
`grace_period=5000` during a *city-wide* shutdown would have its
supervisor SIGKILLed by PID 1 at 400ms regardless of what the house's
own declared grace period asked for, because PID 1's fallback fires
first and does not know or care what `nw-sup` is doing — the supervisor
dying takes the house with it (pid-namespace teardown), pre-empting the
house's own longer-declared grace period entirely.

**So a declared `grace_period` longer than `NW_GRACE_MS` is honest and
fully effective for an explicit, single-unit `STOP` (no city-wide
shutdown in progress), and is silently truncated to whatever `NW_GRACE_MS`
allows during a full city shutdown.** That asymmetry is not stated
anywhere today because the field does not exist anywhere today; once it
does, a plan author declaring a long `grace_period` for shutdown
purposes specifically would be declaring something this mechanism
cannot honor. Two honest ways to resolve it, put to the operator as
**open question 1**:

- **(a) Leave it.** Document the asymmetry plainly (a longer
  `grace_period` protects an explicit `STOP`; a city-wide shutdown still
  runs on `NW_GRACE_MS`) and accept it, on the reasoning that a
  reboot/shutdown is already a machine-wide event with its own urgency,
  and 400ms is short enough that no real workload should expect more.
  Cheapest, and consistent with `pid1.c` staying untouched this round.
- **(b) Raise `NW_GRACE_MS`.** A `pid1.c` change (Grok's territory,
  needs separate authorization) to bound PID 1's own wait by the
  *largest* declared `grace_period` across all units rather than a
  fixed 400ms, so no house's declared grace period is ever silently
  truncated during a full shutdown. This is real design and code work
  in a file this note does not touch, and reopens the exact seam
  Section 6 below declines to cross.

Recommendation: **(a)**, this round. It costs nothing to state and
leaves (b) as a clearly-scoped follow-up if a real workload needs it,
rather than reaching into `pid1.c` on a guess that it's needed.

## 4. `nofile`/`capabilities` precedent this note follows

Both of Phase 4's most structurally similar fields already established
the pattern this note reuses: a mechanism gated on a plan-declared
value, applied via an existing kernel primitive `nwsup.c` already calls
for an unrelated reason (`setrlimit` for `nofile`, `poll` for this),
with the ordering/timing question answered by measurement rather than
assumption (`docs/options/31` Section 10 item 3's fault-injection
control for `oom_score_adj`; `fd-auditor`'s finding that `nofile` could
starve `lid_brick()`/`lid_landlock()` if applied at the wrong point).
This note's own equivalent finding is the control-socket exclusion in
Section 2 — found by working through the mechanism before writing any
code, the same discipline `CLAUDE.md`'s "a rule is at its weakest in the
change that introduces it" asks for.

## 5. Tests and controls

- **Read-back / behavioral, not read-back-only** — unlike
  `capabilities`/`oom_score_adj`, this mechanism is fully exercisable in
  this environment (no cgroup controller, no missing capability
  involved): a house that traps and ignores its stop signal
  (`houses/anysig.c` already exists and does exactly this — it only
  exits once it *receives* a signal, so a fixture that additionally
  ignores the signal, or a small new one, is needed) must be shown
  SIGKILLed within its declared `grace_period` and not before.
- **Paired, both directions**: a `grace_period` long enough that a house
  which stops promptly on its own is NOT killed (proving the mechanism
  does not fire when it should not), and a `grace_period` short enough
  that a house which never stops IS killed within it (proving it fires
  when it should). Read-back on "was killed" alone, without the
  promptly-stopping control, would be satisfied by a mechanism that
  always force-kills regardless of the timeout.
- **The control-socket exclusion (Section 2) needs its own control**: a
  `STOP` sent during an active grace-period wait must still get its
  `OK` reply once serviced (proving the backlog/queueing story is real,
  not merely asserted), and must not have extended the actual kill time
  past the declared `grace_period` from when escalation was armed.
- **`grace_period=0` (unset) must reproduce exactly today's unbounded
  wait** — a control that declares a nonzero value and shows the SAME
  ignoring-fixture eventually gets killed, paired with unset showing it
  is NOT killed within an equivalent window (bounded by the test's own
  patience, not a claim about forever).
- **`test_budget_is_hard_total`'s existing sweep must stay green** — this is
  the test that would refuse the whole approach if a clock primitive
  crept in anywhere in `nwsup.c`; no new exemption or narrowing of its
  regex is needed or proposed.
- **`NW_E_GRACE`... no**, actually: this note builds the applier
  `docs/options/31` deferred; no new `NW_E_*` code is needed, since the
  boot-time refusal for a nonzero, unapplied `grace_period`
  (`die("grace-period: declared but not yet applied")`, as of this
  note's base commit)
  simply stops firing once this mechanism lands — the value becomes
  legal to declare and act on, not newly refused a different way.
  the existing test pinning that refusal was renamed and rewritten
  rather than deleted -- `test_grace_period_bounds_refuse_at_the_supervisor`
  now asserts a legal, in-bound `grace_period` **boots successfully**,
  and that only an out-of-range value is still refused (by
  `NW_E_GRACERANGE`'s reason, not the old "declared but not yet
  applied" one). Correct direction for the mechanism landing, not a
  regression to explain away.

## 6. `supervisor_death_policy` — narrowed by ownership, not deferred by choice

**Finding, not a preference: a real applier for this field cannot be
built inside `nwsup.c` at all, structurally, regardless of who owns
what.** `docs/options/31` Section 6 already flagged that "what a
supervisor dying even means operationally" is undesigned; working
through it for this note surfaces why it cannot be `nw-sup`'s own code
that answers it. `nw-sup` observing its OWN unexpected death is a
contradiction — a process that has crashed, been OOM-killed, or
received an uncatchable signal cannot run cleanup code by definition,
and `nwsup.c`'s own `on_term()` handler only catches the signals it
explicitly installs a handler for (`SIGTERM`/whatever `stop_signal`
names), not `SIGKILL` or a genuine crash. **The only process that ever
observes a supervisor's own death is whatever reaps its exit status —
which is PID 1**, via its own `waitpid` loop over every house pid it
spawned (`pid1.c`). A `supervisor_death_policy` applier is therefore,
unavoidably, a `pid1.c` change: PID 1 would need to distinguish "this
supervisor exited via one of `nw_decide()`'s own graceful paths" from
"this supervisor died some other way," and act on the plan's declared
policy for the second case.

**That is exactly the file this round's authorization does not extend
to.** The operator's delegation was explicit: the restart/wait loop in
`nwsup.c` is operator-delegated per active phase; `pid1.c` and `dawn.c`
remain Grok's untouched territory, stated plainly rather than loosened
by proximity to other `nwsup.c` work this same round. Building a
`supervisor_death_policy` applier would mean editing `pid1.c` without
that authorization, on the strength of an inference ("I'm already
authorized nearby") rather than a direct instruction — precisely the
shape `CLAUDE.md`'s ownership discipline exists to refuse.

**There is also a live historical landmine directly in this field's
path, worth naming even though it does not change what this note
builds.** Bug 3 — "a supervisor giving up, PID 1 restarting it with a
fresh budget, and the pair looping" — is exactly what a naive
`supervisor_death_policy=restart-supervisor` value would reintroduce if
PID 1 ever granted a crashed supervisor a fresh restart budget. `CLAUDE.md`
invariant 4 states plainly that budgets are never nested and PID 1 has no
restart budget and must not grow one (`grep` for `budget`/`restart`/
`respawn` in `pid1.c` returns nothing — a live, checkable invariant this
field's eventual design must not violate). Any future policy vocabulary
for this field has to answer *that* question specifically before it
answers anything else, and that answer belongs in `pid1.c`'s own
territory, with its own design-note-plus-`claims` round, not folded into
this one as a side effect of also touching `nwsup.c`.

**So: `supervisor_death_policy` stays exactly as `docs/options/31` left
it.** The byte exists in the blob (unchanged), the baker validates it
against the closed set `{0}` (unchanged), and `nwsup.c` continues to
refuse any nonzero declared value at startup by name (unchanged by
this note's code). Nothing in this note's code changes
touches it. `docs/QUEUE.md`'s Phase 4 follow-up list should read this
item as "waiting on a `pid1.c`-scoped round with its own authorization,"
not as "next" — a kind-3 statement in `CLAUDE.md`'s own sense, not kind
1, and it should say so rather than imply it is merely unscheduled.

## Definition of done

`make test` passes, quoted from its own output, including the new
grace-period behavioral tests and their paired controls (Section 5).
`test_budget_is_hard_total` stays green with no exemption. The two open
questions (Section 3's `NW_GRACE_MS` interaction, recommendation (a))
are decided before code, the same discipline `docs/options/31`'s
Section 10 batch followed.
