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
  binds: set Path
}

/* A brick is the house's own root: its own libraries and toolchain, at the
   same paths, invisible to every other house. Content-addressed, so two
   houses may legitimately share one -- brick is lone, not disj. */
sig Brick {}
sig Path {}

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

/* Landlock grants beneath the house's root, which is a restriction only when
   that root is a brick (NW_E_LLBRICK). */
fact landlockNeedsBrick { all h: House | Landlock in h.lids => some h.brick }

fact namesAreHouses { #House >= 1 }

/* THE THREE FACTS ABOVE ARE NOT CHECKED, and nothing here could check
   them: a fact constrains which instances exist, so asserting one back
   is a tautology. `control` INVERTED brickNeedsNewNS into the plan
   nwcheck.c rejects -- `some h.brick => NewNS not in h.lids` -- and
   every check in this file stayed green while the run was still SAT,
   because no check mentions bricks.

   They are enforced in nwcheck.c (NW_E_BRICKNS, NW_E_BINDIDX,
   NW_E_LLBRICK) and pinned by test_checker_rejects_crafted_fields,
   which crafts a blob the baker would never emit and asserts the
   reason string. What they do here is shape the instances the two
   checks below run against, which is worth having and is not the same
   as being verified. Plan.tla carries the equivalent note; this file
   did not until `claims` asked why. */

/* Derived budget: reserved + 2 per house, both from blob.h. */
fun fdNeed[]: Int { plus[nwReserved[], 2.mul[#House]] }

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
  fdNeed[] = plus[nwReserved[], plus[#House, #House]]
}
assert Sealed {
  sealed
}

check FdArithmetic for 8 but 12 Int
check Sealed for 8 but 12 Int

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
   above is hand-written three times and tracks nothing (no bound on
   #House is declared here). Worse, `fdNeed`'s `2` tracks blob.h and is
   pinned by NOTHING: `claims` changed NW_MAX_UNITS * 2 to * 3 in the
   header and this file still checked clean, because the generator
   emits limit values, not arithmetic. Alloy's signed 12-bit Int spans -2048..2047, so it
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
