# 17 — Edges: bringing back inter-house sockets

Status: **built, tested and reviewed this round** — `tcb-review`,
`fd-auditor`, `control` and `claims` all ran against the code changes
this note describes and found nothing outstanding as of their last
pass. This line describes a past review; it is not itself gated —
`tools/review-gate.sh` tracks TCB, suite and a fixed prose set
(`CLAUDE.md`, `.claude/rules`, `.claude/agents`, `HISTORY.md`), and
`docs/options/` is in none of them, so editing this file does not
re-owe anything and this sentence cannot go stale that way. What it
*can* go stale against is the code: run `sh tools/review-gate.sh
--check` to see whether the TCB/suite/prose files this note describes
have since changed. `struct
nw_edge`, `NW_MAGIC` bumped to `NWPLAN11`, `NW_MAX_EDGES`, the
`edge=<a>,<b>` plan syntax resolved to indices at the same moment
binds already are (confirmed, not merely proposed — see "What has
changed" below), the wiring itself in `nwspawn.c` (pre-loop socketpair
creation, `F_DUPFD_CLOEXEC`-batch-then-place per invariant 2, the
per-generation... per-edge close, the wiring census, and a structural
self-check that a house's own leftover edge fds are actually closed),
and the two structural checks in `nwcheck.c` and `bakery/nw-cc.py` are
all built and tested — `make test` is green, including seven new tests
exercising this feature (bidirectional exchange, an unwired house
getting no wire, backpressure, a 16-house ring with zero cross-talk,
baker/checker refusals for bad edges, and a tight-fd-budget-plus-edges
preflight pair).

**Reviewed once already, and what that caught.** `tcb-review`,
`fd-auditor` and `control` all ran and were recorded via
`tools/review-gate.sh --record` against an earlier state of this round's
content, plus two `claims` passes on this note's own prose at that
point. They found and this round fixed two real, reproduced bugs in
code, not stylistic nits:

1. **The original leaked-wire-fd self-check killed every boot in an
   environment where nw-spawn's own inherited fd 0/1/2 happened to be
   socket-backed** (a QEMU unix-socket serial console is an ordinary
   way to get this), regardless of whether any edge was ever declared
   — fd-auditor reproduced it live. Fixed by checking only the specific
   descriptors nw-spawn itself created (`edge_fd[k][0/1]` via
   `fcntl(fd, F_GETFD)` answering `EBADF`), never scanning the whole
   fd table.
2. **`pid1.c`'s fd preflight never carried an edge term**, so a plan
   with a tight fd budget plus declared edges could pass the preflight
   and then die deep inside nw-spawn with a raw, un-preflighted `EMFILE`
   — fd-auditor reproduced this too. Fixed by adding `2 * n_edges` to
   the preflight's `need`, a sixth site for invariant 3's arithmetic
   beyond the design note's original list of five (named in pid1.c's
   own comment). A new test, `test_fd_preflight_names_the_shortfall
   _with_edges`, closes the gap: no existing test had combined a tight
   fd budget with declared edges.

Both fixes were themselves re-reviewed (a second, narrowly-scoped
`tcb-review`/`fd-auditor`/`control` round against the actual fixed
code, since a review is keyed to content and editing after a review
un-does it) before being recorded. `blob.h`'s own `_Static_assert`
carries the edge term (via the `NW_FD_NEED` macro, see below);
`bakery/nw-cc.py`'s `check()` carries it too. **`nwcheck.c` does NOT
carry a runtime re-check of it** — say this precisely, since an earlier
draft of this paragraph claimed nwcheck.c "carries" the term as though
live code existed there: `nwcheck.c`'s comment at the retired
fd-budget-check site *names* `NW_FD_NEED` in prose, explaining why no
check was reinstated (the worst case, 392, is still unreachable under
1024 at any legal count) — there is no `if`, no `NW_E_*` return, nothing
executable. `claims` found the overclaim. **`plan.als`'s `fdNeed` and
`Plan.tla`'s `FdNeed` now carry the term too**, per an operator-authorized
follow-up round — see "Ownership flag, resolved" below for the reading
that stopped this round short of them at first, and what changed.

## Ownership flag, resolved (as far as the tree can resolve it)

