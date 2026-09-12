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

  **The lexical check lives here, not in `pid1.c`.** Nothing this tree
  ships sends a group-directed signal:
  `grep -nE "killpg|kill\(-|kill\(0|tcsetpgrp|setsid" *.c` returns
  nothing. It is written in this file rather than beside the code
  because a comment naming the tokens it greps for is a hit for its own
  check — which is exactly how invariant 1's `mount` check was weakened
  to "returns only comments" and stopped being decisive.

  **Precondition, and it is not hypothetical for long:** this is safe
  while PID 1 has no controlling terminal. Give it one and two things
  flip together. `^C` becomes a kernel-generated group signal, which
  makes the `setpgid` load-bearing on real hardware; and the loggers,
  no longer the foreground group, become subject to `SIGTTOU` on
  `write(2)` when `TOSTOP` is set. `SIGTTOU` is not in the set PID 1
  blocks, so the default action applies and **the logger stops** — the
  pipe fills, the house blocks in `write` forever, and freeze detection
  is refused by design, so nothing notices. Demonstrated on a pty by
  `tcb-review`, same program either side, only the `setpgid` differing.
  If a console lands, block `SIGTTOU`/`SIGTTIN` in the logger.

- **Shutdown lets the loggers drain, and SIGKILL is the deadline
  action, not the first one.** `shutdown_city` waits for each logger to
  reach EOF and exit — PID 1 closed its own write ends at boot, so the
  last house's death closes the pipe — bounded by `NW_GRACE_MS` and
  concurrent across units, so the bound is still the grace period and
  not grace × units.

  It was an unconditional SIGKILL until 2026-09-11, racing the drain.
  It won small and lost large: `tcb-review` measured 2500 final lines
  (~160 KiB) relaying 2500, 2433 and 2451 across three runs, and the
  loss was a **contiguous tail** — the end of the output, which is the
  part that says why the machine is going down. At five lines it never
  lost anything, and five lines was the size the suite pinned. A
  property tested only at the size where it holds by luck is the
  characteristic failure with a test attached to it.
  `test_last_words_survive_group_term` now runs 5 and 2500; restoring
  the unconditional SIGKILL turns the 2500 case red and leaves the 5
  case green, which is the whole argument for the second size.
- **Signal-safety:** writes go through `write(2, ...)` directly. No
  `printf` in a signal or post-fork path.

## Bricks

**Landlock grants WRITE beneath the root as of 2026-09-12, and NOT
TRUNCATE**, because it runs after the brick pivot and that root is the
overlay — granting only read made every landlock house's layer
unwritable, confirmed live as `wr_root=denied(13)` where a read-only
image gives `denied(30)`. What the lid still provides is the withheld
`MAKE_` rights (no device nodes, sockets or fifos) and the scoping of
binds. `MAKE_REG` is withheld too, so a landlock house modifies what its
brick shipped with and creates nothing new under `/`.

`TRUNCATE` was granted with the write for one round and is withheld
again: an emptied file copies up into the durable layer and no boot
recovers, which is the same unrecoverable state `REMOVE_FILE` is
withheld to prevent — so granting one and withholding the other is
incoherent. It is still granted **inside a declared bind**, which is
machine-side and outside the layer. The consequence for a house:
`open(..., O_TRUNC)` and `ftruncate` beneath `/` fail with EACCES;
rewrite in place, or declare a bind. Enforced only at ABI ≥ 3, because
the right does not exist below that.

**It does not close the durable-mask class, and the first telling said
it did.** `WRITE_FILE` stays granted and reaches the identical state by
overwriting a brick file in place — measured on a real overlay, eight
bytes over an ELF header, no truncate and no unlink, and every later
mount of the same sealed image plus the same layer is unrunnable while
the image stays byte-identical. Nor can a house do it to its own exec
path: that is `ETXTBSY` while it runs, so the reachable target is a
shared library or a data file the brick shipped. What the withholding
closes is the zero-length route and the accidental `O_TRUNC` rewrite.
Making the class unrepresentable means stopping the layer shadowing
the image's executables at all — a design change, not a rights change.
`CLAUDE.md` invariant 6, `HISTORY.md` §56, §57 and §58.

**THE CLASS IS OPEN, AND THE RECOVERY BELOW IS THE ONLY ANSWER TO
IT.** Not a footnote and not an aside: there is no lid set
that prevents a brick house durably masking its own image, and nothing
in the tree detects one that has. `landlock` is the narrowest and it
narrows the routes, not the outcome. So when a house that booted
yesterday will not exec today and the image still hashes to its own
name, do not debug the image and do not rebuild it — read **THE
RECOVERY** below and delete the layer. Every round that has touched
this so far has reached for a rights change first; the rights are not
where the answer is.

**A LAYER CAN MASK ITS BRICK, DURABLY.** "Reads fall through to the sealed
image" is true and incomplete: the overlay can also whiteout and overwrite,
and those survive reboot because the layer is durable and keyed by an id.
Measured: a house that unlinks its own exec path leaves a whiteout in
`upper` and never starts again — same plan, same sealed brick, `FAIL exec
house errno=2` forever, image byte-identical to its own name. 

