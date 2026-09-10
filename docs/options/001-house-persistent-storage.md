# 001 — House persistent storage

Status: **options, not a decision.** Written 2026-09-10.
Decision returns to the operator.

## Provenance warning — read this first

This doc was requested with a corpus that is not present in this repository.
`MASTER.md`, `DECISIONS.md`, `docs/`, and the grok-web chats do not exist on
disk, are not tracked, and **have never been tracked in any commit**
(`git log --all --pretty=format: --name-only`). `list_repos` returns exactly
one repository. So the instruction "read the corpus first, and stop if this
contradicts it" could not be carried out, and the contradiction check below is
against *this repository only*.

The briefing was re-issued after that was reported, so this doc proceeds under
stated assumptions rather than blocking. Every premise that could not be
verified is flagged **[UNVERIFIED]** at the point it is used. Two of them
appear to be contradicted by the code, and are called out in the next section.
If the corpus says otherwise, the corpus wins and the affected options need
re-costing.

Vocabulary is used as the briefing used it, with these working definitions,
none of which appear anywhere in this repo:

| term | assumed meaning | in repo |
|---|---|---|
| brick | sealed, content-addressed image a house runs from | absent |
| district | VM-level tier above a house | absent |
| ESP | EFI System Partition | absent |
| first dawn | first boot of a given plan or unit | absent |
| R3 | the rollback requirement | absent |
| H1 | the opaque-kit requirement | absent |
| slot | A/B plan slot | **present** |
| house / unit | a supervised process | **present** |

"The usual shape" for `docs/options/` could not be observed — no `docs/` has
ever existed here — so this doc invents one: provenance, gap, constraints,
the five questions, options with native cons, recommendation.

## Contradictions found against this repository

**1. Decision #14 is said to have removed the critical bit from houses. It is
live in the code.** `blob.h` carries `critical` in `struct nw_unit`;
`nwcheck.c` validates it (`NW_E_CRIT`); `pid1.c` halts the city on a critical
house's death; `critical-halt` is a passing test in `tests/run.py`. Either
this repo predates #14 and `critical-halt` is testing something retired, or
#14 is not in force. **This matters to question 4**, because the briefing
rules out a critical flag for the do-not-restart channel on the strength of
#14, and `nwsup.c` already implements a do-not-restart path keyed to exactly
that flag. See question 4 for what is and is not actually available.

