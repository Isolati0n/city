# Handoff: scale, to whoever has the hardware

Base: `ae19cbc` on `origin/main`. Everything below was measured on the
machine named in each block, with `tools/scale-probe.py`. It is the
input to an in-suite scale test, which **does not exist**.

## Read this first: what is done and what is not

**Done — measured, by hand.** `tools/scale-probe.py` is a ladder you run
yourself. `grep scale-probe Makefile` returns nothing; it is not in
`make test`, and it never has been.

**Not done — the test.** Nothing in `make test` runs above
`NW_MAX_UNITS`. The suite's contribution at the top end is
`test_non_provision_at_max`, which asserts exactly-once at
`NW_MAX_UNITS` — 64 today, which is not scale.

That distinction is the whole reason this file exists. **A ladder you run
by hand is evidence about one afternoon; a test in `make test` is
evidence about every change.** `.claude/rules/harness.md` has called a
large-N boot "the most valuable thing you could write" since before any
of this landed, and it is still true. This file hands you the
measurement, not the gap.

## The result

Machine: `ulimit -n` 20000, `pid_max` 32768, 4 CPUs.

- **Clean at 8192 units.** Every unit ran, reported exactly once, held no
  ungranted descriptor, was reaped.
- **Breaks at 10240**, loudly and by name:

```
  FAIL n=10240 phase=boot     open=Nones all-ran=0.15s fds=20488 dupslots=16384 reported=None interleaved=None
       why: city did not open with 10240 houses; last: [nw-root] HALT: log pipe
```

  PID 1 holds two log pipes per house, so `2·10240 + 8 = 20488`
  descriptors is past `ulimit -n` 20000. **Predicted break:
  `n > (20000 − 8) / 2 ≈ 9996`**, and the measurement agrees.

  Controlled by moving the limit: at `ulimit -n 2000` the break moves to
  n = 1024 with the same message, and 512 still passes. So the model is
  the descriptor budget, not a coincidence at one size.

- **Boot cost is quadratic.** `close_others` reads `/proc/self/fd` in
  each spawned house and the spawner inherits PID 1's ~2n log pipes, so
  the sweep is n × 2n. Dividing time-to-all-reported by 2n² gives a
  roughly constant µs-per-swept-descriptor across 1024/2048/4096.

## Do not quote an absolute timing from this repository

A table of six rung timings was written from a single pass and **none of
the six reproduced** when re-run three times per rung on the same machine
a day later. The *shape* held; the constants did not. Run-to-run
variation here approaches a factor of two, and within a single rung the
spread reached about 1.5x.

So: the quadratic shape is the durable finding. Every absolute number in
this file other than the break point and the descriptor arithmetic should
be re-measured on your hardware, with a repetition count, before it is
quoted. A single sample of a timing is not a measurement.

## What the ladder does NOT exercise

Every city it bakes is `kind=oneshot lids=none`, no brick, no bind. So
the numbers say nothing about per-unit loop devices, namespaces, bricks
or seccomp filters at scale — and **bricks phase 2 adds a loop device per
house**, which is a per-unit resource nothing has exercised near a limit.
That was the original reason scale was put ahead of phase 2, and the
ladder still does not cover it.

## Two traps that are already paid for

- **`orphans=0` does not mean what the probe assumes.** The probe treats
  any orphan as a run failure, and `closed … orphans=N` counts orphans
  *reaped*, not orphans that *existed*. Every ladder run to date is
  unaffected in its result — the probe bakes only `unit-probe`, and
  `grep -c fork unit_probe.c` is 0, so no ladder city has ever created an
  orphan — but the check has never once distinguished the two meanings,
  because nothing it runs can produce one. **The moment you point the
  ladder at a forking house, which phase 2 is, that check is unvalidated.**
  See the known-open in `.claude/rules/runtime.md`.
- **Time the city, not your own hold.** The first version of the probe
  passed `--hold-ms 40n` and reported wall time, so n = 1024 showed a
  41 s hold as a 44 s boot — a tidy straight line that was entirely the
  harness measuring itself.

## Where the rest of it is

- `tools/scale-probe.py` — the tool, with its own findings in comments.
- `.claude/rules/harness.md`, "Scale" — the traps, at length.
- `HISTORY.md` §40 — the round it was measured in; §44 and §47 for the
  corrections to what was written about it afterwards.
