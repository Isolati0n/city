# 08 — The brick builder

Status: **options, not a decision.** Written 2026-09-10.
Upstream of `07-identifiers-not-paths.md`: that doc proposes the plan carry a
brick's hash instead of its path, and a hash is not an identity until
something defines what it is computed over. Nothing here is implemented.

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
`mksquashfs` and `mkfs.erofs` are **not installed here**, though `tar` is.
So this box can mount a read-only image and cannot build one — which is
consistent with the bakery being somewhere else, and is a thing to check on
whatever machine the bakery actually runs on.

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

Cost, and it is the real one: **`nw-sup` grows a loop mount.** That is a
`losetup`-equivalent ioctl sequence plus a `mount(2)` with a filesystem type,
in the TCB, per house, with a loop device to allocate and fail to allocate.
Today `nw-sup`'s entire filesystem vocabulary is bind and pivot. It also puts
a filesystem *type* constant into the supervisor, which is the thing `dawn`
exists to keep out of everything below it (`docs/options/06`, option E).

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
`mksquashfs` is not, by default — it records timestamps and can vary block
ordering — and would need its reproducible flags pinned and tested. Neither
packer is installed here, so this is **unverified** and is a real check to
run before choosing images.

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

## The question that decides it

**Is a brick a directory or an image?** Everything else follows.

If **directory**: the hash must be a defined tree walk covering mode and link
text (Q2), the bakery seals a handed-over tree (Q3 A), nothing in the TCB
changes, and the seal remains a property of the Landlock lid rather than of
storage.

If **image**: the hash question disappears, the seal becomes real, and
`nw-sup` or `dawn` grows loop-mounting — a filesystem type constant in the
TCB, which is the thing `06` rejected as option E, arriving by another door.

The measurement leans toward **directory first**: it is what the runtime
already does, it needs no TCB change, and it can carry a working compiler
today. Images are the better end state and should not be built until
something needs the seal to be structural rather than enforced by a lid — and
until `mksquashfs` reproducibility is verified rather than assumed.

Nothing here should be built before the operator picks between those two.
