# 30 — backup tool

Status: **design note. No code in this round.** Gate: the shared
content-addressed store (`docs/options/14-shared-store.md`, `store.c`/
`store.h`, landed) — satisfied, proceed now. Independent of notes 27, 28
and 29.

Every claim below was checked against the tree at the time this note was
written; grep the cited lines rather than trusting the prose if this
note and the code ever disagree.

## Why "compare hash lists" is exactly right for one category and not the other

The operator's framing — content-addressed, so incremental backup is
"compare hash lists, copy what's missing" — is precisely true of one of
the three candidate targets and only approximately true of another. Both
readings matter for the design, so this note states which is which
before proposing a mechanism.

**The shared store is genuinely content-addressed and immutable once
written.** `store.h`'s own contract: `nw_store_put()` names a file
`<dir>/<hex(sha256 of data)><suffix>` and only ever writes it once — "No
policy: no cleanup, no eviction." `write_evidence()` in `nwsup.c` is the
one live caller (`nwsup.c:1140`), naming files at
`NW_EVIDENCE_DIR` (`/nw/evidence`, `blob.h:106`) as `<hex>.evt`. Because
the **name is the hash**, deciding what is missing from a destination
needs no hashing at all at diff time — `ls` both directories and set-
subtract the filenames. Verifying a copy is correspondingly cheap: the
expected hash is already known (it is the filename), so confirming a
copy is correct costs one more read-and-hash pass over the destination
file, not a second independent computation of what the "right" hash
should have been.

**A layer is not content-addressed, and treating it as if it were would
misdescribe the mechanism.** `.claude/rules/runtime.md`'s layers section:
a layer is "keyed by a declared id, never by the house name" — but the
key is a *name* the operator chose, not a hash of the layer's own
bytes, and the whole reason a layer exists is that its content changes
over a house's lifetime (`test_layer_survives_a_restart`'s own state
progression `['absent', 'r', 'rr', 'rrr']` is the tree's own
demonstration that a layer's content is expected to differ from one
backup to the next). So "does the destination already have this
content" cannot be answered by a filename comparison for layers — it
needs either a full copy every time, or a cheap change-detection signal
(mtime+size, the same heuristic every incremental-backup tool not
sitting on content-addressed storage already uses) with the actual
content hash reserved for verification rather than for change detection.

Bricks sit with the store for a different reason: **they are named by
their own hash already** (`bakery/mkbrick.py`'s `brick_suffix()`, final
path `NW_BRICK_DIR/<digest><suffix>`) and, per the operator's own
framing, are regenerable from source (the bakery tree) rather than
irreplaceable — so this note treats them as the same *mechanism* as the
store (hash-named, diff by listing) but lower priority to actually back
up, matching §2's ordering.

## 1. Target: a directory path, and that is the whole abstraction

**Recommendation, matching the operator's own suggestion: the tool
takes one destination — a directory that already exists and is already
writable — and does not know or care whether that directory is a USB
stick, a local second disk, an NFS/SMB/sshfs mount, or anything else.**
Mounting the destination (plugging in the USB drive, mounting the
network share) is the operator's job, done before the tool runs, with
whatever tool the destination technology already provides — this design
adds no network-transport code of its own.

This is not a cop-out; it is the same "descriptor is a path, not a
mechanism" pattern this project already applies elsewhere — a `bind=` in
the plan format is "a path made visible... the same path inside and out"
(`.claude/rules/runtime.md`), not a new transport. A backup destination
plays the identical role: the tool's job is what bytes go where inside
that directory, not how that directory reaches its physical medium.

