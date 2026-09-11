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
Alloy checks. **Every Alloy check is shown failing on every run** — the
suite breaks the thing each one is about and requires a counterexample.
`Sealed` gets two probes, one per conjunct, because the bind half admits
no counterexample at the scope the fd half fails at.

**The TLC invariants have probes too, as of 2026-09-11**, one per
predicate, each listing only its own invariant in the cfg so a sibling
cannot answer for it. Until that day they had none, and `control` showed
the price: `LargestCityFits == TRUE` in `Plan.tla` left the entire suite
green, under an `ok` line that read "4 invariants incl. the boundary".
All four mutations — each invariant replaced by `TRUE`, and `TypeOK`'s
`kind` range widened — now turn the suite red. A TLC run is under a
second, which is the whole reason this was cheap and the reason its
absence was indefensible.

*Their hand-run controls are still in `HISTORY.md` §39 **and** §41, and
that sentence has now been wrong twice: it pointed at §39, was
"corrected" to §41 alone, and the correction was wrong — §39 carries a
"Controls, all run" table with `NW_MAX_FDS = 100` violating
`FdBudgetCovers`. "Run by hand once" was wrong in a second way: two
rounds, not one. `claims` found the correction, having not been asked to
check the thing being corrected* to.

The limits both specs use are generated from `blob.h` by
`tools/gen-spec-limits.py` — including the lid bits, as of 2026-09-11.
**One hand-written number remains that must track the header**, Alloy's
`but 12 Int` bitwidth in `plan.als`, and `test_specs_are_checked`
asserts it covers `NW_MAX_FDS`. The qualifier is load-bearing: `for 8`
is hand-written three times in the same file and is *not* covered by
that sentence, because nothing in `blob.h` corresponds to it — this
file declares no bound on `#House`. The suite requires the three
commands to agree and imposes a floor of 2 (below which the binds
must-fail probe cannot reach a counterexample); it does not derive the
value, and the prose copies of the scope are pinned by nothing — do not
enumerate them here, because an enumeration is a count and the first one
written was already short by two. Nor is a grep the answer: `grep -rn
"scope 8"` was offered as one and misses `tools/jars/README.md`, which
writes it as "Alloy's scope is 8 of each signature". Two attempts to
avoid a count both failed, so: **read the commands in `plan.als`, and
treat any number in prose as unverified.**

**And the qualifier is still too generous — but say what it is pinned
*against*.** `plan.als` hand-writes the fd multiplier
(`plus[nwReserved[], 2.mul[#House]]`) and so does `Plan.tla`
(`FdNeed == Reserved + 2 * n`), and that `2` must track `blob.h`, which
is exactly what invariant 3's "the arithmetic appears in four places"
says. Nothing pins it **against the header**: `claims` changed `* 2` to
`* 3` in both of `blob.h`'s `_Static_assert`s and both specs ran clean,
because `tools/gen-spec-limits.py` emits the four limit values and the
two lid bits and no arithmetic at all — the generated files come out
byte-identical.

Each *is* pinned against a second hand-written copy in its own file:
`assert FdArithmetic` for Alloy, `FdNeedAgrees` for TLC, and each turns
`make test` red on its own. That is a weaker pin and a real one, and
"nothing pins it" — written here for one round without the preposition
— reads as licence to change a spec to match a `* 3` header and then be
surprised by a red suite.

**The one that is pinned in neither direction is `LargestCityFits`'s
`2 * MaxUnits` in `Plan.tla`.** `claims` changed it to `* 3` and TLC
reported `Model checking completed. No error has been found.` Its probe
lowers `MaxFds`, which a *larger* multiplier only makes easier to
violate, so the probe cannot see it. **That is the live gap** — a
second-way assertion of the shape `FdNeedAgrees` already has would
close it, and it is unbuilt.

(This said "neither file holds a limit to drift", which the same round's
own work disproved three lines later in `plan.als`; then "one
hand-written number remains", which `claims` disproved by pointing at
`for 8`; then "one that must track the header", which `claims` disproved
again by pointing at the multiplier. Correcting a sentence into an
absolute is how the last six of these went wrong, and the pattern is
now the most reliable thing in this file: **if a sentence here counts
something, it is probably wrong.**)

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
  Measured: adding `BindNeed = 0` as a `TypeOK` conjunct holds in every
  state TLC explores, and `BindNeed # 0` is violated in the initial
  state. Replacing the whole recursion with `BindNeed == 0` runs clean.

  *This was written as a TLA+ limitation, and it was not: `control`
  deleted the bind conjunct from Alloy's `pred sealed` -- half the
  predicate the check is named for -- and the suite passed, because
  `#binds` is capped by the scope times itself while `nwMaxBinds` is
  `NW_MAX_BINDS`, and at today's values the first cannot exceed the
  second, so no counterexample exists at any legal header value.* The
  Alloy half is pinned now, by a third must-fail probe that lowers
  `nwMaxBinds` below what the scope can reach. The TLA+ half is still
  unpinned; exercising it needs an `Init` that ranges over bind sets.
- `plan.als`'s three cross-field facts (`brickNeedsNewNS`,
  `bindsNeedBrick`, `landlockNeedsBrick`) are facts, not assertions, so
  they constrain instances rather than being tested. `control` inverted
  `brickNeedsNewNS` into the plan `nwcheck.c` rejects and every check
  stayed green. Their enforcement is `nwcheck.c` plus
  `test_checker_rejects_crafted_fields`.
- Both results are bounded: Alloy at the scope and bitwidth written on
  the commands in `plan.als` (8 and 12 today, and the suite asserts the
  bitwidth covers `NW_MAX_FDS`), TLC at one state per legal unit count.
  Read the numbers off the commands, not off this line — see the
  `but 12 Int` note above for why the scope has nothing to track.

So a spec still says nothing about descriptor handling at runtime, which
is where every real bug in this project has been. It now says something
checkable about the format, which it did not before.

## Definition of done

`make test` passes, quoted from its own output. **A check you added must be
shown *rejecting* a crafted bad blob**, not merely accepting good ones — see
`test_brick_needs_newns` in `tests/run.py`, which clears a lid bit by
hand and repairs the CRC to build a blob the baker would never emit.