**2. Options A and C are described in terms of machinery removed earlier the
same day.** Edges were deleted (`HISTORY.md` §17): there are no wires, no
socketpairs, and `NW_KIT` / `NW_WIRES` no longer exist. Option A ("one
designated house owns the disk") requires other houses to reach that house,
and **there is now no inter-house communication mechanism of any kind**.
Option C ("a dirfd in the kit alongside wires") refers to a kit that no longer
has anything in it. Both are re-costed below on that basis.

## The gap — confirmed

```
grep -rniE 'persist|storage|store|state_dir|statedir|datadir|data_dir|
            volume|mount|disk|/var|writable|first.?dawn'
     --include='*.c' --include='*.h' --include='*.py'
     --include='*.als' --include='*.tla' --include='Makefile' .
  → NO MATCH
```

(excluding `--mount-proc` and `CLONE_NEWNS`, which are the test harness and
the namespace lid, not storage)

`struct nw_unit` is `name`, `exec_path`, `critical`, `budget`, `window_s`,
`lids`. A house receives `/dev/null` on fd 0, its own log pipe on 1 and 2, and
`close_others` sweeps everything else (`nwspawn.c`). There is no writable
anything. The gap is total: not in `blob.h`, not in the plan language, not in
`nwsup.c`, not in `bakery/nw-cc.py`.

## A contradiction *inside* the constraint set

Constraint 2 (sealed plan: storage is "not created by anything on the box")
and constraint 3 (bakery-only authorship: "no on-box tool creates ... storage")
together mean **nothing running on the machine may create a store**. The
bakery is offline and emits a blob; it never touches the target disk. So on a
machine that has never booted this plan, no component is permitted to create
the directory.

There are only three ways out, and the doc has to pick one:

- **(i) Provisioning-time materialisation.** The store tree is written into
  the persistent partition when the machine is imaged. Creation is offline, so
  bakery-only authorship holds literally. A plan naming a store absent from
  the partition is a **boot failure**, fail-closed.
- **(ii) Narrow the constraint** to "no on-box tool creates storage *not named
  in the sealed plan*", letting `nw-sup` `mkdir` exactly what the plan
  declares at first dawn. This is a real relaxation and should be decided
  explicitly, not slid past.
- **(iii) Storage is a partition, not a directory.** The plan names a block
  device or subvolume that must pre-exist. Coarsest, and caps the number of
  stateful houses at the number of partitions.

Every option below is annotated with which of these it needs. This is the
single largest thing the corpus may already answer and I could not check.

## The five questions

### Q1 — Where does it live, relative to ESP, slots, bricks?

The answer is forced by constraint 7 (R3 rollback) and is the same for every
option that stores anything at all: **outside all three.** A separate
persistent partition.

- Not the **ESP**: it is a boot filesystem, typically FAT, no ownership, no
  checksums, and sized for boot artifacts.
- Not a **slot**: slots roll back. Data must not.
- Not a **brick**: bricks are sealed and content-addressed. A mutable store
  inside one either breaks the seal or forces an overlay whose upper layer has
  to live somewhere else anyway — which just relocates this question.

So: `/persist` (or equivalent), a fourth thing alongside ESP, slots and
bricks. Everything below assumes that.

### Q2 — Who creates it?

Per the contradiction above: **never automatically, on the box.** Either
provisioning-time (i), or a deliberate relaxation (ii). "nw-sup at first dawn"
is only available under (ii) and should be recognised as amending constraint 3
rather than complying with it.

### Q3 — What happens on slot swap?

Storage must follow **neither the machine nor the slot, but a store identity
declared in the plan and distinct from the house name.**

Keying on house name is the trap. A house renamed in slot B silently gets an
empty store while its data sits orphaned under the old name — silent, which is
this project's characteristic failure shape. Keying on a `store-id` means slot
B can rename `canvas` to `canvas2` and still point it at store `s-canvas`.

- **Rename with the same store-id:** data follows. Correct.
- **Rename without updating the store-id:** new empty store, old one orphaned.
  Detectable at bake time — the bakery can warn that a store declared in the
  previous plan is unreferenced in this one.
- **Slot B drops a house that has data:** the store persists, unreferenced.
  **Nothing on-box may reclaim it** (constraint 3). It accumulates forever
  until an offline operation removes it. This is a real, permanent cost of
  bakery-only authorship and must be accepted openly rather than discovered
  later as a disk-full incident.
- **Rollback to an older slot:** works, because the store is outside the slots
  and the older plan still names the same store-id. **But the data may be
  newer than the plan's schema expectations.** Nothing in this design can fix
  that; the house must tolerate reading data written by a later version of
  itself, or refuse to start. See Q4 — that refusal is the same channel.

### Q4 — Integrity fault, and the do-not-restart channel

**Measured, and the briefing is right.** `nwsup.c`:

```c
if (WIFEXITED(st) && WEXITSTATUS(st) == 0) _exit(0);       /* clean stop  */
if (critical) _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 71); /* no restart  */
... budget/window ring, restart until exhausted ...
```

A house exiting 66 and a house exiting 99 are handled byte-identically: both
nonzero, both non-critical, both restarted up to `budget` (default 3) within
`window_s`. Confirmed by reading, not assumed.

**One correction to the briefing.** A do-not-restart path *does* exist — the
`critical` branch above — and `critical` is live in this repo despite
[UNVERIFIED] decision #14. But it is a **per-unit policy fixed at bake time**,
not a **per-exit signal**. It cannot distinguish "this particular death was a
corruption fault" from "this one was an ordinary crash", which is what the gap
actually requires. So the briefing's conclusion holds even though its premise
is questionable: `critical` is not the channel, for a better reason than #14.

Four candidate channels:

- **Reserved exit code, named in the plan.** The unit record declares which
  exit code means *do not restart me*; `nw-sup` inspects `WEXITSTATUS` and
  refuses. No new handle, so decision #3 is not tripped. The plan names it, so
  the contract is sealed rather than a hardcoded constant.
  *Cons, native:* a single integer is a very narrow channel carrying no
  detail. Exit codes collide with language runtimes — a Python house exits 1
  on an uncaught exception, a shell house exits 2 on misuse, and `sysexits.h`
  values are widely reused. **It cannot cover death by signal**, so a house
  that `SIGSEGV`s midway through detecting corruption is restarted straight
  back into the corrupt store. And it is a *checkable* property, not a
  structural one, which cuts against §14's grain.
- **Sentinel file in the store.** House writes `.integrity-fault` before
  exiting; `nw-sup` checks for it.
  *Cons:* requires `nw-sup` to read inside the store, which breaks the store's
  opacity and gives the supervisor a dependency on the house's filesystem
  layout. Worse, a store corrupt enough to warrant the fault may be exactly
  the store the house cannot write the sentinel into. Fails when needed most.
- **A status descriptor in the kit.** House writes a structured record before
  exit.
  *Cons:* a new handle type, so **blocked behind decision #3**. Also needs the
  kit machinery that §17 removed.
- **Supervisor-side detection.** `nw-sup` checksums the store and refuses to
  start the house if it fails.
  *Cons:* the supervisor must understand the store's format, which is the
  opposite of opacity, and it is O(store size) on every start.

**None of these is structural.** A dead process cannot tell you anything; it
can only leave evidence, and all evidence is checkable rather than
constitutive. Worth stating plainly rather than hunting for a design-it-out
answer that does not exist here.

### Q5 — Smallest version, and what it forecloses

One store, one house, path delivery, pre-provisioned directory:

1. The plan gains a store table: `store <store-id>` entries, and a unit
   optionally references one store by id.
2. `nw-sup` `chdir`s into the store's path before `execv`. No mount, no new
   handle.
3. The store directory is materialised at provisioning (route i). Missing at
   boot ⇒ `nw-sup` fails the unit; a missing declared store is never created.
4. No GC, no migration, no sharing, no quota.

**Forecloses almost nothing, provided the plan names a store-id rather than a
path.** Delivery can move from path to dirfd later without touching the
identity model, because the identity model is the part that is hard to change
once data exists. It does foreclose: multi-house shared stores (deferred, not
prevented), on-box reclamation (by constraint, permanently), and any migration
story (by constraint).

**What it does not give you:** confinement. `chdir` alone does not stop a
house walking `../`. Landlock would, but the Landlock lid in `nwsup.c`
degrades silently to no restriction when the ABI probe fails — a plan can
declare `landlock` and receive nothing but a log line. So the smallest version
provides *addressing*, not *isolation*, and should be described that way.

## Options

### A — No storage; one designated house owns the disk

**Out, in this codebase.** Not because it is a bad idea, but because it
requires every other house to reach the owning house and **edges were removed
today** (§17). There are no socketpairs and no IPC of any kind; a house holds
`/dev/null` and a log pipe. Option A cannot be built without reversing §17 or
introducing a new communication mechanism, which is a larger change than any
other option here.

*Cons if §17 were reversed:* re-creates the ambient service that non-provision
exists to prevent — every stateful house depends on one process, whose death
takes all state with it. Serialises all storage I/O through one supervisor's
restart budget. And it does not remove the problem, it centralises it: the
owning house still needs somewhere to put the bytes, so Q1–Q3 must be answered
anyway, for one house instead of N.

### B — Plan-declared path (`store=` field)

`nw-sup` `chdir`s or mounts before `exec`. No new handle type, so decision #3
is not tripped.

*Cons, native:*
- **The house learns an absolute path.** H1 forbids learning an absolute fd
  number; a path is strictly more information than an fd, and guessable and
  traversable besides. B satisfies the letter of H1 and contradicts its
  intent.
- **`chdir` does not confine.** Only Landlock does, and Landlock here is
  best-effort with a silent-degradation path.
- **Per-unit mounting is not implemented.** The `NEWNS` lid is a bare
  `unshare(CLONE_NEWNS)` with no `pivot_root` and no remount — it isolates
  mount propagation, not the view. "nw-sup mounts before exec" is new work,
  not a use of what exists.
- **Path strings pin an on-disk layout into a sealed plan.** Rollback to a
  slot whose plan used a different path convention orphans the data, which is
  exactly the R3 case the design must survive. Mitigated only by store-ids
  (option E).
- Format cost: a path field per unit, `NW_PATH_LEN`-sized, a new `NW_E_*`
  code, and the `errs[]`/`nw_errstr` bound moved with it.

### C — Storage as a kit item (dirfd)

Most consistent with non-provision and H1: the house receives a descriptor and
uses `openat`, never naming a path.

*Cons, native:*
- **Blocked behind decision #3.** [UNVERIFIED] — I cannot estimate the fd
  allocator work without the corpus, and will not invent a number. What I can
  say from this codebase is the shape of it: a keep-list discipline in
  `nwspawn.c`'s `close_others`, the unresolved `512`-versus-`NW_MAX_FDS`
  bound recorded in `HISTORY.md` §12, and an opaque numbering scheme so the
  house never learns an absolute fd — which the removed `NW_WIRE_<fd>`
  mechanism specifically did *not* provide, since it named the number.
- **The kit no longer exists.** §17 removed it; C reintroduces machinery
  deleted the same day, which deserves an explicit reversal rather than an
  incidental one.
- Requires the house to be written against `*at()` syscalls. The seccomp
  allow-list already permits `openat`, but it also permits absolute-path
  `openat`, so the descriptor is a convention rather than a confinement unless
  Landlock backs it.
- A dirfd survives `exec` only if `CLOEXEC` is cleared deliberately — the
  precise pattern behind bugs 5, 9 and 13.

### D — Storage belongs to districts only

Stateful things are VMs; houses stay stateless.

*Cons, native:*
- **No district tier exists in this repo.** This is not an option so much as a
  new execution model, and the largest item on the list by an order of
  magnitude.
- Wrong granularity for the stated cases. A canvas store or a browser profile
  is small and per-user; paying a VM per profile is absurd against the
  measured ~326 kB per house including its supervisor.
- Defers rather than answers: the district needs Q1–Q3 answered for its own
  disk.
- Splits the system into two programming models, so every new component starts
  with an architectural question instead of a plan entry.

### E — Store identity in the plan, delivery deferred *(added)*

The recommendation. Separate the two questions the seed options conflate.

- **Identity** (settled now): a `store-id` namespace in the plan, distinct
  from house names; stores live on a persistent partition outside ESP, slots
  and bricks; materialised at provisioning; never created, migrated or
  reclaimed on-box; a declared-but-absent store is a boot failure.
- **Delivery** (settled later): path today (B's mechanism), dirfd once
  decision #3 lands (C's mechanism). Because the plan names a store-id and not
  a path, delivery can change without touching identity.

*Cons, native:*
- **Two-phase work is easy to abandon after phase one.** If dirfd delivery
  never lands, this is just B with extra indirection, and the indirection
  costs a lookup table nobody needed.
- The store-id namespace is a **second naming scheme** alongside house names.
  This project's own invariant 3 warns about two things that must agree; the
  bakery must enforce their relationship or they drift.
- Provisioning-time materialisation means **the disk image and the plan must
  agree**, and they are produced by different tooling. A plan naming a store
  the image lacks is caught only at boot, on the machine, which is the latest
  possible moment.
- Does not solve confinement, bit rot, or the do-not-restart channel. Those
  are orthogonal and must be decided separately (Q4, and below).

### F — Store inside the brick with a copy-on-write overlay *(added, rejected)*

Listed to be dismissed explicitly. The brick is sealed and content-addressed;
an overlay's upper layer must persist somewhere outside it, so this relocates
Q1 without answering it, and adds an overlay filesystem to the TCB's
assumptions. **Out.**

## Bit rot (decision #5)

[UNVERIFIED] as to what #5 requires. What is true regardless: content
addressing protects bricks and cannot protect a mutable store, and the plan
cannot fix bit rot — a plan is a table, not a filesystem.

The only real mitigations are below the plan: a checksumming filesystem on the
persistent partition (btrfs/ZFS, or dm-integrity beneath ext4), or
house-maintained checksums inside the store. The first is a provisioning
decision and belongs with route (i); the second makes every stateful house
implement its own integrity scheme and is how you get five incompatible ones.

**Note the interaction with Q4:** filesystem-level detection surfaces as an
I/O error to the house, which then has to convert it into whichever
do-not-restart channel is chosen. Detection and refusal are separate
mechanisms and both are needed; neither implies the other.

## Recommendation

**Option E, with B's delivery mechanism for the first house.**

The reasoning is that the hard, expensive-to-reverse question is *identity* —
where bytes live, what they are keyed to, and what happens on rollback — and
it is answerable now without tripping decision #3. The question that is
blocked, *delivery*, is the cheap one to change later, because it is
`nw-sup`-local and touches no data on disk. Choosing a path today forecloses a
dirfd tomorrow **only if the plan names a path**; if it names a store-id, it
does not.

Against C directly: C is the better end state and I would expect to land there
after #3. Doing it first means blocking a real need — every stateful house —
behind an fd allocator whose cost I cannot even estimate from here, and
reintroducing kit machinery removed hours ago.

Against B alone: B answers delivery and leaves identity implicit in a path
string, which is the field that will be wrong after the first rollback or
rename.

Against A and D: A is not buildable without edges. D is a new execution tier
for a per-user canvas store.

**The three things I would want decided before any of it is written**, because
they are not mine to choose and they are all upstream of the code:

1. Which route out of the creation contradiction — (i) provisioning-time,
   (ii) relax constraint 3 for plan-named stores, or (iii) partitions. This
   changes what the bakery emits.
2. Whether unreferenced stores accumulating forever is acceptable. Constraint
   3 says it must be; that should be an explicit acceptance, not a discovery.
3. Which do-not-restart channel, given that all four candidates are checkable
   rather than structural and the reserved-exit-code option cannot cover death
   by signal.

## What the corpus may already answer

Flagged as genuine unknowns rather than gaps in the design:

- Whether **decision #14** really removed `critical`. If it did, this repo is
  stale and `critical-halt` tests a retired feature. If it did not, `critical`
  is available as a coarse per-unit do-not-restart policy and Q4 gets cheaper —
  though still not a per-exit signal.
- The **fd allocator's actual scope and cost** (decision #3), which is the
  only thing standing between B and C.
- Whether **#5** specifies a mitigation or only requires one.
- Whether the persistent partition already exists in the disk layout, which
  would settle Q1 immediately.
- Whether **districts** are real enough to make D a live option.
- The house style for `docs/options/`, which this doc has guessed at.
