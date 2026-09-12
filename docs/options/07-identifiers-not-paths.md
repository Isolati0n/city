# 07 — Carrying identifiers instead of paths

Status: **options, not a decision.** Written 2026-09-10.
Downstream of the `..` guard landed the same day; that guard is the reason
this doc exists and is explicitly not the answer.

## What happened

`nwcheck.c`'s `path_ok_len` validated a leading `/`, printable bytes, a length
and trailing NULs, and nothing else. It had no notion of a path *component*.
So this baked clean, passed `nw-check`, and booted:

```
house one /bin/brick kind=oneshot lids=newns,seccomp brick=/nw/bricks/<hash>/../..
```

The house's `/` became `/tmp/nw-init-run/nw` — every brick on the machine and
the store — and the supervisor still logged `lid brick` and exited 0. Nothing
reported anything. That is invariant 6 ("its `/` is its own tree … invisible
to every other house and to the machine") being false while every check
passed.

`..` is now rejected inside `path_ok_len`, which is the one site every path in
a plan passes through, so `exec_path`, `brick` and every bind are covered by
one check. `test_path_traversal_refused` pins it, with a negative control.

## Why that is a guard and not a fix

**Rejecting `..` closes traversal. It does not close symlinks.**

A brick directory whose name resolves through a symlink escapes exactly as
cleanly, and both `mount(2)` and `pivot_root(2)` follow symlinks. Nothing in a
sealed plan can tell you whether `/nw/bricks/<hash>` is a real directory or a
link to `/`. The plan is validated offline, possibly on another machine,
against a filesystem that does not exist yet; the string is resolved later, by
a kernel, against a tree the validator never saw.

So the property "this path stays inside the brick" is **not a property of the
plan**. It is a property of the filesystem at the moment `nw-sup` runs, and no
amount of validation in `nwcheck.c` can establish it.

That shape should be familiar. A descriptor number is an integer whose meaning
is assigned by a table this process does not control; bugs 5, 9 and 13 were
all that, and the answer was not a better bounds check — it was to stop
carrying the number. `close_others` sweeps `/proc/self/fd` rather than
trusting arithmetic, and `nwspawn.c` has no `BASE + i` left at all. **A path
is the same class of value and has not had the same answer applied to it.**

## What the plan actually needs to carry

Four kinds of path exist in `struct nw_unit` and `struct nw_bind` today. They
are not the same problem and should not get the same treatment.

### 1. `brick` — an identity, currently spelled as a location

A brick is **content-addressed**. `/nw/bricks/<hash>` carries exactly one bit
of real information, the hash; the rest is a constant that the runtime already
knows. The plan is carrying a location in order to express an identity.

Carry the identity: 64 hex characters, validated as 64 hex characters, and let
`nw-sup` construct the path. There is then no path in the plan to traverse and
no string for a filesystem to reinterpret — **escape stops being
representable** rather than being checked for. The field shrinks from 96 bytes
to 32 (or 33; see the packing note below).

This does not close symlinks by itself. It reduces the attack surface to "is
`/nw/bricks/<hash>` itself a link", which is a property of the brick store —
one directory, built by one thing, checkable once at boot — rather than a
property of every string in every plan. Combined with `openat2(2)` and
`RESOLVE_NO_SYMLINKS | RESOLVE_BENEATH` from a descriptor on `/nw/bricks`, it
closes fully. That is the point of moving it: the remaining check has one
subject instead of N.

### 2. `bind` sources — arbitrary strings, and the widest surface

`struct nw_bind` carries `char path[128]`, free-form, and `nw-sup` concatenates
it onto the brick to form the target and uses it verbatim as the source. Today
`bind=/etc/../etc` is refused; `bind=/etc` never was, and nothing says it
should not be.

The honest observation is that **nobody has ever wanted an arbitrary bind.**
Every use so far is one of a very small set. So enumerate the set:

| kind | what the plan carries | what `nw-sup` constructs |
|---|---|---|
| store | store-id | `/nw/stores/<store-id>` |
| display | nothing (the compositor socket) | the well-known socket path |

`struct nw_bind` becomes `{ uint16_t unit; uint8_t kind; <id> }` — a tagged
union of things the runtime knows how to build, not a string it is handed. A
kind the runtime does not know is `NW_E_BINDKIND` at bake time, which is
strictly better than a path it does not understand at mount time.

**This is the item with the real design cost**, because it is the one that
forecloses. Adding a bind kind becomes a format change rather than a plan
edit. That is the trade being proposed and it should be made deliberately: it
is the same trade as removing edges (§17), and it is only correct if the set
really is small. If a house genuinely needs an arbitrary host path, this is
the wrong design and the guard is the right answer after all.

The display socket is named because `driftwm` is inherent to the system and
will never be replaced, so a well-known path for it is a constant, not a
policy. **The compositor is not yet in the tree; this row is a sketch, not a
requirement.**

### 3. `exec_path` — genuinely a path, and much safer

It names a binary **inside the brick**, and it is resolved *after* the pivot,
inside a sealed content-addressed tree. There is no host filesystem left to
traverse into: the worst a bad `exec_path` can do is fail to exec, or exec the
wrong thing in a tree the brick's author controls.

**Keep it a path.** `..` rejection is exactly the right guard here, not a
stopgap — it is a small surface with an owner. Note the surviving hole: a
symlink *inside a brick* pointing out of it would be followed by `execv`,
which is a property of the brick builder, not of the plan.

### 4. `<slots>/current` — already right

Bounded, `[A-Za-z0-9_-]` only, so it cannot contain `/` or `.` and cannot
escape the slots directory (`HISTORY.md` §21). It is the pattern the other
three should follow, and it already works.

## Cost of the migration, honestly

**Format.** This is **`NWPLAN07`** — it said `NWPLAN06` until 2026-09-11,
when that number was spent on the `window_s` removal (`blob.h`, and see
`docs/plans/01` phase 3, which carried the same stale reservation). Two
documents reserving a magic that the code has already used for a different
layout is how two incompatible formats come to share a name; read
`NW_MAGIC` before spending the next one. The unit sizes below are also
pre-`window_s` (262, not 260).
`struct nw_unit` loses `brick[96]` and gains a
32-byte hash (a 64-hex string; store it as 32 raw bytes, or 64 characters
validated as hex — raw is smaller and makes "is it hex" unrepresentable rather
than checked, at the cost of being unreadable in a hex dump, which has
mattered during debugging). Unit shrinks 262 → 198 or 230. `struct nw_bind`
loses `path[128]` and becomes 4–20 bytes depending on the id width, so
`NW_MAX_BINDS` stops being a meaningful memory constraint.

**The four places.** The fd formula does not move: it depends on unit
count, not on field widths, so `blob.h`'s `_Static_assert`,
`bakery/nw-cc.py`, `fdNeed` in `plan.als` and `FdNeed` in `Plan.tla` are
**untouched**. What does move is `NW_BRICK_LEN` and `NW_PATH_LEN`, which live
in `blob.h` and `bakery/nw-cc.py` only. The specs gain a `Hash` sig and a
`BindKind` enum in place of `Path`, which is small. Cost: four places, all
of them real.

> **Corrected 2026-09-11.** This paragraph said the fd formula "is `8 + 2n`"
> and that the two spec changes are "neither executed by anything, so
> neither can be verified (see `plan.md`)", and costed the option as "two
> real places, two unverifiable ones". Both halves are now false and the
> second was load-bearing for the costing. `tools/jars/` holds TLC and
> Alloy and `test_specs_are_checked` runs both inside `make test`, so a
> spec change here is a change to something that executes — and `plan.md`,
> cited above as corroboration, is one of the files whose copy of that
> sentence was already corrected. **This is the fifth file to carry it**;
> `claims` found the others in `plan.als`, `Plan.tla`, `.claude/rules/plan.md`
> and `proofs/README.md` over two earlier rounds. The `8 + 2n` was a sixth
> prose copy of both the reserved value and the multiplier, in a file
> nothing checks, so it is gone rather than updated.
>
> The option itself is unaffected — this is a costing correction, not a
> change of recommendation.

**Error codes.** `NW_E_BRICK` changes meaning (not-64-hex rather than bad
path). `NW_E_BINDPATH` is replaced by `NW_E_BINDKIND`. The enum and `errs[]`
must move together, as always.

> **LANDED 2026-09-12, and the first sentence is what actually happened,
> inverted.** `NW_E_BRICK` was **retired**, not repurposed: there is no
> invalid value of 32 raw bytes to report, so "not-64-hex" is not a check
> that exists in the blob — the hex spelling only exists in the `NW_BRICK`
> environment handoff, where `nw-sup` validates it and `die()`s rather than
> returning a plan error. A code kept alive under a new meaning is the
> version-namespace defect the `NWPLAN06` → `07` bump exists to avoid.
> `NW_E_BINDPATH` still exists and is unchanged: binds are still paths.
> `HISTORY.md` §51. (`docs/plans/01` got this correction on the day and
> this file did not, which is the same prediction surviving in the copy
> nobody edited. `drift` and `tcb-review`.)

**`nw-sup`.** Gains the two constructors and loses the concatenation at
`nwsup.c:91`. Smaller, not larger — the string handling in the TCB goes down.

**The baker.** Gains `brick=<hash>` and `bind=store:<id>` syntax, loses path
validation. `tests/run.py`'s `make_brick` returns a hash instead of a path,
which is a simplification.

**What it does not fix.** A symlinked `/nw/bricks` or a symlink inside a
brick. Those need `openat2(RESOLVE_BENEATH)` and a brick builder that refuses
to bake links out of the tree. Both are separate work and neither is blocked
by this.

## Options

### A — Identifiers for brick and bind, path only for `exec_path` *(recommended)*

Everything above. Escape becomes unrepresentable for the two fields where the
plan cannot possibly establish the property, and stays a checked guard on the
one field where the surface is small and owned.

Cost: a format bump, a bind-kind enum that forecloses on arbitrary binds, and
the brick store becoming a thing with rules.

### B — Keep paths, add `openat2(RESOLVE_BENEATH)` in `nw-sup`

No format change. `nw-sup` resolves every path from a descriptor on
`/nw/bricks` with symlink and escape resolution disabled, so the kernel
enforces what the plan cannot.

Cheaper and closes symlinks too. But it puts the property back in the TCB as a
runtime check rather than removing it, keeps the free-form strings, and fails
at mount time rather than bake time — the plan still expresses things that
cannot work. This is the "check for it" answer to a problem the project's
recurring answer says to design out. **Worth doing anyway as defence in
depth**; it is not an alternative to A so much as the thing A should also do.

### C — Keep the `..` guard alone

What exists now. Traversal closed, symlinks open, the property still not one
the plan can establish. Acceptable only as long as bricks are built by one
trusted thing on the same machine, which is true today and is exactly the
assumption that will stop being stated.

### D — Do nothing until the brick builder exists

Defensible sequencing: there is no brick builder in this tree, `/nw/bricks` is
populated only by the test harness, and designing the identifier format before
the thing that produces the identifiers is how the bind table got a free-form
path in the first place. The risk is that the guard's limits stop being
remembered — which is what `HISTORY.md` §24 and this document are for.

## The question that decides it

**Is the set of bind kinds small and closed?** If yes, A. If a house will
plausibly need an arbitrary host path, the enum is wrong and the answer is
B + C, with the honest statement that escape is prevented at runtime by the
kernel rather than made unrepresentable by the format.

Nothing here should be built before the brick builder settles what a brick
identity actually is.
