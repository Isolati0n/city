/* Plan lid. Offline. Not in the TCB.
   Assertions the baker and nw-check must agree on.

   THIS FILE IS RUN NOW. `tests/run.py` executes it with the Alloy jar in
   tools/jars and fails if a check finds a counterexample. Until
   2026-09-11 nothing ran it, and two things were wrong that only running
   could find:

   1. It did not execute AT ALL. `run sealed for 8 House` gives a scope
      for House and none for Brick or Path, and Alloy refuses:
      "You must specify a scope for sig this/Brick". Every scope is set
      below. A spec that has never been parsed by its own tool is prose.

   2. `fdNeed` did not add. It was `8 + 2.mul[#House]`, and in Alloy `+`
      on Int is SET UNION, not addition -- so it was the set {8, 2*#House}
      and `sealed` compared that against 1024. Measured: with 5 houses,
      `fdNeed[] = 18` has a counterexample and `fdNeed[] = (8 + 10)`
      holds, which is the union, exactly. The fd formula is one of the
      four places invariant 3 says must agree, and it had never computed
      the fd budget. Arithmetic goes through plus/mul/lte from
      util/integer.

   The limits come from specs/limits.als, generated out of blob.h by
   tools/gen-spec-limits.py. They are not written here, so they cannot
   drift from it -- the second copy is gone rather than checked. */
open util/integer
open limits

sig House {
  kind: one Kind,
  budget: one Int,
  lids: set Lid,
  brick: lone Brick,
  layer: lone Layer,
  binds: set Path,
  capacity: lone Capacity
}

/* A brick is the house's own root: its own libraries and toolchain, at the
   same paths, invisible to every other house. Content-addressed, so two
   houses may legitimately share one -- brick is lone, not disj.

   Phase 3 (2026-09-12) changed brick[96] to a 32-byte sha256 in blob.h and
   this sig needed NOTHING, because it was already an opaque atom with no
   structure: identity and sharing are all it ever modelled, and both
   survived the change. docs/plans/01 predicted "plan.als and Plan.tla gain
   a Hash in place of a brick path" -- neither file had ever modelled a
   path, so the four-place drift of invariant 3 did not reach them here.
   Recorded because a prediction of work that turned out to be unnecessary
   reads, later, like work that was skipped. */
sig Brick {}

/* A writable layer. Opaque, like Brick, and for the same reason: what is
   modelled is identity and pairing, not structure. Unlike a brick it is
   NOT shared -- two houses with one layer would write into each other's
   data -- but nothing here says so, because `lone` does not express
   disjointness and the checker does not enforce it either. Recorded as a
   gap rather than modelled as a property nobody checks. */
sig Layer {}
sig Path {}

/* docs/options/17-edges.md. Opaque, like Brick and Layer, and for the
   same narrower reason: what this file's fd arithmetic needs is a
   COUNT, and Alloy has no bare integer count to add a term to --
   `#Edge` is what makes one exist. No `a`/`b` endpoint fields: the
   structural rules an edge is subject to (index range, no self-edge,
   no duplicate pair) are enforced in nwcheck.c and pinned by
   test_checker_rejects_crafted_edges, not modelled here, the same
   division of labour brickNeedsNewNS's neighbours already use for
   their own NW_E_* checks. */
sig Edge {}

/* A declared capacity for the writable layer, opaque like Brick and Layer
   and for the narrower reason: the only thing the plan format asserts
   about the number is that declaring one requires a layer to bound
   (NW_E_CAPNOLAYER), and that is a question about PRESENCE, not about the
   value. Of the rest of struct nw_res, three fields are range checks
   against the kernel's own bounds -- arithmetic against constants this
   file does not carry and cannot check -- two have an ordering rule and
   no range, and three are unchecked because every 64-bit value of a
   mask, a byte count or a rate is a legal declaration. All of it is
   pinned by test_checker_rejects_crafted_resources, in both directions.
   (This said "the other eight fields are range checks" and named
   test_checker_rejects_crafted_fields, which crafts nothing in the
   block. `claims`.)

   So `capacity` is the resource block's ONE structural rule and the rest
   of the block is deliberately unmodelled. Written down because a sig
   named for a block while modelling one of its fields reads, later, as
   the block being covered. */
sig Capacity {}

/* Explicit in the plan: no default, no inference. */
abstract sig Kind {}
one sig Oneshot, Longrun extends Kind {}

abstract sig Lid {}
one sig Seccomp, Landlock, NewNS, NewNet extends Lid {}

/* There is one seccomp filter and a house does not choose it. A Profile sig
   existed on 2026-09-10 and went with NW_PROF_BUILD the same day --
   HISTORY.md section 23. */

/* Pivoting into a brick without a private mount namespace would repoint the
   machine's root, so nw-check rejects it (NW_E_BRICKNS) and the baker
   refuses to add the lid on the plan's behalf. */
