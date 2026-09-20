# The VM-as-house ideal — record, 2026-09-14

**Status: not decided, not scheduled, not dispatched.** Nothing here is in
the tree. It extends `NW-FROM-SCRATCH-RECORD` with the session that followed
it, and where the two disagree this one is later.

Almost everything below is reasoning. Where a fact was checked or measured
it says so, and the proportion is worth noticing: the earlier record rested
on eight measurements and this one rests on almost none. That is not a
criticism of it — it is where it sits.

---

## 1. The central move: collapse the category

The design has two kinds of thing. A **house** is one root, one brick,
sharing the machine kernel. A **district** is a house running QEMU with its
own kernel. They are separate kinds with separate rules, and every rule has
to be checked against both.

**The ideal: whether something runs a foreign kernel is an implementation
detail of a house, not a different kind of thing.**

A house declares what it needs. If what it needs includes a foreign kernel,
the supervisor runs a VMM instead of an `exec`. Same plan entry, same
budget, same grants, same place on the canvas. There is no separate VM
management surface — no `virsh` beside the process list, one tree, one
inspection path.

This is stronger than the three ground-up designs in the earlier record,
and for a specific reason: those improve a *mechanism*, this removes a
*category*. You never again ask whether a rule applies to districts too.

It was tested twice in conversation and held both times. A Bazzite district
running Steam and a Fortran house running a solver turn out to be the same
plan entry with different insides — which is exactly what the collapse
predicts.

### The rule that falls out

**A house unless it needs a different kernel.** The second kernel buys
exactly one thing: a kernel bug cannot cross the boundary. Everything else
about a VM — the device models, the boot, the memory duplication, the
double scheduling — is cost paid to get that one property.

Houses: games, browser, editor, terminal, emulators, Fortran models.
Anything that is a Linux binary.

Districts: Windows. Another distro entire. A different kernel version. And
anything genuinely untrusted, where a kernel bug escaping would own the
machine.

### One thing this does *not* change

A house is still not one process. `NEXUSWEAVE.md` §6 is deliberate: *one
root, one brick, several programs inside, isolation between workflows
rather than within them.* House-per-process was considered and rejected on
measured grounds, below.

---

## 2. What was taken from Genode, and what was not

### Taken

**Resource budgets handed down, not declared.** A parent allocates from its
own quota and therefore cannot over-commit. Your plan declares numbers side
by side and nothing checks they sum. Under this, a district's houses draw
from the district's allocation and a plan that over-commits is refused
offline. The total becomes knowable from the plan.

**Nesting as the default shape.** Recursive construction where every node
can contain another with its own budget. Districts-containing-houses stops
being a feature and becomes the tree being a tree.

**Refusal rather than warning.** A component whose routes do not resolve
cannot start. You have 26 refusal codes; what is missing is coverage — a
lid set that makes a declared limit unenforceable, a socket the dependent
cannot reach, a descriptor budget the house count exceeds.

**Capabilities, not paths.** Already argued in `blob.h` for one field: the
plan carries a brick hash rather than a path, because a fixed-width hash
*cannot express* a traversal. `nwcheck.c:109` names the two fields that
have not had the fix. Audited separately; two changes, not seven.

**Nothing ambient.** A house's disk, network and display are routed to it
rather than found. Which makes *what can this reach* a property of the
description.

### Not taken, with reasons

**The microkernel base.** Genode is a framework on a microkernel; Linux is
monolithic with ambient authority. There is no merge — you cannot add
capabilities to a kernel that grants everything and then subtracts. The
ideas port; the kernel does not.

**Isolated drivers.** Genode's DDE-Linux runs real Linux driver code as
unprivileged userspace components — a crashing GPU driver is a component
that crashes. This is genuinely better and it is the one property most
wanted. It is not taken because nobody has ported AMDGPU, which has the
widest kernel API surface in the tree, and Genode Labs have i915 and not
AMD after seventeen years.

**The live construction plan.** Sculpt 24.10 exposes its data model
directly and lets components be rewired on the fly. This design seals the
plan and adopts at reboot. Both coherent, opposite answers; the sealed one
is kept.

### A correction recorded, because it was mine

I argued the Genode-plus-driver-domain architecture does not futureproof
you, because the churn moves into a Linux domain you would still maintain.
**That was aimed at Xen's shape and applied to Genode's, and they are not
the same.** Genode has no dom0. DDE-Linux is a porting cost per driver
family, not a second distro with a package manager and an init.

The objection that survives is narrower: the porting cost is real and
per-family, and AMDGPU is the largest instance of it.

---

## 3. Where a district actually sits, and what it costs

**On Linux with KVM, a district is next to the base kernel, not above it.**
KVM is *in* the kernel. When a vCPU runs, the CPU is in guest mode executing
guest instructions directly. The host is not on the path until an exit.
QEMU sits beside the vCPU handling device I/O; it is not between the guest
and the silicon.

