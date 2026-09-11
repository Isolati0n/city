# runtime — territory rules

<!-- nw-init:install-agents v1 -->
**Not an agent.** This was a dispatchable brief until 2026-09-10 and was
never dispatched once. Its content is reference read at the moment it
applies, so `tools/rules-hook.sh` delivers it on a `PreToolUse` for any
file in this territory. Scope: Owns the boot chain and per-unit execution — dawn.c, pid1.c, nwspawn.c, nwsup.c and lids.c. Use for mount and pivot, boot sequence, forking and reaping, shutdown ordering, restart budgets, namespaces, seccomp, Landlock, bricks and binds, and exec of a house. NOT for liveness or freeze detection: there is none, deliberately — read the Liveness section before proposing any.

You own the chain that turns a validated blob into running houses:
`dawn.c` → `pid1.c` → `nwspawn.c` → `nwsup.c` (+ `lids.c`) → the
house. All TCB. A fault here does not crash a program, it fails to boot a
machine.

## Why this is one territory and not two

It was two — a PID 1 agent and a supervisor agent — until 2026-09-10, and
**D11 is the argument against that split.** `nw-spawn` blocked every signal
before its first fork; a signal mask survives both fork and exec; so
`nw-sup` and every house started fully masked, every TERM handler was dead
code, and the symptom appeared somewhere else again — in `pid1.c`'s
shutdown, which sent TERM, got no answer, and expired into SIGKILL. One
cause, three files, and it is invisible to anyone holding one of them.

State flows *down* this chain — mount namespace, signal mask, descriptors,
environment — so a change to any link is a change to everything below it.

## What each stage may and may not do

- **`dawn`** mounts and pivots, then execs `nw-root` with a path. It is
  the only place in the TCB that knows what a filesystem is. Configuration
  comes from the environment (the bootloader supplies it via the kernel
  command line); nothing is defaulted, because a boot that does not say what
  to mount should fail loudly rather than guess at hardware. Strict mounts
  for the root and the ESP (ours to make, failure is fatal); ensure-mounts
  for `/dev`, `/proc`, `/sys` and cgroup2, where `EBUSY` means the
  requirement is already met.
- **PID 1 mounts nothing, and must keep mounting nothing.** `grep` for
  `mount` in `pid1.c` returns only comments — it said "one hit" until
  2026-09-11, when there were two. It cannot
  mount the thing it needs in order to learn what to mount; the alternative
  is a device name compiled into the trusted core, which is the
  fixed-descriptor-number class in a new costume.
- **PID 1 has no restart budget and must not grow one.** `grep` for
  `budget`, `restart` or `respawn` in `pid1.c` returns nothing.
- **`nw-spawn` exits, and that is success**, not something to watch for.
  Require a complete pid report *and* `WIFEXITED` with status 0. Its
  predecessor was fatal on death because it held the only copy of the
  connection graph; with edges gone there is no graph and no mid-life. Do not
  give the spawner one.
- **`nw-sup` owns the budget and the lids**, one authority per unit.

## Hard rules

- **No allocation, no parsing, no recursion after start in PID 1.** The one
  text it reads is `<slots>/current`, at boot, bounded to `NW_NAME_LEN`
  and validated to `[A-Za-z0-9_-]` so it cannot escape the slots directory.
- **The budget is a hard total of deaths for the supervisor's life** —
  `int deaths` in `nwsup.c`, compared against `budget`, never reset —
  and **budgets are never nested.**

  *This said "a ring of timestamps, never a counter" until 2026-09-11,
  and `CLAUDE.md` had already retracted that sentence twice while this
  copy stayed. There has never been a ring. The sliding window that
  replaced it in the telling was worse than a wrong description: a reset
  made the budget unbounded, which is D18. This file is what
  `tools/rules-hook.sh` hands an agent the moment it edits `nwsup.c`, so
  a stale rule here is delivered straight into the work. `drift` and
  `fd-auditor` both found it.* Bug 3 was a supervisor
  giving up, PID 1 restarting it with a fresh budget, and the pair looping.
- **No compile-time descriptor numbers alongside dynamic allocation.** Bugs
  5, 9 and 13 were one mistake three times, and none of them produced an
  error — they produced silently wrong routing. Sweep `/proc/self/fd`.
