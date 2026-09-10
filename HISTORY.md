# Nexusweave / nw-init — session history

A record of one design session: from a speculative OS architecture, through
research and measurement, to ~1,400 lines of working code that boots as real
PID 1. Written chronologically, including the parts that were wrong.

---

## 1. The starting point

The session opened with a design document for **Nexusweave**: an OS that "is
not running containers but *is* containers all the way down." No traditional
kernel — a thin coordination fabric (Rust/Zig) whose only job is to let
containers declare and enforce typed, capability-based *relationships*.
Scheduling, memory, drivers, filesystems, networking, UI: all just more
containers.

Core claims:

1. Radical specialization without compromise — each container language-native
2. The relationship graph *is* the security, scheduling, and resource model
3. Cross-language composition first-class and safe, with automatic adapters
4. The whole OS as a living, evolvable, hot-swappable graph
5. New operational models fall out of the graph

---

## 2. The critique

Research against the OS literature produced a verdict of roughly **70%
already built, 30% wrong or underspecified**.

**Already done, under other names:**

| Claim | Prior art |
|---|---|
| Capability component graph | Genode (shipping, Sculpt OS) |
| Language-isolated processes + typed channels | Singularity / Midori |
| Capabilities as handles, typed IPC | Fuchsia / Zircon, FIDL |
| Verified capability microkernel | seL4 |
| Supervision trees, let-it-crash | Erlang/OTP |
| Typed cross-language composition | WASM Component Model / WASI |

**Wrong:**

- *"Without compromise"* — multiple runtimes multiply heaps, GCs, and cache
  pressure. Not free.
- *"Clean relationship-broken signal"* when a lazy Haskell worker diverges —
  this is the liveness problem. A diverged unit sends nothing. Every real
  supervisor guesses with timeouts.
- *"Automatic adapters"* — refuted by Waldo et al., *A Note on Distributed
  Computing* (1994). The CORBA/DCOM/RMI graveyard.
- *"Zero-copy handoff"* between different GCs — breaks against moving
  collectors; pinning defeats the GC.
- *"Migrate sub-graphs across architectures"* — heterogeneous-ISA migration is
  a research curiosity (Popcorn Linux), not shipping technology.
- *"Thin fabric"* — the enumerated feature list is incompatible with tiny.
  seL4 is ~10k LOC and does far less.

**The central objection:** the design confuses an OS with a
distributed-systems runtime, importing every impossibility result of
distributed computing into the local machine. The defensible version is an
orchestration layer on an existing kernel, not a new kernel.

---

## 3. Narrowing to the init

The scope collapsed to one component: the init system.

**The design thesis, borrowed from s6 and pushed further:** robustness comes
from *where code lives*, not how much exists. All typing, ordering, cycle
detection, and capability-flow analysis happens in an offline compiler that is
not in the TCB. The runtime is a table interpreter.

Five components:

| | role | in TCB |
|---|---|---|
| `nw-cc` | offline compiler, all validation | no |
| `nw-check` | boot-time blob validator | yes |
| `nw-root` | PID 1 | yes |
| `nw-sup` | one supervisor per unit | yes |
| `nw-rc` | day-to-day management | no |

---

## 4. What simulation corrected

Before any C was written, `nw-cc` was built in Python and a discrete-event
simulator exercised the design. Four claims died.

**The `rebind` primitive I specified already existed.** In seL4 a capability
names an *endpoint object*, not a thread. A restarted server calling `Recv` on
the same endpoint is reachable by every existing client capability. There was
nothing to build. The real exposure is *in-flight calls*, which scale with
concurrency, not with peer count.

**The liveness rule was wrong three times.** First dimensionally (deadline is
measured from the last beat, so the floor is `heartbeat + N × pause`, not
`N × pause`). Then too lenient at 3× (still leaked false kills over 30 days).
Then wrong in its *statistic* — p999 cannot bound a tail. Required margin
ranged 8×–20× across runtime profiles and failed outright for heavy tails.
Final rule: `deadline ≥ heartbeat + 1.5 × max observed pause`.

**Restart blast radius.** `scope = dependents` averaged 6.4 units touched and
peaked at 27 of 32 from a single crash. Since endpoint rebinding turned out
free, `self` is safe far more often than assumed.

**Degraded boot is weaker than claimed.** Killing one *non-critical* unit left
86% of the system up on average — but **16% in the worst case**. Some
optional units strand everything below them.

**The RT purity check found something no reviewer would.** A real-time audio
unit reached non-RT code three hops down through the memory manager. Fixing it
required a parallel RT platform — separate arena, DMA, and driver. Three extra
units and 56 MB. That is the honest price of "refuse relationships that
introduce GC pauses."

---

## 5. Hardware measurements

Taken on the container's real silicon (Xeon @ 2.1 GHz, single core).

