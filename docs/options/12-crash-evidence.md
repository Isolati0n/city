# 12 — Sealed crash evidence packages

Status: **built and landed.** This note answers the seven questions
that were asked, corrects one assumption the brief made that reading
the code disproves, and states the COMMITMENT-5 check explicitly rather
than leaving it implicit. It started as design-only per instruction;
the code described throughout is now the actual implementation, not a
proposal, and every present-tense claim below is checkable against
`pid1.c`, `nwsup.c`, `blob.h`, `dawn.c`, `sha256.c`/`sha256.h` and
`tests/run.py` as they stand.

## The assumption the brief made, and why it's wrong

The brief's premise was "nw-sup keeps the last N KB of the house's
stdout/stderr in a ring buffer in its own memory, since it already reads
that pipe." **It doesn't.** Read the actual fd plumbing rather than
assuming it:

- `pid1.c` creates each unit's log pipe once, at boot, and keeps the READ
  end (`log_r`) for itself, forked off into a dedicated per-unit logger
  process (`spawn_logger`) that reads it in a loop and relays every chunk
  to the real console (fd 2, inherited from PID 1) with a `[name] `
  prefix. That logger is a **sibling** of `nw-sup`, not its parent or
  child — both descend from PID 1, connected only by the pipe.
- The WRITE end (`log_w`) is handed down through `nw-spawn`'s `pack_kit()`
  (`nwspawn.c`), which `dup2`s it onto fd 1 and fd 2 of the process that
  is about to `execl(sup, "nw-sup", ...)`. So **`nw-sup`'s own fd 1 and fd
  2 already ARE that same log pipe's write end** — every `say()` call
  writes into it, which is why `restart NAME ...` lines already carry the
  `[name]` prefix on the console. But a write-end fd can never be read
  from, by definition, regardless of how many times it is `dup`'d.
  `grep -n "pipe(\|log_r\|log_w" nwsup.c` returns nothing: confirmed,
  `nw-sup` has zero read access to the house's own output.
- The pipe is **not** recreated per restart — `nw-sup` holds the same
  fd 1/2 across every restart of a unit for its whole supervisor
  lifetime, so there is no per-death EOF either. The logger's `read()`
  loop has no way to know "a restart just happened" on its own.

So the brief's "same trigger, additional artifact, not a new subsystem"
framing is half right and half wrong. **The metadata half is exactly as
described**: `nw-sup` already computes everything the package needs
(`name`, `deaths`, `budget`, `WIFEXITED(st)`/`WEXITSTATUS(st)`/
`WTERMSIG(st)`) at the exact point it already composes the `restart`/
`spent` line (`nwsup.c`, the two `snprintf(line, ...)` sites just before
each `say(line)`). **The output-capture half genuinely cannot live in
`nw-sup`** without either giving it a second, private copy of the pipe
(restructuring who owns the log pipe — a much bigger, riskier change to
the already-hardened `wait_house()` poll loop) or accepting that the
byte capture has to happen where the bytes already flow: PID 1's
existing per-unit logger.

This is the one place this note's answers differ from what the brief
assumed, and it's flagged here rather than patched around silently, per
the operator's own standing rule for this project.

## 1. Where does a house's recent output live while it's running?

**Proposed: PID 1's existing per-unit logger process** (`spawn_logger` in
`pid1.c`), which already reads every byte. It would gain a fixed-size
(`NW_EVIDENCE_TAIL_MAX`, proposed 4096 bytes — a plain `#define`, not a
plan field, same category as `NW_CTL_DIR`) **circular** ring buffer, kept
always in chronological order at flush time (see below), on the stack of
its own forked, never-`exec`'d branch. No `malloc` — the buffer would be
a plain array; invariant 1's `<<absent-in:pid1.c:malloc>>` would stay
satisfied, and nothing proposed here parses the bytes, allocates, or
recurses. It would be filled on every `read()` chunk exactly where the
logger already assembles `prefix`/`buf`/`newline` for the console write.

**A true ring, not a shift-on-append buffer**, because a house that
writes gigabytes is one of the operator's own control cases: at the
logger's existing 256-byte read chunk size, a 1 GiB house would call the
capture path on the order of 4 million times, and a shift-based "keep the
last N bytes contiguous" buffer is O(cap) per call in the worst case —
tens of billions of byte-copies for that one house, pure overhead with
nothing watching it. A circular buffer is O(1) per write regardless of
volume.