- **One seccomp table.** `nwsup.c` calls `nw_apply_house_seccomp()` in
  `lids.c`; it once carried a verbatim second copy. There is one allow-list,
  `strict_allow[]`, and a house does not choose it. *This described
  `NW_PROF_BUILD` as "assembled as `NW_PROF_STRICT` plus `build_extra[]`
  at filter-build time" until 2026-09-11; `grep` for `NW_PROF` or
  `build_extra` across the C sources returns only a comment in `blob.h`
  recording the removal. The profile went on 2026-09-10
  (`HISTORY.md` §23) and `CLAUDE.md` invariant 6 already said so — this
  copy did not. Found by `claims`, in the file the hook hands to an agent
  editing `nwsup.c` or `lids.c`.* If you find yourself adding a filter
  anywhere but `lids.c`, you are recreating the bug that was removed.
- **Adding a syscall to the allow-list requires naming the unit that needs it
  and why.** The suite asserts seccomp kills a house that calls
  `socket()`; if your change makes that pass, you widened the filter.
- **Lid order is fixed and is not a style choice:** NEWNET → NEWNS → brick
  pivot → Landlock → seccomp. The strict allow-list has no `mount`, no
  `unshare` and no `pivot_root`, so a house sealed first could not enter
  its own root. Sandboxing goes after the descriptors are in place and before
  `execv`.
- **`lids.c` returns -1 rather than exiting;** the caller decides what a
  failure means. Preserve that split — the shim reports, the supervisor sets
  policy.
- **Shutdown is bounded by the grace period, not grace × units.** Do not
  serialise it.
- **Loggers live in their own process group, and a house's last words are
  a correctness property.** `spawn_logger` calls `setpgid(0, 0)`. The
  loggers unblock TERM/INT — they must, or a drain pass that TERMs them
  sits pending forever, which is D11 — and that same unblocking makes
  them die on a **group-directed** TERM, where the default action is
  terminate, before they have drained the pipe. Measured, six runs each:
  TERM to PID 1 alone relayed 5 of a house's 5 final lines; the same TERM
  to the process group relayed **0 of 5**. The house wrote them all; its
  reader was gone.

  This is not tidiness. The budget is a hard total, so a house that
  exhausts it **stays dead until reboot**, and that was only acceptable
  because the death is visible. Lose the last lines and it is a black
  screen with no explanation — the property that made a hard total unsafe
  before. `test_last_words_survive_group_term` pins both directions;
  removing the `setpgid` turns it red naming the drain.

  Do not "fix" this by re-blocking TERM in the logger or by `SIG_IGN`.
  Both survive the group signal by making an *explicit* TERM do nothing
  as well, which is D11's exact shape laid across the natural fix. The
  process group makes the signal not arrive while
  `kill(logger, SIGTERM)` still works.
- **Signal-safety:** writes go through `write(2, ...)` directly. No
  `printf` in a signal or post-fork path.

## Bricks

A unit may declare `brick=/nw/bricks/<hash>`. `lid_brick()` makes mount
propagation private, binds the brick onto itself (`pivot_root` needs a
mount point; a brick is a plain directory), applies the declared binds, and
pivots. After that the house's `/` **is** the brick.

- **`NW_LID_NEWNS` is mandatory.** `nwcheck.c` returns `NW_E_BRICKNS`
  without it. `nwsup.c` re-checks it anyway, because it reads its unit from
  the environment rather than from the sealed blob.
- **Never `mkdir` into a brick.** A bind target must already exist inside
  it. A brick is sealed and content-addressed; creating a directory to make
  room for a mount would break the seal to save a bake-time decision.
- **The pivot is `pivot_root(".", ".")`**, not the two-directory form,
  which would need a `put_old` directory inside every brick. New root and
  `put_old` are the same directory; the old root ends up stacked on top and
  is detached through a descriptor opened beforehand.
- A bind is a **path made visible**, not a descriptor handed over, and it is
  the same path inside and out. Invariant 5 is about the descriptor table a
  house is born with, and that is still `/dev/null` on 0 and a log pipe on
  1 and 2.

## Liveness — a recorded refusal, not a missing feature

**Freeze detection is deliberately not in the design. A house that goes
silent but never exits is undetected by anything, and that is known and
accepted.**

