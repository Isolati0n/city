# Addendum 7 — the tree pass, 2026-09-14

The first pass on this file by an agent with a clone, a booting system and
a green suite. Every previous pass reasoned; this one checked. Six facts
were established against the tree or by running something, and each is
marked as such. Everything unmarked below is reasoning, in the same
register as the rest of the file.

Also the first pass that **removes** rather than continues. Six passes of
accretion have left sentences a later correction orphaned and positions
that outlived their premise. Addendum 4 found one instance and named it;
this pass looks for the rest.

Status, which is the first thing this pass changes: see *The status line
has expired*, below. It is no longer true of the whole file.

## What the tree says

**M1 is already the structure. Checked.** The modular pass calls it *the
largest TCB reduction available, and a refactor rather than a feature*:
split `nwsup` so the persistent process holds nothing but a counter and
`waitpid`.

`lid_brick` is called at `nwsup.c:613`, **inside the forked child**,
alongside `lid_netns`, `lid_newns`, `lid_landlock` and seccomp, all before
`execv`. The parent sets `child = p` and calls `waitpid`. Nothing else.
And `nwsup.c:595` says it in a comment: *Stay. Isolation applies to the
house child, not to wait/restart.*

The premise — *nwsup currently does lids, brick mount, pivot and exec,
then a restart loop* — describes one process doing both. The tree
separates them **as code paths** and leaves the privilege and the unused
code in the process that survives.

**Ruled by the owner of the boot chain, 2026-09-14: the parent is not
clean, and M1 is restated rather than struck.** After the child `execv`s,
the supervisor has not unshared, not mounted, not Landlocked, not
seccomped — and `nw-spawn` did none of that either: double-fork,
`pack_kit`, `execl(nw-sup)`. The parent is still root in PID 1's mount
namespace with the ambient set it was born with. Same binary as the child,
so `lid_brick` and friends are still mapped. Same environment. It can
`mount`, `mknod`, `unshare`, open the brick, and fork a second child that
applies lids. *Root that is not using its root* is the surface.

**And it must keep the privilege, which is the part that changes the
proposal.** The next child inherits the parent's capability set at `fork`.
Drop `CAP_SYS_ADMIN` after the first house execs and the restart child
cannot `unshare` or `lid_brick`. Same-uid `kill` and `waitpid` need
nothing; the *next isolated fork* needs everything. So "drop after the
fork" cannot mean "drop for the life of the supervisor" — on a house with
a budget it would make restart unable to isolate, silently, on the second
death.

**An earlier draft of this addendum proposed exactly that**, as one of two
restatements, and it is a defect rather than an imprecision: it would have
disarmed isolation at the first restart. Recorded rather than removed,
because the failure is instructive — the tree fact was right and the
remedy inferred from it was wrong, which is the same shape as `DRIVER_OK`
being true and its use being false.

So M1's honest form is neither "split work that is already split" nor
"drop the parent's capabilities." It is: **the persistent process is a
privileged copy of a binary whose privileged functions it never calls,
because it must remain able to call them in the next child.** The
reduction available is `execv` of a waiter that still holds those caps and
does not contain `lid_brick`. Same privilege, less code. Smaller than the
modular pass claims and real.

**Capability 1's tuple is one third real. Checked.** *Every house is a
brick hash plus a layer. The plan is a hash. The kernel is a hash. So the
entire running system is a tuple of hashes.*

Brick hashes are real: `uint8_t brick[NW_BRICK_HASH]` in `nw_unit`. The
plan is not a hash — the header is `magic[8]`, `n_units`, `n_binds`,
`crc32`, and a CRC32 is an integrity check, not an identity. The kernel
hash is nowhere in the blob at all.

The capability this file calls the one that buys the most cannot currently
be computed. It becomes true with a plan-hash field plus something
carrying the kernel digest, and Addendum 3 already relies on both — *plan
hash, kernel hash, brick hashes* — as though they exist. They do not. This
should be stated in the future tense until they do.

**Capability 2's opening sentence is wrong. Checked.** *nw-check returns
yes or no.* It returns 26 distinct refusal codes: `NW_E_MAGIC`, `NW_E_BRICKNS`,
`NW_E_CAPNOLAYER`, `NW_E_MEMORDER`, `NW_E_RESNICE` and the rest. The
capability survives — querying *what can reach the display*, or *what goes
dark if this house is absent*, is genuinely absent — but the gap is
smaller than the framing claims, and the framing understates the checker
in a document arguing for making it say more.

