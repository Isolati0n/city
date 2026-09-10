# Plan 02 — Making the build provable

Status: **proposed, not started.** Written 2026-09-10. One item needs a
decision because it changes a TCB file; the rest is test and build work.

Everything this system trusts passes through `nw_check`. Invariant 8 says so
explicitly: CRC32 is diagnostic, and *the structural checks are the actual
safety property*. So "provable" has a precise target — if `nw_check` is
proven, the runtime's entire trust assumption is grounded, and every
downstream component is reading a table that has been established correct
rather than one that has merely survived the tests we thought to write.

## What "provable" can honestly mean here, and what it cannot

Four different things get called proof, and conflating them would be this
project's characteristic failure applied to its own verification story.

1. **Machine-checked, all inputs.** A solver explores every input up to a
   bound and reports no counterexample exists. **Demonstrated for the leaf
   validators; not yet achieved for `nw_check` as a whole** — see the three
   failed runs below.
2. **Exhaustive.** The input space is small enough to enumerate, so testing
   *is* proof for that component. Available for the small validators.
3. **Differential.** Two independent implementations agree on every input
   tried. Not proof, but it is the only technique that catches rule *drift*,
   which invariant 3 says is this project's recurring defect.
4. **Reproducible.** The artifact is bit-identical from source, so what runs
   is what was reviewed. Available now, and nearly true already.

**None of these reach the runtime.** `nwsup.c`'s mount, pivot and seccomp,
and everything `dawn` does, depend on kernel behaviour that no solver models.
They need real execution, and two of them currently execute in **no**
environment either machine has (`coverage/` records this). That boundary must
stay stated, or "provable" becomes the kind of confident sentence this
repository keeps finding next to code that does not do what it says.

## Measured baseline — the facts this plan is costed against

All run here today.

**`nwcheck.c` line coverage is 83%**, from every blob a full suite run
leaves behind plus 400 byte-flips. Nobody had measured it, and the metric
depends on the corpus, so it is pinned to one context: immediately after
`tests/run.py`.

*An earlier draft of this line said 79% and explained the gap against
gcov's 83.33% as a corpus difference. That explanation was invented. The
real cause was a bug in the measuring script: gcov marks a line whose
branches are only partly taken as `403*:`, the line-counting regex missed
the asterisk, and 24 of 108 executable lines fell out of both numerator and
denominator. The script now reads gcov's own figure instead of recomputing
it. Recorded rather than quietly corrected, because inventing a plausible
cause for a discrepancy is the same failure as asserting a mechanism works
without running it.*

Eighteen lines never execute, and which ones is the finding:

- **`nw_crc32` is dead.** Exported in `blob.h`, defined in `nwcheck.c`,
  called by nothing — `grep` finds only the declaration and the definition.
  Meanwhile `nw_check` carries its **own inlined copy** of the same CRC loop.
  A second copy of an algorithm in a TCB file, which is the exact class
  invariant 3 exists for.
- **`NW_E_DUPNAME` has never been produced by `nw-check`.** The whole
  duplicate-name comparison body is unexecuted. `baker-reject-dupname`
  rejects at the *baker*; the TCB's own duplicate detection has never run.
- `NW_E_KIND`, `NW_E_LIDS` and `NW_E_LLBRICK` are never reached by the
  existing corpus either.

**The build is bit-reproducible in place and not across directories.** Two
clean builds in the same tree produce byte-identical binaries for all of
them. A build of the same commit in a different directory does not: the
absolute source path leaks through debug info. `-ffile-prefix-map=$(CURDIR)=.`
fixes it — verified, two directories, identical hashes.

**Tooling:** `gcov`, `clang` and `java` are present; `cbmc` (5.95.1), `afl++`
and `lcov` install from the distribution. Nothing here needs a network
dependency at runtime.

## The structural finding that unlocks the proof

CBMC on `nw_check` **as it stands does not terminate** — killed at fifteen
minutes. The inlined CRC loop runs `8 × (len − 20)` times, about 2,100
iterations for a one-unit blob, and unrolling it symbolically swamps the
solver.

