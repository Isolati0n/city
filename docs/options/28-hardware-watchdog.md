# 28 — hardware watchdog

Status: **design note. No code in this round.** Gate: **none** — this is
independent of every other open item (Phase 4's nwctl/grants work, the
control-app and rescue-interface notes, the backup tool). Answers the
operator's five sub-questions on a hardware watchdog feeder, dispatched
alongside notes 27, 29 and 30.

Every claim below was checked against the tree, or measured directly on
this container, at the time this note was written; commands are quoted
so the check can be re-run rather than trusted.

## This is not freeze detection, and does not reopen that refusal

`.claude/rules/runtime.md`'s Liveness section refuses freeze detection
outright: "every form of detection needs a guessed constant, and the
rule has been attempted and wrong every time... A watchdog that fires on
a correctly-slow house is worse than no watchdog, because it converts a
performance problem into a restart loop."

That refusal is about one specific question: **is this HOUSE still doing
useful work?** Answering it needs a guess about how slow is too slow for
*that house's own task*, which varies per house and is exactly the
guessed constant the refusal is about. Nothing in this note reopens
that question or proposes a mechanism for it.

This note is about a different, strictly lower-level question: **is the
kernel scheduler running at all?** A hardware watchdog timer does not
inspect any house, does not know what a house is, and does not guess
anything about workload — it counts down independently of whatever the
CPU is doing, and it force-resets when nothing pats it in time,
regardless of *why* nothing pats it. The two questions differ in kind,
not just in degree:

- Freeze detection asks "is this specific unit of work still making
  progress," which has no answer without a model of what progress means
  for that unit — hence the guessed constant, hence the refusal.
- A hardware watchdog asks "did *any* process get scheduled recently
  enough to write one byte to a device node," which has a single
  universal answer (yes or no) with no per-house model at all. The
  feeder's own job (below) is deliberately as close to "prove the
  scheduler runs" as a userspace program can get — it does nothing else.

The timeout below is a property of the feeder's own trivial loop, not a
guess about any house's workload, and that is the distinction this
note's own text is required to make explicit.

## 1. Mechanism

Linux's watchdog framework exposes a char device, `/dev/watchdog` (or
`/dev/watchdogN` when more than one driver is present), through a
uniform API regardless of which driver backs it (`Documentation/watchdog/
watchdog-api.rst`): open it, and from then on a plain `write()` of any
single byte counts as a heartbeat. No `ioctl` is required for the common
case — `WDIOC_SETTIMEOUT`/`WDIOC_KEEPALIVE` exist for a caller that wants
to read or change the timeout, but a fixed-timeout feeder needs neither.
This matters for the seccomp allow-list below.

**Two backends, and they are not equivalent for this purpose:**

