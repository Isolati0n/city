# 16 — Operator-controlled freeze / single-step

**SUPERSEDED 2026-09-28 (item 1c, `docs/OPERATOR-BRIEF.md` Section 2).**
`FREEZE`/`CONT`, the ptrace code (SEIZE/INTERRUPT/CONT,
`is_ptrace_event_stop`/`forward_if_real_signal`), the exec-fence pipe
and its fd, and the unconditional signalfd that sat beside pidfd (it
existed only to see a ptrace-stop pidfd_open(2) cannot) are all removed
from `nw-sup`. `STOP` still works, with no ptrace forwarding, and the
pidfd_open-failure fallback tiers (signalfd, then a bounded 50ms poll)
are unchanged — signalfd is once again purely that fallback, not a
second channel run alongside pidfd. The design and the review record
below are kept as the reasoning that led here, not as a description of
what the tree does today; read `.claude/rules/runtime.md`'s
`wait_house()` note for the current shape.

Status (as originally written; no longer current): **built and
reviewed across two rounds; the fd-auditor MEDIUM
is now closed.** Freeze and continue (`FREEZE`/`CONT`) are real,
syscall-level verbs on the existing per-unit control socket, wired
into `nw-sup` alongside `START`/`STOP`. Single-step (`STEP`) is named
and scoped but **deferred as a fast-follow**, per instruction — this
round proves freeze-and-inspect works at all before adding
instruction-level control on top of it. A follow-up round closed the
one open finding from the first: `FREEZE` can no longer race a house's
own lid setup before its `execv()` — see "Review findings" below for
the exec-fence fix and the independently-discovered signal-disposition
bug it also closed.

**`wait_house()` needed more than the one fix this note originally
scoped.** Reviewers found the first version treated every stop alike
(silently discarding a real signal once a house had ever been frozen —
`tcb-review`, CRITICAL) and covered only two of the three wait tiers
(the tier every real machine actually uses was still broken — found by
this round's own new test, not by review). Both are fixed; see "Review
findings" below for the full account, including a fix found to be
incomplete by running its own regression test, then corrected again.

## Ownership flag — read before the build, not after

`CLAUDE.md`'s "Who owns which file" currently reads: *"Grok owns `pid1.c`,
`dawn.c` and the restart loop in `nwsup.c`. Claude owns the baker and the
mount path in `nwsup.c`."* Everything this note proposes lives in
`wait_house()`, `handle_ctl_live()`, and the per-unit fork/wait/restart
loop in `nwsup.c` — that is, by name, **the restart loop**, which the
table currently assigns to Grok, not to the mount-path/baker slice it
assigns to me.

`docs/options/11-start-stop-channel.md` (the note that built the control
socket this one extends) already flagged exactly this seam when it was
written — *"If this is built, the restart-loop restructuring is a Grok
brief... Whoever picks this up should confirm that before starting,
rather than inheriting this note's belief about it unchecked."* That
confirmation evidently happened, since the control socket and the
pidfd/signalfd/poll rebuild `wait_house()` now has are already in the
tree — but this note is not the one that built them, and re-entering
that same function for `FREEZE`/`CONT` is the same seam recurring, not a
new one. Stated here rather than silently claimed: this build touches
Grok's assigned territory, on the operator's direct instruction to do so
this round. Per `CLAUDE.md`'s own "fix it and flag it" rule this is
**not** the narrow emergency-fix case (the trunk boots fine today; this
is planned feature work, not a repair). **`CLAUDE.md`'s ownership
section names exactly two ways to proceed: edit the ownership sentences
when the assignment moves, or the narrow emergency-fix case above — it
does not itself name "an operator directed this specific piece of
territory-crossing work" as a third category.** Proceeding under direct
operator instruction is not a contradiction of that (a human operator
can direct work anywhere, and did), but it is a process this file does
not itself write down, and this note should say so rather than imply
its own precedent covers it. Recorded so the table can be corrected or
the assignment revisited rather than the fact going unrecorded.

## The measurement, before any design

**YAMA — two different claims, checkable two different ways, and
`claims` correctly found this note blurring them.** The KERNEL
IDENTITY half is checkable from this tree, right now, by anyone: `tools/
mkboot.sh` defaults `KERNEL=${KERNEL:-/boot/vmlinuz}`, which resolves to
the bundled `6.8.0-139-generic` image and its matching
`/boot/config-6.8.0-139-generic`, which has `CONFIG_SECURITY_YAMA=y` —
re-verified with `grep -i YAMA /boot/config-6.8.0-139-generic` while
writing this revision, not merely recalled. This sandbox's OWN running
kernel (`6.18.44-fc-v37`) has no YAMA at all
(`CONFIG_SECURITY_YAMA is not set`, re-verified via `/proc/config.gz`
the same way), and `/proc/sys/kernel/yama/ptrace_scope` does not even
exist here — so nothing about the RUNTIME VALUE can be re-derived from
this tree; a live boot of the QEMU target is the only way to read it,
and this sandbox cannot perform one.

The RUNTIME VALUE, `1` ("restricted": `PTRACE_ATTACH`/`PTRACE_SEIZE`
refused unless the attacher is the tracee's real parent, the tracee
called `PTRACE_TRACEME`, the attacher holds `CAP_SYS_PTRACE`, or the
tracee opted in via `prctl(PR_SET_PTRACER, ...)`), is an **operator-
reported measurement from an earlier round of this same work** — a live
guest boot, reading a crafted bind-mount of the host's own
`ptrace_scope` file into a brick (which has no `/proc` by default) —
and this note should say so as plainly as `harness.md`'s "a claim that
needs a real boot goes to the operator" section requires, rather than
stating it as flatly as the kernel-identity half that this session
*can* re-check. Written as **reported**, per that section's own
convention, not as something this note re-verified. It is also the
kernel's own compiled-in default (`YAMA_SCOPE_RELATIONAL = 1` in
`security/yama/yama_lsm.c`, and Ubuntu ships that default unchanged),
which is corroborating context for why `1` is unsurprising — not a
substitute for the boot that actually read it. **Not yet added to
`docs/ENVIRONMENT.md`'s dual-environment entries (sched_ext, cgroup v2,
project quota, each with "how it was measured and where") — it belongs
there before this is built, so the next reader finds it where this
project keeps that class of fact rather than only inside this note.**