| operation | cycles | notes |
|---|---|---|
| function call | 52 | baseline |
| syscall floor (`getpid`) | 276 | the boundary tax |
| mprotect flip | 7,290 | current isolation option |
| remap 2 MiB region | 82,864 | single core, before any IPI |
| attach page fault | 157–169/page | **corrected a 46× overestimate** |
| `fork` | 55–80 µs | flat, independent of open fd count |

**The 46× error mattered.** An estimate put attach at ~4M cycles for 2 MiB and
concluded restarts were expensive, requiring supervision changes. Measurement
put it at 86,590 cycles — 41 µs. The proposed mitigations were unnecessary.

**A retracted claim.** Early bootstrap numbers appeared to show super-linear
scaling. Repeat runs showed 1.8×–6.2× run-to-run spread; the "trend" was
single-sample noise on a contended core. `fork` is flat. The apparent cost was
scheduler latency, not syscall cost.

**Protection keys — still unmeasured.** The one number that could still move
the architecture. `pkeybench.c` is written; this container's hypervisor does
not expose PKU (`pkey_alloc` returns `EINVAL`).

---

## 6. The code

### PID 1 (`pid1.c`)

Blocks signals before the first fork, gates on `nw-check`, loads its table
from the validated plan, creates one private log pipe per unit, forks the
broker and loggers, reaps forever, shuts down in reverse.

No allocation after start. No parsing. Restart budget is a ring of timestamps,
not a counter — nothing to overflow.

Booted as **genuine PID 1** via `unshare --pid --fork --mount-proc`, which
exercised orphan reaping for the first time: 9 orphans reaped across three
restarts, all correctly ignored.

### The validator (`nwcheck.c`)

14 structural checks, no malloc, no recursion, bounded loops. Was O(n²) —
15.26 s at 64k units. After replacing the duplicate-name scan with an
open-addressed hash and cycle detection with a counting-sort adjacency index:
**0.10 s at 200,000 units**.

### The broker (`nwbroker.c`)

Owns every edge. Creates a socketpair per declared edge and forks each
supervisor with exactly its own descriptors.

Two properties this buys:

- **Non-provision, not enforcement.** A unit that declares no edges receives
  zero descriptors. It cannot reach a peer because it holds no handle — there
  is no doorman to bypass. Demonstrated: `delta` got 0, `beta` got 2.
- **Readiness observed, not reported.** The broker performed the binding, so
  it knows. The unit is never asked and cannot misreport. This is the gap
  systemd structurally cannot close.

Made **inert after startup**: zero `wait`, `waitpid`, or budget arithmetic
remain. Seccomp permits three syscalls (`pause`, `rt_sigreturn`,
`exit_group`); everything else is `SECCOMP_RET_KILL_PROCESS`.

---

## 7. Thirteen bugs, all found by running

Not one was found by reading the code.

| # | bug | how it presented |
|---|---|---|
| 1 | **Seal read but never verified** | 505 of 507 fuzz-accepted blobs had broken integrity |
| 2 | fd-budget check was dead code | unreachable — C2 fired first |
| 3 | **Nested budgets multiplied** | supervisor gave up, PID 1 restarted it with a fresh budget |
| 4 | **blob index vs table index** | every edge silently mis-routed, no error |
| 5 | `dup2(fd,fd)` doesn't clear CLOEXEC | every unit got zero edges |
| 6 | descriptor leak | units inherited 5 descriptors instead of 1 |
| 7 | **impersonation** | a test process claimed to be `alpha` and got its descriptor |
| 8 | orphans survived halt | 9 processes left running after broker death |
| 9 | `ADOPT_FD` collided with edge range | a unit read a struct field as a peer message |
| 10 | drain loop ran full timeout | added 1 s to boot |
| 11 | stale limits in Python validator | agreed only because every test used 32 units |
| 12 | `name_ok` scanned 32 bytes for a 128-byte field | 96 bytes of `exec_path` unusable |
| 13 | **socketpairs collided with `LOG_BASE`** | at 46 edges, 32 of 33 units wrote output into a peer connection |

**Three of these (5, 9, 13) are the same class:** fixed descriptor numbers
alongside dynamic allocation. That is a recurring failure mode in this design
and deserves a systematic answer rather than a fourth point fix.

**Bug 1 is the most instructive.** 4,000 fuzzed blobs found nothing because
they tested the wrong property — crash-resistance, not semantic correctness. A
validator can be perfectly memory-safe and still accept corrupted input. It
was found by re-reading the code adversarially.

---

## 8. Structural decisions

**Broker death is fatal.** It holds the only copy of the connection graph. A
replacement creates socketpairs that don't match what live units hold —
measured: a supervisor restarted after broker death came back with 0 edges
where the plan declared 1, while its peer held the old connection. Silent
split-brain, undetectable from inside. PID 1 now halts everything: 13
processes → 3.