**The RLIMIT_NOFILE claim holds, and harder than stated. Checked.**
*Nothing in the codebase reads it* is true; the only match is `blob.h:219`
saying so on purpose: *a blob has carried no machine-derived number since
it existed — no getrlimit, no device number, no CPU count — so there is
nothing to label and no labelling path that can diverge from the measuring
path.* That is not an oversight, it is the provenance rule as a structural
property, and it is a stronger version of this file's own argument than
this file makes.

**The virtio field is exposed, and the use built on it is refuted. Run.**
Addenda 5 and 6 both name this as *the one thing that must be checked
before it is relied on*, correctly marked as inferred from mechanism. It
was checked.

QEMU 8.2.2 exposes it over QMP. `x-query-virtio` lists the devices;
`x-query-virtio-status` returns the register. Sampled before the guest
probed, the status set is empty; after `city open`, all four including
`DRIVER_OK`. The transition is the result — a constant would have proved
nothing.

Then the motivating case was run. Addendum 5's own sentence is *a guest
kernel that panics on its own initramfs leaves QEMU running perfectly, so
the record says the district is up while the machine inside is at a panic
screen.* Same disk, an initrd whose `/init` exits immediately: the kernel
probes virtio-blk during driver init, long before it execs `/init`.

```
console: Kernel panic - not syncing: No working init found.
QMP query-status: running
virtio-blk: ACKNOWLEDGE / DRIVER / FEATURES_OK / DRIVER_OK
```

**A panicked guest reports `DRIVER_OK`.** The fact Addendum 5 states stays
true — it means a running kernel with a working driver, observed by the
host, nothing inside cooperating. What does not follow is the use. It was
proposed to close the gap between *QEMU exec'd* and *the guest booted*,
and it moves the boundary from `exec` to *early kernel* — a few hundred
milliseconds, nowhere near a booted machine.

Decision 1 of Addendum 5 therefore stands alone, and now stands on a
measurement rather than for want of an alternative. Two further costs to
record: the commands are `x-` prefixed, QEMU's own marker for an
interface with no compatibility guarantee, so a district design pinned to
them inherits a break that arrives at a QEMU bump; and reading them needs
a QMP socket decided at district launch, so a district started without one
is permanently unattestable.

**Addendum 6's defect is half real. Checked.** `fold.py:565` writes
exactly the seven fields named — `schema_version`, `image_hash`, `name`,
`derived_from`, `baked_by`, `layer_id`, `opaque_check` — and none of the
three distinguishing properties. Exact.

`brickcatalogue.py` is not in the worktree and has no object anywhere in
the repo's history. Addendum 6 calls it a shipped tool, specifies a
query-shape change to it, and estimates a day's work on it. *A missing
field in a schema that ships, not a design note* is true of `fold.py` and
false of the other half of the same sentence.

## What a later pass orphaned

**"Still nothing in the tree that could disagree with any of it."**
Addendum 3 states this as the file's standing condition. It has expired:
six facts above disagree, four of them with load-bearing claims. That
sentence was the file's guarantee of unfalsifiability and it should be
struck rather than carried into a seventh pass.

**The three-pass framing is conceded twice and never rewritten.** Addendum
4 records that *F1 and the snapshot decision are one decision recorded
twice in different passes — the modular and surfacing passes are less
independent than the three-pass framing suggests*, and separately that
never-interrupt and M2 *look like one decision because both are phrased as
reductions* when they are independent axes. Both concessions are made in
passing; the earlier text still presents a stack. This is the accretion
pattern in its purest form — the correction is written, the corrected
sentence is not.

**Capability 3 is contradicted by the M1 fact, and no pass could have
seen it.** *Failures explain themselves* rests on: *the init already holds
the plan, the brick's manifest, the lids and the grants — everything
needed to say why.*

Combine that with the tree. The process holding the lids, the brick and
the grants is the forked child, and it ceases to exist at `execv` — that
is exactly what M1 celebrates. The process that survives holds a counter
and `waitpid`. And PID 1, which writes the record, sees only the
supervisor's exit status.

So everything needed to say why is held, briefly, by a process that is
gone before anyone asks, and the two processes that persist hold none of
it.

