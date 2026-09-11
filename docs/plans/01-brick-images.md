# Plan 01 — Bricks become read-only erofs images

Status: **decided, not built.** Written 2026-09-10. No code yet.

This is a *plan*, not an options doc: the decision is made
(`docs/options/08`, Decision section) and this says how to get there. Every
phase leaves the tree building and the suite green, and every phase names the
negative control that makes its test mean something.

## What is being built

A brick stops being a directory and becomes a single read-only image file,
`/nw/bricks/<64-hex>.img`, named by the sha256 of its own bytes. `nw-sup`
loop-mounts it read-only, applies declared binds, and pivots as it already
does. The seal becomes a property of storage rather than of the Landlock lid,
and the plan carries a hash rather than a path.

**Two properties this closes**, both measured in `08`:

- A content-only hash names a working brick and a broken one identically.
  Inside an image, mode and link text are part of the bytes, so the problem
  is gone rather than solved by a canonical serialization nobody wants to
  write.
- A `lids=none` house can currently write into its own content-addressed
  brick. A read-only image refuses with no lid involved.

## Non-goals, stated so they are not drifted into

Not in this plan: cgroups, `promote`, `/nw/stores` and storage, the bind half
of `07` (bind kinds), the derivation bakery, any brick with a package manager
in it. Bootstrap stays as `08` records it — the first tree comes from
outside, permanently, and nothing here designs for it.

## Prerequisites, and how to check them rather than assume

The target kernel needs `CONFIG_EROFS_FS` and `CONFIG_BLK_DEV_LOOP`. This
container has erofs (`/proc/filesystems`) and `losetup`; the target machine
has been checked for neither.

`tests/run.py` must report both in `print_environment()` **before phase 2
lands**, and phase 2's tests must `skip()` loudly where either is missing.
This is not optional: the Landlock lid ran for its whole life without ever
executing because a capability was absent and the suite went green anyway
(`HISTORY.md` §26).

The bakery needs `mkfs.erofs`. It is not installed by default anywhere we
have looked, and the bakery is not the running machine, so this is a bakery
requirement rather than a runtime one.

## Phase 1 — the baker packs an image. No TCB change. **LANDED 2026-09-11.**

`bakery/mkbrick.py` and `test_brick_image_reproducible`. The flag set is
exported as `EROFS_FLAGS` and the test imports it rather than copying it,
so the negative control drops a flag from the list the packer actually
uses. Three controls run, all failing as required: `-U` removed from the
packer (two packs of one tree disagree), `-T 0` removed (same), and the
image named by the tree path instead of its own bytes (the hash no longer
matches the filename).

The measured behaviour, on this machine: the same tree packs to one hash
twice; a copy at a different path with every mtime rewritten packs to the
same hash; and with `-U` dropped the two packs differ, which is what
makes the first two mean something rather than being a report on
`mkfs.erofs`'s good manners.

Nothing at runtime reads these images yet. That is phase 2.

### The original text of this phase

`bakery/mkbrick.py` (new, not in the TCB): take a directory tree, pack it
with the exact flag set from `08`, name the output by the sha256 of the
resulting file, and print the hash.

```
mkfs.erofs -T 0 -U 00000000-0000-0000-0000-000000000000 \
           --force-uid=0 --force-gid=0 -zlz4hc  <tmp.img> <tree>
mv <tmp.img> /nw/bricks/<sha256(tmp.img)>.img
```

The flags are the spec. A bake that omits one produces a different name for
identical content, so they belong in one place with a comment saying why each
is there, and the test below is what stops them drifting.

**Test** `brick-image-reproducible`: pack the same tree twice and compare;
pack a copy of the tree at a different path with every mtime rewritten and
compare again. Both must give one hash.
**Negative control**: drop `-U` and the two packs must differ. That is the
control worth running because the UUID is random per-invocation and is the
one flag whose absence changes every byte while nothing about the content
moved.

Phase 1 ships alone and changes nothing at runtime.

## Phase 2 — `nw-sup` loop-mounts the image. The TCB change.

`lid_brick()` currently bind-mounts a directory onto itself because
`pivot_root` needs a mount point. It instead:

1. `open(image, O_RDONLY | O_CLOEXEC)`;
2. `open("/dev/loop-control")`, `ioctl(LOOP_CTL_GET_FREE)` for a device
   number, `open("/dev/loopN")`;
3. `ioctl(LOOP_CONFIGURE)` with the image fd and `LO_FLAGS_AUTOCLEAR |
   LO_FLAGS_READ_ONLY`;