**`git log` cannot disambiguate Grok from Claude for `plan.als` or
`Plan.tla`, but it is not silent either, and the first version of this
paragraph overclaimed by saying it was.** Most commits touching either
file — `ddfcb56`, `6b20b28`, `3646f1c` among them — are attributed to a
generic author, `nwdev <nwdev@localhost>`, regardless of which agent
actually wrote them. But `fbefb29` — the commit that *removed* edges in
the first place, touching both `plan.als` and `Plan.tla` — is authored
`Claude <noreply@anthropic.com>` (`git show --stat fbefb29 -- plan.als
Plan.tla` confirms it touches both), and several later commits on other
files are authored `Isolati0n <nwdev@localhost>`. So there is a
per-agent signal in the tree for these two files; what it does *not*
do is name "Grok" anywhere — `git log --all` shows only `Claude`,
`Isolati0n`, `nwdev` and the nw-init pid1/dawn commit identities, never
a Grok-attributed one. The practical reading below is unchanged by
this correction (neither agent is positively named as owner in
`CLAUDE.md`'s sentence, whichever wrote what before), but the premise
that got there was wrong and is fixed here rather than left standing.
`claims` found it.

**My reading of `CLAUDE.md`'s sentence: "Nobody else touches `plan.als`
or `Plan.tla`" most naturally means nobody touches them this week at
all** — neither Grok nor Claude is named as their owner in that
sentence (only "the baker and the mount path in `nwsup.c`" is named
for Claude, and `plan.als`/`Plan.tla` are named separately, negatively),
so "else" has nobody positive to except. Read this way, the sentence is
a freeze, not an assignment. A second reading — "nobody besides these
two agents" — is textually possible but reads oddly given neither
agent is the one named as their owner either. Genuinely ambiguous, and
not resolvable by evidence in this tree.

**UPDATE — the operator authorized this edit directly, and it is now
done.** Everything below this line described the state where this note
stopped short per the reading above; `CLAUDE.md`'s ownership sentence
now records the authorization in place (see its own "Who owns what,
this week" entry). What follows is kept as the record of what was
missing and why, since the reasoning for the freeze reading is still
correct for the next agent who has NOT been told directly.

Both specs now carry the edge term. `plan.als` gained a `sig Edge {}`
(opaque, matching `Brick`/`Layer`/`Capacity`'s precedent — no `a`/`b`
endpoint fields, since the structural edge rules are nwcheck.c's job,
not this file's) and `fdNeed[]` reads
`plus[nwReserved[], plus[2.mul[#House], 2.mul[#Edge]]]`; `Plan.tla`
gained `e` as a model variable (ranging `0..MaxEdges`, restored per its
own retired-edges note rather than reinvented) and
`FdNeed == Reserved + 2 * n + 2 * e`. Both specs' own second-copy pins
(`assert FdArithmetic` in `plan.als`, `FdNeedAgrees` in `Plan.tla`) were
extended to match.

**The control this task also asked for, closing a gap plan.md records
at length: nothing had ever compared either spec's fd-need ARITHMETIC
against blob.h's, only the VALUES.** `blob.h` gained a named
`NW_FD_NEED(nu, ne)` macro (both `_Static_assert`s now call it rather
than repeating the expression), and `tools/fdneed-oracle.c` is a
standalone program that compiles against it and prints the answer for
CLI-given `(nu, ne)`. `tools/gen-spec-limits.py` runs that oracle at two
sample points — `(1, 7)` and `(7, 1)`, chosen so a wrong unit
coefficient, a wrong edge coefficient, or the two coefficients swapped
with each other are each caught by at least one sample — and writes the
results into the generated `limits.als`/`Plan.cfg` as
`fdNeedOracle_1_7[]`/`fdNeedOracle_7_1[]` and
`OracleH1E7`/`OracleH7E1`. Both specs gained a `FdNeedOracleAgrees`
check comparing their own `fdNeed[]`/`FdNeed` against those compiled
values. It also feeds `tests/run.py`'s `tight` boundary value (used by
the existing `FdBudgetCovers`/`LargestCityFits` must-fail/must-hold
probes), replacing a fourth hand-typed Python copy of the same
multiplier with the compiled answer.

**Verified working, not merely built.** Mutating blob.h's `NW_FD_NEED`
macro to `(nu) * 3` (leaving edges at `* 2`) in a scratch copy and
re-running `test_specs_are_checked` fails with `Invariant
FdNeedOracleAgrees is violated by the initial state ... e=7, n=1` —
the oracle, recompiled from the mutated header, disagrees with the
unmutated spec's own formula at exactly the sample built to expose a
unit-coefficient drift. `make test` is green on the unmutated tree
(`specs-are-checked` reports 5 invariants, 3 Alloy checks, 8256 TLC
states covering every legal `(unit, edge)` pair).

## Ownership flag — checked, not assumed (as filed before the build)

`CLAUDE.md`'s "Who owns what, this week" reads: *"Grok owns `pid1.c`,
`dawn.c` and the restart loop in `nwsup.c`. Claude owns the baker and
the mount path in `nwsup.c`. Nobody else touches `plan.als` or
`Plan.tla`."* Everything this note proposes touching —
`nwspawn.c` (where the wiring would live; see below) and `bakery/nw-cc.py`
(where edge parsing and index assignment would live) — needs checking
against that sentence rather than assumed.

- **`nwspawn.c` has no named owner in that sentence.** It is not `pid1.c`,
  not `dawn.c`, not "the restart loop in `nwsup.c`," not "the mount path
  in `nwsup.c`." It sits in the `runtime` territory (`tools/rules-hook.sh
  --owns runtime` lists it), but `CLAUDE.md` is explicit that territory
  membership is a different thing from the weekly per-agent sentence —
  "The ownership assignment above is deliberately not derived from
  anything. It is a scheduling fact about who is working on what, not a
  property of the code." So this is unassigned, not silently claimed.
- **`bakery/nw-cc.py` — "the baker" — is more ambiguous than it first
  looks, and I'm flagging the ambiguity rather than resolving it in my
  own favor.** `CLAUDE.md`'s TCB table names `bakery/nw-cc.py` by the
  role "baker (`nw-cc`)" throughout, and the ownership sentence says
  "Claude owns the baker" — which reads as naming this file by role. But
  a role name standing in for a literal filename in a sentence that
  otherwise names files (`pid1.c`, `dawn.c`) is an inference, not a
  verbatim match, and I am the one making that inference about my own
  scope. Recorded here so it can be confirmed or corrected rather than
  taken as settled by the party it would benefit.

Per `CLAUDE.md`'s "fix it and flag it" rule, neither of these blocks
writing a *design note* — a note is not a commit to Grok's or anyone
else's territory, and this one stops here regardless. It matters for
whoever picks up the *build*, and is stated so that decision is made
knowingly rather than inherited unchecked, the same way
`docs/options/11-start-stop-channel.md` flagged its own restart-loop
seam before anyone built against it.

## Why this reopens a removed feature, and why the old objection does not apply to the new one

Edges were removed in `HISTORY.md` §17, and the removal's stated reason
was not "it was buggy" — the three bugs were a symptom, not the
argument. The argument was that edges' whole justification was
**isolation and capability discipline for its own sake**, and that
premise was explicitly withdrawn — quoted here in `HISTORY.md`'s own
order, premise then conclusion, not reordered as an earlier draft of
this note had it: *"Cybersecurity is not a goal of this system.
Containerization applies to apps, not to the init's own units. Once
that premise is withdrawn... [the entire apparatus] was paying for a
property nobody wanted."*

**That objection does not transfer to the reason given for reopening
this now, and the difference is worth stating precisely rather than
waved past.** The old justification was abstract isolation —
segregating houses from each other as a general security posture. The
new one is narrower and load-bearing in a different way: **a house that
needs to talk to one specific, named peer currently has exactly one
tool — grant it the network lid and a loopback socket — and that
satisfies a narrow need (reach one peer) by granting a broad one (reach
the network namespace generally, with everything else that namespace
makes possible).** This is the same shape invariant 6 already argues for
lids in general: the lid set exists so a house's capability is
*declared*, not defaulted into by whatever happens to be the easiest
tool at hand. An edge, on this reading, is not a security feature bolted
on for its own sake — it is **removing an over-grant that exists today**
because the narrower tool doesn't exist. Secondary and independent of
that argument: making the communication graph plan-declared and
offline-checkable (today two houses talking via a shared `bind=` is
invisible to the plan — nothing declares that relationship exists at
all), and a socketpair's real backpressure (a write blocks until read),
which a shared file structurally cannot provide.

**This is not typing, ordering, or capability-flow analysis, and does
not reopen any of those refusals.** `plan.md`'s *Refused deliberately*
section is explicit that those "None were ever built and after §17 none
are definable," in the specific sense that a flat list of units has no
relations for a graph algorithm to run over. Edges reopen exactly one
relation — "these two named units get a socketpair" — checked once,
offline, as a structural fact about the plan, the same way a `bind=`
path or a `layer=` id is checked. Nothing here proposes ordering
(boot-order dependency is `gate`'s territory, itself waiting on a
prerequisite in `plan.md`, and unrelated to this), nothing proposes
inferring what flows over the wire, and nothing revives cycle detection
— an edge has no direction to form a cycle in, any more than a `bind=`
line does.

**A gap in `plan.md`'s own refusal that this note should name rather
than leave for the next reader to find.** `plan.md`'s Cycle-detection
bullet reads "undefined, not deferred. A plan is a flat list of units
with no relations — **there is no graph, so there is nothing to have a
cycle in**." That premise sentence is what edges make false, not the
refusal's conclusion — a plan with edges genuinely has a graph
(undirected, connectivity-only, but still graph-theoretically capable
of containing a cycle, even though nothing here proposes *detecting*
one). The refusal itself stays correct (nothing here builds cycle
detection, and an undirected connectivity edge has no dependency
semantics for a cycle to mean anything about), but the sentence
*justifying* it would need its premise corrected the day this lands —
"there is no graph" becomes "there is a graph, and nothing here reads
it as one with cycles worth detecting." This is the same shape `gate`'s
own entry two sections below already names for itself ("re-filing TWO
refusals above, not one"), and it belongs on the list of things whoever
builds this must also do, not left for a future `claims` pass to find
a true-when-written sentence gone stale under it.

**`plan.als`'s own retired vocabulary already named the shape this
would need to avoid, and it is worth citing directly.** §16-17 record
that edges were "undirected, so the baker could check connectivity and
nothing else," and that building anything richer on top (ports, typed
flow, per-direction capability) was the fork in the road that led to
removal rather than extension. This note does not propose taking that
fork. What follows below is deliberately a smaller thing than what was
removed in one respect (fewer structural checks worth reinstating,
since two of the three original bugs are now unreachable by construction
elsewhere in the tree — see below) and the same size in another (still
undirected, still connectivity-only, still no port/type semantics).

## The attic implementations, and the bug diagnoses corrected against `HISTORY.md`

Read in full: `attic/electrician.c`, `attic/electrician.zig`,
`attic/electrician.rs`. These are not three independent attempts —
`electrician.c` was the real, load-bearing broker for the edge era, and
was later renamed/rewritten 58% into today's `nwspawn.c` (`git log`
records `rename electrician.c => nwspawn.c`); `electrician.zig`/`.rs`
were bake-off twins built to cross-check the C version, not separate TCB
candidates. All three share one wire format: a flat blob of edge
records, each two `u16` fields naming the **table index** of the two
connected units (undirected — `a`/`b` carry no port or direction), and
one allocation algorithm: `socketpair()` every declared edge up front,
then for each unit walk the edge table and hand it whichever end names
its own index, via `fcntl(F_DUPFD_CLOEXEC, 3)` to a scratch slot and
`dup2()` into a contiguous `3+k` block. **That much — the wire format and
the allocation algorithm — all three genuinely share; the peer-identification
scheme does not, and saying so without qualifying it overclaims a
uniformity that isn't there.** Only `electrician.c` sets `NW_WIRE_<fd>=
<peer-name>` per wire, so a house can look up which fd belongs to which
peer; the Rust and Zig twins only ever `setenv("NW_WIRES", <count>, 1)`
— a bare count, no per-descriptor peer name at all, so a house built
against either twin had no way to learn which peer sat on which fd.
That gap is exactly what `HISTORY.md` records the twins falling behind
on ("The twins are behind by one block... a name-binding house sees
every peer `UNRESOLVED`") — it is the C lineage, the one that lives on
as `nwspawn.c`, that carries the fix this note builds on.

**Correcting my own earlier framing of bug 5 against what
`HISTORY.md` actually says, rather than repeating an imprecise
paraphrase**: bug 5's row reads *"`dup2(fd,fd)` doesn't clear
CLOEXEC — every unit got zero edges."* The root cause is a POSIX
semantics trap, not a mismatch between the baker's output and a
wire-attachment step: `dup2(fd, fd)` is defined to be a no-op when
`oldfd == newfd`, so if a scratch descriptor from `F_DUPFD_CLOEXEC`
ever coincidentally landed on the exact fd a wire was also being
`dup2`'d *to*, the `CLOEXEC` flag that scratch step set was never
cleared, and the descriptor vanished at `execve`. The attic code already
carries the fix (`clear_cloexec()` called unconditionally, outside the
`if (parked[i] != dest)` branch) — what's in the tree today is the
post-mortem version, not the bug. **Bug 4** ("blob index vs table
index — every edge silently mis-routed, no error") is the row closer to
a baker/attachment mismatch, and it is a *different* bug from 5: a
reordering of the unit table between assigning edge indices and
emitting the final blob, with nothing to catch indices that no longer
mean what they meant when computed. **Bug 9** (`ADOPT_FD` collided with
the edge descriptor range — a unit read a struct field as a peer
message) and **bug 13** (socketpairs collided with the log descriptor
base — 32 of 33 units at 46 edges wrote output into a peer's connection)
are the two `HISTORY.md` groups explicitly as one class with bug 5:
*"fixed descriptor numbers alongside dynamic allocation... a recurring
failure mode."* That class has a name in this project already —
**invariant 2** — and is not a new lesson this note needs to derive.

## What has changed in this tree since that code was written

**1. `nwspawn.c` no longer forks all units from one long-lived process
holding a shared table — it forks each unit in its own iteration of a
sequential loop, and the loop's own process (the top-level `nw-spawn`)
is the one place a pre-created socketpair table could still live.**
Reading `nwspawn.c`'s current `main()`: one pass over `h->n_units`,
each iteration double-forking (a `mid` process for pid-reporting
bookkeeping, then the `house` process that `execl()`s `nw-sup`).
Per-unit descriptor setup (`pack_kit()`) already does exactly the
`keep[]`-array-plus-`close_others()` sweep this note would extend for
wires — nothing new to invent there, only more entries in an existing
array. **The place edges would create their socketpairs is before this
loop begins** (one pass over declared edges, mirroring the attic's own
"create every pair first"), and the place each unit's fork/exec branch
picks up its own end(s) is inside the existing per-unit iteration,
alongside `pack_kit()`.

**2. The most serious compatibility question is not about descriptor
numbering — it's the reason `nwspawn.c`'s own header comment gives for
why it no longer has a mid-life, and reintroducing edges must not
reopen it.** Quoted directly, because it is the sharpest constraint in
the whole file: *"Its predecessor, the electrician, stayed alive and
inert because it held the only copy of the connection graph; its death
mid-life was unrecoverable and PID 1 halted on it. With edges removed
there is no graph to hold, so this process has no mid-life and normal
exit is the success path."* A `socketpair(2)`'s two ends do not need a
live process holding a third reference once each end has been handed to
its owning house via `fork()` inheritance — the kernel keeps the pipe
alive for as long as *either endpoint* holds it open, with zero
involvement from whoever created it, the same way a pipe's read end
works after the writer that `pipe2()`'d it has moved on. So **this
design's own commitment is that `nw-spawn` closes its own copies of
every wire descriptor immediately after the owning unit's fork
completes** (the same discipline it already applies to the `pp[0]`/
`pp[1]` pid-reporting pipe, closed within a line or two of use) **and
exits normally after the loop, exactly as it does today.** Whatever the
old electrician's actual reason for staying alive was, this design does
not inherit it, and invariant 4's "boot-time only... exits" contract is
preserved rather than reopened. This is the single most important
sentence in this note to hold whoever builds it accountable to.

**3. `invariant 2` — "no compile-time file descriptor numbers alongside
dynamic allocation... sweep `/proc/self/fd`; do not hardcode" — is
already the general rule bugs 5, 9 and 13 belong to, and this section
is an application of it, not a new discovery.** `pack_kit()`'s existing
two-step pattern (scratch via `F_DUPFD_CLOEXEC`, which by construction
never returns the same number as its input; *then* `dup2()` onto a
small fixed target) already avoids the `dup2(fd,fd)` trap for
`log_w → {1,2}`, because the scratch slot is guaranteed `≥ 3` and the
targets are `0`–`2`. Wire descriptors do not have a compile-time-fixed
target the way `0`/`1`/`2` do, so the same two-step pattern needs one
more discipline named explicitly: **collect every scratch descriptor
this unit needs — the log pipe and every wire — before dup2-ing *any*
of them to their final, per-unit-computed slots.** Interleaving
"allocate one scratch fd, immediately dup2 it, allocate the next" risks
a later scratch fd landing on a target slot an earlier wire already
claimed, if allocation and placement are interleaved carelessly; batching
scratch allocation first removes the ordering dependency entirely rather
than relying on getting the order right. Not a new invariant — a
concrete way invariant 2 could be violated by a careless implementation
of this specific feature, named so it is not rediscovered as bug 14.

**4. The sched-ext work is the precedent for the plan-format change
this needs, and this needs the same four/five-place discipline invariant
3 already requires, not a lighter version of it because edges predate
the mechanism-rule.** `struct nw_edge` (two-unit connectivity, likely
still index-based in the blob — see the bug-4 fix below for why that's
safe) needs `NW_AT`/`NW_EXTENT`/`NW_TYPE` declarations in `blob.h`,
which changes the hashed layout declaration text `plan-formats.txt`
pins — meaning a **new `NW_MAGIC`** (currently `NWPLAN10`; this would be
at least `NWPLAN11`, and the exact next value needs re-checking against
whatever has landed by the time this is built, not assumed from this
note). The new row in `plan-formats.txt` is **mechanically re-derived
by the project's own tooling**, never hand-computed — the ledger's own
history records three separate bugs in the hashing mechanism itself,
each fixed by re-deriving historical rows from source rather than
trusting a hand-written hash, and there is no reason to believe a
hand-computed row for this magic would fare better. And the `2e` fd-budget
term that left `blob.h`'s `_Static_assert`, `nwcheck.c`, `bakery/nw-cc.py`,
`plan.als`'s `fdNeed` and `Plan.tla`'s `FdNeed` when edges were removed
needs to come back in **all five places**, consistently — this is
invariant 3's own drift class, stated for a term that has been through
it once already (removed cleanly, per `HISTORY.md` §17's own account of
the removal touching all five).

## Structural fix for bug 5/9/13's class — the absence-census pattern, applied to a runtime fact instead of a filesystem one

`tools/rules-hook.sh --check` closes the analogous file-ownership defect
by enumerating **ground truth directly** (`git ls-files`, filtered) and
asserting every entry is accounted for — deliberately **not** by
maintaining a second, hand-written list to diff against, because a list
that exists only to be compared against another list is maintained by
the comparator's complaints rather than by anyone who needs it.

**The same shape, applied here: the check must read the wiring
bookkeeping `nw-spawn` builds *as a side effect of doing the wiring*,
not a separately maintained list of "edges that should have been
wired."** Concretely: as `nw-spawn` walks its pre-loop edge table
creating socketpairs and assigning ends, it already has to build a
small per-unit "how many wire descriptors does unit `i` get" count in
order to do the assignment at all — that count *is* the ground truth,
the same way `git ls-files`'s output is ground truth for the file
census, not a second copy of it. **Before `nw-spawn` reaches its own
"report every pid, exit 0" contract**, sum that per-unit table and
assert it equals `2 × n_edges` exactly — every edge must have
contributed to precisely two units' counts, not zero (bug 5's
symptom) and not one (a partial-wiring state nothing in the attic era
apparently produced, but nothing prevented either). A failure here means
`nw-spawn` `die()`s rather than completing its pid report — the same
"a complete report or nothing, never a partial one" contract invariant
4 already requires of it for the ordinary case.

**Where this runs: boot-time only, in `nw-spawn`, not offline in
`nw-cc`.** Unlike a `bind=` path (a pure declaration, checkable
statically), a wired socketpair is a kernel object that does not exist
until boot — there is no offline equivalent of "did the wiring happen"
the way there is for "is this bind path syntactically well-formed."
`nwcheck.c` still gets a structural, offline half of this (below), but
the *count* assertion is inherently a runtime fact.

## Structural fix for bug 4's class — index validity, checked exactly like binds already are

Binds already store a raw unit **index** in the blob
(`bd[b].unit`, compared against `(uint16_t)i` in `nwspawn.c` today) and
have never reported a bug-4-shaped recurrence — the index-based
representation is not itself unsafe; bug 4 was the **baker** computing
an edge's index *before* the unit table's final order was fixed, then
reordering underneath it. So the fix belongs in `bakery/nw-cc.py`'s own
discipline, not in a new blob representation: **resolve every edge's
two endpoint names to indices only after the unit table's order is
final** (or, more robustly, keep edges name-keyed internally until the
single last step that packs the blob, the same moment binds' own
`unit=` field is presumably resolved — this needs confirming against
`nw-cc.py`'s actual bind-index-assignment code when this is built, not
assumed from this note, and is flagged here as unverified rather than
claimed). `nwcheck.c` gets the offline half, structurally: `a < n_units
&& b < n_units && a != b` (no self-wire) for every edge, independently
of whatever the baker already checked, per `plan.md`'s "prefer rejecting
at bake time — but any rule the runtime relies on must be in `nwcheck.c`
too." A **duplicate-edge** check (the same `field_dup`-style pass
`layer` ids already get, per `runtime.md`'s `NW_E_LAYERDUP`) closes the
same "two units get two sockets to the same peer that both look like
the declared one" class binds' own `NW_E_LAYERDUP` precedent already
names.

## Scope: what this format actually declares

**Named, not indexed, at the plan-language level** — `edge=<house-a>,
<house-b>` in plan syntax, resolved to a blob-level index pair by the
baker (matching every other cross-referencing field: `bind=`'s `unit=`,
`layer=`'s id). One socketpair type this round, `AF_UNIX`/`SOCK_STREAM`
— the one type that gives the backpressure this whole note argues for;
`SOCK_DGRAM` does not, and is not proposed. **Whether "sized/typed"
means more than that — a declared buffer-size hint via `SO_SNDBUF`/
`SO_RCVBUF`, or a future second socket kind — is read here as scope for
later, not built now, following sched-ext's own precedent of shipping a
closed set of exactly one legal value with room to grow rather than
guessing at a second one nobody has asked for yet.** If a literal size
field was intended rather than "well-specified, not an anonymous
connectivity graph," that's a fact to confirm with whoever wrote the
brief, not resolved unilaterally here. A fixed, plan-declared count per
city (bounded by a new `NW_MAX_EDGES`, the same shape `NW_MAX_UNITS` and
`NW_MAX_BINDS` already have), two specific named houses per edge, no
dynamic creation, no house discovering an edge it wasn't named in.

## COMMITMENT CHECK

Confirmed: an edge is a plan-time declaration, exactly like a `bind=`
or a lid bit — resolved once by the baker, validated once (twice,
counting `nwcheck.c`'s independent re-check), and wired exactly once,
at boot, by `nw-spawn`, before any house runs. Nothing negotiates an
edge at runtime, no house requests one it wasn't declared for, and
`nw-spawn`'s own exit-after-boot contract (confirmed above, against the
specific historical failure mode of a broker with a mid-life) means
there is no live process anywhere whose continued existence an edge
depends on after boot completes.

## What this note is not proposing

No port/type/direction semantics beyond one socket kind, no dynamic
edge creation, no ordering or gating semantics (`gate` is a separate,
still-deferred field), no revival of cycle detection, typing, or
capability-flow analysis, no change to `nw-sup`'s own per-house loop
(wiring is entirely a `nw-spawn`-side concern; a wired house simply
finds its peer descriptors already in its table via inherited,
non-`CLOEXEC` fds, identified by an `NW_WIRE_<fd>=<peer>` environment
variable, the same scheme the attic code already arrived at after its
own fix).

## Next step

**This section has gone through three stale versions before this one —
kept as history rather than deleted, per the rule that a retired rule is
re-filed, and correcting the record rather than deleting it here since
each version's replacement is the evidence for why it was wrong.** First
it said "no code this round" beneath a Status line already describing
built code. Then "land the outstanding tcb-review/fd-auditor/control
passes," true when written and stale once those passes landed. Then
"`plan.als`/`Plan.tla` still need their edge term," which a `claims`
pass on the operator-authorized follow-up round falsified directly
against the files: both specs carry the term (see the Status line and
"Ownership flag, resolved" above) as of that round.

**What that same `claims` pass found instead, and what genuinely
remains:** four real defects in that round's own prose and one
regression in already-merged code, all named and fixed in place (the
`nwcheck.c`/`blob.h` overclaim about a re-check that doesn't exist as
code, this section's own staleness, a `make checkbrief` contradiction
the `plan.als` edit caused by changing the text CLAUDE.md's invariant-3
annotation pins, and a stray use of the word "budget" in `pid1.c`'s own
comment — added during the earlier edges-landing round, unrelated to
this one, but tripping invariant 1's `absent-in` check — reworded rather
than left). Fixing prose after a `claims` pass re-owes review per
`tools/review-gate.sh`'s own content-keying; the actual next step is
running `make test` clean against this final content, a fresh review
round on what changed since the last recording, and landing. Nothing
about the technical design is left open by this note.
