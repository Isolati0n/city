# nw-init — working rules

Init system for Nexusweave. Linux kernel underneath. A plan is validated
offline, then a runtime table interpreter executes it. Robustness comes from
*where code lives*, not from how much code exists.

## TCB boundary — memorise this

| component | file(s) | language | in TCB |
|---|---|---|---|
| `nw-root` (PID 1) | `pid1.c` + `nwcheck.c` | C | **yes** |
| `nw-spawn` (boot spawner) | `nwspawn.c` + `nwcheck.c` | C | **yes** |
| `nw-check` | `nwcheck_main.c` + `nwcheck.c` | C | **yes** |
| `nw-sup` | `nwsup.c` / `nwsup.rs` + `lids.c` | C / Rust | **yes** |
| `nw-rescue` | `rescue.c` | C | yes |
| baker (`nw-cc`) | `bakery/nw-cc.py` | Python | **no** |
| test suite | `tests/run.py` | Python | **no** |
| spec | `plan.als`, `Plan.tla` | Alloy / TLA+ | **no** |

Anything added to a TCB file needs a stated justification. Anything that can
live in the baker instead, does.

## Invariants that must not be broken

1. **No allocation, no parsing, no recursion after start in PID 1.** Restart
   budget is a ring of timestamps, not a counter.
2. **No compile-time file descriptor numbers alongside dynamic allocation.**
   This produced bugs 5, 9 and 13. Sweep `/proc/self/fd`; do not hardcode.
3. **Limits are derived, never declared twice.** `NW_MAX_UNITS` feeds the
   `_Static_assert` fd budget in `blob.h`. The same arithmetic appears in
   `bakery/nw-cc.py`, `plan.als` (`fdNeed`) and `Plan.tla` (`FdNeed`). Change
   one, change all four, or they drift.
4. **`nw-spawn` exits; its death is not a failure mode.** It forks one
   supervisor per unit, double-forked so PID 1 adopts the houses, reports the
   pids and exits 0. PID 1 requires a complete report *and* a clean exit —
   successful termination is the completion signal, not something to watch
   for. Spawning is boot-time only: PID 1 has no respawn path and restart
   budgets live in `nw-sup`. Do not give the spawner a mid-life.
5. **A unit holds nothing above stderr.** `/dev/null` on 0, its own log pipe on
   1 and 2, and `close_others` sweeps the rest. There is nothing a unit is
   supposed to be handed beyond those. (Until 2026-09-10 this read "wiring is
   non-provision, not enforcement" and concerned declared edges; edges were
   removed — see `HISTORY.md` §17.)
6. **Isolation of units is by lids, not by topology.** Seccomp, Landlock and
   namespaces are applied per unit by `nw-sup` before `execv`. Cybersecurity is
   not a goal of this system; containerization applies to apps.
7. **Authoritative state never auto-restarts on an integrity fault.**
8. **The live city does not grow verbs.** A new plan is a new slot (A/B), never
   an in-place rewrite. This is now an operational rule only: `NoLiveRewrite`
   was withdrawn from `Plan.tla` when edges were removed, because the
   remaining variables have no runtime mutation path and the predicate would
   have been vacuous. See `HISTORY.md` §17.
9. **CRC32 is diagnostic** (threat model is corruption, not tampering). The
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
