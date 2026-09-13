# Handoff: applying the resource block, to whoever has the machine

Base: `33cf074` on `origin/main`, plus the plan-half commit this file
lands with.

The plan half is landed. `struct nw_res` is in the blob, the baker parses
and refuses, `nwcheck.c` validates independently, and the specs carry the
one rule they can. **Nothing applies it.** This file is what to run on a
machine that can, and what each run decides.

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

cgroup v2 is mounted and carries only `hugetlb`; `cpu`, `memory` and `io`
are bound to v1 hierarchies, so none of the three controllers the block
needs is reachable through v2. Project quota is off on the root device.

A test written here would take the unavailable branch and print green,
which is `lid-landlock`'s whole life. So the suite must `skip()` with a
named reason on any machine in that state, exactly as `make_brick()` does
for erofs — the guard in the helper, not at each call site.

## The city, already bakeable

```
house bounded /bin/probe kind=longrun budget=3 lids=newns,seccomp \
  brick=<hash> layer=l-bounded \
  cpus=0 cpu-weight=100 mem-high=64M mem-max=128M \
  io-rbps=1M io-wbps=1M layer-bytes=16M sched=batch
house unbounded /bin/probe kind=longrun budget=3 lids=newns,seccomp \
  brick=<hash> layer=l-unbounded
```

The second house is the pairing. Every assertion about the first is
satisfied by a supervisor that applies the same limit to everything, or to
nothing, unless a house declaring no block is measured alongside and comes
back unlimited.

## What each number decides, and the line that decides it

| field | cgroup v2 file / syscall | the probe | what a wrong answer means |
|---|---|---|---|
| `cpu_mask` | `sched_setaffinity` | house reads `/proc/self/status` `Cpus_allowed_list` | affinity silently not applied |
| `cpu_weight` | `cpu.weight` | read it back from the house's cgroup | a share nobody chose |
| `mem_high` | `memory.high` | read back; then allocate past it and check the house SLOWS and does not die | the throttle never fires |
| `mem_max` | `memory.max` | allocate past it; house is killed, `memory.events` `oom_kill` increments | the backstop is decorative |
| `io_rbps` / `io_wbps` | `io.max` `rbps=`/`wbps=` on the layer's device | read back; measure a write | **the device number is resolved by nw-sup, not carried in the plan** |
| `layer_bytes` | `prjquota` project id on the layer's `upper` | fill past it, expect `EDQUOT` | a capacity that bounds nothing |
| `sched_policy` | `sched_setscheduler` | `/proc/self/stat` field 41 | policy not applied |
| `nice` | `setpriority` | `/proc/self/stat` field 19 | priority not applied |

**The two memory numbers need different probes and that is the point.**
`memory.high` throttles so an operator does not lose work; `memory.max`
kills. A test that only reads both files back is satisfied by a supervisor
that writes them and a kernel that ignores them. `mem_high` has to be shown
*not* killing and `mem_max` has to be shown killing.

## The rule I propose for application, for your call

**A resource limit is not advisory.** If a declared limit cannot be
applied, the house does not start — `die()`, not a log line and a return,
exactly as every lid path in `nwsup.c` already does. A house running
unbounded while the plan says it is bounded is the plan lying, which is
invariant 6's argument transplanted, and the failure mode is worse here:
a house that was supposed to be capped at 128M and is not will take the
machine down rather than itself.

The cost, stated: a city that boots on one machine will refuse to boot on
a machine whose kernel lacks a controller. That is the intended reading of
"not advisory", and it is the opposite of what a container runtime
usually does.

**What it interacts with:** the budget is a hard total, so a house that
cannot get its limits burns its budget and stays down until reboot. That
is already true of a lid it cannot get.

## What I could not settle without the machine

1. **Where the cgroup comes from.** `nw-sup` needs a writable subtree with
   `cpu`, `memory` and `io` delegated. On a systemd machine that is a
   delegated slice; on a bare boot, `dawn` mounts cgroup2 already and PID 1
   would have to create the tree. The second is the design here, and it
   makes `nw-sup` a cgroup writer, which is a new capability in a TCB file.
   Worth a decision before code.

2. **`io.max` needs the device, and the device is a machine property.**
   The block carries the rate only, deliberately. `nw-sup` resolves
   `major:minor` from where the layer actually is (`stat` the `upper`
   directory, take `st_dev`). That is the provenance rule working: the
   number that travels is the rate, the number obtained from the machine is
   obtained on the machine. It has never been run.

## What is already pinned, so you do not re-derive it

- Every range and cross-field refusal, both directions, at bake time
  (`test_baker_refuses_bad_resources`) and in the checker
  (`test_checker_rejects_crafted_resources`).
- The baker's own byte positions for every field of `struct nw_res`
  (`test_baker_writes_the_declared_layout`) — the mutation that needs it is
  swapping `io_rbps` and `io_wbps`, which is legal in every direction and
  visible only as a position.
- `NW_MAGIC` moves with the layout, including through `NW_RES_SIZE`
  (`test_magic_moves_with_the_layout`).

None of that says anything about whether a limit is ever applied.
