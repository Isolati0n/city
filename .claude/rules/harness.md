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

## Traps that have already caught someone

*(This heading said "Two traps". A third was added directly beneath it
in the same diff that acked a count elsewhere in the same section as
harmless English — a heading is the easiest count to leave behind,
because nobody re-reads it while adding to what it heads. `claims`.)*

**The staging trap.** The suite runs binaries from `/tmp/nw-init-run`, not
from the source tree. `make` rebuilds the tree; **`make stage` is what
refreshes the thing the suite executes.** A result obtained after `make`
alone is a result about the previous build. This produced a negative control
that *passed* — which read as "the code works" and actually meant "the test
never saw the change." Always `make stage`.

**The partial-gate trap.** `make test` is not one step. It stages, runs
`install-agents.sh --check`, runs `nw-check`, runs `tests/run.py`, and
runs `tools/coverage-tcb.sh`, in that order — and **the suite is what
the target runs, not its middle step.** On 2026-09-12 a commit was
reported as done on the evidence of `make stage` followed by `python3
tests/run.py`; the brief gate, which runs *before* the suite, was red
and had been made red by that same commit. Both `tcb-review` and
`claims` found it. The report said "the tree builds", which was true
and was about neither the gate nor the suite.

This is the staging trap's sibling and it is worse in one way: the
staging trap gives a result about the previous build, and this gives a
result about part of the tree while the part you did not run is the
part you broke. Run `make test`. If you need the suite alone for speed
while iterating, that is fine — but the run you *report* is the target,
and `STAGE=` and `NW_STAGE=` must name the same directory in both.

It generalises past this repository: every project has a target whose
steps someone eventually runs individually, and the step most likely to
be skipped is the cheap one that runs first.

**The log-chunk trap.** PID 1's logger prefixes the start of a *write chunk*,
not each line inside one, and it reads in bounded chunks. A fixture that
prints several lines and flushes once gets one prefix and then unprefixed
lines; a fixture that writes more than a chunk gets split mid-line. So: have
the fixture tag every line with its own unit name, and do not write assertions
against the logger's prefix for anything but the first line.

**And self-tagging is not enough, which this section used to imply.** The
logger *appends a newline* when a read does not end in one, so a line
straddling a chunk boundary arrives split **mid-token**:
`[orphan] run=3 leav` / `[orph] ing 3 behind`. A tag on every line does
not help — the tag is intact and the word you are counting is in two
pieces. Measured: `test_orphans_across_restarts` failed 5 times in 12
under load on a tree where the reaping was perfect, under a message
blaming the restart budget. `tcb-review` independently mis-reported a
drain result for the same reason and caught itself.

Two ways out, and use both:

- **Pad every fixture line to a divisor of the logger's buffer** (64
  works for 256). Every write is then one whole line, the pipe only ever
  holds whole lines, and a bounded read can only return whole lines — the
  split becomes impossible rather than unlikely. `houses/lastwords.c` and
  `houses/orphan.c` are the worked examples.
- **Reconstitute the byte stream before counting** — strip the prefix and
  the newlines, then match. That keeps the assertion correct even if the
  padding assumption stops holding, which is the point: do not let one
  fixture's arithmetic be the only thing between you and a false failure.

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
raised `NW_MAX_UNITS`, bakes a city of N units, boots it under
`unshare --pid --fork --mount-proc`, and checks that every unit ran,
reported exactly once, held no ungranted descriptor, and was reaped.

**The rebuild is not why it stays out of `make test`.** Measured,
`build_at()` is well under a second and flat in N, and a whole rung at
64 units costs about one second. What is too slow is the large rungs'
quadratic boot, documented below. (This said "a four-place change", then
"a full rebuild plus a re-bake"; `claims` timed the rebuild and both
were wrong. The correction was then inserted mid-sentence, severing the
list of what the probe checks and leaving the same wrong reason standing
in the section's closing paragraph — so the correction had to be made
twice, and the second time by moving it out of the sentence it broke.)

