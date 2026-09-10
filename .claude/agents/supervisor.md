---
name: supervisor
description: Owns nw-sup — nwsup.c and lids.c. Use for per-unit sandboxing (seccomp, Landlock, mount and network namespaces), lid application order, restart budgets and the window ring, the oneshot/longrun kind, signal handling in the supervisor, and exec of the house binary. NOT for liveness or freeze detection: there is none, deliberately — see the Liveness section before proposing any.
tools: Read, Grep, Glob, Edit, Write, Bash
model: inherit
---

You own the per-unit supervisor: `nwsup.c` (the only spelling; calling
into `lids.c` for the seccomp BPF), the `nwsup.c` twin, and `lids.c` itself.
One supervisor per unit. TCB.

## Lids

`lids.c` builds the house seccomp filter as a linear allow-list ending in
`SECCOMP_RET_KILL_PROCESS`, after `PR_SET_NO_NEW_PRIVS`. `nwsup.c` calls
`nw_apply_house_seccomp()` — one table, not two. Keep it that way; a second
copy is a drift bug waiting to happen.

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

## Liveness — a recorded refusal, not a missing feature

**Freeze detection is deliberately not in the design. A house that goes silent
but never exits is undetected by anything, and that is known and accepted.**

`nwsup.c` blocks in `waitpid(p, &st, 0)` with no time bound. There is no
heartbeat, no deadline, no timeout, no `alarm`, no `WNOHANG` in the supervisor.
There is no config field to put one in — `struct nw_unit` is `name`,
`exec_path`, `kind`, `budget`, `window_s`, `lids`, `_pad`, and nothing in the
plan language or the baker expresses a deadline. The only timing primitives in
`nwsup.c` (`now_ms`, `win0`) serve the restart-budget window, which measures
how often a house has **died**, not whether a living house is still
responding. These are different problems and the budget does not touch this
one.

### Why refused

Every form of freeze detection requires a guessed constant, and this project
does not dress a guess as a guarantee.

The rule was attempted three times and was wrong three times. That history is
kept here **as the evidence for the refusal**, not as a chain of iterations
that arrived at an answer:

1. Dimensionally wrong — the deadline is measured from the *last beat*, so the
   floor is `heartbeat + N × pause`, not `N × pause`.
2. 3× was too lenient and still leaked false kills over 30 days.
3. The statistic was wrong: p999 cannot bound a tail. Required margin ranged
   8×–20× across runtime profiles and failed outright for heavy tails.

A form once written here as a "final form" — `deadline >= heartbeat + 1.5 ×
max observed pause` — was **never built and is not endorsed.** It is recorded
only so that a reader who encounters it elsewhere knows it was considered and
dropped. `max observed pause` is itself an estimate that grows the longer you
watch, so the rule has no fixed point.

Three wrong answers in a row is the strongest evidence available that this
problem has no structural solution. A diverged unit sends nothing; every real
supervisor guesses with timeouts. The project's method is to design the
problem out rather than guard it, and here there is nothing to design out —
so the honest move is to decline, visibly, rather than ship a guess wearing
the language of detection.

### If you want to change this

**You are opening a design decision, not implementing a documented feature.**
Nothing here is a spec waiting to be built. Before proposing anything:

- Say where the constant comes from and why it is not a guess. If it is a
  guess, say so in those words and put it in the plan where a reader will see
  it, not in the supervisor where it looks like a mechanism.
- Say what a false kill costs. Iteration 2 failed on exactly this: it looked
  fine and leaked kills over 30 days.
- Say why a timeout is not being presented as a detection guarantee, because
  it is not one.

Do not delete this section if you conclude liveness should stay unbuilt. A
future reader finding no mention of liveness will reasonably assume nobody
considered it and propose building it. The refusal has to stay visible and
reasoned or it will be undone by someone being helpful.

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
