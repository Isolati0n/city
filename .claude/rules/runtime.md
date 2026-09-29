# runtime — territory rules

<!-- nw-init:install-agents v1 -->
**Not an agent.** This was a dispatchable brief until 2026-09-10 and was
never dispatched once. Its content is reference read at the moment it
applies, so `tools/rules-hook.sh` delivers it on a `PreToolUse` for any
file in this territory. Scope: Owns the boot chain and per-unit execution; `tools/rules-hook.sh --owns runtime` lists the files. Use for mount and pivot, boot sequence, forking and reaping, shutdown ordering, restart budgets, namespaces, seccomp, Landlock, bricks and binds, and exec of a house. NOT for liveness or freeze detection: there is none, deliberately — read the Liveness section before proposing any.

You own the chain that turns a validated blob into running houses:
`dawn.c` → `pid1.c` → `nwspawn.c` → `nwsup.c` (+ `lids.c`) → the
house. All TCB. A fault here does not crash a program, it fails to boot a
machine. **Plus `rescue.c`, which is yours and is NOT in that chain** —
it is a mode PID 1 enters instead of booting, and the sentence above
would otherwise answer "no" to a reader asking whether it is theirs.

## Why this is one territory and not two

It was two — a PID 1 agent and a supervisor agent — until 2026-09-10, and
**D11 is the argument against that split.** `nw-spawn` blocked every signal
before its first fork; a signal mask survives both fork and exec; so
`nw-sup` and every house started fully masked, every TERM handler was dead
code, and the symptom appeared somewhere else again — in `pid1.c`'s
shutdown, which sent TERM, got no answer, and expired into SIGKILL. One
cause, three files, and it is invisible to anyone holding one of them.

State flows *down* this chain — mount namespace, signal mask, descriptors,
environment — so a change to any link is a change to everything below it.

## What each stage may and may not do

- **`dawn`** mounts and pivots, then execs `nw-root` with a path. It is
  the only place in the TCB that knows what a filesystem is. Configuration
  comes from the environment (the bootloader supplies it via the kernel
  command line); nothing is defaulted, because a boot that does not say what
  to mount should fail loudly rather than guess at hardware. Strict mounts
  for the root and the ESP (ours to make, failure is fatal); ensure-mounts
  for `/dev`, `/proc`, `/sys` and cgroup2, where `EBUSY` means the
  requirement is already met.
- **PID 1 mounts nothing, and must keep mounting nothing.** `grep` for
  `mount` in `pid1.c` returns only comments — it said "one hit" until
  2026-09-11, when there were two. It cannot
  mount the thing it needs in order to learn what to mount; the alternative
  is a device name compiled into the trusted core, which is the
  fixed-descriptor-number class in a new costume.
- **PID 1 has no restart budget and must not grow one.** `grep` for
  `budget`, `restart` or `respawn` in `pid1.c` returns nothing.
- **`nw-spawn` exits, and that is success**, not something to watch for.
  Require a complete pid report *and* `WIFEXITED` with status 0. Its
  predecessor was fatal on death because it held the only copy of the
  connection graph. Edges are back (`docs/options/17-edges.md`), but a
  socketpair's two ends need no live process holding a third reference
  once each end has been handed to its owning house via `fork()`
  inheritance — `nwspawn.c` closes its own copies once every unit has
  forked and exits normally after the loop, exactly as it always has, so
  there is still no mid-life. (This used to say "with edges gone there
  is no graph", which stopped being true the day edges came back; the
  conclusion — no mid-life — never depended on there being no graph, only
  on nothing needing to hold one open.) Do not give the spawner one.
- **`nw-sup` owns the budget and the lids**, one authority per unit.

## Hard rules

- **No allocation, no parsing, no recursion after start in PID 1.** The one
  text it reads is `<slots>/current`, at boot, bounded to `NW_NAME_LEN`
  and validated to `[A-Za-z0-9_-]` so it cannot escape the slots directory.
- **The budget is a hard total of deaths for the supervisor's life** —
  `int deaths` in `nwsup.c`, compared against `budget`, never reset —
  and **budgets are never nested.**

  *This said "a ring of timestamps, never a counter" until 2026-09-11,
  and `CLAUDE.md` had already retracted that sentence twice while this
  copy stayed. There has never been a ring. The sliding window that
  replaced it in the telling was worse than a wrong description: a reset
  made the budget unbounded, which is D18. This file is what
  `tools/rules-hook.sh` hands an agent the moment it edits `nwsup.c`, so
  a stale rule here is delivered straight into the work. `drift` and
  `fd-auditor` both found it.* Bug 3 was a supervisor
  giving up, PID 1 restarting it with a fresh budget, and the pair looping.
- **No compile-time descriptor numbers alongside dynamic allocation.** Bugs
  5, 9 and 13 were one mistake three times, and none of them produced an
  error — they produced silently wrong routing. Sweep `/proc/self/fd`.