**`nw-sup` is the house's genuine, direct parent — not a proxy, not
double-forked.** `nwsup.c`'s per-unit loop is a single `fork()` whose
child branch calls `execv()` directly (`nwsup.c`, the fork/exec block
inside the main loop): `pid_t p = fork(); ... if (p == 0) { ...
execv(path, av); }`. No intermediate reaper, no `nw-spawn` in the
line — `nw-spawn`'s own double-fork is how it hands houses to PID 1 for
*orphan adoption bookkeeping* (invariant 4's "boot-time only, then
exits"), not how a house's supervisor is related to it. So `nw-sup`
satisfies YAMA restricted mode's parent exemption unconditionally, on
every kernel this project targets, without needing `CAP_SYS_PTRACE` or a
`prctl(PR_SET_PTRACER)` call from the house — which matters as a design
choice, not only as current happenstance: the parent relationship holds
regardless of whether a future change ever drops privilege from
`nw-sup`, where relying on capability alone would not.

**A real, load-bearing bug in `wait_house()`, found before any of this
was built.** `wait_house()` has three tiers (`nwsup.c`, documented at
length in its own header comment): a `pidfd_open`-plus-`poll()` primary
path, a `signalfd(SIGCHLD)` fallback if `pidfd_open` fails, and a bounded
50ms-poll-plus-`waitpid(WNOHANG)` final fallback if `signalfd` also
fails. None of the three was written with ptrace in mind, because
nothing in this tree used ptrace before this round:

- **The `pidfd` tier is immune.** A pidfd only becomes poll-readable
  when the process it names *terminates* — `poll(2)`'s own documented
  behaviour, unaffected by ptrace-stops, job-control stops, or anything
  short of actual exit. This is the tier every machine in the suite
  exercises today (`pidfd_open` succeeds on any kernel ≥ 5.3), so the
  bug below is currently unreachable in practice — which is exactly why
  it was findable only by reasoning about the fallback tiers, not by
  running the existing suite.
- **The `signalfd` tier is not immune, and this is the real bug.**
  Once `nw-sup` becomes the house's ptrace tracer, `SIGCHLD` is
  delivered to it on *every* stop of the tracee, not only on
  termination — this is documented `ptrace(2)` behaviour: a ptrace-stop
  (and a group-stop, once traced) is reported to the tracer's `wait()`
  family **regardless of `WUNTRACED`**, unlike an ordinary job-control
  stop seen by a non-tracing parent. The `signalfd` branch's
  `waitpid(p, &st, WNOHANG)` will therefore return `r == p` with
  `WIFSTOPPED(st)` true on a freeze, and the code as it stands today
  does `if (r == p) { close(wake); return st; }` — treating the stop as
  a reap. Nothing downstream checks `WIFSTOPPED`; the per-unit loop's
  death accounting goes straight to `WIFEXITED(st) ? ... :
  WTERMSIG(st)`, and `WTERMSIG` on a stopped-not-signaled status reads
  the wrong bitfield — a silent wrong number in the same "reads as
  specific and is not" shape `CLAUDE.md`'s own record keeps naming, not
  a crash.
- **The final 50ms-poll tier has the identical bug**, for the identical
  reason: its `waitpid(p, &st, WNOHANG)` is the same call with the same
  blindness to `WIFSTOPPED`.

So without a fix, **freezing a house would look like its death to the
supervisor that is about to freeze it** — the house would be reported
dead, its restart budget spent a life it did not lose, and (depending on
timing) a fresh generation forked to replace a process that is not
actually gone, racing the frozen original. This is why the fix is
described as directly load-bearing rather than a nice-to-have alongside
the feature: freeze is unusable without it, on any machine that falls
back off the `pidfd` tier, and unusable *unpredictably* if a future
kernel or container profile ever makes `pidfd_open` fail where it
succeeds today.

**The fix:** every `waitpid()` call site inside `wait_house()` that
currently treats "`waitpid` returned this pid" as "reap it" must check
`WIFSTOPPED(st)` first and, if true, **not return** — drain/reset and
continue the poll loop, exactly as if nothing had happened. This applies
uniformly to any stop, not only ones this round's own `FREEZE` verb
causes: an external `SIGSTOP` sent to a traced house behaves identically
under ptrace's stop-reporting rules, and `wait_house()` should not be
able to tell the difference (nor does it need to — every caller of this
function already assumes it returns only on genuine termination, and the
fix restores that assumption rather than adding a new one for it to
reason about).

## The six questions

**1. Environment support.** Confirmed above: `ptrace(2)` is available on
every kernel this project targets (it predates sched_ext by decades and
needs no `CONFIG_*` gate the way sched_ext does), and `nw-sup`'s
parent-child relationship with its house satisfies YAMA restricted mode
unconditionally. Unlike item #15, there is no capability gap to design
around — the mechanism is universally available; the design problem is
entirely about the `wait_house()` misread above, not about whether the
kernel offers ptrace.

**2. Plan-level interface — there is deliberately none.** This is the
first place this note diverges from #15's shape rather than following
it: `sched-ext=` needed a plan field because it is a *boot-time declared
capability*, resolved once and never touched again. `FREEZE`/`CONT` are
not that — they are **live operator actions on an already-running
house**, exactly the same shape `START`/`STOP` already are on the same
socket. `docs/options/11-start-stop-channel.md`'s own reasoning applies
verbatim: "a socket is acceptable *because* it changes what is
**running**, never what is **possible** — the plan already decided what
each house may do; this channel only decides whether it is doing it
right now." Freezing a house changes nothing about what it is permitted
to do; it is the same authorization story as `START`/`STOP`
(reachability of the socket via `bind=`, nothing more), extended with
two more fixed request strings. No blob field, no `NW_E_*` code, no
`NW_MAGIC` bump — there is no format change here to reason about the way
there was for `sched-ext=`, because nothing is being added to the plan.

**3. Mechanism.** `PTRACE_SEIZE` (not `PTRACE_ATTACH`) on first `FREEZE`
of a unit, because `SEIZE` does not itself stop the tracee the way
`ATTACH` does — attaching is a separate event from stopping, which
matters here since `nw-sup` wants to attach once, lazily, on the first
`FREEZE`, and stay attached afterward without forcing a stop it did not
ask for. `FREEZE` itself is `PTRACE_INTERRUPT`, which forces a
ptrace-stop on a seized tracee regardless of what it is doing. `CONT` is
`PTRACE_CONT(pid, 0)` — resume, no signal injected, still attached (not
`PTRACE_DETACH`; staying attached is what lets a later `FREEZE` or the
deferred `STEP` reuse the existing tracer relationship instead of
re-attaching). **"Attached" means for that one pid's whole life, not
the unit's** — a restart forks a genuinely new pid that was never
seized, so `ptrace_attached` resets to 0 at every fresh fork, alongside
`house_frozen`; a stale "already attached" surviving a restart would
send `PTRACE_INTERRUPT` at a pid `nw-sup` never actually traced. Found
by a flooded-FREEZE control test misbehaving across restarts, not by
reasoning alone — recorded here because the first draft of this
mechanism got exactly this wrong.

**`FREEZE`/`CONT` are fire-and-forget, exactly like `START`/`STOP`
already are — not synchronously confirmed inside the ctl handler, and
this is a correction from this note's own first draft, found by
reasoning through the mechanism before writing any code rather than
after.** The first draft had `handle_ctl_live()` call `waitpid()`
itself, inside the request handler, to confirm the stop before replying
`"OK\n"`. That creates a real hazard: `handle_ctl_live()` runs
synchronously inside `wait_house()`'s own poll loop, and reaping is
`wait_house()`'s job, done exactly once per real event, with the
resulting status carried back to the per-unit loop's death accounting.
If `handle_ctl_live()` also calls `waitpid()` and the house happens to
die for a real, unrelated reason in the narrow window between the
`FREEZE` request arriving and `PTRACE_INTERRUPT` landing, that call
would reap the real death **inside the ctl handler**, where none of the
budget/`restart`/`spent`/evidence bookkeeping that normally follows a
death lives — the death would be silently swallowed, reaped but never
accounted, the exact "recovery mechanism turns a defect into a
disappearance" shape `CLAUDE.md`'s own record warns about. Two separate
call sites both calling `waitpid()` on the same pid, one of them inside
a request handler that has no path back to the other's bookkeeping, is
the bug — not a hazard specific to ptrace.

So instead: `handle_ctl_live()`'s `FREEZE` branch issues
`PTRACE_SEIZE` (if needed) and `PTRACE_INTERRUPT`, sets a `house_frozen`
bookkeeping flag, and replies `"OK\n"` immediately — meaning "the
request was issued," exactly what `STOP`'s own `"OK\n"` already means
(`STOP` sets `stop_requested` and replies `"OK\n"` before the house has
actually died either; `test_ctl_stop_does_not_count` confirms the real
death afterward with its own follow-up poll, not by reading anything
into the reply). `house_frozen` is set **optimistically, in the
handler, the same fire-and-forget way `stop_requested` already is** —
it is bookkeeping for this handler's own idempotence (so a second
`FREEZE` or a `CONT` on a never-frozen unit can be answered without a
syscall), not a claim that the kernel has already produced the stop.

