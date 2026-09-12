# proofs — what is actually proven about the validator

`sh proofs/run.sh` (about five minutes of solver time; `make proof` runs
the same thing).

This directory exists because the claim did not. `docs/plans/02` recorded
"Tier A — ACHIEVED" in the present tense on 2026-09-11 while the harness that
achieved it lived in a session scratch directory that no longer exists;
`nwcheck.c` was then edited twice and nothing re-ran anything. `tcb-review`
found it by grepping the tree for `cbmc` and getting nothing. A proof that
cannot be re-run is a sentence.

## What each run establishes

Every buffer is `__CPROVER_havoc_object`'d, so the solver considers every
value of it rather than a sample. Bounds, pointer validity, signed overflow,
shifts and division are checked throughout, and `--unwinding-assertions` is
on everywhere, so a loop bound that is too small **fails** instead of quietly
truncating the search.

**Two of the four then narrow the input, deliberately, and the table below
says so rather than claiming "every byte is free" — which this page did
until 2026-09-11, and which `claims` falsified by asserting the pinned bytes
are pinned and watching it verify.** The narrowing is argued sound at each
site: `caller_nw_check.c` pins `n_units`/`n_binds` because every other value
provably returns `NW_E_SIZE` before anything asserted; `leaf_name_dup.c`
assumes names are non-empty and NUL-padded because `name_ok` runs first in
`nw_check` and `leaf_name_ok.c` proves exactly that pair.

| harness | function | quantified over |
|---|---|---|
| `leaf_path_ok.c` | `path_ok_len` | every `NW_PATH_LEN`-byte string |
| `leaf_name_ok.c` | `name_ok` | every `NW_NAME_LEN`-byte string |
| `leaf_name_dup.c` | `name_dup` | every pair of non-empty NUL-padded names (**not** all 2^(8·32) byte strings: the padding is assumed, and `leaf_name_ok.c` proves `name_ok` delivers it) |
| `caller_nw_check.c` | `nw_check` | every byte of a one-unit blob **except the 8 that carry `n_units`/`n_binds`**, leaves abstracted; run again at one bind |

`leaf_name_dup` proves the property the runtime actually depends on: running
`name_dup` over units in order against a table that started empty reports a
duplicate **exactly when** two of them share a name. Both directions, because
a checker that answers `NW_E_DUPNAME` to everything satisfies the first one.

## What is *not* proven, stated plainly

- **Only one direction is.** Every assertion in `caller_nw_check.c` lives
  inside `if (r == NW_OK) { ... }`, so the whole harness expresses
  *accepted implies P* and nothing else. **No defect that makes `nw_check`
  refuse a plan it should accept is expressible here**, however it is
  written: the property is about **a path not taken**, and a rejection
  cannot violate an acceptance postcondition. An over-strict checker
  accepts a strict subset, so every blob it accepts satisfies every
  post-condition here for free — it is not that the assertions are weak,
  it is that soundness is the wrong half.

  Measured 2026-09-12, not reasoned: `nwcheck.c`'s bind loop tested
  `brick[0]` instead of scanning the hash, refusing one legal plan in 256.
  With the proof's matching `brick[0]` assertion **corrected** and the
  checker left broken, `caller_nw_check_bind` still PASSES. `claims`. (The
  same defect did turn the proof red when the *checker* was fixed and the
  assertion was not — the proof pinned the old behaviour. Being pinned to
  the code and being able to catch it are different properties, and this
  file had the first.)

  `PROOF_VACUITY` shows that *some* blob is accepted. Nothing asserts that
  a *particular* well-formed blob is. Closing it means constructing a legal
  blob in the harness and asserting `NW_OK`, and it is unbuilt.

  **Repairing an assertion does not help, and that is the trap.** The bind
  assertion was corrected to scan the whole hash; `fd-auditor` then mutated
  `nwcheck.c` back and `caller_nw_check_bind` **still passed**, with
  `caller_nw_check_bind_reached` failing as wanted — so the body was
  entered and the assertion was evaluated and held. A stricter checker
  accepts a subset, and every blob it accepts satisfies the post-condition
  for free. So a corrected assertion here **cannot fail for the reason its
  name suggests**, which is the shape `CLAUDE.md` says to refuse rather
  than count as coverage. Do not cite a green `caller_nw_check_bind` as
  evidence that a rejection rule is pinned.

- **A result file is stamped; an unstamped one is from before 2026-09-12
  and says nothing about any particular tree.** Each `.txt` opens with the
  tree path, a cksum of `nwcheck.c` + `blob.h`, the git hash with a `+dirty`
  marker, and the time. The output directory is derived from the tree path
  rather than shared, because it was a fixed machine-global
  `/var/tmp/nw-proofs` and `fd-auditor` ran mutant proofs from a scratch
  copy straight over this tree's artifacts — leaving `VERIFICATION FAILED`
  output, produced from a deliberately broken `nwcheck.c`, sitting where
  the next reader would take it for a result about the real tree. Same
  shape as the suite's shared stage path, answered the same way.

- **Do not run this beside `make test`.** `leaf_name_dup` is the long one
  and CBMC is killed under memory or CPU pressure; `run.sh` refuses a run
  that produced no result line rather than reading it as a pass, which is
  the guard working. Measured on a 4-CPU machine: a full run contending
  with the suite and two builds aborted there after ~22 minutes. Run the
  proofs alone, and read a `NO RESULT LINE` as "it did not finish", not as
  a failure of the property.

