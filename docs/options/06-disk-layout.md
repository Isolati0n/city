# 06 — Disk layout

Status: **options, not a decision.** Written 2026-09-10.
Upstream of `05-house-persistent-storage.md`; that doc's Q1 and Q2 cannot be
settled until this one is.

## Provenance warning

Same as `05`. The corpus this brief references — `MASTER.md`, `DECISIONS.md`,
the grok-web chats — is not in this repository and has never been tracked.
Three specific references in the brief could not be found **and appear not to
exist here at all**:

- **`HISTORY.md` §22, the thin-UKI decision.** `HISTORY.md` ends at **§20**.
  There is no §21 or §22. `grep` for `UKI|unified kernel|thin.?core|thin.?uki`
  across every file returns nothing.
- **The R3 rescue sketch.** `R3` appears in exactly one file — `docs/options/05`
  — where I introduced it from the operator's own prose and tagged it
  `[UNVERIFIED]`. It is not defined anywhere.
- **`PE`** (as in "on the ESP outside the PE") — no occurrence.

So the statement "§22 puts the plan and unit images on the ESP outside the PE"
**could not be verified and is treated below as an operator-supplied premise**,
tagged `[PREMISE]`. If §22 says something different, the options that depend on
it need re-costing. Everything not so tagged is checked against the code.

## The gap — confirmed

**There is no disk layout. Not partial: absent.**

```
grep -rnE '\bmount\b|mount\(|umount|pivot_root|MS_[A-Z]+|tmpfs|devtmpfs' \
     --include='*.c' --include='*.h' .
  → NO MATCH
```

Not one mount call in the TCB. `pid1.c` mounts nothing, `nwspawn.c` mounts
nothing, `nwsup.c` mounts nothing. The `NEWNS` lid is a bare
`unshare(CLONE_NEWNS)` with no `pivot_root` and no remount, so it isolates
mount *propagation* and changes no unit's view of the filesystem.

```
grep -rniE 'partition|\bESP\b|EFI|rootfs|/dev/(sd|nvme|vd)|blkid|PARTUUID|
            fstab|GPT|squashfs|erofs|overlay|initrd|initramfs' <all files>
  → every hit is substring noise (`partition("=")` in Python, `_GNU_SOURCE`,
    `undefined`, `prefix`) or docs/options/05, which introduced ESP as the
    operator's vocabulary and flagged it unverified.
```

The whole filesystem story is one `make` variable:

```
STAGE = /tmp/nw-init-run
```

`make stage` `mkdir -p`s `slots/A`, `slots/B`, `slots/rescue` under it and
copies binaries in. That is a lab convenience supplied by the harness. **On
real iron nothing says what exists, what is mounted, in what order, or by
whom.**

### Second gap, as predicted: nothing stages candidates

**There is no promote step and no candidate staging.** The only thing that
writes a slot is `make stage`, at build time, and it writes A and B as
byte-identical copies of the same bake:

```
python3 bakery/nw-cc.py ... --out $(STAGE)/slots/A/plan.blob
cp -f $(STAGE)/slots/A/plan.blob $(STAGE)/slots/B/plan.blob
echo A > $(STAGE)/slots/current
```

Three consequences worth naming:

- There is no moment at which a *new* plan becomes a candidate. A/B exists as
  a directory shape, not as a mechanism.
- **`slots/current` is write-only.** `make stage` writes it; nothing reads it.
  `pid1.c` takes `--slot` or `--plan` from `argv` and never consults
  `current`. The file that records which slot is live is consulted by nothing.
- The `slot-B` test asserts only that slot B boots. Since A and B hold the
  same bytes, it does not exercise slot *difference* at all.

This matters here because **promote is the natural home for store creation and
reclamation** — a non-TCB moment, derived from the plan, that already writes.
It does not exist, so `05`'s Q2 has no host. Named here; not this doc's
subject.

## Constraints

Settled by the operator, not re-litigated below:

