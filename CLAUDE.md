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
   **PID 1 mounts nothing** — `grep` for `mount` in `pid1.c` returns one hit
   and it is a comment; there is no `mount(2)` call.
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

   The budget belongs to `nw-sup`, where it is **a counter over a sliding
   window** — `int deaths` in `nwsup.c`, reset when the window expires, so it
   never accumulates unboundedly. Budgets are **never nested**: bug 3 was a
   supervisor giving up, PID 1 restarting it with a fresh budget, and the pair
   looping. One budget authority per unit, and PID 1 is not it.

   *This said "a ring of timestamps, never a counter" until 2026-09-10, in the
   present tense, as an enforced invariant. `grep` for `ring` across the C
   sources returns nothing and never did: the ring was specified in
   `HISTORY.md` §6 and never built. The sliding-window counter has no overflow
   either, so the property was fine and only the description was false — which
   is exactly the failure this file names at the end.*
5. **The init provisions nothing.** Every house gets `/dev/null` on 0 and its
   own log pipe on 1 and 2. `close_others` sweeps the rest. There is no third
   thing, and no mechanism for granting one. (This replaces the pre-2026-09-10
   statement "wiring is non-provision, not enforcement", which concerned
   declared edges. Edges are erased permanently — `HISTORY.md` §17.)

   **A declared bind is not a third thing.** `bind=` makes a path *visible*
   inside a house's brick; the house then opens it itself, with the name it
   would have used anyway, because a bind is the same path inside and out.
   Nothing is handed over. The invariant is about the descriptor table a house
   is born with, and that is still exactly three descriptors.
6. **Lids are the only thing that decides what a house can *do*; a brick
   decides what it can *see*.** Seccomp, Landlock and namespaces are applied
   per unit by `nw-sup` before `execv`. A unit with `brick=` also
   `pivot_root`s into it first, so its `/` is its own tree — its own
   libraries and toolchain, at the same paths, invisible to every other house
   and to the machine. `brick=` forces `NW_LID_NEWNS`; `nwcheck.c` returns
   `NW_E_BRICKNS` otherwise, because pivoting outside a private mount
   namespace repoints the machine's root. Cybersecurity is not a goal;
   containerization applies to apps.

   Order is fixed and is not a style choice: namespaces, then the brick pivot,
   then Landlock, then seccomp. The allow-list has no `mount`, no `unshare`
   and no `pivot_root`, so a house sealed first could not enter its own root.
   There is **one** allow-list and a house does not choose it; the second
   profile that briefly existed is `HISTORY.md` §23.

   **The Landlock lid is for a house in a brick**, and requires one
   (`NW_E_LLBRICK`). It grants read and execute beneath the house's own root
   — which is the brick, since it runs after the pivot — so any linkage works
   without a list of library paths guessed at in the TCB. Nothing grants write
   beneath the root, so a house cannot write into its own brick; declared
   binds get read and write, which makes the bind table the policy input.
   `HISTORY.md` §26.

   **Lids are not advisory.** If a declared lid cannot be applied, that house
   does not start: every lid path in `nwsup.c` ends in `die()`, never in a log
   line and a return. A house that runs unconfined while the plan says it is
   confined is the plan lying, which is worse than a house that does not run,
   and it is the same defect as a brick that roots on the machine while
   logging `lid brick`. The house then burns its restart budget and stays
   down — deliberately, because a do-not-restart signal would be a second
   meaning on the exit-status channel (bug 9). Everything else boots normally;
   nothing a house does halts the city. `HISTORY.md` §25.

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
  `.claude/agents/runtime.md`, which carries the full reasoning. (It was in
  `supervisor.md` until 2026-09-10; that brief merged into `runtime.md`.)
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
  `grep` for `integrity` or `authoritative` across the **C sources** returns
  nothing (it appears in prose, which is why the scope matters),
  and `nw-sup` handles every nonzero exit identically. Both prerequisites are
  what `docs/options/05` Q4 exists to answer. **This becomes enforceable the
  moment storage lands**, and it belongs back in the numbered list on that
  day, not before. It left the list because a list of enforced invariants that
  contains an unenforceable one teaches a reader that the list is decorative.

## Build and test

```
make            # all binaries
make stage      # stages to /tmp/nw-init-run
make test       # stage + install-agents.sh --check + nw-check + tests/run.py
```

`tests/run.py` boots via `unshare --pid --fork --mount-proc` so `nw-root` is
genuine PID 1 and orphan reaping is actually exercised.

## Dispatching agents

`sh install-agents.sh --list` prints this table; it is repeated here because
this file is always loaded and the briefs are not.

**Dispatch before you push, not after.** Both HIGH findings of 2026-09-10 —
the `..` traversal and the profile that killed compilers — were found in code
that was already committed and pushed, because the reviewers ran afterwards.
Same tokens, same findings, different blast radius. `tools/review-gate.sh
--check` makes this mechanical: it fails while a review is owed, keyed to the
*content* of the files under review, so reviewing and then editing does not
count. Record a completed review with `--record <agent>`.

| when | dispatch | why |
|---|---|---|
| a TCB file changed | `tcb-review` + `fd-auditor`, in parallel | read-only, independent, cannot break anything |
| a test was added or changed | `control` | the negative controls, run mechanically instead of by hand |
| a limit or the blob layout changed | `drift` | invariant 3 otherwise depends on someone remembering |
| a brief, this file, or an environment claim changed | `claims` | kind-1 statements rot silently |
| a speed or scale claim was made | `measurement` | never report a single sample |

**Always build the packet first: `sh tools/review-pack.sh`.** It writes a
file and prints the path; the dispatch prompt tells the reviewer to read that
path. Not optional — a reviewer sent to "go and look" spends most of ~90k
tokens rediscovering the repository, and the packet is the diff, the TCB
files it touched and the suite's environment block, already assembled.

