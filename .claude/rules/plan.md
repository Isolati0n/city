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

`plan.als` and `Plan.tla` are the one part of this repository **you
cannot verify by running.** Nothing executes them: no `alloy`, no `tlc`,
and nothing in the `Makefile` or `tests/run.py` references either file.
The Alloy scope is small, `Plan.tla` has no next-state relation, and what
remains is a type predicate no behaviour is checked against.

So when you edit a spec, say plainly that you could not run it. If someone
treats these files as evidence the implementation is correct, correct them:
they constrain the plan *format* and say nothing about descriptor handling at
runtime, which is where every real bug in this project has been.

**Waiting on a prerequisite:** `java` is present and the TLA+ tools are a
single jar. The day that lands, wire `TypeOK` into `make test` — that is
what turns invariant 3 from a rule people remember into one the build
enforces.

## Definition of done

`make test` passes, quoted from its own output. **A check you added must be
shown *rejecting* a crafted bad blob**, not merely accepting good ones — see
`test_brick_needs_newns` in `tests/run.py`, which clears a lid bit by
hand and repairs the CRC to build a blob the baker would never emit.
