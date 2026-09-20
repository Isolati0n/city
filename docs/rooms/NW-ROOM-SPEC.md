# The room design — specification for implementation

Written 2026-09-16. **Not in the tree. Not scheduled.** This is the
handover document for a branch, not a proposal against `main`.

It replaces how a house is started and where its writable state lives. It
does **not** change the plan format, the bakery, `nwcheck`, the boot path
before PID 1, or the desktop.

Every claim marked **measured** was run in the QEMU guest on Ubuntu
`6.8.0-139-generic` under TCG, unprivileged unless stated. Everything else
is reasoning and says so. Where a measurement contradicted an earlier
belief, the correction is recorded rather than the conclusion alone.

---

## 1. What changes, in one paragraph

Today each house gets a supervisor process that is permanently root,
loop-mounts its own copy of the brick, builds an overlay, pivots, applies
lids, and execs — then loops on `waitpid` holding an integer it cannot
give up, because the next restart child inherits its capability set at
`fork`.

Under this design: PID 1 mounts each **distinct** seal once at boot and
builds one **launch-pad** mount namespace containing only the rooms
filesystem. Every **visit** is born in that pad, enters a user namespace
mapped to its own unprivileged uid, pulls the seal in by descriptor,
builds its overlay on a **room** that is a btrfs subvolume, pivots,
applies Landlock and seccomp to itself, and execs. **Everything after the
unshare is unprivileged.** There is no per-house supervisor.

---

## 2. The three nouns

The plan today names one thing. This names three, and the separation is
the design.

**Seal** — the content-addressed read-only image. Mounted once, at boot,
by PID 1. Shared by every house that uses it.

**Room** — a btrfs subvolume that **is** the writable state. Not an
overlay upper directory that happens to persist; the durable object the
operator names, snapshots and rolls back.

**Visit** — a process that projects seal and room into a root and runs. A
visit is disposable. Killing it does not kill the house.

A workspace on the canvas is a binding of `(seal, room)`, not a pid.
Restart is a new visit into the existing room.

---

## 3. Boot sequence, PID 1

Privileged. This is the whole privileged surface of the system, plus
`reboot`.

1. For each **distinct** seal in the plan: attach a loop device, mount it
   `MS_RDONLY | MS_NODEV`. One loop device per distinct seal, **not** per
   house.
2. Mount the rooms btrfs with `user_subvol_rm_allowed`.
3. For each binding used for the first time, create its room as a
   subvolume, `chown` it to that house's outer uid, `chmod 0700`.
4. Build the **launch-pad**: a fresh mount namespace containing only the
   seals and the rooms filesystem, pivoted into. Every visit is forked
   from here.
5. Reap forever. Write the boot record.

**Measured.** Six houses started concurrently against one shared seal
mount, zero failures, one loop device. The recorded per-house behaviour
is `at 8 houses several never ran, and at NW_MAX_UNITS most never
attached` (`nwsup.c:118`). Sharing removes the contention rather than
racing better.

**Measured, the launch-pad.** From inside the pad: `/nw` gone, `/efi`
gone, the machine root gone, one entry at the root — `rooms`. A visit
forked there sees the same.

```
entries at the pad root: 1 [rooms]
a visit born in the pad -- /nw:gone /efi:gone
```

**This is the correction that matters.** An earlier version of this design
accepted a "pre-pivot window" in which a house could see the machine root,
and argued it was harmless because of the uid mapping. It was not
harmless — see §7 — and it was not necessary. It was an artifact of where
the unshare happened. The pad deletes it. *Do not keep a window and then
argue about it.*

---

## 4. Visit startup, unprivileged

Forked from the pad. In order:

1. `setuid` to this house's outer uid. **Then `prctl(PR_SET_DUMPABLE, 1)`**
   — see the trap in §7.
2. `unshare(CLONE_NEWUSER | CLONE_NEWNS)`; write `setgroups deny`, then
   `uid_map` and `gid_map` as `0 → house_outer 1`.
3. `mount(MS_REC | MS_PRIVATE)` on `/`.
4. tmpfs stage.
5. `open_tree(seal, OPEN_TREE_CLONE | AT_RECURSIVE)` then `move_mount`
   into the stage. The room arrives the same way, **by descriptor**.
6. Pivot into the stage.
7. Overlay: `lowerdir` = the moved seal, `upperdir` = the room,
   `workdir` = a **sibling** of the room on the same filesystem.
8. Binds, Landlock, seccomp — all self-applied.
9. `exec`.

**Measured, step 5.** `open_tree(OPEN_TREE_CLONE)` on a mount created
*outside* the user namespace, by PID 1, succeeds from inside it, and
`move_mount` attaches it. This was the crux: if a visit could not pull
PID 1's seal in after the unshare, the seal would have to be in the copied
table and the design loses its point.

**Measured, step 7.** The workdir must be a sibling. An earlier attempt
put it inside the subvolume used as `upperdir` and overlayfs returned
`EINVAL`. That was a setup error, not a limit.