**CORRECTION — this section previously said that with the CRC abstracted
"the same proof completes in minutes". That was false, and it was committed.**
Three runs, none of which proved anything:

| run | configuration | outcome |
|---|---|---|
| 1 | CRC stubbed, `--unwind 200`, blob filled by a loop | "1 of 354 failed" — but the failure was the harness's own 282-iteration fill loop hitting the bound, so the last 82 bytes stayed **concrete zeros**. The unwinding assertion was CBMC saying the exploration was incomplete. Not a proof; I read it as an artefact. |
| 2 | fill loop replaced by `__CPROVER_havoc_object`, so every byte truly free | **timeout at 13 min**, still unwinding the duplicate-name probe loop at iteration 27 of 128 |
| 3 | as 2, probe loop bounded to 3 with justification | **timeout at 25 min**, still in symbolic execution — `path_ok_len` unwound over 3,000 times across its call sites |

Run 1 is the instructive one: an unwinding-assertion failure is not a
harness nuisance, it is the tool reporting that the input space was
silently narrowed. Reading it as noise is how a bounded proof becomes a
claim it cannot support.

**What does verify, measured:** the leaf validators, in seconds.

```
path_ok_len   0 of 68 failed   VERIFICATION SUCCESSFUL    6.0s
name_ok       0 of 32 failed   VERIFICATION SUCCESSFUL    0.8s
```

Over *all* inputs of the field width, with bounds, pointer validity, signed
overflow and unwinding completeness — plus the post-conditions that an
accepted path is absolute and NUL-terminated within the field. That
includes the `s[i+3]` reads in the `..` guard, which had only ever been
argued safe by hand.

So the shape of a proof here is **compositional**: prove the leaves, then
prove `nw_check` with the leaves abstracted, exactly as the CRC is
abstracted. That is now evidenced at the leaf level and **untried at the
caller level** — it is the next thing to run, not a claim.

One note so it is not re-reported as a defect: with `--conversion-check`,
`path_ok_len` fails on `(unsigned char)s[n]`, a value-changing signed-to-
unsigned conversion. That is well-defined in C and deliberate here — it is
how bytes ≥ 0x80 are accepted. The check is a lint, not a soundness
property, and it is left off.

The CRC abstraction is still right, and it is worth being precise about why:
invariant 8 already says the CRC is diagnostic and the structural checks are
the safety property. Proving the structural guarantees hold *for an arbitrary
checksum result* is **stronger** than proving them for one particular
checksum, not weaker. The CRC's own correctness is a separate, easy
obligation — differential test against `zlib.crc32`, which the baker already
uses.

**So one change fixes both problems at once**: have `nw_check` call the
`nw_crc32` that already exists instead of inlining a duplicate. That removes
the second copy of the CRC from the TCB *and* makes the validator
model-checkable. **This is the item that needs a decision**, because it edits
`nwcheck.c`. It is a small change with an unusually good ratio, and the gate
requires `tcb-review` before it is pushed.

## Tier A — prove `nwcheck.c` (CBMC)

The harness makes the whole blob nondeterministic and calls `nw_check`, so
the solver considers every possible input of that length rather than a
sample. Checked: bounds, pointer validity, signed overflow, conversion,
undefined shift, and loop-unwinding completeness — plus post-conditions that
say what the runtime is entitled to assume when the answer is `NW_OK`:

- an accepted unit name is NUL-terminated within `NW_NAME_LEN`
- an accepted `exec_path` is absolute
- an accepted spare byte is zero
- a brick implies the `NEWNS` lid
- landlock implies a brick

**Compositional, because monolithic does not work.** Prove each leaf over
all inputs of its field (done, seconds), then prove `nw_check` with the
leaves and the CRC stubbed as nondeterministic — so the caller's proof must
hold for *any* answer a leaf could give, which is stronger than proving it
against the real one. Untried at the caller level.

