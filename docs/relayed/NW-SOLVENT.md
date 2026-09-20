# Solvent — an init that dissolves

Written 2026-09-14. My own answer to *what would the ultimate init for this
system be*, with the current design's constraints deliberately set aside.
Nothing here is a proposal against the build order. Marked where a fact was
checked or measured; everything else is reasoning, and there is a great deal
of reasoning.

The name is the thesis. Every init in existence adds a process that stands
between you and the kernel. This one is designed to disappear.

---

## The argument for dissolving

Look at what an init actually does. It arranges isolation, it starts
things, it watches them die, it restarts them, it allocates resources, and
it tells you what happened. Now ask which of those Linux cannot do itself.

Isolation: namespaces, cgroups, seccomp, Landlock. The kernel does it; the
init calls it.

Death notification: `cgroup.events` fires when the last task leaves a
cgroup. The kernel notices; the init blocks in `waitpid` to be told.

Resource policy: cgroup controllers, and since 6.12, **sched_ext** — the
scheduler itself, written as a BPF program, verified before load, running
in kernel context. *(Real, mainline, and this project targets 6.18 LTS.)*

Isolation policy: **BPF LSM** — arbitrary policy at security hooks,
verified before load. *(Real, mainline since 5.7.)*

Death tallies, restart counts, resource accounting: all counters the
kernel already maintains or could maintain in a BPF map.

So the honest question is not *what should the init do*. It is **what is
left for a userspace init to do that the kernel cannot**, and the answer
is smaller than any init admits: something must reap orphans, because
Linux requires a PID 1 that does; something must make the first policy
decision, because the kernel will not decide it for you; and something
must talk to a human.

Everything else is an init reimplementing, in userspace, with a process
per house, what the kernel offers as a hook.

**Solvent's core claim: the supervision half should not be a program. It
should be policy loaded into the kernel, verified before it runs, and
content-addressed like everything else.**

---

## What replaces what

### The supervisor becomes a map and a hook

Today: one `nwsup` per house, blocked in `waitpid`, holding a counter and
a budget. At forty houses, forty processes. At four thousand, `pid_max`.

Solvent: one BPF program attached to `cgroup.events`, plus a BPF map keyed
by house id holding `{deaths, budget, last_status}`. A house dying is a
kernel event that increments a map entry. Nothing is blocked. Nothing is
scheduled. There is no per-house process at all.

This is the modular pass's M2 taken past where it stopped. It proposed one
watcher process instead of forty supervisors and then had to reckon with
that watcher's own death being a single point of failure. **A BPF program
attached to a hook has no death.** It is not a process. It cannot be
killed, cannot leak, cannot be starved, and cannot be the thing that
failed. The single-point-of-failure objection dissolves along with the
watcher.

**Grounded in a tree fact, and it is the strongest evidence for this
design.** M1 — *split the supervisor so the persistent process holds
nothing but a counter and waitpid* — was described as the largest TCB
reduction available. Checking the tree showed the persistent parent
already does only that, and that it must remain fully privileged forever,
because the next restart child inherits its capability set at `fork`. So
the current architecture requires a permanently root, permanently
capable, permanently resident process per house, whose entire job is to
hold a number and be able to fork.

That is the thing to delete. A map entry holds the number. A hook does the
counting. Nothing is resident and nothing is privileged, because a
verified BPF program is not a principal.

### Isolation becomes one policy, expressed once

Today a house is isolated by four mechanisms applied in sequence in a
forked child: `unshare(CLONE_NEWNET)`, `unshare(CLONE_NEWNS)`, an erofs
mount plus overlay plus binds plus `pivot_root`, Landlock, then seccomp,
then `execv`. *(Checked: `nwsup.c:606-620`.)* Six stages, each with its own
failure mode, all reporting the same `_exit(72)`.

Solvent expresses the isolation for a house **once**, in the plan, and
compiles it to a BPF LSM program plus a cgroup configuration. The kernel
enforces it at the hooks. There is no sequence to get wrong, no ordering
between Landlock and seccomp, no stage to fail at, because there are no
stages — there is a policy that is either loaded or not.

What this buys beyond tidiness: a policy that is *one artifact* can be
hashed, and therefore named in the tuple. Today the lid set is a bitfield
whose meaning lives in `nwsup.c`. In Solvent the isolation of a house is a
verified program with a sha256, and *two boots ran the same isolation* is
a string comparison.

### Resource policy becomes the scheduler

`nw_res` is declared in the format, written by the baker, validated by the
checker, and applied by nothing — `nwsup.c` has zero references to it.
*(Checked.)*

Solvent doesn't apply resource limits. It **is** the scheduler, via
sched_ext: the plan's resource declarations compile to a BPF scheduling
policy. Not cgroup weights approximating an intention — the actual
dispatch decision, written from the plan, verified before load.

That makes possible the thing the resource block cannot express: *the
gaming district gets these four cores with no preemption while it is
focused, and they return to the pool when it is not*, as a scheduling
policy rather than a broker making runtime judgements. The judgement is in
the compiled policy, decided offline, and the kernel executes it.

### PID 1 becomes fifty lines

What is left: `waitpid(-1)` forever, because orphans reparent to PID 1 and
somebody must reap them. Write the boot record. Handle the shutdown
signal.

That's it. No supervision, no restart, no isolation, no resource
management, no logging. Fifty lines that can be read in one sitting and
proved with the tooling already in the tree.

