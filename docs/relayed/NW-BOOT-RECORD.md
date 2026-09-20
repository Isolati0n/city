# The boot record — format before mechanism

Written 2026-09-14. Not in the tree.

**Revision note, and it is the most important thing in this file.** The
first draft was written without Addenda 3 and 4 of
`NW-UNCONSTRAINED-DESIGN` in hand, and contradicted both. It reintroduced
a guessed constant Addendum 3 had already rejected, omitted a field
Addendum 3 had already established as permitted, invented an event set
where Addendum 3 had specified one, merged two fields Addendum 3 had
separated, and specified only the write side — which Addendum 4 calls
*the easy half*, and which it had already predicted a reader would mistake
for the whole. Two independent reviews passed over the draft without
catching any of it, because neither reviewer had those addenda either.

Each contradiction is marked below where it occurs rather than quietly
fixed. The recurrence is the finding.

## The constraint, restated because it is the whole design

PID 1 must write it: nothing else knows what spawned and what died. PID 1
does no allocation and no parsing after start. That forces fixed-size
records to a pre-opened descriptor, and the constraint improves the
artifact — a format that cannot grow cannot grow into one that needs
parsing to write.

Addendum 4's correction to that framing stands: this argues the **write**
side only, and appending is the easy half. See *The half this document
does not specify*.

## Record

```
boot       uint32   boot counter, this boot
seq        uint32   record index within this boot, from 0
boottime   uint64   CLOCK_BOOTTIME ns since this boot began
event      uint8    event type
reason     uint8    reason code, enumerated per event type
err        int32    errno, or 0 where no syscall failed
name       char[32] NW_NAME_LEN, the unit; all-zero for city-scope records
wall       int64    seconds since epoch, LABEL ONLY, never ordered on
```

Plus one `BOOT_OPEN` per boot carrying the plan hash — 32 raw bytes from
the header field `NW-PLAN10-BUMP` adds. Not repeated per record: it is
constant for the boot and `boot` already joins them.

**`boottime` was missing from the first draft,** which listed *how long
was it up* as a permanent silence. Addendum 3 had already answered it: the
failure mode written down for clocks is *correction* — a jump, a negative
duration, a sequence that lies — and `CLOCK_BOOTTIME` is not stepped by
that correction. The counter-not-clock rule does not reach it. *Three
weeks ago* stays untrustworthy; *N seconds after this boot's record
opened* does not.

**`reason` and `err` are two fields, not one.** The first draft merged
them into a single `detail` holding an exit code, a signal or an errno by
convention. Addendum 3's *no prose does not mean no reason field* governs:
a small enum, an errno and a house id are all fixed-width and are what the
constraint permits. A numeric field that is sometimes an errno reads as
always one — which is the argument the first draft made for its own
convention, against itself.

## Volume, not a hundred

**The first draft said 100 records. Addendum 3 had already rejected that
number in this file's own terms:** *a hundred records is a guessed
constant. This file refuses guessed constants everywhere else, then picks
a number. A volume is a machine property. A hundred is a plan property.
That is the same reason `NW_MAX_UNITS` is the wrong ceiling next to
`RLIMIT_NOFILE`.*

The ring is bounded by a **volume**. The record count is whatever that
volume divides by the stride. The volume is a machine property and does
not travel in the plan.

Both alternatives stay rejected on their original grounds. *Stop writing
when full* makes the thing that records failures fail when there have been
many failures. *Keep bad boots longer* puts judgement in a component that
has judgement nowhere else and makes the history non-uniform, so counting
stops meaning anything.

Records naming deleted houses are kept and say the house is gone. Dropping
them shrinks the history silently, and *failed four of the last ten*
becomes wrong with nothing indicating it.

## The boot counter is derived, not stored

PID 1 reads the ring at open, takes the highest `boot`, adds one. No second
place holds a number that must agree with this one — invariant 3 applied to
the artifact most tempted to violate it.

An empty ring yields boot 1, because the highest of nothing is zero —
stated as a property of this algorithm on an empty file, not of fresh
rings in general. A ring partially written then truncated, or one whose
records cannot be read, yields whatever the readable records say, which
may be below the true highest. The algorithm cannot tell that from empty
and does not try.

Wrap and the oldest boot number leaves with it, so the counter is monotone
only within what the ring holds. Per Addendum 4's basis rule, a derivation
over a bounded record must report **not in the retained volume** rather
than **never** — the first is a fact about the record, the second a claim
about the machine the format cannot back.

## Event types