1. **No extra partitions** beyond what an ordinary machine has.
2. A/B slots and rescue keep working.
3. The bakery stays offline and authors nothing on the box.
4. Bricks stay sealed and content-addressed.
5. **Anything requiring PID 1 to grow must be called out.** `pid1.c` mounts
   nothing today; mounting is exactly the kind of thing added to PID 1 for
   convenience.

## The chicken-and-egg that decides most of this

`nw-check` must read the plan blob before anything is trusted. So **whatever
holds the plan must already be mounted before PID 1 runs.** PID 1 cannot mount
the thing it needs in order to know what to mount.

There are only two ways out, and picking one settles "who mounts what":

- **Something earlier mounts it** — an initrd / dawn stage that mounts, then
  `exec`s `nw-root` with a path. PID 1 stays exactly as it is.
- **PID 1 mounts it** — which means PID 1 contains a hardcoded device or
  filesystem type, because it cannot read a plan to learn one. That is a
  compile-time constant naming a disk, in the TCB, and it is the same shape as
  the fixed-fd-number class the project has spent thirteen bugs learning to
  avoid.

Everything below assumes the first unless stated.

## The five questions

### Q1 — What partitions, and what filesystem on each?

Under constraint 1, an ordinary machine is **two**:

| # | partition | filesystem | why |
|---|---|---|---|
| 1 | ESP | FAT32 | firmware requires it; not negotiable |
| 2 | root | one of ext4 / btrfs / f2fs | everything else |

That is the floor and the ceiling. No `/boot` (the ESP is `/boot`), no
separate `/var`, no separate data partition — the last is precisely what `05`
Q1 reached for and what the operator has ruled out.

### Q2 — Where does the ESP sit and what lives on it?

Mounted at `/boot` or `/efi`, **read-only after boot** wherever possible.

`[PREMISE]` Per the unverified §22: the plan blob and unit images live on the
ESP, outside the PE. Taking that as given, the ESP holds the boot artifacts,
the A/B slot directories with their `plan.blob` and `.sha256` pin, and
`slots/current`.

FAT32 constrains what can live there: no ownership, no permission bits, no
symlinks, no hardlinks, no checksums, 4 GiB maximum file size, and
case-insensitivity. That rules the ESP out for bricks and for stores.

### The execute-bit argument was wrong — corrected 2026-09-10

This section originally said "anything needing an execute bit, an owner, or
integrity cannot live on the ESP." **The execute-bit half of that is not
right.** A FAT filesystem stores no mode bits, but Linux's vfat driver
synthesises them from mount options (`umask`, `fmask`, `dmask`), so files on a
vfat mount normally come out executable and `execve` works. Something like
`nw-rescue` *could* run from the ESP.

The conclusion survives on the other grounds, which are genuine: no ownership
means units cannot be separated, no checksums means no integrity, the 4 GiB
cap bounds any brick, and case-insensitivity is hostile to content-addressed
names. But one of the four supporting arguments was false and is withdrawn
rather than quietly left standing.

### UNTESTED: the ESP is never vfat in any test

`dawn-real-boot` mounts the ESP as **ext4, not vfat.** `dosfstools` installs
cleanly and `mkfs.vfat` works, but **this kernel cannot mount the result**:

```
$ cat /proc/filesystems | grep -c vfat
0
$ mount /dev/loop1 /mnt
mount: unknown filesystem type 'vfat'
```

`/proc/filesystems` lists only ext2/ext3/ext4 plus virtual filesystems; there
is no `/lib/modules` and no `modprobe`, so the kernel is monolithic without
FAT and no module can be loaded. This is an environment limit, not a design
choice, and it cannot be worked around here.

So every FAT-specific property above is an **assumption, not a verified
fact**, including the corrected execute-bit claim. The first machine that
boots with a real vfat ESP is where these get tested, and case-insensitivity
is the one most likely to bite something, because content-addressed brick
names are hex and a case collision would be silent.

### Q3 — Is there a root filesystem, and is it writable?

**Yes, and it must be writable somewhere**, because bricks, stores and
ephemeral state have nowhere else to go once the ESP is excluded.