`nwsup.c` blocks in `waitpid(p, &st, 0)` with no time bound. There is no
heartbeat, no deadline, no timeout, no `alarm`, no `WNOHANG`. There is no
field to put one in, and nothing in the plan language or the baker expresses
a deadline. `grep` for `clock_gettime`, `now_ms`, `alarm` or `nanosleep` in `nwsup.c` returns nothing since D18 removed the restart-budget window; `#include <time.h>` survived as dead weight. That is four names, so it is not the same claim as "no timing primitives at all", which is what this line used to say — `usleep`, `setitimer`, `timerfd_create` and a `poll` timeout would all pass it. `claims` widened the search and found none of them, so the stronger claim happens to be true today and its stated check does not establish it. The budget counts how often a house has **died**. Different problems; the budget does not touch this
one.

**Why refused:** every form of detection needs a guessed constant, and the
rule was attempted and wrong three times. A watchdog that fires on a
correctly-slow house is worse than no watchdog, because it converts a
performance problem into a restart loop, and the restart loop is the failure
mode this project has already paid for twice.

Reopening this is a **design decision**, not an implementation task. If you
propose one, propose the constant and say who chooses it and what happens
when it is wrong. Do not add a timeout because the code looks like it is
missing one.

## Also refused

**Nothing a house does halts the city.** Exactly two things halt it: the plan
fails validation at boot, or PID 1 dies. The `critical` flag was removed
rather than repaired — `HISTORY.md` §19.

## Orphans at shutdown — decided 2026-09-11

**PID 1 does not wait for orphans at shutdown. They die with the machine
at `reboot`.** This was "undefined — decide it explicitly rather than
letting the race pick", and this is the decision.

*Why not wait:* waiting on an orphan is unbounded by construction.
Nothing knows what a house forked or whether it will ever exit, so a
single stuck grandchild would hang the machine — which is the same
guessed-constant trap the Liveness section refuses, arriving through the
shutdown path instead. Shutdown stays bounded by the grace period.

*What this costs, stated so nobody rediscovers it as a bug:* an orphan
alive when shutdown begins is never reaped and never killed. On real
hardware `reboot(RB_POWER_OFF)` ends it. In a pid namespace the
namespace teardown does.

Measured at the boundary, three runs per rung, children dying either side
of the hold: before it, every orphan is reaped; at or after it, none are.
A sharp cutoff, not a flaky race. `test_orphans_across_restarts` pins
both halves — twelve orphans reaped across four restart cycles, and a
shutdown that closes in ~0.2s while 3-second children are still alive. A
blocking drain in `shutdown_city` turns the second half red at 3.01s.

**Reaping across restarts is no longer untested** — that entry was here
as a gap, and the fixture it lacked is `houses/orphan.c`.

## Known open in this territory

- **`closed … orphans=N` reports orphans REAPED, not orphans that
  existed.** A city that orphaned twelve processes which are still alive
  at shutdown closes with `orphans=0`. Internally the counter is
  `orphans_reaped` and is accurate; the label drops the verb, and an
  operator reads the line as "there were none".

  Not changed here, because it is not a one-word fix: `tests/run.py` and
  `tools/scale-probe.py` both key on the literal `orphans=0`, and the
  scale probe treats **any** orphan as a run failure — which is a
  reasonable health rule for a city of oneshot houses and wrong for a
  city whose houses fork. Renaming the field means deciding what the
  probe should consider healthy. That belongs to whoever owns `pid1.c`
  and the probe together.

  **What this does and does not put in doubt.** Every ladder run to date
  is unaffected in its *result*: the probe bakes only `unit-probe`,
  oneshot, and `grep -c fork unit_probe.c` is 0, so no ladder city has
  ever created an orphan and `orphans=0` there is true under both
  readings. What is weaker than it looked is the *check* — it has never
  distinguished the two meanings, because nothing it runs can produce an
  orphan, so the probe's health rule is unvalidated for the one case
  where the two readings diverge. That matters the moment the ladder is
  pointed at a forking house, which is what bricks phase 2 (a loop
  device per house) would do.

## Definition of done

**`make stage`, not `make`** — then `python3 tests/run.py`. The suite
runs the *staged* binaries under `/tmp/nw-init-run`; `make` alone
rebuilds the source tree and leaves the suite running yesterday's code. This
has already produced one false result, and a false pass is worse than a
failure. Then quote the actual exit codes and log lines. Never claim a change
works from reading alone.