**THE RECOVERY — the only answer to the durable-mask class, not a
footnote to it.** Written down because nothing implements it and the
next person to hit this needs the answer, and pointed at from the
Landlock paragraph above because that is where the wrong answer gets
reached for. It is **not** rebuilding the
brick: the image is untouched and still hashes to its own name, so
rebuilding changes nothing. It is both of these, in order:

    rm -rf /nw/layers/<layer-id>
    python3 tools/stage-layers.py <blob>    # recreates upper/ and work/

`rm` alone gives `FAIL mount layer errno=2` on every boot, because
nw-sup does not create what the stager owns. The house's data is lost;
there is no way to keep it and undo the mask. **Nothing in the tree does
either step** — a reclaim path is a real missing piece. The phase-2 seal protects the
image file, not the house's view of it. `tcb-review`.

**Every brick house also has exactly one writable layer, and the two are
one thing.** `lid_brick()` mounts the erofs image on `NW_BRICK_MNT` and
then mounts an overlay **at that same mountpoint** with
`lowerdir=NW_BRICK_MNT`, `upperdir=/nw/layers/<id>/upper`,
`workdir=/nw/layers/<id>/work`, and the house pivots into that. Reads fall
through to the sealed image; writes land in `upper` and survive a restart.

- **Stacked, not given its own mountpoint.** Verified by mounting, not by
  reading: overlayfs resolves `lowerdir` at mount time and holds the
  superblock, so covering the path afterwards is fine. One mountpoint means
  no second directory for dawn and no third path in the design.
- **Before the binds.** A bind mounted first is hidden by the overlay
  covering the same mountpoint, and the mount would still succeed — the
  house would silently see the brick's empty directory instead of the bound
  path.
- **`upper` and `work` are siblings under one parent**, which answers
  overlayfs's requirement (same filesystem as `upper`, not inside it) by
  construction rather than by a rule anyone has to remember.
- **nw-sup creates NEITHER.** `tools/stage-layers.py` does, from the plan,
  before the boot. A supervisor that mkdir'd a missing layer would turn
  "nothing staged this plan" into "the house silently got an empty layer",
  which is the orphaned-data failure the layer-id exists to prevent. A
  missing layer is a loud `FAIL mount layer` instead.
- **One layer per house, enforced.** Two houses on one id share one
  `upperdir` and one `workdir`: each appends to the other's data and the
  kernel calls the workdir sharing undefined behaviour, into `dmesg`,
  which nothing here reads. `NW_E_LAYERDUP` in `nwcheck.c` (a second pass
  of the same `field_dup` table that catches duplicate names) and a
  named refusal in the baker.
- **Keyed by a declared id, never by the house name.** Rename a house under
  name-keying and it gets an empty layer while its data sits under the old
  name, with nothing reporting anything.
- `/nw/stores` is **gone**, not renamed: the store concept was this
  mechanism under another name.

A unit declares `brick=<hash of an erofs image>` and `layer=<id>`. `lid_brick()` makes mount
propagation private, attaches the image to a loop device, mounts it on
`NW_BRICK_MNT` (`/nw/mnt`, which **dawn creates** — that is a precondition
of any brick house starting), applies the declared binds, and pivots. After
that the house's `/` **is** the brick.

*This described the pre-phase-2 code until 2026-09-12 — "binds the brick
onto itself … a brick is a plain directory" — which was two false clauses
in the file `tools/rules-hook.sh` hands to the next agent who edits
`nwsup.c`. Same shape as the `window_s` sentence, same file, and the
paragraph three sections down that says why a stale rule here is worse than
elsewhere was already there. `tcb-review`.*

- **`LOOP_CTL_GET_FREE` reports a free index; it does not reserve one.**
  Every brick house runs `lid_brick()` concurrently, so without a retry
  they all get the same index and all but one get `EBUSY`. Shipped that
  way and measured: at two houses one failed on every run and the restart
  budget hid it; at eight, houses were permanently lost; at
  `NW_MAX_UNITS`, 6–15 of 64 attached — while the city printed
  `closed houses_reaped=64 orphans=0`.

  The retry re-does `GET_FREE` each attempt, **bounded by `NW_MAX_UNITS`
  because that is derived** — at most that many houses can contend. Do
  not replace it with an index derived from the unit's table position
  (that is `BASE + i` on a device number, i.e. bugs 9 and 13 again) or by
  serialising in `nw-spawn` (that breaks `AUTOCLEAR`'s anchor and
  invariant 5). A file-backed erofs mount would remove the class outright
  and is recorded in `docs/plans/01`; it raises the kernel floor to 6.12,
  which is a production decision.

- **The budget is not a retry mechanism.** Anything that spends a death on
  a transient resource race is spending a hard total (invariant 4) that a
  longrun house needs for a real crash. That is why the concurrency test
  asserts zero restarts and zero `EBUSY`, not merely that every house ran.

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
