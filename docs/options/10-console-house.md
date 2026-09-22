# 10 — The console house

Status: **resolved and built.** This note's first conclusion — no new
plan field, no TCB change — was reviewed clean on paper and then
falsified by actually booting the design under QEMU: a shared seccomp
filter widened by ten syscalls was the wrong mechanism. The operator's
correction is in **"The option the note missed: lids are per-house,"**
the final section — declare the console house without the seccomp lid
at all, since lids are independent per-unit bits, not a single choice
for the whole machine. That correction is what shipped; the "What
actually happened when this was built" section and the "Decision" above
it are both left standing, unedited, because a wrong conclusion deleted
without saying so is how the same mistake gets remade. Everything else
in the note — the userland choice, the bind mechanism for the tty, the
exit semantics, the visibility scope, the wrapper's fd-rewiring design —
held up under building and reviewing it from the start.

## Goal

A booted city with a shell reachable on a terminal, for live exploration —
"a dead compositor isn't a dead machine." The shell must be an **ordinary
house**: its own brick, its own budget, independent of every other house.
Nothing about it may need PID 1, `nw-spawn`, or `nw-sup` to learn a new
concept. If it does, this note stops here and goes to the operator as a
`docs/options` proposal rather than as work.

## Userland: what's obtainable, measured on this box

`command -v busybox` finds nothing; there is no free-standing binary. Two
packages provide one:

```
$ dpkg -l | grep busybox
ii  busybox-initramfs   1:1.36.1-6ubuntu3.1   amd64  Standalone shell setup for initramfs
$ dpkg -L busybox-initramfs | grep bin/busybox
/usr/lib/initramfs-tools/bin/busybox
$ file /usr/lib/initramfs-tools/bin/busybox
/usr/lib/initramfs-tools/bin/busybox: ELF 64-bit LSB pie executable, x86-64,
  ... dynamically linked, interpreter /lib64/ld-linux-x86-64.so.2 ...
$ ldd /usr/lib/initramfs-tools/bin/busybox
	linux-vdso.so.1 (...)
	libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (...)
	/lib64/ld-linux-x86-64.so.2 (...)
```

`busybox-initramfs`'s copy is dynamically linked. `harness.md`'s
*the harness is more capable than the machine* already documents the
consequence of a dynamic binary in a fixture — a missing interpreter is
`ENOENT` on `execve` and looks like a missing binary — and the existing
`dawn-real-boot` fixture works around it by parsing `ldd` and copying the
`.so` files in. That trick would work here too, but a second package avoids
it:

```
$ apt-get install --dry-run busybox-static
Inst busybox-static (1:1.36.1-6ubuntu3.1 Ubuntu:24.04/noble-updates, ...)
Conf busybox-static (1:1.36.1-6ubuntu3.1 Ubuntu:24.04/noble-updates, ...)
```

`busybox-static` is in the box's apt sources and installable (not installed
as of this note — nothing has been changed yet). It ships a single
statically-linked binary, no loader, no `.so` dependencies to enumerate or
copy. **Recommendation: `busybox-static`,** for the same reason
`harness.md` records that the image-build script builds static for real
hardware — one fewer thing for the brick to carry and one fewer thing that
can go missing from it.

### Digest pinning — no new mechanism

`bakery/mkbrick.py` already hashes the **entire packed tree's image file**
with sha256 and names the output by that hash (`sha256_file()` in
`mkbrick.py`; the reasoning for the exact `mkfs.erofs` flag set, so two
packs of identical content produce identical bytes, is in
`docs/options/08` and repeated in the flag comments). Putting the console
house's userland — the busybox binary, plus a wrapper described below —
into the tree `mkbrick.py` packs means it is pinned by that same digest,
the same way every other brick's content is. **No new field carries a
digest for "this house's userland" specifically; the brick's existing hash
already covers it**, because the brick *is* the tree.

### Why exec_path cannot simply be busybox

`nwsup.c`'s house-exec call is:

```
char *av[] = { (char *)name, NULL };
execv(path, av);
```

`av[0]` is always the plan's declared house **name**, never `argv[1]`,
never a value the plan can otherwise set. Busybox's multi-call dispatch
looks at `argv[0]`'s basename to choose an applet (`sh`, `ash`, ...). So
`exec_path=/bin/busybox` only reaches a shell if the house's plan *name* is
literally `sh` — fragile, and it silently breaks if anyone renames the
house. The wrapper below removes this constraint by choosing its own
`argv[0]` when it execs busybox, so the house can be named anything
(`console`, in the example plan line further down).

## Terminal: which device, and how it becomes visible