- **A real hardware watchdog timer** (silicon on the board, or an
  emulated PCI device such as QEMU's `-device i6300esb` or `-device
  ib700`) counts down independently of the CPU and the OS. It resets the
  machine even if every CPU is spinning with interrupts disabled, even
  if the timer interrupt itself has stopped firing, even if the kernel
  has taken a fault inside the scheduler. This is the genuine
  non-guessing guarantee the operator's framing asks for.
- **`softdog`**, the kernel's software watchdog module, arms a kernel
  high-resolution timer and reboots (or panics, depending on
  `soft_panic`) when its callback fires without having seen a recent
  write. It is *not* the same guarantee: its callback is itself
  scheduled by the kernel, so a lockup severe enough to stop the timer
  interrupt or wedge the scheduler at the level real hardware silicon is
  meant to catch can also stop `softdog`'s own callback from running.
  It still catches the case this project's Liveness section already
  distinguishes from a total lockup — userspace starvation, a runaway
  realtime task monopolizing every CPU while interrupts still fire, a
  livelock in ordinary scheduling — which is a real and useful subset,
  just not the full claim "confirms the kernel scheduler runs at all
  independent of the kernel's own state."

  **Recommendation: prefer a real (or hardware-emulated) watchdog device
  over `softdog`, and say explicitly which one a given build is running
  under**, because reporting a `softdog`-backed test as evidence for the
  hardware claim would be exactly the "evidence is real and about
  something else" shape `CLAUDE.md` warns about.

**Measured on this container: neither is available, and the gap is
total, not partial.**

```
$ zcat /proc/config.gz | grep -iE "WATCHDOG|_WDT" | grep -v '# .* is not set'
CONFIG_CLOCKSOURCE_WATCHDOG=y
CONFIG_CLOCKSOURCE_WATCHDOG_MAX_SKEW_US=100
CONFIG_WATCHDOG=y
CONFIG_WATCHDOG_CORE=y
CONFIG_WATCHDOG_HANDLE_BOOT_ENABLED=y
CONFIG_WATCHDOG_OPEN_TIMEOUT=0
CONFIG_WATCHDOG_SYSFS=y
CONFIG_WQ_WATCHDOG=y
```

Only the core framework is compiled in; `# CONFIG_SOFT_WATCHDOG is not
set` and there is no PCI/platform watchdog driver in the config at all
("Watchdog Device Drivers" section header, nothing listed under it).
There is also no module directory for the running kernel to load one
into later: `modprobe softdog` answers `FATAL: Module softdog not found
in directory /lib/modules/6.18.44-fc-v37` — that directory does not
exist for this kernel version at all (a stale `/lib/modules/6.8.0-139-
generic` tree is present from image build, matching neither this
kernel's `uname -r` nor a real target). So `/dev/watchdog*` cannot
appear here by any route, which is why the falsifying test in §5 cannot
run on this container and is deferred.

**Target board: check, not assumed.** The operator's own environment
report elsewhere in this tree (`.claude/rules/harness.md`'s "A claim
that needs a real boot" section) names "Ubuntu 6.8.0-139 under QEMU" as
a machine the operator can run boots on. Whether that VM (or real
target hardware, if this ever leaves QEMU) has a watchdog device
attached is a fact about that machine's configuration, not this
container's, and is not established here. What QEMU itself supports
without any special host hardware is `-device i6300esb` (Intel 6300ESB,
driver `CONFIG_I6300ESB_WDT`) — a standard, well-tested emulated PCI
watchdog that behaves like real silicon from the guest's point of view
(counts down in the emulator process, independent of the guest CPU
state) and is the natural thing to attach for both development testing
and the falsifying test in §5.

## 2. Where the feeder lives

**Not PID 1** — stated as a design constraint, given as commitment 5 by
the operator this round, and independently consistent with this
project's own existing, enforced pattern: `CLAUDE.md` invariant 1
requires `pid1.c` to carry no restart budget and no timer/deadline
machinery at all (`grep` for `budget`, `restart` or `respawn` in
`pid1.c` returns nothing, annotated and checked by `make checkbrief`),
and `.claude/rules/runtime.md`'s Liveness section says the same about
`nw-sup`: "There is no field to put one in... no `clock_gettime`,
`now_ms`, `alarm` or `nanosleep`." Giving PID 1 a feed-the-watchdog timer
loop would be the exact shape of machinery invariant 1 exists to keep
out of the one component every other failure mode routes through.

**Recommendation: an ordinary house, declared in the plan like any
other unit, not a PID-1-adjacent helper with special standing.** Reasons:

- It reuses every mechanism this project already has for a long-running
  process instead of inventing a new privileged category: `nw-sup`'s
  fork/wait/restart-budget loop, the lid set (seccomp/Landlock/
  namespaces), and the resource block (§below). A "PID-1-adjacent
  helper" would need PID 1 to know how to fork it, wait for it, and
  decide what its death means — which is new logic in the one file this
  project works hardest to keep free of exactly that kind of logic
  (`nw-spawn` already forks every unit once at boot and exits; PID 1
  itself forks nothing after that).
- Ordinary supervision by `nw-sup` is the right failure mode for the
  *recoverable* case and does not defeat the watchdog for the
  *unrecoverable* case — see §3.
- It gets elevated scheduling priority for free through the plan's
  existing resource block, no new plan field (§below).
- Its syscall footprint fits the existing seccomp allow-list with zero
  additions (§below) — which matters because
  `.claude/rules/runtime.md` requires naming the unit that needs a new
  syscall and why; this design needs none.

**What the plan format actually offers for "elevated priority," stated
honestly rather than assumed.** `blob.h` defines only three classic
scheduling policies for the `sched_policy` byte —
`NW_SCHED_OTHER`/`NW_SCHED_BATCH`/`NW_SCHED_IDLE` — there is no
`SCHED_FIFO`/`SCHED_RR` option in this plan format at all. The separate
`sched-ext=` field (`docs/options/15-per-house-scheduling.md`) is a BPF
scheduler-class mechanism whose *reject* path (refusing an unsupported
kernel) is built, but whose *accept* path — an actually-loaded policy
that would do anything once declared — is explicitly kind 3, "waiting
on a prerequisite... nothing available to this project can build or
verify one" (`docs/options/15`'s own status line). Declaring
`sched-ext=default` today changes nothing about how the feeder is
scheduled. So the best currently-effective combination is: `sched=other`
with `nice=-20` (the most favorable nice value the format accepts,
checked in `nwcheck.c`'s resource-block bound), plus a dedicated
`cpu_mask` bit (pin the feeder to a core other houses aren't masked
onto) and a high `cpu_weight`. That is real, available protection
against ordinary CFS/EEVDF starvation and CPU contention — it is not a
hard real-time guarantee, and this note does not claim one exists in
this codebase today.

**Syscall footprint, checked against the existing allow-list rather than
assumed.** The feeder's entire loop is: `openat("/dev/watchdog", O_WRONLY)`
once, then forever `write(fd, "\0", 1)` followed by a bounded sleep. Every
syscall that needs — `openat`, `write`, `nanosleep`/`clock_nanosleep`,
`close`, `exit`/`exit_group` on the way out — is already in `lids.c`'s
`strict_allow[]` (`lids.c:13-33`). No `ioctl` is needed (per §1, a plain
byte write is a valid heartbeat under the kernel watchdog API), which
matters because `__NR_ioctl` is not in `strict_allow[]` today and adding
it for this one house would be exactly the "name the unit and why"
syscall-addition `.claude/rules/runtime.md` requires — avoided here by
design rather than by omission. The feeder can run under
`lids=seccomp` (Landlock and a private mount namespace add nothing it
needs, since it touches one device path and no bind).

## 3. What "feeder can't run" means, and the timeout value

Two distinct causes reach the same visible outcome, and the design
should treat them differently on the way there:

- **The feeder dies but the machine is fine** (a bug in the feeder, an
  OOM kill, a signal). `nw-sup` supervises it like any other longrun
  house: on exit it restarts under the normal budget
  (`.claude/rules/runtime.md`'s budget section — "a hard total of deaths
  for that supervisor's life"). If the restart completes well inside the
  watchdog's timeout window, the watchdog is fed again before it would
  have fired and nothing externally visible happens. This is the
  ordinary, desired outcome for a transient failure — the watchdog is
  not meant to punish a house that recovers on its own supervision.
- **Nothing can restart it, or nothing can run at all** — the budget is
  exhausted, `nw-sup` itself is gone, or (the case this mechanism exists
  for) the scheduler is genuinely wedged and *nothing* gets CPU time,
  including a fresh fork of the feeder. In every one of these, no
  process pats the watchdog, the timeout elapses, and the hardware
  forces a reboot regardless of the reason. The watchdog does not need
  to distinguish these cases from each other; distinguishing "recovered
  in time" from "did not" is exactly what the countdown itself does,
  which is the point of choosing a hardware timer over a piece of
  softwre trying to reason about the difference.

**Timeout value: proposed 30 seconds, stated as a starting point to be
measured against real restart latency, not derived from first
principles.** It has to clear the ordinary case above with margin: a
`fork()`+`execve()` of a tiny static binary plus `nw-sup`'s own poll loop
is comfortably sub-second in every measurement this tree has taken of
process startup (`.claude/rules/harness.md`'s scale-probe numbers are the
closest analog, though at a different scale), so 30s leaves roughly two
orders of magnitude of margin for an ordinary restart, while still
bounding how long the machine can sit unresponsive before the hardware
acts. This is exactly the kind of guessed constant the Liveness section
warns is easy to get wrong — the difference here is that getting it
wrong costs an unnecessary reboot in a slow-but-fine case, not a silent
failure to ever detect anything, and the constant is bounding the
feeder's own trivial loop rather than any house's variable workload.
**State it, measure it, and be ready to move it** rather than treating
30s as load-bearing today.

## 4. Interaction with an ordinary kernel panic

Checked against the tree rather than assumed: **there is currently no
`panic=` kernel parameter configured anywhere in this project, so a
genuine kernel panic today does not auto-reboot by any existing
mechanism.**

```
$ grep -n 'APPEND=' tools/mkboot.sh
447:APPEND="console=ttyS0,115200n8 ignore_loglevel NW_ROOT=/dev/vda NW_ROOT_FSTYPE=ext4 NW_ESP=/dev/vdb NW_ESP_FSTYPE=vfat"
```

No `panic=N`. The kernel's own default (`panic_timeout=0`) is to hang
after printing the panic, not reboot. This corrects the sub-question's
own framing, which assumed a panic "already reboots via existing
means" — measured, it does not, today.

**The watchdog closes this gap as a side effect, uniformly with the hang
case, without needing `panic=` at all.** A panic disables interrupts (or
at minimum stops the feeder's own scheduling) exactly as a hang does, so
the feeder cannot pat the device either way, and the timeout fires on
both. This makes the watchdog strictly more general than adding
`panic=N` would be on its own: `panic=N` only covers the case where the
kernel itself detects something is wrong and calls `panic()`; the
watchdog additionally covers the disjoint case named in the operator's
brief — a hang with **no** panic, where nothing in the kernel ever
decides anything is wrong. Once the watchdog is built, `panic=N` becomes
redundant with it for the reboot-on-panic behavior specifically (though
still useful on its own terms as a faster-diagnosed, more specific
signal than a bare timeout) — this note does not propose adding it, and
notes that its absence is not a gap this note needs to close.

## 5. Falsifying test

**The test**: with the feeder's house running and nothing else touched,
kill the feeder's process directly (not through the control channel —
a real `SIGKILL` to the exec'd pid, so `nw-sup`'s own restart path is
exercised exactly as it would be for any real crash) and confirm the
machine hard-reboots within `timeout + margin` seconds, with **no other
intervention** — specifically, the budget must be either pre-exhausted
or the test must repeat the kill fast enough that no successful feed
window occurs, or the test proves the wrong thing (a working restart,
not a working watchdog).

**Not possible on this container, and the reason is structural, not a
missing tool.** §1 already establishes there is no watchdog driver
compiled into this kernel and no module tree to load one into — there is
no `/dev/watchdog` to open here under any configuration, so there is
nothing for a feeder to write to and nothing for a test to observe
timing out. This is deferred to a real boot, the same route
`.claude/rules/harness.md`'s "A claim that needs a real boot goes to the
operator, with the fixture" section already exists for.

**The fixture to hand over, per that section's own requirement — not a
question, the concrete artifact:**

- A city plan with one additional house: the feeder, `kind=longrun`,
  `lids=seccomp`, `sched=other nice=-20`, running a small static binary
  that opens `/dev/watchdog`, writes one byte every `timeout/6` seconds
  (comfortable margin under the timeout without being needlessly
  chatty), and does nothing else.
- A QEMU invocation adding `-device i6300esb` (or `-device ib700`) so
  the guest sees a real watchdog PCI device, matching `tools/mkboot.sh`'s
  existing `-kernel`/`-initrd` boot shape.
- The exact kill command against the feeder's real pid, and the exact
  wall-clock measurement to take (time from kill to the QEMU process
  itself exiting/restarting, the same observable `tools/mkboot.sh`
  already greps a serial log for).
- What a pass looks like: a reboot inside `timeout + margin` with no
  other process intervening. What a fail looks like: the QEMU instance
  sitting motionless past that window, which is the state real hardware
  would otherwise stay in indefinitely on this container's own
  measurement (no watchdog, no `panic=`, no other backstop).

Until that measurement exists, this note's claim about the mechanism
working is a hypothesis, per `CLAUDE.md`'s own test for design
documents — named as such rather than reported as verified.

## Definition of done

This is a docs-only note. No code changes. `docs/QUEUE.md`'s new-notes
entry is updated in the same commit that lands this note, stating
plainly that the falsifying test is handed to the operator rather than
run here, and why.