- **N is bounded.** The caller runs at one unit and no binds; `name_dup` at
  two names. Raise them with `PROOF_UNITS` / `PROOF_BINDS`, and the cost
  climbs steeply — `leaf_name_dup` at two names is already 88 s, because the
  probe chain multiplies the 32-byte comparison; at three it had not
  returned after seven minutes and 2.8 GB when this was written. Nothing
  here is a proof about a 64-unit plan. The suite's `non-provision-at-max`
  boots one; that is a test, not a proof, and the two are the argument
  together.
- **`name_dup`'s probe chain is not reached at the default N**, and this is
  the sharpest thing on this page. At two units, equal names have equal
  hashes, so a duplicate's match is always in the slot it hashes to;
  truncating the chain to a single slot leaves the proof SUCCESSFUL.
  Probing needs three units — two colliding names and a third duplicating
  the displaced one — and that run is not affordable yet. The chain is
  covered from the other side, by `test_dupname_refused`, which plants a
  real collision and does fail when the chain is truncated. Neither artifact
  covers it alone; say which one you mean.
- **The composition is only as good as its stub list.** The caller's stubs
  assume exactly what the leaf proofs assert, and those two lists are kept in
  step **by hand**, in comments that name each other. A post-condition
  assumed at the caller that no leaf proof delivers is a hole, and nothing
  mechanical would catch it. What *is* mechanical is the stub **signatures**:
  `run.sh` type-checks the harness with gcc before handing it to CBMC,
  because CBMC does not — it accepted a stub declared
  `int slot[static NW_DUP_SLOTS]` against a `struct nw_dup_tab *` and went
  on to solve.
- **`hash_name`, `name_dup` and the CRC are unconstrained at the caller.**
  That is the stronger claim, not a gap: `nw_check` must reach the same
  verdicts for any hash, any duplicate judgement and any checksum. The CRC's
  own correctness is discharged elsewhere and differently — `test_difftest`
  drives `nw_crc32_split` across every length and split point and compares
  each answer to `zlib.crc32`, which is the function the baker calls. That
  test did not do it until 2026-09-11: it ran `nw-check` on one staged blob
  and was cited here as a difftest. `claims` read the test.
- **A proof says nothing about the rest of the system.** Every real bug in
  this project has been in descriptor handling at runtime, which no harness
  here touches. `plan.als` and `Plan.tla` constrain the plan *format*, and
  say nothing about descriptor handling either. *This said "nothing
  executes them at all" until 2026-09-11, for a day after `tools/jars/`
  landed and `test_specs_are_checked` began running both inside
  `make test`. That retracted sentence was corrected in `plan.als`, in
  `Plan.tla` and in `.claude/rules/plan.md` and survived here, in the
  file `CLAUDE.md` sends readers to before quoting a proof result —
  a fourth copy, found by `claims`.*
- **`--conversion-check` is off.** With it, `path_ok_len` fails on
  `(unsigned char)s[n]`, a value-changing signed-to-unsigned conversion. That
  is well-defined C and deliberate — it is how bytes ≥ 0x80 are accepted. The
  check is a lint, not a soundness property.

## Controls

Each proof is paired with a `PROOF_VACUITY` variant that asserts the opposite
and **must fail**. The caller also carries a `PROOF_BIND_REACHED` control,
for a different failure: its bind post-conditions sat in a zero-trip loop
and were reported SUCCESS without ever being evaluated —
`__CPROVER_assert(0, ...)` passes there too. A proof prints SUCCESS for an
assertion it never reached, exactly as a test passes an absence assertion
the mechanism never produced. `harness.md`'s pairing rule applies here
unchanged, and is easier to forget because the output is more emphatic. `run.sh` exits non-zero if one of them passes. That catches
the failure mode a proof has that a test does not: contradictory assumptions
make every assertion pass for free, and the run still prints
`VERIFICATION SUCCESSFUL`.

A vacuity control is the weaker of the two controls worth running. The
stronger one is removing the mechanism: deleting
`if (u[i]._pad != 0) return NW_E_RSV;` from `nwcheck.c` moves exactly one
assertion in `caller_nw_check` from SUCCESS to FAILURE, and that is what
says the proof is anchored to the code rather than to the harness. That one
is run by hand, because automating it means committing a broken `nwcheck.c`.

## Generated, not copied

`caller_nw_check.c` includes `nwcheck_comp.c`, which `mkcomp.py` derives from
`nwcheck.c` at run time by removing five leaf function *bodies*. The earlier
version of this proof kept a hand-edited copy of `nwcheck.c` beside it — a
second copy of a TCB file, maintained by memory, which is the defect invariant
3 exists to prevent and would have gone on proving the old copy. `mkcomp.py`
fails loudly if a leaf is renamed or inlined, and a body that survived
stripping would collide with the harness's stub and fail the compile.

## Loop numbering, so the next person does not lose a run to it

`nw_check.4` is the unit loop and is the one that must be bounded.
`nw_check.2` is the `NW_DUP_SLOTS`-entry slot initialisation; bounding *that* produces a
spurious unwinding failure that looks like a real one.