**Limits are derived, never declared.** `NW_MAX_UNITS` from PID 1's descriptor
budget; `NW_MAX_EDGES` from the broker's. Two constants that must agree cannot
drift if one is computed from the other. This came from bugs 2 and 11.

**CRC32 replaced SHA-256** once the threat model was confirmed as corruption,
not tampering. Detection is diagnostic; the 14 structural checks are the real
safety property.

**Shutdown parallelised.** Was `grace × units` — 3.4 hours at 4,096 units.
Now bounded by the grace period alone: 3 seconds.

---

## 9. Test suite

| suite | coverage | result |
|---|---|---|
| `fuzzblob.py` | 4,000 byte-level blob mutations, ASAN+UBSan | 0 crashes, 0 accepted |
| `difftest.py` | C vs Python validator agreement | 401 inputs, 0 disagreements |
| `target.py` | targeted structural mutations | 23/24 (1 is a valid reordering) |
| `brokerfuzz.py` | 12 awkward-but-valid plan shapes | 12/12 handled |
| `scaletest.py` | compile + validate at 4/100/1000/4000 units | all pass |
| `test_reject.py` | malformed configs | 13/13 rejected |

**`scaletest.py` is the most valuable.** Five bugs were correct at 4 units and
wrong at 4,000, invisible because every test used a 4-unit plan.

---

## 10. Scale behaviour

Measured at 2,000 units (4,005 processes):

- **idle CPU: 100%** — a do-nothing constellation costs nothing
- ~326 kB per unit including its supervisor
- responsiveness for unrelated work degraded 1.5× between 500 and 2,000 units
- boot ~2.3 ms/unit, 99% in fork — dominated by 2-core scheduling contention

**Ceilings:** `fs.nr_open` allows ~500,000 units; `pid_max` binds first at
~16,000. Currently configured for 4,096, which validates in 3 ms.

---

## 11. Where the design ended up

**From:** no kernel, containers all the way down, radical specialization
without compromise.

**To:** Linux kernel, containers for fate-sharing boundaries, a plan validated
offline, units wired by non-provision.

The distinctive part is not the containers — it is that the *plan* is checked
before anything runs, ordering is derived from connections rather than
declared alongside them, and a unit receives only what it was granted.

**Design rules that survived measurement:**

- Containerise per fate-sharing boundary, not per logical component
- Never put a boundary on a per-allocation or per-sample path (276 vs 52 cycles)
- Restartable units must not own shared memory regions (82,864 cycles to remap)
- Liveness is a heuristic; say so in the config
- Authoritative state must never auto-restart on an integrity fault

---

## 12. Open items

**Needs hardware:**
- `pkeybench` — could move a dozen items from "wrong to containerise" to
  "situational"
- Bootstrap cost on multicore (current numbers are scheduler noise)
- Cross-core TLB shootdown scaling

**Needs code:**
- Fork pacing in the broker (a 200-unit chain wedged the test container)
- `SIGCHLD` during shutdown is undefined
- Ephemeral units, unit instances, parameterised invocation
- Namespaces, seccomp, cgroups on units — all belong in the supervisor
- SPARK on the broker (~250 lines of startup logic)
- Interface versioning — every edge pins an exact version

**Not started:**
- Socket activation, privilege dropping, log rotation, service control tooling
- Any run on real hardware

**Numbers that are correct for a reason the code does not state.** Both of the
following are unreachable today and neither is a live bug. They are recorded
together because they are the same defect twice: a constant that holds by slack
or by an unstated assumption rather than by arithmetic, so the thing that keeps
it true is not written down and nothing warns when it stops being true. Fix
them in one pass.

- **The fd budget omits the `parked[]` term.** `8 + 2u + 2e` appears in four
  places — the `_Static_assert` in `blob.h`, the constants in
  `bakery/nw-cc.py`, `fdNeed` in `plan.als`, `FdNeed` in `Plan.tla` — and none
  of them models the `nw` duplicated descriptors `pack_kit` creates at
  `electrician.c:113`. The real peak is in the house child before
  `close_others`: 3 stdio + 1 report + `u` log write-ends + `2e` socketpair ends
  + 2 (`nullfd`, `logn`) + `nw` parked. It fits only because `nw <= u-1`, which
  is a *consequence* of `nwcheck.c` rejecting self-edges and duplicate edges —
  an invariant enforced somewhere else entirely, for unrelated reasons, and
  written down in neither the formula nor a comment. So invariant 3 above
  ("limits are derived") is only partly true here: this limit works by the slack
  in `NW_FD_RESERVED`, not by its arithmetic, and `NW_FD_RESERVED` is silently
  doing duty as both reserved descriptors and an unnamed margin that encodes the
  no-parallel-edges assumption.

  Consequence if parallel edges were ever admitted: `nw` rises toward `e`, the
  peak gains a third `e` term, and it exceeds the declared budget once `e`
  passes roughly `u`. The `_Static_assert` keeps passing throughout — it is
  checking a formula that does not describe the code — so the safety property
  stops holding with no diagnostic anywhere. Presentation is an `EMFILE` from
  `F_DUPFD_CLOEXEC` and `die("pack kit")`, which is at least loud, but the
  budget will have been wrong long before it fires.

  Fix: put the term in the formula explicitly, in all four places, so
  `NW_FD_RESERVED` means only actual reserved descriptors again and the
  no-parallel-edges assumption is either stated or stops being load-bearing.

  Caveat for whoever does it: two independent hand-counts of the peak
  disagreed by one. Re-derive it from `pack_kit` and its caller rather than
  trusting either the number above or the one in the review that raised this.