- **`wait_house()`'s `signalfd` is a FALLBACK, not a second channel run
  alongside `pidfd`.** `nwsup.c` opens `pidfd_open(2)` first; `signalfd`
  is created only when that call failed, never unconditionally. This is
  the ORIGINAL shape, restored by item 1c (2026-09-28,
  `docs/OPERATOR-BRIEF.md` Section 2) removing `FREEZE`/`CONT`
  (`docs/options/16`, now superseded) — for one round `FREEZE` needed
  `signalfd` running unconditionally beside `pidfd`, because
  `pidfd_open(2)` is documented to become poll-readable only on genuine
  termination, never on a ptrace-stop, and a frozen house needed a
  channel that could see one. With `FREEZE`/`CONT` gone, nothing here
  ever `ptrace`-attaches a house (`grep -nE "ptrace|PTRACE" nwsup.c`
  returns nothing outside a stale-history comment), so there is no stop
  for `pidfd` to be blind to. `pfd` and `wake` (the two fds) are
  mutually exclusive in the current code: `wake` is only assigned
  inside the `if (pfd < 0)` branch, so at most one of them is ever
  polled, never both. The exec-fence pipe (a `CLOEXEC` pipe that let
  `FREEZE` tell whether a forked child had reached `execv()` yet) and
  its per-fork reset are gone with it — nothing left needs to ask that
  question.

  **One coverage gap this removal quietly opened, closed in the same
  change.** `test_ctl_exec_resets_sigchld_mask` pins a per-fork
  `sigprocmask(SIG_SETMASK, &empty, NULL)` reset (a real, still-
  necessary fix: `wait_house()`'s `signalfd` arming blocks SIGCHLD in
  `nw-sup`'s own process and never unblocks it, so an unguarded second
  fork would inherit that straight into the house's own image). The
  test forced nothing before this change — `pidfd_open` succeeds on
  any real kernel here, so `signalfd` was created unconditionally
  regardless, and the bug's precondition always held. Once `signalfd`
  became fallback-only, the same test with no forced failure never
  makes `wait_house()` touch `sigprocmask` at all, so the reset it
  exists to pin stops mattering to it — measured directly: mutating the
  reset out and rerunning the unmodified test left it green. Fixed by
  making the test force the `pidfd_open` failure with
  `tests/fault_inject.so.c` (the same shim `test_ctl_pidfd_fallback_with_socket`
  already uses, both via `NW_FAULT_ENOSYS=pidfd_open`; the shim was
  consolidated from three now-deleted one-syscall files -- `tests/count_wait.so.c`,
  `tests/block_pidfd.so.c` and `tests/block_both.so.c` -- into this one,
  env-configured, after this fix landed), which put the mutation back to
  red.
<!-- nw-init:absent-ok tests/count_wait.so.c tests/block_pidfd.so.c tests/block_both.so.c -->
- **One seccomp table.** `nwsup.c` calls `nw_apply_house_seccomp()` in
  `lids.c`; it once carried a verbatim second copy. There is one allow-list,
  `strict_allow[]`, and a house does not choose it. *This described
  `NW_PROF_BUILD` as "assembled as `NW_PROF_STRICT` plus `build_extra[]`
  at filter-build time" until 2026-09-11; `grep` for `NW_PROF` or
  `build_extra` across the C sources returns only a comment in `blob.h`
  recording the removal. The profile went on 2026-09-10
  (`HISTORY.md` §23) and `CLAUDE.md` invariant 6 already said so — this
  copy did not. Found by `claims`, in the file the hook hands to an agent
  editing `nwsup.c` or `lids.c`.* If you find yourself adding a filter
  anywhere but `lids.c`, you are recreating the bug that was removed.
- **Adding a syscall to the allow-list requires naming the unit that needs it
  and why.** The suite asserts seccomp kills a house that calls
  `socket()`; if your change makes that pass, you widened the filter.
- **Lid order is fixed and is not a style choice:** NEWNET → NEWNS → brick
  pivot → Landlock → seccomp. The strict allow-list has no `mount`, no
  `unshare` and no `pivot_root`, so a house sealed first could not enter
  its own root. Sandboxing goes after the descriptors are in place and before
  `execv`.
- **`lids.c` returns -1 rather than exiting;** the caller decides what a
  failure means. Preserve that split — the shim reports, the supervisor sets
  policy.
- **Shutdown is bounded by the grace period, not grace × units.** Do not
  serialise it.
- **Loggers live in their own process group, and a house's last words are
  a correctness property.** `spawn_logger` calls `setpgid(0, 0)`. The
  loggers unblock TERM/INT — they must, or a drain pass that TERMs them
  sits pending forever, which is D11 — and that same unblocking makes
  them die on a **group-directed** TERM, where the default action is
  terminate, before they have drained the pipe. Measured, six runs each:
  TERM to PID 1 alone relayed 5 of a house's 5 final lines; the same TERM
  to the process group relayed **0 of 5**. The house wrote them all; its
  reader was gone.

  This is not tidiness. The budget is a hard total, so a house that
  exhausts it **stays dead until reboot**, and that was only acceptable
  because the death is visible. Lose the last lines and it is a black
  screen with no explanation — the property that made a hard total unsafe
  before. `test_last_words_survive_group_term` pins both directions;
  removing the `setpgid` turns it red naming the drain.

  Do not "fix" this by re-blocking TERM in the logger or by `SIG_IGN`.
  Both survive the group signal by making an *explicit* TERM do nothing
  as well, which is D11's exact shape laid across the natural fix. The
  process group makes the signal not arrive while
  `kill(logger, SIGTERM)` still works.

  **The lexical check lives here, not in `pid1.c`.** Nothing this tree
  ships sends a group-directed signal:
  `grep -nE "killpg|kill\(-|kill\(0|tcsetpgrp|setsid" *.c` returns
  nothing. It is written in this file rather than beside the code
  because a comment naming the tokens it greps for is a hit for its own
  check — which is exactly how invariant 1's `mount` check was weakened
  to "returns only comments" and stopped being decisive.

  **Precondition, and it is not hypothetical for long:** this is safe
  while PID 1 has no controlling terminal. Give it one and two things
  flip together. `^C` becomes a kernel-generated group signal, which
  makes the `setpgid` load-bearing on real hardware; and the loggers,
  no longer the foreground group, become subject to `SIGTTOU` on
  `write(2)` when `TOSTOP` is set. `SIGTTOU` is not in the set PID 1
  blocks, so the default action applies and **the logger stops** — the
  pipe fills, the house blocks in `write` forever, and freeze detection
  is refused by design, so nothing notices. Demonstrated on a pty by
  `tcb-review`, same program either side, only the `setpgid` differing.
  If a console lands, block `SIGTTOU`/`SIGTTIN` in the logger.

- **Shutdown lets the loggers drain, and SIGKILL is the deadline
  action, not the first one.** `shutdown_city` waits for each logger to
  reach EOF and exit — PID 1 closed its own write ends at boot, so the
  last house's death closes the pipe — bounded by `NW_GRACE_MS` and
  concurrent across units, so the bound is still the grace period and
  not grace × units.

  It was an unconditional SIGKILL until 2026-09-11, racing the drain.
  It won small and lost large: `tcb-review` measured 2500 final lines
  (~160 KiB) relaying 2500, 2433 and 2451 across three runs, and the
  loss was a **contiguous tail** — the end of the output, which is the
  part that says why the machine is going down. At five lines it never
  lost anything, and five lines was the size the suite pinned. A
  property tested only at the size where it holds by luck is the
  characteristic failure with a test attached to it.
  `test_last_words_survive_group_term` now runs 5 and 2500; restoring
  the unconditional SIGKILL turns the 2500 case red and leaves the 5
  case green, which is the whole argument for the second size.
- **Signal-safety:** writes go through `write(2, ...)` directly. No
  `printf` in a signal or post-fork path.

## Bricks

**Landlock grants WRITE beneath the root as of 2026-09-12, and NOT
TRUNCATE**, because it runs after the brick pivot and that root is the
overlay — granting only read made every landlock house's layer
unwritable, confirmed live as `wr_root=denied(13)` where a read-only
image gives `denied(30)`. What the lid still provides is the withheld
`MAKE_` rights (no device nodes, sockets or fifos) and the scoping of
binds. `MAKE_REG` is withheld too, so a landlock house modifies what its
brick shipped with and creates nothing new under `/`.

`TRUNCATE` was granted with the write for one round and is withheld
again: an emptied file copies up into the durable layer and no boot
recovers, which is the same unrecoverable state `REMOVE_FILE` is
withheld to prevent — so granting one and withholding the other is
incoherent. It is still granted **inside a declared bind**, which is
machine-side and outside the layer. The consequence for a house:
`open(..., O_TRUNC)` and `ftruncate` beneath `/` fail with EACCES;
rewrite in place, or declare a bind. Enforced only at ABI ≥ 3, because
the right does not exist below that.

**It does not close the durable-mask class, and the first telling said
it did.** `WRITE_FILE` stays granted and reaches the identical state by
overwriting a brick file in place — measured on a real overlay, eight
bytes over an ELF header, no truncate and no unlink, and every later
mount of the same sealed image plus the same layer is unrunnable while
the image stays byte-identical. Nor can a house do it to its own exec
path: that is `ETXTBSY` while it runs, so the reachable target is a
shared library or a data file the brick shipped. What the withholding
closes is the zero-length route and the accidental `O_TRUNC` rewrite.
Making the class unrepresentable means stopping the layer shadowing
the image's executables at all — a design change, not a rights change.
`CLAUDE.md` invariant 6, `HISTORY.md` §56, §57 and §58.

**THE CLASS IS OPEN, AND THE RECOVERY BELOW IS THE ONLY ANSWER TO
IT.** Not a footnote and not an aside: there is no lid set
that prevents a brick house durably masking its own image, and nothing
in the tree detects one that has. `landlock` is the narrowest and it
narrows the routes, not the outcome. So when a house that booted
yesterday will not exec today and the image still hashes to its own
name, do not debug the image and do not rebuild it — read **THE
RECOVERY** below and delete the layer. Every round that has touched
this so far has reached for a rights change first; the rights are not
where the answer is.

**A LAYER CAN MASK ITS BRICK, DURABLY.** "Reads fall through to the sealed
image" is true and incomplete: the overlay can also whiteout and overwrite,
and those survive reboot because the layer is durable and keyed by an id.
Measured: a house that unlinks its own exec path leaves a whiteout in
`upper` and never starts again — same plan, same sealed brick, `FAIL exec
house errno=2` forever, image byte-identical to its own name. 

**THE RECOVERY — the only answer to the durable-mask class, not a
footnote to it.** Written down because nothing implements it and the
next person to hit this needs the answer, and pointed at from the
Landlock paragraph above because that is where the wrong answer gets
reached for. It is **not** rebuilding the
brick: the image is untouched and still hashes to its own name, so
rebuilding changes nothing. It is both of these, in order:

    rm -rf /nw/layers/<layer-id>
    python3 tools/stage-layers.py <blob>    # recreates upper/ and work/

`rm` alone gives `FAIL mount layer errno=2` on every boot, because
nw-sup does not create what the stager owns. The house's data is lost;
there is no way to keep it and undo the mask. **Nothing in the tree does
either step** — a reclaim path is a real missing piece. The phase-2 seal protects the
image file, not the house's view of it. `tcb-review`.

**Every brick house also has exactly one writable layer, and the two are
one thing.** `lid_brick()` mounts the erofs image on `NW_BRICK_MNT` and
then mounts an overlay **at that same mountpoint** with
`lowerdir=NW_BRICK_MNT`, `upperdir=/nw/layers/<id>/upper`,
`workdir=/nw/layers/<id>/work`, and the house pivots into that. Reads fall
through to the sealed image; writes land in `upper` and survive a restart.

- **Stacked, not given its own mountpoint.** Verified by mounting, not by
  reading: overlayfs resolves `lowerdir` at mount time and holds the
  superblock, so covering the path afterwards is fine. One mountpoint means
  no second directory for dawn and no third path in the design.
- **Before the binds.** A bind mounted first is hidden by the overlay
  covering the same mountpoint, and the mount would still succeed — the
  house would silently see the brick's empty directory instead of the bound
  path.
- **`upper` and `work` are siblings under one parent**, which answers
  overlayfs's requirement (same filesystem as `upper`, not inside it) by
  construction rather than by a rule anyone has to remember.
- **nw-sup creates NEITHER.** `tools/stage-layers.py` does, from the plan,
  before the boot. A supervisor that mkdir'd a missing layer would turn
  "nothing staged this plan" into "the house silently got an empty layer",
  which is the orphaned-data failure the layer-id exists to prevent. A
  missing layer is a loud `FAIL mount layer` instead.
- **One layer per house, enforced.** Two houses on one id share one
  `upperdir` and one `workdir`: each appends to the other's data and the
  kernel calls the workdir sharing undefined behaviour, into `dmesg`,
  which nothing here reads. `NW_E_LAYERDUP` in `nwcheck.c` (a second pass
  of the same `field_dup` table that catches duplicate names) and a
  named refusal in the baker.
- **Keyed by a declared id, never by the house name.** Rename a house under
  name-keying and it gets an empty layer while its data sits under the old
  name, with nothing reporting anything.
- `/nw/stores` is **gone**, not renamed: the store concept was this
  mechanism under another name.
- **A declared `layer-bytes=N` makes the layer a fixed-size, loop-mounted
  filesystem instead of a plain host directory, closing the
  "resource block, nothing applies it" gap above for this one field.**
  Project-quota enforcement was refused -- `docs/ENVIRONMENT.md` records
  project quota off on this machine's root device, `quotactl` answering
  `ESRCH` -- so capacity is a filesystem boundary instead, the same way
  a brick's seal already is one. `tools/stage-layers.py` truncates a
  backing file (`<id>.img`, a SIBLING of `<id>/` so the mountpoint stays
  an empty directory) to `layer_bytes` and formats it with
  `mkfs.ext4 -d`, pre-populated with empty `upper`/`work` directories --
  the same "build from a directory tree" shape `mkfs.erofs` already
  uses for bricks, so nw-sup mounts what staging built rather than
  creating anything itself. `nw-sup` loop-mounts that file at
  `NW_LAYER_DIR/<id>` -- `loop_attach()` is the brick's own loop-attach
  retry loop, extracted so this second loop-mounted image reuses it
  rather than duplicating it, and the loop device fd itself is
  opened `O_RDWR` here (not `O_RDONLY` as the brick's is): the seal is
  over-determined either way, and read-write needs BOTH the backing fd
  and the device fd to agree. Landing before that, the overlay's
  `upperdir`/`workdir` resolve inside this mount instead of on the
  machine root, so the rest of `lid_brick()` is unchanged.
  `layer_bytes=0` (unset) is the plain-directory case, exactly as
  before -- `test_layer_survives_a_restart` is that regression check,
  unmodified. `test_layer_bytes_enforces_capacity` boots two houses on
  separate, equally-capped layers: one fills its own to `ENOSPC`
  (confirmed via `statvfs(2)` from inside the house that the reported
  capacity is bounded by the declared size, not the host disk), the
  other's independent restart-and-persist cycle is unaffected, checked
  by mounting its own backing file read-only after the boot -- a sized
  layer's contents are NOT visible from outside while the house is
  running, unlike the plain-directory case, because they live inside a
  mount torn down with the house's own private namespace.

  **Redeclaring `layer-bytes` for an id that already has a DIFFERENT
  on-disk representation is refused at stage time, not acted on.**
  `tcb-review` found the first version judged only what the current
  plan asked for and never consulted what was already on disk, so
  re-baking an unchanged `(id, brick)` pair with a changed
  `layer-bytes=` silently built a second, disjoint representation next
  to the first -- a sized store mounted OVER a plain directory's
  contents, or the reverse left a sized store's data behind an
  unmounted mountpoint -- with nothing anywhere reporting it. Exactly
  the "renamed house, orphaned data" failure the layer-id keying design
  above exists to rule out, reached through a second identity axis
  (sized vs. unsized) that design never accounted for. `stage()` now
  inspects the disk before acting -- `<id>.img` existing means sized,
  a plain `<id>/upper` existing with no `.img` beside it means unsized
  -- and refuses a request that disagrees with what it finds, by name,
  rather than choosing a winner. A REQUEST THAT AGREES stays idempotent
  exactly as before. `test_layer_bytes_representation_switch_refused`
  pins the refusing transitions named above (a resize, sized to
  unsized, unsized to sized) and the one that must not refuse.

  **`nw-sup`'s own pairing re-checks missed the capacity's half of the
  pairing too**, a LOW finding from the same review: `brick without
  layer` and `layer without brick` were re-validated (nw-sup reads its
  unit from the environment, not the sealed blob, so nothing upstream
  stands behind these values), but a forged or buggy `NW_LAYER_BYTES`
  with no `NW_LAYER` fell through `lid_brick()`'s guard and simply
  skipped the capacity block -- failing safe, but inconsistently with
  its two neighbours, which die rather than silently drop a field they
  cannot act on. `die("layer bytes without layer")` closes it, mirroring
  `nwcheck.c`'s own `NW_E_CAPNOLAYER`.
  `test_layer_bytes_without_layer_dies_at_the_supervisor` drives it
  directly, the same way `test_brick_hash_revalidated_at_the_supervisor`
  drives its neighbours: no plan can carry this state, so the guard is
  exercised against a value no plan produced.

A unit declares `brick=<hash of an erofs image>` and `layer=<id>`. `lid_brick()` makes mount
propagation private, attaches the image to a loop device, mounts it on
`NW_BRICK_MNT` (`/nw/mnt`, which **dawn creates** — that is a precondition
of any brick house starting), applies the declared binds, and pivots. After
that the house's `/` **is** the brick.

*This described the pre-phase-2 code until 2026-09-12 — "binds the brick
onto itself … a brick is a plain directory" — which was two false clauses
in the file `tools/rules-hook.sh` hands to the next agent who edits
`nwsup.c`. Same shape as the `window_s` sentence, same file, and the
paragraph three sections down that says why a stale rule here is worse than
elsewhere was already there. `tcb-review`.*

- **`LOOP_CTL_GET_FREE` reports a free index; it does not reserve one.**
  Every brick house runs `lid_brick()` concurrently, so without a retry
  they all get the same index and all but one get `EBUSY`. Shipped that
  way and measured: at two houses one failed on every run and the restart
  budget hid it; at eight, houses were permanently lost; at
  `NW_MAX_UNITS`, 6–15 of 64 attached — while the city printed
  `closed houses_reaped=64 orphans=0`.

  The retry re-does `GET_FREE` each attempt, **bounded by `NW_MAX_UNITS`
  because that is derived** — at most that many houses can contend. Do
  not replace it with an index derived from the unit's table position
  (that is `BASE + i` on a device number, i.e. bugs 9 and 13 again) or by
  serialising in `nw-spawn` (that breaks `AUTOCLEAR`'s anchor and
  invariant 5). A file-backed erofs mount would remove the class outright
  and is recorded in `docs/plans/01`; it raises the kernel floor to 6.12,
  which is a production decision.

- **The budget is not a retry mechanism.** Anything that spends a death on
  a transient resource race is spending a hard total (invariant 4) that a
  longrun house needs for a real crash. That is why the concurrency test
  asserts zero restarts and zero `EBUSY`, not merely that every house ran.

- **`NW_LID_NEWNS` is mandatory.** `nwcheck.c` returns `NW_E_BRICKNS`
  without it. `nwsup.c` re-checks it anyway, because it reads its unit from
  the environment rather than from the sealed blob.
- **Never `mkdir` into a brick.** A bind target must already exist inside
  it. A brick is sealed and content-addressed; creating a directory to make
  room for a mount would break the seal to save a bake-time decision.
- **The pivot is `pivot_root(".", ".")`**, not the two-directory form,
  which would need a `put_old` directory inside every brick. New root and
  `put_old` are the same directory; the old root ends up stacked on top and
  is detached through a descriptor opened beforehand.
- A bind is a **path made visible**, not a descriptor handed over, and it is
  the same path inside and out. Invariant 5 is about the descriptor table a
  house is born with, and that is still `/dev/null` on 0 and a log pipe on
  1 and 2.
- **`NW_CTL_DIR` is the one bind target with its own rule, not a general
  read-only-bind field.** Item 1f (2026-09-28, `docs/OPERATOR-BRIEF.md`
  Section 2): a declared `bind=` that resolves to `NW_CTL_DIR` gets
  remounted `MS_BIND|MS_REMOUNT|MS_RDONLY` after the ordinary
  `MS_BIND`, in the bind loop — the same shape `brick=` forcing
  `NW_LID_NEWNS` already uses, not a new plan field, because there is
  exactly one path this applies to and it never varies per house.

  **Resolved by `stat()`-identity (device and inode), not by comparing
  the declared string to the constant.** The first version did compare
  strings, and `tcb-review` broke it without touching a symlink: a
  plan declaring `bind=/nw/ctl/`, `bind=//nw/ctl` or `bind=/nw/./ctl`
  passes `nwcheck.c`'s `path_ok_len` (which rejects `..` and control
  bytes, not a trailing slash or a doubled slash), bind-mounts the
  identical directory exactly as `/nw/ctl` would — the kernel resolves
  a mount's source path the same way `stat()` does — and yet none of
  those spellings `strcmp`-matched `NW_CTL_DIR`, so the remount
  silently never fired and the real control directory stayed fully
  read-write through a differently-spelled bind. `stat()` on the
  declared bind and on `NW_CTL_DIR` itself, compared by `st_dev`/
  `st_ino`, answers "does this resolve to the control directory" the
  way the mount itself resolves it, closing the whole class of
  equivalent spellings at once rather than adding a string variant to
  reject one at a time. A failed `stat()` on either side `die()`s
  rather than silently treating "cannot tell" as "not the control
  directory" — the exact failure mode the string comparison had.

  **Two mount(2) calls, not one**: the kernel silently drops
  `MS_RDONLY` combined with `MS_BIND` in a single call, so the remount
  is the only step that takes effect. Measured directly: after both
  calls, `open(..., O_CREAT)` and `unlink()` through the mountpoint get
  `EROFS` (errno 30) while `connect(2)` to a socket already there still
  succeeds — a read-only bind withholds the ability to change what a
  directory contains, not the ability to read or connect to what is
  already in it.

  **`NW_CTL_DIR` itself is `0700`, chmod'd unconditionally on every
  boot, not only on the branch that just created it.** `/nw` is on the
  persistent root, so a directory a predecessor binary left at `0755`
  survives an upgrade to a binary that only conditionally narrows a
  mode it did not choose; every `nw-sup` enforces `0700` every time
  instead. **`fchmod` on an `O_DIRECTORY|O_NOFOLLOW`-opened descriptor,
  not `chmod(2)` by path** — `chmod(2)` follows a symlink, so if
  `NW_CTL_DIR` were ever replaced by one the path form would narrow the
  symlink's *target* instead and leave the directory the name is
  supposed to mean untouched. `fd-auditor` found this inert under
  today's threat model (every house is uid 0; invariant 5 says nothing
  here gains from planting such a symlink that it could not already do
  directly) and worth the categorical fix anyway rather than a check
  for one attack shape. The socket FILE's mode comes from the process
  `umask` at `bind(2)` time — `socket(2)`/`bind(2)` take no mode
  argument — so `nw-sup` sets `umask(0077)` immediately around that one
  `socket()` plus `bind()` pair and restores it right after, not for
  the rest of the process's life, so nothing else `nw-sup` creates (a
  loop device, a layer mount) is affected by the narrowed mask.

  **What this closes and what it does not, stated because
  `docs/options/11`'s own access-control paragraph would otherwise read
  as contradicted by it.** Every house on this machine is uid 0 with no
  privilege dropped (invariant 5), and root does not go through a
  directory's permission bits, so `0700` and a read-only bind change
  nothing about what one house's uid-0 process can already reach on
  this machine — a brickless house's unrestricted view of `NW_CTL_DIR`,
  which `docs/options/11` already documents as inherited from invariant
  5 rather than opened by the control channel, is exactly as
  unrestricted after this as before it. What it closes is real against
  a uid this design does not have yet: measured, connecting to a
  `0600` socket as a mapped, non-root uid gets `EACCES` where root's
  own connect through the identical path succeeds.

