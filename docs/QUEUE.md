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

Base at last edit: `07ebd9f` (origin/main).

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
  is fallback-only, the same test needed `tests/block_pidfd.so.c` (an
  existing shim) added to keep forcing that precondition — verified by
  mutating the reset out and watching the unmodified test stay green,
  then confirming the fix turns it red again.
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
service, baker prints the edges it adds. NOT STARTED.

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

None blocking right now. Item 1b's own ten questions get answered inside
that note, then batched to the operator as that item's own step, not
listed here in advance.
