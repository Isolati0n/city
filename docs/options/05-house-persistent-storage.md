# 05 — House persistent storage

Status: **options, not a decision.** Written 2026-09-10.
Supersedes an earlier draft numbered `001-`, renumbered to the two-digit house
convention. **[UNVERIFIED]** — `docs/options/` was empty in this repository, so
`05` is inferred from the three filenames given in the brief
(`03-fd-handle-allocator.md`, `04-rescue.md`, `S6-PARITY.md`). If `05` is
taken, this file moves.

## Contradiction check: VOID — not performed

**No corpus was available.** `MASTER.md`, `DECISIONS.md`, `docs/` and the
grok-web chats are absent from this repository, untracked, and never tracked in
any commit; `list_repos` returns one repository. The instruction was to read the
corpus and stop on contradiction. That check **was not performed and no claim
is made about it.** This document must not be read as "nothing in the corpus
contradicts this."

Everything this doc knows about decisions #3, #5, #14, H1 and R3 came from the
brief's prose, not from the corpus. Every such premise is tagged
**[UNVERIFIED]** at the point of use, and those tags stay until someone checks
them against the real register.

Vocabulary (brick, district, ESP, first dawn, R3, H1) appears nowhere in this
repository and is used as the brief used it.

## The gap — confirmed

```
grep -rniE 'persist|storage|store|state_dir|statedir|datadir|data_dir|
            volume|mount|disk|/var|writable|first.?dawn' \
     --include='*.c' --include='*.h' --include='*.py' \
     --include='*.als' --include='*.tla' --include='Makefile' .
  → NO MATCH
```

(excluding `--mount-proc` and `CLONE_NEWNS`, the harness and the namespace lid)

`struct nw_unit` is `name`, `exec_path`, `critical`, `budget`, `window_s`,
`lids`. A house receives `/dev/null` on fd 0 and its log pipe on 1 and 2;
`close_others` sweeps the rest. There is nowhere for a house to keep anything.

## What changed since the earlier draft

Three things, and they move the answer.

**1. §17 is permanent.** Edges are erased, not under review. Options A and C
are re-costed against that as settled fact rather than as a pending question.

**2. Decision #3 is discharged.** [UNVERIFIED as to its original text, but the
discharge is recorded in `HISTORY.md` §18.] The fd-allocator gate existed
because edges and fixed descriptor bases collided — bugs 5, 9 and 13. With no
edges there is no descriptor arithmetic anywhere in the spawn path and no
allocator problem. **Option C is unblocked.** This doc's earlier "blocked
behind #3, duration unknown" is withdrawn.

**3. Constraint 3 was over-stated and is corrected by the operator.**
Bakery-only authorship applies to **plans**, not to filesystem objects. No
on-box tool authors or rewrites a plan; nothing ever said no on-box tool may
create a directory. The creation contradiction this doc previously identified
therefore dissolves — all three routes are legal, and the question is which is
best.

**And one new tension created by the fix.** `CLAUDE.md` invariant 5 now reads
"**the init provisions nothing** … there is no third thing and no mechanism for
granting one." Taken literally, that forecloses option C, because a dirfd is
exactly a third thing. See *The invariant-5 question* below; it is the single
decision that picks the delivery mechanism.

## The five questions

### Q1 — Where does it live, relative to ESP, slots, bricks?

Forced by R3 [UNVERIFIED], and identical for every option that stores anything:
**outside all three.** A separate persistent partition.

- Not the **ESP** — boot filesystem, typically FAT, no ownership, no checksums.
- Not a **slot** — slots roll back; data must not.
- Not a **brick** — sealed and content-addressed; a mutable store inside one
  either breaks the seal or needs an overlay whose upper layer must live
  somewhere else anyway, which relocates the question.

### Q2 — Who creates it?

**All three routes are now legal.** Re-costed on merit:

| route | for | against |
|---|---|---|
| **(i) Provisioning-time** — the store tree is written when the machine is imaged | Nothing on-box creates anything; earliest possible failure detection is still boot, but the artifact is auditable offline | The disk image and the plan are produced by different tooling and must agree; a plan naming a store the image lacks fails on the machine, the latest possible moment. Adding a stateful house requires re-imaging, which is absurd for a canvas store |
| **(ii) First-dawn** — `nw-sup` `mkdir`s exactly what the plan declares | Plan and reality cannot disagree, because reality is derived from the plan. Adding a stateful house is a bake, not a re-image. Smallest moving parts | Puts a `mkdir` in a TCB process. Needs a precise rule for what "exactly what the plan declares" means (mode, owner, parents) or it becomes a policy engine |
| **(iii) Storage-as-partition** — the plan names a block device that must pre-exist | Coarsest isolation, and the filesystem boundary is real rather than conventional | Caps stateful houses at the partition count; repartitioning to add a house is worse than re-imaging; wastes space at every granularity |