## `rescue` — an operator mode, and it is not a fallback

Added 2026-09-20, when an audit of `tools/rules-hook.sh` found `rescue.c`
in no territory. It is TCB by `CLAUDE.md`'s table, it is forked by PID 1,
and no rules file mentioned it — so the hook had never delivered anything
to anyone editing it, and there was nothing to deliver.

**`rescue.c` is the smallest thing in the TCB and the rule is to keep it
that way.** It writes a fixed string to fd 2 and returns 3: one
`write(2)` IN THE SOURCE, no parsing, and `int main(void)`, so no
argument handling at all. Say source rather than process — `strace -c`
on the built binary counts thirty syscalls dynamically linked and
fifteen static, because the loader runs first, and `brk` appears in
both. Anything that makes the SOURCE need a second syscall is a request
to put logic in the one component whose value is that it has none.
`claims` measured it.

**The behaviour is in `run_rescue` in `pid1.c`, not in `rescue.c`.** PID 1
composes `<dir>/nw-rescue` from the `--rescue` argument, forks, execs,
waits, and exits with the child's status. Read that function before
changing anything here; the binary is a message, the mode is the caller.

**It is reached only when asked for, and a failed boot does not fall into
it.** `run_rescue` runs when `--rescue DIR` is given and none of `--plan`,
`--slot` or `--slots` is. Nothing in the boot chain passes it — `dawn`
execs `nw-root --slots <path>` and nothing else — and the only `--rescue`
in the tree outside `pid1.c` is in `tests/run.py`. So a burned image
carries a rescue binary that the burned image's own boot chain cannot
reach. If you want rescue on a failure, that is a new mechanism and a
design decision, not a repair.