**QEMU (buildable and testable in this container today):** the kernel
command line in `tools/mkboot.sh` already carries `console=ttyS0,115200n8`,
and `-serial file:"$OUT/console.log"` is the only serial device configured.
That device is the *kernel's* console — sharing it with an interactive
house would make it impossible to prove which process's output is on the
line, which the test design below treats as disqualifying. **A second
serial device, `ttyS1`, dedicated to the console house and never touched by
the kernel or by any other house, is a `tools/mkboot.sh` change** — add a
second `-serial` line to the `qemu-system-x86_64` invocation. That is a
harness/build-tooling file, not TCB, and not a plan-format change.

**Real hardware:** `tty1` (the VT the kernel already attaches its own
console to, or a dedicated one if the kernel command line moves the console
elsewhere) is the analogous device, and it is *not* addressed by this note.
Nothing in this tree boots on real hardware yet — `docs/ENVIRONMENT.md`
records no bootloader boot, no real hardware, on this box — and a VT
introduces the exact controlling-terminal question the next section works
through for the serial case, with a different device driver and a
different set of assumptions to re-measure. Naming it here as future work
rather than silently assuming the serial answer transfers.

### Getting the device node into the brick's view: the existing `bind=` mechanism, no new field

A unit already declares `bind=<path>`, and `nwsup.c`'s bind loop is:

```
int n = snprintf(tgt, sizeof tgt, "%s%s", NW_BRICK_MNT, binds[i]);
...
if (mount(binds[i], tgt, NULL, MS_BIND | MS_REC, NULL) < 0) die("bind");
```

`binds[i]` is used as **both** the host-side source and, appended to
`NW_BRICK_MNT`, the in-brick target — literally the same path inside and
out, which `plan.md`'s hard rules already state as the shape a bind takes.
So `bind=/dev/ttyS1` needs nothing new: it bind-mounts the host's
`/dev/ttyS1` (created by devtmpfs before any house's mount namespace is
unshared — the same precondition `harness.md`'s *what else the harness
provides for free* names for `/dev/vda`) onto `/dev/ttyS1` **inside the
brick**, provided the brick's own tree contains a placeholder at that exact
path. `plan.md`'s hard rule "never `mkdir` into a brick" is about `nw-sup`
at runtime; `mkbrick.py` building the placeholder file (and its parent
directory) into the tree at bake time is the ordinary, already-established
way every other bind target gets created (the suite's own `bind=/etc`
fixture works the same way).

**This is the whole answer to "does the plan format need to say anything
new for the console house to reach a terminal."** It does not. `bind=`
already exists and already does exactly this.

## Whether this needs a TCB change: session and job control

This is the part that could have forced a TCB change, so it gets the
longest treatment. `pid1.c` (lines 271–283) already carries a precondition
comment about exactly this scenario:

> PRECONDITION, because a console is a plausible next change and the suite
> can never see it: this is safe while PID 1 has no controlling terminal.
> Give it one and two things flip at once. A `^C` becomes a
> kernel-generated group signal ... and the loggers ... become subject to
> `SIGTTOU` ... If a console lands, block SIGTTOU/SIGTTIN here.

Nothing in this tree calls `setsid()` anywhere: `grep -rn setsid *.c` in
the repository root returns no hits at all — not even inside the
PRECONDITION comment above, which discusses controlling terminals and
`SIGTTOU`/`SIGTTIN` without ever using that literal name. Every
process descended from PID 1 — `nw-spawn`, `nw-sup`, every house —
therefore shares PID 1's session, and PID 1 is not the leader of a session
with no controlling terminal *unless something in that session opens a tty
without `O_NOCTTY`*. If the console house's shell did that directly, it
would make **PID 1's session** acquire a controlling terminal — exactly the
flip the comment warns about, and exactly the TCB change (blocking
`SIGTTOU`/`SIGTTIN` in the logger, per that comment) this note would then
have to hand to Grok as a `pid1.c` brief.

**The design below avoids it instead of triggering it.** Nothing in this
design calls `setsid()`, `ioctl()`, or attempts to become a session leader
at all. Checked against the seccomp allow-list in `lids.c`
(`strict_allow[]`): `setsid` and `ioctl` are **not in it**, nor is
`dup2`/`dup3`. So a design that needed any of those would *also* need
"adding a syscall to the allow-list requires naming the unit that needs it
and why" (`runtime.md`), on the one shared table every seccomp house uses —
a second, independent reason to avoid it if a design that doesn't need it
exists. One does:

- The console house's `exec_path` is not busybox directly. It is a small
  non-TCB wrapper, compiled statically and packed into the same brick,
  whose entire job is: open the bound tty path (already visible via
  `bind=`, as above), then `close(0); openat(tty)` (landing on fd 0 because
  it is the lowest free descriptor), and the same for 1 and 2, then
  `execve("/bin/busybox", (char *[]){"sh", NULL}, environ)`.
- Every syscall that needs is already in `strict_allow[]`: `close`,
  `openat`, `execve`. No allow-list change.
