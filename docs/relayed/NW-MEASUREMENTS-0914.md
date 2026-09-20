# What the machine said — measurements, 2026-09-14

Not in the tree. Every number below was produced by running something on
this machine tonight, and each says where it ran, because two of them
disagree between the container and the guest. Nothing here is reasoning
except where marked.

Written because these facts currently exist in one chat transcript and
nowhere else, which is the state this project's own rule warns about.

---

## 1. `mkboot.sh` stages two modules, and both `overlay` and `erofs` are modules

**The finding, and it is the largest one here.** `tools/mkboot.sh` stages
exactly `nls_iso8859-1` and `nls_utf8` into the initrd. On the kernel it
boots — Ubuntu `6.8.0-139-generic` — the config reads:

```
CONFIG_OVERLAY_FS=m
CONFIG_EROFS_FS=m
```

Neither module is staged. So **a brick house cannot start in a QEMU boot
today**: `mount("overlay", …)` returns `ENODEV`, measured, and erofs would
fail the same way.

**What this means for the suite.** `brick-is-a-root`,
`brick-image-is-sealed`, `layer-survives-a-restart`,
`many-brick-houses-all-start` and `fold-house` all pass. They run in the
host-side harness, on the host kernel, where both filesystems are
available. No QEMU boot has ever mounted an overlay — checked across every
boot log produced today, including the UEFI one.

So items 1 and 2 of the build order are built and tested, and untested on
the boot path. That is the same shape as the `SKIP` finding earlier today:
the tests are real, and they do not cover the thing a reader would assume
they cover.

The fix is two lines in `mkboot.sh`'s module loop, which already has the
staging machinery for nls. Worth doing before anything else here, because
every brick-related result to date is a host-side result.

## 2. A mount namespace survives with zero processes — on the boot path

The experiment named in `NW-SOLVENT`'s rival design as its load-bearing
bet, and never run: *kill every process in the mntns, setns back in, write
to the overlay.*

Run inside the guest, as a house, all syscalls (there is no `unshare(1)`
or `nsenter(1)` in the root image):

```
[ns] overlay.ko loaded
[ns] child: overlay mounted inside its own mntns
[ns] pinned /proc/80/ns/mnt -> /nwns/pin
[ns] every process in that namespace is dead
[ns] setns ok — re-entered the dead namespace
[ns] read through the overlay: 'written while alive'
[ns] write after death: ok
```

**So restart can be `setns` + `exec`: no remount, no privileged resident
parent per house.** That is the mechanism the whole
delete-the-supervisor design rests on, and it works on the kernel the
system actually boots.

Two supporting results from the container run of the same experiment,
which are worth keeping because the guest probe does not cover them:

- **The pin is what holds it.** Unmount the nsfs bind and re-entry fails
  immediately with `reassociate to namespace 'ns/mnt' failed: Invalid
  argument`. The namespace's lifetime is exactly the bind's, which makes
  *recreate* a real two-verb operation rather than a hope.
- **Re-entry is repeatable** — three times into one pinned namespace, each
  write landing. Restart is not a one-shot trick.
- **A sealed erofs brick survives inside the persisted namespace**: after
  every process died, the brick file still read and the overlay still took
  writes.

One caveat that shapes the design: `mount -o loop` **failed inside** the
unshared namespace and worked when the loop device was attached outside
and the device mounted within. So loop attachment is PID 1's job at first
inhabitation. On the 6.18 target this stops mattering —
`CONFIG_EROFS_FS_BACKED_BY_FILE=y` there and not on 6.8.

## 3. fs-verity works, and the digest is not a function of content alone

Enabled in the guest, on a root with `tune2fs -O verity`:

```
fs-verity: sha256 using implementation "sha256-generic"
ENABLE  ok
MEASURE ok  alg=1 size=32  digest=0c6e57b0…a1c45d17
reopen for write  REFUSED  errno=1 (Operation not permitted)
```

The refusal is at `open`, not at `write`. An object, once sealed, is
immutable to the filesystem.

**And the digest depends on the verification parameters.** Byte-identical
1271-byte files:

```
block_size=4096   digest=0a365f0e0f7679504f6507cd33b0e74586e5fc284f7bf26ba5f22dace1c78bc8
block_size=1024   digest=9a30c81c99d2969c339f833929584910b2a714f3072e3f772aec9c43cf78edce
```