**It ends in `_exit`, as every `halt_now` in `pid1.c` does. The split
worth naming is success against failure, not rescue against shutdown.**
`_exit` is the rule across the boot chain — `halt_now` at every failure
in `pid1.c`, `die` in `dawn.c` — and `shutdown_city`'s
`reboot(RB_POWER_OFF)` is the exception, guarded by `getpid() != 1`.
`pid1.c`'s comment says why it reboots: returning is `Attempted to kill
init`. What makes rescue different is that it is the only `_exit` on a
SUCCESS path.

A first telling called it an asymmetry against shutdown and said that on
hardware the success path panics the machine. That is a rule with no
subject, and the paragraph above is what denies it: argv is fixed at
every exec of the burned chain — `initrd-init` execs `/dawn` with
`{"init", 0}`, `dawn` execs `nw-root --slots <path>`, and the
`APPEND=` line in `tools/mkboot.sh` carries no `init=` or `rdinit=` —
so no hardware boot can reach this mode at all. **If one ever does, its success path exits
PID 1, which is a panic**; that is the prerequisite, and it arrives with
whatever adds a hardware caller. The lab cannot see it either way,
because `unshare --pid --fork` turns a PID 1 exit into a status, which
is the gift `.claude/rules/harness.md` inventories. `claims` ran the
reachability census.

**Exit status 3 means two things.** `rescue.c` returns 3, and
`run_rescue`'s `WIFEXITED` fallback also reports 3 when the child died
on a signal — reproduced with a stand-in that kills itself. So "rescue
ran and printed" and "rescue was killed before it could" are one
status. `test_rescue` is not fooled because it requires the message
text as well as the status; the status alone would not be a pin. Keep
the pair if that test is ever touched.

Two more things this channel does, neither wrong and both worth knowing
before anyone changes it. A missing `nw-rescue` in the named directory
exits **70**, not 3, because the child's own `halt_now` becomes the
child's status and PID 1 passes it through — "exits with the child's
status" working exactly as written. And `waitpid`'s return is discarded
into an `st` initialised to zero, so a `waitpid` that failed would make
`WIFEXITED(0)` true and PID 1 would exit **0**, reporting success
having collected nothing. Reachability of that one is unestablished —
signals are not blocked at that point and no handler is installed, so
`claims` could not construct it — but the subject of this section is
what the status means, and a path that reports success from a failed
wait belongs in it.

## Liveness — a recorded refusal, not a missing feature

**Freeze detection is deliberately not in the design. A house that goes
silent but never exits is undetected by anything, and that is known and
accepted.**

`nwsup.c` blocks in `waitpid(p, &st, 0)` with no time bound. There is no
heartbeat, no deadline, no timeout, no `alarm`, no `WNOHANG`. There is no
field to put one in, and nothing in the plan language or the baker expresses
a deadline. `grep` for `clock_gettime`, `now_ms`, `alarm` or `nanosleep` in `nwsup.c` returns nothing since D18 removed the restart-budget window; `#include <time.h>` survived as dead weight. That is four names, so it is not the same claim as "no timing primitives at all", which is what this line used to say — `usleep`, `setitimer`, `timerfd_create` and a `poll` timeout would all pass it. `claims` widened the search and found none of them, so the stronger claim happens to be true today and its stated check does not establish it. The budget counts how often a house has **died**. Different problems; the budget does not touch this
one.

