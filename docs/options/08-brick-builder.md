# 08 — The brick builder

Status: **decided 2026-09-10 — a brick is a read-only image.** The options
below are kept because the reasoning is the record; the decision and its
consequences are at the end, and the build sequence is
`docs/plans/01-brick-images.md`.
Upstream of `07-identifiers-not-paths.md`: that doc proposes the plan carry a
brick's hash instead of its path, and a hash is not an identity until
something defines what it is computed over. **Nothing here is implemented.**

## What exists today

**There is no brick builder.** `/nw/bricks` is a directory `dawn` creates and
nothing populates. The only bricks that have ever existed are built by
`make_brick` in `tests/run.py`, which assembles a tree and hashes it — enough
to prove the runtime treats the path as opaque, and enough for nothing else.

The runtime side is real: `nw-sup` bind-mounts the brick onto itself,
applies declared binds, and `pivot_root`s in (§22). Since §26 the Landlock
lid grants read and execute beneath that root and **no write**, so a house
cannot modify its own brick. The seal is currently a property of the lid, not
of the storage.

## Measured here, not assumed

Everything in this section was run on this box today. It is the evidence the
options below are costed against.

**A working compiler brick is 78 entries and 46 MB.** Traced `gcc -O0` on a
trivial C file with `strace -f -e trace=openat,execve,newfstatat`, took every
existing file it touched (61 files, 39 MB), copied them into a tree, and
compiled inside it under `chroot`:

```
=== brick ===
  size: 46M
  entries: 78
=== compile inside chroot ===
  binary produced:
compiled inside the brick
  run exit=0
```

That is a floor, not a figure to plan with: one compile, one trivial file, no
`make`, no C++ (`cc1plus` never loaded), no headers beyond what `stdio.h`
pulls in, and `/dev/null` created by hand.

**The naive closure does not work, and the reason generalises.** The first two
attempts produced a brick where `chroot` reported
`failed to run command '/usr/bin/gcc': No such file or directory`. Fifteen of
the sixty-one entries are symlinks, and every compiler driver in `/usr/bin`
is one:

```
/usr/bin/gcc   -> /etc/alternatives/gcc   -> /usr/bin/x86_64-linux-gnu-gcc-13
/usr/bin/as    -> /usr/bin/x86_64-linux-gnu-as
/lib64/ld-linux-x86-64.so.2 -> ../lib/x86_64-linux-gnu/ld-linux-x86-64.so.2
```

`/etc/alternatives/gcc` **never appears in an `openat` trace** — the kernel
resolves the chain and reports the call on the path the program asked for. A
closure computed from syscall tracing is therefore missing every intermediate
hop, and a brick built from it is broken in a way that only shows at `execve`.
Copying full symlink chains is what made it work.

**A content-only hash names a broken brick and a working one identically.**
Two hashes over the same tree: one over `(path, content)`, one over
`(path, mode, symlink target, content)`.

```
before:              content-only=3afc8aff219029f8   content+mode+link=f1a18cf47a9a1e15
chmod -x cc1:        content-only=3afc8aff219029f8   content+mode+link=641265a391872785
  gcc: fatal error: cannot execute 'cc1': execvp: No such file or directory
  BROKEN
repoint /usr/bin/gcc: content-only=3afc8aff219029f8  content+mode+link=f196ac3fe4faa3df
```

Removing one execute bit breaks the compiler and leaves the content-only hash
byte-identical. Repointing a symlink changes what runs and leaves it
byte-identical. **A content-addressed image that ignores mode and symlink
targets is not addressed by its content**, and the failure is silent: the
plan names a hash, the runtime mounts it, the house starts and cannot compile.

**Storage support, this kernel:** `/proc/filesystems` lists `ext4`,
`squashfs`, `erofs`, `overlay`, `tmpfs`. It lists no FAT of any kind.
Neither packer was installed; both were installed for the measurement below,
which is itself the point — the bakery is not this box, and needing to obtain
a packer is not a cost that distinguishes the options.

**A note on where the evidence comes from.** Landlock runs at ABI 7 on the
operator's clone and returns `ENOSYS` here; neither environment has a FAT
driver. So the suite is fully exercised only as the **union of two machines**,
and that is a property of the pair rather than of either one. No single
environment has ever run every test in this project, and `vfat-esp` has now
run in none — it stays untested until something boots on hardware.

## Q1 — What is a brick on disk?

### A directory tree under `/nw/bricks/<id>/`