```
BOOT_OPEN     plan hash, house count
DAWN_UP       name — the plan named it and it exec'd
DAWN_ABSENT   name — the plan named it and it did not
STARTED       name — mid-boot, via the start/stop channel
STOPPED       name — mid-boot, via the start/stop channel
DIED          name, reason, err
WATCHER_DIED  name of the watcher
CITY_HALT     reason code, err where a syscall failed
```

From Addendum 3, which had specified this set; the first draft invented a
different one and lost **attestation versus actuation** in the process.
`DAWN_UP` and `DAWN_ABSENT` are what dawn attested before the machine was
up; later starts *append* rather than rewrite that attestation. A boot
whose dawn said H absent and whose record later says H started is not a
contradiction. With the start/stop channel now approved, that distinction
is live rather than theoretical.

**What `DIED` can and cannot mean, established against the tree.** The
restart loop is in `nwsup.c`, inside the supervisor. PID 1 reaps
*supervisors* — `pid1.c:90` prints `house exit` once, when a supervisor
exits. Intermediate deaths are reaped inside a process PID 1 never sees,
and at exhaustion the supervisor `_exit`s with the house's own code, so
PID 1 receives a status indistinguishable from a clean oneshot exit.

`DIED` is therefore the supervisor's exit and nothing finer. Confirmed by
the owner of `pid1.c`: the log pipe is not a back channel, because the
parent closes `log_r` after `spawn_logger` and PID 1 never reads those
bytes; the report pipe is closed once the spawn pids arrive; the
exhaustion wait status carries no flag, no distinct exit, no remaining
descriptor.

**Ruled: restart history does not belong on this ring, and its absence is
not a missing field.** A channel into PID 1 makes the writer parse. A
second writer on the ring turns a dumb circular buffer into a concurrent
object. The supervisor's log line carries
`restart <name> death=n/budget exit=X` or `signal=X` and `spent` at
exhaustion; it dies with the boot, which makes it a different artifact,
not an incomplete one. If restart counts must survive a reboot they are a
supervisor-owned file with a supervisor-owned format.

## Reason codes

Enumerated per event type, never a `strerror` string, never a bare integer
with no key. `err` holds an errno only where a syscall failed and is 0
otherwise — the two fields exist so neither has to be sometimes the other.

`CITY_HALT`'s codes come from the existing `halt_now()` call sites, a small
list rather than a long tail — twenties of sites, many reusing one reason:
`hold-ms`, `slots/current`, `slot unchosen`, `slot path too long`,
`no plan`, `open plan`, `stat plan`, `plan size`, `plan read`, `plan`,
`log pipe`, `signalfd`, `report pipe`, `fork spawner`, `path`,
`exec spawner`, `spawn report`, `report count`, `spawn pids`,
`reap spawner`, `spawner exit`, `logger fork`, `rescue fork`,
`exec rescue`, `reboot: not PID 1`, `reboot failed`, plus from the
pre-flight stack `getrlimit nofile`, `setrlimit nofile`, `fd need=…`.

An errno is honest for the syscall failures — `open plan`, `pipe2`,
`signalfd`, `getrlimit`, `reboot`. The validation halts — `plan`,
`hold-ms`, `slots/current`, the fd shortfall — have none, and carry a
reason code with `err` at 0.

`DAWN_ABSENT` still needs its reason code decided: whether a planned house
dawn left unstarted is modelled absence or an error. Addendum 3 named this
as belonging in the enum and not answerable by inferring readiness. Open.

## Where it lives, and what it costs

On the writable root — `dawn.c:154` passes `MS_RDONLY` for the ESP only.
In its own place, not beside any house's data: a boot record is not a
house's data and must not inherit whatever happens to one.

Opened by PID 1 before the pipe loop and kept for the life of the boot,
which changes the descriptor arithmetic.

The peak **without** this file is `6 + n` — stdio, one `log_w` per house,
signalfd, two report-pipe ends. Provenance, because the register matters
more than the number. The pre-interleave peak of `3 + 2n` was
**measured**, on two independent machines: last open 510, first fail 511
against soft 1024, no slack at either end. The post-interleave `6 + n` is
**counted from source** by the author of that change and **bounded by
measurement** — last open 1018, first fail 1020 on `report pipe` — but
that change is not in the tree, so nobody has run the count against a tree
containing it. That `NW_FD_RESERVED` was chosen rather than counted is
that author's own statement, not an inference from the code.