**Why refused:** every form of detection needs a guessed constant, and the
rule has been attempted and wrong every time. A watchdog that fires on a
correctly-slow house is worse than no watchdog, because it converts a
performance problem into a restart loop, and the restart loop is the failure
mode this project has already paid for twice.

Reopening this is a **design decision**, not an implementation task. If you
propose one, propose the constant and say who chooses it and what happens
when it is wrong. Do not add a timeout because the code looks like it is
missing one.

## Also refused

**Nothing a house does halts the city.** Exactly two things halt it: the plan
fails validation at boot, or PID 1 dies. The `critical` flag was removed
rather than repaired — `HISTORY.md` §19.

## Orphans at shutdown — decided 2026-09-11

**PID 1 does not wait for orphans at shutdown. They die with the machine
at `reboot`.** This was "undefined — decide it explicitly rather than
letting the race pick", and this is the decision.

*Why not wait:* waiting on an orphan is unbounded by construction.
Nothing knows what a house forked or whether it will ever exit, so a
single stuck grandchild would hang the machine — which is the same
guessed-constant trap the Liveness section refuses, arriving through the
shutdown path instead. Shutdown stays bounded by the grace period.

*What this costs, stated so nobody rediscovers it as a bug:* an orphan
alive when shutdown begins is never reaped and never killed. On real
hardware `reboot(RB_POWER_OFF)` ends it. In a pid namespace the
namespace teardown does.

