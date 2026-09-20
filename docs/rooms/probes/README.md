# Probe sources for `NW-ROOM-SPEC.md`

**Nothing in this tree builds or runs any of these.** They are not in the
`Makefile`, not in `tests/run.py`, and not in `make test`. A directory of
C files that nothing compiles reads as live code, which is why this file
exists rather than being left to be inferred.

They were run by the operator in the QEMU guest the spec's §12 describes
— Ubuntu `6.8.0-139-generic`, TCG, no KVM, unprivileged unless the spec
says otherwise — and their results are in the spec. The spec is where a
reader should look for what any of them established; this directory is
the source behind those claims, committed so a result can be re-run
rather than believed.

Committed verbatim as relayed, alongside the spec, on the `rooms` branch.
Neither has been executed on the machine this repository's suite runs on.