The ring adds one for the whole boot, so the peak becomes **`7 + n`
counted** against **`8 + n` checked**, leaving **one** of headroom. Say it
in that order: the tempting summary reads as though the 8 were obtained by
adding this descriptor to the 6, and it was not. The constant was chosen
before any of this existed and is still undeclared as to what it counts.
The boot record is the first *use* of its slack, spending one of the two.
The remaining one is still nobody's.

## The read side

Answered 2026-09-14 by a prose review with no repository access, which is
the right reader for four decisions that are all properties of a format.
Each carries what it forecloses, because a format decision that forecloses
nothing usually decided nothing.

**Raw records, not a projection.** A reader is permitted to know the
layout, which is fixed and self-describing. A projection would need a
second format, a second writer, or a parser in PID 1 — all barred by the
constraint that produced the write side — and would make the read side
depend on the writer's interpretation of what a record means, which is the
judgement this format refuses to put anywhere. A reader may compute a
projection transiently, in memory, without it becoming part of the
artifact. *Forecloses:* the record ever being self-summarising, embedding
a query result, or answering a question the operator did not ask; and any
reader that cannot parse fixed-width binary. That last is a real cost and
the correct one.

**It filters, and only on fields as written** — `boot`, `seq`, `event`,
`name`, `reason`, `err`. Not on derived properties, not on comparisons,
not on anything needing more than one record to evaluate. A filter on a
field is a property of the format; a filter on a relationship is a
property of a reader's interpretation. *Forecloses:* filtering by
"failures that matter", by divergence, by "the last ten boots like this
one" — those are reads, not filters, and belong to whatever consumes the
filtered output. Also forecloses adding a field solely to make a filter
cheap.

**Comparison and summary are both permitted reads and neither is a
property of the format.** The record carries `boot`, `seq` and the plan
hash; comparison is a join on those, summary is a count over a filtered
set. Neither requires the record to change. *Forecloses:* a precomputed
comparison, a baseline, or a stored "normal" to judge a run against. And
the record does not know which boots are comparable — a reader may rely on
the plan hash as a string, never as a guarantee of comparability.

**A stale read announces itself, and the format must make that
detectable.** Every read of the ring is a snapshot: it is bounded, the
oldest record is overwritten, and a reader cannot know what was
overwritten without having read it before. A reader may see that the
oldest `boot` in the ring is not 1, and must render that as **not in the
retained volume** rather than **never** — the basis rule on the read side.
*Forecloses:* any read claiming "never", and any reader that must know the
record count in advance.

### One thing this moved on the write side

The read side needs *bounded, oldest overwritten* to be a **guarantee**,
not an implementation detail. The write side already decided it; it had
not stated it as something a reader may rely on. Making it explicit is not
a new decision, but by Addendum 4's own test — *a seam that closes without
moving either side was not load-bearing* — it counts: the seam forced the
write side to say what it already meant.

### The magic: the argument fails, the conclusion survives

Addendum 4 argues the read side needs a magic of its own because *the
record is written at boot N and read at boot N+k, possibly by a tool that
did not exist when the entry was written*, and that this makes the
record's case **stronger** than the plan's.

The review's objection is correct and worth keeping: that is a statement
about the reader's provenance, not about the record's layout. A magic
selects a layout. It is needed only if the layout can change between the
write and the read, and Addendum 4 assumes that rather than arguing it.

Where the review then overreaches: *if the layout is fixed, the magic
buys nothing; either the layout is fixed and no magic is needed, or the
layout can change and the write side was never settled.* That dichotomy is
the reasoning that produced a real defect in this project, recorded in
`blob.h`. The plan's layout was settled until `window_s` left
`struct nw_unit`; the magic did not move with it, and every pre-change
blob was refused as `NW_E_SIZE` — *correct verdict, wrong diagnosis*, with
`NW_E_MAGIC` made unreachable for the one class it exists for. The comment
that resulted is the rule: **the magic and the layout MUST move together
or the diagnosis lies — a rule for whoever changes the layout, not
something the code enforces.**

Settled-now is not fixed-forever, and a format whose magic is omitted
because the layout is currently stable is a format that will change its
layout and lie about it. So the conclusion holds.

But it holds for a different reason than Addendum 4 gave, and the better
reason also answers *stronger than the plan's*: **the plan has a checker
and the record does not.** A plan with a stale magic is refused offline by
`nw-check` before anything runs. A record with a stale layout is read by
whatever happens to be installed, with no validation step anywhere, and a
layout mismatch surfaces as plausible wrong values rather than as a
refusal. That is the asymmetry — not who reads, but whether anything
checks. Amend Addendum 4's sentence accordingly rather than dropping it.