**Flush to the tail file (below) is on every chunk, not throttled — this
note's first draft proposed throttling and building it found the draft
wrong.** The reasoning for throttling was real (at 256 bytes/chunk,
flushing every chunk for a 1 GiB house is ~4 million `pwrite` pairs), but
a byte-count threshold high enough to bound that cost (`NW_EVIDENCE_TAIL_MAX
/ 4`, 1024 bytes, in the first draft) is far above what a TYPICAL crash
ever writes — a few short lines, tens of bytes — so ordinary houses never
crossed the threshold and their tail files stayed empty for the entire
death that needed them. Built and run against a real death (`unit-boom`,
which writes one 5-byte line before exiting): both evidence packages for
its two deaths showed `tail_bytes=0` although the tail file itself held
the real output by the time the packages were composed — the throttle
was silently defeating the one case this feature exists for, found by
running it, not by reasoning about the threshold. Flushing every chunk
instead costs at most one `pwrite`-pair per `read()` chunk, each bounded
by `NW_EVIDENCE_TAIL_MAX`, and chunks are already bounded by the pipe's
own read granularity — a real, but small and constant, per-chunk cost
regardless of total volume, which is the right trade for correctness on
the common case over an optimization for an extreme one. No time source
is added to `pid1.c` either way.

**The file itself is written in logical order**, not raw ring-physical
order: at each flush the logger writes the ring's two segments (the
tail-to-wrap-point piece, then the wrap-point-to-cursor piece, or a
single segment if the ring hasn't wrapped) in that order, so a human
reading the tail file sees the house's actual last N bytes in the actual
order it wrote them — never a buffer that reads correctly to code and
looks scrambled to an operator.

**Best-effort, never fatal to the logger's real job.** If the tail file
can't be opened at logger startup (directory missing, permission,
whatever), the logger sets a flag and skips every subsequent flush for
that unit's whole life — it never retries, never blocks, and never lets
this secondary path affect the one thing the logger exists for: relaying
live output to the console.

## 2. Where does the package get written?

**Proposed: `NW_EVIDENCE_DIR "/nw/evidence"`**, to be added to `blob.h`
beside `NW_LAYER_DIR`/`NW_CTL_DIR`, neither of which it touches. Two
kinds of file would live there, and only one of them is "the package":

- **`<name>.tail`** — the logger's rolling capture, described above. Not
  a package, not a death record: it's overwritten in place, always
  exactly the current tail, exists for the unit's whole life, and is
  purely internal plumbing between the logger and `nw-sup`. An operator
  can read it, but nothing here treats it as an artifact.