- Nothing here calls `setsid()` or opens the tty as a session leader with
  no controlling terminal, so PID 1's session is untouched and the
  precondition comment's "this is safe while PID 1 has no controlling
  terminal" continues to hold. **No `pid1.c` change, and no brief for
  Grok.**
- The cost, stated rather than hidden: no job control. `^C`/`^Z` do
  nothing special (there is no controlling terminal to generate the
  signal), and busybox `ash` runs in whatever degraded-but-functional mode
  it uses when `isatty()` is true but there is no process-group-based job
  control to hook into. That is a real trade against "a full login
  session," made explicitly rather than discovered later, and is
  consistent with "live exploration," not a login-manager replacement.

**Open, not yet measured**, and named rather than assumed before the build:
whether `ash` refuses to run interactively at all without a controlling
terminal, and whether three independent `openat()`s of the same UART
(rather than three `dup()`s of one) behave correctly under QEMU's 16550
emulation. Both are cheap to check by booting once with the wrapper before
writing the test's assertions around it — this note does not claim they
are fine, only that they are the two things the build phase must check
first.

## Exit semantics: the existing kind/exit0 mechanism, unchanged

`blob.h`:

```
#define NW_KIND_ONESHOT  0u   /* exit 0 completes; never restarted */
#define NW_KIND_LONGRUN  1u   /* any exit is unexpected, incl. 0 */
```

and `test_kind_exit0` in `tests/run.py` already pins exactly the behaviour
wanted: a `kind=oneshot` house that exits 0 is done, not restarted, and
does not spend its budget. **`kind=oneshot` is the mechanism. Logout is the
shell process exiting 0, which is not a death** — no new exit-status
convention, no signal, nothing PID 1 or `nw-sup` needs to distinguish that
they do not already distinguish for every other oneshot house.