**Pick (ii), first-dawn materialisation by `nw-sup`.** With constraint 3
corrected it is no longer illegal, and it is the only route where the plan is
the single source of truth about what exists. (i) creates a second authority —
the image — that must be kept in agreement with the plan, and this project's
own invariant 3 is a standing warning about two things that must agree. (iii)
is a resource allocation scheme wearing a storage design.

The rule for (ii) should be deliberately rigid so it cannot grow into a
package manager: **create the store directory if absent, with a fixed mode,
never recursively beyond one level, never delete, never migrate, never touch
contents.** Anything more is out of scope by constraint 3 as corrected.

### Q3 — Slot swap, rename, drop

**Storage follows a `store-id` declared in the plan and distinct from the house
name.** This is the load-bearing decision in the whole document.

Keying on house name is the trap, and it is this project's signature failure
mode. Rename `canvas` to `canvas2` in slot B and a name-keyed store silently
hands the house an empty directory while the real data sits orphaned under the
old name. **Nothing crashes.** Bugs 4, 9 and 13 were all silent wrong routing
and none of them crashed; §15's wire-order defect was the same shape again.
A store-id decouples the two so a rename is just a rename.

- **Rename, same store-id** — data follows. Correct.
- **Rename, store-id changed** — new empty store, old one orphaned. The bakery
  can and should warn: a store declared in the previous plan and unreferenced
  in this one is almost always a mistake.
- **Slot B drops a house that has data** — the store persists, unreferenced.
  Under constraint 3 as corrected, an on-box tool *may* now legally reclaim it,
  but nothing should: reclamation is destruction, and no design here has a way
  to know the drop was intentional. Recommend explicit offline reclamation and
  accept unbounded accumulation until then.
- **Rollback to an older slot** — works, because the store is outside the slots
  and the older plan names the same store-id. **But the data may be newer than
  the plan's code expects.** Nothing here can fix that; the house must tolerate
  reading data written by a later version of itself, or refuse to start. That
  refusal is Q4.

### Q4 — Integrity fault and the do-not-restart channel

**Measured.** `nwsup.c`:

```c
if (WIFEXITED(st) && WEXITSTATUS(st) == 0) _exit(0);       /* clean stop */
if (critical) _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 71);
... budget/window ring, restart until exhausted ...
```

Exit 66 and exit 99 are byte-identical: both nonzero, both non-critical, both
restarted up to `budget` (3) within `window_s`.

**`critical` is not available as the channel**, and the reason is not
[UNVERIFIED] decision #14 — it is that `critical` is a **per-unit policy fixed
at bake time**. Even working as intended it cannot distinguish "this death was
corruption" from "this death was an ordinary crash". That reason survives
however #14 resolves. (Separately: `critical` is live in this repo and has a
defect — it halts the city when a critical house exits **0**. See `HISTORY.md`
§18; recorded, unresolved, not this doc's business.)

Five candidates:

1. **Reserved exit code named in the plan.** Cheap, no handle, sealed contract.
   *Cons:* one integer carries no detail; collides with language runtimes
   (Python exits 1 on an uncaught exception, `sysexits.h` values are widely
   reused); **cannot cover death by signal** — a house that `SIGSEGV`s or is
   `SIGKILL`ed while detecting corruption never reaches its exit, and is
   restarted straight back into the corrupt store. Checkable, not structural.
2. **Sentinel file in the store.** *Cons:* the supervisor must read inside the
   store, breaking opacity; and a store corrupt enough to warrant the fault may
   be one the house cannot write to. Fails exactly when needed.