- **`<64-hex>.evt`** — one per real death, **named by the sha256 of the
  package's own content, not by house name or timestamp.** Decided by
  the operator, revising this note's first draft (which had proposed
  `<name>-<deaths>.evt`), because item #6 on the operator's roadmap — one
  shared content-addressed store for files, snapshots and crash reports —
  is coming right after this feature, and a name-plus-timestamp package
  would need renaming to fit that store later. Hashing from the start
  avoids that rework, and it's the convention this tree already has:
  `NW_BRICK_DIR "/" <64-hex>.img`, sha256, raw 32 bytes internally, hex in
  the path — this reuses the exact same width and spelling rather than
  inventing a second one. **The house name and death count still live
  INSIDE the package content** (question 3), so a human or a later tool
  can still find things by house or time; only the filename becomes pure
  content identity. A separate small index (a per-house directory listing
  or a generated file mapping house → hash list) is a browsability
  convenience for later, not baked into the package's own identity.

  **This needed a hash function `nw-sup` did not have before this
  feature, and that is a real, stated TCB addition, not a free
  convention change.** Bricks are hashed by the baker (`bakery/mkbrick.py`,
  Python, explicitly non-TCB — `hashlib.sha256`) and the rest of the TCB
  only ever read the resulting 32 bytes as an opaque value, before this
  feature. `grep -rln "sha256" *.c *.h` (excluding `boot-out/`, which is
  a build copy) now returns five files, not two: `nwcheck.c` and
  `blob.h` (comments naming the algorithm, unchanged from before), plus
  `nwsup.c`, `sha256.c` and `sha256.h` — the implementation this section
  proposed and the tree now has. (This note's first draft said the grep
  "returns nothing," which was false even before any code existed here;
  `claims` caught it, and a second `claims` pass caught this paragraph
  going stale in the other direction once the code landed.) No crypto
  library is linked into any TCB binary — every TCB link line in the
  `Makefile` is a bare `$(CC) $(CFLAGS) -o $@ <sources>`, no
  `-lcrypto`/`-lssl`, no `LDLIBS`/`LDFLAGS` at all; `sha256.c` is
  vendored source, not a linked library, which is the point of writing
  it from scratch against NIST's own test vectors rather than reaching
  for OpenSSL.

  **CRC32 is a real collision-risk regression for this purpose, and that
  argument stands on its own — it is not what invariant 8 says.** This
  note's first draft attributed the argument to invariant 8 ("CRC32 is
  diagnostic, not identity"); invariant 8's actual text
  (`CLAUDE.md`) is about corruption versus tampering, and never uses the
  word "identity" — `claims` caught the misquote. The collision argument
  is made on its own merits instead: a 32-bit digest has a birthday-bound
  collision risk around 2^16 (~65,000) items, and this feature's own
  answer to question 5 is that evidence "accumulates on disk forever" —
  a population that plausibly reaches that range over a long-running
  system's life, for a feature whose entire point is not losing evidence
  silently. sha256's ~2^128 bound is not a close call by comparison.

  So this round vendors a small, self-contained SHA-256 implementation
  (a new file, e.g. `sha256.c` / `sha256.h` — one function,
  `nw_sha256(data, len, out[32])`, no other primitives) into the TCB,
  called from `nw-sup` at the exact point it has finished composing a
  death record and is about to write it.

  **The alternative — computing the hash outside the TCB, the way bricks
  already do — was considered and is rejected, not overlooked, and the
  first draft of this note didn't say so, which `claims` correctly
  flagged as a gap given `CLAUDE.md`'s explicit bias ("anything that can
  live in the baker instead, does").** `nw-sup` could write a
  provisionally-named package and let a separate, non-TCB tool (the same
  category as `bakery/mkbrick.py`, or the later fold/bakery retention
  pass question 5 already defers to) compute the hash and rename it in.
  That would keep sha256 out of the TCB entirely. It's rejected because
  it reopens exactly the problem this decision exists to close: a
  package would sit under a *non-final* name for however long it takes
  that separate tool to run — unbounded, since nothing here schedules
  it — meaning the feature would still need a rename step somewhere,
  just deferred one hop later and now dependent on an out-of-band sweep
  running promptly. "Avoid rework" was this note's stated reason for
  hashing from the start; a deferred rename doesn't avoid the rework, it
  postpones it past the point where evidence is trustworthy to read.
  Given that, the TCB addition is the smaller cost, and it is scoped as
  narrowly as the alternative would have needed the algorithm to exist
  somewhere anyway: one function, reused later by whatever item #6
  builds, not a general crypto surface.

  **Justification, per `CLAUDE.md`'s rule that a TCB addition needs a
  stated one**: not scope creep for evidence alone — it's the first
  instance of a mechanism item #6 is going to need generally, it reuses
  an algorithm this tree already trusts (sha256, already the brick
  identity) rather than introducing a third hash into the vocabulary
  alongside CRC32 and sha256-in-the-baker, and correctness is checkable
  independently of anything else in this feature: a known-answer test
  against NIST's own FIPS 180-4 test vectors, run in the suite, verifies
  the implementation on its own terms before anything else here depends
  on it.

  **Write order and atomicity, matching the brick precedent this note
  already cites rather than inventing a weaker one.** Compose the
  complete record (header fields plus the tail bytes) in memory first,
  hash *that* to get the final name — so there is no partially-*named*
  file, ever, since the name cannot exist before the content that
  produces it does. But composing-then-hashing does **not** by itself
  make the *write* atomic, and this note's first draft claimed it did
  ("no partially-written file... at any point"), which `claims` correctly
  called an overclaim: a single bounded `write()` can still be
  interrupted (kill, OOM, host crash) between `open()` and completion,
  leaving a truncated file sitting under a name every reader is entitled
  to trust hashes to itself — precisely the property `bakery/mkbrick.py`
  already guards against for bricks, via a temp name plus an atomic
  `rename(2)` once the write is verified complete, not via "the name
  comes from finished content" alone. This note adopts the same
  mechanism rather than a new one: write to a temp path under
  `NW_EVIDENCE_DIR`, then `rename()` to the hash-derived final name once
  the write returns success. A reader never observes a partially-written
  file under a content-addressed path, matching the precedent this note
  already leans on.

  **The temp name is `mkostemp()`'s, not `.tmp-<pid>`, and building it
  found the difference matters.** A first build used `.tmp-<pid>`, and
  running it while other boots were active on this same machine
  reproduced the exact failure this paragraph exists to prevent: two
  concurrent boots' `nw-sup` processes collided on the identical temp
  path and one clobbered the other's write before its `rename()` ran,
  losing a package. The cause is `NW_EVIDENCE_DIR` being machine-root
  and shared across every concurrent boot (the same caveat `harness.md`
  already states for `NW_BRICK_DIR`/`NW_LAYER_DIR`/`NW_CTL_DIR`) combined
  with `getpid()` being unique only *within* one boot's pid namespace —
  most boots run under `unshare --pid`, where numbering restarts from 1,
  so two independent boots routinely produce `nw-sup` processes with the
  *same* small pid. `mkostemp()`'s random suffix is unique regardless of
  pid, closing the collision outright, and it takes `O_CLOEXEC` directly
  at creation rather than needing a separate `fcntl` after `open()`.

  **What was "cross-boot collision" under name+timestamp naming mostly
  disappears under content addressing, and is worth saying explicitly
  rather than leaving a reader to work it out.** Two different deaths of
  the same unit collide under this scheme only if their entire recorded
  content is byte-identical — same reason, same value, same death count,
  same budget, *and* the same tail bytes. Within one `nw-sup` process's
  life that's already impossible (`deaths` differs by construction).
  Across two different boots it's possible but rare, and when it
  genuinely happens the two events really are indistinguishable by every
  field this package records — collapsing them to one file is correct
  deduplication, the same property content-addressing already gives
  bricks, not data loss. Under the temp-name-then-`rename()` mechanism
  above, an unconditional `rename()` over an existing same-named file is
  exactly this case: the existing file is already known, by the hash
  itself, to hold identical content, so replacing it with a byte-identical
  copy is a no-op in every way that matters — not a fallback for a name
  clash, a direct consequence of what the name now means.

