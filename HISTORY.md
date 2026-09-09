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
