/* Plan lid. Offline. Not in the TCB.
   Assertions the baker and nw-check must agree on. */

sig House {
  budget: one Int,
  lids: set Lid
}

abstract sig Lid {}
one sig Seccomp, Landlock, NewNS, NewNet extends Lid {}

fact namesAreHouses { #House >= 1 }

/* Derived budget: 8 reserved + 2 per house. One constant. */
fun fdNeed[]: Int { 8 + 2.mul[#House] }

pred sealed { fdNeed[] <= 1024 }

run sealed for 8 House
