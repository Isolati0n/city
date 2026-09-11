------------------------------ MODULE Plan ------------------------------
EXTENDS Integers, Sequences, FiniteSets

CONSTANTS MaxUnits, MaxFds, Reserved, MaxBinds

ASSUME MaxUnits = 64 /\ MaxFds = 1024 /\ Reserved = 8 /\ MaxBinds = 128

Houses == 1..N
VARIABLES n, kind, lids, brick, binds
N == n

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