What the runtime already does. `nw-sup` bind-mounts and pivots; no new mount
code, no loop device, nothing added to the TCB.

Sealing is by convention and by the Landlock lid. Nothing at the storage layer
stops a privileged process — or a house with `lids=none` and a bind that
happens to point there — from writing into a brick, and nothing detects it
afterwards. The hash is a name someone computed once; it is not re-derivable
at boot without walking the tree, which at 46 MB and 78 entries is cheap but
at a realistic toolchain is not.

### A read-only image file (`squashfs` or `erofs`)

`/nw/bricks/<id>.img`, loop-mounted read-only. Sealing becomes a property of
the storage: the mount is `MS_RDONLY` over a filesystem with no write path,
so the brick cannot be modified by anything, with or without a lid. It is one
file, so it can be copied, verified and deleted atomically, and its hash is
the hash of the file — no tree walk, no manifest, no question about what the
hash covers, because the image encodes mode and symlink target inside itself.

Cost: **`nw-sup` grows a loop mount** — a `losetup`-equivalent ioctl sequence
plus a `mount(2)` with a filesystem type, per house, with a loop device to
allocate and to fail to allocate. Today `nw-sup`'s entire filesystem
vocabulary is bind and pivot.

**Correction, 2026-09-10.** An earlier draft of this doc called that
"`06`'s rejected option E arriving by another door". That is wrong and the
error is worth keeping visible. E was rejected because **PID 1 cannot read a
plan before mounting the thing the plan is on**, which forces a hardcoded
device into the un-restartable process where a fault does not crash a program
but fails to boot a machine. None of that applies here: brick mounting happens
in `nw-sup`, per house, *after* the plan is read and validated, and a fault
there kills one house while the city keeps running — which is invariant 6's
own posture. `dawn` already takes `NW_ROOT_FSTYPE`, so a filesystem type in
the TCB is not a new precedent either. The honest cost is **one mount call and
one constant in a restartable process**, and it should be costed as that
rather than as a rejected decision returning.

Also: erofs and squashfs are mountable on the target kernel today, and that
is a property of a kernel build, not of this design. It becomes a boot
dependency.

### An image, mounted once by `dawn`, bound by `nw-sup`

Split the difference: `dawn` loop-mounts every brick image under
`/nw/bricks/<id>/` during the mount stage, and `nw-sup` bind-mounts and pivots
exactly as it does now. The loop-mount code stays in the file that already
owns mounting; `nw-sup` learns nothing new.

Cost: `dawn` must know which bricks exist before the plan is read, so either
it mounts everything in a directory (and boot time scales with brick count,
and an unused brick still costs a loop device) or the plan is read before
`dawn` finishes, which is the chicken-and-egg `06` already settled the other
way.

## Q2 — What does the hash cover?

Settled by the measurement above rather than by preference: **content alone is
not enough.** The hash must cover, per entry, at minimum:

- the path, normalised and sorted with a fixed collation (`LC_ALL=C`);
- the entry type — regular, directory, symlink;
- the mode, at least the execute bits;
- for a symlink, the **link text** and not the resolved target — resolving at
  bake time bakes in the builder's filesystem;
- the content, for regular files.

Deliberately excluded: mtime, atime, uid/gid, inode, and hard-link structure.
Every one of them varies between two bakes of identical input, and none of
them changes what the house can execute. Excluding uid/gid assumes bricks are
single-owner and world-readable; if that ever stops being true it belongs in
the hash and this line is where to change it.

An image format (Q1 option B) sidesteps this question by construction: the
hash is of the image file, and the image already encodes mode and link text.
That is a real argument for images and is the strongest one.

## Q3 — What is the input, and where do the files come from?

The hard constraint: **the bakery is offline, there is no package manager
anywhere in this design, and nothing on the running machine authors or mutates
a brick.** So the bakery cannot resolve a dependency, cannot fetch, and cannot
run a build.

### A directory tree, handed over

The bakery is given a populated tree and only seals it: walk, hash, name,
optionally pack into an image. It resolves nothing and fetches nothing, so
every constraint holds trivially.

Where the tree comes from is then explicitly **out of scope and someone
else's problem** — a distro chroot, a Nix store path, a container export,
somebody's `tar`. That is either the honest scoping or the whole question
being pushed sideways, depending on how you read it. It is the smallest thing
that could work and it is what `make_brick` already does in miniature.

### A manifest plus a content-addressed blob store