The alternative, `kind=longrun`, would restart the shell after every
logout, budget permitting — but every exit (0 included) counts against the
hard total (invariant 4), so each logout would spend one of a finite number
of sessions before the house stays down for the rest of the boot. That is
a real, different design ("the console keeps coming back" vs. "the console
is done when you log out"), and this note recommends `oneshot` because the
operator's own framing — "a normal exit, not a budget-spending death" —
names the property `oneshot` already has and `longrun` does not.

## Visibility and write scope

Exactly what any brick house gets, nothing else:

- **The sealed brick** (busybox-static + the wrapper), read-only, pivoted
  into per `lid_brick()`.
- **One writable layer**, keyed by a declared id, for anything the shell
  writes beneath `/` — e.g. a shell history file, if one is wanted; nothing
  requires it.
- **The one declared bind**, `/dev/ttyS1`, visible at the same path inside
  and out, opened by the house's own wrapper — not handed in, per
  invariant 5.
- **`lids=newns,landlock,seccomp`.** `newns` is mandatory once `brick=` is
  set (`NW_E_BRICKNS` otherwise); `landlock` and `seccomp` are not
  mandatory but there is no reason to omit them — the wrapper and busybox
  need nothing outside `strict_allow[]` or outside what Landlock already
  grants a brick house (read+write+execute beneath its own root, plus `rw`
  in the declared bind).

No new descriptor, no new lid bit, no new field.

## Mechanism-rule table

| field | mechanism | new? |
|---|---|---|
| `exec_path` | names the wrapper inside the brick | existing |
| `brick=` / `layer=` | `lid_brick()`, unchanged | existing |
| `bind=/dev/ttyS1` | `nwsup.c` bind loop, unchanged | existing |
| `kind=oneshot` | `nw-sup`'s exit-status handling, unchanged | existing |
| `lids=newns,landlock,seccomp` | `lid_newns`/`lid_landlock`/seccomp, unchanged | existing |
| fd 0/1/2 → tty | the wrapper's own `close`+`openat`, in the house's own code | not TCB, not a field |

Every row is a mechanism this tree already has. Nothing is added to
`struct nw_unit`, `nwcheck.c`, the baker's cross-field rules, or either
spec. Per the mechanism rule's clause 5, there is no descriptive-metadata
question here either — everything above is either an existing field used
as designed, or code that is not the TCB's concern at all.

## Example plan line

```
house console /bin/wrap kind=oneshot budget=3 \
  lids=newns,landlock,seccomp brick=<64-hex> layer=console bind=/dev/ttyS1
```

(`exec_path` is `/bin/wrap`, an absolute path resolved *inside* the brick
after the pivot — a plain in-brick path with no brick-identifying prefix,
the same shape every brick fixture plan line in `tests/run.py` already
uses, e.g. `house one /bin/brick kind=oneshot lids=newns,seccomp
brick={two} layer=l-two`.)

## Test design

**Buildable and runnable in this container**, not something to hand to the
operator — measured today, not assumed:

```
$ time sh tools/mkboot.sh --run --check
...
[dawn] mounted /dev
[dawn] mounted /sysroot
[dawn] mounted /sysroot/efi
[dawn] pivot_root unavailable, MS_MOVE
[dawn] pivoted MS_MOVE
...
[nw-root] city open houses=4 slot=/efi/slots/A
[nw-root] house exit alpha status=0
[nw-root] house exit beta status=0
[nw-root] house exit gamma status=0
[nw-root] house exit delta status=0
== check: city open, stayed up, no panic, no HALT ==

real	0m56.820s
```

`qemu-system-x86_64` is installed, `/boot/vmlinuz` exists, `/dev/loop0`
exists, and the process is root — no KVM (`/dev/kvm` does not exist, so
`ACCEL=tcg`), which is why this takes closer to a minute than a few
seconds, but it completes, boots the real slot/plan-blob path (not a
command-line-injected plan), and exercises the `MS_MOVE` pivot fallback
`harness.md` records as having no lab coverage. This is evidence for
*this* note's purposes only — whether a QEMU-based console test is
buildable here — and is not a correction to `docs/ENVIRONMENT.md`, which
is scoped to what `make test`'s own harness exercises; that is a separate
question from what this container's tools can do when invoked by hand, and
is out of scope for this round.

**The test, once built:**

1. Bake a plan with the console house (as above) alongside the default
   probe houses, on `ttyS1`.
2. Extend `tools/mkboot.sh` with a second serial backend on `ttyS1` that a
   test can both write to and read from — `-serial file:` is write-only
   from the guest's side and cannot inject a command; a `-chardev
   socket,...  -serial chardev:consock` (or a pty backend) is needed so the
   test process can send bytes in and read the echo back. This is the one
   piece of harness tooling this design does need to add.
3. Boot, wait for `city open`, write a distinguishing command string (e.g.
   `echo NWCONSOLE-<random>`) into the `ttyS1` side, and read back the
   same string from the same channel.
4. **The control, per `harness.md`'s "a test you add must be shown failing
   when the thing it tests is removed":** re-run with the console house
   removed from the plan (or its `bind=` pointed at a path that does not
   exist, so the wrapper's own open fails and it exits nonzero
   immediately). The test must go red **for that reason** — no response on
   `ttyS1` — and not for an unrelated one; the assertion needs a paired
   positive (the string arrives) precisely because "nothing arrived" would
   otherwise also be satisfied by the whole boot never reaching the
   console house at all, which is the unpaired-absence shape `harness.md`
   already names.

**Why `ttyS1` and not `ttyS0` settles the provenance question directly,**
without needing to parse or disambiguate interleaved output: the kernel
console and every other house's log line go to `ttyS0` only; nothing else
in this design ever writes to `ttyS1`. Any byte arriving there came from
the wrapper or from busybox running inside the console house's brick, by
construction, not by inference from a prefix or a timing argument.

## Decision

No new plan field. No TCB change — the wrapper avoids `setsid`/`ioctl`
entirely, so `pid1.c`'s controlling-terminal precondition is not
triggered, and no allow-list addition is needed since every syscall the
wrapper uses is already in `strict_allow[]`. Per the stated decision rule,
this proceeds to a build this round, in the two-commit shape, once this
note has a `claims` review — pending confirmation that the two unmeasured
assumptions above (interactive `ash` with no controlling terminal;
independent opens of one UART) hold when actually booted.

## What actually happened when this was built

The two unmeasured assumptions above both held: a standalone pty test
(not QEMU) showed the wrapper's fd rewiring works and `ash` runs
interactively with no controlling terminal, exactly reporting "can't
access tty; job control turned off" as predicted. **The "no TCB change"
conclusion did not hold**, and the paragraph above is left standing,
uncorrected in place, because the mistake it makes is worth seeing rather
than editing away.

Booting the assembled brick under QEMU killed busybox with `SIGSYS` on
`prctl` — a syscall not in `strict_allow[]`, called by busybox
unconditionally at startup (confirmed with `strace`). Adding `prctl`
alone was not enough: a `tcb-review` dispatched on the change rebuilt it
in a scratch copy and, reading the killed syscall number off the kernel's
own audit line each time a further one was needed, found the actual gap
is **ten** syscalls before an interactive `busybox sh` over a real tty
will run at all — `prctl`, `getuid`, `rt_sigaction`, `getppid`, `uname`,
`ioctl`, `geteuid`, `getpgrp`, `poll`, `setpgid`. Every addition through
all ten, in that scratch copy, made `tools/console-boot-test.py` pass
end to end (the token sent over `ttyS1` came back, and the no-bind
control stayed silent) — so the design's mechanism (the bind, the
wrapper, `kind=oneshot`, the dedicated serial line) is sound; only the
"no TCB change" premise was wrong.

**`ioctl` is exactly what this note's own design section said was being
avoided**, and for the reason stated there: it is not in `strict_allow[]`
and the filter has no per-argument inspection, so granting it grants the
*entire* `ioctl(2)` surface to every seccomp house, not a job-control
subset. That is a materially larger, more consequential widening than
adding `prctl` alone — which is what the build actually tried first,
during this same round, on the evidence of one non-interactive `strace`
run; that attempt is not itself written down earlier in this document,
so "originally anticipated" would overstate what this note's own prior
text commits to. On its own `prctl` would not have been enough anyway.
Deciding whether the full widening is acceptable — or redesigning the
wrapper to avoid needing an interactive `ash` at all, trading away more
of "a normal shell" to keep the syscall surface where it is — is a
design decision this note does not get to make by itself, per the same
decision rule that authorized the build in the first place: **this
needs the operator.**

Why the first attempt's measurement missed the rest: the command run
during that attempt was `strace -f -e trace=prctl /bin/busybox sh -c
"echo hi"`. That flag traces only `prctl` by construction, and `-c` runs
`ash` non-interactively, which is precisely the path that skips the
job-control-adjacent syscalls (`ioctl`, `setpgid`, `getpgrp`, `poll`) an
interactive shell attached to a real tty takes. The command's output was
accurate and never capable of showing the thing that mattered — this
project's own "the evidence is real and it is about something else,"
reproduced in the writing of this very note.

**One fix survives independently of this and is landing on its own,
regardless of what the operator decides above:** booting also found that
`nwsup.c`'s Landlock rule application (`ll_beneath()`) died with
`FAIL landlock rule errno=22` on the console house's `bind=/dev/ttyS1`,
because the kernel refuses directory-only Landlock rights (`READ_DIR`,
every `MAKE_*`/`REMOVE_*`) on a bind target that is not a directory, and
every bind `nwsup.c`'s Landlock code path was ever asked to open before
this one was a directory (`test_landlock_confines`'s `bind={shared}` is
the only one that reaches it; `tests/run.py` also has a `bind=/etc/hosts`
that is never booted and so never reaches this code, which is why the
claim is scoped to what the function was asked to open, not to every
`bind=` line in the suite). That is a latent bug in the existing bind
mechanism, not
new mechanism this feature introduces, and it is fixed and reviewed
(`tcb-review`, `fd-auditor`) independently of whether the console house
itself ever ships.

The wrapper (`houses/console-wrap.c`), the boot test
(`tools/console-boot-test.py`), and `tools/mkboot.sh`'s `NW_CITY`/
`NW_EXTRA_BRICKS` hooks are pushed to `wip/console-house`, not `main`,
since the feature they support does not work yet and CLAUDE.md's own
rule is no half-finished implementations on the trunk.

## The option the note missed: lids are per-house

The operator's correction, and it is a correction to how this note
*thought*, not only to what it concluded. `lid_landlock`, `lid_newns`,
`lid_netns` and seccomp are four independent bits in one `lids=` byte
(`bakery/nw-cc.py`'s `"seccomp"`/`"landlock"`/`"newns"`/`"newnet"` map;
`blob.h`'s `NW_LID_*`), and the suite already has houses with no seccomp
lid at all. The note above reached for the one shared seccomp filter as
if it were the only knob a house has, found it too narrow, and stopped —
without asking whether seccomp was the right lid to declare on this
house in the first place.

**Declare the console house `lids=newns,landlock,newnet`. No seccomp
lid.** That is the whole fix. `lids.c` is untouched; no other house's
filter widens; the ten-syscall gap stops being a question because
nothing is filtering syscalls for this house at all.

### The cost, stated rather than hidden

This house runs as uid 0 with the full syscall surface — every syscall
the kernel has, not a `strict_allow[]` subset — confined by exactly
three things: its own mount namespace (`NW_LID_NEWNS`), Landlock, and a
network namespace with no interfaces (`NW_LID_NEWNET`). `CLAUDE.md`
already records, in prose, that such a house can `mknod`, `mount` and
`unshare` inside its own namespace. The operator's instruction was not
to take that on faith: **measure what it can actually do.**

### Measured, not argued

A first attempt at this measurement sent `mknod`/`mount`/`unshare` as
shell commands over the interactive console and got `sh: mount: not
found` / `sh: unshare: not found` — which measured something real (ash's
standalone-shell dispatch for those two applets needs something under
`/proc`, and a house's own pivoted root never mounts `/proc` at all) and
was not an answer to the question asked. A missing dependency and a
security refusal produce the same shell text, and this project's own
"the evidence is real and it is about something else" is exactly this
shape.

`houses/console-escape-probe.c` (in the tree, not shipped in the real
console house's brick — a measurement tool for this investigation only)
calls the syscalls directly and prints each one's own errno, so there is
no shell, no `/proc` dependency, and no ambiguity between them. Booted
under the same `lids=newns,landlock,newnet`, no seccomp:

```
PROBE mknod_char rc=-1 errno=13(Permission denied)
PROBE mknod_block rc=-1 errno=13(Permission denied)
PROBE mkdir_root rc=-1 errno=13(Permission denied)
PROBE mount_tmpfs rc=-1 errno=1(Operation not permitted)
PROBE mount_bind_root rc=-1 errno=1(Operation not permitted)
PROBE mknod_char_in_layer rc=-1 errno=13(Permission denied)
PROBE mknod_block_in_layer rc=-1 errno=13(Permission denied)
PROBE unshare_newns rc=0 errno=0(-)
PROBE pivot_root_noop rc=-1 errno=1(Operation not permitted)
```

Read plainly: it cannot create a device node (char or block) or a new
directory at its own root (`EACCES` — Landlock's `MAKE_CHAR`/
`MAKE_BLOCK`/`MAKE_DIR` withheld, exactly as invariant 6 already says).
It cannot mount a tmpfs, bind-mount `/` over anything, or `pivot_root`
(`EPERM` — Landlock's blanket refusal of the mount family for any
confined domain, independent of the ruleset's specific rights, which is
what `CLAUDE.md` invariant 6 means by "matters for a house with no
seccomp"). It **can** call `unshare(CLONE_NEWNS)` successfully —
harmless on its own: that call only gives the process its own copy of
the mount table, and a Landlock domain is attached to the calling
task's credentials rather than to a mount namespace, so `CLONE_NEWNS`
does not touch it.

**`mknod_char_in_layer`/`mknod_block_in_layer` are the same two calls,
run again inside `/mnt` — a plain directory in the writable overlay —
rather than at `/` itself.** The first pair could have been read two
ways: Landlock refused `MAKE_CHAR`/`MAKE_BLOCK` (the claimed mechanism),
or something about `/` specifically — the pivoted root's own
mountpoint, rather than an ordinary directory beneath it — behaved
differently from an ordinary write, since `/` is already known to be
writable (`WRITE_FILE` is granted there) in a way that could have been
mistaken for "device nodes are creatable here too". Identical `EACCES`
in `/mnt` rules that out: the refusal is Landlock's `MAKE_CHAR`/
`MAKE_BLOCK` withholding, not something particular to the root
mountpoint.

**That last clause is reasoned, not re-measured, and the difference is
worth being honest about.** The probe's own call order is mknod (char,
block), mkdir, the tmpfs mount, the bind mount, the same two mknods
again in `/mnt`, *then* `unshare`, *then* `pivot_root` — so only
`pivot_root`'s refusal is a direct measurement of what happens *after*
`unshare(CLONE_NEWNS)`; the six refusals before it were never retried
afterward in this run. That `pivot_root_noop` still comes back `EPERM`
post-unshare is consistent with the reasoning above and is real evidence
for it, but "the three refusals above hold exactly the same afterward"
overstated a re-measurement this probe did not perform for those three
specifically.

**No FILESYSTEM escape found either way** — the mount-family refusal
that matters for all of them is the one thing `pivot_root_noop` did
confirm survives the unshare — but the honest description is one
confirmed post-unshare data point plus a mechanism argument for the
rest, not seven identical repeated measurements. The qualifier is not
decorative: the filesystem is one of two things measured this round.
The other — what this lid set lets a house do to *another process* in
the city — is not the same story, and is not folded into "no escape
found" below. Per the operator's stop condition (a filesystem escape
would have meant stop and report instead of building), this measurement
alone is what authorized building the console house from here.

### Measured: process reach

**A second, separate question, asked after the filesystem one because
building had already been authorized by the time it was asked: what can
this house do to *another process* in the city, not to the filesystem.**
No seccomp lid means no restriction on `kill(2)`, `ptrace(2)` or
`process_vm_readv(2)` at all — none of the three is governed by
Landlock, which is filesystem-only in this tree, or by the mount/net
namespaces this house has. There is no pid namespace lid, so every
process in the city — this house's, any other house's, any
supervisor's — shares one flat pid space with the same numbers
everywhere.

**Discovery has no `/proc` to read either, so it is the same
brute-force `kill(pid, 0)` sweep the operator specified**, extended in
`houses/console-escape-probe.c`'s `reach_probes()`. Getting the range
right took three wrong attempts, each found by running, and the
comment in that function records all three so the next person does not
repeat them: scanning up from pid 2 hit kernel threads and this house's
own supervisor and log-relaying process, and killing one of those broke
the probe's own log pipe and ended it with `SIGPIPE` before it ever
reached the victim; scanning up from this process's own pid on the
assumption a later-declared unit always gets a higher one was falsified
by the boot log itself (`spawned probe pid=80`, `spawned victim
pid=82`, this process's own runtime pid `84` — the victim's house pid
sits *below* this process's, not above, because this house's longer lid
chain loses the race to fork+exec against the victim's simpler one); a
wider one-directional retry did not help because the direction, not the
distance, was wrong. The working sweep is a symmetric window around this
process's own pid, excluding pid 1, this process's own pid, and its own
supervisor (`getppid()`, which for an exec'd house is exactly that), and
staying clear of low pids where kernel threads and this house's logger
live.

Booted alongside `houses/reach-victim.c` — an ordinary `lids=seccomp`
house with no brick, chosen deliberately: seccomp restricts what a
process may call itself, not what another process may do to it, so a
"confined" victim is not a shield here and the measurement is not
flattered by picking an undefended one. Quoted from an actual run:

```
PROBE reach_exists_pid57 rc=0 errno=0(-)
PROBE reach_ptrace_seize_pid57 rc=-1 errno=1(Operation not permitted)
PROBE reach_vm_readv_pid57 rc=-1 errno=3(No such process)
PROBE reach_sigkill_pid57 rc=0 errno=0(-)
```

repeated for every pid the sweep finds in range (typically 3-4 in a
two-house test boot). **`kill(pid, 0)` finds them and `kill(pid,
SIGKILL)` reaches them — confirmed against the real victim, not just a
process the sweep happened to find**, by the boot log's own line:
`[nw-root] house exit victim status=9` — status 9 is `SIGKILL`'s raw
signal number in a `WIFSIGNALED` wait status, the same encoding the
earlier `status=13` (`SIGPIPE`, from the first wrong attempt above) used.
This is not inferred from the sweep; it is nw-root itself reporting that
the victim house died by exactly the signal this probe sent.

`PTRACE_SEIZE` is refused (`EPERM`) on every pid, every run. This did
**not** reproduce locally in an isolated test (a plain fork()ed child
with the identical Landlock ruleset applied to itself, ptrace-seized by
its uid-0 parent, succeeds) — the difference between that and the real
measurement is not fully identified. Candidates not ruled out: the
sibling relationship (probe and victim share a common ancestor rather
than one being the other's direct parent), or something about this
house's own `unshare(CLONE_NEWNS|CLONE_NEWNET)` changing how its
capabilities are evaluated against the target. **This is named as an
open question rather than resolved**, because guessing a mechanism here
and writing it down with confidence is exactly the failure this
project's rules exist to catch — the fact that ptrace is refused is
measured and reproducible; why is not, yet.

`process_vm_readv` never succeeds either — `EPERM` in some runs, `ESRCH`
("No such process") in others, against a pid that `kill(pid, 0)` had
just confirmed existed moments before. Both outcomes mean the same
thing operationally (no memory content was ever read; every attempt
returned an error, none a byte count), and the `ESRCH` cases are at
least partly explained by the sweep also catching short-lived kernel
worker threads spawned by this house's own erofs/loop/overlay mount
setup, which have no user address space `process_vm_readv` can name —
plausible given the pid clustering observed, not fully disentangled
from genuine victim-process results within this investigation.

**So: no filesystem escape, and — separately — an unconfined house can
discover and kill any process in the city it can reach by pid, but this
measurement did not achieve a memory read and did not confirm a
successful `ptrace` attach against another process, for reasons not
fully explained.** That is the honest shape of the finding, not "an
operator console that can reach every process in the city" in the
strongest sense that phrase could be read — kill, yes, demonstrated and
confirmed against the real target; attach and read, refused, every
time, by a mechanism this note does not claim to have identified.

### Candidate mitigations for the process-reach cost — listed, not built

Per the operator's instruction: this is the record of what exists to
choose from, not a decision. Nothing below is implemented.

- **A pid-namespace lid.** The structural fix — a house in its own pid
  namespace cannot even *name* a process outside it, so `kill(pid, 0)`
  on anything but its own descendants would be `ESRCH` before any
  permission question arises. This is a **plan-format change**: a fifth
  `NW_LID_*` bit, `unshare(CLONE_NEWPID)` in `nwsup.c` alongside
  `lid_newns()`, and the usual four-place update invariant 3 requires
  (`blob.h`, the baker, both specs). It also changes what "this house's
  own supervisor" means for reaping — a pid-namespace init has its own
  reaping obligations — which is new TCB surface, not a flag flip.

- **Landlock signal scoping, if the kernel's ABI has it — measured
  here, not assumed.** `landlock_create_ruleset(NULL, 0,
  LANDLOCK_CREATE_RULESET_VERSION)` returns `7` on this kernel
  (`6.18.44-fc-v37`), and `LANDLOCK_SCOPE_SIGNAL` (restricting signal
  delivery across a Landlock domain boundary) is a real ABI 6+ feature —
  but this box's installed `/usr/include/linux/landlock.h` predates it:
  no `LANDLOCK_SCOPE_SIGNAL` constant, no `scoped` member on `struct
  landlock_ruleset_attr`. Using it from `nwsup.c` would need either
  newer kernel headers or a hand-extended struct definition matching
  what the running kernel actually accepts — a real but small
  implementation cost, named so it is not discovered mid-change. It
  would not touch `ptrace`/`process_vm_readv` at all (Landlock's scoping
  in this ABI is signals only), so it closes the `kill` finding and
  leaves the ptrace/read question exactly as open as it is today.

- **A narrow seccomp denylist for this house specifically.** Tempting
  and, on its own terms, **already refused**: `runtime.md`'s hard rules
  say "There is **one** allow-list and a house does not choose it" and
  name a second filter path as "recreating the bug that was removed."
  A per-house denylist is a second table by a different name. Listing
  it here because it is the mitigation someone will reach for first,
  not because it is free — building it means reopening that decision
  explicitly, in `runtime.md`, not adding a table beside it.

### Built and controlled

`tools/console-boot-test.py`'s original two checks (the token echoed
back over `ttyS1`; silence when `bind=` is removed) pass unchanged under
the new lid set — nothing about reachability depended on seccomp. Two
more, as instructed:

- **`console-house-seccomp-control`**: the identical plan with `seccomp`
  added back to the lid set. The house dies, budget exhausted, the same
  way the first build attempt did — proving the lid set is what makes
  this work, not the brick, the bind, or the wrapper. If this control
  ever passed (the house working *with* seccomp declared), that would
  mean the fix was accidental.
- **`console-house-lids-exact`**: the baked blob's `lids` byte, read at
  its declared offset, is exactly `0x0e` (`landlock|newns|newnet`) — no
  seccomp bit, no stray bit either.

All four pass. Quoted from the actual run:

```
ok console-house-reachable (received b"...sh: can't access tty; job
control turned off\r\n~ # ...NWCONSOLE-8f2c1a\r\n...")
ok console-house-control (silent on ttyS1 with bind= removed, as required)
ok console-house-seccomp-control (adding seccomp back kills the house,
budget exhausted, as required)
ok console-house-lids-exact (blob lids byte = 0x0e = landlock|newns|newnet,
no seccomp)
ALL CONSOLE BOOT CHECKS PASSED
```

**Wired into the gate, not run by hand.** `tools/console-boot-test.py`
ran only when someone typed its name, so `make test` never re-checked
any of this — the exact silence failure `CLAUDE.md` names: a mechanism
that is correct and never reached looks identical to one that does not
exist. `tests/run.py` now has `test_console_house_reachable`, which
imports `tools/console-boot-test.py` as a module (the same
`importlib.util.spec_from_file_location` idiom `_fold_house_body()`
already uses for `tools/fold-house.py`), captures its stdout, and
requires `rc == 0` and `"ALL CONSOLE BOOT CHECKS PASSED"` in the output.
A missing `qemu-system-x86_64`, a non-static `/bin/busybox`, or a
non-root run makes `check_environment()` raise `Unavailable`, which the
test turns into a named `SKIP: console-boot-test (...)` — never a
silent pass, per `harness.md`'s *a test whose outcome depends on the
environment must say so*. It is registered in `main()`'s test list
after `test_landlock_bind_to_a_file`, so a plain `make test` boots the
four QEMU guests this needs; `docs/ENVIRONMENT.md` records the measured
cost (about fifty seconds of the suite's forty minutes) and the three
things the test asks for before it asks the machine to boot anything.

**The control asked for — drop the bind and `make test` goes red —
was run, not assumed.** `write_city()` in `tools/console-boot-test.py`
was edited to force `with_bind = False` unconditionally (a one-line,
clearly-marked temporary change, never committed), and a full `make
test` was run against it. It failed exactly where the missing bind
should make it fail, not somewhere upstream:

```
FAIL: token not echoed back over ttyS1
FAIL: console-boot-test.py did not report all checks passing (rc=1)
```

and the overall run ended `EXIT_CODE=2`. The forced line was then
reverted and a plain `--only console-house-reachable` run confirmed
green again before anything was staged. The control is not itself
checked into the tree — `write_city()`'s own `with_bind` parameter is
what the checks above already exercise, by calling it with `True` and
`False` — so this paragraph is the record of having run it, the same
shape `plan.md`'s definition of done asks for a crafted-bad-blob
rejection.

### What this means for the mechanism-rule table above

Unchanged in substance, corrected in one row: `lids=` is
`newns,landlock,newnet`, not `newns,landlock,seccomp`. No new plan
field, no TCB change — this time genuinely, checked by building it
rather than by reading `strict_allow[]` and stopping. The `nw-sup`
Landlock fix earlier in this note is unaffected by any of this and has
already landed on `main` independently, with its own test
(`test_landlock_bind_to_a_file`).
