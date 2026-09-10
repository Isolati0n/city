/* Plan lid. Offline. Not in the TCB.
   Assertions the baker and nw-check must agree on. */

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

/* Derived budget: 8 reserved + 2 per house. One constant. */
fun fdNeed[]: Int { 8 + 2.mul[#House] }

/* Same arithmetic as NW_MAX_BINDS in blob.h, MAX_BINDS in bakery/nw-cc.py
   and MaxBinds in Plan.tla. Change one, change all four. */
fun bindNeed[]: Int { #(House.binds) }

pred sealed { fdNeed[] <= 1024 and bindNeed[] <= 128 }

run sealed for 8 House