Input is a list of `(path, mode, hash)` plus a store of blobs. The bakery
assembles, verifies each blob against its hash, and seals. Two bakes of the
same manifest are identical by construction, and the manifest is reviewable
in a way that a 46 MB tree is not.

Cost: a blob store is a second content-addressed thing to build, populate and
garbage-collect, and `docs/options/05` has not settled `/nw/stores` yet.
Nothing generates the manifest, so this needs the tree option anyway to
bootstrap.

### A derivation: build the brick inside a brick

The bakery runs the build inside a previously sealed brick, so the toolchain
is itself pinned by hash and the build is reproducible by construction. This
is the design the rest of the project points at.

**It cannot bootstrap itself.** The first compiler brick must come from
outside — the tree option, once — and after that this is available. Worth
stating because it is the reason the tree option is not merely a stepping
stone that gets deleted.

It also bends a constraint and this must be said plainly: **it requires
running a build**, which is not fetching or resolving, but it is authoring.
The constraint as written is "nothing *on the running machine* authors or
mutates a brick", and a bakery is not the running machine — so it holds, but
only because of where the bakery is. If the bakery ever moves onto the
machine, this option dies with it.

## Q4 — Do two bakes of the same input agree?

With the Q2 hash and the tree input: **yes, and the failure modes are known.**

Stable across bakes: content, mode, link text, path.

Breaks it: mtime and atime (excluded); directory iteration order (fixed by
sorting under `LC_ALL=C`); uid/gid (excluded, with the caveat in Q2); files
the build wrote with a timestamp or a build path inside them — the classic
one is `__DATE__`, and a compiler embedding its own absolute build directory
is the same shape.

The last of these is not the bakery's to fix, and pretending otherwise is how
reproducibility efforts become unbounded. **The bakery's guarantee should be
narrow and stated: identical input tree gives an identical hash.** Whether
two runs of a *build* produce identical trees is the build's problem, and the
derivation option (Q3 C) is where it would be addressed.

An image format adds one more: the packer itself must be deterministic.
**Measured 2026-09-10, both packers installed for the purpose.**

With default flags, *neither* is deterministic — not even packing the same
tree twice in a row:

```
mksquashfs, default:   same tree twice  fb5e834d33d96d0d  b84c435b60b0ff29
mkfs.erofs, default:   same tree twice  6b6948a35276212d  4195a7de27421cce
```

Note that `mksquashfs -help` says `-reproducible` is **the default**, and it
still differs. A tool's claim about itself is not evidence, which is this
project's whole thesis restated by a third party.

With the flags below, both become exactly reproducible — same tree twice, and
a copy of the tree at a different path with every mtime rewritten:

```
mksquashfs:  a08054996275b8d7   a08054996275b8d7   a08054996275b8d7
mkfs.erofs:  f70e013790e3159b   f70e013790e3159b   f70e013790e3159b
```

Both mount, and a compiler works inside each as a read-only image with a
tmpfs where a declared bind would go. Both refuse writes at the storage layer
with no lid involved. **Determinism therefore does not decide it** — the
deciding measurement is below.

## Q5 — Does a brick declare what it needs bound in?

### It stays purely a plan property

Status quo. The plan says `bind=`, `nwcheck.c` validates it, `nw-sup` mounts
it. A brick is inert content.

A brick that needs a store and a plan that forgets to bind one fails at
runtime, as a house that starts and then cannot find its data — exactly the
kind of failure that is invisible until someone looks.

### The brick ships a required-binds manifest, checked at bake time

The brick carries a list of paths it expects; the baker refuses a plan that
declares a brick without satisfying them. The failure moves from runtime to
bake time, which is this project's stated preference.

Cost: it puts policy inside the brick, and the plan is supposed to be the
single place that says what a house gets. It also makes the brick's hash
depend on its requirements, so changing a requirement renames the brick — which
is arguably correct, and is certainly surprising the first time.

**Recommend keeping it a plan property** until a brick exists that needs
something, then revisiting. The bake-time check can be added later without a
format change, because it is a baker rule and the baker is not in the TCB.

## Q6 — The smallest brick with a working compiler

Measured: **78 entries, 46 MB**, demonstrated compiling and running inside a
`chroot`. Caveats stated above — one trivial compile, no `make`, no C++.

Two things that measurement teaches beyond the number:

- The closure must follow **symlink chains**, not just the paths a trace
  reports. `/usr/bin/gcc -> /etc/alternatives/gcc -> …` is invisible to
  `openat` tracing and fatal at `execve`.
