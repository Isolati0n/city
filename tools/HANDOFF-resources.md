# Handoff: applying the resource block, to whoever has the machine

Base: `33cf074` on `origin/main`, plus the plan-half commit this file
lands with. **Stale past that point until this update** (Phase 3,
`docs/OPERATOR-BRIEF.md` Section 3, and Phase 4, `docs/options/31`) —
this file's own claim "Nothing applies it" was true at `33cf074` and is
no longer true of most of the table below; the parts still true are
narrower now, and this update says which.

**What changed since `33cf074`, briefly, because the table below still
carries fields Phase 3/4 resolved.** `cpu_mask`, `cpu_weight`, `mem_high`,
`mem_max`, `sched_policy` and `nice` are applied now — `nwsup.c` reads
each as a plain-decimal `NW_*` env var `nwspawn.c` forwards from the
sealed unit's `struct nw_res`, and dies by name if the mechanism cannot
be applied (`.claude/rules/runtime.md`'s "The resource block: most
fields enforced, since Phase 3"). `layer_bytes` moved earlier still, to
`runtime.md`'s Hard rules, applied via a loop-mounted, sized filesystem
rather than project quota — the project-quota row below is retired, not
merely unproven. `io_rbps`/`io_wbps` are **deleted from the format
entirely** as of Phase 4 (`NW_MAGIC` NWPLAN12, `docs/options/31`),
because nothing in three phases ever forwarded or read either one, and
the io.max device-resolution question this file's own §2 below asked
was never answered — the field left with the question still open,
which is worth knowing if a future bump reopens the class rather than
rediscovering it as new work. `oom_score_adj` (Phase 4) is applied too,
by a direct `/proc/self/oom_score_adj` write — no cgroup controller
involved, so it is not part of the cgroup-availability story this file
is about, and is not added to the table below for that reason; its own
environment limitation (this container's process itself lacks
`CAP_SYS_RESOURCE`, so a *negative* declared value cannot be verified
applied here even though the write mechanism runs) is recorded in
`docs/ENVIRONMENT.md` and `docs/options/31` §8, not here.

**What is still exactly the situation this file was written to describe:
`task_cap`.** `struct nw_res` carries it, the baker parses and refuses a
declared zero, `nwcheck.c` validates the range independently, and
`nwsup.c` applies it via `pids.max` on the same per-generation cgroup
leaf `mem_high`/`mem_max`/`cpu_weight` already use — **but this machine
cannot exercise the write**, for the same reason it could not exercise
the memory and cpu fields before Phase 3 landed elsewhere, and unlike
those three, nobody has yet had the machine to verify `task_cap`
anywhere. This file is what to run on a machine that can.

Written as a fixture rather than a question, per `.claude/rules/harness.md`:
*"What to hand over is the fixture, not the question: the city, the blob,
the probe and the exact line whose output decides it."*

## Why this machine cannot

```
$ grep cgroup2 /proc/mounts
cgroup2 /sys/fs/cgroup/unified cgroup2 rw,relatime 0 0
$ cat /sys/fs/cgroup/unified/cgroup.controllers
hugetlb
$ quotactl(QCMD(Q_GETINFO, PRJQUOTA), "/dev/vda")
-> -1 errno 3 (ESRCH)
```

**Narrower than it was when this was written.** cgroup v2 is mounted and
carries only `hugetlb`; at `33cf074` that blocked `cpu`, `memory` and
`io` alike, and blocked the whole table below. Phase 3 shipped
`mem_high`/`mem_max`/`cpu_weight` behind a named boot-time refusal
instead of waiting for this machine to change (`nwsup.c` dies
`cgroup memory controller unavailable` / `cgroup cpu controller
unavailable` on this exact machine, verified directly by baking and
booting a plan that declares one) — so those three are no longer
blocked on this file, they are blocked-and-refused-loudly on the
machines that lack the controller, which is the "not advisory" rule
below, already applied. What remains genuinely blocked here is `pids`
specifically, for `task_cap`, by the identical mechanism: `cgroup v2`
carries no `pids` entry in `cgroup.controllers` above, so
`CLONE_INTO_CGROUP` placement still works (it needs no delegation) but
writing `pids.max` cannot be exercised, and `nwsup.c` is expected to
die the same way the other three do — unverified here for lack of the
controller, which is exactly this file's subject now, narrowed to one
field.

A test written here would take the unavailable branch and print green,
which is `lid-landlock`'s whole life. So the suite must `skip()` with a
named reason on any machine in that state, exactly as `make_brick()` does
for erofs — the guard in the helper, not at each call site. (Phase 3's
`mem_high`/`mem_max`/`cpu_weight` took the stronger route instead — no
skip, a named `die()` — because "not advisory" means a controller
gap is a boot refusal, not a quietly-skipped assertion; `task_cap` should
get the same treatment once a machine can verify the die() fires for
the right reason and not merely for the right *absence*.)

## The city, already bakeable

```
house bounded /bin/probe kind=longrun budget=3 lids=newns,seccomp \
  brick=<hash> layer=l-bounded \
  cpus=0 cpu-weight=100 mem-high=64M mem-max=128M \
  task-cap=8 layer-bytes=16M sched=batch
house unbounded /bin/probe kind=longrun budget=3 lids=newns,seccomp \
  brick=<hash> layer=l-unbounded
```

(`io-rbps=`/`io-wbps=` are gone from the plan language as of Phase 4 —
see the note at the top of this file — so they are gone from the
fixture too; a copy of this city predating that bump that still
declares them will not bake under `NWPLAN12`.)

The second house is the pairing. Every assertion about the first is
satisfied by a supervisor that applies the same limit to everything, or to
nothing, unless a house declaring no block is measured alongside and comes
back unlimited.

## What each number decides, and the line that decides it

**Applied and verified elsewhere, listed here for the table's own
completeness — do not re-derive these on this machine, they already
have a `die()` reproduction on this exact one, in `runtime.md`:**

| field | cgroup v2 file / syscall | the probe | what a wrong answer means |
|---|---|---|---|
| `cpu_mask` | `sched_setaffinity` | house reads `/proc/self/status` `Cpus_allowed_list` | affinity silently not applied |
| `cpu_weight` | `cpu.weight` | read it back from the house's cgroup | a share nobody chose |
| `mem_high` | `memory.high` | read back; then allocate past it and check the house SLOWS and does not die | the throttle never fires |
| `mem_max` | `memory.max` | allocate past it; house is killed, `memory.events` `oom_kill` increments | the backstop is decorative |
| `sched_policy` | `sched_setscheduler` | `/proc/self/stat` field 41 | policy not applied |
| `nice` | `setpriority` | `/proc/self/stat` field 19 | priority not applied |

**Still unverified anywhere — this file's actual, narrowed subject:**

| field | cgroup v2 file / syscall | the probe | what a wrong answer means |
|---|---|---|---|
| `task_cap` | `pids.max` on the house's per-generation cgroup leaf | fork to the declared cap, confirm the next `fork()` fails `EAGAIN`/`ENOMEM` inside the house; the *neighbor* house on a separate leaf must be unaffected | a cap that bounds nothing, or bounds the whole machine instead of one house |

`layer_bytes` is gone from this table — it is applied through a
loop-mounted, sized filesystem (`runtime.md`'s Bricks section), not
through project quota, and its own test
(`test_layer_bytes_enforces_capacity`) already runs in `make test` on
every machine, including this one. `io_rbps`/`io_wbps` are gone from
this table because they are gone from the format.

**The two memory numbers need different probes and that is the point.**
`memory.high` throttles so an operator does not lose work; `memory.max`
kills. A test that only reads both files back is satisfied by a supervisor
that writes them and a kernel that ignores them. `mem_high` has to be shown
*not* killing and `mem_max` has to be shown killing. `task_cap` has the
same shape one level down: reading `pids.max` back is satisfied by a
supervisor that writes it and a kernel that never enforces it, so the
probe has to show the fork that *should* fail, failing — the pairing
`plan.md`'s "definition of done" already requires of every new check,
applied to a runtime mechanism instead of a bake-time one.

## The rule already adopted, not merely proposed

**A resource limit is not advisory, and this is no longer a proposal for
your call — it is what `nwsup.c` does for `mem_high`, `mem_max` and
`cpu_weight` today.** If a declared limit cannot be applied, the house
does not start — `die()`, not a log line and a return, exactly as every
lid path in `nwsup.c` already does. A house running unbounded while the
plan says it is bounded is the plan lying, which is invariant 6's
argument transplanted, and the failure mode is worse here: a house that
was supposed to be capped at 128M and is not will take the machine down
rather than itself. Verified directly on this machine (§ above): a plan
declaring `mem-high=` dies naming the memory controller, one declaring
`cpu-weight=` dies naming the cpu controller. `task_cap` is designed to
the same rule and is the one field where nobody has yet had the machine
to confirm the `die()` actually fires for the right reason.

The cost, stated: a city that boots on one machine will refuse to boot on
a machine whose kernel lacks a controller. That is the intended reading of
"not advisory", and it is the opposite of what a container runtime
usually does.

**What it interacts with:** the budget is a hard total, so a house that
cannot get its limits burns its budget and stays down until reboot. That
is already true of a lid it cannot get.

## What this file could not settle without the machine, and how much of it Phase 3 answered

1. **Where the cgroup comes from — settled.** `nw-sup` creates a fresh,
   uniquely-named leaf per fork generation (`"<name>.<generation>"`)
   under its own delegated subtree and destroys it after
   `cg_kill_sweep()`, never reusing one across a restart — `runtime.md`'s
   "A cgroup is never reused across a restart of the house" bullet has
   the reason (a `cgroup.kill` write poisons the directory for whatever
   is placed into it next). `nw-sup` is a cgroup writer now; that was
   flagged here as worth a decision before code, and the decision was
   made in Phase 3.

2. **`io.max` needs the device, and the device is a machine property —
   moot.** `io_rbps`/`io_wbps` were deleted from the plan format
   entirely in Phase 4 rather than answered: nothing in three phases of
   implementation ever forwarded or read either field, so the device-
   resolution question this bullet asked was retired along with the
   field it was about, not resolved. A future field wanting `io.max`
   would need to answer it fresh.

## What is already pinned, so you do not re-derive it

- Every range and cross-field refusal, both directions, at bake time
  (`test_baker_refuses_bad_resources`) and in the checker
  (`test_checker_rejects_crafted_resources`), now covering `task_cap`
  and `oom_score_adj` alongside the original fields.
- The baker's own byte positions for every field of `struct nw_res`
  (`test_baker_writes_the_declared_layout`) — `io_rbps`/`io_wbps` are
  gone, so the swap-test mutation this bullet used to cite no longer
  exists; `task_cap` and `oom_score_adj` have no such backstop of their
  own (no cross-field rule ties either to another field, so nothing
  catches the two of them being swapped except the same byte-position
  test), per `docs/options/31`.
- `NW_MAGIC` moves with the layout, including through `NW_RES_SIZE`
  (`test_magic_moves_with_the_layout`) — carried across the Phase 4 bump
  to `NWPLAN12`, with `plan-formats.txt` holding the new layout
  signature.

None of that says anything about whether `task_cap` is ever applied —
which is this file's one remaining open question.