**One consequence worth stating rather than discovering:** if the
destination is a network mount, ordinary filesystem semantics (a write
that returns success before it is durable on the remote, an unmounted
share silently reading as an empty directory instead of failing) are
the operator's mount's problem, not this tool's. The tool can and should
check the destination exists and looks like a prior backup or an empty
directory before writing into it, refusing an ambiguous case (see §3's
verification and §5's restore precondition) rather than guessing.

## 2. What gets backed up, and in what priority

Ordered exactly as the operator framed it, with the reasoning made
explicit per target:

1. **The shared store** (`/nw/evidence`, `NW_EVIDENCE_DIR`) —
   irreplaceable: it is the historical record of what happened on past
   boots (`docs/options/12-crash-evidence.md`), not derivable from
   anything else on the machine. Highest priority, and the cheapest
   category to back up correctly (§ above).
2. **Layers** (`/nw/layers/<id>/upper` or `<id><suffix>`,
   `NW_LAYER_DIR`) — irreplaceable, and the actual reason an operator
   would reach for this tool: a house's accumulated state (a game's
   save data, any file a long-running house wrote and kept) exists
   nowhere else. Priority equal to the store in what matters, higher in
   what it costs to get wrong, because unlike evidence a layer is
   *mutable* — an incomplete or torn backup of a layer can be silently
   wrong in a way a content-addressed file cannot (§3's consistency
   caveat).
3. **Bricks** (`/nw/bricks`, `NW_BRICK_DIR`), lowest priority, because
   they are regenerable: a brick is `mkfs.erofs` run against a
   deterministic tree the bakery already has on disk elsewhere
   (`bakery/mkbrick.py`), named by the hash of its own resulting image.
   Losing a brick costs a rebuild, not data. **Named as lowest priority,
   not omitted** — a machine with no access to the original bakery
   source tree (a truly bare "restore onto fresh hardware" scenario)
   has no other way to get its bricks back, so backing them up is cheap
   insurance for that specific case even though it is usually redundant
   with the source tree.

