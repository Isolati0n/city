# 31 — Phase 4: the plan-format bump

Status: **design note. No code in this round.** Written against the tree
at commit `e283893` (Phase 3 landed). Every file:line citation below was
checked against that tree, not against memory of an earlier phase.

Scope, quoted from `docs/OPERATOR-BRIEF.md` Section 3 and its 2026-09-28
amendment: one `NW_MAGIC` bump. Adds `lock`, stop signal + grace period
(unset = today's behavior), a supervisor-death policy, per-house `nofile`,
and, per the amendment, capabilities, a task cap and an OOM priority.
Deletes `io_rbps`, `io_wbps`, `sched_ext`. **Every field not yet applied
is refused by name** — this note takes that literally: a field lands in
the format only paired with either a real applier or a refusal, never
with silent acceptance, which is the `nw_res` defect `CLAUDE.md`'s
mechanism rule exists to prevent.

This note answers the design questions the brief poses before code, and
ends with the batched-question list Section 5 asks for. It does not
cover the items the brief lists as separate, later design-note rounds
(stop-grace escalation, the supervisor-death applier, run/stop profiles
plus `nwctl`, the permission-diff tool, self-verifying confinement, the
Landlock port/scope fields, the device lid, rootless houses, or
`provides`/`needs`) — those get their own notes when their turn comes,
per the brief's own ordering. What follows is scoped to the bump itself:
which fields get a real applier now, which get the format slot and a
named refusal, and the five-place mechanics of the bump itself.

## 1. What "one bump" actually touches

Invariant 3's discipline (`CLAUDE.md`) is "change one, change all four":
`blob.h`, `bakery/nw-cc.py`, `plan.als`, `Plan.tla`. This project has a
fifth place specific to the magic itself: `plan-formats.txt`, an
append-only ledger of `(NW_MAGIC value, sha256 of blob.h's declared
layout)` rows, verified by `test_magic_moves_with_the_layout()` in
`tests/run.py`. Current magic is `NWPLAN11` (`blob.h:27`); this bump
becomes `NWPLAN12`. The ledger row is **derived, not hand-typed** — the
file's own header comments record two prior derivation-function changes,
each time re-deriving every existing row from the commit that defined
that magic, and the convention this note follows is the same: run
whatever `plan-formats.txt` names as its current signature command
against the new `blob.h`, append the row, do not touch the others.

`nwcheck.c` is a sixth place for every new structural rule (each new
`NW_E_*` code, its string in `errs[]` in the same order, the
`nw_errstr` bound) — invariant 3 doesn't name it because it isn't a
*limit*, but the same "the runtime relies on it, so it must be checked
independently of the baker" rule from `plan.md`'s Hard rules applies to
every cross-field refusal below.

Current `NW_E_*` tail is `NW_E_EDGEDUP = 27` (`blob.h:769`), terminator
`NW_E__COUNT` (`blob.h:790`). New codes start at 28, in the order they
appear below — renumbering on retirement is this project's own
precedent (`blob.h:738`'s comment on the retired `NW_E_BRICK`), not
something this note needs to invent.

## 2. The struct layout

`struct nw_res` (`blob.h:426-441`) loses `io_rbps`/`io_wbps`
(`blob.h:430-431`, offsets 24/32) and `sched_ext` moves from
`struct nw_unit` (`blob.h:484`, byte 227) to retirement — see §3. The
freed bytes are not reused by coincidence: this bump's new per-unit
scalar fields (`lock`, `stop_signal`, `grace_period`, `nofile`,
`capabilities`, `task_cap`, `oom_score_adj`, `supervisor_death_policy`)
need new space in `struct nw_unit`, and `blob.h:567`'s own comment on
`_pad`'s reuse as `sched_ext` is explicit that reusing a freed byte for
an unrelated field is fine *only* because `NW_MAGIC` moves regardless —
readers of an old blob never see the new field silently repurposed
under them, because the magic mismatch refuses first. This note does
not pre-assign byte offsets; that is `drift`'s and the implementer's
job once the field list below is fixed, following `NW_AT`/`NW_TYPE`
exactly as every existing field does (`blob.h:643`, `:653-660`).

Two structurally different additions:

- `struct nw_res` gains nothing here. `task_cap` and `oom_score_adj`
  are unit-level scalars like `lock`, not resource-block members —
  `nw_res` is specifically the cgroup-backed and syscall-backed
  *tunables* block (`runtime.md`'s "resource block" section), and
  `task_cap`/`oom_score_adj` fit that description, but putting them in
  `nw_res` would mean touching `pack_res`'s offset table
  (`plan.md`'s "Check the struct sizes" bullet) for two fields that are
  otherwise ordinary scalars. **Recommendation: add both to `nw_res`
  anyway**, because `pids.max` and `oom_score_adj` are exactly the kind
  of per-controller/per-process tunable the block exists to hold, and a
  second, parallel "unit-level resource-shaped field" location is the
  two-lists problem `CLAUDE.md`'s "who owns which file" section argues
  against applied to a struct instead of a file. Open question 4 below.
- `capabilities` needs a bitmask wide enough for `CAP_LAST_CAP` (40 on
  a current kernel, `include/uapi/linux/capability.h`) — a `uint64_t`,
  named-classes-over-a-mask question in §7.

## 3. Deletions: `io_rbps`, `io_wbps`, `sched_ext`

**`io_rbps`/`io_wbps`** (`blob.h:430-431`): `runtime.md`'s "resource
block" section already states plainly that this machine's cgroup v2
mount carries only the `hugetlb` controller — `io` is on a v1 hierarchy
here and untestable, and nothing in `nwsup.c` has ever written
`io.max` (`grep -n "res\." nwsup.c` returns nothing per the correction
already landed in Phase 3 review). Deleting rather than leaving unused:
a declared, unenforced io limit is exactly the `nw_res` defect this
project already paid for once. **Correcting an overstatement from this
section's first draft: there is no `nwcheck.c` range check on either
field to delete.** `nwcheck.c:327`'s own comment says so directly —
`cpu_mask`, `mem_high`, `mem_max`, `io_rbps`, `io_wbps` and
`layer_bytes` "have no range check here and that is correct: every
64-bit value of a mask, a byte count or a rate is a legal declaration,
so there is no bound to quote." Only the baker side is real
(`bakery/nw-cc.py:647-650`, `parse_bytes(v, "io-rbps")`/`"io-wbps"`),
and that parser key goes with the fields — **there is no refusal to
add**, because a plan naming a key that no longer exists in the grammar
is already an unknown-key error via whatever generic mechanism the
baker uses for that (the same path a typo in any other key takes).

**`sched_ext`** (`blob.h:484`, `NW_SCHED_EXT_*` at `blob.h:368-370`):
supersedes `docs/options/15-per-house-scheduling.md`'s reject path
(present in the tree; read for this note — corrects an earlier draft
of this section that assumed it was missing). This one is **not** dead
code the way `io_rbps`/`io_wbps` are — `nwsup.c:661-683`'s
`sched_ext_supported()` genuinely probes the running kernel (stats
`/sys/kernel/sched_ext`, then mmaps `/sys/kernel/btf/vmlinux` and scans
for the `sched_ext_ops` type name — real, working, kernel-support
detection with no allocation and a bounded scan, per its own comment),
and `apply_sched_ext()` (`nwsup.c:694-`) is wired into the boot sequence
(`nwsup.c:1994`). What it does with that detection is the reason it's
being deleted rather than kept: `NW_SCHED_EXT_DEFAULT` names exactly one
policy with **no working, loadable artifact behind it on this project's
own test kernel** — this section's first draft quoted `docs/options/15`
as saying "nothing available to this project can build one," and that
framing is `docs/options/15`'s own, since-superseded first draft: its
newest section ("Attempt 3," measured 2026-09-29, the same day as this
note) got a real, distro-packaged binary (Fedora's `scx_c_schedulers`,
`scx_simple`) to the point of an actual load attempt against the
runner's kernel, where it failed on a kernel-BTF mismatch specific to
that kernel (`libpf: extern (func ksym) 'scx_bpf_consume': not found in
kernel or module BTFs`) — a narrower, kernel-specific failure, not "no
artifact exists." **The decision this note makes is unchanged by that
correction** — `docs/QUEUE.md`'s own record of Attempt 3 says explicitly
that Phase 4 deletes `sched_ext` regardless of the result — but the
justification is restated accurately: there is still no policy this
project can demonstrate loading and verified on the kernel it actually
tests against, whatever the reason on any one kernel turns out to be.
So `apply_sched_ext()`'s only two outcomes, both today and for as long
as that remains true, are `die("sched-ext unsupported")` on a kernel
lacking the feature, or a different, equally-final `die()` on a kernel
that has it, because loading nothing while claiming a policy was
applied is exactly `nw_res`'s original defect reached through the one
branch nothing available here can exercise. A field whose only real
behavior is "always refuse, for one of two reasons" is a two-line
refusal at the call site that declares it, not a byte in every blob and
a kernel-probing function that exists to refuse. **This is also the
existing precedent this note leans on directly in §5 and §10 for how a
declared-but-unbuildable field should be refused** — at nw-sup startup,
by name, not silently accepted. Deleting the field: the baker's
`sched-ext=` parser key and `nwcheck.c`'s corresponding validation go
with it; `sched_ext_supported()`/`apply_sched_ext()` and the marker/BTF
constants are removed from `nwsup.c` entirely rather than kept dead,
matching this project's "delete when a sentence is wrong, don't retract
in place" instinct applied to code instead of prose — there's no future
reader served by a kernel-probing function nothing calls.

**What this note does NOT delete**: `NW_SCHED_UNSET`/`OTHER`/`BATCH`/`IDLE`
(`blob.h:349-353`) and `sched_policy` (`blob.h:441`) are the real,
kernel-backed `sched_setscheduler(2)` policy — `runtime.md` confirms
`sched_policy` is one of the three syscall-backed (non-cgroup) resource
fields that already work on this machine. Only the `sched_ext` byte and
its four `NW_SCHED_EXT_*` constants go. Naming both explicitly here
because `sched_ext`/`sched_policy` read as one family and are not.

## 4. `lock` — mostly already built

`docs/options/22-lock-unlock.md` is a complete, 442-line design note for
this field, written before this bump had a number. Read against the
current tree, its designs still hold: the encoding it proposes (a
single byte reusing the freed `sched_ext` position, `locked=0` as the
default so an old, all-zero-tail blob under the new magic — impossible
anyway, since the magic itself gates this, but the point stands for a
freshly-baked plan that omits the key — reads as today's only behavior)
and the semantics (`stopped = !locked` at init, reusing the existing
`STOP`'d branch and adding a `STATUS` verb) are unchanged by anything
Phase 2 or Phase 3 did.

**The mechanism is already built and proven**: `decide.h`/`decide.c`
fully implement `lock`'s LOCKED(1)/UNLOCKED(0) semantics — UNLOCKED
never increments `deaths`, never reaches `SPENT`, starts idle — as one
of `proofs/caller_decide.c`'s six CBMC-proven invariants (invariant 2,
confirmed present and non-vacuous by the tcb-review pass quoted in this
session's earlier work: `0 of 7 failed`, and a scratch mutation deleting
the `stop_requested` guard turned exactly the invariants naming it red).
`nwsup.c:1171` currently reads:

```c
static const int nw_lock = 1;
```

with a comment (`nwsup.c:1170`, truncated in this excerpt but present in
the file) already anticipating this exact bump — the value is
hardcoded pending "the day the field exists."

**What this bump actually does for `lock`, concretely:**

1. `struct nw_unit` gains the `lock` byte at the position §2 assigns.
2. The baker parses `lock=locked`/`lock=unlocked`. `docs/options/22`'s
   §5 "Encoding" only argues for the byte's own position and
   size-neutrality in the struct, not for this spelling in plan source —
   an earlier draft of this bullet attributed the naming argument to
   that section and it isn't there; `lock=unlocked` appears once,
   unargued, in that note's own §9 Tests. The named-value recommendation
   is this note's own, made here for consistency with how `lids=` and
   `kind=` are already spelled — a bare `0`/`1` would be the one plan
   key that breaks that pattern. `lock=locked` is the default when the
   key is omitted.
3. `nwcheck.c` validates the byte is one of the two known values
   (`NW_E_LOCK`, next code = 28) — the same closed-set shape
   `NW_E_LIDS` already uses for `lids`.
4. `nwspawn.c` gains `setenv("NW_LOCK", ..., 1)` beside the existing
   `NW_BUDGET`/`NW_KIND` forwarding (`nwspawn.c:362-363`).
5. `nwsup.c:1171`'s `static const int nw_lock = 1;` becomes a
   `getenv("NW_LOCK")` read at startup, replacing the constant — no
   other line in `nwsup.c` changes, because every call site already
   reads `nw_lock` as a variable rather than inlining the literal.
6. `docs/options/22`'s output-tail-across-runs marker-line design and
   its `relaunch-house.py` must-force-locked note apply unchanged; that
   tool already forces a throwaway single-house plan through the real
   baker (`plan.md`'s `--lab` bullet), so it gets whatever default the
   baker's parser picks unless it's updated to force `lock=locked`
   explicitly — recommend doing so for the same reason it already forces
   `--lab`: a relaunch is definitionally not the kind of long-lived,
   intentionally-idle house `lock=unlocked` exists for.

**Edges-on-unlocked, restated from `docs/options/22`**: refused at both
bake and boot, because an unlocked house that's also a `provides` for an
edge is a promise the plan can't keep (nothing ever forks it to hold up
its end). This is a cross-field rule, so it needs both a baker refusal
and an independent `nwcheck.c` one, per `plan.md`'s Hard rules — a new
code (`NW_E_LOCKEDGE`, 29) rather than reusing `NW_E_EDGEIDX`, because
the failure reason ("this unit will never run") is different from an
out-of-range endpoint and a reader of a refusal string deserves to know
which.

**Test plan** (all listed in `docs/options/22`, restated for this note
to keep them next to the field): an unlocked house never restarts under
a fault-injected repeated-crash sequence (the `decide_seq.c` exhaustive
harness already covers this at the pure-function level; the integration
test is new — boot, crash the house `N > budget` times, assert zero
restarts and the STOP'd/idle state rather than SPENT); `STATUS` on an
unlocked, never-started house reports idle rather than crashing or
timing out; a `lock=unlocked` plan declaring an edge is refused at bake
time (baker) and at boot time (a hand-crafted blob with the lid cleared
and CRC repaired, matching this project's own "show it rejecting a
crafted bad blob" convention from `plan.md`'s Definition of done).

## 5. `stop_signal` — built now; `grace_period` — format only, refused

The amendment groups these as one bump item ("stop signal and grace
period, unset = today's behavior"), but they are not one mechanism, and
splitting them is what keeps this bump honest about which fields get a
real applier.

**Where SIGTERM is hardcoded today**, all three call sites checked
directly:

- `nwsup.c:1067`, inside `on_term()` — the async-signal-safe handler
  installed at `nwsup.c:1725-1726`. `kill(child, SIGTERM)`.
- `nwsup.c:1207`, inside `handle_ctl_live()`'s `STOP` branch — reached
  only after `nw_decide()` returns `NW_DECIDE_TERM_CHILD`
  (`nwsup.c:1201-1207`).
- `nwsup.c:2024`, the ordinary-control-flow fork-race-window site
  (`nwsup.c:2016-2025`), same `nw_decide()` gating.

**`stop_signal` is a real applier, buildable now, because `kill(2)`
itself is the mechanism and it's already async-signal-safe.** A plan
declares `stop-signal=<name>` from a closed set — **open question 5**:
recommend `{TERM, INT, HUP, QUIT}`, the signals a well-behaved daemon
typically treats as "shut down," not an arbitrary integer, for the same
reason `lids=` is a named set rather than a raw bitmask the plan
language elsewhere avoids. Mechanism: a new file-scope
`static int nw_stop_signal = SIGTERM;` in `nwsup.c`, set once at
startup from `getenv("NW_STOP_SIGNAL")` before `on_term` is installed —
a plain global int read inside a signal handler is signal-safe (it's
not a function call, not a lock, not non-reentrant libc state), so
`on_term()` becomes `kill(child, nw_stop_signal)` with no change to its
async-safety argument. The two ordinary-control-flow sites change the
same way. `nwcheck.c` gets a closed-set check (`NW_E_STOPSIG`, 30),
mirroring `NW_E_LIDS`/`NW_E_LOCK`.

**`grace_period` is NOT buildable as a small addition, and this note
refuses it rather than pretending otherwise.** The reason is structural,
not a matter of engineering effort: `wait_house()` (`nwsup.c:1216`)
blocks on the house's pidfd with **no timeout** — `runtime.md`'s
Liveness section states this as a deliberate, load-bearing property
("There is no field to put one in... every form of detection needs a
guessed constant, and the rule has been attempted and wrong every
time"), and a per-house `grace_period` that means "wait this long after
signaling, then escalate to SIGKILL" is precisely a bounded wait added
to that same call. It is not the *general* freeze-detection case the
Liveness section refuses — the bound comes from the plan, not a guessed
constant — but it still changes `wait_house()`'s blocking model (poll
with a timeout instead of forever, deciding what to do when the timeout
fires while the pidfd is also readable, interaction with the existing
`extra_fd` control-socket member of the same poll), and the brief itself
lists "stop-grace escalation" as a separate, later design-note-plus-code
round. Building the escalation quietly inside this bump would be doing
exactly the work the brief scheduled for later, under the cover of a
field that "sounds small."

So: `grace_period` gets a format slot in this bump (so the eventual
escalation applier doesn't need a second magic bump), the baker accepts
and validates it (bounds-checked, a plain millisecond count with 0 =
unset), and **`nwsup.c` refuses to start any house that declares a
nonzero `grace_period`** until the escalation applier lands. This is
not a novel shape: it is exactly what `apply_sched_ext()` already does
today (§3) for a declared-but-unbuildable policy — `die()` with a named
reason at nw-sup startup, never a silent accept — and this note follows
that precedent rather than inventing a new one. Naming a "not built yet"
condition with its own `NW_E_*` code is still worth flagging as open
question 1: does a not-yet-applied-but-syntactically-valid field refuse
at **bake time** (baker refuses any nonzero value, like the `nw_res`
zero-value case in `plan.md`'s "declared zero cannot be represented"
bullet) or at **boot time**, `sched_ext`-style (`nwsup.c` refuses it, so
a blob baked today with the field reserved starts working the moment a
later bump's `nw-sup` binary lands, with no rebake)? Recommendation
below in §10.

## 6. `supervisor_death_policy` — format only, refused (applier deferred)

The brief lists "supervisor-death applier" as its own later
design-note-plus-code round, separate from this bump. Treated exactly
like `grace_period`: the byte exists in `struct nw_unit` in this bump
(so the magic doesn't have to move twice), the baker validates it
against a closed set, and it is refused at whichever point §10's answer
picks — this note does not attempt to design the applier itself (what a
supervisor "dying" even means operationally, whether PID 1 restarts the
whole unit's supervisor or treats it as the unit being gone for good, is
exactly the open design work the brief scheduled its own round for).
What this note does fix now is the field's *name-space*: reserve
`NW_E_SUPDEATH` (31) for its eventual refusal/validation code so the
numbering in §1 doesn't need to skip around when that later note lands.

## 7. `capabilities` — built now, per the amendment

**What exists today**: every house runs as uid 0 with nothing dropped.
The only privilege-adjacent call in the whole TCB is
`PR_SET_NO_NEW_PRIVS` (`grep -nE "PR_SET_NO_NEW_PRIVS" nwsup.c lids.c`
— confirmed present in both, part of the seccomp/Landlock setup, not a
capability drop). `docs/options/20-grants-schema.md` §6 (already in the
tree, read in full for this note) settles a narrower fact than this
section's first draft claimed, and getting the scope right matters
because §6's own drafting history records exactly this overclaim being
made once already and corrected (`docs/QUEUE.md`'s account of that
correction round). **What §6 actually settles: a process ptracing its
own direct descendant, under matching credentials, needs no
`CAP_SYS_PTRACE`** — sourced from `ptrace(2)`'s documented access-mode
algorithm (man7.org, "Ptrace access mode checking," step 3: matching
real/effective/saved uid needs no capability), and holding under every
plausible reading of this project's stated "YAMA off," including the
stricter scope-1 reading, because a direct parent-child relationship
satisfies scope 1's own descendant requirement too. **What §6
explicitly does NOT settle, calling it "a further open gap rather than
papered over": whether that same freedom extends to one house ptracing
a sibling.** The same-uid clause in the permissive reading ("any other
process running under the same uid") does not, on its own text,
distinguish "own descendant" from "any other house" — every house here
runs uid 0 (invariant 5) — but §6 states plainly that which reading
actually governs in practice "was not resolved here." A related,
separately-sourced fact from the same section — `/proc/<pid>/status` is
readable by DAC alone at this tree's `hidepid=0`, no ptrace or
capability check at all — is fully settled and unaffected by the
sibling-ptrace ambiguity; it answers a different question (process
visibility, not attach permission).

**So this field's controls have to be written against what's actually
settled, not against the broader, since-corrected claim.** A
descendant-ptrace control (a house's own child) can assert the settled
fact directly: dropping `CAP_SYS_PTRACE` does not, and per the kernel
source cannot, prevent a process from ptracing its own fork, so a test
here is a control on the FIELD's honesty rather than on the mechanism —
it should never be sold as something the field prevents. A
*sibling*-ptrace control cannot assert either direction as settled: §6
leaves it open which YAMA reading governs, so a control here can only
report what this specific kernel does today, labeled as a measurement
of the current environment rather than a property this field
guarantees either way. Closing the sibling case for real is a uid
problem, not a capability problem, and belongs to the later, explicitly
deferred "rootless houses" item, exactly as §6's own decision states.

**Design questions, answered:**

- **How the plan spells the set.** Recommend **named classes**, not a
  raw mask — matching `lids=` exactly (`bakery/nw-cc.py:557`'s
  `parse_lids()` is the precedent: comma-separated names, OR'd into a
  closed bitmask, refused on an unknown name). A raw 64-bit mask would
  be machine-independent in the wrong direction: `CAP_*` bit positions
  are a kernel ABI, not a plan-language concern, and spelling them by
  number would make a plan unreadable and would let a plan silently
  target a capability the current kernel doesn't define yet. Named
  classes also make the **unknown-capability case answerable exactly
  as the brief already answers it — refuse** (a name the parser's table
  doesn't recognize is a syntax error at bake time, same shape as an
  unknown `lids=` token; a name the *parser* recognizes but the
  *running kernel* doesn't — `CAP_LAST_CAP` advertised by
  `/proc/sys/kernel/cap_last_cap` is lower than the capability's bit
  position — refuses at nw-sup startup with a named reason, because
  that's a kernel-vs-plan mismatch the checker running at bake time on
  a *different* machine cannot see).
- **Drop order relative to other setup steps.** The amendment already
  specifies it: after mounts, cgroup placement and lids, before exec.
  This note confirms that ordering is consistent with `runtime.md`'s
  fixed lid order (NEWNET → NEWNS → brick pivot → Landlock → seccomp) —
  capability drop slots in *after* seccomp, immediately before `execv`,
  because seccomp's own allow-list is itself capability-gated in one
  place worth naming explicitly: `mount`/`unshare`/`pivot_root` are
  already absent from `strict_allow[]` regardless of capabilities
  (`runtime.md`'s "Lid order is fixed" bullet), so dropping
  `CAP_SYS_ADMIN` after seccomp is already applied is redundant-but-
  harmless belt-and-suspenders, not load-bearing — the seccomp filter is
  what's actually load-bearing there, exactly as invariant 6 already
  states of the lid order generally.
- **Interplay with Landlock, seccomp, user namespaces.** No user
  namespaces exist in this tree (every house is real uid 0, invariant 5)
  so there is no `CAP_*`-in-a-userns subtlety to design around yet — that
  question is exactly what the later "rootless houses" item exists to
  answer, and this note explicitly does not pre-empt it. Landlock and
  seccomp are orthogonal axes (what paths/syscalls, not what
  capabilities), so no interaction beyond ordering.
- **Mechanism.** In the forked child, after lids and before `execv`:
  `capset(2)` (or the `libcap` equivalent hand-rolled the way this TCB
  hand-rolls everything else — no libcap dependency, matching this
  project's own no-external-library discipline for the TCB) to drop the
  bounding set via repeated `PR_CAPBSET_DROP` for every bit not in the
  plan's declared set, then clear permitted/effective/inheritable/ambient
  for anything dropped, keeping `PR_SET_NO_NEW_PRIVS` as already set.
  Read back from `/proc/<pid>/status`'s `CapBnd`/`CapEff`/`CapPrm`/
  `CapAmb` hex fields, matching the amendment's own stated read-back
  mechanism exactly.
- **Defaults, from primary sources, marked as required by the
  amendment.** Wine/Proton's own documented need: `CAP_SYS_NICE`, for
  `setpriority()` thread-priority adjustment — sourced from
  `github.com/ValveSoftware/Proton` issue #7031, exact warning text
  `"wine: RLIMIT_NICE is <= 20, unable to use setpriority safely"`, and
  the community workaround is `setcap CAP_SYS_NICE=eip` on the
  launcher binary rather than a capability granted by an init. No
  broader authoritative capability list for pressure-vessel/SteamOS
  sandboxing turned up in this research pass — **marked unmeasured**,
  per the amendment's own instruction, because QEMU has no GPU and a
  compositor's real capability needs (`CAP_SYS_NICE` almost certainly,
  possibly `CAP_SYS_TTY_CONFIG` for VT switching, unverified) can only
  be established by running one on real hardware.
- **Default set recommendation**: an operator house (console, compositor)
  gets `CAP_SYS_NICE` and nothing else beyond what no-new-privs already
  implies; a game/Proton house gets the same; every other house
  (`capabilities=` omitted) drops everything, matching the "UNSET =
  today's behavior" pattern **only insofar as UNSET must still mean
  something concrete** — and here it can't mean "keep full root" the
  way `lock` and `stop_signal`'s UNSET can, because "full root" is the
  thing this field exists to move away from. **This is a real
  divergence from the bump's own stated UNSET convention and is called
  out as open question 2.**

**Controls**, covering all four probes the amendment names
(`mknod`/`mount`/raw-socket/`ptrace`-`bpf`), each restated against this
tree's actual test conventions and against what §6 actually settles
rather than the corrected overclaim: a dropped house's attempt to
`mknod`, `mount`, or open a raw `AF_PACKET` socket must each fail with
`EPERM` from the capability check specifically — distinguished from a
seccomp `EPERM`-via-`SIGSYS` or a Landlock `EACCES` by running the probe
against a house with **capabilities dropped and no other lid set**, the
same "test the mechanism in isolation" discipline `CLAUDE.md`'s "a
claim with parts is covered when every part is" bullet requires (the
seccomp allow-list already denies raw sockets independently —
invariant 6's grant comment — so a combined-lids test would be
satisfied by the wrong mechanism and prove nothing about the
capability drop specifically). Ptrace splits into the two controls §7
above already draws: a descendant-ptrace control asserting dropping
`CAP_SYS_PTRACE` does **not** prevent a same-uid parent-child ptrace
today (proving the settled negative stays true, so a kernel change or
a later rootless-houses landing that silently alters it doesn't go
unnoticed), and a sibling-ptrace control labeled as a measurement of
this specific kernel/YAMA setting rather than an assertion either
direction, since §6 leaves that question open. **`bpf` — the fourth probe the amendment names and the one an earlier
draft of this section silently dropped rather than scoping.**
`grep -n "__NR_bpf" lids.c` returns nothing: `bpf(2)` is already absent
from `strict_allow[]`, so any house with `lids=...,seccomp` already gets
`EPERM`-via-`SIGSYS` on the syscall regardless of capabilities — the
same "test the mechanism in isolation" concern the mknod/mount/socket
controls already raise applies here in the opposite direction: a house
with BOTH seccomp and a capability drop would have its `bpf` control
satisfied by the wrong mechanism. So a real capability-only `bpf`
control needs a house with **capabilities dropped and seccomp
absent** — the one combination not otherwise exercised by this
section's other three controls, each of which assumes seccomp is
present as an independent, unrelated lid. Whether `CAP_BPF` (kernel
≥ 5.8) or the older, broader `CAP_SYS_ADMIN` gate applies on the target
kernel is a separate, unverified primary-source question, left for the
code round rather than answered here. Read-back-matches-plan is a
direct string comparison against `/proc/<pid>/status`.

## 8. `task_cap` (pids.max) and `oom_score_adj` — built now

Both straightforward given Phase 3's cgroup infrastructure is already
proven and live.

**`task_cap`**: `pids.max` written into the per-generation cgroup
directory `house_cgroup_open_generation()` already creates
(`nwsup.c`), the same way `mem_high`/`mem_max`/`cpu_weight` are already
written there today (per `runtime.md`'s "resource block: most fields
enforced" section). No new mechanism class — this is the fourth
cgroup-file write alongside three that already exist and are already
tested (`memory.high`, `memory.max`, `cpu.weight`). Read-back:
`pids.current`/`pids.max` in the same directory, same pattern the
existing memory/cpu tests already use.

**Control**: a fork-bomb house (`while(1) fork();`, a new fixture under
`houses/`, matching this project's convention of a purpose-built C
fixture per mechanism rather than a shell one-liner) stops forking at
its declared cap while a neighbor house's own heartbeat (an existing
fixture, or a trivial new one that increments a counter file) keeps
advancing on schedule — proving the cap is per-cgroup and doesn't starve
the sibling, which is the actual property worth asserting (that the cap
works at all is a one-line `pids.current == pids.max` read; that it
doesn't collaterally damage an unrelated house is the harder and more
valuable claim, matching `runtime.md`'s `mem_high`-vs-`mem_max` "test
both directions" discipline for a different field).

**`oom_score_adj`**: written to `/proc/self/oom_score_adj` **in the
child, before capabilities are dropped** per the amendment's own
ordering. **Correcting this section's first draft on why that ordering
isn't load-bearing**: the claim was that `CAP_SYS_RESOURCE` only gates
setting *another* process's value, never your own — checked against the
kernel's actual gate (`fs/proc/base.c`'s `__set_oom_adj()`) and that
isn't the distinction the kernel draws. The real condition is
`(short)oom_adj < task->signal->oom_score_adj_min && !capable(CAP_SYS_RESOURCE)`
— the capability is required to *lower* a value below a floor a
previous, privileged write already raised, for self or other alike, not
gated by whose process it is. In practice this still doesn't make the
ordering load-bearing for this TCB: nw-sup's forked child holds every
capability up to the point it drops them, so it can write any value in
range regardless of which check governs, and nothing here has
previously raised `oom_score_adj_min` for this process to hit the
floor against. Recorded here so a future reader has the actual
mechanism rather than the corrected-away one. Range, from primary source
(`kernel.org/doc/Documentation/filesystems/proc.rst`, quoted verbatim):
**-1000 (`OOM_SCORE_ADJ_MIN`) to +1000 (`OOM_SCORE_ADJ_MAX`)**; "-1000...
is equivalent to disabling oom killing entirely for that task since it
will always report a badness score of 0"; "The value of
`/proc/<pid>/oom_score_adj` is added to the badness score before it is
used to determine which task to kill." `nwcheck.c` bounds-checks the
declared value to that closed range (`NW_E_OOMRANGE`, 32) exactly the
way `NW_E_RESNICE` already bounds `nice` to the kernel's range
(`blob.h:761`).

**Ordering is not deterministically testable**, exactly as the amendment
says to mark it: the actual kill order under real memory pressure
depends on every other process's RSS and badness score at the moment of
an OOM, which this suite cannot control for. What **is** testable: the
value read back from `/proc/<pid>/oom_score_adj` after the house starts
matches what the plan declared — a read-back test, not a behavioral one,
named as such so nobody later cites it as proof the ordering works.

## 9. `nofile` — built now, per-house

**Primary sources, both already gathered for this note:**

- Proton's own `README.esync`
  (`raw.githubusercontent.com/ValveSoftware/wine/proton_7.0/README.esync`):
  `/etc/security/limits.conf`: `* hard nofile 1048576`; systemd:
  `DefaultLimitNOFILE=1024:1048576`.
- This init's own current default, from the code rather than a guess:
  PID 1 raises its **soft** `RLIMIT_NOFILE` to
  `NW_BOOT_NEED(n_houses, n_edges)` (`pid1.c:723-724`,
  `blob.h:263`'s macro) — a small, city-wide number bounded by
  `NW_FD_RESERVED + NW_MAX_UNITS + 2*NW_MAX_EDGES` — against the
  **original, kernel-inherited hard limit**, which it never raises
  except in the documented EPERM fallback (`pid1.c:704-707`, and that
  fallback *lowers* the hard limit to `need`, it doesn't raise it).
  Every house inherits this exact soft/hard pair unmodified through
  fork (`pid1.c:690-691`'s own comment: "Every house inherits this
  table by fork, unmodified before exec,"). **So today's uniform
  per-house ceiling is whatever the kernel handed PID 1 at boot** — not
  measured in this note beyond that, because it is a property of the
  boot environment (initramfs/kernel defaults), not of this code, and
  `harness.md`'s "measure, don't assert" discipline says this belongs
  measured on the actual target kernel rather than asserted here. What
  the code guarantees is the *floor* (`need`, small) and that nothing
  today raises it further per-house.

**Mechanism**: in the forked child, before `execv` (after mounts/cgroup/
lids, same general neighborhood as capabilities and `oom_score_adj` —
exact relative order among these three new per-house setup steps is
open question 3): `getrlimit(RLIMIT_NOFILE, ...)`, then
`setrlimit(RLIMIT_NOFILE, {cur=nofile, max=max(nofile, existing hard)})`
if the plan declares a value. **This can only raise the soft limit up
to the process's own inherited hard limit without `CAP_SYS_RESOURCE`** —
and `CAP_SYS_RESOURCE` is exactly the kind of capability the new
capabilities field (§7) might drop for an ordinary house. So a plan
declaring both `capabilities=` (dropping `CAP_SYS_RESOURCE`) and a
`nofile=` above the inherited hard limit is a genuine cross-field
conflict, structurally identical to `brick`-requires-`newns`: **refused
independently in both the baker and `nwcheck.c`** if the declared
`nofile` exceeds what's derivable as the process's ceiling *and*
`CAP_SYS_RESOURCE` is being dropped — `NW_E_NOFILECAP` (33). If
`capabilities=` is unset (today's full-root behavior) the raise can
reach `RLIM_INFINITY`/`fs.nr_open`, matching Proton's own documented
need of 1048576 provided the kernel's `fs.nr_open` allows it (a
boot-environment fact, not this code's to guarantee — refuse loudly
with the actual `errno` rather than silently clamping, matching
`pid1.c`'s own "named shortfall, never start fewer than named" pattern
for the city-wide case).

**Read-back**: `/proc/<pid>/limits`, parsing the `Max open files` row's
soft value — a text-format read, unlike the other three fields'
numeric `/proc` reads, so the test needs its own small parser; worth
noting as a minor asymmetry rather than a problem.

**Control**: a house declaring `nofile=16` that then successfully opens
a 17th file descriptor and is NOT killed/blocked is the over-acceptance
direction (mirrors `plan.md`'s "missing direction" lesson about
bind-loop over-rejection — a limit test satisfied only by the
under-limit case proves nothing about the limit actually being *a*
limit); the paired test opens exactly 16 then attempts a 17th and
asserts `EMFILE`.

## 10. Batched questions, each with a recommended default

Per Section 5 of the master brief: one message, each question with a
recommendation, so the operator can answer "approve all."

1. **A declared-but-not-yet-applied field (`grace_period`,
   `supervisor_death_policy`) — refuse at bake time or at boot time?**
   Recommend **boot time** (`nwsup.c` `die()`, by name), matching the
   existing `apply_sched_ext()` precedent exactly (§3, §5) rather than
   inventing a different shape for these two fields. A bake-time
   refusal means a plan written today for a future `nw-sup` binary
   can't even be baked until that binary exists; a boot-time refusal
   lets the blob be baked once and simply start working the day the
   applier lands, with the baker never needing to know which
   fields its *own* binary happens to enforce versus merely format.
   This mirrors how `nw_res` fields were originally handled (accepted
   by both, unenforced by the runtime) **except** that this bump
   deliberately closes that gap by making the boot-time refusal
   explicit and named, rather than silent, which is the actual fix
   `CLAUDE.md`'s mechanism rule asks for.
2. **`capabilities=` unset means "drop everything," not "keep today's
   full root."** This breaks the bump's own "UNSET = today's behavior"
   pattern that every other new field follows. Recommend accepting the
   break explicitly and stating it as the one field where UNSET is a
   new, more restrictive default — the alternative (UNSET = full root)
   would mean every existing plan silently keeps full root forever
   unless someone remembers to opt in, which inverts what this field is
   for. Flagging because a reader who expects the pattern to hold
   everywhere in this bump will otherwise be surprised by exactly the
   one field where it doesn't.
3. **Ordering among the three new before-exec child-side steps**
   (`nofile`'s setrlimit, `oom_score_adj`'s `/proc` write,
   `capabilities`'s drop) relative to each other, all three already
   ordered *after* mounts/cgroup/lids and *before* exec per the
   amendment. Recommend: `oom_score_adj` first (needs no privilege,
   never fails), `nofile` second (needs `CAP_SYS_RESOURCE` if raising
   past the inherited hard limit — must run before that capability is
   possibly dropped), `capabilities` last (irreversible, so everything
   that might need a capability the plan is about to drop must already
   be done).
4. **`task_cap`/`oom_score_adj` placement: fold into `struct nw_res`, or
   keep as bare `struct nw_unit` scalars like `lock`?** Recommend
   folding into `nw_res` (§2) for the reason given there — they're
   resource-shaped fields and a second location for that shape is the
   two-lists problem. This does mean touching `pack_res`'s offset table
   and its dedicated `test_baker_writes_the_declared_layout` swap-test
   (`plan.md`'s "Check the struct sizes" bullet) even though neither
   field is cgroup-controller-shared with the existing four.
5. **Named signal set for `stop_signal`**: recommend `{TERM, INT, HUP,
   QUIT}` as the closed set, matching common daemon shutdown-signal
   conventions rather than exposing the full signal namespace (a house
   declaring `stop-signal=KILL` would be asking this init to do
   something indistinguishable from not stopping it gracefully at all,
   which defeats the field's purpose; a house declaring
   `stop-signal=SEGV` is asking for something this init has no business
   sending). Open to a different set if the operator has a specific
   house in mind that needs one not listed.

## Tests, summarized (each field's own section above has the detail)

`lock`: zero-restart-when-unlocked integration test, idle `STATUS`,
edge-on-unlocked bake+boot refusal pair. `stop_signal`: a fixture that
traps its declared signal and a control that traps the *wrong* one and
must NOT catch it. `capabilities`: three isolated-mechanism probes
(`mknod`/`mount`/raw-socket, capability-only, no other lid) plus a
`CAP_SYS_PTRACE` control scoped to the negative finding (§7), plus
read-back string match. `task_cap`: fork-bomb-capped-while-neighbor-
advances. `oom_score_adj`: read-back only, ordering explicitly marked
untestable. `nofile`: open-to-the-declared-limit-then-EMFILE-on-the-next
pair, plus the `capabilities`-drops-`CAP_SYS_RESOURCE` cross-refusal.
`grace_period`/`supervisor_death_policy`: a crafted blob with a nonzero
value in the reserved-but-unapplied field, refused by name, matching
`plan.md`'s Definition of done convention for every new check.
