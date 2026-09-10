/* Plan lid. Offline. Not in the TCB.
   Assertions the baker and nw-check must agree on. */

sig House {
  kind: one Kind,
  budget: one Int,
  lids: set Lid,
  brick: lone Brick,
  profile: one Profile,
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

/* Which seccomp allow-list the house wears. Build is Strict plus what a
   toolchain needs; it is a superset in lids.c so the two cannot drift. */
abstract sig Profile {}
one sig Strict, Build extends Profile {}

/* Pivoting into a brick without a private mount namespace would repoint the
   machine's root, so nw-check rejects it (NW_E_BRICKNS) and the baker
   refuses to add the lid on the plan's behalf. */
fact brickNeedsNewNS { all h: House | some h.brick => NewNS in h.lids }

/* A bind is a path made visible inside a root. Without a brick there is no
   root to bind into (NW_E_BINDIDX). */
fact bindsNeedBrick { all h: House | some h.binds => some h.brick }

/* A profile without the lid that applies it is a filter nobody wears
   (NW_E_PROFILE). */
fact buildNeedsSeccomp { all h: House | h.profile = Build => Seccomp in h.lids }

fact namesAreHouses { #House >= 1 }

/* Derived budget: 8 reserved + 2 per house. One constant. */
fun fdNeed[]: Int { 8 + 2.mul[#House] }

/* Same arithmetic as NW_MAX_BINDS in blob.h, MAX_BINDS in bakery/nw-cc.py
   and MaxBinds in Plan.tla. Change one, change all four. */
fun bindNeed[]: Int { #(House.binds) }

pred sealed { fdNeed[] <= 1024 and bindNeed[] <= 128 }

run sealed for 8 House
