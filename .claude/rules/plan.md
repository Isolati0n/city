# plan — territory rules

<!-- nw-init:install-agents v1 -->
**Not an agent.** This was a dispatchable brief until 2026-09-10 and was
never dispatched once. Its content is reference read at the moment it
applies, so `tools/rules-hook.sh` delivers it on a `PreToolUse` for any
file in this territory. Scope: Owns the sealed plan — blob.h, nwcheck.c, nwcheck_main.c, bakery/nw-cc.py, plan.als and Plan.tla. Use for any change to the blob layout, a limit, a structural check, a NW_E_* code, plan-language syntax, or the specs.

You own what a plan **is** and what makes one acceptable: `blob.h`,
`nwcheck.c`, `nwcheck_main.c`, `bakery/nw-cc.py`, `plan.als` and
`Plan.tla`.

## Why this is one territory and not three

It was three — a validator agent, a baker agent and a spec agent — until
2026-09-10. The split was wrong, and the repository says so: every
plan-format change in its history touched `blob.h`, `nwcheck.c`,
`bakery/nw-cc.py`, `plan.als` and `Plan.tla` **in a single commit**.
Invariant 3 requires exactly that ("change one, change all four"), so an
agent that owns one of them owns a fraction of every change it will ever be
asked to make, and cannot see whether the other fractions agree.

The boundary that does matter here is not between files, it is **trust**:
`nwcheck.c` and `blob.h` are TCB, the baker and the specs are not.

## Hard rules

- **Verify the seal, do not merely read it.** Bug 1: fuzz-accepted blobs had
  broken integrity because the CRC was read and never compared. Memory-safe
  and wrong is still wrong.
- **CRC32 is diagnostic**, not a tamper defence; the threat model is
  corruption. The structural checks are the real safety property. Do not
  argue for SHA-256 on integrity grounds it does not provide.
- **No malloc, no recursion, bounded loops** in `nwcheck.c`. It was O(n²)
  and took 15.26 s at 64k units; an open-addressed hash brought it to 0.10 s
  at 200,000. Do not reintroduce a nested scan.
- **Field lengths must match the struct.** Bug 12: a 32-byte scan over a
  128-byte field left most of `exec_path` unvalidated. Pass the length.
- **Trailing bytes must be zero, and an empty optional field is still
  checked.** A blank `brick` has every byte verified zero, for the same
  reason `_pad` is: an unvalidated field cannot be given meaning later,
  because an old blob carrying garbage would be accepted by a new checker
  that reads it.
- **Prefer rejecting at bake time — but any rule the runtime relies on must
  be in `nwcheck.c` too.** The baker is not in the TCB and a blob can
  arrive from anywhere. The cross-field rules — a brick forces
  `NW_LID_NEWNS`, and a bind requires a brick — are each enforced in both
  places independently. (A third, `NW_PROF_BUILD` requires `NW_LID_SECCOMP`,
  went with the profile on 2026-09-10; `HISTORY.md` §23.)
- **The baker refuses; it does not repair.** A brick house that forgot
  `newns` is a bake error, not a plan to quietly add a lid to. A lid nobody
  asked for is a lid nobody reviewed.
- **Every new check needs a new `NW_E_*` code, its string in `errs[]` in
  the same order, and the `nw_errstr` bound updated.** Codes have been
  renumbered when checks were retired — never assume a numeric value, read
  the enum.
- **The lid set is closed.** Unknown bits are `NW_E_LIDS`.
- **Check the struct sizes, do not eyeball them.** The Python
  `struct.pack` format and the C struct must agree. Take the format from
  `bake()` in the baker, run `struct.calcsize` on it, and compare against
  `sizeof(struct nw_unit)` and `sizeof(struct nw_bind)` from a compiled
  throwaway. Do not copy the format string into a brief or a comment: the one
  that used to be here went stale on 2026-09-10, the day the format changed. A
  mismatch surfaces as a size error from `nw-check`, not as a Python
  exception, so it will look like a corrupt blob rather than a bug in you.

## Refused deliberately