**Measured, steps 8–9.** Landlock returns `EACCES` on a write its ruleset
does not permit, and seccomp returns `EPERM` on a filtered syscall, both
inside the user namespace, in every house. This was the other thing that
could have killed the design — if a house cannot confine itself, something
privileged must do it.

**Measured, the seal holds.** A write through the overlay leaves the lower
intact; a direct write to the shared seal is `EROFS`; `mount` and `mknod`
from inside are both `EPERM`.

**Measured, what does NOT work.** `mount("/proc/self/fd/N", dir, MS_BIND)`
returns `EINVAL` for an `O_PATH` descriptor, while plain-path binds inside
the same userns work. Descriptors reach the visit through
`open_tree`/`move_mount`, never as bind sources.

---

## 5. Commit — the protocol, and it is not a timer

**Freeze the payload, `syncfs` the room, snapshot, thaw.** In that order.

Snapshot is `BTRFS_IOC_SNAP_CREATE_V2` on a descriptor to the **raw**
subvolume, not to the overlay. Overlay descriptors are overlay inodes. PID
1 of the visit holds that descriptor, opened before the overlay covers it,
and **must not leak it to the payload** — with `user_subvol_rm_allowed` a
payload holding it could delete its own history.

**Measured.** Snapshot creation is `inode_owner_or_capable` on the source
subvolume, not `CAP_SYS_ADMIN`. A house owning its room can snapshot it
from inside its own user namespace, with the descriptor opened inside or
inherited from outside. Both work. It also works while the room is the
live `upperdir` of a running overlay, and a write made through the overlay
is present in the snapshot.

**Measured.** With `user_subvol_rm_allowed`, an unprivileged house can
delete its own snapshot. Without it a house can create snapshots it cannot
remove and the volume fills. The mount option is a machine property and
does not travel in the plan.

**Measured, the snapshot is a commit.** After snapshotting, a write to the
live room does not appear in the snapshot, and the prior file does. **A
commit name and its data are the same object.** This is the direct answer
to the failure measured earlier: overlay-plus-marker produced a marker
claiming generation 36101 with data ending at 34194.

### When to commit

```
on canvas hide:
    freeze (or already frozen); wait for cgroup.events frozen 1
    syncfs(room_fd)
    SNAP_CREATE_V2
    stay frozen

on canvas show:
    thaw

on explicit "save this" while shown:
    freeze, wait, syncfs, SNAP_CREATE_V2, thaw
```

**Not on an interval while a visit is shown.** Measured: six concurrent
snapshots on one btrfs all succeeded, but the ioctl serialises — 379 ms,
then 142, 92, 96, 68, 60. A 379 ms freeze of a game or the compositor is a
dropped-frame burst. The number is TCG and loose; the *shape* is a
transaction queue and will not go away with KVM. The standing rule already
names the moment when a stall is free: the operator looked away.

So "five minutes" means *a workspace you have looked away from has a
snapshot*, not *every running program is snapshotted every five minutes*.

### What freeze does and does not buy

**Measured.** `cgroup.freeze` stops every task in the cgroup including
children forked after it joined (3 of 3), a `SIGCONT` does not wake it, it
resumes exactly where it stopped, and `cgroup.events` reports `frozen 1` /
`frozen 0` so the state is readable rather than inferred. Memory is
retained: `memory.current` was 24.3 MB running and 24.3 MB frozen.

**Measured, and this is the trap.** Freezing does **not** make the room
durable. Power cut immediately after a freeze: the marker claimed
generation 5129 and the data file was **empty**. `syncfs` *then* freeze
still lost four generations, because the payload kept running during the
~300 ms the freeze took. **Freeze first, then sync.** The intuitive order
is the broken one.

Freeze buys exactly two things: no new `write()` enters the kernel during
the snapshot, and no race between the sync and the ioctl. It does not
finish a write sitting in libc, does not make `write(tmp); rename(tmp,
dest)` atomic, and cannot undo an `O_TRUNC` that already emptied a file.

### The published cost

**Measured.** Of 240 files inside snapshots taken while six houses wrote
continuously, **one was incomplete** — created `O_TRUNC` and caught before
being rewritten. That is an application-consistency failure, not a
filesystem one, and freezing cannot fix it.

State the guarantee precisely and do not overstate it: **a name in the
rollback list cannot refer to a tree that does not contain the bytes
`syncfs` had when the ioctl returned.** It can still be a consistent tree
of inconsistent application files.

---

## 6. Rollback

Snapshots accumulate as a list. **The operator picks. Never automatic.**

A mutable file may say *which of the permitted snapshots is current*. It
may never say *that a new one is now policy*.

Cap the retained count in the plan. Deletion needs
`user_subvol_rm_allowed`, which is therefore not optional.

---

## 7. Identity and ownership

