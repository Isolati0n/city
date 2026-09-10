------------------------------ MODULE Plan ------------------------------
EXTENDS Integers, Sequences, FiniteSets

CONSTANTS MaxUnits, MaxFds, Reserved

ASSUME MaxUnits = 64 /\ MaxFds = 1024 /\ Reserved = 8

Houses == 1..N
VARIABLES n, kind, lids
N == n

FdNeed == Reserved + 2 * n

TypeOK ==
  /\ n \in 1..MaxUnits
  /\ kind \in [1..n -> {0, 1}]   (* 0 oneshot, 1 longrun; explicit, no default *)
  /\ FdNeed <= MaxFds

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
