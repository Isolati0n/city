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

The bakery needs `mkfs.erofs`. It is a bakery requirement rather than a
runtime one, since the bakery is not the running machine. *"It is not
installed by default anywhere we have looked" stood here until 2026-09-11,
directly above a LANDED section reporting bricks packed on a machine where
`which mkfs.erofs` answers `/usr/bin/mkfs.erofs`.* `tests/run.py` reports
its presence in `print_environment()` as of the same day —
`test_brick_image_reproducible` skips on exactly that condition, and a skip
condition missing from the environment block is the gap that let the
Landlock lid run green for its whole life.

**Measured here, 2026-09-11, before any phase 2 code:**

```
erofs in /proc/filesystems      yes (squashfs too)
/dev/loop-control, losetup      present
mkfs.erofs                      /usr/bin/mkfs.erofs (erofs-utils 1.7.1)
max_loop                        8
NW_MAX_UNITS                    64
```

**`max_loop` is not a ceiling and must not be read as one.** It is the
count of devices created when the module loads; `LOOP_CTL_GET_FREE`
allocates past it, and `devtmpfs` creates each node as it goes. Measured
2026-09-11: 80 devices over 80 files, and 64 distinct devices over a single
image. Re-measured 2026-09-12 by binding rather than by reading the
parameter: **4096 devices attached on this same kernel reporting
`max_loop=8`, up to `/dev/loop4095`, with no failure** — the probe stopped
at its own loop bound, not at a ceiling. So the ceiling is **≥ 4096 and was
not found**, which is ≥ 64× `NW_MAX_UNITS`.

*This has now been reported as a hard ceiling once, after the paragraph
above was written.* Phase 2 was described as blocked by `max_loop=8`. It is
not blocked, and the correction is the same one this paragraph already
made: **bind devices to measure the limit; do not read the parameter.** A
report that phase 2 is walled needs a failed `LOOP_CTL_GET_FREE` or a
failed `LOOP_SET_FD` behind it, with the count at which it failed.

One trap in measuring it: `mount -o loop` (util-linux) reuses a device
already backing the same file, so 64 mounts of one image consumed **one**
device. Phase 2 calls `LOOP_CTL_GET_FREE` itself and gets 64. Measure the
call the code makes.

## Phase 1 — the baker packs an image. No TCB change. **LANDED 2026-09-11.**

`bakery/mkbrick.py` and `test_brick_image_reproducible`. The flag set is
exported as `EROFS_FLAGS` and the test imports it rather than copying it,
so the negative control drops a flag from the list the packer actually
uses. The controls below were run by hand and all failed as required
(only the `-U` one is a standing assertion in the suite): `-U` removed
from the
packer (two packs of one tree disagree), `-T 0` removed (same), and the
image named by the tree path instead of its own bytes (the hash no longer
matches the filename).

The measured behaviour, on this machine: the same tree packs to one hash
twice; a copy at a different path with every mtime rewritten packs to the
same hash; and with `-U` dropped the two packs differ, which is what
makes the first two mean something rather than being a report on
`mkfs.erofs`'s good manners.

**Two holes in that test, found by `control` on 2026-09-11 and closed the
same day.** Recorded because "the controls all failed as required" was
true and still left the test green against packers that cannot be right:

- Every assertion above survived a `pack()` that threw its `tree` argument
  away and packed an empty temporary directory. Reproducibility, naming
  and the `-U` control are all satisfied by packing nothing at all,
  consistently — so the test whose name is "content-addressed" never
  asserted the image depends on the tree. Two different trees must now get
  two different names.
- `--force-uid=0 --force-gid=0` were pinned by nothing, because every tree
  in the test was owned by one user. Deleting both left the suite green
  while the sentence they carry was false: measured, a 1000-owned copy
  packs to a different hash without them. The test now chowns a copy and
  requires the same hash, and says in its `ok` line which side ran.

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
  Wasteful and simple. The loop pool is the one resource in this phase that
  is **not** namespaced — `LOOP_CTL_GET_FREE` draws from a global kernel
  pool, and a mount namespace does not partition it.

  **The invariant-3 derivation, settled 2026-09-12.** The requirement is one
  device per brick house, so `NW_MAX_UNITS` devices in the worst case. The
  measured ceiling is ≥ 4096 and was not reached, against an `NW_MAX_UNITS`
  of 64 — so there is no measured number to derive *against*, and a
  hand-written loop limit would be a second copy of a constraint that does
  not bind. **Nothing is added to `blob.h` for this.** What is recorded
  instead is the check: if a raised `NW_MAX_UNITS` ever approaches the
  measured ceiling, re-measure by binding, and only then consider a derived
  limit.

  This is not the resource that runs out first, and the scale ladder already
  says which is: PID 1 holds two log pipes per house, so the descriptor
  budget breaks at n > (`ulimit -n` − reserved) / 2 — about 9,996 on the
  machine in `tools/HANDOFF-scale.md`. That is ~2.4× below the loop figure
  measured here and it binds first.