**Confirmed as a real tension, same ruling.** The route that exists is not
a record field: `die()` writes `[nw-sup] FAIL <stage> errno=N` to the log
pipe and `_exit(72)`, and the logger puts it on the console. Every lid
stage exits 72, so neither the parent nor PID 1 can tell brick-mount from
Landlock from exec. Distinct lid-stage codes — the unbuilt proposal 2 of
`NW-SUPERVISOR-OBSERVATION` — would give the waiter a reason *code*, and
still not the brick hash or the bind that failed. Having the child write
the boot record before it dies makes a second writer on the ring, which
that format rules out for the same reason it rules out a channel into
PID 1.

So failures explain themselves **to the console, once, as a line that does
not survive**. They do not explain themselves to the boot record or to any
later query. M1 in its small form does not improve this — the explainer is
still the child, and both the current parent and a minimal waiter see only
72.

The two proposals pull in opposite directions on who holds the
explanation, and the file stacks them. It should say so instead.

**Addendum 4's own test has been run and the file does not know.** *A seam
that closes without moving either side is a seam that was not load-
bearing.* The write side has now moved a third time: `DIED` cannot carry
per-restart death, because the restart loop is inside `nwsup` and PID 1
reaps supervisors, not houses. That strengthens Addendum 4's claim and
belongs recorded beside it.

**Addendum 5's memory asymmetry depends on unbuilt machinery.** *Districts
are the one thing whose resource commitment is a plan fact rather than a
runtime observation* is true of the declaration and not of the
enforcement: `nw_res` is declared in the format, written by the baker and
validated in `nwcheck.c`, and `nwsup.c` has zero references to it. Nothing
applies a resource limit today. The asymmetry is real in the plan and
absent in the running system, and the sentence does not distinguish those.

## The status line has expired

Every pass carries *not decided, not scheduled, not dispatched*. That is
now wrong for a third of the file, which means it has stopped telling a
reader anything.

Decided and operative: never-interrupt, which the boot record was
specified against. The boot record itself, specified, reviewed twice, and
ruled on by the owner of `pid1.c`. Restart policy leaving the TCB, decided
and unbuilt. Save-immediate switch-deferred, confirmed for houses and
districts. The start/stop channel, approved and sequenced before
scratch-becomes-saved.

Checked, no longer inferred: the six facts above.

Still not decided, not scheduled, not dispatched: the five capabilities as
capabilities, M1 through M4, districts as a plan kind, the read side of
the record.

Three states, not one. A blanket disclaimer over all three hides which
parts a reader may build on.

## A measurement the thesis has never had

The thesis is *optimise for being changed*, and nothing in six passes says
how you would know whether it is working. One is available now.

`NWPLAN10` is a change — two fields, one magic increment. What it costs is
a direct reading: files touched, whether every old blob diagnoses as
`NW_E_MAGIC` rather than `NW_E_SIZE`, whether the offset pins catch a
field addition that shifts nothing, how long between decision and green
suite.

There is already one data point and it is a bad one. `NWPLAN06` changed
the layout without moving the magic, and every pre-change blob was refused
as `NW_E_SIZE` — correct verdict, wrong diagnosis, with `NW_E_MAGIC` made
unreachable for the one class it exists for. A file arguing that this
system is better at being changed than its alternatives should record the
one time it measured that and got a defect.

## What this pass does not reopen

Never-interrupt as a ban on push. The name as the sealed half. The counter
as the ordering field, and boottime as permitted within it. Volume rather
than a hundred. Oldest dropped. Stock kernel. The readiness-protocol
refusal — which the virtio result reinforces rather than weakens, since
what was refuted is an inference from an observed register, not the
principle that the host may observe one. Districts as a plan kind. A
district's disk as base plus delta. The guest's init as out of scope.

## What this pass adds to the not-opened list

Both items this pass first listed here are now answered above and have
been removed: M1 pays for itself as a code-surface reduction and not as a
privilege reduction, and capability 3 does not survive M1 — it does not
survive the current arrangement either. What replaces them:

Where the explanation should live, given that the only process holding it
is required to die and neither surviving process can be given it without a
second writer or a parse. That is a question about an artifact nobody has
proposed, not about either existing proposal.

Whether `brickcatalogue.py` should exist, since Addendum 6 specified a
query-shape change to a tool that does not.