- **`close_others` bounds at 512 while `NW_MAX_FDS` is 1024.**
  `electrician.c:48`, `:58` and `:65` all restate 512 — the `/proc`-missing
  fallback loop, `int doomed[512]`, and the `nd < 512` guard. The guard is the
  dangerous one: it drops descriptors from the doom list with no error, so they
  survive `exec`. The static assert admits `u=64, e=448`, at which point fd
  numbers in the house child run past 512, the sweep leaves peers' socketpair
  ends open, and a unit inherits descriptors it was never granted — a direct
  violation of invariant 5, presenting as a unit able to read or write an edge
  it does not appear in, with no error.

  Fix: derive both bounds from `NW_MAX_FDS` rather than restating them, and
  make the truncation `die()`. Better, delete the fallback entirely — it exists
  only to cover a missing `/proc`, and it is the sole reason a second bound
  exists at all. Without `/proc/self/fd` the electrician cannot honour
  non-provision, so failing loudly is the correct behaviour and the constant
  disappears with it.

---

## 13. Honest assessment

**Compared to s6:** better *designed* for validation and capability
discipline, roughly a third of the functionality, years behind on the thing
only time buys. s6 stopped producing ordinary implementation bugs years ago.
This produced thirteen in one session, and the rate has not fallen.

**Compared to Sculpt OS:** same family — Genode's recursive parent-defines-
routing is the same principle as the broker. Sculpt runs on a microkernel with
kernel-enforced capabilities, has drivers, a GUI, and years of production use.
This has offline validation, which Sculpt does not.

**What it is:** a working skeleton with the two hardest components built and
heavily attacked. The architecture held up under thirteen corrections, which
is the encouraging part.

**What it is not:** an init. It has never run on real hardware, and the
next change is still likely to find something.

---

## 14. The pattern

Every significant correction in this session came from building or measuring,
never from further design discussion.

- The `rebind` primitive already existed
- Attach cost was off by 46×
- The liveness statistic was wrong three times
- Apparent non-linear scaling was measurement noise
- Thirteen bugs, all found by execution

And the recurring fix, arrived at independently four times: **design the
problem out rather than checking for it.** No ordering list to get wrong. No
counter to overflow. No second limit to drift. No channel to impersonate.

---

## 15. Session note — 2026-09-09

Two defects fixed. Both landed on clauses from section 14 almost verbatim,
which is either evidence the pattern is real or evidence of looking for it;
recorded here so the next reader can judge.

### The two fixes

**No second limit to drift — the seccomp allow-list.** `nwsup.c` carried its
own inline 33-entry table and applied the filter through `prctl` directly. It
never linked `lids.o`, and the Makefile built `lids.o` as a target nothing
referenced. The two tables were found to agree exactly, as sets *and* in order
— order matters because the jump offset is computed `NALLOW - i`, so a
reordering changes the generated BPF even with identical membership. They were
merged while they still agreed rather than after they diverged. The fix deleted
a copy rather than adding a test that the copies match: `__NR_read` now appears
in exactly one file. `lids.h` is included by both translation units, so a
signature change cannot pass the compiler unnoticed.

**No ordering list to get wrong — wire binding.** The electrician assigned each
unit's wires in blob edge-declaration order and told the unit only a count, so
`fd 3+k` meant "the k-th edge in file order that mentions me", pinned to
nothing. Two plans with identical units, peers and edge multiset, differing only
in the order two `wire` lines appeared, wired the same unit to different peers
on the same descriptor. Silent: both blobs passed `nw-check` rc=0, both booted
rc=0.

Reproduced before fixing, at 2 wires and at 62:

    hubmap wires=2 digest=0x92adcd67 map=fd3:IAM=north,fd4:IAM=south
    hubmap wires=2 digest=0x9e722feb map=fd3:IAM=south,fd4:IAM=north

At 62 wires the whole permutation reversed, asserted by an FNV-1a digest over
the ordered tokens computed independently on both sides. Deterministic across
ten alternating boots. `tests/wire_order.py` holds the reproduction.