The real question is not ro-versus-rw but **what rolls back**. That is Q6.

### Q4 — Where do bricks live?

`/nw/bricks/<content-hash>/` on root. They need an execute bit and stable
inode semantics, so the ESP cannot hold them.

The load-bearing consequence, and the reason this answers Q6 too:
**content-addressed bricks coexist.** Installing a new brick does not replace
an old one; it adds a directory with a different hash. Several generations sit
side by side.

### Q5 — Who mounts what, and when?

**Dawn (an initrd/initramfs stage) mounts; PID 1 mounts nothing.**

Sequence: firmware → dawn → mount ESP read-only → mount root → `pivot_root`
→ mount `/proc`, `/sys`, `/dev`, `/run` (tmpfs) → `exec nw-root --slot
/efi/slots/A`.

**Zero PID 1 growth.** `pid1.c` already accepts `--slot` and `--plan` as
paths and reads the blob with plain `open`/`read`. Dawn hands it a path that
is already mounted, and nothing in the TCB learns what a filesystem is. This
is the whole reason to prefer it: the alternative puts a device name inside
the trusted core.

### Q6 — Rollback, against a filesystem that does not roll back

This is the question `05` could not answer without a partition, and it is
answerable without one:

> **Rollback does not require the filesystem to roll back, because bricks are
> content-addressed and coexist.**

An older plan in slot B references older brick hashes. Those directories are
still on root. Booting slot B runs the old bricks without moving a byte of
data. `/nw/stores` is untouched by the rollback and carries forward, which is
what data should do.

What this costs: **old bricks are never reclaimed by anything.** Root fills up
with every generation ever installed. That is a garbage-collection problem,
and its natural home is the promote step — which does not exist. This is the
strongest argument in this document for building promote before storage.

### Q7 — tmpfs

`/run` and `/tmp` as tmpfs, mounted by dawn, for the system.

**Per-house ephemeral state has no mechanism and should not get one here.**
Invariant 5 says the init provisions nothing, and a tmpfs handed to a house is
a third thing. A house with `NEWNS` cannot mount its own — `__NR_mount` is
absent from the `lids.c` allow-list, so a `lids=seccomp` house is killed for
trying. Ephemeral per-house storage is the same question as persistent
per-house storage and belongs in `05`, not here.

## Options

### A — ESP + single read-write root *(recommended)*

Root is ext4 or f2fs, mounted rw by dawn. `/nw/bricks/<hash>/` for bricks,
`/nw/stores/<store-id>/` for stores, tmpfs for `/run` and `/tmp`. Slots on the
ESP. Rollback by content-addressed brick coexistence.

*Cons, native:*
- **The seal on a brick is conventional, not enforced.** Nothing stops a
  process with write access from modifying `/nw/bricks/<hash>/` in place, at
  which point the hash is a lie. The ESP-plus-one-partition constraint means
  there is no filesystem boundary to lean on; only Landlock could enforce it,
  and Landlock here degrades silently to nothing when the ABI probe fails.
- **No bit-rot protection.** ext4 and f2fs do not checksum data. `05`'s
  decision-#5 concern lands squarely here and this option does nothing for it.
- **A full disk takes out everything at once** — bricks, stores, and the
  ability to stage a new candidate. One partition, one failure.
- **Unbounded brick growth** with no reclamation, per Q6.

### B — ESP + btrfs root with subvolumes

Same two partitions. Root is btrfs; `@bricks`, `@stores` and `@run` are
subvolumes.

*Pros worth stating because they are real:* btrfs checksums data, which is the
only option here that answers bit rot at all; snapshots give a genuine rollback
primitive; subvolume quotas bound one store's growth.

*Cons, native:*
- **Complexity in the boot path.** btrfs is a large, actively developed
  filesystem, and dawn must understand it before anything is trusted.
- **A subvolume boundary is conventional too.** It is a stronger convention
  than a directory but it is not a partition, so option A's seal objection is
  reduced rather than removed.