So a store addressed by verity digests has a **store-wide constant** that
must be fixed before any object is sealed and cannot change without
re-sealing everything. It is invisible in the object hash and yet
determines it. Reasoning, not measurement: that is the same species of
binding as a shared base layer — smaller, since it couples objects to a
format rather than apps to each other, but it should be named rather than
treated as a versioning detail.

## 4. Corruption is detectable, and there are three distinct states

Corrupting bytes **under** an already-sealed object, by writing to the
image directly and rebooting:

```
fs-verity (vda, inode 39): FILE CORRUPTED! pos=0, level=-1,
  want_hash=sha256:5f7c9edf…  real_hash=sha256:bfed5012…
open(/nwverity/target) = 3  errno=0
read FAILED errno=5 (Input/output error)
```

Corrupting the **verity metadata** instead, leaving the data intact:

```
fs-verity (vda, inode 39): Wrong data_size: 7233194814767134812 (desc) != 1271 (inode)
open(/nwverity/target) = -1  errno=22 (Invalid argument)
```

Three states, each with its own errno:

| state | signal |
|---|---|
| object absent | `ENOENT` at open |
| data corrupted after sealing | open succeeds, read `EIO` |
| verity metadata corrupted | open fails `EINVAL` |

**This refutes a position taken in review** — that under digest-addressing
there is no way to open an object and get a mismatch, so corruption and
absence become the same symptom. They do not. A store is a filesystem: the
name is a path, the digest is metadata, and nothing makes the kernel
compare them, which is exactly the gap verity covers. The kernel also logs
both hashes, so attribution is *better* than either side of that argument
claimed — not merely *this is wrong* but *this is what it should have
been*.

## 5. Verification costs 401 ms for the largest object, and demand paging does not save you

`sha256` of `libLLVM.so.20.1`, 136.9 MB: **401 ms at 342 MB/s**.

I expected demand paging to make eager verification catastrophic — hash
137 MB to use a few. Measured resident pages during an actual GTK app run:

```
   94.4M / 136.9M   68.9%   libLLVM
    4.8M /  41.4M   11.5%   libgallium
```

The app pages in 69% of `libLLVM` anyway, so eager verification costs
roughly 1.5× the read plus CPU, not the 20× the argument assumed. My
argument was weaker than I thought. `libgallium` at 11.5% is where it
holds.

fs-verity avoids the question — the kernel verifies incrementally and the
cost is bounded by what is actually read.

## 6. A GUI app's closure, and where the weight is

Transitive package closures: `galculator` 182 packages / 489 MB,
`gnome-calculator` 208 / 533 MB, `firefox` 102 / 552 MB. Firefox has
*fewer* packages than the calculator because it bundles what the
calculator inherits. This number over-counts — the tool follows every
alternative of an OR-dependency.

Runtime working set, traced under a virtual display, 25 seconds of startup
with no user interaction: **221 files, 251.4 MB**, of which

```
  136.9 MB  libLLVM.so.20.1        GTK initialises a GL context
   41.4 MB  libgallium…so          and falls back to Mesa's software renderer
   29.4 MB  libicudata.so.74
    7.8 MB  libgtk-3.so.0          the actual UI
```

Three files are 84% of it. Of the whole set, **0.26 MB is the app's own
files** — 99.9% is shareable by content.

This number under-counts, and the reason matters: no file dialog, no
printing, no clipboard, no theme change. `docs/options/08` already hit
this with a compiler — the naive closure looked complete and `chroot`
still failed, because `/etc/alternatives/gcc` is resolved by the kernel
and never appears in a syscall trace at all. A trace is a sample of one
execution, not a specification of a closure.

## Two bake-time flags nobody has set

Both discovered by needing them tonight, neither in `mkboot.sh`:

- `tune2fs -O verity` on the root, without which fs-verity cannot be
  enabled on anything.
- `mkfs.ext4 -O quota` plus a `prjquota` mount, without which the resource
  block's disk-capacity field has no mechanism at all.

## What none of this establishes

Every guest result is on Ubuntu `6.8.0-139-generic` under TCG with no
KVM, on one machine. The container results are on `6.18.44` in a
container, which is not the target either.

The verity probe sealed a file the house itself wrote, not an object
placed by a bakery — who enables verity and when is untested, and it is
the question the whole store design turns on.

And nothing here has been booted on real hardware.