The fix exports `NW_WIRE_<fd>=<peer name>` per wire; a unit names the peer it
wants and resolves a descriptor.

**The rejected fix is the more instructive half.** Sorting edges canonically
makes the mapping stable, which passes a test comparing two orderings — while
the unit still cannot name its peers. *Stability is not knowability.* Ordering
by peer name fails for a second reason: inserting a new peer silently renumbers
every existing descriptor, with no plan-visible change to the edges that
already existed. Both are ordering lists to get wrong. What shipped removes
ordering from the semantics instead, so there is no rule left to get wrong.
Consequently the test's criterion is the `hubbind` line, not `hubmap`: the hub
resolves each peer through `NW_WIRE_*` *before* reading, then asserts that
descriptor delivered that peer's identity. The map still flips with edge order,
and that is correct — the fix does not reorder anything, it makes position
irrelevant.

### Method

Section 14 held four more times, and in each case reading would not have done.

- The wire defect was reproduced before anyone touched it, and at 62 wires as
  well as 2 — five earlier bugs were correct at 4 units and wrong at 4,000.
- The env ceiling was **measured, not estimated**: 2,702 bytes added at the
  design ceiling (63 wires, longest names `name_ok` permits), 10,294 total,
  real `execv`. Real ceiling 38,788 variables, bounded by `RLIMIT_STACK/4` —
  established by halving and doubling the stack limit (19,452 / 38,788 /
  77,694), not by reading `getconf ARG_MAX`, which coincides with it at the
  default and would have looked like the answer. ~615x headroom; failure mode
  is a clean `E2BIG`.
- The two seccomp tables were diffed mechanically as sets and as sequences.
  Eyeballing would have missed a reordering.
- A bug introduced in `houses/hub.c` during this work was caught only by
  running: a display-elision `break` exited the loop that also counted matches,
  so a passing run reported `ok=20` of 62. The small case passed and the
  resolution looked right.

`spec` was asked to confirm rather than assume, and reported that `Wire` in
`plan.als` is an unordered `sig` with no ordering relation — so the two blobs
the reproduction compares are *the same instance* in the model. The
implementation had invented an order the format never granted it. Not a spec
violation; a place where code read meaning into something the format left free.
Note also that both specs passed identically while the bug was live, which is
the sharpest available demonstration of their scope.

### Residue — three smaller instances of the same classes

Honest accounting: each fix left something the pattern would want closed.

- **`NW_WIRE_*` is a contract nothing validates.** The environment is inherited
  wholesale, so a stray `NW_WIRE_9` can survive into a unit with no wire 9.
  Cross-routing is structurally impossible (the electrician overwrites the whole
  range with `overwrite=1`, and outside it the descriptor is closed), and the
  failure is loud — except at fd 0, where `/dev/null` yields EOF rather than
  `EBADF`, a false "peer gone" rather than a false peer. The version with
  nothing to get wrong is a single `NW_WIRE_MAP="3=north,4=south"` string.
- **The seccomp entry point has three declarations and the compiler checks
  two.** `lids.c`, `lids.h`, and the `extern "C"` block at `nwsup.rs:19`. They
  were verified to match by reading. That gap was closed with a comment, which
  is checking for the problem rather than designing it out.
- **The twins are behind by one block.** `electrician.rs:209` and
  `electrician.zig:308` set only `NW_WIRES`/`NW_KIT`, so a name-binding house
  sees every peer `UNRESOLVED`. Uncaught here because `tests/bakeoff.py` never
  runs in this container.

The shape worth keeping: two faithful fixes, each leaving a smaller instance of
the class it removed. Consistent with section 13 — the architecture holds, and
the next change is still likely to find something.

---

## 16. Design note — two planes, app broker, KEEP (2026-09-10)

Proposal under evaluation, not adopted. System plane unchanged: validated plan,
static for the boot, `NoLiveRewrite` absolute. On top, an app plane wiring GUI
apps live on a canvas, with a KEEP artifact holding edges plus layout, restored
after reboot. Saving is a bake into a new slot. Grant model: **permit-live,
warn-continuously, refuse-at-save** — the app broker grants on a local check,
the baker re-validates in the background and marks the canvas when the
arrangement becomes unsaveable, naming the edge responsible.

Objections are recorded in full below because the conclusions are weaker than
the objections and the next reader needs both.

### 1. Where the two-plane split leaks

**`NoLiveRewrite` is not violated, and that fact is worth nothing.** The
predicate is literally `UNCHANGED <<n, e, crit, lids>>` over plan variables. An
app broker touches none of them, so formally the split is clean. But `Plan.tla`
has no next-state relation and no temporal formula, so `NoLiveRewrite` is never
checked against any behaviour. It passed identically while the wire-order bug
was live. Anyone citing it as evidence the split is safe is citing a predicate
no behaviour is evaluated against.