fact brickNeedsNewNS { all h: House | some h.brick => NewNS in h.lids }

/* A bind is a path made visible inside a root. Without a brick there is no
   root to bind into (NW_E_BINDIDX). */
fact bindsNeedBrick { all h: House | some h.binds => some h.brick }

/* A brick and a layer come together or not at all: the brick is what a
   house can see, the layer is what it can keep, and one without the other
   is a house whose writes vanish or an area nothing mounts. nwcheck.c
   returns NW_E_LAYERPAIR for either direction. Like the facts
   above this SHAPES instances rather than being checked -- see the note
   below -- and its enforcement is nwcheck.c plus
   test_checker_rejects_crafted_fields. */
fact layerPairsWithBrick { all h: House | some h.layer <=> some h.brick }

/* Landlock grants beneath the house's root, which is a restriction only when
   that root is a brick (NW_E_LLBRICK). */
fact landlockNeedsBrick { all h: House | Landlock in h.lids => some h.brick }

/* A capacity bounds the writable layer, so declaring one without a layer
   names nothing. nwcheck.c returns NW_E_CAPNOLAYER. Like the facts
   above this SHAPES instances rather than being checked -- see the note
   below. */
fact capacityNeedsLayer { all h: House | some h.capacity => some h.layer }

fact namesAreHouses { #House >= 1 }

/* THE FACTS ABOVE ARE NOT CHECKED, and nothing here could check
   them: a fact constrains which instances exist, so asserting one back
   is a tautology. `control` INVERTED brickNeedsNewNS into the plan
   nwcheck.c rejects -- `some h.brick => NewNS not in h.lids` -- and
   every check in this file stayed green while the run was still SAT,
   because no check mentions bricks.

   They are enforced in nwcheck.c (NW_E_BRICKNS, NW_E_BINDIDX,
   NW_E_LLBRICK) and pinned by test_checker_rejects_crafted_fields --
   plus NW_E_CAPNOLAYER, whose pin is
   test_checker_rejects_crafted_resources,
   which crafts a blob the baker would never emit and asserts the
   reason string. What they do here is shape the instances the two
   checks below run against, which is worth having and is not the same
   as being verified. Plan.tla carries the equivalent note; this file
   did not until `claims` asked why. */

/* Derived budget: reserved + 1 per house + 2 per edge, all from blob.h,
   matching NW_BOOT_NEED in blob.h and nwcheck.c's own re-check. Was
   reserved + 2 per house + 2 per edge (NW_FD_NEED) until
   docs/OPERATOR-BRIEF.md Section 1.3 replaced the pre-interleave *2
   house coefficient with the *1 the log-pipe interleave has actually
   produced since 2026-09-14. The edge term returned with
   docs/options/17-edges.md; it left with edges the first time
   (HISTORY.md section 17) and the four/five-place drift class applies
   to it exactly as it does to the house term beside it. */
fun fdNeed[]: Int { plus[nwReserved[], plus[#House, 2.mul[#Edge]]] }

/* Same arithmetic as NW_MAX_BINDS in blob.h, MAX_BINDS in bakery/nw-cc.py
   and MaxBinds in Plan.tla. Change one, change all four.

   This said `#(House.binds)` until 2026-09-11, which is the number of
   DISTINCT paths bound by any house. The blob does not store distinct
   paths: struct nw_bind is a (unit, path) PAIR, and the baker builds one
   row per house per declared bind. Two houses sharing /shared is one path
   and two rows -- so the spec permitted plans the implementation refuses,
   by a factor that grows with sharing, and sharing a bind is the normal
   case rather than a corner. `drift` found it with a two-house city that
   nw-check reports as binds=2 while this expression counted 1.

   The comment above sat directly over the wrong expression, which made it
   the most credible-looking thing in the file.

   `binds` is declared `binds: set Path` on House, so as a relation it is
   House -> Path and `#binds` is the number of (house, path) tuples, which
   is one per row of the blob's bind table. `#(House.binds)` was the number
   of distinct Path atoms any house reaches.

   RUN as of 2026-09-11: the alloy jar is in tools/jars and tests/run.py
   executes this file. `#binds` needed no change -- cardinality of a
   relation is not arithmetic, so it never had the `+` defect fdNeed
   had. */
fun bindNeed[]: Int { #binds }

/* lte, not <=, for the same reason plus is not +: these are Int
   comparisons and must go through util/integer. The limits are blob.h's,
   via the generated module. */
pred sealed { lte[fdNeed[], nwMaxFds[]] and lte[bindNeed[], nwMaxBinds[]] }

/* THE CHECKS THE BUILD RUNS. Each is an agreement between this file and
   the implementation, and each fails loudly if the two diverge.

   FdArithmetic is the one that would have caught the `+` defect: it says
   the formula equals reserved + 2 per house, computed a different way.
   Sealed says every plan this file admits fits blob.h's budgets, so
   lowering NW_MAX_FDS below what the scope needs fails here rather than
   at boot. Both are BOUNDED to the scope on the command -- see the note
   at the foot of this file about what the scope is and is not. */
assert FdArithmetic {
  fdNeed[] = plus[nwReserved[], plus[#House, plus[#Edge, #Edge]]]
}
assert Sealed {
  sealed
}

/* fdNeedOracle_1_7[] and fdNeedOracle_7_1[] are GENERATED --
   tools/gen-spec-limits.py compiles tools/bootneed-oracle.c against
   blob.h's own NW_BOOT_NEED macro (was NW_FD_NEED, in
   tools/fdneed-oracle.c; both renamed together,
   docs/OPERATOR-BRIEF.md Section 1.3) and runs it at these two points --
   not a further hand-typed copy of the coefficients. FdArithmetic above pins
   fdNeed[]'s formula against a SECOND copy written in THIS SAME FILE,
   which catches an accidental typo but not a multiplier that drifted
   from blob.h consistently in both copies: plan.md records the
   measurement (`* 2` -> `* 3` in blob.h, both specs stayed clean). This
   is the comparator that was missing.

   Two points, not one, and each with only one of #House/#Edge large:
   a single sample where both are large cannot tell "unit coefficient
   wrong" from "edge coefficient wrong" apart, and a single sample
   where only one is nonzero cannot catch the two coefficients being
   swapped with each other. Swapping which count is 7 and which is 1
   between the two samples catches that swap specifically. */
assert FdNeedOracleAgrees {
  (#House = 1 and #Edge = 7 => fdNeed[] = fdNeedOracle_1_7[])
  and
  (#House = 7 and #Edge = 1 => fdNeed[] = fdNeedOracle_7_1[])
}

check FdArithmetic for 8 but 12 Int
check Sealed for 8 but 12 Int
check FdNeedOracleAgrees for 8 but 12 Int

/* NOT a unit limit. `for 8` is Alloy's search scope -- how large a model
   it will look for a counterexample in -- and it is 8 against an
   NW_MAX_UNITS this file deliberately does not name. It quoted the value
   as 64 until 2026-09-11: a spec whose limits are generated should not
   carry a hand-copied number in its prose either, and that one goes
   stale the day the header moves. `claims`. This file declares no upper
   bound on #House at all, so there is nothing here to drift against;
   what would drift is a reader taking 8 for the limit. Raising the scope
   costs solver time and proves nothing extra about a bound that is not
   stated.

   `but 12 Int` IS a real limit, and the suite pins it: it must cover
   NW_MAX_FDS. Do not read that as "the last hand-written number" --
   two rounds tried to write that sentence and both were wrong. `for 8`
   above is hand-written on every check in this file and tracks nothing
   (no bound on #House is declared here) -- do not give it a count,
   for the reason the next paragraph demonstrates about this exact
   sentence. `fdNeed`'s `2`s used to track blob.h in name only and be
   pinned by NOTHING: `claims` changed NW_MAX_UNITS * 2 to * 3 in the
   header and this file still checked clean, because the generator
   emitted limit values, not arithmetic. **That is no longer true.**
   FdNeedOracleAgrees above compares fdNeed[] against
   fdNeedOracle_1_7[]/fdNeedOracle_7_1[], compiled from blob.h's own
   fd-need macro rather than retyped (NW_FD_NEED at the time; the same
   mechanism now runs against NW_BOOT_NEED). Both the ORIGINAL mutation
   (NW_MAX_UNITS's own `* 2` -> `* 3`, Plan.tla's FdNeed left
   unchanged) and the edge-coefficient equivalent were run against this
   mechanism: on Plan.tla's TLC side, where a scratch `* 3` for units
   alone reproduces cleanly, TLC reports `Invariant FdNeedOracleAgrees
   is violated by the initial state ... e=7, n=1` -- the oracle,
   recompiled from the mutated header, now disagrees with the spec's
   own unchanged formula at exactly the sample built to expose a
   unit-coefficient drift. Alloy's signed 12-bit Int spans -2048..2047, so it
   must cover NW_MAX_FDS; at 2048 the value wraps and the failure
   presents as a counterexample to Sealed plus a vacuous model -- the
   right problem under two wrong names. test_specs_are_checked asserts
   the bitwidth covers the generated limit, by name, before Alloy runs.

   Recorded 2026-09-11 after `drift` reported the 8-versus-64 row as a
   mismatch. It is a real question and the answer is that the cell is empty,
   not that the numbers disagree -- which is worth writing down here, because
   the next reader will ask it again.

   Also empty, for the same kind of reason: this file has no notion of a unit
   *name*. Name uniqueness is enforced in nwcheck.c and in the baker, and is
   not modelled here. */
run sealed for 8 but 12 Int
