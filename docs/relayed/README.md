# Relayed documents — none of these is in the tree, scheduled, or decided

Written elsewhere and committed here **unchanged**, byte-identical to
what was relayed, the same handling `docs/NW-EXPECTATIONS-UNANCHORED.md`
got. Editing one to match this tree would destroy the record of what was
written before the thing it describes, which is the only part that does
not survive implementation.

**Each one labels itself on its third line** — read that line before
reading anything else in it. They range from measured records of boots
that happened on a machine this repository has no access to, through
ground-up designs explicitly not proposed against `main`, to a recorded
dead end. `CLAUDE.md`'s rule is to label such writing at the top rather
than as a caveat at the end, and they already do; this file exists for
what the labels do **not** carry.

## What the labels do not carry

- **`NW-SOLVENT.md` rests on `sched_ext`, which no kernel here has.** It
  names it repeatedly and never says it is unavailable, so the document
  reads as a proposal and was relayed as a recorded dead end. Checked
  both kernels this project has seen: `/sys/kernel/sched_ext` does not
  exist on the container's `6.18.44-fc-v37`, and
  `CONFIG_SCHED_CLASS_EXT` is absent from `/boot/config-6.8.0-139-generic`
  altogether — not `is not set`, not present at all. Nothing in either
  can run what it describes.

- **`NW-UNCONSTRAINED-ADDENDUM-7.md` reverses an earlier conclusion**
  rather than extending it. It is the first pass on that file by anyone
  holding a clone and a booting system, and it restates M1 and corrects
  two capability claims. Read it as a correction to its predecessor, not
  as a continuation.

## What nothing here establishes

Every measurement in these documents was taken on the operator's QEMU
guest. None has been reproduced on the machine this repository's suite
runs on, and nothing in `make test` reads, builds, or checks any of
them. Where one of them names a file, a constant, or a `grep`, that name
was true of some tree at some time and this directory makes no claim
about which.