**Per-house unprivileged outer uids. Keep them.** The reason changed but
the answer did not.

They are no longer what makes seeing `/boot` harmless — the pad deleted
that. They are what keeps `/rooms` a directory of *names* rather than
*writable foreign state*: a visit with a shared outer uid can open a
sibling room and write it, and satisfies `inode_owner_or_capable` on it,
so it could snapshot or delete a sibling's rollback list.

**Two tightenings, in order.** Do not give the visit a *path* to `/rooms`;
hand it a descriptor to its own room, the same way the seal arrives. And
keep the per-house uid anyway, as the owner the snapshot ioctl checks and
the second lock if a descriptor leaks or Landlock is applied late.

**Measured, the severity that justified this.** With a shared `0 → 0` map,
during the old pre-pivot window a house could **write another house's
layer** and **append to the boot record**. Both succeeded. With per-house
uids both are refused with `EACCES` and the victim file and record are
verifiably unchanged.

**Measured, the trap that will cost someone an afternoon.** After
`setuid()` without an `exec`, `/proc/self/*` remain owned by the previous
uid and become unwritable, so the `uid_map` write fails **silently** and
the process lands on the overflow uid 65534. Overlay then fails `EACCES`
and the honest reading is *houses cannot run unprivileged*.
`prctl(PR_SET_DUMPABLE, 1)` after the `setuid` fixes it.

**Known and unresolved.** A seal mounted by PID 1 as uid 0 looks like the
overflow uid inside a `0 → house_outer` map. Acceptable while the seal is
world-readable. An idmapped clone stamped by PID 1 per uid is optional
polish, not required.

**Measured, and worth knowing.** A file a house writes to its room reports
uid 0 inside the namespace and the outer uid outside. Anything reading
rooms from outside — a backup, the fold tool, the operator at a console —
sees the outer ids.

---

## 8. What this deletes

- `nwsup.c` entirely. The privileged half is gone because the house
  confines itself; the restart half is a new visit.
- `nwspawn.c` and the double fork.
- One loop device and one erofs mount per house.
- The pre-pivot window, as a category.
- Overlay-as-state-store, and every commit marker beside it.
- `cgroup.freeze` as a durability primitive. It stays as the canvas pause.

**Explicitly not deleted:** the startup path's shape, the plan format, the
bakery, `nwcheck`, the boot path before PID 1.

---

## 9. Costs, at full strength

**One btrfs for every room.** An `ENOSPC`, a corrupted tree or a btrfs bug
takes every house's writable state at once. Overlay-on-ext4 spread that
risk differently, with a worse consistency story. This is a real trade and
the machine owns it.

**Torn application writes.** §5. One in 240 under hammering. Not fixable
by this design or any other without the application's cooperation.

**Restart rebuilds the projection.** The room is durable; the overlay,
Landlock rules and seccomp filter are not. They are functions of the plan,
so this is acceptable. Do **not** pin every visit's mount namespace to
avoid it — pinning is how per-house supervision gets accidentally
recreated.

**Snapshot clutter.** Every commit is a subvolume. Cap it.

**PID 1 is still permanently root.** The floor is: mount each distinct
seal once, create a room on first use, `reboot`. `erofs` is not
`FS_USERNS_MOUNT`, measured — `EPERM` in a user namespace while `tmpfs`
and `overlay` both mount — which is why the floor is not zero.

---

## 10. Open, and honestly

**The VM-house freeze, and this one has a user-visible consequence.**
cgroup v2 freeze is signal-path; tasks in D-state and some KVM worker
threads can leave `cgroup.events` at `frozen 0`. A district may never
report frozen, which under §5 means it never gets a snapshot. **Could not
be tested here — no `/dev/kvm` and no virtualisation flags in
`/proc/cpuinfo`.** The protocol must poll with a timeout, skip the
snapshot if `frozen` never reaches 1, leave the previous snapshot current,
and never block the canvas on it. That is a named check, not a hope.

**`OPEN_TREE_NAMESPACE` is closed as a target.** It was the 7.0 answer to
the window; the pad solved it on 6.8. Do not carry an upgrade dependency
for a solved problem, and do not maintain two startup paths.

**Untested:** concurrent *first inhabitation* of many houses; behaviour
when the rooms volume is near full; whether the pad itself can leak if
built wrong.

---

## 11. Exit condition for the branch

**It merges when** a house starts unprivileged from the pad on the boot
path, with its room as a btrfs subvolume, commit-on-hide working, and the
suite green.

**It dies if** the ownership or idmap questions turn out to break
something the current design handles, or if the single-btrfs blast radius
is judged worse than the state it replaces.

A branch with no answer to *what makes this merge or die* becomes a
graveyard.

## 12. What none of this establishes

Every measurement is from one machine, in QEMU, under TCG with no KVM, on
Ubuntu `6.8.0-139-generic`. Nothing here has run on real hardware. The
design has never been built; it has only been tested in pieces.