**Directory creation follows the `NW_BRICK_DIR`/`NW_LAYER_DIR` precedent,
not the `NW_CTL_DIR` one**, and for a specific reason: `NW_CTL_DIR` is
created by `nw-sup` itself (idempotent `mkdir`) because `nw-sup` is the
only thing that ever touches it. `NW_EVIDENCE_DIR` is touched by PID 1's
logger too, and the logger runs *before* `nw-sup` even starts (loggers
are spawned in PID 1's own boot-time setup loop, well before
`nw-spawn`/`nw-sup` exist as processes). So `dawn.c` should create it —
a proposed `mkpath(NW_ROOT_MNT NW_EVIDENCE_DIR)`, one line beside the two
that already exist for brick/layer — guaranteeing it exists before
anything that needs it, including the earliest consumer. **Flat namespace, no
hex-prefix sharding**, matching `NW_BRICK_DIR`'s own precedent exactly
rather than inventing a second convention nobody asked for; if the
directory ever gets large enough for that to matter, sharding is a
mechanical change for whoever hits it, not a problem to solve now.

## 3. What's in the package

Fixed format, fixed max size, plain text (not a new blob.h struct — this
is a runtime diagnostic artifact, never read back by anything in the
TCB, so `nwcheck.c`'s validation rules and invariant 3's drift concerns
don't apply to it at all):

```
NWEVT1
unit=<name>
reason=exit|signal
value=<0-255>
death=<deaths>
budget=<budget>
tail_bytes=<0..NW_EVIDENCE_TAIL_MAX>
--
<tail_bytes raw bytes, verbatim, unparsed>
```

Header fields are all bounded-width (`unit` is at most `NW_NAME_LEN-1`
bytes, `reason` is one of two fixed strings, `value`/`death`/`budget` are
small unsigned integers, `tail_bytes` is bounded by
`NW_EVIDENCE_TAIL_MAX`), so the header has a fixed maximum size
(proposed: a 256-byte `snprintf` buffer, matching the existing `line[96]`
precedent in `nwsup.c` scaled up for the extra fields) and the whole
package's maximum size is `256 + NW_EVIDENCE_TAIL_MAX` — fixed, never
growing, checkable by reading the constants rather than by trusting a
comment.

**A house that never wrote anything** produces `tail_bytes=0` and an
empty region after the `--` line — the same code path as a tail-file
read failure (missing file, permission error): both mean "no tail
available," and `nw-sup` doesn't distinguish them. No special case
needed for "silent house" versus "capture path unavailable" — they
produce the identical, valid, empty-tail package.

## 4. Who writes it

**`nw-sup`, at the exact point it already composes the `restart`/`spent`
line** — the two `snprintf(line, ...)` + `say(line)` sites in `nwsup.c`'s
main loop. No new trigger, no new decision about *when* to write: the
same `if (budget == 0 || deaths > (int)budget)` branch that already
picks "spent" versus "restart" supplies every field the package needs,
computed from values `nw-sup` was already holding (`name`, `deaths`,
`budget`, `WIFEXITED(st)`, `WEXITSTATUS(st)`/`WTERMSIG(st)`) — nothing
new is computed, mirroring the comment already in that function
("Nothing new is computed... No new state, no new syscall, no new
field"). `nw-sup` opens the unit's `.tail` file (best-effort: a read
failure just means an empty tail, per question 3), composes the fixed
header, writes both to the `.evt` file, and continues to the existing
`say(line)`/loop-or-`_exit()` behavior **completely unaffected by
whether the evidence write succeeded**. Evidence-writing is a side
effect appended after the decision, never a precondition of it — see the
COMMITMENT-5 check below.

**`nw-sup` reading `.tail` and PID 1's logger writing it are two
independent, unsynchronized processes, and the first version of this
note ignored that entirely.** `tcb-review` measured the consequence
directly: a house's own final output missed its evidence package in
roughly 1 of 8 ordinary runs, worse under load, because `nw-sup` reaps
the death and reads `.tail` before the logger, scheduled independently
and descheduled for any reason, has drained and flushed the last
`read()` it already has sitting in the pipe. `control` reproduced the
same gap mechanically, by injecting a fixed half-second delay into the
logger's read loop right after its `read()` call and showing the tail
come back stale.

**A hard constraint narrowed the design space more than the first
attempt accounted for: `nwsup.c` may never read a clock, for anything,
regardless of purpose.** `runtime.md`'s Liveness section requires this
("a budget that can read a clock can reset on one, which is D18 coming
back") and `tests/run.py`'s `test_budget_is_hard_total` enforces it
mechanically, by grepping the whole file for `clock_gettime`, `now_ms`,
`alarm`, `nanosleep`, `usleep` and their relatives and failing if any
appear -- not scoped to the restart-budget code, deliberately, because
intent isn't something a grep can verify. A first build of the wait
below used `nanosleep()` between `FIONREAD` checks and `make test`
caught it immediately: `nwsup.c has regained a timing primitive:
['nanosleep']`. Found by running the actual gate, not by re-reading the
rule -- exactly the kind of thing this project's own record says gets
missed by inspection.

Three designs were tried in sequence, and every rejection below was
found by running the design, not by reasoning about it beforehand:

- **`stat()`-based quiescence** (poll `.tail`'s mtime/size, declare the
  logger caught up once a reading repeats unchanged). Wrong once
  tested: a single unchanged reading is also what "nothing has been
  flushed yet at all" looks like, so it declares victory before the
  first flush on a freshly-started logger, defeating the wait under any
  real delay.
- **`FIONREAD` plus stall detection, with `nanosleep()` between checks**
  (`ioctl(2, FIONREAD, &pending)` on `nw-sup`'s own copy of the log
  pipe's write end -- verified this reports the pipe's buffered-byte
  count correctly from the write side, not only the read side --
  combined with "keep waiting while `pending` falls, give up after N
  ticks of no change"). Wrong twice over: the stall-detection heuristic
  fails by reasoning alone (`pending` sits exactly as flat while the
  logger is genuinely stalled as while it is merely descheduled for a
  moment, so "unchanged for N ticks" cannot tell the two apart), and
  the `nanosleep()` it was built on is flatly forbidden in this file
  regardless of the heuristic wrapped around it, per the constraint
  above.
- **A pure `FIONREAD` busy-spin, no sleep, no yield, bounded by an
  iteration count instead of a tick count.** Compliant with the
  constraint (no clock read anywhere), but measured insufficient on its
  own: for a house that writes nothing before dying, where the only
  thing to capture was `nw-sup`'s own line written an instant before
  the wait starts, a plain spin reached `pending == 0` (confirmed by
  direct instrumentation: the logger really had read the bytes) and
  then read a genuinely empty tail 100% of 6 runs. `FIONREAD` reports
  that the logger has *read* a chunk, not that it has *flushed* it, and
  a spin with no yield lets `nw-sup` race straight back into the tail
  read before the logger's userspace gets to run `flush_tail()` for
  what it just read.

**What's adopted is two changes together, and the first is the one that
actually matters -- the second only narrows what remains after it:**

1. **Read the tail *before* announcing the death, not after.** The two
   call sites in `nwsup.c` were `say(line); write_evidence(...);`;
   they are now `write_evidence(...); say(line);`. This removes the
   tightest instance of the race outright rather than trying to win it:
   the previous order needed `write_evidence()` to capture a line
   `nw-sup` had *just* written into the same pipe an instant earlier --
   nothing but a handful of kernel-then-userspace instructions
   separates "written" from "flushed" for that specific byte, and no
   amount of polling closes a gap that narrow (see the third rejected
   design above -- that measurement is what forced this reordering,
   not a preference). What `write_evidence()` has to wait for now is
   only the **house's own prior output**, and, for a second-or-later
   death of the same unit, the **previous** death's own restart/spent
   line -- both already sitting in the pipe with an ordinary amount of
   real wall-clock time behind them (a `waitpid()` return, a
   `snprintf()`, at minimum) by the time `nw-sup` gets here, not
   written a moment ago.
2. **A `sched_yield()`-based `FIONREAD` spin, bounded by
   `NW_EVIDENCE_DRAIN_SPINS` (200000) iterations, not by time.** Loop
   `ioctl(2, FIONREAD, &pending)` on `nw-sup`'s own copy of the pipe's
   write end; if nonzero, call `sched_yield()` (give up the remainder
   of this timeslice to another runnable process -- reads no clock,
   takes no duration, reports no elapsed time) and check again, up to
   the iteration ceiling. No stall detection, no give-up-early branch.
   `sched_yield()` is what makes the difference over the plain spin: it
   gives the logger's userspace an actual chance to run `flush_tail()`
   between `nw-sup`'s checks, rather than `nw-sup` monopolising the CPU
   with pure polling.

**Consequence of reordering, stated plainly because it is a real
narrowing of what this feature captures, not a footnote: a death's own
tail never contains `nw-sup`'s own line for *that* death.** That line
is written by `say()` immediately after `write_evidence()` returns, so
it is never inside the package being written right now -- it shows up,
if anything does, at the head of the *next* death's tail for the same
unit (since the ring is never reset between deaths and that line has
since had a full house-lifetime to reach the tail file), or not at all
if there is no next death. A silent house under `budget=0` -- one
death, ever, nothing written before it -- now gets a genuinely empty
tail, which is the correct, honest answer for "nothing was captured",
not a surprise to explain away.

**Measured, not assumed, and the first measurement here overclaimed --
`tcb-review` ran more trials than I did and found the honest number.**
My own first pass (`test_evidence_captures_death_output` and
`test_evidence_silent_house_empty_tail`, 30 runs idle plus 25 runs
alongside three `yes > /dev/null` processes) came back 55 of 55 clean,
and the first version of this paragraph reported that as the mechanism's
measured reliability. `tcb-review` re-ran the same test at higher trial
counts and higher contention on the same machine and got real,
reproducible failures: with three `yes` processes pinning 3 of this
machine's 4 cores, 40 trials produced 2 failures and two separate
60-trial batches each produced 1; at six `yes` processes, 40 trials
produced 4 failures (roughly 2.5-10% depending on load, not 0%). My
55-trial sample was too small to see a race in that range -- not a
flaw in the mechanism the sample missed, a flaw in treating 55 runs as
enough to call something closed.

**So the honest claim is narrower than "closes it": the reorder removes
the dominant cause of failure and the spin narrows what's left, and
what's left is not zero.** `control` measured the two changes
separately and put a number on each one's share: with the reorder alone
and `NW_EVIDENCE_DRAIN_SPINS` cut to 2 (barely a wait at all), both
tests stayed green through 15 runs under load -- the reorder is doing
nearly all of the work. With the reorder kept but the wait loop deleted
entirely, `test_evidence_captures_death_output` failed 2 of 15 runs
under load (`test_evidence_silent_house_empty_tail` couldn't see this
one at all before it was given the paired positive case below). So the
wait loop's own contribution, isolated, is real but small next to the
reorder's.

**Impact, stated plainly rather than assumed:** `tcb-review` traced
every call site and confirmed `write_evidence()`'s outcome never
reaches `say()`, the restart/budget decision, or `_exit()` status --
this is diagnostic-only, exactly as question 4 and the COMMITMENT-5
check require, so a miss here produces a missing or truncated tail
region in one `.evt` package, never a wrong boot decision. It is worth
naming, not hiding, that the scenario this feature exists for -- a
crashing house -- correlates with elevated load (many houses
restarting or crash-looping at once), which is exactly the condition
under which this mechanism is least reliable. That is an argument for
what this feature is (best-effort diagnostics, stated as such
throughout this note) and not a case for building the cross-process
signalling this note already declined as disproportionate scope; there
is no bound on `NW_EVIDENCE_DRAIN_SPINS`, clock-free or otherwise, that
turns a probabilistic wait into a proof, and no measurement on this
machine is a claim about another one. A logger delayed past the spin
ceiling still gets a stale or empty tail; `NW_EVIDENCE_DRAIN_SPINS`
bounds how many times this checks, not how long that takes in
wall-clock terms on a given machine.

**`control` found a second, separate defect in the test suite itself,
not in the mechanism: `test_evidence_silent_house_empty_tail`'s empty-tail
assertion was an unpaired absence, exactly the shape `harness.md` names
by title.** Deleting the ring-buffer capture in `pid1.c` outright left
that test green, because an empty tail is what both "nothing to
capture" and "capture is broken" produce, and nothing in the test told
them apart. Fixed by baking a second house into the same city -- one
that writes real output under the same `budget=0` -- and asserting its
package has a genuine, non-empty tail before trusting the silent
house's empty one. Verified: reintroducing the same ring-buffer-deletion
mutation now fails this test on the paired assertion, every run.

**The honest failure rate above (2.5-10% under load) made both evidence
tests themselves too flaky for `make test` to be the gate it claims to
be, so both now retry through `_retry_evidence_race` (`tests/run.py`), a
bounded (8-attempt) retry of the whole bake-and-boot, not merely the
read.** The race is fixed the instant `nw-sup` writes the immutable,
content-hash-named `.evt` file, so re-reading the same file cannot
change its content -- only a fresh boot gets a fresh roll. This is
bounded specifically so it does not become a hiding place for a real
regression: reverting the reorder (question 4's fix) still fails
reliably after all 8 attempts, confirmed by running it, because that
mutation's failure rate is far higher than the accepted residual one
(`control` measured it at 8/8 to 7/8 depending on which assertion
catches it). A retry that could absorb *that* mutation too would be a
retry doing nothing.

**`fd-auditor` found no live bug, and one fragility worth naming: fd 2's
identity is asserted in `nwspawn.c` and never checked at the point of
use in `nwsup.c`.** `write_evidence()` calls `ioctl(2, FIONREAD, ...)` trusting
that fd 2 is still the unit's log pipe write end, a fact established
only by `nwspawn.c`'s `pack_kit()` doing `dup2(logn, 2)` before `execl`
into `nw-sup` and by nothing in `nwsup.c` ever closing or reassigning
it afterward. Today that holds (confirmed by tracing a real process
across five deaths under `strace`), and even a future violation would
degrade safely -- a non-pipe fd 2 makes `ioctl` fail or return 0
immediately, read as "already drained," never a crash or a misroute to
another unit's data. Still, this is the shape invariant 2 asks a
reviewer to distrust on principle: a fixed descriptor number whose
correctness depends on an invariant maintained in another file rather
than checked locally. `write_evidence()` now confirms it with
`fstat(2, &st)` plus `S_ISFIFO(st.st_mode)` before entering the wait,
falling back to no-wait (not a crash, not a hang) if the assumption
ever stops holding -- designing the assumption out rather than trusting
the comment to hold, per this project's own stated preference.

**A second, unrelated fix landed alongside this one: `.tail` is now
opened with `O_TRUNC`.** `tcb-review`'s other high-severity finding --
`NW_EVIDENCE_DIR` is machine-root and durable, the same way
`NW_BRICK_DIR` and `NW_LAYER_DIR` are, so a house name reused across a
later boot (or a later point in one supervisor's life) whose new
instance dies before writing anything would, without `O_TRUNC`, inherit
a previous instance's stale tail content silently. Reproduced exactly:
a same-named `/bin/true` death reporting a prior boot's unrelated
`unit-boom` crash bytes. `O_TRUNC` at the logger's open call scopes
tail content to "since this logger opened this file," which is the
boundary that already applies to everything else evidence-adjacent (a
fresh logger per boot, per unit).

## 5. Retention

**Explicitly out of scope this round.** `.evt` files accumulate on disk
forever, exactly the accepted-accumulation class `harness.md` already
documents for `NW_BRICK_DIR` and `NW_LAYER_DIR` images ("the images are
also never cleaned... they accumulate under NW_BRICK_DIR on the
machine"). A later fold/bakery pass could sweep old evidence — no such
tool exists, and building one is a separate, later feature (kind 3,
waiting on a prerequisite that doesn't exist yet: a policy for how old is
old enough, which is a decision, not an implementation detail). The
`.tail` file needs no retention question at all — it's a single,
constant-size file per unit, continuously overwritten in place, and
never grows.

## 6. Interaction with the channel just landed

**A `STOP` produces no evidence file at all, and this falls out of the
existing, already-mutation-tested control flow rather than needing new
detection logic.** The `stop_requested` early-`continue` in `nwsup.c`
sits *before* `deaths++` and both `snprintf`/`say(line)` sites — that
ordering is exactly what the previous round's `control` review
mutation-tested (`deaths++` structurally unreachable from the
`stop_requested` branch). Placing the evidence-write call at the same
two sites as the existing `say(line)` calls means `STOP` structurally
cannot reach it, with zero new branching to get this right or wrong.
**The same is true of a house killed by ordinary city shutdown** —
`nwsup.c` has three `if (stopping)` sites; the two that matter here are
the ones after `wait_house()` returns and before the death-accounting
code (the ones guarding each `_exit(WIFEXITED(st) ? WEXITSTATUS(st) : 0)`
that follows a shutdown-time kill), and both sit before this code. The
third `stopping` check guards against starting a new fork at all and
isn't part of this path. So a house terminated by a normal shutdown
produces no evidence file either, for the identical reason. Neither case
needed a decision here; both are consequences of code that already
existed and was already proven correct.

**The one honest caveat, inherited rather than reopened, and this note's
first draft got its own direction wrong.** The cross-generation
misdirection race decided in `docs/options/11-start-stop-channel.md`
means a `STOP` aimed at a dying generation can land on its freshly-forked
successor instead. Tracing the actual control flow (`nwsup.c`): the
misdirected kill still runs through `handle_ctl_live()`, which sets
`stop_requested` — so the successor's death hits the *same*
`stop_requested` early-`continue` this section already established
produces no evidence, not the ordinary death path. **The misdirected
generation's death therefore produces no evidence file either** — not
"an evidence package that exists but is misattributed," which is what
this note said until `claims` traced the code and found the opposite.
That's worse, not better: it's a second, silent instance of the exact
gap `docs/options/11-start-stop-channel.md` already named as unresolved
("a brand-new, healthy instance of the unit died... for a reason nothing
in the log records") — this feature was supposed to be the fix for
exactly that kind of silence, and for this one specific race it isn't.
Nothing here needs to solve the misdirection race itself (that stays
`docs/options/11`'s decided, accepted trade-off); it's flagged so a
reader doesn't assume this feature closes a gap it doesn't reach. The
*original*, correctly-attributed crash that triggered the restart in the
first place is unaffected and does get an evidence file, via the
ordinary death path, exactly as every other death does.

## 7. COMMITMENT-5 check

**Nothing here has `nw-sup` or PID 1 make a decision based on a
package's contents, because nothing here ever reads a package back.**

- `nw-sup` writes fields it already computed for the console line it was
  always going to print — no new computation, no branching on evidence
  history, no "if this house has died N times, do X differently." The
  restart/spent decision (loop back and fork again, or `_exit()`) is
  made **before** the evidence write and is unaffected by whether that
  write succeeds, per question 4.
- PID 1's logger mechanically mirrors every byte it reads into a fixed
  ring buffer and flushes it verbatim after every chunk — it never
  inspects what it's copying, never branches on content, and its one
  failure mode (can't open the tail file) permanently *disables* the
  side path rather than retrying or escalating.
- `nw-sup`'s wait for the pipe to drain (`write_evidence()`'s bounded
  `FIONREAD`/`sched_yield()` spin, see question 4) branches only on a
  **byte count**, never on content — it cannot see what is or isn't in
  the pipe, only how much, so there is no judgment call hiding in it
  either.
- Neither process ever opens a `.evt` file for reading. These are
  write-only from the TCB's perspective and read-only-by-an-operator
  artifacts — there is no code path anywhere in this design where the
  trusted core looks at what it wrote.

No proposal here adds judgment to the trusted core. It adds a mechanical
record of what the core already knew at the moment it already acted.