**Where it breaks and why.** On this machine (`ulimit -n` 20000,
`pid_max` 32768, 4 CPUs): clean at 8192 units; at 10240 PID 1 stops with
`HALT: log pipe`, because it holds two log pipes per house and
2·10240 + 8 = 20488 descriptors is past the limit. The predicted break is
therefore n > (20000 − 8) / 2 ≈ 9996. Controlled by lowering the limit
tenfold: at `ulimit -n 2000` the break moves to n = 1024 with the same
message and 512 still passes. **It fails loudly** — a named halt, not a
crash and not silent misrouting.

**Boot cost is quadratic, and that shape is the only part of it worth
writing down here.** `close_others` reads `/proc/self/fd` in each
spawned house and the spawner inherits PID 1's ~2n log pipes, so the
sweep is n × 2n. Dividing time-to-all-reported by 2n² gives a roughly
constant µs-per-swept-descriptor across 1024/2048/4096, which is what a
correct model predicts and a wrong one would not.

**The absolute figures are deliberately not here, because the ones that
were did not reproduce.** A table of six timings was written from a
single pass on 2026-09-11; `claims` re-ran three repetitions per rung on
the same machine and the same stated limits and none of the six came
back. The shape held; the constants did not.

**Run-to-run variation on this machine approaches a factor of two, and
that is the durable finding.** Not a ratio between the two passes — the
sentence here said "roughly half at every rung" and `claims` disproved
that too on the next round: the ratios ranged across the rungs and were
not a constant, and the spread *within* a single rung reached about 1.5x
on its own. So a second number replaced the first inside the paragraph
whose whole point was that the first number should not have been there.
**Third time. Do not put one here.**

Run `tools/scale-probe.py` and quote its output with the repetition
count, the way `measurement` requires. A single sample of a timing is
not a measurement, and neither is a ratio between two of them.

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

**Handed over 2026-09-11:** `tools/HANDOFF-scale.md` carries the ladder
result — clean at 8192, a named `HALT: log pipe` at 10240, the
`(ulimit − reserved) / 2` model and its control, and the quadratic shape
— for whoever takes scale on better hardware. It says plainly that the
measurement is done and **the test is not**.

**Still open:** nothing in `make test` runs above `NW_MAX_UNITS`. Not
because of the rebuild — that is under a second, see the top of this
section — but because the boot above a few thousand units is quadratic
and would dominate the suite. The suite's contribution is exactly-once
at `NW_MAX_UNITS` (read from the header, not written down here), in
`test_non_provision_at_max`; the ladder is a tool you run by hand.

*This read "and now with a number attached ... because getting there
costs a rebuild" until 2026-09-11. Both halves went stale in one edit:
the rebuild reason was corrected ninety lines above and left standing
here, and the number it pointed at was removed by the same edit. A
correction applied to the top of a section and not its foot is the
survived-by-not-being-moved shape, which this repository has now
produced in five separate files. `claims`.*

## A capability guard belongs in the helper, not at each call site

`erofs_available()` was written, was correct, and **three of its four
callers did not consult it**. On a machine without `mkfs.erofs` the suite
crashed with a bare `FileNotFoundError` instead of skipping — while the
environment block two screens above printed the right warning. That is the
silence failure from `CLAUDE.md` in the suite's own guard rail: the
mechanism worked perfectly and was simply not reached.

**So the guard lives in `make_brick()`**, which raises `Unavailable(why)`,
and `main()` turns that into a named skip using the test's own function
name. A new brick test gets the skip for free and cannot forget it. The
same move also makes a stray skip name structurally impossible, because the
name is derived rather than typed.

