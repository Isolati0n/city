------------------------------ MODULE Plan ------------------------------
EXTENDS Integers, Sequences, FiniteSets

CONSTANTS MaxUnits, MaxEdges, MaxFds, Reserved

ASSUME MaxUnits = 64 /\ MaxEdges = 128 /\ MaxFds = 1024 /\ Reserved = 8

Houses == 1..N
VARIABLES n, e, crit, lids
N == n

FdNeed == Reserved + 2 * n + 2 * e

TypeOK ==
  /\ n \in 1..MaxUnits
  /\ e \in 0..MaxEdges
  /\ crit \in [1..n -> {0,1}]
  /\ FdNeed <= MaxFds

NoLiveRewrite ==
  (* Next plan is a new slot. The live city does not grow verbs. *)
  UNCHANGED <<n, e, crit, lids>>

HaltOnElectricianDeath == TRUE
=============================================================================
