# 11 — The start/stop channel

Status: **design only. No code this round**, per instruction. This note
answers the questions that were asked and states, rather than resolves,
the ones that were explicitly reserved for the operator.

## Given constraints, restated rather than re-derived

Start/stop only, only for houses the plan already permits to run at all.
Nothing here touches a plan or a slot. Access is granted the same way
everything else in this tree is granted visibility — `bind=`, to the
houses the plan names. A socket is acceptable *because* it changes what is
**running**, never what is **possible** — the plan already decided what
each house may do; this channel only decides whether it is doing it right
now. Shaped around one use case (driftwm's suspend/relaunch), not a
general control plane. Ordering dependencies are out of scope — a future
checkable plan field ("NWPLAN10"), not spawn-order tricks or retry loops
here.

## Which process owns the socket, and why

**`nw-sup`, one socket per unit.** Not PID 1:

- Invariant 1 is the whole argument on its own: "no allocation, no
  parsing, no recursion after start in PID 1." A start/stop protocol is a
  request to parse, however small — even a fixed two-word command is
  parsing PID 1 does not do today and the invariant says it must not grow.
  Putting the channel in PID 1 is exactly the shape invariant 1 exists to
  forbid, regardless of how minimal the format is.
- "One authority per unit" (invariant 4, restart budgets) is the
  second reason, independent of the first: `nw-sup` already is that
  authority for its unit — it owns the budget, the lids, and the child
  pid. A start/stop command is a third thing that authority already needs
  to arbitrate against restarts, so putting it anywhere else would be a
  second authority making decisions about the same child, which is the
  exact shape invariant 4 calls out by name ("budgets are never nested").
- `nw-sup` never unshares its **own** mount namespace — `lid_newns()` is
  called only inside the forked house's branch (`if (p == 0) { ... if
  (lids & NW_LID_NEWNS) lid_newns(); ... }` in `nwsup.c`). So `nw-sup`
  itself always lives in the shared, machine-root namespace — the same
  namespace `bind=`'s source paths are resolved in. A socket `nw-sup`
  creates there is reachable by another house's `bind=` the same way
  `/etc` or a device node is; a socket created *inside* a house's own
  brick/namespace would not be, since nothing else can see into a
  house's private mount namespace at all. This is why the socket has to
  be `nw-sup`'s, not the house's.

**One socket per unit, path derived from the unit's declared name** (the
same "declared id, never the house name, so a rename can't orphan it"
argument `runtime.md` makes for `NW_LAYER_DIR` — `NW_BRICK_DIR` is keyed
differently, by the content hash of the image, which is a
content-addressing rationale rather than this one, so it is not cited
here), e.g. `NW_CTL_DIR "/" <unit-name> ".sock"`. Created unconditionally
by that
unit's `nw-sup` — not gated on any new plan field, per the "nothing
touches plans" constraint. This is the same shape as the three fixed
descriptors invariant 5 already gives every house unconditionally: a
baseline supervisor behaviour, not a declared field, so it needs no row in
the mechanism-rule table any more than the log pipes do.

**The socket file is `unlink()`ed on every normal terminal exit path,
and not on a `die()`.** `tcb-review` swept every `die()` inside `nw-sup`
after the control socket is created and found none of them unlink it —
the same accumulation class `harness.md` already documents for
`NW_BRICK_DIR` and `NW_LAYER_DIR` images, not a new one. Harmless in
the sense that matters: the next start of the same unit `unlink()`s the
path before binding, so a stale file never wedges a boot. Left as an
accepted accumulation rather than fixed, for the same reason the brick
and layer cases are.

### A boundary this design runs into and does not get to decide alone

Making `nw-sup` able to *accept a connection while also waiting for its
child* means its main loop can no longer be the blocking
`waitpid(p, &st, 0)` it is today (`nwsup.c`, the unit-supervision loop).
It needs to multiplex a `SIGCHLD` signal (via `signalfd`, so it can sit in
the same `poll()` as the socket — ordinary, not a guessed timeout: `poll()`
with no deadline over two fds is an event multiplex, not freeze detection,
and does not reopen the Liveness refusal in `runtime.md`, which is about
guessing *how long is too long*, not about *waiting for more than one kind
of event*) against the control socket. That is a real change to the shape
of `nw-sup`'s per-unit loop.

**`CLAUDE.md`'s "Who owns which file" assigns exactly that loop to
Grok** — "Grok owns `pid1.c`, `dawn.c` and **the restart loop in
`nwsup.c`**." The operator's instruction to write a brief for Grok named
`pid1.c`/`dawn.c` explicitly; this note is flagging that the channel's
core mechanism reaches into `nwsup.c`'s restart loop too, which is Grok's
by the standing ownership table even though it was not named in that
sentence. **If this is built, the restart-loop restructuring is a Grok
brief, in addition to anything touching `pid1.c` or `dawn.c` — of which
there is none identified so far,** since nothing here needs PID 1 or
`dawn` to learn anything new. Whoever picks this up should confirm that
before starting, rather than inheriting this note's belief about it
unchecked.

## Wire format

One Unix domain stream socket per unit, at that unit's fixed, name-derived
path. **No unit name in the request** — the socket's own path already
identifies which unit, so there is nothing to parse beyond recognising one
of two fixed strings:

- Request: exactly `"START\n"` or `"STOP\n"`, one per connection, and
  nothing else is a request — no length-prefixed frame, no key=value
  syntax, no room for a second command to be added by extending the parser
  later without this note being revisited. Minimal on purpose: the whole
  argument for putting this in `nw-sup` rather than PID 1 was that even a
  small amount of parsing has to happen *somewhere*, and "somewhere" should
  not grow past what one specific use case needs.
- Response: exactly one line back over the same connection, then close.
  `"OK\n"` on success, `"ERR <reason>\n"` on refusal — synchronous, so the
  caller learns the result without a second channel or a poll of its own.

**HYPOTHESIS, not demonstrated: the implementation reads the request
with one `read(2)` and no reassembly loop.** `tcb-review` flagged that
POSIX does not guarantee an `AF_UNIX SOCK_STREAM` write this small
arrives at the reader in a single `read()`, so a request split across
two writes on the caller's side would come back short and be refused
as malformed rather than honoured. Not exercised by anything in the
suite (a single `sendall()` of five or six bytes is effectively always
one syscall in practice), so this is a latent protocol gap rather than
a failing test — recorded here per the project's own rule that a
hypothesis is labelled as one, not silently treated as covered.

## Authorization

**Solely by reachability of the socket, via `bind=`.** No credential check
inside the protocol (no `SO_PEERCRED` inspection, no token, no per-caller
allow-list) — that is what "a socket is acceptable because it changes what
is running, never what is possible" buys: whoever can reach the socket can
start or stop the unit, but cannot make it do anything the plan did not
already permit it to do at all. Adding a caller allow-list would need the
plan to declare who may call whom, which is exactly the "nothing touches
plans" constraint refusing itself back into existence — if that
granularity turns out to matter, it is a plan-format change and belongs in
NWPLAN10, not smuggled into this channel as a second, undeclared
authorization layer.

**This inherits an existing gap rather than opening a new one, and that
should be said rather than left implicit.** A brickless house
(`lids=none` or without `NW_LID_NEWNS`) already shares the machine root
outright — invariant 5 names this ("those verbs act on the CITY's [not
its own]"; what follows from a house's privilege level is invariant 6's
subject, not this fact's) — so a brickless house can reach *any* unit's
control socket under `NW_CTL_DIR` without needing a `bind=` at all, the
same way it can already reach anything else on the shared root. That is
not a hole this design cuts; it is the same hole invariant 5 already
documents for every
brickless house, restated here because this is the first feature whose
entire access-control story rests on namespace visibility mattering.

## Refusals, each paired with the test that pins it

- **Malformed request** (anything other than exactly `"START\n"` or
  `"STOP\n"`) → `"ERR bad request\n"`, connection closed, unit state
  unchanged. Test: send garbage, assert the exact refusal string and
  assert the unit's running/stopped state and death count are unchanged
  afterward — the state assertion is the paired positive; a refusal
  string alone would also be satisfied by a channel that refuses
  everything.
- **`STOP` on an already-stopped unit** → idempotent, `"OK\n"`, no second
  signal sent, no death counted. Test: stop twice, assert the second is a
  no-op against the running process table and the death counter, not only
  that it returns `OK`.
- **`START` on an already-running unit** → idempotent, `"OK\n"`, no second
  fork. Test: start twice, assert only one child pid exists throughout.
- **`START` on a unit whose budget is exhausted** — **undecided, reserved
  for the operator** (see below). Whatever is decided needs a paired test
  either way: if refused, assert the refusal and that no fork happens; if
  granted, assert the death counter's behaviour on the next natural
  crash.
- **A `STOP` request and an unrelated crash arriving "at the same time"**
  — `nw-sup`'s loop processes one readiness event per iteration, so the
  internal state is never torn: exactly one of the `stop_requested` path
  or the ordinary death/restart-accounting path runs, never both, never
  neither. That much is deterministic and is what `test_ctl_stop_does_not_count`
  pins. **"There is no window where both are true at once" overstated
  what that buys, and is corrected here rather than left standing** —
  `tcb-review` reproduced a real, sub-millisecond window with an
  external `SIGKILL` timed against a `STOP`: at zero delay the unrelated
  death is silently reclassified as "stopped" (no `restart`/`spent`
  line, budget untouched); the ordering the loop picks is whichever
  readiness bit `poll()` reports first, not whichever event genuinely
  happened first in real time, and `poll()` cannot distinguish those
  when both arrive in the same call. The window is real and narrow, not
  absent.

## A defect this design did not name: cross-generation misdirection

Found by `tcb-review` while checking the paragraph above, and not
covered by any test in the suite. The wire protocol names a *unit*
(`NW_CTL_DIR/<name>.sock`), never a process incarnation, so a `STOP`
that arrives while the unit is already mid-restart from an unrelated
death can sit unaccepted in the kernel backlog and then get serviced
against the **new**, freshly-forked generation the moment `nw-sup`
calls `wait_house()` for it — reproduced: the original death is
correctly logged (`restart … death=…`), the `STOP` connection is
accepted afterward, and the second generation is killed via the
`stop_requested` path, which by design logs nothing and does not touch
the budget. From the caller's side: one `STOP` sent, one `OK` received.
From the plan's side: a brand-new, healthy instance of the unit died
within about a second of being forked, for a reason nothing in the log
records.

It is a consequence of the socket being keyed by unit rather than by
incarnation, which is deliberate (the wire protocol carries no
identifier for either, per this doc's own minimalism), and there is no
way to close it without a protocol change: a generation token in the
wire format, checked by the caller or the channel, is a design
decision and not a one-line fix.

**Decided: refused for now, not waiting on anything.** A narrow,
sub-millisecond race window against an unrelated crash, not a security
hole — the channel's authorization story (reachability via `bind=`)
is untouched by it either way. Building a generation token now would
be protocol complexity spent closing a race that may never fire in
practice; better to run the channel for a while and see whether it
actually does before paying that cost. Revisit if it does — landing a
fix at that point needs a paired test the way every other refusal in
this file does: a legal `STOP` that must be accepted, and a stale one
that must be refused for naming the wrong generation.

## Reserved for the operator — not decided here

Restated exactly, not resolved:

- **Does a `STOP` count as a death against the hard-total restart
  budget** (invariant 4)? The channel can tell "requested" apart from
  "unrequested" internally (previous section), so either answer is
  buildable; which one is *correct* is a policy call this note does not
  make.
- **Can `START` revive a unit whose budget is already spent?** If yes,
  the budget stops being a hard total in the sense invariant 4 currently
  states it ("never reset") — this channel would be a second way to reset
  it, which is exactly the shape invariant 4 warns against for a
  *different* reason (nested budgets, bug 3). If no, a deliberately
  stopped unit that has also exhausted its budget is gone for the rest of
  the boot regardless of the stop, which may or may not be the intended
  reading of "stopped."
- **What happens to a unit's layer on `STOP` versus on death?** Nothing
  in the current design touches `/nw/layers/<id>` either way — a stopped
  unit's writable layer sits exactly where a normally-exited one's would.
  Whether `STOP` should behave differently (e.g. because it is expected to
  be followed by a `START` shortly, versus a death which may not be) is a
  product decision about driftwm's suspend/relaunch behaviour, not a
  mechanical consequence of anything above.

## What this note is not proposing

No ordering field, no dependency graph, no retry loop, no generic RPC
surface, no caller allow-list beyond `bind=` visibility. All four are
either refused elsewhere in this tree already (cycle detection, ordering —
`plan.md`'s *Refused deliberately*) or explicitly deferred to a future
plan-format change this note is told not to make.

## Next step

A `claims` review of this note, then report to the operator with the three
reserved questions above and the Grok-ownership finding — nothing is built
this round.
