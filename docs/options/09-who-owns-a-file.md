# 09 — who owns a file when two workstreams are inside one territory

**Status: open. This file exists so the question has a home; nothing here
is decided.**

## The question

`.claude/rules/{plan,runtime,harness}.md` divide the tree into
territories, and that division is clean — `runtime` owns the whole boot
chain, `dawn.c`, `pid1.c`, `nwspawn.c`, `nwsup.c` and `lids.c`. It has no
gap for two agents to fall into.

Territories are not workstreams. On 2026-09-11 two concurrent
workstreams — bricks and lifecycle — both landed inside `runtime`, and
inside two files:

- **`nwsup.c`** holds `lid_brick()` and the bind mounts (bricks) beside
  the restart budget and death handling (lifecycle).
- **`nwspawn.c`** never mounts; it hands the brick and binds on through
  `setenv("NW_BRICK", …)` and `NW_BIND_n` (bricks) beside the budget
  handoff and the mid-fork reap (lifecycle).

A territory map does not stop two agents colliding inside a territory.
That is the whole problem and no amount of redrawing the map fixes it.

## Why it is not urgent yet, and when it becomes urgent

Phase 1 of `docs/plans/01` is baker-only and touches neither file. Phase 2
touches `nwsup.c`. The lifecycle work already in flight touches both. The
day both are in flight at once is the day this has to be answered.

## The options, unargued

- **A. One owner per file, named here.** Simple; makes the other
  workstream ask before editing. Costs a round trip on every
  cross-cutting change.
- **B. Split the files.** Move the brick half of `nwsup.c` into its own
  translation unit so the territory and the workstream coincide. Costs a
  TCB change to fix a coordination problem, which is the wrong direction
  — `CLAUDE.md` says anything that can live outside the TCB does.
- **C. Serialise instead of dividing.** Only one workstream touches the
  boot chain at a time. Costs parallelism, needs no code change, and is
  the only option that is free to reverse.
- **D. Nothing.** Accept the collision and resolve it in review. This is
  what happened on 2026-09-11 and it worked, because the two halves were
  in different files. It will not work when they are in the same one.

## What would settle it

Not an argument — an instance. The first genuine conflicting edit to
`nwsup.c` shows which option would have cost least. Until then this is a
decision without evidence, and `CLAUDE.md` records it as waiting on a
prerequisite rather than pretending it is settled.