### The torn record

A reader scanning at fixed stride can read a record PID 1 was mid-write on
at power loss. Nothing marks a record complete, so a partial entry is
indistinguishable from a written one at the same offset.

**Settled: the record carries a CRC over its own bytes.** A prose review
rejected a checksum on the grounds that *a checksum requires the writer to
compute it, which is arithmetic the writer is barred from doing after
start*, and proposed instead a trailing copy of `seq` written after a
barrier.

That is a misreading of the constraint, and the constraint is written
down: `CLAUDE.md:32` says **no allocation, no parsing, no recursion after
start in PID 1**. Arithmetic is not on that list, and PID 1 already does
it after start — `pid1.c` computes
`(long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000` and calls `snprintf`.
A CRC over a fixed-width record allocates nothing, parses nothing and
recurses nowhere. The scheme proposed to avoid the arithmetic costs two
writes and a barrier per record; the arithmetic it avoids was never
forbidden.

**The review's other conclusion is right and the CRC serves it better.**
It argued that a torn record and a record whose content is wrong are not
different in kind, and the format should treat them as one case. Exactly —
and a CRC is the one field that makes both the same case, where a trailing
`seq` copy detects only the tear. One check, one verdict: the record
parses and its CRC matches, or the reader stops there.

Still open, and it is a sizing question rather than a format one: whether
the stride should be a sector, so that a record is atomic at the device
level and the CRC catches corruption rather than tearing. At 62 bytes of
content, one record per 512-byte sector wastes seven eighths of the
volume, and the volume is what bounds the history. Nobody has decided
whether the history or the atomicity is worth more, and the volume being a
machine property means the answer may differ per machine.

A barrier is separately available — `fsync` is a syscall, and syscalls are
not barred — but it is a latency decision at boot rather than a format
decision, and it belongs to whoever measures it.

## The half this document does not specify

Addendum 4's finding, which the first draft walked straight into. F2
argued the write side; the surfacing pass then moved the hard part and the
spec did not follow. A bounded ring, read only when the operator looks,
never announced, presented as a snapshot — the work is no longer writing
under pressure. It is answering *did this house fail last boot* from a long
run of fixed records, and nothing says who reads them, through what, or
what a reader sees.

Six capabilities depend on that half, and they are not six features to
add — they are six reads the design cannot currently deliver: divergence
trend, grant saturation, absence-as-information, declaration drift, plan
epochs, and the watcher's own track record. One specification, not six
features.

This document does not close that, and must not be read as closing the
record's format question — which is exactly the mistake Addendum 4
predicted a reader of Addendum 3 would make, and the mistake this
document's first draft then made in writing.

## Not a live view, and not a notification

*Is house H running now* remains a kernel read, labelled observed. The
record answers what happened. F1 stands.

Under never-interrupt nothing pushes. But Addendum 3's *a bar you glance
at is not a push* applies, and the first draft got this wrong by claiming
nothing subscribes to the file: a death already in the record **may**
appear on waybar, because waybar is a surface the operator looks at rather
than a message that arrives. Hiding it was the rule applied past its
reason. A house that exhausts its budget still stays down until reboot,
and nothing announces.

## What this cannot answer

Named so the boundary is learned from the spec rather than reconstructed
at three in the morning.

**How many restarts, and against what budget.** Not in the record, and per
the ruling above never will be — a boundary, not a gap. The supervisor's
log line answers it for the life of the boot. Across reboots nothing does.

**Whether a unit was removed from the plan.** `BOOT_OPEN` carries a house
count, so a change is visible as a different number; which house is not.
A unit dropped before it ever started leaves no trace of the removal.

**What a dead watcher was watching.** `WATCHER_DIED` carries a name and no
scope.

Not silences, contrary to earlier readings: *how long was it up* is
answered by `boottime`, and whether a dependent came up is answered by
`seq` ordering. What `seq` does not carry is that the *edge existed* — the
`gate` field is consumed before the fork and leaves nothing behind.
Ordering is present; the relationship is not.

## Falsified by

Records not in `boot`/`seq` order when read back. A `DIED` for a name with
no preceding `DAWN_UP` or `STARTED`. A wrapped ring whose oldest record is
not the one overwritten. A record written after PID 1 has allocated or
parsed anything. A `boottime` that goes backwards within one `boot`. And
the one that matters most: any reader that must know the record count in
advance to make sense of the file — if it does, the ring is not
self-describing and the format has failed at its one job.