**On Genode it is one layer above, permanently, by design.** An exit traps
to the microkernel, routes to a VMM component in userspace, is handled, and
the guest resumes — an extra context switch and an IPC per exit. For
exit-light workloads the difference is small; for device-heavy ones it is
structural.

That is a real argument for Linux underneath if performance is the
priority, and it cuts against the Genode thread rather than for it.

### The remaining gap is the device path, not the hypervisor

Pin each vCPU 1:1 to a physical core and isolate those cores, and double
scheduling and lock-holder preemption stop arising. AVIC delivers
interrupts without exits. Nested paging means memory access needs none.
Pass the USB controller through and input hops disappear.

What is left is GPU command submission. With native context the guest
submits through a virtio transport the host translates — that hop is the
price of the host keeping the card. With passthrough it disappears and the
district owns the GPU.

**Two configurations, not one.** Native context: compositor keeps the
screen, accept a submission hop. Passthrough with pinned cores and AVIC:
essentially bare metal, district owns the card.

The genuine gap in Linux is **gang scheduling** — treating a VM's vCPUs as
one schedulable entity. Linux does not do it. Everything else above already
exists and is unwired.

---

## 4. House-per-process: considered and rejected

Proposed for maximum observability and control. Rejected on three grounds,
two of them measured.

**It reverses a settled decision.** §6 says isolation is between workflows,
not within them.

**The numbers.** Measured house ceiling: 510 pre-patch, 1018 after the
interleave. A browser is roughly one content process per tab plus
infrastructure — thirty tabs is forty houses before anything else runs.

**Loop devices. Checked.** `CONFIG_BLK_DEV_LOOP_MIN_COUNT=8`, and
`nwsup.c:118` already records the failure: *two brick houses produced one
`FAIL loop configure errno=16` on every run, hidden because the default
budget absorbed the restart. At 8 houses several never ran.* Every brick
house needs a loop device.

**And a fact about software.** Browser subprocesses share memory for
compositing and depend on their parent's IPC. Separate mount namespaces per
renderer and the browser does not work.

### What the goal actually needed

The goal was *see and act on a single process separately*, and it does not
require house-per-process. `cgroup.procs` lists every process in a house
and the controllers report per-process. Isolation granularity is what costs
loop devices and descriptors; observation granularity is nearly free.

**What is missing is the reader, not a smaller house.** Nothing displays
what a house contains. That is the boot record's unspecified read side and
the registry viewer, item 8, unbuilt.

### Two observation levels, and they are genuinely different

**Inside a house**, processes are yours — in your cgroup, in your `/proc`.
Per-process visibility is free.

**Inside a district**, processes belong to a foreign kernel. The honest
surface is the wrapper: running or not, resources committed, whatever the
guest volunteers. Reaching further means an agent inside the guest, which
is a channel into an untrusted thing.

---

## 5. Audio, as a grant

Three ways a district gets sound, in descending order of preference.

**Shared through a virtual device.** virtio-sound, host daemon mixes. Card
stays with the host, every district can have audio at once. This is the
native-context answer applied to audio.

**Exclusive passthrough.** The district gets the real PCI controller or a
USB interface. Bit-exact, lowest latency, takes the device off the rest of
the machine. Right for audio production, wrong for anything else.

**A dedicated USB interface per district**, which is what people actually
do when they want both.

Under the collapse, this is just another grant: a house declares
`audio=shared` or `audio=device:<id>`, and `nwcheck` refuses a plan where
two things claim exclusive use of one device. Offline-checkable, which no
distro can do.

Two hard parts, hard everywhere: **latency**, where every layer costs
milliseconds and shared mixing is fine for games and bad for live
monitoring; and **hotplug**, which is the same unsolved problem as the USB
drive — the plan is sealed and cannot name a thing that did not exist. Still
open.

None of this exists. Not the host stack, not the daemon, not the field.
**Audio is the gate in front of the entire desktop**, gaming included.

---

## 6. Gaming, and the tournament case

Tournament-grade is about **jitter**, not throughput.

**What this architecture offers that a distro cannot.** There is no
background anything — not "I disabled the indexer", but nothing exists
unless the plan names it, which removes the largest source of frame-time
outliers structurally. `cpuset` gives a house exclusive cores; the guest
kernel exposes `cpu io memory` as delegable, **measured**. Mesa, Proton,
kernel and plan are a hash tuple, so *what changed* is a diff. A bad update
is one reboot back.

**Three things that would need building**, in order of effect: IRQ affinity
so GPU and USB interrupts land off the game's cores; direct scanout in
driftwm so fullscreen bypasses composition; a persistent shader cache,
which needs scratch-becomes-saved.

**The ceiling.** None of it touches panel latency, peripheral polling, or
network jitter — which usually dominate. What it buys is the variance under
your control.