**And a crash is a failure, announced as one.** `main()` catches anything
that is not `SystemExit` (which is `expect()`'s own path), prints
`FAIL: <test> raised an unhandled exception`, and exits non-zero
deliberately. It previously escaped as a traceback: the interpreter does
exit non-zero on that — measured, 1 direct and 2 through `make` — but
nothing in the output read as a failure, and whether that survives a
wrapper is not a property this suite should inherit. Control: inject
`open("/nonexistent/...")` into a test and the run ends
`FAIL: hash-pin crashed`, exit 1.

**The general shape, because it has now arrived from both sides.** A
capability difference between the lab and somewhere else looks like a code
defect when the lab has *less* than the machine (`/nw/mnt` absent, every
brick test failing at `mount ... errno=2`), and looks like a *pass* when
the lab has more than another environment. The second is worse, and both
are answered the same way: ask the question the code under test asks, in
one place, and make not-asking impossible rather than remembered.

## Durable fixture state: reset ONCE PER RUN, never per boot

Writable layers are the first thing in this tree that survives a test.
Everything before them lived in the stage or in `WORK` and went away
with it; a layer is durable by design, keyed by a declared id, and
sitting on the machine root. So the suite has to decide *when* it wipes
one, and the candidate answers below are not equivalent. (This sentence
counted them, in the file whose own rule is never to put a count here —
and the count was of the list directly beneath it, which is the shape
`CLAUDE.md` says survives review. It also called both wrong answers
silent, which the next paragraph's own wording contradicts: a failure
against correct code is not silent.)

**Per boot is wrong, and it is the tempting one.** Production never
wipes a layer, so a harness that does is testing something the machine
will not do. The concrete cost is a test that cannot be written: a
durability-across-reboot test is two `boot()` calls on one blob, and
per-boot reset wipes between them, so the second boot reads an empty
layer and the test either fails against correct code or, worse, passes
because "absent" was what it expected. That is the harness being *less*
capable than the machine — the mirror of the section above, and the
direction that produces a false failure rather than a false pass.

**Per test is wrong differently:** it is unenforceable. It is the line a
new brick test forgets, and forgetting it is invisible until some later
test reads a value a previous one left behind.

**Once per suite process, per id, is the one that holds.** First
sighting of an id resets it; every later sighting does not. Each test
starts from a pristine layer without inheriting the previous suite
*run*, and a test that boots the same plan twice keeps what the first
boot wrote. It lives inside `stage_layers()`, which is the only creator,
so a new brick test gets it without knowing it exists — the same move as
the capability guard above, for the same reason.

**The evidence that this is not hypothetical is that it bit the harness
before it bit anyone in production.** A probe wrote one byte over `/id`,
the write copied up into the layer, and every subsequent run of
`test_brick_is_a_root` read `id=xrick-one` from a *correct* brick. The
durable-mask failure `.claude/rules/runtime.md` records, arriving
through the test suite. Two things generalise from it:

- **Durable state under test needs a reset whose granularity matches
  production's, not the test's convenience.** Ask what the machine does
  between two of these events. If the machine does nothing, the harness
  doing something is a difference you will debug later as a code defect.
- **A probe must not write where an assertion reads.** That one is
  `CLAUDE.md`'s "a rule is at its weakest in the change that introduces
  it": the fixture that demonstrated a layer is writable did so over the
  file every other assertion in the test reads.

## A note for reviewers working read-only

`tests/run.py` refuses an over-long `NW_STAGE` at startup. The bound comes
from `exec_path[NW_PATH_LEN]`, which the suite fills with
`{STAGE}/nw/bin/<fixture>`; a session scratchpad path can exceed it. Do not
copy the number out of here — the refusal prints it, and two written-down
values have been wrong already, each in the generous direction (it was the
slack left in `NW_BRICK_LEN`, a constant phase 3 deleted, and the fallback
that replaced it was a literal shorter than the longest fixture name).
Pick something short under `/tmp`, and pick it **distinctly** — two agents
sharing `/tmp/nwc` will silently fight over one stage. `tcb-review` hit
that and it cost a round trip.

**And a distinct stage no longer isolates the BRICK tests.** Phase 3 has
`nw-sup` compose an absolute `NW_BRICK_DIR` path, so `make_brick` writes
its image to the machine root rather than into the stage — and the filename
is the sha256 of the tree's *contents*, so two runs of the same tree target
the identical file. `make_brick` does `rm -f` then `mkfs.erofs`; a
concurrent run opening that path inside the window gets `ENOENT` and the
house dies at `open brick image`, which reads as a code defect and is not
one. There is no fix here to apply — the absolute path is the point of
phase 3 — so it is written down instead. If you are running a suite beside
another agent's, expect it, and do not chase it as a regression.

The images are also never cleaned: `make stage` only removes `$(STAGE)`.
They accumulate under `NW_BRICK_DIR` on the machine. `fd-auditor`.

## Definition of done

Quote real command output. Never summarise a run you did not execute.
