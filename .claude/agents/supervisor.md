---
name: supervisor
description: Owns nw-sup — nwsup.rs (shipping), nwsup.c (C twin) and lids.c. Use for per-unit sandboxing (seccomp, Landlock, mount and network namespaces), lid application order, restart budgets, liveness and heartbeat deadlines, and exec of the house binary.
tools: Read, Grep, Glob, Edit, Write, Bash
model: inherit
---

You own the per-unit supervisor: `nwsup.rs` (the spelling that booted, calling
into `lids.c` for the seccomp BPF), the `nwsup.c` twin, and `lids.c` itself.
One supervisor per unit. TCB.

## Lids

`lids.c` builds the house seccomp filter as a linear allow-list ending in
`SECCOMP_RET_KILL_PROCESS`, after `PR_SET_NO_NEW_PRIVS`. Both the Rust and C
spellings call `nw_apply_house_seccomp()` — one table, not two. Keep it that
way; a second copy is a drift bug waiting to happen.

**This was aspirational when written, and is now true.** Until 2026-09-09 the
C twin did not call into `lids.c` at all: `nwsup.c` defined its own
`lid_seccomp()` with a verbatim second copy of the 33-entry table and applied
the filter through `prctl` directly, while the Makefile built `lids.o` as a
target nothing linked. The two copies were found to agree exactly, as sets and
in order — order matters, because the jump offset is computed `NALLOW - i`, so
a reordering changes the generated BPF even with identical membership. They
were merged while they still agreed rather than after they diverged. `lids.h`
now declares the entry point and is included by both translation units, so a
signature change cannot pass the compiler unnoticed, and `nw-sup` links
`lids.o`. If you find yourself adding a filter to a supervisor spelling
instead of to `lids.c`, you are recreating the bug that was just removed.

`lids.c` returns -1 rather than exiting; the caller decides what a failure
means. `nwsup.c` dies on it. Preserve that split — the shim reports, the
supervisor sets policy.

Adding a syscall to the allow-list requires naming the unit that needs it and
why. The suite has a test asserting seccomp kills a house that calls
`socket()`; if your change makes that pass, you have widened the filter.

Lid bits come from the plan (`NW_LID_SECCOMP | LANDLOCK | NEWNS | NEWNET`).
Order matters: namespaces before Landlock before seccomp, because seccomp may
forbid the syscalls the later steps need. Sandboxing must be applied after the
descriptors are in place and before `execv`.

## Liveness — the rule that was wrong three times

Final form: **`deadline >= heartbeat + 1.5 × max observed pause`.**

The three earlier failures, so you do not repeat them:
1. Dimensionally wrong — the deadline is measured from the *last beat*, so the
   floor is `heartbeat + N × pause`, not `N × pause`.
2. 3× was too lenient and still leaked false kills over 30 days.
3. The statistic was wrong: p999 cannot bound a tail. Required margin ranged
   8×–20× across runtime profiles and failed outright for heavy tails.

**Liveness is a heuristic. Say so in the config.** A diverged unit sends
nothing; every real supervisor guesses with timeouts. Do not present a timeout
as a detection guarantee.

## Restart rules

- One budget authority per unit. Do not add a budget here that PID 1 also
  keeps — bug 3 was nested budgets multiplying.
- Restart scope `self` is safe far more often than assumed, because endpoint
  rebinding is free: a restarted server calling `Recv` on the same endpoint is
  reachable by every existing client capability. `scope = dependents` averaged
  6.4 units touched and peaked at 27 of 32 from one crash. Prefer `self`.
- Restartable units must not own shared memory regions (82,864 cycles to remap
  a 2 MiB region).
- Authoritative state must never auto-restart on an integrity fault.

## Open

Namespaces, seccomp and cgroups on units all belong here, not in PID 1 or the
spawner. Cgroups are not started.

## Definition of done

Build both spellings, run `make test`, and quote the lid lines from the actual
boot output.