**Bounded, and the bound must be stated.** The proof runs per `(n_units,
n_binds)` shape. One unit is tractable; the plan is to prove a ladder — 1, 2
and 3 units, with and without binds — and state plainly that units are
validated independently in a loop with no cross-unit state except the
duplicate-name table, so the inductive step is an *argument* and not a
machine-checked fact. Anyone extending this should attack that gap first: the
name table is the only place where unit `i` can affect unit `j`.

## Tier B — exhaustive where the space is small

Not sampling. Enumerating:

- `nw_errstr` over every value in `INT_MIN..INT_MAX` clamped to the enum
  range plus the boundaries — the array-versus-enum correspondence becomes a
  fact rather than a convention. This pair has drifted before.
- `name_ok` and `path_ok_len` over all 256 byte values at each significant
  position, and over every position for the terminator and trailing-zero
  rules. Small enough to be complete.

## Tier C — coverage measured, with a floor that fails the build

`make test` gains a coverage run over the TCB and a floor. 83% is the current
number for `nwcheck.c` after a full suite run; the floor starts there and
only rises.
Without this, coverage is a thing nobody looks at until someone measures it
once, which is exactly what happened today.

The floor is not the point — **the uncovered list is**. It should be printed
every run, because that list is what produced three findings this afternoon.

## Tier D — differential on plans, not bytes

The current `difftest` asserts only that the baker's output is accepted, and
`fuzz-200` flips bytes in a valid blob. Neither can find a *rule* difference,
because random bytes essentially never produce a structurally interesting
blob — which is why `NW_E_DUPNAME`, `NW_E_KIND` and `NW_E_LIDS` are all
unreached.

Generate random **plans** instead: names from an alphabet that includes
illegal characters, duplicate names, paths with `..`, bricks without
`newns`, landlock without a brick, out-of-range binds. Bake each, run
`nw-check`, and assert **baker-accepts ⟺ checker-accepts**, with the reason
strings mapped. Any disagreement is invariant-3 drift, which is the failure
mode that has bitten this project most often.

This is also what reaches the paths the coverage gap names.

## Tier E — guided fuzzing

`afl++` or `clang -fsanitize=fuzzer` over `nw_check`, seeded with the corpus
Tier D generates, running under ASan and UBSan. Coverage-guided fuzzing finds
the inputs a human would not think of; the current 400 deterministic flips do
not qualify as fuzzing so much as smoke-testing.

Cheap first step available today with no new tools: build the existing suite
with `-fsanitize=address,undefined` and run it. Any latent UB in the TCB
surfaces immediately.

## Tier F — make the specs execute

`plan.als` and `Plan.tla` are the one part of this repository that cannot be
verified by running, because nothing runs them. `java` is present and the
TLA+ tools are a single jar. Wiring TLC into `make test` to check `TypeOK`,
`BrickNeedsNewNS`, `BindsNeedBrick` and `LandlockNeedsBrick` is what turns
invariant 3 from a rule people remember into one the build enforces.

Until then the honest label on those files stays kind 3, not kind 1.

## Tier G — a reproducible artifact

Add `-ffile-prefix-map=$(CURDIR)=.` and a test that builds the tree twice in
two different directories and compares every binary. Verified to work today.
This is the cheapest item here and it is the one that makes "what you ran is
what you reviewed" checkable rather than assumed — and it composes with the
brick reproducibility already specified in `docs/options/08`.

## Sequence

1. **Tier G and Tier C now.** No TCB change, no decision needed: the build
   flag, the twice-build test, and the coverage floor with its uncovered
   list.
2. **The CRC restructure**, on approval, with `tcb-review` before push.
3. **Tier A** against the restructured validator, added to `make test` behind
   a `make prove` target so a normal run stays fast.
4. **Tier D**, which both closes the coverage gap and catches rule drift.
5. **Tier B**, **E**, **F** as they earn their place.

## What this does not make provable, restated

The boot chain. The lids. The pivot. Anything that depends on what a kernel
actually does. Those are covered by tests that must run on real kernels, and
the coverage record already says two of them run on none we have. Proving the
validator raises the floor under everything; it does not touch the ceiling.
