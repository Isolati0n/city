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

   NOT RUN. There is no alloy on this machine and nothing in the Makefile
   or tests/run.py executes this file, so this correction is reasoning
   about Alloy's semantics, not a checked result. It matches what nw-check
   and the baker measurably do, which is the agreement that matters. */
fun bindNeed[]: Int { #binds }

pred sealed { fdNeed[] <= 1024 and bindNeed[] <= 128 }

/* NOT a unit limit. `for 8 House` is Alloy's search scope -- how large a
   model it will look for a counterexample in -- and it is 8 against
   NW_MAX_UNITS = 64 in blob.h, MAX_UNITS in bakery/nw-cc.py and MaxUnits in
   Plan.tla. This file declares no upper bound on #House at all, so there is
   nothing here for those three to drift against; what would drift is a
   reader taking 8 for the limit. Raising the scope costs solver time and
   proves nothing extra about a bound that is not stated.

   Recorded 2026-09-11 after `drift` reported the 8-versus-64 row as a
   mismatch. It is a real question and the answer is that the cell is empty,
   not that the numbers disagree -- which is worth writing down here, because
   the next reader will ask it again.

   Also empty, for the same kind of reason: this file has no notion of a unit
   *name*. Name uniqueness is enforced in nwcheck.c and in the baker, and is
   not modelled here. */
run sealed for 8 House