- **Snapshots invite live rollback**, which is exactly what invariant 7 forbids
  — a new plan is a new slot, not an in-place rewrite. Having the primitive
  available makes the forbidden thing easy.
- The project has never run on real hardware; adopting a filesystem for
  properties measured nowhere is the kind of claim `measurement.md` exists to
  block.

### C — ESP + read-only root image + writable subtree

Root is an erofs/squashfs image mounted ro, with a writable area for stores.

*Cons, native:*
- **The writable area has to live somewhere**, and with no extra partition
  that means a loopback file or a rw remount, both of which reintroduce the
  boundary problem this option was chosen to solve.
- Installing a brick means rebuilding the whole root image, so brick
  installation stops being incremental and becomes a re-image.
- Dawn grows materially: image verification, loop mounting, overlay assembly.

### D — ESP only, root on tmpfs

No root partition; everything ships on the ESP and runs from RAM.

**Out, and not on preference.** FAT32 has no execute bit, so bricks cannot be
executed from it; no ownership, so units cannot be separated; 4 GiB file cap;
no checksums. And tmpfs is not persistent, so stores would not survive a
reboot, which is the entire point of `05`. Listed to be dismissed explicitly.

### E — PID 1 mounts *(rejected, listed because it is what gets built by accident)*

PID 1 mounts root itself, and dawn disappears.

*Cons, native:* PID 1 must contain a hardcoded device path or filesystem type,
because it cannot read a plan before mounting the thing the plan is on. That
is a compile-time constant naming hardware, inside the TCB, in the process
where a fault does not crash a program but fails to boot a machine. It is the
fixed-fd-number class in a new costume. It also adds mount, error and retry
paths to a file whose invariant is no allocation, no parsing, no recursion.
**Explicitly rejected under constraint 5.**

## Recommendation

**Option A, with two things recorded rather than hidden.**

A is right because it is the minimum that works and because it makes `05`'s
answer a directory rather than a partition, which is what the operator asked
for. Its rollback story is genuinely sound — content-addressed coexistence
means the filesystem never has to roll back — and it costs PID 1 nothing.

B is the better filesystem and the wrong bet today. Its advantages (checksums,
snapshots, quotas) are real, and every one of them is unmeasured on hardware
this project has never run on. The migration A → B is a re-image, not a
redesign: both are "ESP plus one root", and the paths do not change. Take B
when bit rot is a measured problem rather than an anticipated one.

Two things A does not solve, which should be written down now rather than
discovered later:

1. **Brick reclamation.** Unbounded growth with no GC.
2. **Bit rot.** ext4 does not checksum; `05`'s decision-#5 concern is
   unaddressed by this layout and stays open.

And one sequencing claim: **promote should be built before storage.** It is
the missing non-TCB moment, and brick GC, store creation and store
reclamation all want to live in it.

## What this unblocks in `05`

**Q1 becomes answerable, and the previous answer is superseded.** `05` said
"a separate persistent partition, outside ESP, slots and bricks." Under
constraint 1 that is out. The layout answer is **`/nw/stores/<store-id>/` on
the root filesystem** — a directory, not a partition. The reasoning `05` used
to reach for a partition (data must survive rollback) is satisfied instead by
content-addressed brick coexistence: the filesystem never rolls back, so
nothing has to be outside it.

**Q2 becomes answerable in principle and blocked in practice.** `05`
recommended route (ii), first-dawn materialisation by `nw-sup`. With a layout
that stands up, the better host is **promote** — non-TCB, derived from the
plan, already writing. But promote does not exist. So Q2's answer is "at
promote, once promote exists", and until then route (ii) remains the fallback.

**Q3, Q4 and Q5 are unaffected.** Store identity keyed to a `store-id`
distinct from the house name, the do-not-restart channel, and the smallest
version all stand as written; none depended on the layout.

**One `05` con is now sharper.** Its option E warned that provisioning-time
materialisation makes the disk image and the plan two authorities that must
agree. Under this layout the root filesystem is written by whatever installs
bricks, so that warning applies to brick installation too, not just stores.