- Everything on the failure path is `die()`, per invariant 6: a declared lid
  or a declared brick that cannot be applied stops that house.

**Test** `brick-image-is-a-root`: the existing `brick-is-a-root` retargeted at
images — two houses, two images, different `/id` at the same path, neither
seeing the machine's root.
**Test** `brick-image-is-sealed`: a house with **`lids=none`** attempts to
write into its own brick and is refused. This is the property images buy that
the lid version cannot, so it is the test that justifies the phase.

**Negative control — the one named here does not exist.** This said "drop
`MS_RDONLY` from the mount and the write succeeds, failing the test." It
does not. Prototyped end to end on 2026-09-11 (image → `LOOP_CTL_GET_FREE`
→ `LOOP_CONFIGURE` → erofs mount → `pivot_root(".", ".")` → `execv` a static
house), outside the tree:

| variant | result |
|---|---|
| everything present | mounts `ro`; house's write REFUSED |
| no `MS_RDONLY` | `mount erofs: Permission denied` — house never runs |
| no `LO_FLAGS_READ_ONLY` | mounts `ro`; write REFUSED (flag does nothing) |
| neither | Permission denied |
| both fds `O_RDWR`, neither flag | mounts **`ro` anyway**; write REFUSED |

The kernel forces `LO_FLAGS_READ_ONLY` when either the backing-file fd or
the `/dev/loopN` fd is `O_RDONLY`, and erofs has no write path, so it mounts
`ro` regardless. **The seal is over-determined and no flag we pass is what
enforces it.** Dropping `MS_RDONLY` does fail the test — at the mount, for a
reason that is not the seal — so quoting it as "the write succeeds" would be
a true-looking sentence beside a mechanism that is not doing the work.

**The control that does work runs from the other side**: the brick as a
*directory*, bind-mounted onto itself, which is what `nwsup.c` does today.
Same house, same content:

```
house: write into brick SUCCEEDED -- seal is broken
```

and `written-by-house` left in the tree. That is what proves the test can
detect an unsealed brick. Use it.

**`LO_FLAGS_AUTOCLEAR` is load-bearing and its control is real**: dropping
it leaks the device (`losetup -a` shows it still attached after the house
exits), where the full sequence leaves none. Keep that one.

## The mountpoint — DECIDED 2026-09-12: an existing empty directory

An image cannot be mounted onto itself the way a directory brick is
bind-mounted onto itself, so phase 2 needs a mount point `lid_brick()` does
not have today. **It is an empty directory on the machine root, created by
`dawn` alongside `/nw/bricks` and `/nw/stores` — `/nw/mnt`. Not a tmpfs.**

This is a **production** decision, not a harness one: `lid_brick()` runs in
the TCB on every house at every boot, so whatever is chosen ships and the
tests follow it. That is why this plan says decide before writing the code.

**The cost comparison.** The directory costs one `mkdir` in `dawn`, once,
ever. The tmpfs costs a mount syscall per house per boot plus a tmpfs
instance the kernel tracks for the life of every house. What the tmpfs buys
is that nothing is written to disk at the mountpoint — but nothing is
written there anyway: it is an empty directory, covered by `pivot_root` a
moment later. And a tmpfs still needs an existing directory to mount *on*,
so it does not remove the requirement that the layout guarantee a path. It
inherits that requirement and adds a layer.

### The fact it rests on, verified in the code rather than assumed

Two houses cannot collide on one mountpoint, because `nw-sup` unshares its
mount namespace before `lid_brick()` runs, so each house's mount of
`/nw/mnt` is invisible to every other. **Checked, and it holds in both
halves — the unshare and the propagation:**

