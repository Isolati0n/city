------------------------------ MODULE Plan ------------------------------
EXTENDS Integers, Sequences, FiniteSets

CONSTANTS MaxUnits, MaxFds, Reserved, MaxBinds

ASSUME MaxUnits = 64 /\ MaxFds = 1024 /\ Reserved = 8 /\ MaxBinds = 128

Houses == 1..N
VARIABLES n, kind, lids, brick, profile, binds
N == n

LidNewNS == 4
LidSeccomp == 1
ProfStrict == 0
ProfBuild == 1

FdNeed == Reserved + 2 * n

(* Same limit as NW_MAX_BINDS in blob.h, MAX_BINDS in bakery/nw-cc.py and
   bindNeed in plan.als. Change one, change all four. *)
BindNeed == Cardinality(UNION {binds[i] : i \in 1..n})

(* brick[i] = "" means the house shares the machine root. binds[i] is the
   set of paths bound into that house's brick before it pivots. *)
TypeOK ==
  /\ n \in 1..MaxUnits
  /\ kind \in [1..n -> {0, 1}]   (* 0 oneshot, 1 longrun; explicit, no default *)
  /\ profile \in [1..n -> {ProfStrict, ProfBuild}]
  /\ FdNeed <= MaxFds
  /\ BindNeed <= MaxBinds

(* A brick is a root; pivoting into one needs a private mount namespace, or
   the pivot repoints the machine's root. nwcheck.c returns NW_E_BRICKNS. *)
BrickNeedsNewNS ==
  \A i \in 1..n : brick[i] # "" => LidNewNS \in lids[i]

(* No root, nothing to bind into. nwcheck.c returns NW_E_BINDIDX. *)
BindsNeedBrick ==
  \A i \in 1..n : binds[i] # {} => brick[i] # ""

(* A profile without the lid that applies it is a filter nobody wears.
   nwcheck.c returns NW_E_PROFILE. *)
BuildNeedsSeccomp ==
  \A i \in 1..n : profile[i] = ProfBuild => LidSeccomp \in lids[i]

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