**Not in scope, and stated so nobody adds it by extrapolation:** the
`.layers` sidecar and the plan blob itself. Both are baker output —
regenerated from the city file each bake (`plan.md`'s sidecar section)
— so they belong with bricks conceptually (regenerable from source), and
are cheap enough to re-derive that this note does not propose backing
them up at all. What must survive a restore is the **city file** (the
bakery's own source input) existing somewhere the operator controls —
this tool does not manage that; it is an ordinary text file already
outside `/nw/*`.

## 3. Mechanism

**Read destination's existing hash list if any, diff against source,
copy only what's missing — with the qualification §"Why compare hash
lists" above requires per category:**

- **Store and bricks** (hash-named): list source filenames, list
  destination filenames, copy every source name absent from the
  destination. No hashing needed to decide what to copy — the name
  already answers it. This is the exact "diff hash lists" mechanism the
  operator described, because it is genuinely applicable here.
- **Layers** (name-keyed, mutable content): per declared layer id,
  compare a cheap signature — `(size, mtime)` per file for an unsized
  layer's `upper/` tree, or `(size, mtime)` of the single `.img` file
  for a sized one — against a record the tool itself keeps from the
  last backup (a small manifest written to the destination, not
  embedded in the layer format, since the layer format is not this
  tool's to change). A changed signature means re-copy that file (or
  the whole image, for a sized layer — an ext4 image cannot be
  partially re-copied by content the way a directory tree's individual
  files can, so a sized layer is always copied whole when its signature
  changes, which is the honest cost of choosing the fixed-size
  representation for capacity enforcement).

**Read-only against source, never touches a running house or
coordinates with nw-sup — stated as a design constraint, not
merely a description, with the consequence spelled out:** this tool
takes no lock, sends no signal, and does not check whether a house
using a given layer is currently running. If it is, a layer's `upper/`
tree (or `.img` file) can be mutated by the live house *during* the
backup's own read pass, exactly the crash-consistency risk of copying
any live, mounted filesystem image without a snapshot — the copy may be
torn (some files reflect a state before the write, some after,
internally inconsistent). This is an accepted, stated cost of the "never
coordinates" constraint rather than an oversight: the alternative is
either a live lock (which is coordination, explicitly out of scope) or
a filesystem-level snapshot mechanism (LVM/btrfs/ZFS snapshots), which
this note does not assume the target disk supports and does not
propose building. **Recommendation: document this caveat for the
operator plainly — running the tool while the target house is stopped
(or the machine is between boots) is the only way this tool gives a
guaranteed-consistent copy; running it live is best-effort.**

## 4. Verification

**Recommendation: full re-hash of the destination copy after copying,
by default, for both categories — with the cost stated rather than
hidden, and a spot-check option for the case where the cost is
prohibitive.**

- **Store and bricks**: verification is nearly free relative to the
  copy itself. The expected hash is the filename, already known before
  the copy starts; hashing the destination file after writing it is one
  more sequential read, and the write itself already had to read the
  source once — so full verification here costs roughly 50% more I/O
  than an unverified copy (one extra read pass), not double. Given how
  cheap that is, there is no real tradeoff to make: always fully
  verify this category.
- **Layers**: this is where a genuine tradeoff exists. Full verification
  means reading back the entire copied layer (every file in `upper/`,
  or the whole `.img`) and comparing against a hash computed from the
  source at copy time — a second full read pass over data that may be
  large (a sized layer can be declared up to whatever `layer-bytes=`
  the plan gave it). That doubles the I/O of the backup for this
  category. **Recommended default: full verification anyway**, because
  a layer is exactly the data category this whole tool exists to
  protect, and the tool's only value is being trustworthy about what it
  claims to have safely copied — an unverified "backup" that turns out
  to be silently truncated on a flaky USB stick is worse than not
  having attempted one, because it gives false confidence. **Offer a
  spot-check mode as an explicit, named opt-out** (`--quick`, hashing
  only a sampled fraction of blocks) for an operator who has measured
  that full verification is too slow for their destination media, so
  the cost tradeoff is a decision the operator makes deliberately, not
  a default that hides it.

## 5. Restore path — sketched, not built this round

Recovering a layer onto a fresh machine requires all of the following,
stated so the sketch is honest about what this tool alone does not
solve:

1. **The plan must still declare the same layer id.** Layers are keyed
   by declared id, not by house name or by content
   (`.claude/rules/runtime.md`'s "Keyed by a declared id, never by the
   house name" rule) — restoring `<id>/upper` or `<id><suffix>` onto a
   fresh machine with no plan naming that same `layer=<id>` puts the
   data somewhere nothing will ever mount it. This is a precondition on
   the **city file** (the bakery's source input), which per §2 this
   tool does not manage — the operator must have kept it (or rewritten
   an equivalent one) separately.
2. **The paired brick must exist or be rebuilt.** `brick=`/`layer=` are
   required together (`plan.md`'s `NW_E_LAYERPAIR`); a restored layer
   with no matching brick is a plan the checker refuses to boot at all.
   If bricks were not included in the backup (§2's lowest-priority
   framing), the operator rebuilds it from the bakery source tree
   before the restored plan can boot — this is the direct cost of that
   priority call, stated rather than hidden.
3. **Placement**: copy the backed-up `upper/`+`work/` (recreating an
   empty `work/` if it was not itself backed up — it is overlayfs
   scratch space, not data, so there is nothing to restore into it) or
   the `.img` file into `NW_LAYER_DIR/<id>` on the fresh machine, then
   let the existing staging path (`tools/stage-layers.py`, which
   `.claude/rules/runtime.md` already names as "THE RECOVERY" for a
   different failure class) take over from there rather than
   reimplementing its directory-shape decisions in this tool.
4. **What this tool does NOT sketch, deliberately**: reconciling a
   restore against a layer that already exists and has since diverged
   (the fresh machine is not actually fresh — a partial prior boot
   already wrote something). That is a merge decision with no obvious
   automatic answer and is out of scope for "at least sketch it."

## Refused / out of scope for this round

- **Any network transport built into the tool itself** — §1's directory
  abstraction is the whole answer; a network destination is the
  operator's mount, not this tool's protocol.
- **Coordinating with a running house or `nw-sup`** — §3's accepted
  cost; building coordination is a larger, separate design (locking,
  quiescing a house before backup) not asked for this round.
- **Encryption, compression, retention policy, scheduling** — none was
  asked for, and per this project's own rule against machinery without
  a stated need (`CLAUDE.md`'s "Do NOT build anything beyond these two"
  pattern from the tooling-fix instructions this session already
  follows), none is proposed here. If a concrete need surfaces later,
  it is a follow-up, not scope creep folded into this note.

## Definition of done

This is a docs-only note. No code changes. `docs/QUEUE.md`'s new-notes
entry is updated in the same commit that lands this note.