1. `nwsup.c`, in the parent before the fork loop: `if (brick) { … if
   (!(lids & NW_LID_NEWNS)) die("brick without newns"); }`. A brick house
   whose plan omits `newns` never reaches a fork. `die()` ends in
   `_exit(72)`, so this is not advisory.
2. In the child, in this order: `if (lids & NW_LID_NEWNS) lid_newns();`
   then `if (brick) lid_brick(…);`. Given (1), `brick` implies `NEWNS`, so
   `lid_newns()` — which is `unshare(CLONE_NEWNS)` or `die` — **always**
   precedes `lid_brick()`. There is no path to `lid_brick()` without a
   preceding unshare.
3. `unshare(CLONE_NEWNS)` alone would not be enough: it copies the mount
   table, and a new mount still propagates to a shared peer group. The
   first statement of `lid_brick()` is
   `mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL)` or `die`, before any
   bind. So nothing propagates back.
4. `nwcheck.c` returns `NW_E_BRICKNS` independently, so a sealed plan
   cannot express the case at all.

Both halves matter and (3) is the one that would have been easy to miss:
the unshare alone is the half people quote, and it is not sufficient.

### Where the one line goes, and who writes it

`dawn.c` already does `mkpath(NW_ROOT_MNT "/nw/bricks")` and
`mkpath(NW_ROOT_MNT "/nw/stores")` on consecutive lines. `/nw/mnt` is one
more `mkpath` beside them, which is what "one mkdir, once, ever" means
literally rather than as an estimate. **Not written here:** this plan
decides, it does not implement, and `dawn.c` belongs to whoever holds the
boot chain that week — see the ownership note in `CLAUDE.md`.

Two things for whoever lands it. The directory must exist **before** any
house is spawned, which `dawn` satisfies by construction since it runs
before PID 1 execs. And a house whose brick is a *directory* does not touch
`/nw/mnt` at all — the bind-onto-itself path is unchanged — so the
mountpoint is dead weight for directory bricks and that is the correct
trade: one empty directory, versus a per-house mount syscall to avoid it.

**What is NOT namespaced, and is the real shared resource in this phase:**
the loop device pool. `LOOP_CTL_GET_FREE` allocates from a global kernel
pool; a mount namespace does not partition it. The mountpoint decision does
not touch that, and nothing below should be read as saying it does.

## Phase 3 — the plan carries a hash, not a path

`struct nw_unit`'s `brick[96]` becomes a 32-byte binary sha256 (`brick[32]`),
`nw-sup` builds `/nw/bricks/<hex>.img`, and the plan stops carrying a path.
This is `07` option A for the brick half, and it is only meaningful now that
`08` has defined what the hash is *of*: the image file's bytes.

- Unit shrinks 260 → 198 bytes; format becomes **`NWPLAN07`**.

  *Both numbers here were wrong until 2026-09-11, and the magic was the
  dangerous one.* `NWPLAN06` was spent on the `window_s` removal that same
  day, which took the unit from 262 to 260 — so this line named a magic
  that already exists and means a different layout. An agent landing
  phase 3 by following it would have found `NW_MAGIC` already saying
  `NWPLAN06`, changed nothing, and shipped a second incompatible format
  under the same name, with old and new blobs both claiming to be 06 and
  the size check as the only thing telling them apart. That is verbatim
  the defect the 05 → 06 bump was made to remove, re-created inside the
  version namespace. Found by `drift` and by `tcb-review`, independently.
  Check `NW_MAGIC` in `blob.h` before spending the next number, not this
  file.
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
  (`08` Q1 C) comes back. **Measured on this machine and not met** — see the
  prerequisites section: `LOOP_CTL_GET_FREE` allocated 64 distinct devices
  for one image and 80 overall, with `max_loop` reading 8. It stays on this
  list because it is a property of the target kernel, not of this one, and
  the target has still been checked for neither.
- If the target kernel lacks erofs and cannot gain it, squashfs is the
  fallback and costs ~7× on mount; the flag set for it is recorded in `08`.
- If a brick ever needs to be writable, none of this survives. It is not
  supposed to — a writable brick is not content-addressed — but the failure
  would present as somebody wanting a house to keep state, which is
  `docs/options/05` and is a different question that must not be answered by
  quietly making the brick writable.