4. `mount("/dev/loopN", mountpoint, "erofs", MS_RDONLY | MS_NODEV, NULL)`;
5. binds, `pivot_root`, detach — unchanged from today.

**`LO_FLAGS_AUTOCLEAR` is the design decision in that list.** The loop device
frees itself when its last reference goes, so there is no teardown path to
get wrong, no cleanup on the `die()` paths, and nothing leaked when a house
is killed. This project's recurring answer: no ordering to get wrong.

Costs to state plainly rather than discover:

- **A filesystem type constant enters `nw-sup`.** `"erofs"`, hardcoded.
  Justified because bricks are one format by decision, and the blob's magic
  already versions the plan format if that ever changes. The alternative —
  carrying the type as a string in the plan — adds a free-form string to the
  TCB's input path, which is what `07` exists to remove. `dawn` already takes
  `NW_ROOT_FSTYPE`, so a type in the TCB is not a new precedent.
- **One loop device per house.** Two houses sharing a brick hash get two
  devices, because each mounts inside its own namespace after `unshare`.
  Wasteful and simple. `/dev/loop-control` allocates dynamically, so the
  binding limit is `max_loop`, and **that is a limit that must be derived and
  checked against `NW_MAX_UNITS`, not discovered at 64 houses** — invariant 3.
  Measure it before phase 2 is called done.
- Everything on the failure path is `die()`, per invariant 6: a declared lid
  or a declared brick that cannot be applied stops that house.

**Test** `brick-image-is-a-root`: the existing `brick-is-a-root` retargeted at
images — two houses, two images, different `/id` at the same path, neither
seeing the machine's root.
**Test** `brick-image-is-sealed`: a house with **`lids=none`** attempts to
write into its own brick and is refused. This is the property images buy that
the lid version cannot, so it is the test that justifies the phase.
**Negative control**: drop `MS_RDONLY` from the mount and the write succeeds,
failing the test. Run it; a control that passes means the test is measuring
the announcement again.

## Phase 3 — the plan carries a hash, not a path

`struct nw_unit`'s `brick[96]` becomes a 32-byte binary sha256 (`brick[32]`),
`nw-sup` builds `/nw/bricks/<hex>.img`, and the plan stops carrying a path.
This is `07` option A for the brick half, and it is only meaningful now that
`08` has defined what the hash is *of*: the image file's bytes.

- Unit shrinks 262 → 198 bytes; format becomes `NWPLAN06`.
- `NW_E_BRICK` changes meaning to "not 32 bytes of hash", and the all-zero
  case still means no brick and is still validated byte by byte.
- The `..` guard in `path_ok_len` **stays** — `exec_path` and binds are still
  paths, and there it is the right answer rather than a stopgap. But
  `test_path_traversal_refused`'s brick case must be rewritten, because a
  hash field cannot express a traversal at all. That is the point: the check
  disappears because the input class disappeared.
- Four-place drift: `NW_BRICK_LEN` moves to a hash length in `blob.h` and
  `bakery/nw-cc.py`; `plan.als` and `Plan.tla` gain a `Hash` in place of a
  brick path. The fd formula does **not** move — it depends on unit count,
  not field widths.

**Dispatch `drift` after this phase**, per the table in `CLAUDE.md`. It is
exactly the change that rule exists for.

## Sequencing, and what can be interleaved

1 → 2 → 3, and each is independently shippable. Phase 1 is offline and
harmless. Phase 2 is the only TCB change and can land while the plan still
carries a path — the path just points at an `.img` instead of a directory.
Phase 3 is a format change with no runtime behaviour in it.

Do **not** merge 2 and 3. Phase 2 changes how a brick is mounted and phase 3
changes what the plan says; landing them together means a failure has two
candidate causes and the bisect is between them.

## What would make this the wrong plan

Recorded now, while it is cheap to say:

- If `max_loop` turns out to bind below `NW_MAX_UNITS` and cannot be raised,
  per-house loop devices are wrong and the `dawn`-mounts-everything variant
  (`08` Q1 C) comes back.
- If the target kernel lacks erofs and cannot gain it, squashfs is the
  fallback and costs ~7× on mount; the flag set for it is recorded in `08`.
- If a brick ever needs to be writable, none of this survives. It is not
  supposed to — a writable brick is not content-addressed — but the failure
  would present as somebody wanting a house to keep state, which is
  `docs/options/05` and is a different question that must not be answered by
  quietly making the brick writable.
