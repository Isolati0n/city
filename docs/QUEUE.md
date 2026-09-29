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

Base at last edit: `a8175bf` (origin/main).

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
5. `docs/OPERATOR-BRIEF.md` landed verbatim, `docs/QUEUE.md` rewritten to
   match (Section 0's first action) — `cbf931c`.

## Phase 1 — independent items, start now

Status of each, current as of this commit:

- **1a** — DONE, `3bcb850`. HISTORY §17 header note, `docs/options/11`
  status line ("design only" → "built"), `nwsup.c`'s `wait_house()`
  comment (it contradicted its own later paragraph about the control
  socket), commitment 2 restated verbatim as invariant 5's opening
  sentence in `CLAUDE.md`. Restating it required first fixing it:
  invariant 5's prior wording ("exactly three descriptors ... no third
  thing") was false the moment edges came back — a wired house holds
  3+k descriptors for k declared wires. Two `claims` rounds surfaced
  the same stale "edges are gone" premise copied into
  `.claude/rules/plan.md` (cycle-detection refusal), `.claude/rules/
  runtime.md` (nw-spawn-exits bullet), `nwspawn.c` (a second stale
  comment near the brick/bind env-var setup), and `docs/options/05`/
  `06` (citations of the old wording; 05's "invariant-5 question"
  section reframed, three further downstream echoes covered by one
  top-of-file flag note rather than patched individually). Report on
  where else commitments text lives: full quotes of commitment 2 exist
  in three places, not two — `docs/OPERATOR-BRIEF.md` (source),
  `CLAUDE.md` invariant 5, and `docs/options/05-house-persistent-storage.md`,
  which this same commit's own rewrite of "## The invariant-5 question"
  quotes it in full twice more — missed on the first writing of this
  line, found by a `claims` re-check of this exact bullet.
  `NEXUSWEAVE.md` is not in this tree, so there is no fourth file to
  reconcile.
  `make checkbrief`: 5 verified, 0 contradicted, 4 uncheckable.
  `make test`: EXIT:0, PASSED WITH SKIPS (only the recorded vfat-ESP
  skip).
- **1b** — DONE, `3ff6c47`. Blocks Phase 2 no longer — landed and
  approved (operator: proceed on the note's own recommended answers,
  no separate round-trip). `docs/options/22-lock-unlock.md` answers
  all ten numbered questions from the brief with file:line citations
  against the current code: (1) mount lifetime needs no change —
  `lid_brick()` already mounts fresh per fork and tears down via
  namespace exit plus `LO_FLAGS_AUTOCLEAR`; unlocked's idle state
  (item 6) is the only new mechanism. (2) the log ring has no run-
  boundary marker today; proposes one written into the existing log
  stream before every fork, with `write_evidence()` trimming to the
  last occurrence. (3) edges refused on unlocked houses, at bake and
  boot (an idle house's wire end sits open and unread). (4) two
  contradictions to refuse (unlocked+nonzero-budget, unlocked+edge); a
  third was looked for and not found. (5) `lock` is one byte, reusing
  the slot `sched_ext` frees in the same bump — size-neutral, `0` =
  locked so an unmodified city file keeps today's behavior. (6) the
  idle state reuses the existing `STOP`'d branch verbatim, gated on
  `lock` instead of always starting `0`; a new `STATUS` verb is
  proposed. (7) `tools/relaunch-house.py` should force `locked` in its
  own throwaway plan; profiles are undecided because nothing by that
  name exists in the tree yet. (8) the launch path (`nwctl`/driftwm/
  compositor) has no subject yet — none of them are built — flagged
  rather than answered speculatively. (9) a red-then-green test list
  per mechanism, including a 4-metric 50-cycle leak check and a full-
  suite locked-house regression run. (10) keep the existing
  environment-string pattern for the shell's inputs (answer only, no
  implementation). `claims` review found four citation-only slips (an
  off-by-one line number used twice, a broken internal cross-
  reference, a doc citation short by one line), all fixed; no claim
  about a future mechanism was found stated as already existing.
  Operator confirms: none of the ten answers rests on an unresolved
  design question — every one is either a concrete recommendation or
  correctly says the question has no subject yet (7's profiles half,
  8 in full), which is not the same as being undecided about something
  that exists to decide.
- **1c** — DONE, `941ecc8` (+ coverage record `df1a001`). Blocks Phase 2
  no longer — landed. Removed from `nw-sup`: FREEZE/CONT's ptrace code
  (SEIZE/INTERRUPT/CONT, `do_freeze`/`do_cont`,
  `is_ptrace_event_stop`/`forward_if_real_signal`), the exec-fence pipe
  and its per-fork setup/reset, and the two wire-protocol branches in
  `handle_ctl_live()` (live and "stopped" versions). `wait_house()`'s
  `signalfd` is once again purely the `pidfd_open`-failure fallback,
  not run unconditionally beside `pidfd` — the two are now mutually
  exclusive, and the poll array shrank from 3 members to 2 accordingly.
  The `pidfd_open`-failure fallback tiers and their tests are
  unchanged, per the brief. `docs/options/16` marked superseded (kept,
  not deleted). Deleted: the 12 `test_ctl_freeze_*`/
  `test_ctl_start_stop_unaffected_by_exec_fence` tests, and the
  now-orphaned `tests/delay_exec.so.c` fixture plus its
  `tools/rules-hook.sh` exemption entry. A real coverage gap the
  removal quietly opened was found and closed in the same change:
  `test_ctl_exec_resets_sigchld_mask` never forced `pidfd_open` to
  fail, so under the old unconditional-signalfd code its own
  precondition (SIGCHLD blocked in nw-sup's process) always held
  regardless of the per-fork reset it exists to pin; now that signalfd
  is fallback-only, the same test needed an LD_PRELOAD shim forcing
  `pidfd_open` to ENOSYS added to keep forcing that precondition —
  verified by mutating the reset out and watching the unmodified test
  stay green, then confirming the fix turns it red again. (That shim
  was `tests/block_pidfd.so.c` at the time; since consolidated into
  `tests/fault_inject.so.c` — see the LD_PRELOAD-consolidation entry
  below.)
  `.claude/rules/runtime.md` documents the restored shape and this fix.
  Reviews: `tcb-review`, `fd-auditor`, `control` — no findings from any
  of the three; `control` independently reproduced all three legs of
  the sigchld-mask fix's justification in an isolated scratch copy.
  `make checkbrief`: 5 verified, 0 contradicted, 4 uncheckable.
  `make test`: EXIT:0, PASSED WITH SKIPS (only the recorded vfat-ESP
  skip) — first run caught a real but self-inflicted failure (a stale
  `tools/rules-hook.sh` UNOWNED entry for the just-deleted fixture,
  still in git's index until `git add` recorded the deletion), fixed,
  second run clean.
- **1d** — DONE, `74413db`. `NW_BOOT_NEED(n,e) = NW_FD_RESERVED + n + 2*e`
  in `blob.h` (was `NW_FD_NEED`, `+2*n+2*e`), used everywhere invariant 3
  requires agreement (both `_Static_assert`s, the baker,
  `tools/gen-spec-limits.py`, `tools/bootneed-oracle.c`, both specs'
  `fdNeed`/`FdNeed`/`FdArithmetic`/`FdNeedAgrees`/`LargestCityFits`), plus
  the authorized `pid1.c` edit (pre-flight now calls the macro instead of
  hand-typing it, and a new boot line at "city open" prints
  `bootneed=`/`nofile_soft=`/`nofile_hard=`). Supersedes item 1 of Stage 0
  above (the fd-need term `de8a5e8` landed used the conservative assert
  this item replaced). `fd-auditor` found and fixed a real (currently
  unreachable) 32-bit-narrowing risk in the macro's internal arithmetic;
  `tcb-review` and `claims` found and fixed two further-out prose sections
  (`CLAUDE.md`, `.claude/rules/plan.md`) plus one agent brief
  (`.claude/agents/drift.md` + its `install-agents.sh` heredoc copy) that
  still quoted the old formula after the direct-site edits landed.
- **1e** — DONE, `123ddf2`. `bakery/nw-cc.py --city` refuses a house
  declaring `lids=none` unless `--lab` is also passed; `--probe` is
  unaffected (already requires an explicit `--lids`, inherently
  synthetic). Threaded through every tool that bakes or rebakes a city
  plan: `tools/stage-candidate.py` gained its own `--lab` flag (off by
  default, on in the test harness's `_stage()` wrapper);
  `tools/scale-probe.py` and `tools/fdorder-sweep.py` always pass it;
  `tools/relaunch-house.py` passes it unconditionally (its throwaway
  plan echoes an already-live unit's `lids=`, not a fresh deployment
  decision); `tools/fold-house.py` and `tools/mkboot.sh` are
  unchanged, since both rebake a real plan and should get no
  exemption. `tests/run.py`'s ~81 `--city` bakes all pass `--lab`
  (the suite is lab context); the one exception is the new control
  pair in `test_baker_rejects` — the same city file refused without
  `--lab` (naming it, leaving no blob at `--out`) and accepted with
  it, plus a `lids=newns` bake needing no `--lab` at all.
  `.claude/rules/plan.md` documents the rule and who gets the
  exemption and why. `control`: no findings across five mutation
  checks. `make checkbrief`: 5 verified, 0 contradicted, 4
  uncheckable. `make test`: EXIT:0, PASSED WITH SKIPS (only the
  recorded vfat-ESP skip).
- **1f** — DONE, `8a96877` (+ coverage record `abe4059`). `NW_CTL_DIR`
  is `0700` (was `0755`), fchmod'd unconditionally every boot via an
  `O_NOFOLLOW`-opened fd rather than `chmod(2)` by path (fd-auditor:
  the path form follows a symlink); the control socket's own
  `socket()`+`bind()` pair is wrapped in `umask(0077)`/restore so the
  socket file is `0600` regardless of the inherited umask; a declared
  `bind=` that resolves to `NW_CTL_DIR` — by `stat()` device+inode
  identity, not string comparison — gets remounted
  `MS_BIND|MS_REMOUNT|MS_RDONLY`. The identity check replaced an
  initial `strcmp` version after `tcb-review` found it bypassable by
  spelling (`bind=/nw/ctl/`, `//nw/ctl`, `/nw/./ctl` all resolve to the
  same directory but none string-match the constant). New test
  `test_ctl_dir_hardening` needs no lid (the restriction is mount-level,
  not Landlock), asserts an ordinary bind stays writable while a bind
  of `NW_CTL_DIR` is refused with `EROFS`, and that the directory is
  `0700` after boot — deliberately widened to `0755` first, since the
  directory persists on the machine root across the whole suite run and
  a stale-correct value could otherwise mask a missing chmod.
  `.claude/rules/runtime.md` and `docs/options/11` document the
  mechanism and its relationship to that document's already-recorded
  access-control gap (every house is uid 0, so this closes nothing
  about what a brickless house can already reach — it only closes
  access for a uid this design does not have yet). `control`'s own
  review run damaged the shared stage mid-run (a variable-name mixup
  against the Makefile, not a code defect) before completing any
  mutation test; no findings from it to act on. `make checkbrief`: 5
  verified, 0 contradicted, 4 uncheckable. `make test`: EXIT:0, PASSED
  WITH SKIPS, run twice clean (one interleaved unrelated FAIL —
  evidence-captures-death-output — did not reproduce in three isolated
  reruns or a second full run, and coincided with the stage damage
  rather than this diff).
- **1g** (docs, `claims` + batched questions each): `docs/options/21`
  (output ownership; the `pid1.c` section is a brief for Grok, not an
  edit), `docs/options/23` (erofs file-backed bricks), `docs/options/20`
  (grants table + schema generator + the machine hash from Section 1.4).
  - `docs/options/21`: **DONE**, this commit. Reads "nw-sup owns each
    house's output" as interpretation/disposition, not capture — capture
    structurally cannot move to nw-sup (its fd 1/2 are the log pipe's
    write end only, `nwspawn.c:224-225`; the logger predates nw-sup's own
    existence, `pid1.c:770-826`), which docs/options/12 already
    established. The run-boundary problem is already solved inside
    nw-sup by docs/options/22 §2's marker line, with no pid1.c change.
    Finding for Grok: **no pid1.c/dawn.c change requested** by this note
    — stated as the brief, rather than silently omitted, plus one
    optional, explicitly-not-recommended question (on-disk per-run
    tail trimming) left open for anyone who wants it. `claims` review
    found 6 citation/line-number defects (all off-by-a-few-lines or a
    wrong section attribution, one small misquote) and no substantive
    error; all fixed.
  - `docs/options/23`: **DONE**, this commit. The prior hold
    (`docs/plans/01-brick-images.md`'s own "HELD until after phase 3")
    is satisfied — phase 3 landed. Measured directly on this container:
    `mount -t erofs <file>` silently loop-attaches (util-linux doing the
    same `LOOP_CTL_GET_FREE`/`LOOP_CONFIGURE` dance `nwsup.c` does by
    hand); the genuine loop-free mechanism is raw
    `fsopen`/`fsconfig(FSCONFIG_SET_STRING, "source", ...)`/`fsmount`/
    `move_mount`, confirmed working and loop-free
    (`CONFIG_EROFS_FS_BACKED_BY_FILE=y`). No primary-source citation
    exists anywhere in the tree for the prior "kernel >= 6.12" figure —
    said plainly rather than repeated, with the config symbol and this
    note's own measurement offered instead. Verified bricks (fs-verity,
    the amendment's item E): unmeasurable on this container at every
    layer (`CONFIG_FS_VERITY` unset, no `fsverity` CLI, no verity flag
    in this `mkfs.erofs`) — stated as a hard gap, not assumed working;
    the digest/`NW_BRICK_HASH` relationship is structurally different
    (Merkle-tree descriptor vs. a flat whole-file sha256) and neither
    reduces to the other. No pid1.c/dawn.c/Grok involvement — this is
    Claude's own mount-path territory in `nwsup.c`, not built this
    round. `claims` found one section misattribution (a real quote,
    wrong CLAUDE.md section) and two minor citation overstatements;
    all fixed. Every environment claim (config flags, the loop-fallback
    trap, the raw-syscall mechanism, the fs-verity gap) was
    independently reproduced by the reviewer on this same container,
    matching down to exact device numbers and dmesg lines.
  - `docs/options/20`: **DONE**, this commit. Reconciles the brief's
    four-part schema (applier/observer/reject path/test) against
    CLAUDE.md's existing five-clause mechanism rule: applier and test
    already map onto it; reject path and observer are unnamed there but
    already practiced (reject path widely, via `NW_E_*` codes and real
    refusals like `apply_sched_ext()`'s; observer unevenly, e.g. the
    brick hash's own re-validation). Recommends the schema generator as
    a stricter, machine-checkable form, not a rewrite of CLAUDE.md's
    prose. A grants table inventories every current plan field against
    all four; the one open gap it makes visible in table form (every
    `nw_res` field but `layer_bytes` has a reject path and no applier)
    is Phase 3's already-scheduled work, not a new finding. Machine
    hash (decision 4): the baker's bake-time sha256 print already
    satisfies it; the boot-time nw-spawn/evidence half is genuinely new
    code; flagged the one real open reading — "NO sidecar hash file"
    is read as scoped to the new boot-time hash, not the existing,
    load-bearing bakery/staging `.sha256` sidecar those tools already
    depend on. `provides`/`needs`: designed as pure baker-side sugar
    compiling to ordinary edges (no new blob field, no `NW_E_*` code,
    no runtime change), explicitly distinguished from the still-deferred
    `gate` field it is not. `claims` found five defects (a Phase-3/
    Phase-4 mislabel, a stated-vs-actual grep-scope mismatch, one
    proposed-not-practiced rule cited as practiced, one overstated
    Observer claim, one overreached ownership inference); all fixed.

    **§6 added, this commit, per direct instruction**: two capability-lid
    facts for the not-yet-built capabilities field. (a) A measured gap —
    dropping a house's entire capability bounding set does not make a
    sibling's `/proc/<pid>/status` unreadable (confirmed against
    `fs/proc/base.c`'s `has_pid_permissions()`: at this tree's default
    `hidepid=0`, no ptrace check runs at all). Decision: accept for v1 —
    process-visibility isolation is a PID namespace's job, not a
    capability set's, and Phase 4's "rootless houses" slice is where
    that would eventually land if taken up. (b) A settled fact — a
    process ptracing its own direct descendant under matching uid/gid
    needs no `CAP_SYS_PTRACE`, robustly under every plausible reading of
    "the target kernel leaves YAMA off" (both readings, and even the
    stricter scope-1 default, agree), cited to `ptrace(2)` and
    `Documentation/admin-guide/LSM/Yama.rst`. Explicitly does NOT assert
    that Wine's own process topology matches this premise — flagged as
    a separate, unverified question for whoever needs the answer for a
    real deployment. A first draft of (b) claimed a false safety
    boundary ("does not extend between separate houses") that its own
    quoted scope-0 text contradicted, silently equated "YAMA off" with
    a specific `ptrace_scope` value, pinned an unverified `hidepid=`
    integer, and overclaimed CLAUDE.md's mechanism-rule clause 3 — a
    `claims` pass found all four (plus one already-caught wineserver
    premise issue) and a second `claims` pass confirmed all five fixed.

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
CBMC harness and a mutation check that turns it red. **DONE, this
commit.**

Built as new files `decide.c`/`decide.h` (`nw_decide()`, a genuinely
stateless function — no static or global variable of any kind, checked
directly), wired into `nwsup.c` at every real decision point (the
top-of-loop shutdown check, the idle/`stopped` branch's START handling,
the post-fork race-window check, the post-`wait_house` cascade, and
`handle_ctl_live()`'s STOP branch), replacing the inline conditional
logic that used to live there. `nw_lock` is hardcoded to `1` (`const
int nw_lock = 1;`) until the plan-format bump adds a real field; the
`UNLOCKED` rows are proven and tested regardless, ahead of anything
selecting them. Behavior-preserving: every pre-existing test passes
unchanged, confirmed by three full `make test` runs across the review
cycle. `deaths` keeps its exact pre-refactor shape in `nwsup.c`
(`int deaths = 0;`, one increment, nothing else) so CLAUDE.md
invariant 4's existing checkbrief annotations keep matching; the
budget *comparison* moved into `decide.c` and invariant 4's prose and
annotations were updated to say so precisely, including which specific
mechanism (CBMC vs. the exhaustive event-sequence test) proves which
specific claim — the first draft overclaimed that CBMC proves the
budget threshold, and a `claims` pass caught it.

**The exhaustive event-sequence test** (`tests/decide_seq.c`,
`test_decide_exhaustive_sequences`): 10000 sequences (5-symbol
alphabet, length 4, both lock states, both `complete_on_0` states,
budget 0..3) — enough to reach `SPENT` via consecutive deaths at every
tested budget, place `TERM` at every position, and interleave
STOP/START with a death inside one run, in well under a second.
Asserts the priority order decide.h documents (`stopping` >
`stop_requested` > clean-oneshot > `lock` > budget) directly at every
child-exit event, not only the generic budget/lock bookkeeping a first
draft had — `control` found the first draft silently accepted a
mutation reintroducing "STOP counts as a death" and one disabling the
oneshot-clean-exit check (both still caught by the pre-existing real-boot
tests, but not by this new machinery, despite its own docstring implying
otherwise); both are fixed and independently re-verified to catch those
exact mutations.

**Review, in full**: `control` found the two decide_seq.c gaps above
(fixed) and one real, previously-uncovered wiring gap — `nwsup.c`'s
`NW_DECIDE_IDLE` case resetting `stop_requested = 0` was unprotected
by anything in the suite, discovered by mutating it out and finding
every existing test, including a ~60-test prefix of the full suite,
stayed green. New test `test_ctl_stop_requested_clears_across_a_relaunch`
closes it deterministically (a second, genuine STOP against a
relaunched house, which `handle_ctl_live()`'s own `!stop_requested`
guard would silently swallow if the flag were never cleared) — verified
to catch the exact mutation `control` found, without relying on the
fork-to-exec signal-timing race a naive "does the relaunch survive"
test would have been. `tcb-review` found the refactor otherwise clean
(traced every call site against the pre-refactor behavior; ran the
CBMC proof directly plus its own mutation control; found one LOW
cosmetic asymmetry — the top-of-loop stopping check discarded
`nw_decide()`'s answer instead of switching on it — fixed to match the
other four call sites). `claims` on the CLAUDE.md invariant 4 edit
found the overclaim above (fixed), a stale TCB-boundary table missing
`decide.c` (fixed), and `proofs/caller_decide.c`'s docstring undercounting
its own assertions (six invariants, seven asserts — invariant 4 is two
independent asserts; fixed, and `proofs/README.md` gained the row it
was missing for this harness). `make checkbrief`: 5 verified, 0
contradicted throughout. `make test`: `PASSED, WITH SKIPS`, run three
times clean across the review cycle. `make prereport`: clean, every
finding acked with a stated reason.

## Tooling — LD_PRELOAD shim consolidation + stage-path isolation

Two small, scoped test/tooling fixes, neither touching trusted-core
behavior. **DONE, this commit.**

1. **Stage-path isolation** — `tests/run.py` defaults `NW_STAGE` to
   `/tmp/nw-init-run` for everyone, which caused a real collision
   between concurrent review-agent runs. This is a dispatch-discipline
   fix, not a `tests/run.py` code change: every reviewer dispatched
   concurrently in this session (and the two `control`/`claims` runs
   for this very consolidation) was given an explicit, unique
   `NW_STAGE`/`STAGE` path. No change to the default itself — nothing
   surfaced a concrete need for auto-uniquifying it, so none was added.
2. **LD_PRELOAD shim consolidation** — `tests/count_wait.so.c`,
   `tests/block_pidfd.so.c` and `tests/block_both.so.c` (each a
   near-identical hand-rolled `dlsym(RTLD_NEXT, ...)` interception for
   a different syscall) are replaced by one reusable shim,
   `tests/fault_inject.so.c`, configured by two environment variables
   read at runtime (`NW_FAULT_ENOSYS`, a comma-separated
   `{pidfd_open,signalfd}` list; `NW_FAULT_LOG_WAITPID=1`). All five
   real call sites migrated (`test_pidfd_open_failure_falls_back`,
   `test_ctl_exec_resets_sigchld_mask`, `test_ctl_pidfd_fallback_with_socket`,
   `test_ctl_tier3_fallback_with_socket`, `test_wait_is_poll_not_spin`).
   The three old files deleted in this same commit, after — not before
   — the replacement was proven equivalent.

   Controls, run before deletion: all five migrated tests pass
   unchanged with the new shim. Mutating `fault_enosys_wants()` to
   always return 0 (disabling all ENOSYS-forcing) turns four of the
   five red on their own paired-absence checks ("shim never fired" /
   "never announced a waitpid() call"); deleting the shim file entirely
   turns the same four red at compile (`fault_inject.so.c: No such file
   or directory`) and the fifth (`wait-is-poll-not-spin`, which
   tolerates a missing shim via `os.path.exists`) red on its own
   positive-proof check instead. `test_ctl_exec_resets_sigchld_mask`
   stays green under the ENOSYS-neutering mutation — a real, pre-existing
   gap (it asserts real `SigBlk` state rather than a shim-fired log
   line, so if `pidfd_open` genuinely succeeds the precondition the test
   needs never occurs), independently confirmed by `control` to be
   identical on the OLD `tests/block_pidfd.so.c` under the equivalent
   neutering mutation — not something this consolidation introduced or
   made worse. Worth a follow-up paired check on that one test; not
   this round's scope.

   Reviews: `control` (confirmed the above, no other divergence between
   old and new shim behavior across all five call sites) and `claims`
   (one MEDIUM — the shim's header comment claimed "no libc wrapper" for
   `pidfd_open`, false on glibc >= 2.36; fixed to state the real reason,
   that `nwsup.c` calls the raw syscall directly rather than the libc
   symbol; one LOW — an unnamed instance-count in
   `.claude/rules/runtime.md`'s "three one-syscall files," fixed to name
   all three; one LOW/informational — this file's own reference to
   `tests/block_pidfd.so.c` as "an existing shim" went stale the moment
   this diff deleted it, fixed here). Both reviews run concurrently on
   isolated stage paths per item 1 above.

   `make test`: EXIT:0, PASSED WITH SKIPS (only the recorded vfat-ESP
   skip). `make checkbrief`: 5 verified, 0 contradicted, 4 uncheckable.
   `make prereport`: clean, no shapes matched.

## New design notes 27–30 — operator-approved

Dispatched alongside the tooling fixes above. Two-commit shape per note
(design note, then a `claims` fixes commit) as usual.

- **27 — control-app** (phone control panel + plan editor). **GATED —
  design only, no code.** Needs Phase 4's `nwctl` and the grants table
  (`docs/options/20`) before it can be built; neither exists yet.
  Transport is decided, not merely recommended: a tunnel (e.g.
  Tailscale) is the sole transport, no LAN-only fallback as a primary
  path. The tunnel daemon and the app's own listener share one house
  (`lids=newns,landlock,newnet`, no seccomp — the console house's own
  combination, for the same syscall reason: both need `socket`/`ioctl`,
  neither of which `lids.c`'s allow-list carries), because nothing in
  this plan format lets two houses share a network namespace.
  `docs/options/27-control-app.md`.
- **28 — hardware watchdog.** **No gate — proceeds independently.**
  `docs/options/28-hardware-watchdog.md`. Explicit about not being
  freeze detection (a different question at a different level: "is the
  scheduler running at all," not "is this house making progress").
  Falsifying test cannot run on this container (measured: no watchdog
  driver compiled into this kernel at all, `CONFIG_SOFT_WATCHDOG` unset,
  no module directory for the running kernel) — handed to the operator
  as a fixture per `.claude/rules/harness.md`'s existing pattern for a
  claim needing a real boot.
- **29 — rescue interface.** **Foundation resolved by the operator,
  design otherwise unblocked; no code this round.** First draft
  reported "the console house's existing rescue-slot mechanism" as not
  existing in the tree; the operator corrected this by reading
  `rescue.c`/`run_rescue()` directly — `nw-rescue` is real, wired, and
  tested today (`test_rescue`), just a deliberate placeholder, and this
  note is its real design, not a fourth mechanism. Reading
  `run_rescue()`'s own control flow closely in response also reversed
  an earlier recommendation: rescue mode boots no plan and starts no
  houses at all (`pid1.c`'s dispatch returns before the normal
  plan-loading path runs), so there is no live `nw-sup` to query and no
  case for "extend `nwctl`" — the design is now a separate, small
  companion tool sharing `nwctl`'s conventions and `tools/unit-info.c`'s
  blob-reading code, not `nwctl` itself, and no longer gated on Phase
  4's `nwctl` at all. Local/serial reachability stays the recommended
  transport, now for a stronger reason than first drafted (no plan
  means no house could be running a tunnel daemon in this mode even in
  principle). **One genuinely new, narrower question is open**: growing
  `rescue.c`'s own body would violate `.claude/rules/runtime.md`'s
  already-enforced TCB-minimality rule for that exact file; this note
  recommends the smallest fix that keeps the rule true — `rescue.c`
  gains exactly one `execv()` of a non-TCB companion binary, nothing
  else — and flags that landing it needs that rule amended (with
  `tcb-review`) before code, which is the operator's call, not this
  note's to make silently. `docs/options/29-rescue-interface.md`.
- **30 — backup tool.** **No gate — proceeds independently.** Builds on
  the shared content-addressed store (landed). `docs/options/30-backup-tool.md`.
  Distinguishes the store (genuinely content-addressed, "diff hash
  lists" applies exactly) from layers (name-keyed, mutable — needs a
  cheap change-detection signal instead) rather than forcing one
  mechanism onto both.


Reviewed by `claims`: one HIGH (note 27's status line spliced two
unrelated sentences — one real, from `docs/options/22`, one from
`docs/QUEUE.md`'s own item 8 on a different question — into a single
fabricated composite quotation attributed to one source; fixed by
attributing each clause to its real source and re-confirming the
underlying claim, `nwctl` genuinely absent, by a fresh grep) and one LOW
(note 29's Makefile-grep characterization undersold the command's real
4-line output; fixed to quote it in full and explain each line). No
other findings across all four notes; the reviewer's own coverage
summary lists every checkable claim it verified.

Note 29 re-reviewed after the operator's rescue-foundation correction
(above): one HIGH (the note's argument for why the proposed companion
binary would be non-TCB leaned on "no boot-time caller links it in,"
which does not hold for its own cited precedent, `tools/initrd-init.c`
— the kernel calls it automatically and it itself execs into `dawn`;
fixed by re-grounding the argument in `CLAUDE.md`'s TCB table being a
closed, explicit enumeration rather than a caller-derived rule, and
flagging that `tcb-review` should re-examine specifically this point),
one MEDIUM (`--rescue DIR` is not literally required to be the sole
argument — the dispatch only checks `--plan`/`--slot`/`--slots` are
absent, confirmed by actually running `nw-root --rescue <dir>
--hold-ms 400` and reaching rescue mode anyway; fixed), and one
LOW/MEDIUM (a quoted `sed` line range that stopped short of the
`_exit(...)` line and closing brace the note's own code block showed;
fixed to the range that actually reproduces it). `make prereport` and `install-agents.sh
--check` clean after fixes.

Notes 27 and 30 amended by the operator (verified design analysis, not
a plain instruction): **27**'s §3 Authentication is now a deliberate,
named decision — the tunnel's own identity (Tailscale) is the sole
authentication, no separate app-level token — citing Home Assistant's
`trusted_networks` auth provider (home-assistant/core#15812) as
precedent for "declare the tunnel identity AS the auth, explicitly,"
plus an optional additional app-layer check using Tailscale Serve's
identity-forwarding headers, verified against Tailscale's own current
docs rather than assumed. **30** gained a quiesce step (STOP the house,
back up, START it again, closing a mid-write-corruption problem a
torn read cannot fix by re-running), a staged-path-plus-atomic-rename
restore (never in-place, with a kill-mid-restore control and a
pre-restore snapshot as the undo path), and `stat()`-not-`lstat()` when
backing up a layer root (a symlinked root's target content, not the
8-byte link). Reviewed by `claims` on just the changed sections: two
HIGH (§3's residual-risk paragraph misattributed the `SO_PEERCRED`/
no-token quote and its "One gap worth naming" heading to
`docs/options/29-rescue-interface.md`, which has neither — the quote
and heading are `docs/options/11-start-stop-channel.md`'s own
Authorization section, fixed by re-citing the real source; and 30's
quiesce step claimed the STOP/START channel was "the same mechanism"
29's rollback question relies on, when 29's rollback actually depends
on `tools/stage-candidate.py`'s slot-switching, which 29 itself says
is *not yet built* — fixed by citing STOP/START on its own terms and
naming what 29 actually depends on and its own unbuilt status) and one
MEDIUM (30's descendants paragraph cited `.claude/rules/runtime.md`'s
whole-machine-shutdown orphan material to support a claim about a
single house surviving a per-unit STOP, which signals only the house's
own top pid, never a process group — fixed to state plainly that a
descendant can survive a STOP and that the cited material is about
machine shutdown, not this case). Also corrected, found by the same
pass: a stale `nwsup.c`/`blob.h` line-number citation in 30's opening
paragraph, drifted since the note was written and now current. Both HA
citation dates (the note's own explicit 2018-not-2017 correction to
the operator's stated 2017) and the Tailscale Serve header names were
independently verified against primary sources and confirmed accurate.
`make prereport` (one `prose-count` false positive on 27's "the one
place" idiom, acked) and `install-agents.sh --check` clean after fixes.

## Phase 3 — per-house cgroups. Depends on Phase 2.

`clone3`+`CLONE_INTO_CGROUP` placement, `nw-sup` itself outside the
cgroup. `mem_high`/`mem_max`/`cpu_weight`/`cpu_mask`/`nice`/`sched_policy`
applied in place with kernel read-back where controller-backed. No io
limits, no groups, no pause (all cut per Section 1.6). `cgroup.kill`
for whole-house kill (version-gated; no fallback path has ever run on a
kernel that actually lacks `cgroup.kill` — this container's own kernel
(6.18) has it, so that branch is read, not exercised), used on
restart/spent/stop escalation. Death autopsy from
`memory.events`/`pids.events`, delta-since-this-generation,
"unavailable" rather than a false zero. Evidence format version bump
(`NWEVT1` → `NWEVT2`).

**Implemented and landed on this machine; `make test` green (full run,
`PASSED, WITH SKIPS` — the one skip is the pre-existing, unrelated
FAT-ESP gap).** `mem_high`/`mem_max`/`cpu_weight` were NOT verified
controller-backed in this round: this container's own cgroup v2 offers
only `hugetlb` (`cpu`/`memory`/`io` are on v1 hierarchies), so a plan
declaring `mem-high`/`mem-max` dies at boot here naming
`cgroup memory controller unavailable`, and one declaring `cpu-weight`
names `cgroup cpu controller unavailable` (two separate strings in
`nwsup.c`, not one combined message) — a genuine refusal, not a bug,
verified directly (baked a plan with `mem-high=64M`, booted it, got
exactly the first of those two). `cpu_mask`/`sched_policy`/`nice` are
plain syscalls and succeed everywhere, including here.
`tools/HANDOFF-resources.md`'s fixture for the three controller-backed
fields (which needs real memory/cpu delegation) was NOT run in a QEMU
guest in this round — that remains for whoever has the machine, same
as it was before Phase 3. A third, undisclosed-until-now gap of the
same shape: the brief's own required control ("a house OOM-killed once
and then crashing plainly must report a crash the second time") has no
test either. `grep -n "oom_kill\|pids_max" tests/run.py` finds only a
comment about the NWEVT2 magic bump — no test reads either field's
actual *value* back out of an evidence package. The format is verified
correct (`test_evidence_captures_death_output` and its neighbours
parse `oom_kill=`/`pids_max=` without erroring), but nothing asserts
what value they hold.

A genuine, real-kernel finding along the way, not a QEMU gap: writing
"1" to a cgroup's own `cgroup.kill`, even on an empty cgroup,
permanently SIGKILLs the next process ever placed into that SAME
directory (invisible in `cgroup.events`/`cgroup.freeze`; only
destroying and recreating the directory clears it) — reproduced in a
standalone program with no nw-sup code at all. Fixed by giving every
house-restart generation its own freshly created cgroup leaf, never
reused; `.claude/rules/runtime.md`'s resource-block section carries the
full mechanism and reasoning.

That fix changed a heavily-documented, tested invariant's own
demonstration: the whole-house kill now runs on every restart (per
Section 3's own wording), so a house's forked-and-abandoned orphan is
caught at that house's OWN next restart rather than surviving to the
outer shutdown. `test_orphans_across_restarts` case B needed a
different fixture (`unit-orphanhang`, parent also ignores TERM and
outlives the test) to keep demonstrating "PID 1 does not wait" — now
via PID 1 SIGKILLing a stuck supervisor directly, one layer further
down than before.

**Reviewed by the full TCB set (control, tcb-review, fd-auditor,
claims), all four in parallel, per the dispatch table.** Findings and
fixes:

- **fd-auditor**: no fd-collision or leak in the class this project has
  already paid for (bugs 5/9/13) — `cgroup_fd` is closed on every path
  including clone3 failure, every helper's own fd is paired. Two LOW:
  a directory (not an fd) leaked on a die() before clone3 ever placed a
  process, and `NW_CGROUP_DIR` hardcoded independently in three places
  (blob.h, tests/cgroup_premount.c, tests/run.py) with nothing keeping
  them in sync — the second is now the first entry in a new "resource
  not torn down on an error path" finding class this round's own
  `.prereport-ack` records.
- **tcb-review HIGH, fixed**: the directory-leak fd-auditor flagged as
  contained (no live path to hit it) was reproducible and serious —
  `house_cgroup_open_generation()`'s own die() calls (mem-high/mem-max/
  cpu-weight write or readback failure, or clone3() itself failing)
  left that generation's cgroup directory behind forever; the SAME
  unit started again (relaunch-house.py, or any future respawn) hits
  EEXIST on generation 0 and is permanently bricked until the machine
  reboots — and the harness's own `cgroup_premount.c` cleanup wipes the
  exact evidence that would show it, so `make test` could not have
  caught it. Reproduced directly (force clone3() to fail via
  LD_PRELOAD on a plan with NO resource fields at all, confirm the
  leftover directory, boot the identical plan again with the fault
  removed — second boot dies `cgroup generation dir errno=17`).
  **Fixed**: a `die_cgroup()` helper `rmdir`s the just-created,
  never-populated directory before dying, for every failure between
  `mkdir()` succeeding and `clone3()` ever placing a process into it —
  the one window where a plain `rmdir` is unambiguously safe, because
  nothing could have written `cgroup.kill` to a directory that was
  never populated. Re-reproduced against the fix: second boot now
  succeeds cleanly (`status=0`). One LOW (missing `#include <stdio.h>`
  in `tests/cgroup_premount.c`), also fixed.
- **control**: the `.prereport-ack` mechanism-claim for the
  per-generation-cgroup fix named three tests as pinning it
  (budget-no-reset, crash-does-not-halt, orphans-across-restarts);
  only the last one actually does — the other two only check restart
  counts and timing, never `exit=` vs `signal=`, so a death silently
  turned into a SIGKILL by a reintroduced poisoning bug reads the same
  as a real one to their own assertions. Ack narrowed to name only the
  test that pins it. Separately: the ambient cgroup2 mount
  (`_ensure_ambient_cgroup2()`) is real and necessary — proved by
  reverting it in a namespace where the mount was explicitly
  `umount`ed first, which failed exactly as expected — but the mount
  itself outlives the test process and persists in the container's own
  mount table, so a later re-run in the SAME container cannot
  re-verify it: a green run there is riding on an earlier run's own
  side effect, not re-exercising the fix. Documented in both
  `tests/run.py`'s own comment and a new `.claude/rules/harness.md`
  section ("A cgroup2 mount is durable fixture state too").
- **claims**: two HIGH factual errors, fixed. Both `.claude/rules/
  plan.md` and `.claude/rules/runtime.md` claimed `grep -n "res\."
  nwsup.c` finds real reads for the six enforced fields — checked
  directly, it finds nothing, because `nwsup.c` never spells `res.` at
  all; it reads six `NW_*` env vars via `getenv()` into local C
  variables, and it is `nwspawn.c` (7 hits) that reads the sealed
  unit's `struct nw_res` and forwards each field as an env var. Both
  files corrected to name the actual mechanism and a grep that finds
  it. Also fixed: a die() message quoted as one combined string
  (`cgroup memory/cpu controller unavailable`) when it is two separate
  strings in `nwsup.c`; a comment in `nwsup.c` citing "this container's
  own guest kernel (6.8)" when this container's kernel is 6.18 and
  6.8.0-139 refers to a different, separately-reported machine (the
  operator's QEMU guest); a stale comment in `dawn.c` ("no cgroup logic
  exists in nw-sup and none should be added") directly contradicted by
  this round's own cgroup logic added to `nwsup.c` (`git diff --stat`
names the file); and an
  undisclosed gap in this entry itself — the brief's required control
  ("a house OOM-killed once and then crashing plainly must report a
  crash the second time") has no test reading back the `oom_kill`/
  `pids_max` *values*, only their format. One intermittent failure
  reproduced during review (`test_ctl_stop_requested_clears_across_a_
  relaunch`, "first generation never reached exec") could not be
  reproduced in 15 isolated re-runs or a fresh full-suite re-run —
  consistent with a rare scheduling spike (fork-to-exec measures 2-4ms
  under no load, two orders of magnitude under the check's old 0.2s
  wait) rather than a deterministic regression. That check's own
  margin was tighter than its sibling checks in the same test (0.2s vs
  0.3s) before Phase 3; widened to match them. Verified the widening
  does not mask the failure class it exists to catch: forcing clone3()
  to fail via the same LD_PRELOAD fault used above still reports `FAIL:
  first generation never reached exec` at the new, wider threshold.

`make test` green after all fixes (full run, `PASSED, WITH SKIPS`, the
same pre-existing FAT-ESP skip; `EXIT=0`, read directly rather than
through a pipe to `tail`). `make prereport` and `install-agents.sh
--check` clean.

## Phase 4 — the one plan-format bump. Depends on Phase 3. NO extra bump —
the 2026-09-28 amendment's new fields ride inside this same one.

`NW_MAGIC` bump, five-place discipline, ledger updated by tooling. Adds
`lock`, stop-signal + grace period (unset = today's behavior),
supervisor-death policy, per-house `nofile` (measure this init's current
default hard limit and verify Proton/Wine esync's real requirement from
primary sources before sizing the field), and, per the amendment, three
more fields each with its own applier/read-back/reject-path/test:
**capabilities** (an allowed-powers set, applied in the child after
mounts/cgroup placement/lids and before exec, read back from
`/proc/<pid>/status`'s `CapBnd`/`CapEff`/`CapPrm`/`CapAmb` — design
questions on how the plan spells the set, an unknown-to-the-kernel name,
and drop order, answered before code; a compositor's and a Proton/Wine
game's real needs listed from primary sources and marked unmeasured
until run on real hardware, since QEMU has no GPU); **task cap** (a
house's `pids.max`, needs Phase 3's cgroup placement); **OOM priority**
(`oom_score_adj`, written before capabilities are dropped, with the
kernel's own documented ordering effect cited and its actual ordering
marked not deterministically testable). Deletes `io_rbps`, `io_wbps`,
`sched_ext` (supersedes `docs/options/15`'s reject path). Every
not-yet-applied field refused by name. Then, each with its own
design-note-plus-`claims` step before code: stop-grace escalation;
supervisor-death applier; run/stop profiles + minimal `nwctl` (now
including `nwctl why <house>` — a plain-English reason from the existing
STATUS reply, the last crash record and boot refusals, no new protocol —
and `nwctl times`, showing the four monotonic timestamps per start
[fork/mounts-done/lids-applied/exec] nw-sup would record, measurement
only, never feeding a decision); permission-diff tool (baker-side;
reports widenings separately, and gains an audit "exposure report" mode
per the amendment — capabilities/lids/writable binds/devices/edges/
network/uid per house, each graded by a documented deterministic
formula a test pins, stated plainly as a report and not a proof);
self-verifying confinement (full break-out set in boot tests, a cheap
subset at every real start); Landlock port/scope fields (only what a
Landlock test can falsify); device lid (reject path first); rootless
houses (last; console and compositor stay real-root). `provides`/`needs`
service resolution per `docs/options/20` — exactly one provider per
service, baker prints the edges it adds.

**Design note landed: `docs/options/31-phase4-plan-bump.md`.** Two-commit
shape as usual (`5608ec7` draft, `0bfd65d` `claims`-review fixes — the
review found the note restating a ptrace/capability overclaim that
`docs/options/20` §6's own drafting history had already caught once, and
quoting `docs/options/15`'s sched_ext justification from a framing that
document's own newest section has since superseded; neither changed the
note's decisions). Covers the bump mechanics and every field this initial
pass builds a real applier for (`lock`, `stop_signal`, `nofile`,
`capabilities`, `task_cap`, `oom_score_adj`) plus the two added as a
format slot only, refused at nw-sup startup by the existing `sched_ext`
precedent until their own later rounds land an applier (`grace_period`,
`supervisor_death_policy`). Batched open questions sent to the operator
per Section 5. **This was true at the time this paragraph was
written; the code has since landed — see the paragraph below.**

**Operator answered the five batched questions (with two additions),
and the code landed: `debf96f` records the decisions (including the
oom_score_adj-vs-capabilities fault-injection verification the operator
asked for by name — the recalled "Grok measurement" does not hold for a
negative value on this kernel, confirmed rather than assumed), `6fbb7c7`
is the `NW_MAGIC` bump (`NWPLAN11` -> `NWPLAN12`) plus every applier
this round builds.** Five review agents ran against the diff before the
commit (tcb-review, fd-auditor, claims, control, drift), per the master
brief's TCB dispatch table, and found one real HIGH-severity bug fixed
before landing: fd-auditor caught the original `nofile` applier
unconditionally setting `RLIMIT_NOFILE` before `lid_brick()`/
`lid_landlock()` ran, so a tight but legal `nofile=` could starve those
lids' own descriptor needs — reproduced live as
`FAIL open loop-control errno=24`, misdiagnosed as a loop-device
defect, burning the restart budget. Fixed by splitting the applier into
an early raise (while `CAP_SYS_RESOURCE` is still held) and a deferred
lower (after every lid that needs headroom, immediately before
`execv`), pinned by `test_tight_nofile_does_not_starve_the_brick_pivot`
and shown failing against the pre-fix applier. A second, latent finding
(a shift-by-64 UB when `cap_last_cap == 63`) was also fixed. `control`
found `test_oom_score_adj_readback` does not actually pin the
write-before-capabilities ordering in this environment (a positive
value needs no capability either side of the drop); the test's
docstring now says so rather than implying coverage that isn't there —
the ordering *decision* itself still rests on the one-time
fault-injection measurement in `debf96f`/§10 item 3, not on a
regression test. `claims` and `drift` found and fixed several stale
prose claims across `docs/options/31`, `.claude/rules/runtime.md` and
`.claude/rules/plan.md` (a capability-drop ordering description still
saying "after seccomp" after the real fix moved it before; an
"ambient" capability claim the code never implements; a
described-but-never-built `NW_E_NOFILECAP` cross-field refusal,
superseded by ordering instead; stale `NW_E_*` numbers after the
`sched_ext` retirement's renumbering shift; a field count and a
retired `io_rbps`/`io_wbps` worked example in the two territory rule
files, neither touched by this bump until the review found them).
`make test` passes end-to-end (113 `ok` lines, `coverage-tcb.sh` at the
99% floor); `make prereport` and `make checkbrief` are clean.

`grace_period` and `supervisor_death_policy` still have no applier —
refused by name at `nw-sup` startup, matching the `sched_ext`
precedent — and are the next fields due their own design-note-plus-
`claims` round, per the ordered list above.

**Amendment items outside the bump itself, tracked here so they don't
get lost in Phase 4's list:**
- `docs/options/23` (erofs file-backed bricks) gains a **verified
  bricks** section: fs-verity on the image files (not dm-verity, which
  needs a block device bricks don't have), whether file-backed erofs
  reads through it, how its digest relates to the sha256 file name,
  measured on the target kernel, unmeasured parts stated plainly. NOT
  STARTED — folds into 1g's existing `docs/options/23` work.
- `docs/options/24` — **boot counting**, a NEW design note, code
  explicitly out of scope until operator sign-off. What counts as a
  successful boot (city open vs. compositor exec vs. an operator
  `nwctl bless`, each one's weakness stated); who writes the mark and
  what, citing systemd-boot's own primary docs on its automatic
  boot-assessment counter; the exact exception to "nothing on the boot
  partition is written at runtime" this requires, and why it doesn't
  violate commitment 4; what a rollback does; how this is tested given
  QEMU boots directly and not through systemd-boot. NOT STARTED.

## Section 4 — scx side thread. Bounded: 3 push iterations, CI+docs only.

Third attempt at proving the sched_ext accept path, differently shaped
from the two already recorded in `docs/options/15`: a privileged Fedora
or Arch container on the free `ubuntu-24.04` runner, using that distro's
own packaged `scx` binary against the runner's kernel rather than
building from source. Confirm the package exists before attempting
anything else. Measurement only — Phase 4 deletes the `sched_ext` plan
field regardless of this result. **DONE, 2 of 3 push iterations used**
(`07ebd9f`, `695e62a`). Confirmed `scx_c_schedulers` exists in Fedora's
own repo (42/43/44 stable) before writing any workflow, per the
instruction. A privileged Fedora 42 container on the runner installed
it and actually invoked `scx_simple` — the first time across three
attempts, in three rounds, that a load was attempted at all — and
libbpf refused
it: `scx_bpf_consume` not found in this kernel's BTF, a version-pairing
mismatch one layer further down the stack than attempt 2's link-time
skew (packaged binary vs. this exact kernel's kfunc set, rather than
self-built tool vs. its own skeleton). `/sys/kernel/sched_ext/state`
stayed `disabled` before/while/after, quoted verbatim in
`docs/options/15`. Decisively answers that struct_ops loading is
*permitted* in this CI environment; still does not show a scheduler
reaching `enabled`, for a different reason than before. Workflow
deleted per the "not kept" convention. Full result:
`docs/options/15`'s "Attempt 3" section.

## Open questions carried forward

None blocking. 1a-1g, Section 4 and Phase 2 are all landed as of this
commit; every flagged item along the way (the `.sha256` sidecar scope in
docs/options/20, the optional on-disk per-run tail trim in
docs/options/21, the Wine-process-topology premise in docs/options/20
§6) was resolved with a stated recommended default or explicitly
deferred to whoever picks up the relevant later work, not left as a
blocking question here.
