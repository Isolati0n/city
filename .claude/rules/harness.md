# harness — territory rules

<!-- nw-init:install-agents v1 -->
**Not an agent.** This was a dispatchable brief until 2026-09-10 and was
never dispatched once. Its content is reference read at the moment it
applies, so `tools/rules-hook.sh` delivers it on a `PreToolUse` for any
file in this territory. Scope: Owns tests/run.py and the fixture houses (unit_probe.c, houses/*.c). Use for adding or repairing tests, scale runs, fuzzing, difftests, and for proving that a new test can actually fail.

<!-- nw-init:absent-ok wire_order.py -->

You own the put-together suite and the fixture houses. Not in the TCB — and
that is exactly why the suite is the largest source file in this project,
larger than any file in the trusted core.
Read `houses/` rather than any list of fixtures written down here.

## Two traps that have already caught someone

**The staging trap.** The suite runs binaries from `/tmp/nw-init-run`, not
from the source tree. `make` rebuilds the tree; **`make stage` is what
refreshes the thing the suite executes.** A result obtained after `make`
alone is a result about the previous build. This produced a negative control
that *passed* — which read as "the code works" and actually meant "the test
never saw the change." Always `make stage`.

**The log-chunk trap.** PID 1's logger prefixes the start of a *write chunk*,
not each line inside one, and it reads in bounded chunks. A fixture that
prints several lines and flushes once gets one prefix and then unprefixed
lines; a fixture that writes more than a chunk gets split mid-line. So: have
the fixture tag every line with its own unit name, and do not write assertions
against the logger's prefix for anything but the first line.

## A test whose outcome depends on the environment must say so

**Never let a test pass down a branch the environment forced.** `lid-landlock`
existed for its whole life without ever running: every machine it was
exercised on lacked Landlock, so it took the unavailable path, returned early,
and the suite printed a green line. The lid granted too little to execute a
dynamically linked binary and nothing found out.

So: detect the capability explicitly, the same way the code under test detects
it — `landlock_abi()` calls `landlock_create_ruleset` rather than reading a
config file. Then `skip(name, why)` with a named reason. A skipped test is not
a passing test, and `main()` refuses to print a bare `ALL TESTS PASSED` when
anything was skipped.

`print_environment()` runs before the first test and says what this machine
provides. **When you report a suite result, report that block with it.** A
green line is evidence only against a stated environment; without one it is a
claim about nothing.

**Detect the capability, not a tool that implies it.** `fs_mountable()`
reads `/proc/filesystems`; it does not check for `mkfs.vfat`. Those are
different questions, and confusing them produced a wrong correction on
2026-09-10: the mkfs tool is installed here, the kernel has no FAT driver at
all, and "the tool exists so the gap can close" was published before anything
tried to mount one. Ask the question the code under test asks.

- **A branch taken because something was missing.** If a test has an if/else
  on a capability, only one side runs on any given machine. Say which side
  ran, in the `ok` line, so a reader of the output knows what was exercised.

## Every absence assertion must be paired. The pairing is the test.

**An assertion that something is absent must sit beside a positive assertion
that the thing which would have produced it actually ran.** Unpaired, it is
satisfied by the mechanism working *and* by the mechanism never being
reached, and those are opposite outcomes reported identically.

This is not a nicety and not a style preference. It is the test. Alone,
`expect("badcall survived" not in out)` asserts nothing about seccomp: it
passes when the filter kills the house, when the exec fails, when the plan
never booted, and when the fixture was not built. Adding `expect("badcall
started" in out)` is what turns it into a claim about the filter, because
only then does the pair mean *the house reached the call and did not get
past it*.

Check for this shape whenever you touch a test, not once. It has been found
three separate times in this suite, which makes it common rather than
incidental:

- `lid-advisory` passed on every machine that lacked Landlock;
- `seccomp-kill` asserted only that `badcall survived` was absent;
- `kind-exit0` asserted only that `restart quitter` was absent.

Grep for it: `not in out`, `not in (`, `== 0`, `is None`, `assertNotIn`,
`returncode != 0`. For each hit ask **what else makes this true**, and if the
answer includes "the code under test never ran", the assertion is not
finished. A rejection test has the same shape from the other side: a baker
that exits non-zero for a syntax error is not proof that it rejected the
thing you meant, so assert the reason string too.

## The rule that makes a test worth having

**A test you add must be shown *failing* when the thing it tests is
removed.** Run the control before you believe the test.

Two ways a green test can be fake, both seen here:

- it asserts on a string that gets printed whether or not the mechanism ran
  (assert on the *effect*, not on the announcement);
- it asserts on state the test itself created.

`test_brick_is_a_root` is the worked example: the controls were removing the
`lid_brick()` call, and keeping its `say()` while skipping the
`pivot_root` syscall. The second control is the one that matters — the
first would pass against a supervisor that logged the lid and did nothing.

The same rule stated for the checker: a check must be shown rejecting a
crafted bad blob, not merely accepting good ones.

## How to write a test here

- **Test the property, not the absence of a crash.** Thousands of fuzzed
  blobs found nothing because they tested crash-resistance instead of
  semantic correctness — a validator can be perfectly memory-safe and still
  accept corrupted input (bug 1).
- Boot through `unshare --pid --fork --mount-proc` so `nw-root` is real
  PID 1; orphan reaping only exists under that.
- Assert *which* unit did what and *how many* descriptors it holds. Bugs 4, 6
  and 13 all presented as silently wrong routing, never as a failure.
- **Never put a count in this file.** Counts belong inside an assertion,
  where being wrong makes something fail instead of quietly misleading a
  reader. Quote `make test`'s own roster instead.
- A test that disappears, or starts passing for a different reason than it
  used to, is a **finding to report** — not a baseline to re-derive quietly.

## The harness is more capable than the machine

A new failure category, 2026-09-11. Everything found before this was a
claim the code did not honour. This is a test setup quietly satisfying
a requirement the real machine will not.

`dawn-real-boot` runs under `unshare --mount --pid --fork`. That was
silently doing two things beyond isolating, and both are preconditions
a bootloader handoff does not provide:

1. **The mount namespace is already private.** util-linux `unshare
   --mount` defaults `--propagation private`, which is
   `MS_REC|MS_PRIVATE`. A bootloader-supplied root is `MS_SHARED`.
   `pivot_root` and `MS_MOVE` both return `EINVAL` on a shared root.
   The test never saw it. Dawn now remounts; the comment in `dawn.c`
   is the record.
2. **The current root is not rootfs.** `pivot_root(2)` is defined to
   fail when the current root is the kernel's initramfs `rootfs`
   (`EINVAL`, see NOTES). The harness is on the host filesystem. A
   `-kernel/-initrd` boot is on rootfs. The test took the success
   path; production took the failure path. Dawn now falls back to
   `MS_MOVE` plus `chroot`; that branch has no lab coverage.

`unshare --pid --fork` adds a third gift the suite treats as success:

3. **PID 1 exiting is an exit status, not a kernel panic.** In a pid
   namespace the namespace dies and `unshare` returns the child's
   code. `expect(rc == 0)` after `--hold-ms` used to read a clean
   shutdown that was `_exit(0)`. On real hardware that `_exit` is
   `Attempted to kill init!`. Production PID 1 now ends in
   `reboot(RB_POWER_OFF)`. Measured inside `unshare --pid --fork`:
   reboot does not return; the parent sees 130 (SIGINT). The suite
   helper `city_closed` accepts 0 or 130 *after* the `closed` line,
   and rejects a panic string. The timer is only `--hold-ms`; dawn
   forwards `NW_HOLD_MS` when the lab sets it and never otherwise.

`boot()` itself uses `--pid --fork --mount-proc`. `--mount-proc`
implies `--mount`, so the happy-path boots get the private namespace
too. They do not pivot, so they still would not have found (1) or (2).

### What else the harness provides for free

Looked, 2026-09-11. Not fixed here. Each is a requirement a real boot
has to meet that the suite does not ask.

- **`/proc` is already there, and already the right pid ns.**
  `--mount-proc` remounts it. Dawn mounts `/proc` only after the
  pivot. A house that inspects `/proc/self/fd` before that mount
  would see nothing, or the outer ns; the suite never starts a house
  that early.
- **`/dev` already has the block nodes.** `dawn-real-boot` hands dawn
  `/dev/loopN` created on the host. A real boot needs `devtmpfs`
  before `mount(NW_ROOT)` so `/dev/vda` exists. Dawn does that; the
  test cannot fail that path because the nodes pre-exist (ensure_mount
  treats `EBUSY` as success).
- **The binaries are dynamically linked and the test copies the
  loader.** `dawn-real-boot` parses `ldd` and copies `.so` files into
  the ext4. A missing interpreter is `ENOENT` on `execve` and looks
  like a missing binary. The image-build script builds static to close
  that; `make test` does not.
- **stderr is already a pipe the suite reads.** A real boot's fd 2 is
  the kernel console. Chunking, interleaving, and a stuck serial line
  blocking a log pipe are invisible here.
- **The kernel is already up, with the filesystems and LSMs the host
  happened to load.** No virtio, no NLS module, no command-line-to-env
  handoff, no dirty ext4 from the previous run. `dawn-real-boot`
  substitutes ext4 for FAT when `/proc/filesystems` has no `vfat`;
  that skip is named. The NLS gap (`VFAT=y` and `iso8859-1=m`) is
  not, and only showed on a kernel boot.
- **Houses in the default city are oneshot and finish inside the hold
  window.** A long-run house, a missing exec path on the real disk,
  and a second mount after an unclean shutdown are all off-camera.

A test that needs one of those gifts must say so in the `ok` line or
`skip`, the same way Landlock does. Do not add a mount, a namespace,
or a copied library to make the test green without recording that the
machine will not have it.

## Scale: measured 2026-09-11, and the numbers are in the tool

The gap is closed by `tools/scale-probe.py`. It rebuilds the tree at a
raised `NW_MAX_UNITS` — a full rebuild plus a re-bake, so far too slow
for `make test` — bakes a city of N units, boots it under
`unshare --pid --fork --mount-proc`, and checks that every unit ran,
reported exactly once, held no ungranted descriptor, and was reaped.

**Where it breaks and why.** On this machine (`ulimit -n` 20000,
`pid_max` 32768, 4 CPUs): clean at 8192 units; at 10240 PID 1 stops with
`HALT: log pipe`, because it holds two log pipes per house and
2·10240 + 8 = 20488 descriptors is past the limit. The predicted break is
therefore n > (20000 − 8) / 2 ≈ 9996. Controlled by lowering the limit
tenfold: at `ulimit -n 2000` the break moves to n = 1024 with the same
message and 512 still passes. **It fails loudly** — a named halt, not a
crash and not silent misrouting.

**Boot cost is quadratic.** Time to every house having run: 0.46 s at
256, 1.22 s at 512, 2.64 s at 1024, 12.4 s at 2048, 54.0 s at 4096,
140 s at 8192. `close_others` reads `/proc/self/fd` in each spawned
house and the spawner inherits PID 1's ~2n log pipes, so the sweep is
n × 2n. Dividing the measured time by 2n² gives 1.26, 1.47 and 1.61 µs
per swept descriptor at 1024/2048/4096 — consistent to within 30%, which
a wrong model would not be.

**The break is a DEATH, and a large-N harness has to tell three exits
apart.** Its wait loop ends when every house has reported, when the
deadline expires, or when PID 1 exits on its own — and the last is what
the break above *is*. Collapsing the last two into "timed out" relabels
the tool's headline result as a hang: verified 2026-09-11 by running the
breaking rung, which returns in seconds with
`why: city did not open with 10240 houses; last: [nw-root] HALT: log
pipe`. A timeout message there would have been both wrong and slow.
Conversely the deadline branch must not require that the city never
opened: one that opens and is then too slow falls through to the content
checks and reads as lost units. Set a flag at each exit; do not infer
which one was taken.

**And parameterise every reader by the name width, including the ones
inside the wait loop.** The width fix reached the baker and the two
report readers and missed the loop's own `house=(u\d{4})`, so above the
four-digit boundary the loop never saw completion and ran to the
deadline at every rung — inside the interval the tool exists to
characterise. Found by grepping for the literal after the fix, not by
running: at the rungs where it bites, the city dies first and the death
exit hides it.

### Three traps this found, all in the probe rather than the code

Worth reading before you write a large-N test, because each one produced
a confident wrong answer first.

- **Do not assert presence on the logger's prefix.** Requiring
  `[uNNNN] house=uNNNN` reported 4 of 64 units missing on a correct
  tree. The prefix marks a write *chunk*, not a line — the log-chunk
  trap above, met head-on. Presence comes from the fixture's self-tag.
- **A prefix that disagrees with the self-tag is not misrouting.**
  `spawn_logger` emits three separate `write(2)` calls per chunk —
  prefix, buffer, newline — and every logger shares fd 2, so another
  logger can write between them. Measured over three runs each:
  mismatches were [0,1,0] at n=16, [0,0,0] at n=64, [0,0,2] at n=128,
  while missing and duplicate units were 0 everywhere. Nondeterministic,
  which a routing defect is not. **The merged console therefore cannot
  distinguish wrong routing from interleaving at any N**, so the bug
  4/9/13 class is not observable on that channel — separating them needs
  per-unit capture, which belongs to the logging pass. What *is* sound
  is exactly-once: it catches loss and duplication and it is
  deterministic.
- **Time the city, not your own hold.** The first version passed
  `--hold-ms 40n` and reported wall time, so n = 1024 showed a 41 s hold
  as a 44 s "boot" — a tidy straight line of 43 ms per unit that was
  entirely the harness measuring itself.

The probe also carries its own warning when every rung passes, because a
ladder that never breaks usually is not reaching anything.

**What the ladder does NOT exercise.** Every city it bakes is
`kind=oneshot lids=none` with no brick and no bind. `HISTORY.md` §40
gives the reason scale went first as bricks phase 2 adding a loop device
per house — a per-unit resource nothing had exercised near a limit — and
the probe still does not exercise it. The numbers say nothing about
per-unit loop devices, namespaces or seccomp filters at scale. `control`
caught the mismatch between the tool and the sentence justifying it.

**Still open, and now with a number attached:** nothing in `make test`
runs above `NW_MAX_UNITS`, because getting there costs a rebuild. The
suite's contribution is exactly-once at `NW_MAX_UNITS` (read from the
header, not written down here), in
`test_non_provision_at_max`; the ladder is a tool you run by hand.

## Definition of done

Quote real command output. Never summarise a run you did not execute.
