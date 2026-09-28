# QUEUE — current truth only

This file is current-state, not a log. When a stage lands or a decision
changes, this file is edited in the same commit as the change; stale
entries are deleted, not appended around. Git history is the log.

If this file disagrees with the repo, the repo wins, and whoever notices
fixes this file.

Base at last edit: `446b2ba` (origin/main).

## Decision authority (Part C, standing)

Two authorities, split by kind, not by file:

1. **Decided alone, recorded here**: names, wording, refusal texts, test
   design, mutation choices, file placement, ordering, internal structure.
2. **Stop and ask the operator, ONLY for**: a plan-format change (a field,
   `NW_MAGIC`); reversing a refusal or a stated commitment; any of PID 1's
   verbs; a new or widened permission or lid; an ownership conflict
   (`pid1.c`/`dawn.c` are Grok's; `plan.als`/`Plan.tla` beyond the
   authorized fd-need edit are frozen absent direct instruction); contradicting
   an operator decision; a flaky test (diagnose, never add a retry).
   Batch these one message per stage; keep working on unblocked items
   while waiting. (This item's list is a paraphrase of a standing
   operator instruction that is not itself in this tree; no enumerated
   "commitments" or "verbs" list exists here to check it against, so no
   count is given for either — see CLAUDE.md's own rule against a count
   with nothing to check it against.)

Review by risk (not by directory): trusted core (`nwsup.c`, `nwspawn.c`,
`nwcheck.c`, `blob.h`, the plan format, anything deciding what boots or
what a house may do — CLAUDE.md's own TCB table is the exhaustive list;
this parenthetical is not one) gets the full set — `control` + `tcb-review`
+ `fd-auditor` when fds/mounts/cgroups are touched + `claims` on prose.
This narrows CLAUDE.md's own dispatch table, which pairs `tcb-review` and
`fd-auditor` unconditionally on any TCB change; the narrowing is the
standing operator instruction (Amendment 2, Part C.3), not a decision
made here, and practice so far has dispatched both together regardless —
`de8a5e8` and `446b2ba` both did, whether or not the change was fd-shaped.
`bakery/`/`tools/`/test-only gets `control` + one reviewer + `claims` on
docs. Docs-only gets one `claims` pass. Every push needs a full
`make test` and an `ls-remote` verification, regardless of tier.

Low on usage: push to `wip/<stage>` with a `tools/HANDOFF-<stage>.md`,
never to `main`, still verified via `ls-remote`.

## Stage 0 — DONE, landed on `main`

1. Edge fd-need term in `plan.als`/`Plan.tla`, oracle cross-check against
   `blob.h` — `de8a5e8`.