Measured at the boundary, three runs per rung, children dying either side
of the hold: before it, every orphan is reaped; at or after it, none are.
A sharp cutoff, not a flaky race. `test_orphans_across_restarts` pins
both halves — twelve orphans reaped across four restart cycles, and a
shutdown that closes in ~0.2s while 3-second children are still alive. A
blocking drain in `shutdown_city` turns the second half red at 3.01s.

**Reaping across restarts is no longer untested** — that entry was here
as a gap, and the fixture it lacked is `houses/orphan.c`.

**Phase 3 added a SECOND way an orphan stops existing, narrower than
either of the two above, and it fires first when it applies.**
`cg_kill_sweep()` runs after every death nw-sup reaps, not only at
final exit (docs/OPERATOR-BRIEF.md Section 3: "used on restart"), so an
orphan a house forked and abandoned is SIGKILLed the moment THAT
house's own generation dies — before the outer shutdown, and whether
or not one is even in progress. This does not change the statement two
paragraphs up: `test_orphans_across_restarts` case A still demonstrates
"PID 1 reaps a genuine orphan promptly," because either mechanism
(nw-sup's own generation-kill, or PID 1's separate catch-all `waitpid`)
ends the same child and PID 1 still counts it once it dies.

**Case B needed a different fixture, not a different property.** A
house whose own top-level process exits immediately after forking (the
shape both case A and the pre-Phase-3 case B used) now has its
children reaped by ITS OWN generation's sweep well before any outer
shutdown could matter — measured directly, naming the old fixture
(`unit-orphanslow`) here now reports `orphans=12`, not 0. So the
"still alive when shutdown starts" scenario needs a generation that
never completes a death inside the test's own window at all:
`unit-orphanhang`'s parent also ignores TERM/INT and sleeps 3s, so
nw-sup stays blocked inside `wait_house()` the whole time, and
`cg_kill_sweep` for that generation never runs. **This is what actually
demonstrates "PID 1 does not wait"**, and not in the way case A's
mechanism does: `shutdown_city()` (`pid1.c`) does not wait for that
stuck SUPERVISOR either — after `NW_GRACE_MS` it SIGKILLs
`houses[i].pid` (nw-sup itself) directly, never reaching `cg_kill_sweep`
at all, so the house and its three children all survive exactly as far
as the pid-namespace teardown at the very end. The target of that
direct kill is the supervisor, not the house or its orphans — which is
the same "does not wait" property, one layer further down the chain
than case A exercises.