- **Cycle detection.** Undefined, not deferred. A plan is a flat list of
  units with no relations — there is no graph, so there is nothing to have a
  cycle in. This file once claimed a counting-sort adjacency index for it.
  Wanting it back means proposing a plan format with relations in it, which
  is a design decision, not a restoration. `HISTORY.md` §16 and §17.
- **Typing, ordering, capability-flow analysis.** None were ever built and
  after §17 none are definable. Fields are range-checked, which is not
  typing.

## The specs, honestly

**They run.** `tools/jars/` holds TLC and Alloy, and
`test_specs_are_checked` in `tests/run.py` executes both inside
`make test`. `TypeOK`, `FdBudgetCovers`, `FdNeedAgrees` and
`LargestCityFits` are TLC invariants; `FdArithmetic` and `Sealed` are
Alloy checks. **The two Alloy checks are shown failing on every run** —
the suite breaks the thing each is about and requires a counterexample.
The TLC invariants have no such probe: their controls were run by hand
once and are recorded in `HISTORY.md` §39, which is weaker and is why
this sentence separates them.

The limits both specs use are generated from `blob.h` by
`tools/gen-spec-limits.py` — including the lid bits, as of 2026-09-11.
**One hand-written number remains**, Alloy's `but 12 Int` bitwidth in
`plan.als`, and `test_specs_are_checked` asserts it covers `NW_MAX_FDS`.
(This said "neither file holds a limit to drift", which the same round's
own work disproved three lines later in `plan.als`. Correcting a
sentence into an absolute is how the last four of these went wrong.)

*This section said the opposite until 2026-09-11 — "you cannot verify by
running … no `alloy`, no `tlc`, and nothing in the `Makefile` or
`tests/run.py` references either file" — for a day after all three
clauses became false. `plan.als`'s own copy of that paragraph was
corrected and this one was not, which is the survived-by-being-moved
shape, in the file `tools/rules-hook.sh` hands to the next agent to
touch a spec. `claims` found it.*

**What running them found immediately** is the reason to keep saying
this out loud: neither file PARSED. Alloy refused `plan.als` for a
missing scope; TLC refused `Plan.tla` for a use-before-definition. And
`fdNeed` did not add — Alloy's `+` on `Int` is set union, so the fd
formula, one of the four places invariant 3 names, had never computed
the fd budget. `HISTORY.md` §39.

**What is still not checked, so do not cite it:**

- **TLC's `BindNeed` is only ever evaluated at 0.** It is reachable only
  through `TypeOK`, and `Init` gives every house an empty bind set.
  Measured: adding `BindNeed = 0` as a `TypeOK` conjunct holds in all 64
  states, and `BindNeed # 0` is violated in the initial state. Replacing
  the whole recursion with `BindNeed == 0` runs clean.

  *This was written as a TLA+ limitation, and it was not: `control`
  deleted the bind conjunct from Alloy's `pred sealed` -- half the
  predicate the check is named for -- and the suite passed, because at
  scope 8 `#binds` cannot exceed 64 while `nwMaxBinds` is 128, so no
  counterexample exists at any legal header value.* The Alloy half is
  pinned now, by a third must-fail probe that lowers `nwMaxBinds` below
  what the scope can reach. The TLA+ half is still unpinned; exercising
  it needs an `Init` that ranges over bind sets.
- `plan.als`'s three cross-field facts (`brickNeedsNewNS`,
  `bindsNeedBrick`, `landlockNeedsBrick`) are facts, not assertions, so
  they constrain instances rather than being tested. `control` inverted
  `brickNeedsNewNS` into the plan `nwcheck.c` rejects and every check
  stayed green. Their enforcement is `nwcheck.c` plus
  `test_checker_rejects_crafted_fields`.
- Both results are bounded: Alloy at scope 8 with a 12-bit `Int`, TLC at
  one state per legal unit count.

So a spec still says nothing about descriptor handling at runtime, which
is where every real bug in this project has been. It now says something
checkable about the format, which it did not before.

## Definition of done

`make test` passes, quoted from its own output. **A check you added must be
shown *rejecting* a crafted bad blob**, not merely accepting good ones — see
`test_brick_needs_newns` in `tests/run.py`, which clears a lid bit by
hand and repairs the CRC to build a blob the baker would never emit.