2. `nw-spawn`'s fd-ordering discipline (`pack_kit()`), pinned as far as it
   can be, with the real collision axis `tcb-review` found (a wire's own
   source colliding with an earlier wire's target) resolved by a
   reachability proof against `main()`'s edge-creation loop, not by a
   test — `446b2ba`.
3. `scx_simple` GitHub Actions continuation: libbpf/bpftool version skew
   diagnosed precisely (skeleton generator v1.8 vs link-time headers
   1.3.0), not fixed within the five-iteration budget; recorded honestly
   as "attempted, not closed"; workflow file deleted per the "not kept"
   convention — `ff5fc9c` (landed before this file existed; confirmed via
   `git log --all` as an ancestor of `446b2ba`).

Nothing outstanding from Stage 0. The reject-path sched_ext mechanism
that item 3's own investigation sits beside (`docs/options/15`) is
likewise landed and reviewed; its accept path stays kind 3 (no working
policy artifact yet), which is `docs/options/15`'s own conclusion, not a
gap this queue tracks separately.

## Stage 1 — pure-core refactor. NOT STARTED.

Status: waiting on the operator's brief. The original multi-stage message
that defined "Stage 1: pure-core refactor" (with a larger set of
state-table rules, reduced by Amendment 1 to the three named below) was
referenced but its full text was not resent after the amendments landed;
no enumerated original list is in this tree to check a count against, so
none is given. Per the standing rule — "If a stage refers to a brief you
don't have the full text of, stop and ask the operator to resend it. Do
not reconstruct it." — this stage does not start until that text is back
in hand.

**Open question, recommended default**: resend the original Stage 1
brief verbatim, or confirm the three reduced state-machine decisions
(shutdown-while-frozen → kill; grace-expiry-while-frozen → kill;
START-on-frozen-house → no-op) are the entire scope now. Recommended:
the operator resends the brief; reconstructing seven-reduced-to-three
decisions from memory of an amendment is exactly the kind of thing this
project's own record shows going wrong quietly.

## Stage 2 — cgroups minus PAUSE/RESUME. NOT STARTED. Depends on Stage 1.

Scope per Amendment 1: placement, limits and read-back, whole-house kill,
death autopsy. PAUSE/RESUME (cgroup freeze) and group pause are removed
from scope permanently, superseded by the LOCK/UNLOCK feature below.

## LOCK/UNLOCK design note — NOT STARTED. Runs after Stage 2, before Stage 3.

Target: `docs/options/22-lock-unlock-houses.md`. Design note only, no
code; one `claims` pass before landing; does not wait for operator
sign-off unless a finding changes the semantics below.

**Semantics, as specified by the operator (Amendment 1), recorded here
so Stage 3's format bump can implement them without re-deriving them:**

- One plan field per house: `lids=`-shaped, `lock=locked|unlocked`.
  UNSET means `locked` (today's behavior, unchanged).
- **LOCKED**: boot-started; restarted after death under a hard-total
  budget, exactly like every house today (compositor, seat-manager,
  audio, console are the named examples).
- **UNLOCKED**: NOT boot-started. The supervisor starts idle, holding
  the control socket (today's existing "stopped" state). `START` launches
  it. ANY exit — clean or crashed — returns it to idle: no restart, no
  budget consumed. A crash writes a crash record; a clean exit (status 0)
  writes none. Relaunches are unlimited (no budget applies to an
  unlocked house at all). `STOP` closes a running unlocked house and
  never counts against anything.
- Declaring both `unlocked` and a restart budget is a contradiction,
  refused at bake time AND at boot (nwcheck.c), matching every other
  cross-field rule in `plan.md`'s Hard rules.
- A minimal `nwctl` (list / start / stop, a small STATUS reply) ships
  inside the compositor's brick, reaching the control socket via a
  read-only `/nw/ctl` bind. Example launcher entry: `nwctl start
  <house>`. The compositor and the console stay real-root (no brick
  seal on those two), unchanged from today.

**The questions the note must answer, each against the CURRENT
code with a file:line citation, and each stating plainly what could not
be verified rather than guessing:**

1. Mount lifetime for an unlocked house: per-`START` or once at boot?
   Loop-device `AUTOCLEAR` interaction? A concrete plan for measuring
   leaks across 50 launch/quit cycles (loop devices, mounts, cgroup
   dirs, fds, zombies).
2. Output tail across runs: how a crash record avoids reading stale
   output from a PREVIOUS run of the same unlocked house — mark run
   boundaries in the log, or something else.
3. Edges and unlocked houses: recommend refusing a declared edge on an
   unlocked house (an edge's socketpair is created once, at boot, before
   any house exists to hold it — an unlocked house that has not started
   yet has nothing to inherit it), name the exact refusal text and where
   it is checked (baker and `nwcheck.c`, matching every other structural
   rule).
4. Every contradictory declaration to refuse at bake AND boot, not only
   `unlocked` + budget — read the full cross-field-rule list in
   `plan.md` and check each field's interaction with `lock=`.
5. Encoding: a spare byte, precedent is `sched_ext`'s NWPLAN09→NWPLAN10
   bump (`docs/options/15`) — `locked=0` so an old blob's zero byte
   still means today's behavior, fits in the single format bump Stage 3
   is scoped to.
6. Idle-state cost (a supervisor holding a socket, doing nothing —
   memory/fd footprint), the STATUS reply's exact format, and behavior
   of STOP / FREEZE-CONT / TERM / START-on-an-already-running-house.
7. Interplay with `tools/relaunch-house.py` and any profile's existing
   RUN/STOP verbs — does `lock=` change what those tools may assume.
8. The launch path in full: `nwctl` inside the compositor's brick, its
   `PATH`, and — using ONLY mechanisms that already exist in this tree
   today — what a launched game house actually needs to run (a bind, a
   brick, an edge if wired to something). Flag gaps; do not design new
   mechanisms to fill them.
9. Tests, red then green, including the 50-cycle leak check from
   question 1 and a locked-house regression (today's boot-and-restart
   behavior must be provably unchanged for every `lock=locked` house,
   including the implicit-default case).

## Stage 3 — the plan-format bump. NOT STARTED. Depends on the LOCK/UNLOCK note.

ONE format bump carrying every field accumulated since NWPLAN10:
`lock=`, `stop-grace` (if the LOCK/UNLOCK note's answer to question 6
needs one), and anything else queued by that point. Not a bump per
field — the whole reason this stage exists after the design note rather
than interleaved with it.

## Stage 4 — design notes for operator sign-off. NOT STARTED. Depends on Stage 3.

Notes, none of them code:
- `docs/options/20` — grants/schema.
- `docs/options/21` — output-ownership, with a `pid1.c`-touching brief
  written for Grok specifically (ownership: Grok owns `pid1.c`).
- `docs/options/23` — erofs file-backed bricks. Per the operator's own
  framing this splits off content from an earlier "asleep houses" note;
  no note by that name exists anywhere in this tree's history
  (`git log --all --diff-filter=A -- "*asleep*"` returns nothing), so
  that provenance is the operator's account, not something this tree
  confirms, and `docs/options/23` should be written from LOCK/UNLOCK's
  own content rather than assumed to be extracting from a file that
  turns out not to exist.

## Open questions carried forward

- Stage 1's brief text (see above) — blocking Stage 1's start.
- Nothing else is currently blocked; every other open question lives
  inside the stage it belongs to, above, with its own recommended
  default.
