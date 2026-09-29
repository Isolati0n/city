# 23 — erofs file-backed brick mounts, and verified bricks

Status: **design note. No code in this round.** Answers
`docs/OPERATOR-BRIEF.md` Section 2 item 1g's scope for this note
("erofs file-backed brick mounts, kernel 6.12+, loop fallback, measure
the stacking limit on the target kernel") plus the amendment's item E
("VERIFIED BRICKS: fold into the `docs/options/23` note... evaluate
fs-verity on the image files... whether file-backed erofs reads through
it, how its digest relates to the sha256 file name, and measure on the
target kernel. Say what remains unmeasured.").

`docs/plans/01-brick-images.md` already decided to build this and held
it for a sequencing reason, not a capability one. This note does not
re-decide that; it measures what the hold was waiting on and reports
what is now known, on this container, plus what remains genuinely
unmeasured.

Every claim below was checked against the tree, or measured directly in
a throwaway scratch mount inside an unshared namespace (nothing under
`/nw/*` or the real repo touched), at the time this note was written.

## 1. The hold, and whether it still applies

`docs/plans/01-brick-images.md:192-208` already designed this and held
it explicitly: "**HELD until after phase 3, and the kernel floor is NOT
the reason**... The reason to wait is sequencing: it is a second
implementation of brick mounting landing immediately after the first
shipped a live defect... Take it after phase 3, when the plan carries a
hash and the mount path is otherwise stable." Phase 3 — the plan
carrying a 32-byte hash instead of a path — is landed
(`docs/plans/01-brick-images.md:365`, "Phase 3 — the plan carries a
hash, not a path. **LANDED 2026-09-12.**"; `CLAUDE.md`'s "The
characteristic failure" section makes the same point in passing,
"phase 3 made a brick a 32-byte hash," `CLAUDE.md:1441`).

**So the stated hold condition is satisfied.** This note reads that as
license to design the mechanism now (this section and §2-§3 below), not
as license to build it in this round — 1g is docs-only, and this is
still a mount-path change to trusted-core code (`nwsup.c`'s
`lid_brick()`/`loop_attach()`), which needs `control` + `tcb-review` +
`fd-auditor` when it actually lands, per Section 5's review-by-risk
table. Recorded here as: **ready to schedule, not scheduled by this
note.**

## 2. The mechanism, measured directly rather than assumed

`docs/plans/01`'s claim — erofs can mount a regular file directly via
`fsopen`/`fsconfig`/`fsmount`/`move_mount`, no loop device — is
**confirmed true on this container's kernel (6.18.44-fc-v37)**, and the
measurement surfaced a trap worth recording precisely because it looks
like the opposite result at first:

- **`mount -t erofs <file> <mnt>`, the command form, is NOT file-backed
  — it silently loop-attaches.** `strace` on the `mount` process shows
  util-linux itself calling `LOOP_CTL_GET_FREE` → `LOOP_CONFIGURE` →
  `mount("/dev/loop0", ...)` — exactly `nwsup.c`'s own `loop_attach()`
  dance (`nwsup.c:117-156`), done by the `mount` command on the
  caller's behalf. `losetup -a` during the mount confirms a device is
  attached. **This is the same shape as invariant 1's `mount` grep and
  the log-chunk trap: the obvious command *looks* like the mechanism
  being asked for and is actually the fallback wearing its clothes.**
  Anyone implementing this by shelling out to `mount` (or by calling
  the ordinary `mount(2)` syscall with a file path as source) would
  ship the loop path again, silently.
- **The genuine mechanism is the raw syscalls, called directly, and
  the source must be set as a string, not a file descriptor.**
  `fsopen("erofs", ...)` → `fsconfig(FSCONFIG_SET_STRING, "source",
  <path-to-the-regular-file>, 0)` → `fsmount(...)` → `move_mount(...)`.
  `fsconfig(FSCONFIG_SET_FD, "source_fd", ...)` and `..., "fd", ...`
  were both tried and both fail `EINVAL` — the file descriptor variants
  do not work for this filesystem; only naming the path as a string
  does.
- **Confirmed genuinely loop-free**, not merely by the command
  succeeding: `losetup -a` stays empty throughout, and
  `/proc/self/mountinfo` (the kernel's own view, not `mount`'s cosmetic
  output) shows the source as the regular file's own path, on an
  anonymous device (`0:41`), not `/dev/loopN`. `dmesg` distinguishes the
  two mechanisms by their own log line: loop-backed mounts log `erofs
  (device loop0): mounted with root inode @ nid 36`; the file-backed
  mount logs `erofs (device erofs): mounted with root inode @ nid 36` —
  "device erofs", not a loop device name.
- **The gating kernel config, found empirically rather than assumed**:
  `CONFIG_EROFS_FS_BACKED_BY_FILE=y` on this kernel
  (`/proc/config.gz`). This is the actual feature flag; nothing in the
  tree named it before this note.

**Implementation consequence for whoever builds this** (not done in
this round): `lid_brick()` cannot get this behavior through `mount(2)`
or by shelling out — it needs the four raw syscalls
(`fsopen`/`fsconfig`/`fsmount`/`move_mount`), which have glibc wrappers
in recent versions but may need direct `syscall(2)` calls depending on
the build toolchain's glibc version; that check is part of the
eventual build, not this note. This is Claude's own territory to build
when scheduled — "Claude owns... the mount path in `nwsup.c`"
(`CLAUDE.md:932-934`) — so unlike note 21, this note needs no brief to
Grok.

## 3. The kernel-version citation, corrected

`docs/plans/01-brick-images.md:194` cites "kernel >= 6.12" for this
feature. **No primary-source citation for that number exists anywhere
in this tree** — checked directly: every mention of "6.12" tied to
file-backed erofs traces back to that one sentence and its restatement
in `.claude/rules/runtime.md:403-405` and `docs/OPERATOR-BRIEF.md:36`,
with no kernel.org reference, no commit hash, no
`Documentation/filesystems/...` citation — unlike this same tree's
`sched_ext` claim, which does cite kernel.org by name
(`docs/options/15-per-house-scheduling.md:58`, "sched_ext did merge
into mainline at Linux 6.12 (`kernel.org`'s..."); `docs/ENVIRONMENT.md`
states the same 6.12 fact but without naming kernel.org directly. The
"`tcb-review` demonstrated it working here" the plan cites as its
evidence has no retrievable artifact either: the `.reviews/tcb.*` files
that would record such a run are all zero bytes.

**This note does not manufacture a citation it cannot back.** What it
can state, measured rather than cited: `CONFIG_EROFS_FS_BACKED_BY_FILE`
is enabled and the mechanism works end-to-end on this container's
6.18.44-fc-v37 kernel — comfortably above whatever the true floor
turns out to be, and the target kernel is 6.18 LTS regardless
(`docs/OPERATOR-BRIEF.md`'s "Earlier decisions still stand" list), which
is the same point `docs/plans/01` already made about why the exact
number doesn't matter operationally. **Recommended default: drop the
specific "6.12" figure from future prose unless a primary source is
found for it, and cite the config symbol
(`CONFIG_EROFS_FS_BACKED_BY_FILE`) plus this note's own measurement
instead.** This is a correction to an existing, already-hedged claim,
not a new design question — the hold this note lifts in §1 never
depended on the number being right.

## 4. Verified bricks (fs-verity) — the amendment's item E

**Unmeasurable on this container, at every layer, and said plainly
rather than assumed working:**

- Kernel: `CONFIG_FS_VERITY` is **not set** (`/proc/config.gz`), and
  `FS_IOC_ENABLE_VERITY` against a real scratch file returns
  `EOPNOTSUPP`, confirmed live, not just read from the config.
- Tooling: no `fsverity` CLI installed (available in the distro's repo,
  not installed here). `mkfs.erofs --help` (erofs-utils 1.7.1, the
  version on this box) has **no verity-related flag at all** — whatever
  version might add one is unverified from here.
- erofs's own sysfs feature list (`/sys/fs/erofs/features/`) has no
  verity-related entry on this kernel.

So the amendment's requested experiment — build a tiny erofs image,
enable fs-verity, report the digest format — could not be run on this
machine. **This is a hard capability gap in this environment, the same
shape `docs/ENVIRONMENT.md` already records for other features (e.g.
`sched_ext`), and it needs re-measuring on the actual target kernel and
erofs-utils version before this can move past design.**

**What can be said without running it, read directly from the kernel
UAPI header (`linux/fsverity.h`) rather than measured:** fs-verity's
digest is not a flat hash over a file's concatenated bytes. It is the
hash of a small fixed `struct fsverity_descriptor` (hash algorithm,
block size, data size, and a Merkle-tree root hash over the file's
blocks, plus an optional salt affecting every block hash) —
structurally different from a flat sha256 of the whole file, even
though both may use SHA-256 as the underlying primitive. `NW_BRICK_HASH`
is exactly the latter: `bakery/mkbrick.py`'s `sha256_file()` reads the
built `.img` in 1 MiB chunks and hashes the stream directly
(`bakery/mkbrick.py:72-77,161`) — a flat hash of the packed image
file's bytes, computed **after** packing, not of the source tree.
(This corrects an imprecise description elsewhere in this tree calling
it "the sha256 of the tree's contents" — it is the sha256 of the built
image file, which is deterministic across repacks of the same tree only
because `mkfs.erofs` is invoked with a fixed, documented flag set.)

**Consequence for design, stated as reasoning rather than as something
measured**: these two hashes cannot be unified into one value, and
should not be designed as if they could be — `NW_BRICK_HASH` is the
plan's naming and content-addressing identity (what `brick=` refers to,
what `nwcheck.c` and `nw-sup` compare against), and fs-verity, if it is
ever adopted, would be an **additional**, kernel-enforced, at-rest
tamper/corruption check layered on top — not a replacement for the
plan's own identity field, and not derivable from it or vice versa.
Adopting fs-verity would mean the image carries two independent
digests for two independent purposes: this note takes no position on
whether that trade is worth it, because there is nothing here capable
of measuring the actual benefit (read-time cost of `nw-sup`'s existing
full-file re-hash at brick mount versus a kernel-enforced incremental
check) to weigh against the added complexity.

**Say what remains unmeasured, per the amendment's own instruction**:
whether file-backed erofs (§2) reads through fs-verity at all on a
kernel that has both enabled; which erofs-utils version (if any) adds
native fs-verity support to `mkfs.erofs`; and the actual read-time cost
comparison above. None of these can be answered from this container.

## 5. The stacking limit, on the target-adjacent kernel

Already measured in this tree and not re-run destructively this round,
per the instruction to avoid bulk-attaching loop devices on this
ephemeral container (`docs/ENVIRONMENT.md:131`, "The container is
ephemeral"), which other work may be running on concurrently. The
existing measurement
(`docs/plans/01-brick-images.md:56-86`, taken on this same machine):
binding loop devices via `LOOP_CTL_GET_FREE` directly (not through
`mount -o loop`, which reuses a device for a repeated same-file mount
and under-reports) reached **4096 devices attached with no failure**,
which is `max_loop`-independent and ≥ 64× `NW_MAX_UNITS`. Confirmed
today: baseline is a clean 0 attached, `max_loop=8` (a parameter, not a
ceiling — the same measurement already established this), and the
trap it names (loop-reuse via `mount -o loop` under-reporting) is
independently reproduced by this note's own §2 measurement, which found
the same command silently loop-attaching.

**This section becomes largely moot for bricks specifically, once §2 is
built**: file-backed erofs mounting uses no loop device at all, so a
brick house stops consuming a loop device entirely. It does not remove
loop devices from the picture altogether — a sized layer
(`layer-bytes=`) still loop-mounts its ext4 backing file
(`.claude/rules/runtime.md`'s layer-capacity section), so the stacking
question stays live for layers even after bricks stop needing it. The
tree's own prior conclusion stands: the true binding constraint at
scale is the file-descriptor budget, not the loop-device count
(`docs/plans/01-brick-images.md:239-250`, `.claude/rules/harness.md`'s
"Where it breaks and why").

## Definition of done

This is a docs-only note. No code changes to `nwsup.c` or anywhere
else. `docs/QUEUE.md`'s item 1g entry is updated in the same commit
that lands this note.
