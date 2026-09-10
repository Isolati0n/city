# nw-init — working rules

Init system for Nexusweave. Linux kernel underneath. A plan is validated
offline, then a runtime table interpreter executes it. Robustness comes from
*where code lives*, not from how much code exists.

## TCB boundary — memorise this

| component | file(s) | language | in TCB |
|---|---|---|---|
| `dawn` (mount stage) | `dawn.c` | C | **yes** |
| `nw-root` (PID 1) | `pid1.c` + `nwcheck.c` | C | **yes** |
| `nw-spawn` (boot spawner) | `nwspawn.c` + `nwcheck.c` | C | **yes** |
| `nw-check` | `nwcheck_main.c` + `nwcheck.c` | C | **yes** |
| `nw-sup` | `nwsup.c` + `lids.c` | C | **yes** |
| `nw-rescue` | `rescue.c` | C | yes |
| baker (`nw-cc`) | `bakery/nw-cc.py` | Python | **no** |
| test suite | `tests/run.py` | Python | **no** |
| spec | `plan.als`, `Plan.tla` | Alloy / TLA+ | **no** |

Anything added to a TCB file needs a stated justification. Anything that can
live in the baker instead, does.

## Invariants that must not be broken

Everything in this list is **enforced now** — kind 1 under *How briefs are
written* below — and each is checkable against the code as it stands. Rules
that are refused, or that are waiting on a prerequisite, are in the two
sections after this one and are deliberately not numbered here.

1. **No allocation, no parsing, no recursion after start in PID 1.** The blob
   is already validated; PID 1 reads a table, it does not interpret text.
   **PID 1 mounts nothing** — `grep` for `mount` in `pid1.c` returns zero.
   `dawn` mounts and hands PID 1 a path; nothing in the TCB below `dawn`
   learns what a filesystem is. The one text PID 1 reads is
   `<slots>/current`, at boot, bounded to `NW_NAME_LEN` and validated to
   `[A-Za-z0-9_-]` so it cannot escape the slots directory.
   **PID 1 has no restart budget and must not grow one** — `grep` for
   `budget`, `restart` or `respawn` in `pid1.c` returns nothing. Budgets live
   in `nw-sup`; see invariant 4.
2. **No compile-time file descriptor numbers alongside dynamic allocation.**
   This produced bugs 5, 9 and 13. Sweep `/proc/self/fd`; do not hardcode.
3. **Limits are derived, never declared twice.** `NW_MAX_UNITS` feeds the
   `_Static_assert` fd budget in `blob.h`. The same arithmetic appears in
   `bakery/nw-cc.py`, `plan.als` (`fdNeed`) and `Plan.tla` (`FdNeed`). Change
   one, change all four, or they drift.
4. **`nw-spawn` exits; its death is not a failure mode.** It forks one
   supervisor per unit, double-forked so PID 1 adopts the houses, reports the
   pids and exits 0. PID 1 requires a complete report *and* a clean exit —
   successful termination is the completion signal, not something to watch
   for. Spawning is boot-time only: PID 1 has no respawn path and restart
   budgets live in `nw-sup`. Do not give the spawner a mid-life.

   The budget belongs to `nw-sup` and obeys two rules there: it is a **ring of
   timestamps, never a counter**, because there is nothing to overflow; and
   budgets are **never nested** — bug 3 was a supervisor giving up, PID 1
   restarting it with a fresh budget, and the pair looping. One budget
   authority per unit.
5. **The init provisions nothing.** Every house gets `/dev/null` on 0 and its
   own log pipe on 1 and 2. `close_others` sweeps the rest. There is no third
   thing, and no mechanism for granting one. (This replaces the pre-2026-09-10
   statement "wiring is non-provision, not enforcement", which concerned
   declared edges. Edges are erased permanently — `HISTORY.md` §17.)
6. **Lids are the only thing that decides what a house can touch.** Seccomp,
   Landlock and namespaces are applied per unit by `nw-sup` before `execv`.
   Cybersecurity is not a goal; containerization applies to apps.

   The honest consequence, recorded because it is load-bearing: **reachability
   has moved out of the sealed plan and into the lid set.** A `lids=none` house
   can open its own socket — nothing structural stops it. `__NR_socket` is
   absent from the `lids.c` allow-list, so a `lids=seccomp` house is killed for
   trying, and that is a live test. The plan no longer says what a house can
   reach; only its lids do.