**What `wait_house()`'s fix has to get right is independent of that
flag entirely, and is a strictly simpler requirement than tracking
freeze state in the wait loop at all: it must never treat *any*
`WIFSTOPPED` status as a reap**, whether the stop was caused by our own
`PTRACE_INTERRUPT`, an external `SIGSTOP` sent to a traced house, or
anything else that produces one — `wait_house()` does not need to know
which, and should not try to. `CONT` mirrors `FREEZE`: `PTRACE_CONT(pid,
0)` issued if `house_frozen` is set, the flag cleared, `"OK\n"` replied
immediately, no confirming `waitpid()`. A caller that needs to know the
freeze has actually taken kernel effect polls `/proc/<pid>/status`
afterward, the same way `STOP`'s own tests already poll `/proc` rather
than trusting the reply as proof.

**4. Who applies it.** `nw-sup`, in `handle_ctl_live()` (and the
`stopped`-state ctl handler beside it), recognising two new fixed
request strings alongside the existing `START\n`/`STOP\n`. No new
process, no new descriptor class beyond the ptrace relationship itself
(which holds no descriptor — `ptrace(2)` is not fd-based).

**5. Failure mode — and it is a different shape from every lid's,
because the request being refused is not "may this house run" but "can
I pause a house that is already running."** A lid's `die()` on refusal
is correct because an unconfined house running where the plan says
confined is the plan lying — nothing comparable is at stake here. If
`PTRACE_SEIZE` fails (measured causes: `ESRCH` if the house already
exited between the request arriving and the attach; in principle
`EPERM` if a future change ever breaks the parent relationship this
design leans on, though nothing in this tree can produce that today),
the house is **not** killed and **not** left in some half-attached
state — the ctl handler replies `"ERR <reason>\n"` over the same
connection and socket the existing malformed-request refusal already
uses. **The connection and the `"ERR "`-prefixed shape are precedented;
the parameterized reason text is not** — every `ERR` reply in the tree
today is the single fixed string `"ERR bad request\n"`, so `"ERR
seize failed\n"` and `"ERR interrupt failed\n"` would be the first
instance of a *variable* reason on this channel, not a second use of an
existing one, and this note should not read as claiming otherwise. A
`FREEZE` on an already-frozen unit is idempotent `"OK\n"` (no second
`PTRACE_INTERRUPT`, matching `START`/`STOP`'s own idempotence pattern);
a `CONT` on a unit that was never frozen is idempotent `"OK\n"` with no
syscall at all.

**6. COMMITMENT CHECK — and this is the question this note has to
answer most carefully, because it is the one most easily confused with
a refusal already on record.** `runtime.md`'s Liveness section refuses
**freeze detection** — an automatic mechanism that decides, on its own,
that a house is stuck: "every form of detection needs a guessed
constant, and the rule has been attempted and wrong every time."
**This is not that, and the distinction is not a matter of degree: it
is the presence or absence of a trigger `nw-sup` invents for itself.**
`FREEZE` and `CONT` fire **exclusively** because a caller connected to
the socket and sent exactly that request — the same trigger `START` and
`STOP` already have, which nobody has read as reopening the Liveness
refusal, because it isn't one. Confirmed by construction rather than by
argument: nothing this note proposes reads a clock, a byte count, or an
elapsed-time threshold anywhere (`wait_house()`'s fix touches only how a
*status already returned by the kernel* is interpreted, adding no new
wait, timeout, or polling cadence of its own). If a future change ever
made `nw-sup` decide *by itself* that a house looks frozen and should be
paused or reported as such, that would be freeze detection and would
need the Liveness section revisited on its own terms — nothing here is
that, and nothing here should be read as a step toward it.

## Wire protocol extension

Same shape as `docs/options/11-start-stop-channel.md`'s existing
protocol, extended rather than replaced: one Unix domain stream socket
per unit, one connection, one fixed request string, one line back, close.

- `"FREEZE\n"` → lazily `PTRACE_SEIZE`s if not already attached, then
  `PTRACE_INTERRUPT`s, then replies immediately — `"OK\n"` meaning "the
  request was issued," not "confirmed frozen" (see the mechanism section
  above for why a synchronous confirmation inside the handler is the
  wrong shape); `"ERR <reason>\n"` if `PTRACE_SEIZE` itself fails, house
  left running. The actual transition to ptrace-stop is observed by
  `wait_house()`'s own poll loop, asynchronously, the same as `STOP`'s
  own actual death is.