## File descriptors — hard limit, named shortfall

`getrlimit(RLIMIT_NOFILE)` before the first fork. Need is
`NW_FD_RESERVED + n` after the log-pipe interleave (was `+ 2n`
when both ends were held). Compared to the HARD limit. Soft is
not the ceiling.

**The raise of soft to hard is NOT an optimisation.** What is off
the correctness path is the *decision* — refuse or accept, made
against `rlim_max` and unaffected by the raise. The raise is what
makes an accept mean anything: delete only the `setrlimit` and a
plan the check just accepted halts `report pipe` whenever
`soft < need <= hard`. Measured at n=3 soft=8 hard=20, and again
at n=6 soft=10, by two reviewers from different rungs. This file,
`blob.h` and `pid1.c` all carried the wrong version for one round;
it is the shape `CLAUDE.md` calls characteristic, and none of the
three was caught by reading.

Refusal names need, hard, and shortfall. It does not start fewer
houses than the plan named.
`test_fd_preflight_names_the_shortfall` asserts all three numbers
and pairs the refusal with an accepting run.

## The resource block: most fields enforced, since Phase 3

**`layer_bytes` moved to Hard rules on 2026-09-25. Six more —
`mem_high`, `mem_max`, `cpu_weight`, `cpu_mask`, `sched_policy`, `nice`
— moved from kind 3 to enforced-now here on 2026-09-29
(docs/OPERATOR-BRIEF.md Section 3).** `struct nw_res` is a field of
every unit; the baker refuses a malformed block and `nwcheck.c`
validates one independently for the whole struct.

**`nwsup.c` never spells `res.` at all, unlike `nwspawn.c` — say the
check the right way round.** `grep -n "res\."` finds nothing in
`nwsup.c` and 7 hits in `nwspawn.c` (`u[i].res.layer_bytes`,
`.cpu_mask`, `.mem_high`, `.mem_max`, `.cpu_weight`, `.nice`,
`.sched_policy`), because `nwspawn.c` is what reads the sealed unit's
`struct nw_res` and forwards each field as a plain-decimal `NW_*` env
var — the same convention `NW_BRICK`/`NW_LAYER` already use.
`nwsup.c` reads those six env vars with `getenv()` into local C
variables (`mem_high`, `mem_max`, `cpu_weight`, `cpu_mask`, `nice_val`,
`sched_policy`; `grep -nE "getenv\(\"NW_(MEM_HIGH|MEM_MAX|CPU_WEIGHT|
CPU_MASK|NICE|SCHED_POLICY)\"\)" nwsup.c` finds all six), never through
a `res.` struct access — the gap this section used to describe is
closed, and the checkable grep for it names the env vars, not a
substring that was never going to be in this file. `mem_high`/
`mem_max`/`cpu_weight` are applied via a per-house-GENERATION cgroup
(`memory.high`/`memory.max`/`cpu.weight`, written and kernel-read-back);
`cpu_mask`/`sched_policy`/`nice` are plain syscalls
(`sched_setaffinity`/`sched_setscheduler`/`setpriority`) in the child,
before `execv`. **Still nothing writes `io_rbps`/`io_wbps`** — neither
`nwsup.c` nor `nwspawn.c` forwards or reads either one — because io was
cut from Phase 3's own scope (docs/OPERATOR-BRIEF.md Section 1.6: "no
io, no groups, no pause"). Those two, and only those two, are still kind 3
here.

