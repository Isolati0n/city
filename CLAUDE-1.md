# nw-init — working rules

Init system for Nexusweave. Linux kernel underneath. A plan is validated
offline, then a runtime table interpreter executes it. Robustness comes from
*where code lives*, not from how much code exists.

## TCB boundary — memorise this

| component | file(s) | language | in TCB |
|---|---|---|---|
| `nw-root` (PID 1) | `pid1.c` + `nwcheck.c` | C | **yes** |
| `nw-electrician` (broker) | `electrician.c` + `nwcheck.c` | C | **yes** |
| `nw-check` | `nwcheck_main.c` + `nwcheck.c` | C | **yes** |
| `nw-sup` | `nwsup.c` / `nwsup.rs` + `lids.c` | C / Rust | **yes** |
| `nw-rescue` | `rescue.c` | C | yes |
| baker (`nw-cc`) | `bakery/nw-cc.py` | Python | **no** |
| test suite | `tests/run.py`, `tests/bakeoff.py` | Python | **no** |
| spec | `plan.als`, `Plan.tla` | Alloy / TLA+ | **no** |

Anything added to a TCB file needs a stated justification. Anything that can
live in the baker instead, does.

## Invariants that must not be broken

1. **No allocation, no parsing, no recursion after start in PID 1.** Restart
   budget is a ring of timestamps, not a counter.
2. **No compile-time file descriptor numbers alongside dynamic allocation.**
   This produced bugs 5, 9 and 13. Sweep `/proc/self/fd`; do not hardcode.
3. **Limits are derived, never declared twice.** `NW_MAX_UNITS` and
   `NW_MAX_EDGES` feed the `_Static_assert` fd budget in `blob.h`. The same
   arithmetic appears in `bakery/nw-cc.py`, `plan.als` (`fdNeed`) and
   `Plan.tla` (`FdNeed`). Change one, change all four, or they drift.
4. **Electrician death is fatal.** It holds the only copy of the connection
   graph. PID 1 halts everything (exit 70). Never add a broker restart path.
5. **Wiring is non-provision, not enforcement.** A unit with no declared edges
   receives zero descriptors. There is no doorman to bypass. Do not add one.
6. **Readiness is observed by the electrician, never reported by the unit.**
7. **Authoritative state never auto-restarts on an integrity fault.**
8. **The live city does not grow verbs.** A new plan is a new slot (A/B), never
   an in-place rewrite. See `NoLiveRewrite` in `Plan.tla`.
9. **CRC32 is diagnostic** (threat model is corruption, not tampering). The 14
   structural checks in `nwcheck.c` are the actual safety property. The seal
   must be *verified*, not merely read — that was bug 1.

## Build and test

```
make            # all binaries
make stage      # stages to /tmp/nw-init-run (artifacts/ is noexec)
make test       # stage + nw-check on the blob + python3 tests/run.py
```

`tests/run.py` boots via `unshare --pid --fork --mount-proc` so `nw-root` is
genuine PID 1 and orphan reaping is actually exercised.

## The rule that matters most

Thirteen bugs in this codebase were found by running, zero by reading. Do not
report a change as working until it has been built and run. Prefer designing
the problem out over checking for it: no ordering list to get wrong, no counter
to overflow, no second limit to drift, no channel to impersonate.
