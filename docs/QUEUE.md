# QUEUE — current truth only

This file is current-state, not a log. When an item lands or a decision
changes, this file is edited in the same commit as the change; stale
entries are deleted, not appended around. Git history is the log.

`docs/OPERATOR-BRIEF.md` is the single source of truth for scope and
decisions, committed verbatim. This file tracks STATUS against it —
what's done, in progress, or next — and is the recovery path after a
session restart: read both files, nothing else, to pick back up.

If this file disagrees with the repo, the repo wins, and whoever notices
fixes this file.

Base at last edit: `553071e` (origin/main).

## Decision authority (Section 5 of the brief)

Decided alone, recorded here: names, wording, refusal texts, test
design, internal structure. Stop and ask ONLY for: a plan-format change
beyond the brief; reversing a refusal or a commitment; a new permission;
an ownership conflict (`pid1.c`/`dawn.c` are Grok's; item 1d is the only
authorized edit); anything contradicting Section 1 of the brief; a flaky
test (diagnose, never add a retry). One batched question message per
note or stage, each with a recommended default.

Review by risk: trusted core gets `control` + `tcb-review` (+
`fd-auditor` when fds/mounts/cgroups change) + `claims` on prose.
`bakery/`/`tools/`/tests get `control` + one reviewer. Docs get one
`claims` pass. Every push needs a full `make test` and an `ls-remote`
check. Low on usage: push to `wip/<stage>` with a
`tools/HANDOFF-<stage>.md`, never to `main`.

## Superseded by the master brief — do not use

The prior working agreement (Amendment 1, Amendment 2, and the queue
structure this file held before the brief) is superseded in full. Two
concrete reversals worth naming so nobody re-derives the old answer by
habit: the earlier "keep the conservative fd-need assert" decision is
overridden by Section 1.3's `NW_BOOT_NEED(n,e)` formula; the earlier
Stage 1/2/3/4 ordering (pure-core refactor first, cgroups second, a
lock/unlock note, one format bump, four sign-off notes) is replaced by
Section 2/3's Phase 1/2/3/4 structure, which interleaves docs-only and
trusted-core items inside Phase 1 rather than doing all design notes
before all code.

## Stage 0 — DONE, landed on `main`, before the master brief

1. Edge fd-need term in `plan.als`/`Plan.tla`, oracle cross-check against
   `blob.h` — `de8a5e8`. Its formula is now superseded by
   `NW_BOOT_NEED(n,e) = NW_FD_RESERVED + n + 2*e` (Section 1.3); item 1d
   below is where that lands.
2. `nw-spawn`'s fd-ordering discipline (`pack_kit()`), pinned as far as it
   can be — `446b2ba`.
3. `scx_simple` GitHub Actions continuation, first two rounds: libbpf/
   bpftool version skew diagnosed, not fixed, workflow deleted —
   `ff5fc9c`. Section 4 is a third, differently-shaped attempt (a
   packaged binary from another distro's container, not a from-source
   build), not a repeat of the same approach.
4. `docs/QUEUE.md` seeded — `553071e`.

## THIS ITEM — docs/OPERATOR-BRIEF.md + QUEUE.md rewrite (Section 0's first action)

In progress: this commit. One `claims` pass before landing, per the
brief's own instruction for this item.

## Phase 1 — independent items, start now

Status of each, current as of this commit:

- **1a** (docs, `claims`): HISTORY §17 header note, `docs/options/11`
  status line, `nwsup.c` stale `extra_fd` comment, commitment 2 restated
  in `CLAUDE.md` and everywhere else it appears, report on where else
  commitments text lives (`NEXUSWEAVE.md` is not in this tree). NOT
  STARTED.
- **1b** (design note, `docs/options/22`, `claims` + batched questions):
  lock/unlock, the ten numbered questions from the brief. NOT STARTED.
  Blocks Phase 2 (Phase 2 requires this note approved).
- **1c** (trusted core: `control`+`tcb-review`+`fd-auditor`): FREEZE/CONT
  removal from `nw-sup` — verbs, ptrace code, exec-fence pipe/fd, the
  unconditional signalfd beside pidfd, and their tests. Keep the
  pidfd_open-failure fallback tiers. `docs/options/16` marked superseded.
  NOT STARTED. Blocks Phase 2 (Phase 2 requires this landed).
- **1d** (trusted core, AUTHORIZED edit to `pid1.c`): `NW_BOOT_NEED(n,e)`
  macro in `blob.h`, used by the `_Static_assert`, `tools/gen-spec-limits.py`,
  the baker, and the boot preflight; one new boot line printing
  `BootNeed`/`RLIMIT_NOFILE` at city open. NOT STARTED. Supersedes item
  1 of Stage 0 above (the fd-need term `de8a5e8` landed used the
  conservative assert this item replaces).