**In substance the property is not preserved, it is scoped away.** Invariant 8
exists so the running system cannot acquire new capability-granting operations.
The app plane is exactly such an operation. "The live city does not grow verbs"
becomes true of the plane where nothing interesting changes and silent about the
plane where the user works. That is a weaker claim wearing the same words.

**The real leak is invariant 5.** Non-provision says a unit with no declared
edges holds no descriptors, so there is no doorman and nothing to bypass. An app
broker that manufactures socketpairs on request *is* a doorman. App-plane
isolation becomes enforcement — the broker decides — rather than structural.
Invariant 5 does not say "prefer non-provision"; it says do not add a doorman.
And a doorman granting on a *local check* is a checkable property substituted
for a structural one, which is the specific move `electrician.md` forbids.

The system plane statically grants a dynamic authority: the app broker is a unit
started at boot, and its authority to create arbitrary app edges is conferred
there. The split does not dodge this. It relocates it.

**"Only copy of the connection graph" survives only in a weakened form.** Each
broker would hold the only copy of *its own* graph. Two consequences:

- Invariant 4 cannot apply to both. If app-broker death is fatal, one crashed
  desktop component halts the machine. If it is not fatal, the app graph must be
  reconstructible, so a second copy exists — and the KEEP *is* that second copy.
  The single-point-of-failure reasoning that justifies halt-on-death therefore
  does not transfer, and the two brokers need explicitly different death
  semantics. Recorded because `electrician.md` says "do not add self-restart",
  and someone will apply that to the app broker by analogy and be wrong.
- **Split-brain is the harder half.** `electrician.md` rejects electrician
  restart because a replacement makes socketpairs live units do not hold —
  silent, undetectable from inside. A restarted app broker re-reading the KEEP
  has precisely this: surviving app units hold descriptors minted by its
  predecessor, while its own table says the edge exists. Unless it can re-adopt
  descriptors or the apps restart with it, the app plane reproduces the exact
  failure the halt rule exists to prevent, in the plane where halting is least
  acceptable. **No mechanism in the current design addresses this.**

### 2. Reporting which edge failed

Mechanically trivial; the interesting costs are elsewhere.

`int nw_check(const void *, uint32_t)` has three call sites — `pid1.c:239`,
`electrician.c:172`, `nwcheck_main.c:26` — plus the `blob.h:68` declaration. Add
`nw_check_at(blob, len, uint32_t *where)` and make `nw_check` a wrapper passing
NULL: all three sites stay untouched.

**No effect on the no-allocation or bounded-loop constraints.** The index is
already live at every failure point (`i` in the edge loop; `i` and `j` for
`NW_E_DUPEDGE`). Writing it through a caller-supplied pointer allocates nothing,
adds no recursion, changes no loop bound.

Four real costs:

- `NW_E_DUPEDGE`'s culprit is a *pair*. Reporting only `i` names the second
  occurrence, which need not be the edge the user just drew. The out-param wants
  to be a small struct, not a scalar.
- **Blob edge index is the wrong identity for a canvas.** The UI needs "the edge
  between Editor and Shell". Edge indices are unstable across bakes — inserting
  an edge shifts every later index. That is the ordering hazard fixed in section
  15 reappearing in the error path. Report the two *unit names*, which are
  stable and already duplicate-checked.
- `nw_check` returns on first failure; a canvas wants all bad edges marked.
  Accumulating results needs somewhere to put them, which is the first pressure
  toward allocation in a no-malloc file. Correct split: the baker reports every
  failure (Python, unconstrained); `nwcheck.c` keeps first-failure-plus-location
  as the boot gate. Re-running the checker per edge is not an option — the
  duplicate-edge scan is O(n²) (`nwcheck.c:154-160`), so that is O(n³).
- Any new code or field moves `errs[]` and the `nw_errstr` bound with it.

### 3. `NW_MAX_UNITS = 64` and the fd budget

The app plane needs its own limits, for a stronger reason than 64 being small.

The `_Static_assert` bounds *the electrician's* descriptor table: two fds per
unit and two per edge held simultaneously in one process. An app broker is a
different process with a different table and its own `RLIMIT_NOFILE`. Reusing
`NW_MAX_UNITS` would make one name mean two things in two processes — a second
limit to drift, in the exact sense of section 14.

Raising the system-plane constants instead is worse than it looks. At `u=200,
e=300` the assert yields `8 + 400 + 600 = 1008 <= 1024`: it passes with 16 to
spare, and nothing marks that margin. Measured ceilings put `pid_max` as the
real binding constraint at ~16,000 units (`fs.nr_open` allows ~500,000), at
~326 kB per unit including its supervisor — so the system-plane process count is
not what limits a desktop; one broker's fd table is.