**"Not advisory" is no longer proposed; it is what
`house_cgroup_open_generation()` and the syscall block in `nwsup.c`'s
child branch both do.** A DECLARED field that cannot be applied dies
the house at boot, by name — `die("mem-high write")`,
`die("cpu mask")`, and so on for each of the six — never a log line
and a return. Scoped to what is actually declared:
`cgroup_parent_setup()`'s own `need_memory`/`need_cpu` gating means a
house declaring none of the three cgroup-backed fields never attempts
controller delegation at all, so a machine with no memory/cpu
controller is not lied to by a missing MECHANISM for a field nothing
asked for.

**CLONE_INTO_CGROUP and `cgroup.kill` need no controller delegated at
all**, unlike `memory.high`/`memory.max`/`cpu.weight` — confirmed by
running this suite, which places every house into its own cgroup
regardless of what it declares, on a machine where `memory` and `cpu`
have never been delegated. Controller delegation gates writing those
three specific files, not cgroup placement or the whole-house kill.

**A cgroup is never reused across a restart of the house, and this is
load-bearing, not tidiness.** Writing "1" to a cgroup's own
`cgroup.kill` — even on an EMPTY cgroup — SIGKILLs the next process
ever placed into that SAME directory, invisibly (neither
`cgroup.events` nor `cgroup.freeze` show it), and only destroying and
recreating the directory (even at the identical path) clears it.
Reproduced in a standalone program with no nw-sup code at all: mkdir,
clone3, exit, write "1" to the empty cgroup's `cgroup.kill`, clone3
again into the same directory — SIGKILL, 0/20 survivals across a tight
loop. So `house_cgroup_open_generation()` creates a fresh,
uniquely-named leaf (`"<name>.<generation>"`) for every fork, and
`cg_kill_sweep()` is always followed by an `rmdir` of that same
directory rather than a reuse for the next one.

**This is also why the whole-house kill (Section 3's "used on
restart") runs immediately after every death, not deferred to final
exit** — a house's own forked-and-abandoned orphan is now caught the
moment ITS OWN generation dies, not merely at the outer shutdown. See
the Orphans-at-shutdown section above for exactly what this changes
(restart-driven orphans no longer survive to the outer shutdown) and
what it does not (PID 1 still does not wait for a stuck supervisor or
a still-alive house — it SIGKILLs the supervisor directly after
`NW_GRACE_MS`, which is a different mechanism reaching the same "does
not wait" property one layer further down).

**THIS MACHINE CANNOT EXERCISE `mem_high`/`mem_max`/`cpu_weight` VIA A
REAL BOOT, and that is a refusal, not a bug.** cgroup v2's real global
hierarchy here is mounted with `hugetlb` as its only controller —
`cpu`, `memory` and `io` are on v1 hierarchies (docs/ENVIRONMENT.md) —
so a house declaring `mem-high`/`mem-max` dies at boot on this machine
specifically, by name (`cgroup memory controller unavailable`), and one
declaring `cpu-weight` names `cgroup cpu controller unavailable` — two
separate `die()` strings in `nwsup.c`, not one combined message —
because delegation is genuinely attempted and genuinely fails.
Verified directly: baking a plan with `mem-high=64M` and booting it
here produces exactly the first of those two. `cpu_mask`/`sched_policy`/`nice`
succeed everywhere, since those are plain syscalls, not cgroup files —
a measurement over cgroup controllers does not establish a claim over
the whole block, and `tools/HANDOFF-resources.md`'s table is what
distinguishes them.

`tools/HANDOFF-resources.md` still carries the fixture — the city, the
file or syscall each field decides, and the probe that decides it —
for whoever has a machine with real memory/cpu delegation. `mem_high`
must be shown NOT killing and `mem_max` must be shown killing, because
a test that reads both files back is satisfied by a supervisor that
writes them to a kernel that ignores them. No test in this suite
exercises those three on the unavailable branch, matching
`lid-landlock`'s own rule: `skip()` with a named reason rather than a
green line that means nothing.

## Known open in this territory

- **`closed … orphans=N` reports orphans REAPED, not orphans that
  existed.** A city that orphaned twelve processes which are still alive
  at shutdown closes with `orphans=0`. Internally the counter is
  `orphans_reaped` and is accurate; the label drops the verb, and an
  operator reads the line as "there were none".

  Not changed here, because it is not a one-word fix: `tests/run.py` and
  `tools/scale-probe.py` both key on the literal `orphans=0`, and the
  scale probe treats **any** orphan as a run failure — which is a
  reasonable health rule for a city of oneshot houses and wrong for a
  city whose houses fork. Renaming the field means deciding what the
  probe should consider healthy. That belongs to whoever owns `pid1.c`
  and the probe together.

  **What this does and does not put in doubt.** Every ladder run to date
  is unaffected in its *result*: the probe bakes only `unit-probe`,
  oneshot, and `grep -c fork unit_probe.c` is 0, so no ladder city has
  ever created an orphan and `orphans=0` there is true under both
  readings. What is weaker than it looked is the *check* — it has never
  distinguished the two meanings, because nothing it runs can produce an
  orphan, so the probe's health rule is unvalidated for the one case
  where the two readings diverge. That matters the moment the ladder is
  pointed at a forking house, which is what bricks phase 2 (a loop
  device per house) would do.

## Definition of done

**`make stage`, not `make`** — then `python3 tests/run.py`. The suite
runs the *staged* binaries under `/tmp/nw-init-run`; `make` alone
rebuilds the source tree and leaves the suite running yesterday's code. This
has already produced one false result, and a false pass is worse than a
failure. Then quote the actual exit codes and log lines. Never claim a change
works from reading alone.