- **1e** (`bakery/`, `control` + one reviewer): baker refuses `lids=none`
  in a city plan, with an explicit lab flag for fixtures. NOT STARTED.
- **1f** (trusted core, `control`+`tcb-review`): control-socket hardening
  — `0700` directory, `umask(077)` socket creation, read-only `/nw/ctl`
  bind for console/launcher houses. NOT STARTED.
- **1g** (docs, `claims` + batched questions each): `docs/options/21`
  (output ownership; the `pid1.c` section is a brief for Grok, not an
  edit), `docs/options/23` (erofs file-backed bricks), `docs/options/20`
  (grants table + schema generator + the machine hash from Section 1.4).
  NOT STARTED.

No ordering dependency among 1a/1d/1e/1f/1g/Section-4 — any can run
concurrently; 1b and 1c gate Phase 2 specifically, not each other or the
rest of Phase 1.

## Phase 2 — pure-core refactor. Depends on 1b (approved) and 1c (landed).

`nw-sup`'s supervision decision becomes a pure function
`decide(lock, complete_on_0, stopping, stop_requested, start_requested,
has_child, child_exited, exit_status, deaths, budget)` returning one of
`FORK, WAIT, IDLE, RESTART, SPENT, EXIT_SUP, TERM_CHILD`. No clocks, no
I/O inside it. Behavior-preserving (every existing test passes
unchanged); until the format bump the shell always passes `LOCKED`, with
the `UNLOCKED` rows present and tested ahead of having a plan field that
can select them. An exhaustive event-sequence test, length justified in
the commit. Six named invariants (STOP never increments deaths; UNLOCKED
never increments deaths; SPENT, LOCKED-only, is absorbing; deaths rise
only on an unrequested LOCKED exit; a shutting-down supervisor never
forks or restarts; START on a running house is a no-op), each with a
CBMC harness and a mutation check that turns it red. NOT STARTED.

## Phase 3 — per-house cgroups. Depends on Phase 2.

`clone3`+`CLONE_INTO_CGROUP` placement, `nw-sup` itself outside the
cgroup. `mem_high`/`mem_max`/`cpu_weight`/`cpu_mask`/`nice`/`sched_policy`
applied in place with kernel read-back and a control against an
unlimited house each. No io limits, no groups, no pause (all cut per
Section 1.6). `cgroup.kill` for whole-house kill (version-gated, with a
freeze-then-kill fallback), used on restart/spent/stop escalation. Death
autopsy from `memory.events`/`pids.events`, delta-since-this-run,
"unavailable" rather than a false zero. Evidence format version bump.
All controls run in the QEMU guest. NOT STARTED.

## Phase 4 — the one plan-format bump. Depends on Phase 3.

`NW_MAGIC` bump, five-place discipline, ledger updated by tooling. Adds
`lock`, stop-signal + grace period (unset = today's behavior),
supervisor-death policy, per-house `nofile` (measure this init's current
default hard limit and verify Proton/Wine esync's real requirement from
primary sources before sizing the field). Deletes `io_rbps`, `io_wbps`,
`sched_ext` (supersedes `docs/options/15`'s reject path). Every
not-yet-applied field refused by name. Then, each with its own
design-note-plus-`claims` step before code: stop-grace escalation;
supervisor-death applier; run/stop profiles + minimal `nwctl`;
permission-diff tool (baker-side); self-verifying confinement
(full break-out set in boot tests, a cheap subset at every real start);
Landlock port/scope fields (only what a Landlock test can falsify);
device lid (reject path first); rootless houses (last; console and
compositor stay real-root). `provides`/`needs` service resolution per
`docs/options/20` — exactly one provider per service, baker prints the
edges it adds. NOT STARTED.

## Section 4 — scx side thread. Bounded: 3 push iterations, CI+docs only.

Third attempt at proving the sched_ext accept path, differently shaped
from the two already recorded in `docs/options/15`: a privileged Fedora
or Arch container on the free `ubuntu-24.04` runner, using that distro's
own packaged `scx` binary against the runner's kernel rather than
building from source. Confirm the package exists before attempting
anything else. Measurement only — Phase 4 deletes the `sched_ext` plan
field regardless of this result. NOT STARTED.

## Open questions carried forward

None blocking right now. Item 1b's own ten questions get answered inside
that note, then batched to the operator as that item's own step, not
listed here in advance.