**Ordering dependency with a recorded open item.** Section 12 notes the budget
formula omits the `parked[]` term and fits only by slack in `NW_FD_RESERVED`.
Any new limits consume exactly that slack. The app plane must not reuse the
formula until that item is fixed, or the two defects compound and the
`_Static_assert` keeps passing while the property fails.

### 4. What the baker can check that a live broker cannot

**First, a correction that undercuts the usual example.** Section 6 states the
validator does cycle detection and describes optimising it with a counting-sort
adjacency index. A search for `cycle|acyclic|topolog` across every `.c`, `.h`,
`.py`, `.als` and `.tla` in the tree returns **nothing**. Cycle detection exists
in neither the checker nor the baker.

**Section 6 is deliberately left as written.** Neither the author of this note
nor the operator can tell whether the check was implemented and later removed,
or whether the claim was never accurate. Editing section 6 would erase the
evidence that the two disagree, and the disagreement is the useful artifact: it
means at least one statement in the historical record about what the TCB does
was wrong at some point, and nothing caught it. Recorded here as a live
documentation defect rather than repaired silently. Do not cite section 6 as
precedent for a baker-only check until it is resolved, and resolve it by
determining which of the two is true rather than by making them agree.

**Second, the structural blocker.** `struct nw_edge` is `{uint16_t a; uint16_t
b;}` — no direction, no capability label. Duplicate detection normalises to
`lo/hi` (`nwcheck.c:155-159`) and the electrician treats `a` and `b`
symmetrically (`electrician.c:191-192`). Edges are undirected. So "multi-hop
capability flow" has nothing to flow along: reachability in an undirected graph
is connected-components, and on a desktop canvas nearly everything is in one
component. **Any multi-hop policy requires adding direction and probably
capability labels to the edge record** — a blob format change, new `NW_E_*`
codes, and all four fd-budget sites revisited. This is the largest cost in the
proposal and it is not in the proposal.

Granting that direction is added, the genuinely baker-only checks are:

- **Global acyclicity.** Local view answers "may A connect to B"; a cycle
  A→B→C→A is visible only whole.
- **Transitive confinement** — "this app is never transitively connected to the
  network unit". B may already reach Net via C. The broker would need an
  incrementally maintained transitive closure over a graph it is mutating, in a
  latency-critical path. That is a graph engine in the runtime, which inverts
  the table-interpreter principle the architecture rests on.
- **Non-existence properties.** "No path from A to Net" quantifies over all
  paths. A grant check answers a question about one edge. Non-existence over a
  mutating graph is not establishable locally at all; it needs a quiescent
  snapshot, which is what a bake is. This is the strongest argument for the
  two-phase model and should be the one cited.
- **Aggregates** — budget, degree, totals. A broker can keep counters, but a
  counter is a second copy of a fact derived from the graph. Section 14: no
  counter to overflow.
- **Saveability itself.** Only the baker produces the artifact, so only the
  baker knows whether the arrangement is expressible.

### Objections to permit-live / warn / refuse-at-save specifically

- **It inverts the stated preference.** `baker.md`: "prefer rejecting at bake
  time over checking at boot time — a plan that cannot be expressed cannot be
  mis-executed." This permits constructing the inexpressible and reports later.
- **"The specific edge responsible" is often not well defined.** When an
  arrangement becomes unsaveable the fix is frequently a choice among several
  edges — a minimal-cut question. Naming one edge names an arbitrary member of a
  set, and the user may remove the wrong one.
- **The proposal does not say what happens to live edges when a save is
  refused.** Three options, each bad: they keep running and the KEEP silently
  omits them, so the restored desktop differs from the running one; they are
  torn down at save time, so saving mutates the working arrangement; or saving
  is blocked until manual repair, which is least bad but admits a canvas state
  with no exit.
- **Layout and edges in one artifact is a rate mismatch.** Layout is cosmetic
  and changes on every window drag; edges are semantic. One artifact means
  either baking a new slot at UI rates or letting layout lag. Two artifacts —
  a KEEP referencing a layout blob by hash — keeps the blob CRC meaningful.

### App-broker death — settled

Both horns, then a recommendation, because this gates everything else.

**Fatal.** Preserves one rule for all brokers with no exception to remember, and
makes split-brain structurally impossible: if everything the broker wired dies
with it, no survivor holds an orphaned descriptor.

It fails on a premise check. The electrician *earns* halt-on-death because it is
**inert after startup** — seccomp'd down to `pause`, `rt_sigreturn`,
`exit_group`, with zero `wait`, `waitpid` or budget arithmetic remaining. Its
death is therefore nearly impossible and genuinely exceptional, so treating it
as fatal costs almost nothing. An app broker is the exact inverse: long-lived,
interactive, servicing UI events, mutating a graph live. It cannot be inert and
stay responsive. Applying halt-on-death to a process with the opposite
characteristics copies the electrician's *conclusion* while discarding its
*premise*. The result is the worst combination available — the component most
likely to crash, given the most catastrophic death semantics. A desktop that
loses every running app because the wiring canvas segfaulted is not a desktop.