Give the reviewer the *path*, never the packet's contents: piping it into the
prompt moves the cost into this context instead of removing it. That is
exactly why the first version of this script went unused the one time there
was an opportunity to use it.

**Every reviewer shares one reporting contract:** a finding carries the
command that shows it and that command's verbatim output, or it is labelled
`HYPOTHESIS`. There was a separate `repro` agent for this and it was never
dispatched once, because the discipline belongs inside the reviewers rather
than beside them.

### Territories are rules, not agents

`plan`, `runtime` and `harness` were dispatchable briefs until 2026-09-10 and
were dispatched **zero times**: the changes here are cross-cutting, so the
authoring happens in the main thread where the whole picture is. Their
content is still load-bearing — the liveness refusal, the seal rules, the
staging trap — so it lives in `.claude/rules/` and `tools/rules-hook.sh`
delivers it on a `PreToolUse` for any file in that territory. Reference read
at the moment it applies, rather than a worker spawned to do the work.

What that leaves is the shape the evidence supports: **read-only reviewers
that fan out, plus rules that arrive when they are relevant.** Reviewers are
the part that pays — they need no shared context and they cannot break
anything.

**Edit a territory file with `Edit` or `Write`, not a Bash heredoc.** The
hook matches `Bash` too and will search a command string for a territory
filename, but that is a backstop and it is guessable-around. The dedicated
tools name the file, so the match is exact.

This is worth a rule because the first version of that hook matched only
`Edit|Write`, worked perfectly, and **never fired once** — nearly every edit
here goes through Bash with a python heredoc, so it never matched. A
mechanism that is correct and routed around is worse than a broken one: it
looks like it is working. The diagnosis was wrong too, and wrongly confident
— "settings load at session start" was inferred from a missing stamp and
reported as the cause without testing it. Hooks and agent definitions both
refresh live; that was checked afterwards, by running a probe.

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

## The characteristic failure

**This project does not produce crashes. It produces a true-looking sentence
sitting next to code that does not do what it says.** Every defect found here
so far has that shape, and knowing the shape is most of the defence.

The record, which is the argument:

- **Bugs 4, 9 and 13** were silent wrong routing. Not one returned an error.
  Descriptors went to the wrong place and every process reported success.
- **D11** was a TERM handler that could never fire, installed on a blocked
  signal, sitting beside a comment saying the supervisor handled TERM. The
  city shut down "gracefully" by timing out into SIGKILL.
- **`supervisor.md`** specified a liveness rule in the present tense. Nothing
  in the tree had ever implemented it.
- **`baker.md`** claimed typing, ordering, cycle detection and capability-flow
  analysis. None had ever existed, and after §17 none were even definable.
- **`lids.c`** stated as fact that its build profile carried "what a compiler
  and a build driver need" and that "without them any compiler dies
  instantly". It killed `gcc` on the first `exec`. `HISTORY.md` §23.
- **Invariant 4 in this file** said the restart budget was a ring of
  timestamps, never a counter. It is a counter. `grep` for `ring` across the C
  sources has always returned nothing.
- **`path_ok_len`** validated a path that could contain `..`, under a comment
  and an invariant both asserting that a house cannot see outside its brick.
  A traversing brick baked clean, passed `nw-check`, booted, and logged
  `lid brick` while rooted on the machine.

Notice what is common. In every case the code was memory-safe, the tests were
green, and the prose was confident. Nothing was reviewing the *relationship*
between the sentence and the behaviour, because reading them together is
exactly the thing that feels like it has already been done.

**So: a sentence describing behaviour is worth nothing without a test that
fails when the behaviour is removed.**

That is the whole rule, and it applies to comments, to briefs, to this file,
and to commit messages. Write the sentence if it helps a reader — but the
sentence is not the evidence. The test that fails without the mechanism is the
evidence, and until it exists, the behaviour is a hypothesis however carefully
it is worded.

The discipline already exists here and should be named as such: the negative
controls. `brick-is-a-root` was believed only after removing `lid_brick()`
made it fail, *and* after keeping the log line while skipping the
`pivot_root` syscall made it fail too — the second control is the one that
matters, because the first would pass against a supervisor that announced the
lid and did nothing. `path-traversal-refused` was believed only after deleting
the component check made it fail. Do that every time. A test that has never
been seen failing is a test that has never been tested.

Two corollaries worth stating, because both have been got wrong:

- **A green suite is evidence only against a stated environment.** The
  Landlock lid never worked, in any environment, for its whole life: every
  machine it ran on lacked Landlock, so it took an early return and the suite
  printed green. `tests/run.py` prints `print_environment()` before the first
  test and refuses to print `ALL TESTS PASSED` when anything was skipped.
  **Report that block whenever you report a suite result** — and never report
  a green line as evidence about a feature the machine cannot execute.
- **A control that passes is not good news.** It means the test is bad, or the
  control is. The first control on `brick-is-a-root` passed because the suite
  runs staged binaries and `make` alone had not restaged — the harness was
  lying, and the reading "it works" was available and wrong.
- **Green does not mean covered.** A test can pin the conjunction of two
  guards while pinning neither. `fds_ge3=0` inside a brick stays green if
  `O_CLOEXEC` is dropped and stays green if the `close()` calls are dropped;
  only removing both fails it. Ask what single change would still leave it
  passing.

## The rule that matters most

Every bug found in this codebase so far was found by running, and none by
reading. Do not report a change as working until it has been built and run.
Prefer designing the problem out over checking for it: no ordering list to get
wrong, no counter to overflow, no second limit to drift, no channel to
impersonate, no path for a filesystem to reinterpret.
