# 14 — Shared content-addressed store

Status: **built and landed.** `store.c`/`store.h` and the migration
into `write_evidence()` are checkable against `store.c`, `store.h`,
`nwsup.c`, `Makefile`, `tools/mkboot.sh`, `tools/rules-hook.sh`,
`tests/store_probe.c` and `tests/run.py` as they stand. `make test`
passed clean three times across the review cycle below (twice before
`control`/`tcb-review`, once after fixing `tcb-review`'s HIGH finding);
`claims` reviewed an earlier draft of this note and found a real
overclaim of this same status line before there was anything to land,
plus a stale line citation and a control missing its stated
red-mutation, all fixed at the time; `control` and `tcb-review` then
reviewed the built code, `tcb-review` found one HIGH finding (fixed;
see Review findings below) and `control` found this note overclaiming
two of its own controls' scope (also fixed below, in the controls'
own paragraphs and in the affected tests' docstrings). This note
answers the seven questions the brief asked and states the
COMMITMENT-6 check explicitly.

## A process deviation, flagged rather than hidden

The brief said design questions first, `claims` review of the note,
*then* code. What actually happened: the two existing content-
addressing implementations (`bakery/mkbrick.py`'s `pack()` and
`nwsup.c`'s `write_evidence()`) were read in full before a line of this
note was written, and by the time their shared shape was understood
well enough to answer question 2 honestly, the shared primitive itself
(`store.c`/`store.h`) and its migration into `write_evidence()` had
already been written alongside that reading, not after it. `claims`
was dispatched on this note before `make test`, `control` or
`tcb-review` ran, and before any commit, which is the checkpoint the
brief asked it to sit at even though code preceded the note rather than
the reverse. The deviation is naming code that already exists in the tense the code
is actually in, rather than a proposal in a tense the next edit would
immediately falsify — which is exactly the trap `docs/options/13-
crash-relaunch.md`'s own review history hit once already (a stale
future-tense paragraph left standing after the mechanism it described
was built). Writing this note against the real, already-built shape
avoids reproducing that finding here.

## The two existing implementations, read before designing anything

**`bakery/mkbrick.py`'s `pack()`** (bake-side, not TCB): builds an erofs
image from a directory tree into a `tempfile.mkstemp()` name in the
target directory, hashes the finished file, then `os.replace(tmp,
os.path.join(out_dir, digest + suffix))` — unconditional, because
"[s]ame content, same name: a rebuild is a no-op rather than an error.
Content-addressed storage has no update, only arrival" (the function's
own comment, `bakery/mkbrick.py:163-164`).

**`nwsup.c`'s `write_evidence()`** (live-side, TCB), before this
change: composed the evidence record in memory, hashed it with the
vendored `nw_sha256()`, opened a `mkostemp()` temp name in
`NW_EVIDENCE_DIR`, wrote it, then `rename()`d it unconditionally onto
`<hex>.evt` — the identical shape, in C, written independently. Its own
comment named the reason for the unconditional rename in the same
words mkbrick.py's does: "the target, if it already exists, is already
known by the hash itself to hold identical content."

Two things follow directly from reading both side by side, and both
answers below rest on them rather than on a general argument:

- **Neither implementation checks for existence before doing its
  expensive work.** mkbrick.py cannot — the hash depends on the built
  image, which only exists once `mkfs.erofs` has already run.
  `write_evidence()` *could* have — the record is fully composed in
  memory, and its hash is knowable, before anything touches disk — and
  simply never did.
- **Both already produce exactly one file per hash on disk**, because
  an unconditional rename onto a name that already holds identical
  bytes is a no-op in every way that matters. What differs is not the
  end state; it is whether the write was actually skipped, which
  matters only for cost, not correctness — and it is the one place a
  shared primitive can do strictly better than either existing
  implementation without changing what either produces.

## 1. Fold bricks/layers in, or a new mechanism for what doesn't have a home yet?