**Not fatal, reconstruct from the KEEP.** Survives the crash, and gives up
nothing that was actually held: the KEEP already is a second copy of the graph,
so the "only copy" property is gone by construction the moment a KEEP exists.

Naively it is unsound, and for a reason sharper than the usual split-brain
argument. Surviving apps hold descriptors minted by the dead broker. A
replacement either mints fresh socketpairs for the same logical edge — leaving
each app holding one end of a *different* pair, so messages go nowhere, silently
— or it trusts the KEEP and never verifies, in which case its table describes
connections it cannot confirm. **And the deeper problem: to re-wire a surviving
app the broker must pass it a descriptor, which requires a control channel to
that app — a channel the dead broker created.** Reconstruction from the KEEP
does not restore the means of reconstruction.

**What the dilemma actually turns on.** "Only copy of the connection graph" was
never the real reason; it is a proxy for **undetectability**. The electrician's
death is unrecoverable because a replacement has no way to learn what survivors
hold and no channel by which to correct them. The property that matters is the
absence of a re-adoption mechanism, not the uniqueness of the graph. That
distinction is what makes the two brokers genuinely different rather than
analogous: the electrician has no such mechanism and could not easily be given
one; the app broker can be built with one from the start.

**Recommendation: non-fatal, conditional on two things, and unsound without
both.**

1. **Control channels provisioned by the system plane, not the app broker.**
   Each app gets a control socket created at boot by PID 1 or its supervisor and
   handed to the app broker. Its lifetime is independent of the broker, so a
   replacement receives the same channels and can reach every surviving app. The
   system plane's static provisioning is what makes the app plane's dynamic
   wiring recoverable — non-provision is preserved for the channel that matters.
2. **Apps must support rebinding an edge descriptor at runtime.** A restarted
   broker *replaces* descriptors rather than assuming the old ones, which is
   what defeats split-brain. This is not a new burden: a live wiring canvas
   already means an app's edges can change while it runs, so any app fit for
   this plane must handle rebind regardless. Section 5 and `supervisor.md`
   already treat rebinding as cheap and available.

With both, the app broker holds no unrecoverable state: the graph is in the
KEEP, the channels come from the system plane, and descriptors are replaceable.
Restart is then sound and the halt rule correctly does not transfer. With
either missing, restart reproduces exactly the failure `electrician.md`
describes, in the plane where halting is least acceptable — so *plain*
"non-fatal, reconstruct from the KEEP" as proposed is rejected.

Corollary worth stating: the app broker is not analogous to the electrician and
should inherit none of its rules by default. Each rule must be re-derived from
the app broker's own premises. `electrician.md`'s "do not add self-restart"
applies to the electrician and not here.

### Verdict — direction first, as its own piece of work

Unsoftened: **the two-plane proposal should not proceed as specified, and
adding direction to the edge record should be a separate piece of work that
lands first.**

The goal is worth having. The order is wrong, for a reason that is not
stylistic. With undirected edges the baker can check connectivity and nothing
else, and on a desktop canvas nearly every app is in one connected component —
so the answer to almost any confinement question is "yes, connected", which
is no answer. Effectively every property the app plane is *for* — this app must
not reach the network, that one must not reach the filesystem service, this
capability must not flow past that boundary — is directional. Build the
two-plane machinery on today's `{a, b}` record and you get a doorman, a
split-brain hazard, a warn-at-save path with almost nothing it can warn about,
and a background baker whose full validation is barely stronger than the
broker's local check. That is a large amount of mechanism bought for very little
checking, and the checking was the entire justification for the two-phase model.

Direction wants to land alone for the ordinary reason: it touches `blob.h`,
`nwcheck.c`, `bakery/nw-cc.py`, `plan.als`, `Plan.tla`, the electrician's wire
collection, and the four fd-budget sites. That is a format change across the
TCB and both specs, and it should be verified on its own evidence rather than
as a sub-task inside a desktop feature.

Sequence: fix the section 12 `parked[]` item (limits cannot move until the
formula is honest) → add direction to the edge record as standalone work →
then reconsider the app plane, with the restart conditions above as
preconditions rather than open questions.

Direction is **necessary but not sufficient**. It does not touch the invariant 5
objection: a broker that manufactures edges on request is still a doorman, and
that remains the strongest argument against the whole shape. Direction makes the
two-plane model *checkable*; it does not make it *structural*.

### If it proceeds anyway

Narrow the broker's local check to exactly the subset that *guarantees*
single-edge saveability: name validity, self-edge, duplicate, degree and budget
headroom. Then only genuinely multi-hop properties can fail late, the warn path
covers a small well-defined set, and "the edge responsible" is usually
meaningful because it is the edge that closed a cycle or crossed a confinement
boundary.
