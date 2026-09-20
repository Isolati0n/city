# The answerable machine — an init that records causes, not events

Written 2026-09-14. A second ground-up design, on the same footing as the
first: not decided, not scheduled, not dispatched, and nothing in the tree
can contradict it yet. Where a tree fact or a measurement is used it is
marked; everything else is reasoning.

The compiled-boot design optimises for provability. This one optimises for
a different thing entirely — a machine that can answer questions about
itself — and the two are not the same design with a different emphasis.
They disagree, and where they do it is noted.

## The inversion

Every system that records anything records **events**: a sequence of
things that happened, ordered by time, correlated afterwards by a human
who knows what to look for. Logs, journals, ring buffers, traces. All the
same shape. The reader's job is to reconstruct causation from adjacency,
and that reconstruction is where the skill is and where the errors are.

Record **causes** instead. Every entry names the entry that caused it. The
record is a directed graph rather than a sequence, and *why did this
happen* is a traversal instead of a correlation.

Two consequences fall out immediately, and they are the design.

**An entry with no cause is a root cause.** Not by analysis — by having no
parent. The set of root causes in a boot is computed, not diagnosed. That
is the entire content of what an operator usually spends an evening
producing.

**An entry that cannot name its cause cannot be written.** The cause field
is not optional. A component with something to say and no basis for saying
it is physically unable to say it, which makes the class of confident
unjustified statement *unrepresentable* rather than discouraged.

That second one is the point. This project's characteristic failure, in
its own words, is a confident true-looking sentence beside code that does
not do what it says. A record format in which unjustified statements
cannot be expressed attacks that failure at the level of the type rather
than the level of the reviewer.

## Provenance is a type, not a convention

Every value the machine holds is a triple: the value, how it was obtained,
and when.

Three bases, and only three. **Stated** — it came from the plan, sealed
and offline. **Observed** — it was read from the kernel at a named moment
and may be stale. **Derived** — it was computed from other values, and the
derivation names them.

There is no fourth basis and in particular there is no bare number. A
value without a basis will not typecheck, will not serialise, and cannot
cross a component boundary.

**This is the day's evidence made structural.** Every significant failure
found on 2026-09-14 was one value crossing a boundary without its
provenance:

- `DRIVER_OK` on virtio-blk means *a guest kernel reached driver init*.
  It was read as *the district is up*. Measured: a guest at a kernel panic
  screen reports all four status bits, with QEMU reporting `running`.
- `_exit(72)` from a supervisor child means *some lid stage failed*. Every
  stage exits 72, so it was read as nothing at all.
- `NW_FD_RESERVED = 8` was chosen. `rlim_max` was measured by `getrlimit`.
  They sit in one comparison and look identical.
- A test reporting `SKIP` means *no evidence*. Eight of them were read as
  *not applicable*, and the Landlock test had never executed anywhere the
  project ran.

Four instances of one bug. The basis rule already exists in this design —
F1 says a running-state answer is labelled observed-from-the-kernel rather
than stated-by-the-plan — and it is applied in two places. Applied
everywhere, as a type, none of the four is expressible.

## The plan and the machine are the same object

Today the plan is one artifact and the record is another, and comparing
them is a human act. Put both in the graph, distinguished only by basis:
the plan's entries are **stated**, the boot's are **observed**, the
comparisons between them are **derived**.

The queries this makes uniform were previously six separate features:

*What was supposed to happen* is a filter on stated. *What happened* is a
filter on observed. *What diverged* is a join, which is one operation
rather than a capability. *What has never happened* is a stated entry with
no observed descendant across the retained history — the dead-declaration
census, for free. *Whether the predicted consequence matched the observed
one* is the same join at a different depth.

None of these is a new mechanism. They are one relation queried five ways,
and the reason they read as five features today is that the plan and the
record are two artifacts nobody can join.

## Why-not is answerable, and nowhere else is

Every diagnostic system answers *why did this happen*. Almost none answers
**why did this not happen**, because absence has no event and an event log
has nothing to hang an answer on.

Here it does, because absence has exactly three causes and the graph
distinguishes them:

**It was never named.** No stated entry exists. The answer is a fact about
the plan, available offline, before boot.

**It was named and refused.** A stated entry with a derived refusal
attached, produced by the offline checker — which knows the unit, the
field and the rule, and today prints one word. *(Tree fact: a plan with an
invalid house name produces the complete output `name`.)*

**It was named, accepted, and did not come up.** A stated entry with an
observed absence, which is the attestation case the design already has.

*Why is there no sound?* returns one of: the audio house is not in your
plan; it is in your plan and the plan was refused for this reason; it is
in your plan and it did not start, here is the stage it died at. No other
operating system answers that question at all, and the reason this one can
is that it has a sealed total description of what was supposed to exist.

## Replay, and the counterfactual

If the boot is deterministic given its inputs, and every external input is
in the graph with a basis, then the machine's history is **re-executable**
rather than merely readable.

*Why is the machine in this state* is answered by running the boot again
from the recorded inputs. And the interesting form: run it again with one
input changed. *Would it have come up if the disk had been larger?* is not
a guess.

This is where the two designs meet. The compiled-boot tape is exactly what
makes replay tractable — a fixed action sequence with no runtime branching
is a thing you can re-execute — and symbolic evaluation over a machine
description is the counterfactual with the machine replaced by a
description of one. Neither design needs the other, and both are better
with it.

## Where this design disagrees with the current one

Stated plainly, because a design that quietly contradicts a settled
decision is worse than one that argues against it.

**It needs more in PID 1 than the current design permits.** A causal graph
has a variable number of edges. Building one requires allocation, and
`CLAUDE.md:32` bars allocation, parsing and recursion in PID 1 after
start. *(Tree fact.)* Either the graph is assembled outside PID 1 from
fixed-width entries that carry a parent id — which is achievable and is
the version I would build — or the constraint moves, and the constraint is
load-bearing for reasons that have nothing to do with introspection.

Fixed-width entries with a parent id are the honest compromise: the writer
stays dumb and the graph is a read-side derivation. That also puts the
whole design on the far side of the seam the current boot record has
already identified as unspecified.

**Cause attribution can be wrong, and a wrong cause is worse than none.**
An event log that says only *these things happened* is never misleading
about causation because it never claims any. A graph that names the wrong
parent produces a confident false explanation — this project's signature
failure, arriving through the mechanism built to prevent it. The mitigation
is that a cause must be **known** to the writer at the moment of writing,
never inferred by proximity: PID 1 knows which house it spawned and which
supervisor it reaped, and where it does not know, the edge is absent
rather than guessed.

**Replay requires capturing all nondeterminism**, and the honest position
is that a real machine has sources nobody enumerates — timing, device
enumeration order, entropy. Replay is exact for the compiled part and
approximate for the rest, and a design that claimed otherwise would be
making the claim this document exists to make unrepresentable.

## Falsified by

An entry written with a guessed parent. Two replays of one recorded boot
diverging. A value crossing a component boundary without a basis, which
would mean the type is a convention after all. A *why-not* answer that
cannot distinguish never-named from named-and-refused. And the sharpest
one: any question an operator asks that the graph answers **confidently
and wrongly**, because the entire argument for causes over events is that
the wrong answer becomes impossible rather than merely rarer.
