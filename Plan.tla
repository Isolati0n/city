------------------------------ MODULE Plan ------------------------------
EXTENDS Integers, Sequences, FiniteSets

CONSTANTS MaxUnits, MaxFds, Reserved, MaxBinds

(* The constants arrive from specs/Plan.cfg, generated out of blob.h by
   tools/gen-spec-limits.py. This ASSUME used to pin them to literals --
   `MaxUnits = 64 /\ MaxFds = 1024 /\ ...` -- which was the second copy
   invariant 3 is about, sitting in the file that exists to state the
   limits. With the values now derived, pinning them here would make
   every check below a tautology: a blob.h change fails the ASSUME and
   the invariants are never reached, so nothing about the RELATIONSHIP
   between the limits is ever evaluated. Measured 2026-09-11: lowering
   NW_MAX_FDS to 100 failed the old ASSUME, not FdBudgetCovers, which is
   the wrong error for the right problem.

   So: sanity only. The relationships are checked as invariants, where a
   counterexample names which one broke. *)
ASSUME /\ MaxUnits \in Nat \ {0}
       /\ MaxFds \in Nat \ {0}
       /\ Reserved \in Nat
       /\ MaxBinds \in Nat

VARIABLES n, kind, lids, brick, binds
N == n

(* `Houses == 1..N` stood above the declaration of N until 2026-09-11.
   TLA+ requires definition before use, so this module did not parse:
   "Unknown operator: `N'". It was also referenced by nothing. Both facts
   were invisible because no tool had ever read this file -- the same
   shape as plan.als, which Alloy refused for a missing scope. Two specs,
   neither parseable by its own checker, both reading as verification.
   Deleted rather than reordered: a definition with no use is the thing
   that rots. *)

LidNewNS == 4
LidLandlock == 2

FdNeed == Reserved + 2 * n

(* Same limit as NW_MAX_BINDS in blob.h, MAX_BINDS in bakery/nw-cc.py and
   bindNeed in plan.als. Change one, change all four.

   This was Cardinality(UNION {binds[i] : i \in 1..n}) until 2026-09-11 --
   the number of DISTINCT paths, unioned across houses. The blob counts
   rows, not paths: struct nw_bind is a (unit, path) pair and the baker
   emits one per house per declared bind, so two houses sharing /shared is
   one path and two rows. The spec admitted plans nw-check refuses, by a
   factor that grows with sharing. Summing per-house cardinalities is what
   the implementations do. Found by `drift`.

   NOT RUN. There is no tlc and no tla2tools jar on this machine, and
   nothing in the Makefile or tests/run.py executes this file. Plan.tla
   still has no next-state relation, so what stands here is a type
   predicate no behaviour is checked against. Treat this as a corrected
   statement of the format, not as a verified one. *)
BindNeed == IF n = 0 THEN 0
            ELSE LET Rows[i \in 1..n] ==
                     IF i = 1 THEN Cardinality(binds[i])
                     ELSE Cardinality(binds[i]) + Rows[i - 1]
                 IN Rows[n]

(* brick[i] = "" means the house shares the machine root. binds[i] is the
   set of paths bound into that house's brick before it pivots. *)
TypeOK ==
  /\ n \in 1..MaxUnits
  /\ kind \in [1..n -> {0, 1}]   (* 0 oneshot, 1 longrun; explicit, no default *)
  /\ FdNeed <= MaxFds
  /\ BindNeed <= MaxBinds

(* A brick is a root; pivoting into one needs a private mount namespace, or
   the pivot repoints the machine's root. nwcheck.c returns NW_E_BRICKNS. *)
BrickNeedsNewNS ==
  \A i \in 1..n : brick[i] # "" => LidNewNS \in lids[i]

(* Landlock grants beneath the house's root; only a restriction when that
   root is a brick. nwcheck.c returns NW_E_LLBRICK. *)
LandlockNeedsBrick ==
  \A i \in 1..n : LidLandlock \in lids[i] => brick[i] # ""

(* No root, nothing to bind into. nwcheck.c returns NW_E_BINDIDX. *)
BindsNeedBrick ==
  \A i \in 1..n : binds[i] # {} => brick[i] # ""

(* ---------------------------------------------------------------------
   WHAT TLC ACTUALLY CHECKS, added 2026-09-11 when the jar landed.

   This file had VARIABLES and no Init and no Next, so there was nothing
   for TLC to explore: every definition above was a definition nothing
   evaluated. The model below is deliberately static -- Next is UNCHANGED
   -- because the plan format has no runtime mutation path (HISTORY.md
   section 17). It exists so the arithmetic gets EVALUATED at every legal
   unit count rather than read.

   The constants come from specs/Plan.cfg, generated out of blob.h by
   tools/gen-spec-limits.py, so the ASSUME above is a live comparison
   against the header rather than a remembered copy. Change NW_MAX_UNITS
   in blob.h alone and the ASSUME fails here -- which is invariant 3
   enforced instead of recalled.

   FdBudgetCovers is the same claim as the _Static_assert in blob.h, said
   in a second place and checked by a different tool: the descriptor
   budget must cover the largest legal city. It is the predicate that
   fails if someone raises NW_MAX_UNITS without raising NW_MAX_FDS.

   The other definitions above (BrickNeedsNewNS, LandlockNeedsBrick,
   BindsNeedBrick) are NOT state-checked: they are acceptance rules that
   a generated plan would satisfy by construction, so checking them here
   would be circular. They are enforced in nwcheck.c and pinned by
   test_checker_rejects_crafted_fields. Stated so nobody reads this
   model as covering more than it does. *)

Init ==
  /\ n \in 1..MaxUnits
  /\ kind = [i \in 1..n |-> 0]
  /\ lids = [i \in 1..n |-> {}]
  /\ brick = [i \in 1..n |-> ""]
  /\ binds = [i \in 1..n |-> {}]

Next == UNCHANGED <<n, kind, lids, brick, binds>>

FdBudgetCovers == FdNeed <= MaxFds

(* NoLiveRewrite is deliberately NOT restated as a predicate here.
   With edges removed, e was the only variable with a plausible runtime
   mutation path -- nothing can add a unit or change a lid while the city
   runs. UNCHANGED <<n, kind, lids>> would be trivially true and evaluated
   by nothing, which reads as assurance without being any. The slot
   discipline it stood for survives as an operational rule in CLAUDE.md.
   See HISTORY.md section 17.

   HaltOnElectricianDeath is removed with the electrician: nw-spawn exits
   as its success path, so there is no death to halt on. *)
=============================================================================
