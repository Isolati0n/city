# The bake ledger

Written 2026-09-14. Not in the tree. Entirely offline: no PID 1, no
runtime rule, no channel, no second writer on anything. That is unusual
for a proposal in this project and is most of why it is worth doing first.

Two features, one artifact. The refusal store makes an existing claim true.
The accepted-plan record is the missing prerequisite for bisecting the
machine. They are the same ledger read from two ends, and building either
alone would build most of the other.

## What is thrown away today

Checked against the tree.

**On acceptance**, `nw-cc.py` prints:

```
wrote /tmp/led.blob units=1 binds=0 crc=0xf9eb38f3 bytes=300 sha256=13dd75c2…
  no resource block: cg
```

Plan hash, shape, and which houses declared no resource block. Printed to
a terminal and gone.

**On refusal**, the entire output is:

```
name
```

One word. That is worse than this proposal assumed before checking, and
it changes the shape of the work: the refusal cannot merely be *retained*,
because there is not yet enough of it to retain.

What the baker holds at that instant and does not print: which unit, since
it builds `idx = {n: i for i, n in enumerate(names)}` and validates per
house; the offending value, since it is the thing being tested; and the
rule that rejected it, since that is the branch it is standing in. Forty-five
`raise SystemExit` sites, most already carrying good text —
`cpus={v}: range {part} runs backwards`, `too long: {s}` — and seven
carrying nothing but a bare noun: `duplicate name`, `unit count`,
`bind count` and `fd budget` among them.

So step one is not storage. It is making the refusals say what the baker
already knows, and the uneven quality of the existing messages is the
argument: the good ones prove the information is in scope at the call
site.

## Why refusals are the densest output the system produces

*Every change is a proposal* is currently half a proposal system — a gate
with no record of what it rejected.

And a refusal is where plan-shaped failure explanations live. That was
established today by a separate route: the init cannot explain its own
runtime setup failures, because the only process holding the facts is
required to die and every channel out is barred. The offline checker has
no such problem — it holds the plan, the hashes, the lid set and the
grants, and a refusal is a first-class output rather than something
squeezed past a constraint.

So the one place in this system that *can* explain a failure properly is
the one place that prints a word and exits.

## The ledger

One append-only file in the bakery, not on the machine and not in the
plan. Bakery output is not a machine property and does not travel.

One row per bake attempt:

```
when          bake timestamp (wall; the bakery has a clock and no reason to distrust it)
verdict       accepted | refused
plan_sha      sha256 of the blob, for accepted rows
source_sha    sha256 of the .city input, for every row
code          refusal code, for refused rows
detail        what the baker knew: unit name, field, offending value
shape         units, binds, crc, bytes, for accepted rows
blob_path     where the accepted blob was retained
```

`source_sha` is the field that makes the whole thing work, and it is the
one a first draft would omit. A refused bake has no plan hash — there is
no blob. Without a hash of the *input*, two attempts at the same broken
city are unrelatable and the store answers nothing. With it, re-proposing
something already refused is a lookup.

## What it buys, first end: the refusal store

**Re-proposing a refused city shows last time's reason before the bake
runs.** Not a warning and not a block — the operator may have fixed the
machine rather than the plan. It is a lookup on `source_sha`, printed and
then the bake proceeds.

**The refusal history is a record of what was tried and why it did not
work.** For a system whose thesis is *optimise for being changed*, that is
the change history nobody keeps: every other system records what you did,
and this records what you attempted. Ten refusals of the same shape is a
statement about the format, not about the operator.

**It gives the thesis its missing measurement.** Addendum 7 noted that six
passes never said how you would know *optimise for being changed* is
working. Refusals per accepted bake, and time between first refusal and
acceptance, are direct readings. They are counts, not judgements.

## What it buys, second end: bisect

Bisecting a machine is unavailable to every other OS because no other OS
has a complete addressable state. Here, *boot 41 was fine, boot 48 is not*
is a search over retained plans, run by booting them — not a rebuild,
since the sealed artifacts still exist.

It needs two things that do not exist. The ledger is one: a plan store
beyond the two slots, with each accepted blob retained and addressable by
hash.

**The other is the kernel hash, and this is the concrete argument for a
field Addendum 3 assumed and never made.** Without it, the tuple cannot
distinguish *my plan broke it* from *the kernel bump broke it* — which is
the most common real case and the one a bisect exists to answer. The
identity argument for the kernel hash is aesthetic; this one is
operational, and it belongs in `NWPLAN10` alongside the plan hash rather
than waiting for a second bump.

Bisect itself is not proposed here. The prerequisite is, and it arrives
free with the refusal store.

## What this does not do

**It does not run on the machine.** Nothing here is read at boot, and the
ledger must never become an input to one — a bakery that consults its own
history to decide what to emit is a bakery with judgement, and the
provenance rule exists to keep judgement out of baked numbers.

**It does not gate.** A refusal already refused does not become an error
of a different kind. Showing the prior reason and proceeding is the whole
behaviour.

**It does not retain refused blobs**, because there are none. Only the
input.

**It does not explain runtime failures.** The narrowing established
today stands: plan-shaped failures are refused before boot and explained
here; runtime setup failures carry a stage name and nothing more. This
makes the first half true. It does not touch the second.

## Falsified by

A refusal whose `detail` names a unit the source does not contain. Two
bakes of byte-identical input producing different `source_sha`. An
accepted row whose `blob_path` no longer holds a blob matching its
`plan_sha` — that one is the ledger's own rot and the reason to check it
rather than trust it. And a bisect that cannot separate a plan change from
a kernel change, which would mean the kernel hash never landed and the
second half of this proposal was never finished.
