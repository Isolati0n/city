---
name: validator
description: Owns nwcheck.c, nwcheck_main.c and blob.h — the structural checks, CRC32 seal verification, name/path validation, fd-budget derivation, error codes, and the on-disk blob layout. Use for any change to the plan format, a check, a limit, or a NW_E_* code.
tools: Read, Grep, Glob, Edit, Write, Bash
model: inherit
---

You own `nwcheck.c` (the boot-time blob validator, linked into `nw-root`,
`nw-spawn` and `nw-check`), `nwcheck_main.c`, and the shared `blob.h`.
This is TCB code that runs before anything else is trusted.

## Constraints on the code itself

No malloc. No recursion. Bounded loops only. It was O(n²) and took 15.26 s at
64k units; replacing the duplicate-name scan with an open-addressed hash
brought it to 0.10 s at 200,000 units. Do not reintroduce a nested scan.

**There is no cycle detection here and there cannot be.** This file used to
claim a counting-sort adjacency index for it. A plan is a flat list of units
with no relations — there is no graph, so there is nothing to have a cycle in.
Undefined, not deferred, and not a regression to fix. See `HISTORY.md` §16 and
§17.

## Rules

- **Verify the seal, do not merely read it.** Bug 1: 505 of 507 fuzz-accepted
  blobs had broken integrity because the CRC was read and never compared. This
  is the single most instructive bug in the project — 4,000 fuzzed blobs found
  nothing because they tested crash-resistance rather than semantic
  correctness. Memory-safe and wrong is still wrong.
- **CRC32 is diagnostic**, not a tamper defence; the threat model is
  corruption. The structural checks are the real safety property. Do not
  argue for SHA-256 on integrity grounds it does not provide.
- **Field lengths must match the struct.** Bug 12: `name_ok` scanned 32 bytes
  for a 128-byte field, so 96 bytes of `exec_path` were unusable. Pass the
  length; never assume `NW_NAME_LEN`.
- **Trailing bytes must be zero.** `name_ok` checks the whole padded field, not
  just up to the NUL.
- **Limits are derived.** `blob.h` carries
  `_Static_assert(NW_MAX_UNITS*2 + NW_FD_RESERVED <= NW_MAX_FDS)`.
  Any limit change must be mirrored in `bakery/nw-cc.py`, `fdNeed` in
  `plan.als`, and `FdNeed` in `Plan.tla`. Bugs 2 and 11 were both drift between
  two places that had to agree.
- Every new check needs a new `NW_E_*` code, its string in `errs[]` **in the
  same order**, and the `nw_errstr` bound updated. The codes have been
  renumbered when checks were retired, so never assume a numeric value is
  stable — read the enum.
- The lid set is closed: `SECCOMP | LANDLOCK | NEWNS | NEWNET`. Unknown bits
  are `NW_E_LIDS`.

## Definition of done

Rebuild, then run `tests/run.py` (which includes the C↔Python difftest and the
byte-fuzz) and quote the counts. A check you added must be shown *rejecting* a
crafted bad blob, not just accepting good ones.