**Anti-cheat**, set aside for the performance discussion and restated here:
EAC and BattlEye refuse to run in a VM, Vanguard loads before Windows. That
is vendor policy and no engineering changes it. Where a publisher has
enabled Proton support, the game runs in a house with no VM involved.

---

## 7. Fortran and numerical work

A house, not a district — Fortran is a compiled binary and needs no foreign
kernel. Bake a brick with the compiler runtime, BLAS, MPI and the model.

**Why the sealed model suits it unusually well.** Reproducibility in
scientific computing is a real and painful problem: results shift because
BLAS changed or flags moved. Here the run is a tuple of hashes, and
re-running it two years later is booting that generation. No module system
on a cluster gives that.

Two specifics: **threading must match the pinning**, because OpenMP and MKL
read the visible CPU count and often see the host's inside a cgroup — set
`OMP_NUM_THREADS` to the cpuset or they oversubscribe. And **MPI across
houses is a network question**, which has no design at all.

One open idea worth keeping: whether a house could declare huge-page and
NUMA policy in the plan — offline, checkable, reproducible. `cgroup.hugetlb`
exists and the resource block already has the shape.

---

## 8. Driver isolation: wanted, and declined

This was the longest thread and it ended by narrowing what was actually
wanted.

**What Linux can isolate today.** Any device you can pass through — a USB
controller, a NIC, an audio interface — goes in a district. Its driver runs
in a guest kernel; when it panics, the guest panics and the machine does
not. The IOMMU stops it DMAing over your memory. That is real, and it needs
nothing new.

**What it cannot.** The GPU. Pass it through and the district has it, so the
compositor does not. There is no arrangement where the GPU driver is
isolated and the host still renders the desktop.

**And most of the kernel is not drivers.** ext4, the block layer, the
network stack, the scheduler — a bug in any is a kernel bug on the machine.

### The wish came apart into two halves

**Organizational:** *every device in this machine is a named thing in the
tree, with a budget and declared grants.* This does **not** need kernel
enforcement. The plan could name the drivers it expects and the devices they
claim, checked offline, refused when the machine does not match. Cost: a
plan field and a check.

**Structural:** a driver fault is contained. Cost: the AMD port.

The halves separate cleanly and only one is expensive.

### Why the structural half was declined

**Rollback covers regressions; isolation covers faults.** A bad driver
update is a reboot into the previous generation with the change identified
as a hash diff — the common case by a wide margin. A driver that hangs after
three hours hangs on every generation, because it is a bug in code rather
than a bad update.

Regressions are frequent and solved. Faults are rarer and unsolved.

**And containment's value scales with distrust.** On a machine with tenants
it is the product. Here every house is yours and there is no adversary
inside; the worst outcome of a GPU fault is that you lose your own session.

**The operator has never had a GPU driver crash.** That is the deciding
fact. This is not ideal in an absolute sense; it is proportionate.

**Districts remain the escape hatch.** Containment is available
per-workload where it is judged warranted, rather than paid for everywhere.

---

## 9. What to build instead, and it is small

A GPU driver fault usually does not kill the kernel — **it kills the
compositor**, because driftwm holds the DRM context. The houses are still
running, their processes alive, their memory intact. What was lost is the
thing drawing them.

So the question becomes whether the compositor can die and return without
taking the session. driftwm already has both pieces: the session file
records which houses were open and where they sat, restored **dormant with
nothing auto-launching**; and `suspend-window` replaces a window with a
compositor-drawn stand-in that brings the app back in the same place.

With the start/stop channel — already approved — a compositor crash becomes:
driftwm dies, PID 1 restarts it, the canvas returns with everything in
position, surviving houses reattach, and the rest are stand-ins you click.

Not a preserved session. A recovered one, built from features that exist
plus one already approved.

**And record it.** The compositor is a house; PID 1 reaps it; that is a
boot-record entry with a name. *The display died* becomes attributable
rather than inferred from a black screen. **Adopted.**

---

## 10. Where this sits

Ranked by what actually threatens a session:

1. An update breaks something — **solved**, by generations.
2. A house dies and you do not know why — **partly**; the boot record is
   specified, the read side is not.
3. The compositor dies and takes the canvas — **solvable**, §9, from
   existing parts.
4. A kernel panic — rare, nothing helps.
5. Driver isolation — fifth on a list of four.

The last hour went to driver isolation, tenant threat models and fault
containment. None of those is on the operator's list. They entered because
Genode has them and Genode came up, and that is worth recording so a later
reader does not mistake the length of the thread for its weight.

## What none of this establishes

Everything in §1 through §8 is reasoning. The VM-as-house ideal has met no
measurement. The two configurations in §3 have not been compared on this
hardware. The audio design has no implementation to test against.

The measured facts it leans on are all from earlier: the 510/1018 house
ceiling, the loop contention already recorded in `nwsup.c:118`, the
delegable cgroup controllers, and the kernel configs.

Nothing here has booted on real hardware.