- `"CONT\n"` → `PTRACE_CONT(pid, 0)` issued if currently frozen,
  `"OK\n"` replied immediately, no confirming `waitpid()` in the
  handler; `"OK\n"` immediately with no syscall at all if not currently
  frozen.
- `"STEP\n"` → **deferred, named, not built this round.** Scoped in
  advance so extending the parser later does not repeat this note's own
  reasoning: `PTRACE_SINGLESTEP` on a currently-frozen tracee, one
  machine instruction, then re-stop and reply `"OK\n"`; `"ERR not
  frozen\n"` if the unit is not currently frozen. This round's `FREEZE`
  is deliberately sufficient on its own for the stated first use — an
  operator or tool inspecting a paused house via ordinary `/proc/<pid>/`
  reads (`status`, `maps`, `fd/`, `stack`), none of which require being
  the tracer and all of which work identically on a stopped or running
  process. `STEP`, and any register-level introspection beside it,
  **does** require holding the tracer role continuously and is exactly
  the fast-follow this note reserves rather than builds.

**A real constraint worth naming rather than discovering later: a
process has exactly one tracer.** Once `nw-sup` holds the ptrace
relationship (from the first `FREEZE` onward, for the unit's whole
life), an external debugger cannot also `PTRACE_ATTACH` to the same pid
while `nw-sup` holds it — it would get `EPERM`, the same "second
authority over one child" shape invariant 4 already names for restart
budgets, arriving here for ptrace instead. This is not a defect to fix
this round: the stated use case is `nw-sup`-mediated freeze/inspect, not
handing the house to an external debugger, and `/proc` reads (this
round's whole inspection story) do not need the tracer role at all. It
is recorded here because the moment `STEP` or any register read is
built, this constraint is why nothing else can "just also" attach for a
deeper look at the same time — a design question for that round, not
this one.

## Refusals, each to be paired with a test that pins it

- **Malformed request** (unchanged from #11 — `FREEZE`/`CONT`/`STEP` are
  three more fixed strings the same parser recognises, nothing about the
  refusal path for garbage changes) → `"ERR bad request\n"`.
- **`FREEZE` on an already-frozen unit** → idempotent `"OK\n"`, no second
  `PTRACE_INTERRUPT`. Test: freeze twice, assert the state is
  unchanged (still stopped, no second ptrace event observed) and the
  reply is `"OK\n"` both times.
- **`CONT` on a unit that was never frozen** → idempotent `"OK\n"`, no
  syscall issued, unit's running state unaffected. Test: send `CONT` to
  a freshly-started unit, assert it is still running and the reply is
  `"OK\n"`.
- **`FREEZE` racing the house's own natural exit** — the same shape
  `docs/options/11-start-stop-channel.md` already names for `STOP`
  racing a crash: `PTRACE_SEIZE`/`PTRACE_INTERRUPT` on a pid that has
  already exited returns `ESRCH`, refused with `"ERR <reason>\n"`, no
  effect on the (already-dead) house's death accounting, which proceeds
  through the ordinary `wait_house()` path exactly as it would have
  without the `FREEZE` ever arriving. **Test, and a real bug this exact
  test found**: a flood of `FREEZE`/`CONT` pairs against a house dying
  and restarting on its own, asserting nw-sup never crashes, every
  reply is well-formed, and the budget still exhausts normally — not a
  bare `FREEZE` flood with no `CONT`, which would legitimately (and
  correctly, per the fix) stall the death/restart cycle rather than
  starve it, since a genuinely frozen house is not dying. Running this
  found that `ptrace_attached`, as first written, was sticky for the
  supervisor's *whole life* rather than one generation's: after the
  first death and restart, a later `FREEZE` saw the flag already set
  from the *previous*, now-dead pid, skipped `PTRACE_SEIZE`, and issued
  `PTRACE_INTERRUPT` against a pid `nw-sup` had never actually attached
  to. **Fixed** by resetting both `ptrace_attached` and `house_frozen`
  to 0 at every fresh fork, alongside the existing per-generation
  reset the restart loop already does for other state.
- **The `wait_house()` fix itself, tested directly and adversarially**:
  freeze a unit, assert **no** `restart`/`spent` line is produced and
  the death counter is unchanged while it sits frozen for a period a
  human would call "a while" (a fixed, generous wall-clock wait in the
  test, not a claim about a general timeout — this project already
  refuses timeout-shaped reasoning in `nw-sup` itself; the test may use
  one, `nw-sup` may not), then `CONT` it and assert it resumes and
  completes normally. The control for this test **must** revert the
  `WIFSTOPPED` check and show it failing — a false restart/spent line
  appearing while the unit is genuinely still alive and frozen — per
  `harness.md`'s "a test that has never been seen failing is a test that
  has never been tested."

## Review findings, and what changed because of them

**`tcb-review` — CRITICAL, reproduced live, fixed.** The first version
of this fix (both `WIFSTOPPED` guards in `wait_house()`) treated *every*
stopped status identically: "not a reap, keep polling." That is correct
for our own `FREEZE`, and wrong for anything else, because of a fact
neither this note nor the code accounted for: **once a pid has ever been
`PTRACE_SEIZE`d, every signal sent to it — not only the
`PTRACE_INTERRUPT` this file issues — is intercepted into a
signal-delivery-stop and never reaches the tracee until the tracer
re-injects it via `PTRACE_CONT`'s own signal argument.** `do_cont()`
always calls `PTRACE_CONT(pid, 0)`, which is correct for resuming our
own event-stop (a group-stop carries no signal to redeliver) and wrong
for anything else, because there was nothing else to distinguish.

Net effect, reproduced against the real staged binary: `FREEZE`, then
`CONT` (house apparently back to normal, running), then **`STOP`** —
the existing item #11 verb — replies `"OK\n"` exactly as it always has,
and the house never dies. Its own `SIGTERM` was intercepted the moment
it was sent, because the pid had been seized once, earlier, and was
never un-seized. The same fate awaits PID 1's own graceful-shutdown
`SIGTERM` (`on_term` in `pid1.c`) for any house that has ever been
frozen even once in its life — a silent, permanent loss of governability
for that generation, invisible until someone tries to stop it and
nothing happens. This is precisely `CLAUDE.md`'s "characteristic
failure" shape, landed by this round's own code: a true-looking
sentence ("`FREEZE`/`CONT` are fire-and-forget, matching `START`/
`STOP`'s own shape") standing next to code that quietly broke the very
verb it was compared to.

**Fixed** by distinguishing the two kinds of stop rather than treating
them alike. `ptrace(2)`'s own documented signal: a stop nw-sup itself
caused via `PTRACE_INTERRUPT` is reported as `status>>8 == (SIGTRAP |
(PTRACE_EVENT_STOP<<8))`; an ordinary intercepted signal reports
`WSTOPSIG(status)` as the real signal number, with no such event bits
set. `is_ptrace_event_stop()` checks the first form; anything else is
forwarded via `ptrace(PTRACE_CONT, p, NULL, (void*)(intptr_t)
WSTOPSIG(st))` — re-injecting the exact signal that was intercepted, so
the tracee processes it under its normal disposition, same as if
ptrace had never been involved. Verified empirically before touching
the real code, with a minimal standalone reproduction (seize, interrupt,
observe the event-stop signature; cont; send a real `SIGTERM` while
running; observe an ordinary signal-delivery-stop with the *wrong*
signature; forward it; observe the process actually terminate via
`WIFSIGNALED`/`WTERMSIG`) — not assumed from the man page alone.
`test_ctl_freeze_cont_then_stop_actually_kills` is the permanent
regression test, reproducing `tcb-review`'s exact finding: freeze,
cont, then stop, asserting the house is actually dead afterward — this
is the test that would have caught the defect had it existed before
the finding, and does now.

**The first attempt at that fix was itself incomplete, and found wrong
by running the new test, not by re-reading it.** `forward_if_real_signal()`
was added only inside the two fallback tiers (`signalfd`, the 50ms
poll), because that is where the original `WIFSTOPPED` guards already
lived. `test_ctl_freeze_reattaches_after_restart` — which runs under no
forced fallback, i.e. the `pidfd` tier every real machine actually
uses — immediately failed with `"STOP did not actually kill the first
generation"`. The reason is structural, not a missed line: **`pidfd`
is documented to become poll-readable only on genuine termination,
never on any kind of ptrace-stop** — not our own event-stop, and not an
ordinary intercepted signal either. Under the `pidfd`-only tier,
`wait_house()`'s `poll()` has no fd that becomes ready when a signal is
intercepted, so it does not merely mis-handle the stop — it never
observes one happening at all, and blocks forever.

**Fixed by a real structural change, not a third guard**:
`signalfd(SIGCHLD)` is now created and polled *unconditionally*,
alongside `pidfd` when `pidfd` is also available, rather than only as
a fallback for when `pidfd_open` fails. `pidfd` keeps doing what it is
good at — the fast, common, termination-only path, `break`ing straight
to a final reap the moment it fires, exactly as before. `signalfd` is
the channel that can see *every* kind of ptrace-stop, so it is what
now catches an intercepted real signal under the default tier and
forwards it, the same way it already did under the fallback tiers.
The 50ms-poll tier is unchanged in spirit and now reached only when
*both* `pidfd` and `signalfd` fail. This costs one extra descriptor per
`wait_house()` call in the common case (every house, frozen or not,
now gets a `signalfd` alongside its `pidfd`) — a small, fixed,
dynamically-allocated cost per supervisor process, not per house-count
in the sense invariant 3 or invariant 2 govern (each `nw-sup` is its
own process with its own table; this does not touch PID 1's or
`nw-spawn`'s own descriptor budget arithmetic at all). Re-verified:
every existing `pidfd`-fallback and `signalfd`-fallback test (which
force one tier or the other via the same `LD_PRELOAD` shims as before)
still passes unchanged, alongside the newly-passing default-tier case.

**`fd-auditor` — MEDIUM, CLOSED in a follow-up round.** `child = p` is
set the instant `fork()` returns, before the child has applied any lid
or reached `execv()`, and `handle_ctl_live()`/`do_freeze()` were
reachable the moment the ctl socket existed — there was no gate on
whether the target pid had finished lid setup. An operator issuing
`FREEZE` immediately after observing a restart could in principle have
seized the forked copy of `nw-sup` mid-`lid_brick()` (which may hold a
loop device attached via the already-contested `LOOP_CTL_GET_FREE`
retry `runtime.md` documents) rather than the house itself, and held
that scarce resource open for as long as it stayed frozen —
operator-controlled and potentially unbounded, unlike the transient
contention `runtime.md` already documents resolving via the restart
budget.

**The fix: a CLOEXEC exec-fence, one pipe per supervisor generation.**
Right before `fork()`, `nw-sup` creates `pipe2(ef, O_CLOEXEC)`. The
parent closes `ef[1]` immediately and keeps `ef[0]` (made
`O_NONBLOCK`) as the static `exec_fence_rd`; the child closes `ef[0]`
and keeps `ef[1]` open through every lid application. CLOEXEC's
defining behaviour closes the child's `ef[1]` the instant its
`execv()` succeeds — no explicit signal, no new synchronization
primitive, nothing beyond what the kernel already does to every
CLOEXEC descriptor across exec. The parent's `check_exec_fence()`
reads `exec_fence_rd`; `read()` returning `0` (EOF) means the child's
`ef[1]` is gone, which can only happen post-exec, so `child_execed` is
set and the fd is closed and reset to `-1` promptly (mirroring the
existing `ptrace_attached`/`house_frozen` per-generation reset this
same file documents above). `FREEZE` now calls `check_exec_fence()`
first and answers `"ERR not execed\n"` — a named, distinguishable
refusal rather than proceeding into `do_freeze()` at an undefined
point in the child's own startup — when `child_execed` is still 0.
This adds exactly one descriptor to `nw-sup`'s own table per
supervisor generation, reviewed by `fd-auditor` alongside the fix
itself.

**A second, independently-discovered bug closed in the same change,
not introduced by it.** Forcing the fork-to-execv window
deterministically (an `LD_PRELOAD` shim, `tests/delay_exec.so.c`,
delaying `execv(2)` by a configurable interval) and then asserting
that `STOP` still kills a pre-exec child — a premise the fix's own
control needed to hold — found that it did not: `fork()` copies
signal *disposition*, so every forked child inherited `nw-sup`'s own
installed `SIGTERM`/`SIGINT` handler (`on_term`) until its own
`execv()` reset handled signals back to `SIG_DFL`. A `SIGTERM` sent to
a pre-exec child therefore invoked the *parent's* handler code — a
no-op from the killed target's own perspective — instead of
terminating it. Confirmed pre-existing via `git show HEAD~2:nwsup.c`
(predates this round entirely), not something the exec-fence work
introduced; only this round's more rigorous testing exposed it.
**Fixed** by resetting `SIGTERM`/`SIGINT` to `SIG_DFL` at the very
start of the child fork branch, before any lid application.

**A third bug, in the signal *mask* rather than disposition, found by
`tcb-review` auditing the second — and this file's own first telling
of it was wrong.** It said the mask was "the pre-existing D11 fix
already clears", which is true of generation one and false of every
generation after it: D11's clear (`sigprocmask(SIG_SETMASK, &empty,
...)`, top of `main()`) runs exactly once, before the restart loop
starts. What actually determines later generations' mask is
`wait_house()`, which blocks `SIGCHLD` (`sigprocmask(SIG_BLOCK, &sc,
...)`) to arm its own signalfd every time it is called, and never
unblocks it — so by the second fork, `nw-sup`'s own process already
has `SIGCHLD` blocked, and the new child inherits that. Uncaught, it
rides through `execv()` into the house's own image: measured via
`/proc/<pid>/status`'s `SigBlk`, the exec'd process itself (not merely
nw-sup's transient pre-exec copy) carried `SIGCHLD` blocked from its
second restart onward. Any longrun house that forks its own children
and expects default `SIGCHLD` delivery silently stops reaping them
after this unit's first restart, for the rest of the supervisor's
life, with nothing anywhere reporting it. **Fixed** by resetting the
full mask to empty in the same child branch, alongside the
`SIG_DFL` resets.

Controls, red then green: `test_ctl_freeze_refused_before_execv` sends
`FREEZE` inside the shim-forced pre-exec window and requires exactly
`"ERR not execed\n"`, positively confirming via `/proc/<pid>/comm` on
the pre-exec child (not yet `sleep`, the shim's underlying binary)
that the window was genuinely hit rather than merely timed;
`test_ctl_freeze_after_execv_still_works` confirms the unchanged
post-exec path; `test_ctl_start_stop_unaffected_by_exec_fence` pins
the SIGTERM/SIGINT-disposition fix, confirming `STOP` still kills a
pre-exec child. Mutation: removing the `!child_execed` gate turns the
first red (`FAIL: FREEZE inside the pre-exec window was 'OK\n', not
the named refusal`); removing the signal reset turns the third red
(`FAIL: STOP did not kill the pre-exec child`) — both reproduced
directly, not assumed. `test_ctl_exec_resets_sigchld_mask` pins the
third fix the same way: it forces a house into the pre-exec window,
reads `/proc/<pid>/status`'s `SigBlk` directly, and requires it `0`
rather than carrying `SIGCHLD`'s bit; removing the mask reset turns it
red, reproducing exactly the `tcb-review` finding above.

**`control` — a fourth coverage gap, found and closed.** None of the
three tests above exercises the per-generation `child_execed = 0`
reset itself: the two FREEZE tests only ever run against a fresh
`nw-sup`'s first fork, where the flag's implicit initial value (`0`)
is correct whether or not the reset line runs, and the STOP/START test
forces a second generation but never sends `FREEZE` into it.
Confirmed by `control` as a real gap, not a hypothetical one: removing
`child_execed = 0` from the reset passed the *entire* suite, all three
tests included, exactly this project's own "green does not mean
covered" shape.
`test_ctl_freeze_refused_before_execv_second_generation` closes it —
STOP+START forces a genuinely new generation (nw-sup's own
`LD_PRELOAD`/delay-shim environment is set once at process launch and
inherited by every fork it makes, so the shim delays the second
generation's `execv()` exactly as it did the first's), and `FREEZE`
sent into *that* generation's pre-exec window must still see the
named refusal rather than a stale, carried-over confirmation from the
first. Mutation: removing the reset turns it red — `FAIL: FREEZE
inside the SECOND generation's pre-exec window was 'OK\n', not the
named refusal -- a stale child_execed=1 carried over from the
confirmed first generation` — reproduced directly.

**`fd-auditor` — clean.** Live fd census across 35+ restart
generations (`/proc/<nw-sup-pid>/fd`, sampled every 150ms while
forcing rapid restarts) showed the table flat at exactly the standard
kit plus the one persistent fence read end, no growth, no leak in
either direction; a `grep` for `dup`/`dup2` in `nwsup.c` and `lids.c`
returns nothing, so nothing between `fork()` and `execv()` can defeat
the pipe's `CLOEXEC`-ness; the mechanism uses only kernel-assigned fd
numbers, no literal. One `HYPOTHESIS`, not exercised: a failing
`fcntl(F_SETFL, O_NONBLOCK)` in the parent (immediately after a
successful `pipe2()`, about as close to unfailable as a syscall gets)
would `die()` the supervisor and orphan the pre-exec child — not an fd
leak, and PID 1's ordinary orphan reaping still collects it, so left
as a documented theoretical edge rather than a fix.

**`tcb-review` — one further finding, left as a documented, benign
limitation rather than fixed.** `check_exec_fence()`'s EOF test cannot
distinguish "this generation's `execv()` succeeded" from "this
generation died before ever reaching it" — both close the write end
via ordinary process exit, and the function's own comment already
says so ("execv succeeded, or the child exited without one"). The
practical consequence is narrow and does not reopen anything this
mechanism closes: `do_freeze()`'s `PTRACE_SEIZE` fails with `ESRCH`
against an already-dead pid, so a `FREEZE` racing a die-before-exec
death can only ever read back `"ERR freeze failed"` instead of the
more accurate `"ERR not execed"` — never a false `"OK\n"`, measured by
`tcb-review` hammering the race for ~35,000 attempts and observing
exactly two such replies, neither a seize. Not fixed, because the
correct fix (confirming liveness independently of the pipe) would mean
a second `waitpid()` call site racing the supervisor's own reap loop
in `wait_house()` — a materially larger and riskier change than the
diagnostic-accuracy gap it would close. Left named here rather than
silently accepted.

**`control` — two coverage gaps, both closed.** First,
`test_ctl_freeze_races_natural_exit`'s own assertions (any `OK` or any
`ERR`-prefixed reply passes) are too loose to catch the real bug
`control` went looking for and found: with the per-generation
`ptrace_attached`/`house_frozen` reset removed, a second generation's
`FREEZE` skips `PTRACE_SEIZE` (the flag is stale from the first,
now-dead generation) and fails — a **wrong** failure, not a hang, and
one the flood test's loose assertions let straight through, 6/6 runs.
**Fixed** with `test_ctl_freeze_reattaches_after_restart`, which forces
a genuinely new pid via `STOP`+`START` (the same idiom
`test_ctl_start_relaunch_and_spent` already uses) and asserts the
second generation's `FREEZE` replies exactly `"OK\n"` *and* that
`/proc` actually shows `t` — closing both the loose-assertion gap and
the trust-the-reply gap in one test. Second, of the two `WIFSTOPPED`
guards, only the tier-3 (50ms-poll) one had a test forcing that
specific fallback tier; the signalfd tier's guard had none, because the
only fallback-tier freeze test used `tests/block_both.so.c`, which
blocks pidfd *and* signalfd and always lands on tier 3. **Fixed** with
`test_ctl_freeze_survives_signalfd_fallback`, using
`tests/block_pidfd.so.c` (blocks only `pidfd_open`, confirmed by
`control`'s own `strace` to land on the signalfd tier every time) —
the direct counterpart already established for `STOP` by
`test_ctl_pidfd_fallback_with_socket`.

**One claim in this section was wrong on its own terms, caught by a
second `claims` pass rather than left standing.** `fd-auditor`'s "no
new file descriptor anywhere in the diff" was true of the diff *at the
time it ran* — before the tier-1 restructuring above existed. It is
false of the diff as it stands now, and the two sentences contradicted
each other inside this same note: the "Fixed by a real structural
change" paragraph above already says plainly that `signalfd` is now
created unconditionally, "one extra descriptor per `wait_house()` call
in the common case." **That is the true, current claim; the one
below is what a specific audit found true of an earlier round, and
saying so is the fix, not deleting either sentence.** Worth naming
exactly why the audit's own method didn't catch the later change:
`fd-auditor`'s grep matched `open`/`socket`/`pipe`/`dup`/`accept` as
literal substrings, and `signalfd` contains none of them — the
"word-versus-symbol trap" `plan.md` names for annotations, arriving
here as a grep instead. Re-run today, that same grep is still
methodologically sound for what it checks (no *new* `open`/`socket`/
`pipe`/`dup`/`accept` call exists in the diff) and still simply does
not cover `signalfd`, which is why the fd-descriptor count and the fd
audit's own conclusion must be read as two separate claims rather than
one restating the other.

**Everything else `control`, `tcb-review` and `fd-auditor` checked was
confirmed accurate**: `ptrace()` has no documented
interaction with a traced process's own descriptor table; `nw-sup`'s
own long-lived descriptors (pidfd, ctl listen socket, signalfd) remain
correctly `CLOEXEC` and are unaffected by anything ptrace-related; the
pidfd tier's immunity to ptrace-stops holds and needed no guard (though
`fd-auditor` suggested — a cheap, accepted suggestion, applied above —
documenting *why* as explicitly as the other two tiers already do,
rather than leaving that one tier's correctness resting on an unstated
assumption); `nw-sup` calling `ptrace()` on its own child is unaffected
by the house's own seccomp filter (`lids.c` never installs
`SECCOMP_RET_TRACE`, and `ptrace()` runs in `nw-sup`'s own process
context regardless); the per-generation reset is correctly placed
(`control`'s finding was the missing test, not a misplaced fix); the
`WIFSTOPPED`-without-`WUNTRACED` reporting claim is independently
verified true.

## What this note is not proposing

No plan or blob change, no new lid, no register-level introspection
verb, no external-debugger handoff, no automatic/heuristic freeze of any
kind. `STEP` is named and scoped, not built. Nothing here reopens or
narrows the Liveness refusal in `runtime.md` — see the commitment check
above for why, argued rather than merely asserted.

## What was changed

- `nwsup.c`: `#include <sys/ptrace.h>`; `ptrace_attached`/`house_frozen`
  globals, reset per generation immediately before each `fork()`;
  `is_ptrace_event_stop()`/`forward_if_real_signal()`; `do_freeze()`/
  `do_cont()`; `FREEZE`/`CONT` branches in `handle_ctl_live()` and in
  the separate stopped-state ctl handler; `wait_house()` restructured
  so `signalfd(SIGCHLD)` is created and polled unconditionally,
  alongside `pidfd` when both are available, rather than only as a
  fallback for when `pidfd_open` fails — the tier-1 gap `tcb-review`
  and `test_ctl_freeze_reattaches_after_restart` both found.
- `tests/run.py`: `_pid_state()` helper; new tests —
  `test_ctl_freeze_and_cont`, `test_ctl_freeze_survives_pidfd_fallback`,
  `test_ctl_freeze_survives_signalfd_fallback`,
  `test_ctl_freeze_reattaches_after_restart`,
  `test_ctl_freeze_idempotent_and_cont_without_freeze`,
  `test_ctl_freeze_races_natural_exit`,
  `test_ctl_freeze_not_running_while_stopped`,
  `test_ctl_freeze_cont_then_stop_actually_kills` — all registered in
  `main()`'s list.
- This note.

`control`, `tcb-review` and `fd-auditor` were dispatched in parallel;
every finding above either has a fix and a regression test, or (the
lid-setup race) is recorded as an explicit, named, not-fixed-this-round
limitation rather than silently left. Each fix was self-verified by
mutation before being reported as done: the `WIFSTOPPED`/signal-
forwarding fix and the per-generation reset were each reverted in turn,
shown to fail the test that exists for it, then restored and shown
green again.

## Next step

`make test`, quoted in full, then push.
