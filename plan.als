/* Plan lid. Offline. Not in the TCB.
   Assertions the baker and nw-check must agree on. */

sig House {
  critical: one Int,
  budget: one Int,
  lids: set Lid
}

abstract sig Lid {}
one sig Seccomp, Landlock, NewNS, NewNet extends Lid {}

sig Wire {
  a: one House,
  b: one House
}

fact noSelfWire { all w: Wire | w.a != w.b }
fact undirectedUnique {
  all disj w, v: Wire |
    not (w.a = v.a and w.b = v.b) and
    not (w.a = v.b and w.b = v.a)
}
fact namesAreHouses { #House >= 1 }
fact critical01 { all h: House | h.critical = 0 or h.critical = 1 }

/* Derived budget: 8 reserved + 2 per house + 2 per wire. One constant. */
fun fdNeed[]: Int { 8 + 2.mul[#House] + 2.mul[#Wire] }

pred sealed { fdNeed[] <= 1024 }

run sealed for 8 House, 16 Wire