7. **The live city does not grow verbs.** A new plan is a new slot (A/B), never
   an in-place rewrite. This is now an operational rule only: `NoLiveRewrite`
   was withdrawn from `Plan.tla` when edges were removed, because the
   remaining variables have no runtime mutation path and the predicate would
   have been vacuous. See `HISTORY.md` §17.
8. **CRC32 is diagnostic** (threat model is corruption, not tampering). The
   structural checks in `nwcheck.c` are the actual safety property. The seal
   must be *verified*, not merely read — that was bug 1.

*Renumbered 2026-09-10: the old invariant 7 left the enforced list (see
Waiting on a prerequisite, below), so old 8 → 7 and old 9 → 8. Invariants 1–6
kept their numbers. A pre-2026-09-10 reference to "invariant 8" means the
live-city rule, now 7; there were no references to old 9.*

## Refused deliberately

Statements here are decisions not to build something. They are not gaps and
not backlog. Reopening one is a design decision, and the reasoning is recorded
where the work would land so it cannot be undone by someone being helpful.

- **Freeze detection.** A house that goes silent but never exits is undetected
  by anything. Every form of detection needs a guessed constant, and the rule
  was attempted and wrong three times. See the Liveness section of
  `.claude/agents/supervisor.md`, which carries the full reasoning.
- **Nothing a house does halts the city.** Exactly two things halt it: the
  plan fails validation at boot, or PID 1 dies. The `critical` flag was
  removed rather than repaired — `HISTORY.md` §19.

## Waiting on a prerequisite

Statements here are real rules with no subject yet. They become enforceable
the moment the prerequisite lands, and they are recorded so nobody has to
rediscover them.

- **Authoritative state never auto-restarts on an integrity fault.** Inherited
  from `HISTORY.md` §11 and still correct. It has no subject today: there is
  no persistent state anywhere in the design, and no integrity-fault channel —
  `grep` for `integrity` or `authoritative` across the tree returns nothing,
  and `nw-sup` handles every nonzero exit identically. Both prerequisites are
  what `docs/options/05` Q4 exists to answer. **This becomes enforceable the
  moment storage lands**, and it belongs back in the numbered list on that
  day, not before. It left the list because a list of enforced invariants that
  contains an unenforceable one teaches a reader that the list is decorative.

## Build and test

```
make            # all binaries
make stage      # stages to /tmp/nw-init-run
make test       # stage + nw-check on the blob + python3 tests/run.py
```

`tests/run.py` boots via `unshare --pid --fork --mount-proc` so `nw-root` is
genuine PID 1 and orphan reaping is actually exercised.

## How briefs are written

`CLAUDE.md` and the agent briefs in `.claude/agents/` are read by agents that
**act on them**, not by people who can tell aspiration from fact. Nearly every
defect found in the 2026-09-10 brief audit came from one cause: a statement
that read as fact was actually a refusal or a plan, and nothing marked which.
Liveness, scale testing and the integrity-fault rule were all found this way,
each separately, each costing a round trip.

So every statement in a brief is exactly one of three kinds, and it must be
written so a reader can tell which without checking:

1. **Enforced now.** The code does this today. **A statement of this kind must
   be checkable against the code as it stands** — if you cannot point at the
   file and line that makes it true, it is not this kind. Write it in the
   present tense and expect it to be verified.
2. **Refused deliberately.** A decision not to build something. State the
   refusal, the reasoning, and that reopening it is a design decision rather
   than an implementation task. Do not delete these: a reader who finds no
   mention will assume nobody considered it and propose building it.
3. **Waiting on a prerequisite.** A real rule with no subject yet. Name the
   prerequisite and where the decision lives. Keep it out of any list of
   things that are enforced.

Two habits follow from this:

- **Never put a count in a brief.** Not the number of checks, tests, difftest
  inputs, or bugs. A count is a hostage to the next commit and has been wrong
  three separate times in one day. Point at the code or quote the suite's own
  output. A bug *identifier* (bug 5, bug 13) is a name, not a count, and is
  fine. The one place a count belongs is inside a test assertion, where being
  wrong makes something fail instead of quietly misleading a reader.
- **When something is removed, re-file the rule rather than deleting it.**
  Move it to kind 2 or kind 3 with the reasoning intact. A rule deleted is a
  rule someone re-derives badly later.

## The rule that matters most

Every bug found in this codebase so far was found by running, and none by
reading. Do not report a change as working until it has been built and run. Prefer designing
the problem out over checking for it: no ordering list to get wrong, no counter
to overflow, no second limit to drift, no channel to impersonate.
