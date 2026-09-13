# NW-EXPECTATIONS-UNANCHORED

**Status: unanchored. Not a design. Not a queue.**

This document records a design review between two agents on an unconstrained
init design. The label matters more than the content.

**Provenance.** Written by one agent from a review conducted with another.
Every verification appearing in this document was run by the reviewer and
none by the author. Where a count or a check is cited, it is the reviewer's
measurement.

## Why this is labelled

This is not a design. It is a set of expectations, each recording what would
have to exist for it to be checked.

The condition that makes the label necessary, stated precisely: nothing
currently in the tree can disagree with any sentence in this document, which
means nothing in the tree can confirm one either. That is the point at which
a specification stops being a claim about the world and becomes a claim about
itself.

This is a general test, applicable to any specification, and it is not "how
far ahead of the implementation is it." It is: could any existing thing
falsify a single sentence of it. Right now, none can.

The supervision half does not exist. Every conclusion below is a claim about
an unwritten mechanism. The value of this record is the reasons, not the
conclusions — the reasons are what get lost when implementation starts and
someone reaches for a conclusion without them.

The next step is the supervision half. Not more design. Do not implement
anything from this document. Do not treat it as a queue. The queue is
unchanged: the fold helper, then resource blocks — and disk capacity now
belongs in that block, since bandwidth is not capacity.

## Three corrections to the design as written

### 1. "The machine's state is small and complete" is wrong as stated

It is a description of the model rather than of the machine. A layer
accumulates a cache, a lock file, a generated credential, a database. Saying
that state does not exist is worse than documenting it, because an
acknowledged escape hatch is a place to look and an unacknowledged one is not.

The claim that survives is: **a container's state is bounded to one directory
whose path is known.** That is a real property and a much smaller one.

Sourced comparison that produced this, corrected mid-review: NixOS does not
document `/var/lib` as escaping its declarative model. It does document that
`/var/lib/nixos` holds the state making declaratively-managed UIDs stable
across reboots. That is a declared thing depending on an undeclared file, and
it is the failure in its sharpest form — not "some state escapes" but "a
declared thing depends on an undeclared thing."

(Recorded from recollection, corrected in review. The precise claim is a
documented dependency on specific state under `/var/lib`, not an
acknowledged escape of the whole directory. The distinction between official
documentation and a known consequence of how a tool works was drawn during
review and should be preserved: only the first is a promise someone made.)

### 2. Resource exhaustion has no bound in the tree

Verified during review by the reviewer: nothing in the tree bounds a layer's
size. The resource block designed shortly before this review carries CPU,
memory throttle, memory backstop, disk bandwidth, and priority. Disk bandwidth
is not capacity. A container can fill the machine without violating its plan.

First thing to add. Disk capacity belongs in the resource block.

### 3. No monotonic identity across boots

When did this change and how long has this been running are both unanswerable
as designed. Basic questions for a desktop. Not addressed by anything below.

## Six expectations

Each records what would have to exist for it to be checked.

### E1. Two modes with a promote-defined boundary

Invisibility needs a baseline and change destroys the baseline, so two modes —
but the boundary is not a user-declared switch. It is already an event: stable
whenever running a promoted slot, change between staging a candidate and its
promotion. The last known-good boot is the last promoted slot, durable and
hash-named and robust to arbitrary change rate because promotion is an event
rather than an inference.

*Falsifiable when:* promote exists and a boot can be classified against it.

### E2. Kernel-observed death, never inferred

A death is discrete if the kernel says so and a threshold if the init decides
how long silence means death. Those had been merged in the reviewer's own
phrasing and the merge was conceded.

The relation already exists. Verified during review by the reviewer:
`pid1.c` has twelve hits for SIGCHLD, waitpid and signalfd; `nwsup.c` has four.
The init is the parent, the kernel delivers the exit, the supervisor blocks in
`waitpid`.

Parenthood is not a channel, so the no-channels rule does not forbid this. What
forbids polling is that polling is inference from absence — not a channel
objection, and citing the channel rule for it would be citing the wrong rule
for the right conclusion.

*Falsifiable when:* a runtime surface exists and can be shown to report a
death the kernel delivered rather than one the init deduced.

### E3. Two sentences, not two thresholds

A modeled-over-budget death and an unmodeled death differ in what the init
*has*, not in how confident it should be.