3. **Status descriptor in the kit.** No longer blocked (#3 discharged) but
   collides with invariant 5 — see below. *Cons:* a third provisioned thing;
   still requires the house to be alive enough to write it, so it shares
   candidate 1's signal problem.
4. **Supervisor-side checksum before start.** *Cons:* the supervisor must
   understand the store format, the opposite of opacity, and it is O(store
   size) on every start — unusable for an image cache.
5. **Structural: make the house unable to run on a corrupt store** *(new,
   costed below)*.

#### Candidate 5 — structural, in two forms

The brief is right that 1–4 are all *guards*, and the project's method is to
design the problem out. The move that does that is to **invert the burden**:
stop asking a dead house to report a fault, and require a live house to prove
health. Silence then means fault, which automatically covers death by signal,
because a `SIGKILL`ed process cannot prove anything.

**5a — Proof-of-clean-close (a dirty marker).** The store carries a one-word
header. `nw-sup` sets it *dirty* before `execv` and the house clears it on
clean shutdown; a store found dirty at start is not handed over and the unit is
not restarted. A killed house leaves it dirty automatically.

*Cons, native:*
- **Far more conservative than today.** Every unclean death — an ordinary
  segfault in code nowhere near the store — becomes a permanent stop. To avoid
  that the marker must denote *in-flight mutation* rather than *open*, which
  means the house implements transactions. That is what real stateful software
  does, but it is a large requirement to impose on "a canvas store".
- **Power loss sets it.** Every unclean reboot leaves every stateful house
  refusing to start, and with no on-box repair tool the machine needs an
  operator. This is the single biggest objection and it is severe.
- The supervisor learns one word of store format. Small, but nonzero, and it
  is a precedent.

**5b — Atomic whole-store replacement.** The store is immutable and
content-addressed; a commit writes a new store and `rename()`s it into place.
**A partially-written store cannot exist**, so corruption is not detected, it is
impossible. This is the only candidate that is genuinely structural rather than
a better guard, and it is the same trick bricks already use.

*Cons, native:*
- **O(store size) per commit.** Fine for a canvas layout or settings; absurd
  for an image cache, a browser profile, or a database — which are named in the
  brief as motivating cases.
- Needs 2× peak store size free on the partition.
- Does not survive the case where the *house's own logic* writes consistent-
  but-wrong data. It removes torn writes, not bugs.

**Honest reading:** 5b is correct and does not scale; 5a scales and is
conservative to the point of fragility. A real system probably wants 5b for
small stores and 5a-with-transactions for large ones, which means the plan must
declare which discipline a store uses — a `store-kind` field alongside the
store-id. That is more design than this doc should settle, and it is the reason
Q4 should not be decided at the same time as Q1–Q3.

### Q5 — Smallest version, and what it forecloses

One store, one house:

1. Plan gains a store table: `store <store-id>` entries; a unit references at
   most one store by id.
2. `nw-sup` materialises the directory at first dawn if absent (route ii),
   fixed mode, one level, never deletes.
3. Delivery per the invariant-5 question below.
4. No GC, no migration, no sharing, no quota, no integrity discipline.

**Forecloses almost nothing, provided the plan names a store-id and not a
path.** Delivery can change later without touching data. It does foreclose
multi-house shared stores (deferred, not prevented) and on-box reclamation
(recommended, not required).

**What it does not give you: confinement.** `chdir` does not stop a house
walking `../`. Landlock would, but the Landlock lid degrades silently to no
restriction when the ABI probe fails — a plan can declare `landlock` and
receive nothing but a log line. The smallest version provides *addressing*,
not *isolation*, and must be described that way.

## The invariant-5 question — the decision that picks delivery

`CLAUDE.md` invariant 5 now reads: *the init provisions nothing … there is no
third thing and no mechanism for granting one.*

- If that is **descriptive** — a statement of what is true today — then option
  C is available and, with #3 discharged, is the better end state.
- If it is **prescriptive** — never a third thing — then C is out permanently
  and B is the only delivery mechanism, forever.

This is not a detail. It decides whether storage arrives as a descriptor or a
path, and it should be settled deliberately rather than inferred from wording
written to describe the post-§17 state.

## Options

### A — No storage; one designated house owns the disk

**Out, permanently.** It requires other houses to reach the owning house, and
§17 removed provisioned IPC for good. The precise position: the init provisions
no channel, and a `lids=seccomp` house cannot make one (`__NR_socket` absent
from the allow-list, enforced by a live test). A `lids=none` house *could* open
its own socket — so A is not literally impossible, it is only achievable by
declining the seccomp lid on every participating house, which trades the
storage problem for a strictly worse isolation posture.

*Other cons, if pursued anyway:* centralises rather than removes the problem —
the owning house still needs Q1–Q3 answered; its death takes all state with it;
and all storage I/O serialises through one supervisor's restart budget.

### B — Plan-declared path

`nw-sup` `chdir`s (or mounts) before `exec`. No new handle.

*Cons, native:*
- **The house learns an absolute path** — strictly more information than a
  descriptor, and guessable and traversable besides.
- **`chdir` does not confine.** Only Landlock does, and it is best-effort here
  with a silent-degradation path.
- **Per-unit mounting is not implemented.** `NEWNS` is a bare
  `unshare(CLONE_NEWNS)` with no `pivot_root` and no remount — it isolates
  propagation, not the view. "mounts before exec" is new work.
- **A path in a sealed plan pins an on-disk layout.** Rollback into a slot with
  a different path convention orphans data — the R3 case. Mitigated only by
  store-ids, i.e. by option E.
- Format cost: a path field per unit, a new `NW_E_*` code, `errs[]` and the
  `nw_errstr` bound moved together.

### C — Storage as a descriptor (dirfd)

**No longer blocked.** #3 is discharged and H1 is retired, so both of this
option's former objections are gone.

*Cons, native:*
- **Collides with invariant 5 as currently worded.** A dirfd is a third
  provisioned thing. Unresolved above.
- Requires the house to be written against `*at()` syscalls.
- The descriptor is a *convention*, not a confinement: the seccomp allow-list
  permits `openat` with absolute paths too, so without Landlock the house can
  ignore the dirfd and open the store by path anyway.
- A dirfd surviving `exec` needs `CLOEXEC` cleared deliberately — the precise
  pattern behind bugs 5, 9 and 13. §17 removed all such arithmetic from the
  spawn path; this reintroduces the *category*, though not the `BASE + i` form.

### D — Storage belongs to districts only

*Cons, native:* no district tier exists in this repository, so this is a new
execution model rather than an option; wrong granularity — a VM per browser
profile against a measured ~326 kB per house; defers rather than answers, since
the district needs Q1–Q3 for its own disk; and it splits the system into two
programming models.

### E — Store identity in the plan, delivery decided separately *(recommended)*

- **Identity, settled now:** a `store-id` namespace distinct from house names;
  stores on a persistent partition outside ESP, slots and bricks; materialised
  at first dawn by `nw-sup` under a rigid create-only rule; never migrated;
  reclamation offline only.
- **Delivery, settled by the invariant-5 question:** path (B) or dirfd (C).
  Because the plan names a store-id rather than a path, the choice can change
  later without touching data on disk.

*Cons, native — all three retained from the earlier draft:*
- **Two-phase work is easy to abandon after phase one.** If the delivery
  question is never revisited, this is B with an indirection nobody needed.
- **The store-id namespace is a second naming scheme** alongside house names.
  Invariant 3 is a standing warning about two things that must agree; the
  bakery must enforce the relationship or they drift.
- **Under route (ii) the plan is the only authority**, which removes the
  image/plan agreement problem — but a plan that declares a store and a house
  that never uses it still creates a directory, so the plan can litter.

### F — Store inside the brick with a CoW overlay *(rejected)*

The upper layer must persist outside the brick anyway, so this relocates Q1
without answering it and adds an overlay filesystem to the TCB's assumptions.
**Out.**

## Bit rot — decision #5 [UNVERIFIED]

Content addressing protects bricks and cannot protect a mutable store, and the
plan cannot fix bit rot: a plan is a table, not a filesystem. The mitigations
live below the plan — a checksumming filesystem on the persistent partition
(btrfs/ZFS, or dm-integrity beneath ext4), or house-maintained checksums inside
the store. The first is a provisioning decision; the second yields five
incompatible schemes.

Note the interaction with Q4: filesystem-level detection surfaces as an I/O
error, which the house must then convert into whichever do-not-restart channel
is chosen. **Detection and refusal are separate mechanisms and both are
needed.** Candidate 5b is the only proposal that removes the need for the
first.

## Recommendation

**Option E, with route (ii) first-dawn materialisation, and delivery deferred
one decision — the invariant-5 question.**

The expensive-to-reverse half is identity: where bytes live, what they are
keyed to, what survives rollback. It is answerable now and nothing gates it.
The cheap half is delivery: `nw-sup`-local, touches no data on disk, and
changeable later *provided the plan names a store-id*.

If invariant 5 is descriptive, I would take **C** for delivery, now that #3 is
discharged — a descriptor is the better mechanism and the reasons it was
blocked no longer hold. If invariant 5 is prescriptive, **B**, accepting that
the house learns a path.

On Q4, I would **not** decide it with Q1–Q3. The honest position is that
candidate 1 is incomplete for the reason the operator identified, candidate 5b
is correct and does not scale, and 5a scales but converts every unclean reboot
into an operator event. That trade deserves its own options doc.

**Three things that are the operator's, not mine:**

1. Is invariant 5 descriptive or prescriptive? Picks B or C.
2. Is unbounded accumulation of orphaned stores acceptable pending offline
   reclamation? Recommended, but it is a disk-full incident waiting.
3. Which integrity discipline, per store kind — and whether `store-kind`
   belongs in the plan alongside `store-id`.

## What the corpus may already answer

Genuine unknowns, not design gaps:

- Whether **#14** removed `critical`. A passing `critical-halt` test currently
  enforces the opposite of a reportedly locked decision (`HISTORY.md` §18).
- Whether **#5** specifies a bit-rot mitigation or only requires one.
- Whether the persistent partition already exists in the disk layout — settles
  Q1 immediately.
- Whether **districts** are real enough to make D live.
- Whether `03-fd-handle-allocator.md` should now be marked discharged, given
  §18.
- The real house style for `docs/options/`, and whether `05` is free.