- `/dev/null` had to be created by hand. A brick needs its device nodes baked
  in or bound in, and since §26 removed the Landlock lid's special-case
  `/dev/null` rule, a brick that wants a writable one declares a bind.

A brick this size also settles a smaller question: at 46 MB, walking and
hashing a brick at boot to verify its name is affordable, and at a realistic
multi-language toolchain it is not. That asymmetry is an argument for the
image format, where verification is one hash of one file.

## Decision — 2026-09-10: a brick is a read-only erofs image

Taken by the operator. There is no time pressure on this project, so the two
problems images close are worth their cost, and directory-first would mean
writing a canonical serialization spec in order to delete it later.

**Why images, in the operator's words and this doc's evidence:**

- A content-only hash names a working brick and a broken one identically
  (measured above). Fixing that for a *directory* means inventing a canonical
  serialization — ordering, modes, link text, hardlinks, device nodes,
  xattrs — with every edge case sitting in the TCB's input path. An image
  makes the problem **vanish rather than solving it**, because modes and link
  targets are inside the file's bytes by construction. That is this project's
  own method: design the problem out rather than check for it.
- The brick is currently sealed **by the lid, not by storage**. A `lids=none`
  house can write into its own content-addressed image. An image makes the
  seal structural and independent of lids entirely — demonstrated above:
  both formats refused a write with no lid involved.

**Which packer: erofs, with `lz4hc`.** Determinism did not decide it — both
are exactly reproducible once forced. The deciding measurement is mount cost,
which is paid once per house per boot:

```
mount + read cc1 (ms, 7 runs sorted, median = 4th)
  squashfs gzip   184 187 196 [196] 259 286 382     image 20 MB
  erofs lz4hc      19  20  27  [27]  33  34  98     image 28 MB
  erofs uncompr    13  13  14  [14]  14  15  22     image 46 MB
```

~7× faster to mount and first-read, and the spread is far below the effect,
so this is a real difference and not scheduler noise. At `NW_MAX_UNITS` that
is roughly 1.7 s of boot against 12.5 s. The 8 MB the compression costs is
cheap; the boot latency is not. Compression stays on because uncompressed
buys ~13 ms and costs 18 MB per brick.

### Reproducibility is required, and this list is the spec

A brick's name must mean *this exact content*, not *this particular build*,
or two bricks cannot be compared and a brick cannot be rebuilt and confirmed.
Anyone reproducing a brick must force exactly these, and a bake that omits
one produces a different name for identical content:

```
mkfs.erofs -T 0 \
           -U 00000000-0000-0000-0000-000000000000 \
           --force-uid=0 --force-gid=0 \
           -zlz4hc \
           <image> <tree>
```

- `-T 0` — every file timestamp. Without it, mtimes leak into the image.
- `-U <fixed>` — the filesystem UUID is **random by default**. This is the
  single largest source of nondeterminism and the easiest to miss, because it
  changes every byte-compare while nothing about the content moved.
- `--force-uid=0 --force-gid=0` — ownership, which Q2 excludes from identity.
- `-zlz4hc` — the compressor is part of the output, so it is part of the name.

Verified reproducible across: the same tree packed twice; a copy of that tree
at a different path with every mtime rewritten to a fixed date; and with
compression on. For the record, `mksquashfs` needs a longer list
(`-noappend -reproducible -mkfs-time 0 -all-time 0 -force-uid 0 -force-gid 0
-no-exports`) and its `-help` claims `-reproducible` is already the default
while producing different bytes on consecutive runs — a tool's claim about
itself is not evidence.

### Bootstrap: a permanent property, no decision attached

The first compiler brick cannot be built by a compiler brick. Something
outside this system must hand over the first tree, and Q3 A — the bakery
seals a tree it is given — is therefore **permanent rather than a stepping
stone**. Recorded so nobody deletes it as scaffolding later. Not designed for
now: no derivation, no manifest, no blob store until something needs one.

### What this decides for `07`

`07`'s brick half is now concrete: the plan carries the hash, and `nw-sup`
builds `/nw/bricks/<hex>.img`. Traversal stops being representable in the
brick field because a hash cannot contain a separator — the `..` guard stays
for `exec_path` and for binds, where it is the right answer rather than a
stopgap. The bind half of `07` is untouched and still open.

The build sequence is `docs/plans/01-brick-images.md`.