- Modeled carries a comparison: the plan said N, this is N+1.
- Unmodeled carries a bare fact plus the diff to the last promoted slot.

The absence of the comparison is the uncertainty signal. No confidence level is
assigned, because a confidence number is a threshold wearing a percentage.

Error asymmetry, stated because the two cases require opposite handling:
unmodeled deaths are urgent and hard (the plan is silent, so there is no second
side to the diff); modeled-over-budget deaths are less urgent and easy (the
plan gives both sides). The init cannot use the same threshold or the same
confidence for both. Holding them apart is two different sentences, not two
thresholds.

*Falsifiable when:* a runtime surface exists and its output form can be shown
to differ between the two cases by the presence or absence of a comparison.

### E4. The kind field improves only the declared fraction

`blob.h` already declares two of three lifetime cases via a kind field:
`NW_KIND_ONESHOT` (exit 0 completes, never restarted) and `NW_KIND_LONGRUN`
(any exit unexpected, including 0). This was added because exit 0 was carrying
two opposite meanings distinguished only by which integer.

The real gap is narrower than "no lifetime model." A longrun house with a
budget conflates two meanings:

- deaths within budget are normal, and
- any death is a violation, the budget is courtesy.

One field closes that. It does **nothing** for the house the plan author never
thought about. That case surfaces as the boot answer relocated: "no plan model,
here is the diff to the last promoted slot." The field must not be credited
with solving the undeclared case.

*Falsifiable when:* the field exists and a plan can be written where the two
meanings are distinguished, and a death under each produces the expected
surface form.

### E5. Every surface names its basis

Plan hash, machine identity, timestamp. An answer without its basis is a claim,
and a stale answer should announce itself rather than being confidently wrong.

This also closes a smaller problem: if a house moves between kinds across a
plan edit, the form of the sentence changes while the machine does not, and a
representation change can be read as a state change. Naming the basis prevents
this — a sentence that says which plan hash produced it cannot be mistaken for
a change in the machine, because the basis changed and says so.

**Marked as the earliest to become falsifiable.** It is a property of the
output format, and the output format is the first thing the supervision half
produces. Test it first.

*Falsifiable when:* the output format exists and every surface can be checked
for a basis field.

### E6. The floor below the console is a person

Any notification channel is itself a house, so the surface for a death cannot
depend on a house that might be the one that died. This is a property rather
than a role, and it tells you what the console house must be: reachable when
nothing else is, not dependent on the session, and its own death is the one
thing the init cannot rely on it to report.

But the init's own relationship to the user is not a surface — the init writes
to a pipe or a console device and something must be reading. Below the console
house there is no surface, only a person power-cycling and reading a boot
record, which is not a notification.

The console house is the last *active* surface, not the last surface. Naming
the human floor as a notification, or leaving the chain appearing to terminate
in something active, is the same class of error as the invisible state.

*Falsifiable when:* the console house exists and its liveness can be shown to
be independent of the session and of every other house.

## Two objections that stand and were not resolved

### O1. Query tool: plan-answering and machine-answering are different things

A query tool answering from the plan and one answering from the machine are
different things. The plan can say a container *should* see a path; only the
kernel can say whether the mount happened.

Resolution taken: the query tool is output modes on the existing checker,
answering from the plan only and saying so. That resolution is accepted. The
consequence is that a machine-answering tool is a separate thing that does not
exist. It is not retired by the resolution; it is placed outside the design.

### O2. Attention is a tax and the capability set is built on pull

A desktop is something a person uses while doing something else. Every
capability requiring inspection is a tax on that person.

This objection is fatal to a capability set built on pull. It is what forced
the two-mode resolution (E1). It has **not been retired**, only routed around.
If the two-mode boundary does not hold in practice — if the system is in change
mode more often than stable mode, or if the stable mode is not actually silent
— the objection returns and the design is unaddressed by it.

## Closing note on method

The review's own constraint was: do not report having run anything not run, do
not present reasoning as measurement, and if naming a limitation of another
system, name where it is known from. The review violated this once — a NixOS
limitation was stated as if sourced when it was recollection — and the
violation was caught by the other agent and corrected in the document above.
The pattern is recorded here because it is the failure mode this project
spends most of its time on, and it arrived inside a critique whose closing
section was about not doing it.
