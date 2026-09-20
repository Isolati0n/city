# The compiled boot — an init with no interpreter

Written 2026-09-14. A ground-up design, not a proposal against the current
build order. Nothing here is decided, scheduled or dispatched, and nothing
in the tree can contradict it yet — which by this project's own rule makes
every claim below a statement about itself until something can. Marked
where a tree fact was used; everything unmarked is reasoning.

## The inversion

Every init ever written is an interpreter. It reads a description at
runtime and decides what to do with it. systemd parses unit files and
resolves a dependency graph while booting. `nw-init` parses far less and
resolves far earlier, but PID 1 still walks a blob and branches on what it
finds.

Remove the interpreter. **The plan compiler emits the boot itself** — a
flat, pre-resolved sequence of actions in which every name is already a
number, every path already an offset, every conditional already taken. Not
a description of what should happen. A tape of what happens.

PID 1 becomes a tape head. It does not parse, because there is nothing to
parse: the actions are fixed-width and were validated offline. It does not
branch, because the branches were taken at bake time. It does not know any
names, because names are a bake-time concept. Perhaps four hundred lines,
and provable in a way a nine-hundred-line PID 1 will never be.

Everything interesting moves offline, where Alloy, TLA+ and CBMC already
sit in the tree waiting for something worth pointing at.

## Five things that follow

### Capabilities are descriptors, not paths

A house starts with an empty mount namespace and a set of open file
descriptors it was handed: `O_PATH` for directories, sealed `memfd` for
content, `pidfd` for processes it may signal. It cannot *name* anything.
There is no path to traverse, no string to validate, no `..` to defend
against.

A grant stops being text in a plan that lids must enforce and becomes a
descriptor that either is in the house or is not. Enforcement moves from a
runtime check to an absence.

**Grounded in a tree fact.** `blob.h` already makes this argument for one
field: the plan carries a brick *hash* rather than a path, because a path
is a free-form string in the TCB's input path and `path_ok_len` had to
defend it — a brick that traversed out with `..` baked clean, passed
`nw-check`, booted, and logged `lid brick` while rooted on the machine. A
fixed-width hash **cannot express** a traversal. This design applies that
one argument to everything instead of to one field.

### The init is self-hosting

Dawn, the spawner, the logger, the checker: all houses, all sealed, all
content-addressed, all named in the tuple that identifies the running
system. The trusted base is the tape head and nothing else.

Replacing the supervisor becomes a plan change rather than a rebuild,
which is what modularity was supposed to mean and currently does not.

### The plan carries a proof

The baker emits the blob **and** a machine-checkable certificate that this
blob satisfies the invariants. The boot-time check verifies the
certificate rather than re-deriving the properties.

Verification is cheap and total. Re-derivation is expensive and partial,
and it is where twenty-six error codes and a nine-hundred-line checker
come from — a tree fact: `nw-check` returns 26 distinct refusal codes
today, each a hand-written re-derivation of something the baker already
knew.

Proof-carrying plans. Nobody ships this for system configuration, and this
is the one project structurally ready for it, because the models already
exist beside the code and prove things about a design rather than about an
artifact.

### Shutdown is the tape run backwards

Every action has an inverse. Mount has unmount, spawn has signal-and-wait,
bind has unbind. The compiler emits both directions from one description.

Ordered shutdown stops being a hand-written stage that must be kept in
agreement with startup. It is the same artifact reversed, and it cannot
drift, because there is only one of it.

### Two boots of one tuple produce identical traces

Not *the same houses came up*. The same actions, in the same order, with
the same arguments, byte for byte.

The boot record stops being a log and becomes a diff target. What differed
between boot 41 and boot 48 is a diff of two traces, and the answer is a
line number rather than an investigation.

## The capability that exists nowhere else

**The machine has a total function, and it can be evaluated without the
machine.**

If the boot is a compiled tape and every action is explicit, then given a
plan and a *description of a machine*, the tape can be executed
symbolically to yield the entire boot's outcome without booting. Not a
simulation — an evaluation.

Which houses start. Which do not, and why. Peak descriptor count, exactly.
Total memory committed. Whether it refuses on this kernel.

The pieces exist. The provenance rule already splits plan properties from
machine properties, and the fd pre-flight is a hand-written instance of
precisely this evaluation for one resource. Generalise it and *will it
boot over there* becomes a question with an exact answer, computed at bake
time, on a laptop, for hardware nobody owns yet.

No operating system can answer that today. This one could, because it is
the only one with a compile step and a clean travel/don't-travel split.

## What it costs

**The tape is bigger than the blob and harder to read.** Provability is
gained; eyeballing a plan is lost. For a machine whose thesis is *optimise
for being changed*, losing human readability of the central artifact is a
real cost and not obviously worth it.

**Compile times rise, possibly a lot.** The bakery becomes the thing that
must be fast, and the bakery is currently Python.

**Descriptor-only capabilities require every program to accept being
handed its world** rather than opening it. Most Unix software cannot. A
shim would be needed, and the shim is a new trusted component that partly
undoes the win — the honest version is that this is achievable for houses
the project builds and not for arbitrary third-party software.

**And the structural risk, which is the real one.** Nearly everything
moves into the compiler, so the compiler becomes the system. If the
compiler is wrong, it is wrong in a sealed, content-addressed,
reproducible way, and the failure mode is a machine that is confidently
and identically broken on every boot.

That is this project's signature failure — a confident true-looking
artifact beside a thing that does not work — promoted from a defect class
to an architecture. Worth building anyway, but that is the trade, and it
should be written down before anyone starts rather than discovered.

## Falsified by

A tape action that cannot be inverted, which would mean shutdown is not
the reverse and the two must be maintained separately after all. Two boots
of one tuple producing different traces on the same machine. A symbolic
evaluation that disagrees with a real boot on the same machine
description. And the one that would sink it: a certificate the boot-time
verifier accepts for a blob that violates an invariant, which would mean
verification is not cheaper than re-derivation, only shorter.