**The latter, as the brief's own default recommended, and nothing found
while reading the two existing implementations argues against it.**
`NW_BRICK_DIR` and `NW_LAYER_DIR` stay exactly where they are:
`bakery/mkbrick.py` keeps hashing whole erofs images built from a
directory tree, `nwsup.c`'s brick/layer mounting is untouched, and
`plan.md`'s and `runtime.md`'s existing rules about them (content-
addressed by image hash, keyed by a declared layer id, `NW_E_LAYERPAIR`,
the loop-device retry) apply to code this change does not touch at all.

What is new is scoped to content whose bytes are fully known before
the destination write — the evidence-package shape, not the brick
shape — which is exactly `write_evidence()`'s case and not
`mkbrick.py`'s. A future artifact type that, like a brick, can only be
hashed after an expensive build step does not fit this primitive any
more than `mkbrick.py` does, and is out of scope for the same reason.

## 2. What's the actual primitive?

**A single function, not a directory, not a manifest, not a service.**
`store.h`:

```c
int nw_store_put(const char *dir, const void *data, size_t len,
                  const char *suffix, char out_hex[65]);
```

It takes a buffer already in memory and a destination directory (a
parameter, not a fixed path — `NW_EVIDENCE_DIR` is one caller's choice,
not the function's), hashes it, and writes `<dir>/<hex><suffix>` if
that name is not already there. Nothing about `dir` or `suffix` is
baked into the function; a future artifact type picks both for itself.
There is no index, no listing, no metadata beyond what is already
implicit in a directory of hash-named files — the same "no manifest"
shape both existing implementations already have.

**No Python twin ships this round, and that is a deliberate absence,
not an oversight.** The only real second consumer this round
(`write_evidence()`) is C. Building a Python-side helper now, with no
real bake-side consumer to prove it against, would be exactly the
"designed for a hypothetical one" shape question 7 warns against.
`bakery/mkbrick.py`'s `pack()` already *is* an instance of the
atomic-rename half of this pattern — it has been since before this
note — so a future bake-side artifact type has real, existing code to
imitate deliberately, the same way `write_evidence()`'s own prior
implementation gave this round something concrete to lift out rather
than invent.

## 3. Where does dedup happen?

**At the write path, checked before the write, and that is the one
place this differs from either existing implementation rather than
merely factoring one of them out.** `store.c`:

```c
struct stat st;
if (stat(finalpath, &st) == 0)
    return 0;
```

before any `mkostemp`/`write`/`rename`. This is real dedup in the sense
question 3 asked for — the second write of identical content does no
I/O at all, not merely "converges to the same bytes after doing the
work twice." The distinguishing, checkable signal is the destination's
inode: a skip leaves it untouched; a write-then-rename always installs
a new inode (the temp file's) at that name. `test_store_dedup_writes_
exactly_once` asserts the inode is unchanged across two identical
writes, not just that one file ends up on disk — the weaker assertion
would pass even if the skip branch were deleted, since the unconditional
rename underneath it converges to the same end state either way.

The check is a plain `stat`, not a lock, and it can race a concurrent
writer of the *same* content — see question 6 and the concurrent
control below for why that race is safe rather than merely unlikely.

## 4. Cross-machine sync

**Out of scope, deliberately, and nothing below designs toward it.** No
network transport, no sync protocol, no assumption that a future one
would look any particular way. If the local mechanism is well-defined —
one directory, one hash, one name — sync is a problem layered on top of
it later, by whoever needs it, and this note stops here rather than
gesturing at that layer.

## 5. Who owns this?

**Live-side, for the only real consumer this round.** `nw_store_put()`
is called from `nwsup.c`'s `write_evidence()`, which runs during a real
boot as part of the TCB — the same caller, same directory
(`NW_EVIDENCE_DIR`), same trigger (a house's death) as before this
change. Nothing about directory creation changes: `dawn.c` already
creates `NW_EVIDENCE_DIR` before `nw-sup` exists (because PID 1's
logger needs it first), and that is unaffected — `store.c` does not
create the directories it is pointed at; a caller that names a
directory nothing has created gets `stat()`/`mkostemp()` failing with
`ENOENT`/`ENOTDIR`, which surfaces as `nw_store_put()` returning `-1`,
exactly like handing it a bad path today would.

**Bake-side has no owner this round**, per question 2's answer above —
there is no Python implementation to own yet, because there is no real
bake-side consumer.

**Confirmed: the evidence-package live-side case is not regressed.**
`write_evidence()`'s caller-visible behavior — which directory, which
suffix, which header format, which bytes end up in the file, when a
package is (and is not) produced — is unchanged; only the four lines
that used to hash-then-mkostemp-then-write-then-rename by hand are
replaced by one call into the shared function. Confirmed by reading the
diff, not merely by assertion: the record composition, the drain wait,
the header format and the `D12`/tail-capture logic above it are
byte-for-byte untouched; only the block after `record`/`total` are
computed changed.

## 6. COMMITMENT-6: does this add judgment to the TCB?

**No, and here is the whole of what `nw_store_put()` decides:** does
`<dir>/<hex><suffix>` already exist. That is a mechanical existence
check, not a preference between artifact types and not a choice about
which one matters more. There is no branch anywhere in `store.c` that
asks what kind of content it was given, and none that could — the
function never sees anything but a buffer, a length, a directory and a
suffix, all supplied by the caller.

**No cleanup, no eviction, no retention policy — matching the existing
`NW_BRICK_DIR`/`NW_LAYER_DIR`/`NW_EVIDENCE_DIR` precedent of
accumulating forever.** Nothing added here changes that: whatever
directory a caller points this at keeps growing exactly as it already
did, one file per distinct hash, forever. If that becomes a problem for
some future caller, that caller's own retention decision is exactly
that — the caller's, made outside this function, the same way nothing
in the existing evidence-package or brick code cleans up after itself
today.

**The one place a judgment call was possible and was refused**: what
happens when a concurrent writer of *different* content somehow landed
identical bytes is not this function's problem to detect (that would be
detecting a hash collision, which is a cryptographic property of
SHA-256, not a policy question), and what happens when a caller's
`stat()` result is stale by the time it renames is answered by "rename
converges to whichever writer's identical bytes landed last" rather
than by any preference — see the concurrent-write control below.

## 7. Migration this round

**Yes — `write_evidence()` now calls `nw_store_put()`, and there is no
follow-up round for this.** The alternative (define the mechanism,
migrate later) was rejected for the same reason question 7's own
recommendation gives: shipping the primitive with no real consumer
proves nothing about whether its contract actually fits a real,
already-shipped caller's needs, versus one imagined while writing it.

## What was built

- **`store.h` / `store.c`** (new, TCB by inclusion into `nw-sup`'s
  build — justified below): `nw_store_put()`, exactly as described in
  question 2/3.
- **`nwsup.c`**: `#include "store.h"`; `write_evidence()`'s hand-rolled
  hash/mkostemp/write/rename block replaced by one call.
- **`Makefile`**: `nw-sup`'s recipe gains `store.c store.h` as
  prerequisites and `store.c` as an extra source, the same way
  `sha256.c` was added when evidence packages landed.
- **`tools/mkboot.sh`**: its isolated static-build file list (a
  hand-written list for files at the repository root, unlike its
  already-globbed `houses/*.c`/`tools/*.c` handling) gains
  `store.c store.h`.
- **`tests/run.py`'s `test_build_is_reproducible`**: needed no change —
  its own top-level source list is `os.listdir(ROOT)` filtered by
  extension, already a glob, so `store.c`/`store.h` are picked up
  without edits. (This is where `tools/unit-info.c` was *not* picked up
  automatically during item #4, because that file's list was still a
  hand-written subset at the time; it is a glob now, for both the
  top level and `tools/`, so this file class of omission cannot recur
  here.)
- **`tools/rules-hook.sh`**: `store.c`/`store.h` added to the
  `"runtime"` territory (same set `sha256.c`/`sha256.h` are in, same
  reasoning); `tests/store_probe.c` added to `UNOWNED`, worded like the
  existing `tests/sha256_vectors.c` entry.
- **`tests/store_probe.c`** (new, not TCB, not a house): a small CLI
  wrapping one `nw_store_put()` call, with an optional gate path it
  spin-polls for *before* calling it (never inside `store.c`), used
  only by the concurrent-write control to synchronize two processes
  more tightly than a fixed sleep does.
- **`tests/run.py`**: `_build_store_probe()`, `_store_put()`, and four
  tests — `test_store_dedup_writes_exactly_once`,
  `test_store_name_match_is_not_trusted_as_content_match` (added after
  `tcb-review`'s finding below, not part of the original four
  questions' controls), `test_store_different_content_never_collides`,
  `test_store_concurrent_double_write_is_safe` — registered in
  `main()`'s test list.

**Why `store.c`/`store.h` may be added to the TCB.** `CLAUDE.md`:
"[a]nything added to a TCB file needs a stated justification. Anything
that can live in the baker instead, does." This cannot live in the
baker: it is called synchronously, during a real boot, from inside
`nwsup.c`'s live write path. The baker runs offline, before any boot
exists to write evidence about. The same justification `sha256.c`
already carries for the identical reason (`nwsup.c` needs a sha256
implementation live, and the baker's Python one cannot be called from
C at boot time).

## Controls, red then green

**1. Writing the same content twice results in exactly one file on
disk.** `test_store_dedup_writes_exactly_once`. Red under: deleting the
`stat()`-and-return-0 branch in `store.c` (every write becomes
unconditional, so the second call reports `rc=1` instead of `rc=0`,
and the destination's inode changes on the second call — both asserted
directly, not inferred from file count alone, so a mutation that keeps
the file count right by accident cannot pass).

**2. Different content never collides.** `test_store_different_content_
never_collides`. Two distinct pieces of content into the same
directory and suffix; asserts two distinct hex names, exactly those two
files present, and each file's bytes matching its own input (not the
other's) — the same shape `expect(one != two, ...)` already checks for
two different bricks, applied here to the shared function instead of
to `mkbrick.py`'s own hashing. Measured red under a mutation that makes
`nw_sha256()` ignore its `len` argument (hashing every input as if it
were empty): both pieces of content then hash identically, so the
second `nw_store_put()` call reports "already present" (`rc=0`) instead
of a fresh write, and `expect(rc1 == 1 and rc2 == 1, ...)` fails first.

**3. Existing evidence-package tests still pass, unchanged in
behavior.** Every evidence test already in the suite before this
change — `test_evidence_captures_death_output`,
`test_evidence_silent_house_empty_tail`, `test_evidence_stop_produces_
none`, `test_evidence_ring_buffer_is_bounded` — reads `.evt` files by
scanning `NW_EVIDENCE_DIR` for hash-named files and parsing their
header; none of them constructs a hash, a temp path, or a rename
itself, so none of them can tell the difference between the old
hand-rolled write and the new shared call. Confirmed by running them
after the migration (below), not merely by this argument.

**4. A concurrent double-write of identical content doesn't corrupt
either copy or crash either writer.** `test_store_concurrent_double_
write_is_safe`. Two `store_probe` processes, same content, synchronized
by a shared gate path each spin-polls for and the test releases only
once both are confirmed alive — not a fixed sleep, which was tried
first and measured unreliable: two processes given the same `usleep()`
duration wake up within the scheduler's own jitter of each other, which
is consistently *wider* than the microseconds `nw_store_put()` itself
takes for a small buffer, so in every timed run tried one process's
whole `stat`-`write`-`close` sequence finished before the other's timer
even elapsed — no real overlap. The gate forces both into
`nw_store_put()` within the polling loop's own granularity of each
other instead, close enough to actually interleave, and only inside
`store_probe.c` — never a delay inside `store.c` itself.

Asserted: neither process exits non-zero, both report the same hash,
exactly one file exists afterward, and its bytes are exactly the
content written — not truncated, not doubled, not corrupted. This is
safe by construction rather than by luck: `rename(2)` onto an existing
name is atomic on a single filesystem, and the only way either racing
writer can land is with the complete, identical bytes the other one
would also have written, so there is no interleaving that produces a
partial file. No lock is taken anywhere in `store.c`.

**Measured, not merely argued — and re-measured by `control` under load,
which changed what this paragraph can honestly claim.** A mutation
replacing the temp-file-then-rename block with a direct
`open(finalpath, O_EXCL)` — a plausible "simplification" that drops the
atomic-swap shape and lets the loser of the race collide on the
kernel's own exclusive-create check instead of converging — was caught
in 4 of 5 runs under the gated version of this test (`rc1=-1 rc2=1` and
the reverse), each with the exact command quoted in this note's own
history: `python3 tests/run.py --only
store-concurrent-double-write-is-safe`, repeated. It was caught in 0 of
5 (and 0 of 8, across two batches) under a first, sleep-based version
of the same test, which is why the gate replaced the sleep before this
control was trusted.

**That catch rate is conditional on the machine being otherwise idle,
and this note originally stated it unqualified.** `control` re-measured
the same `O_EXCL` mutation on the same machine under four CPU-bound
busy loops (`nproc`=4) and got 10/10 at idle immediately before, then
1/10, then a repeated 0/10, under that contention — diagnosed as the
two racing processes being released from the gate together but then
scheduled far enough apart that one runs its entire `nw_store_put()`
call to completion before the other is scheduled at all, so they
serialize instead of interleaving and there is nothing left to catch.
This is a property of the CONTROL's reliability as a regression
detector under load, not of `nw_store_put()` itself — the real
implementation is correct regardless of scheduling — and it is exactly
the corollary `CLAUDE.md`/`harness.md` already name for a test whose
outcome depends on the environment: say so, rather than quote an
unqualified number. The test's docstring now states this explicitly;
treat an `ok` here on a busy machine (a shared CI box, or another
agent's suite running concurrently — `harness.md` already documents
this tree being run alongside others) as weaker evidence than the same
line on an idle one, and do not cite the "4 of 5" figure without this
qualifier attached.

A second mutation — writing in place with `O_TRUNC` instead of a temp
file, no `rename` at all — passes every time, under every
synchronization mechanism tried, at idle. With byte-identical content
on both sides, no interleaving of two `write()` calls carrying the same
bytes at the same offset can produce a *different* final byte at any
position, so the FINAL state this test inspects (after both racers have
exited) cannot show corruption under this mutation no matter how
tightly the race is forced. `control` pushed on the stronger claim this
note first made — that the mutation is "structurally unobservable" in
general — and that overclaims by scope: `O_TRUNC` at `open()` time does
reintroduce a real window where the file at `finalpath` is briefly
empty or truncated before the `write()` completes, and a THIRD reader
polling that path throughout the race could plausibly observe it, even
though this test's own two assertions (the racers' exit codes, and the
directory's contents after both are done) cannot. `control` attempted
to construct exactly such a third-reader probe and did not manage to
catch a transient bad read in 5 attempts on this machine's filesystem
(the window is apparently sub-microsecond for a buffer this size) — so
this remains a **HYPOTHESIS**, not a demonstrated finding: unobservable
from this test's vantage point, not established as unobservable in
general. The property this control actually establishes is narrower
than "any non-atomic implementation is caught" — it is "an
implementation whose race can produce a hard failure or a torn write
*visible in the final state* is caught, on an otherwise-idle machine,"
which is what an `O_EXCL` collision or a partial `write()` would be.
`real dedup + identical content` is the case in front of this project's
evidence packages (two concurrent boots computing the same death), not
a general non-atomicity fuzzer or a transient-read detector, and the
control is honestly scoped to that — narrower than the first version of
this paragraph said, in the two ways `control` found.

**Run, not merely argued for.** `make test` (`make stage` first, per
`harness.md`'s staging trap):

```
ok store-dedup-writes-exactly-once
ok store-different-content-never-collides
ok store-concurrent-double-write-is-safe
ok evidence-captures-death-output
ok evidence-silent-house-empty-tail
ok evidence-stop-produces-none
ok evidence-ring-buffer-is-bounded (peak 952 KiB against a 16384 KiB ceiling, 64 MiB written)
...
PASSED, WITH SKIPS -- this environment could not exercise:
  dawn-real-boot:vfat-esp: kernel has no FAT driver ...
31 checks: 31 pass, 0 fail, 0 skip
coverage-tcb: nwcheck.c 99% of executable lines (floor 99%)
[exited with code 0]
```

The one skip is the pre-existing, unrelated vfat-ESP environment gap
`print_environment()` already names — nothing this change touches. All
four evidence-package tests pass unchanged, confirming control 3.
Red-under-mutation for controls 1, 2 and 4 is quoted in each control's
own paragraph above, from runs against a scratch copy of the tree with
`__pycache__` cleared, matching `harness.md`'s bytecode-cache trap
guidance for a Python-side mutation (not applicable to these C
mutations directly, but followed anyway since the harness invoking them
is Python).

## Review plan

`fd-auditor` is **not** being dispatched. `store.c` does exactly what
question 6 says it may: `stat`, `mkostemp`, `write`, `close`, `rename`,
`unlink` — no `dup2`, no fixed descriptor numbers, no file locking
anywhere (the concurrent-write control's safety comes from `rename(2)`
being atomic, not from any lock this code takes). If review finds a
descriptor-handling concern this note missed, that finding itself would
be the reason to dispatch it after the fact, not a reason to dispatch
it pre-emptively against a design with none of the triggers `runtime.md`
and `CLAUDE.md` name for that agent.

`control` and `tcb-review` are dispatched in parallel: this touches
`nwsup.c` (TCB) and, per `runtime.md`'s own scope line, `dawn.c`'s
territory is implicated by the directory-creation reasoning in question
5 even though no line of `dawn.c` changed — flagged for the reviewer to
confirm rather than asserted here as settled.

## Review findings, and what changed because of them

**`tcb-review` — HIGH, reproduced, fixed.** The first version of
`nw_store_put()` trusted `stat(finalpath, &st) == 0` alone as proof the
existing file already held the requested content. It does not: the
name is derived from bytes the caller composed, and for the one real
caller (`write_evidence()`) those bytes are fully deterministic and
computable in advance from the plan a house was baked from (`unit`,
`reason`, `value`, `death`, `budget` are all knowable, and a house
controls its own exit and its own last output, i.e. the tail). Any
unconfined co-resident house — no `brick=`, no Landlock, uid 0, sharing
the same `/nw/evidence` on the machine root, which invariants 5/6 make
the common case rather than an edge case — can plant arbitrary bytes at
the exact path its own next evidence record will land at, before that
record is ever written. The first version's dedup check then saw
`stat()` succeed, returned `0` ("already present"), and never wrote the
real record — silently and permanently, since nothing later ever
revisits an already-"present" name. `tcb-review` reproduced this
directly against the real `store.c`, planting garbage at a precomputed
hash path and getting `rc=0` back with the garbage left in place.

This is `plan.md`'s bug 1 one file over: "the seal must be VERIFIED,
not merely read," here about a name rather than a CRC. The code this
change replaces did not have this weakness — its write was
*unconditional* (temp file, then rename, always), so any pre-planted
content at the destination was clobbered by the real bytes every time.
The dedup optimization this note's question 3 asked for traded that
self-healing property away, and neither an earlier draft of this note
nor the three controls it originally described exercised the case
(every one of them only ever writes through `nw_store_put()` itself, so
the target directory only ever contains correctly-hashed content in
every one of them).

**Fixed by verifying content, not trusting the name.** `store.c`'s
`nw_store_put()` now only skips the write when an existing file at the
destination is confirmed, byte-for-byte, to already hold the requested
content (`nw_store_content_matches()`, reading the existing file back
in fixed `NW_STORE_CMP_CHUNK`-sized chunks against the caller's buffer
— bounded regardless of `len`, so this needs no allocation and no
variable-length stack buffer). A size mismatch, a content mismatch, or
a file that cannot even be opened to check are all treated identically
to "nothing was really there" and fall through to the unconditional
write-then-rename, restoring the self-healing property the code being
replaced already had. Genuine dedup — identical content actually
already present — still skips the write; only a *mismatch* now forces a
real write where the first version silently did not.

**`test_store_name_match_is_not_trusted_as_content_match`** is the
regression test for this, added directly in response to the finding: it
plants a same-length-but-different-bytes poison (the case a
size-only check would miss) and a different-length poison at a
precomputed hash path, asserts both provoke a real write (`rc=1`) whose
result is the real content, and separately confirms genuine dedup
(identical content actually present) still returns `rc=0` — so the fix
cannot be a mutation that simply always rewrites. Verified red under
the original (vulnerable) code in a scratch copy: `FAIL: a same-length
content mismatch must be a real write (rc=1), got rc=0 -- a name match
was trusted as a content match`. Verified green against the fix, and
against the pre-existing three controls, which are unaffected by this
change (all three only ever write matching content, so the new
content-verification branch they exercise always takes its "matches"
path, identically to before).

**`fd-auditor`'s exclusion stands.** `tcb-review` noted the finding
above is a trust/TOCTOU issue in its own territory, not a
descriptor-handling one, and independently confirmed `store.c`'s fd
handling is clean (the one `mkostemp()` fd — now joined by one
`open(O_RDONLY)` fd in the content-verification path — is closed on
every path including both failure branches; no `dup2`; no fixed
descriptor numbers). Not dispatching `fd-auditor` was the right call for
the reason given, not merely lucky.

**Everything else `tcb-review` and `control` checked was confirmed
accurate**, including: the migration is behavior-preserving apart from
the write mechanism itself (record composition, the drain-wait logic,
the header format, and the tail-capture logic above it are byte-for-
byte untouched); `_GNU_SOURCE` is load-bearing for `mkostemp` (confirmed
by removing it and seeing the implicit-declaration warning return);
`dawn.c` still creates `NW_EVIDENCE_DIR` unchanged; `write_evidence()`
is called only from the ordinary supervise loop, never from a signal
handler, so there is no async-signal-safety concern; and all three
original controls, plus the four pre-existing evidence tests, hold
after the fix. `control`'s own mutation passes (deleting the dedup
branch entirely still turns `test_store_dedup_writes_exactly_once`
red) and its check of the concurrent-write gate mechanism found it
reliable across repeated runs.

One LOW/robustness note from `tcb-review`, not treated as blocking:
`tools/coverage-tcb.sh` only measures `nwcheck.c`, so `store.c` — now
the only new TCB file with non-trivial logic — has no coverage-floor
enforcement. Pre-existing tool scope, not introduced by this change;
left as-is rather than widening the coverage tool's own scope inside
an unrelated item.

**`control` — mutation-tested every new/changed test, and found the
shipped mechanism sound but two things about how this note DESCRIBED
its own tests wrong.** Controls 1 and 2 (the dedup-stat branch, and
`nw_sha256` actually hashing over `len`) were confirmed exactly as
described — same mutations, same failures quoted. The evidence-
migration controls were also confirmed: skipping the `nw_store_put()`
call entirely, and truncating the buffer passed to it while the header
still claims the real `tail_bytes` count, are both caught by the
pre-existing evidence tests, which `control` confirmed exercise real
byte-level content (tail bytes, `reason`/`value`/`death`/`budget`) and
not merely a file's existence.

**What was wrong is covered above rather than asserted twice: the
concurrent-write control's catch rate is load-dependent (this note
originally quoted "4 of 5" with no qualifier), and the `O_TRUNC`
mutation's "structurally unobservable" framing overclaimed by scope
(unobservable from this test's own assertions, not demonstrated
unobservable in general).** Both are fixed in this control's own
paragraph above and in the test's docstring, rather than repeated here
— this section exists so a reader auditing the review record finds
both findings attributed to their source, not so the correction is
written a third time. `control` also caught a real, if non-blocking,
diagnostic-quality issue in the pre-existing `_retry_evidence_race`
helper: under the "skip the call" mutation, its fixed-per-process house
name means retries 2..N can land on evidence packages the shared
store's own real dedup correctly recognises as already on disk (byte-
identical to what a genuine first failure produced), so the exception
this helper raises can quote the wrong attempt's message. The retry
still fails overall — no regression is masked as a pass — but a future
debugger would be pointed at the wrong assertion; `_retry_evidence_
race`'s own docstring now says so and says how to isolate the real
first failure.