**And the one signal only it can see becomes central rather than
incidental.** An orphan reparenting to PID 1 means a process outlived its
containment. Today that is `orphans_reaped++` and a console line.
*(Checked: `pid1.c:101`.)* In Solvent it is the *only* thing PID 1
observes that the kernel does not already report through a hook — which
makes it the single most valuable fact the process produces, and the
reason PID 1 exists at all beyond satisfying the kernel.

---

## The three things I would add that no init has

### 1. Generations, not slots

A/B is a two-element special case of something better. Keep **N**
generations, each a complete tuple: plan hash, kernel hash, every brick
hash, the compiled BPF policies, the desktop layout. The boot menu is
generated at bake time from the retained set.

Rolling back is not a promote-and-demote dance with a trust record. It is
booting a different generation, which is a thing that exists on disk and
was validated when it was made. The trust record shrinks to *which
generation booted successfully last*, which is a fact rather than a
mechanism.

This also gives bisect its search space directly, and it makes the
question *what changed* a diff of two tuples rather than an investigation.

### 2. The desktop is in the tuple

The radical one for a distro rather than an init. The plan should not stop
at houses. It should describe the **canvas**: which houses exist, where
they sit, which are fixed, which are grouped.

Then *my machine* includes *how my machine looks*, the layout is sealed
and content-addressed like everything else, and a generation is a complete
working environment rather than a set of processes. Rolling back a bad
update restores your desk, not just your software.

The line to hold is the one the current design already draws: a layout
names which of the permitted houses appear and where — never what is
permitted. Geometry must never grant anything.

### 3. The machine can prove what it ran, to someone else

Given a tuple, the compiled policies, the certificate the baker emitted,
and the boot trace, the machine holds a complete verifiable statement of
what it ran. Not an attestation from a TPM vendor about firmware — a
statement about the entire userspace, checkable by anyone with the
artifacts.

Nothing else can do this, because nothing else has a complete sealed
description of its own state. It is a by-product of the tuple being real
rather than a feature to build.

---

## What I would cut

**The restart budget as a supervisor concept.** It becomes a map entry and
a decider brick reading a kernel-maintained tally, which is where the
modular pass already wanted policy to live.

**The `lids` bitfield.** Replaced by a compiled policy with a hash. A
bitfield whose meaning lives in one C file is exactly the un-named,
un-hashed, un-versioned thing this design otherwise refuses.

**Per-house supervisor processes.** Entirely. This is most of the win.

**The log pipe per house.** With policy in the kernel, the interesting
events are hook events, not stdout lines. Houses still write to stdout and
somebody still collects it, but it stops being the diagnostic channel and
becomes what it should be: program output.

---

## The costs, and they are large

**BPF verifier limits are real.** Complex policy fails to load, and it
fails at load with a verifier message that is famously difficult. A
policy that will not verify is a plan that will not boot, and the error
surfaces in a tool nobody in this project has used. This is the largest
practical risk and I am not going to soften it.

**It requires kernel configuration.** sched_ext and BPF LSM are mainline
but must be enabled, and BPF LSM must be in `lsm=`. The *stock kernel,
never modified* rule survives — these are config options, not patches —
but *works on any distro kernel* does not.

**It contradicts a decision on the record.** The current design names eBPF
as the escape hatch, deliberately unused, on the explicit grounds that
every gap found so far has a stock mechanism and reaching for eBPF before
those are used would be adding a tool for problems that do not exist.
That reasoning is correct as written. Solvent's answer is that the problem
does now exist and is named in the tree: a permanently privileged resident
process per house that cannot drop its capabilities because the next
restart needs them. That is a problem no stock mechanism solves, because
it is a consequence of userspace supervision itself.

**Debuggability gets worse before it gets better.** A supervisor you can
`strace` becomes a program you cannot. The introspection has to be built
first, not after, or the machine becomes less answerable rather than more.

**And the failure mode is the project's own.** Policy verified before
load, content-addressed, reproducible — and wrong. It would be wrong
identically on every boot, with a hash attesting that it is exactly the
wrong thing you meant to load. That is the signature failure promoted to
architecture, which is also true of the compiled-boot design, and I
suspect it is what *any* maximally-sealed design converges on.

---

## What I actually believe

If I were starting this system today with no constraints, I would build
Solvent, and the reason is a single measured fact rather than an
aesthetic.

The current architecture requires a permanently root, permanently capable,
resident process per house whose only job is to hold an integer and retain
the ability to fork. That is not a design choice anyone made — it is what
userspace supervision costs, discovered by reading the tree. Every other
part of this system is sealed, hashed, validated offline and named in a
tuple. That one part is a privileged process holding a number, and it is
per house, and it scales with the thing the design most wants to scale.

The kernel offers a hook that does the same job with no process, no
privilege, and no death. Not using it is defensible today because the
alternative is unproven. It would not be defensible in a design starting
from nothing.

## Falsified by

A BPF policy expressing the current lid set that the verifier rejects,
which would end the isolation half. A `cgroup.events` hook that cannot
distinguish the deaths a restart budget needs to count. A sched_ext policy
that cannot express *these cores, exclusively, while focused* without a
runtime broker. A generation set whose disk cost makes N=2 the only
practical value, which would make generations a rename of A/B. And the one
that matters most: an operator who cannot find out why a house did not
start, because everything that used to be a process is now a program in
the kernel and nothing was built to ask it.
