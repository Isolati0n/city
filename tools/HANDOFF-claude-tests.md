# Handoff: tests/run.py (Claude)

Base: 20bad4d. These need your file. The production mechanism is
already gone.

## dawn-real-boot

dawn no longer forwards `NW_HOLD_MS`. The kernel command line is
how that variable reached production. `boot()` already execs
`nw-root --hold-ms N` directly and is fine.

`test_dawn_real_boot` still does `env NW_HOLD_MS=800 .../nw-dawn`.
That process will now stay in `poll(-1)` and the test will hang
or fail `city_closed`.

Proposed shape:

- Drop `NW_HOLD_MS=800` from the `env` list.
- After `city open` is visible, SIGTERM the unshare child (PID 1
  of the nested pid ns). `shutdown_city` is the production close
  path; that is what this test should be watching, not a timer
  smuggled through the mount stage.
- Keep the assertions that dawn mounted and pivoted. Pair
  `city_closed` with a line that proves the TERM was delivered
  (`shutdown TERM houses`), so a hang-and-timeout cannot pass
  as a close.

Do not put the timer back on dawn's argv or env. mkboot --check
fails on `[nw-root] closed` on an unattended boot.

## budget-hard-total

The current test is green against a 30-second window. It pins
"no reset inside a 7s hold" given the fixture's 1.2s deaths,
not "budget is a hard total".

A pin that means the name:

- deaths spaced further apart than any window the removed
  `window_s` field could have held, or
- a control that restores a sliding window in nw-sup and
  watches this test go red.

That control is slow. If the cost is not worth it, say so in
the test's docstring rather than keeping the current name.
The restart loop is in nw-sup; the test file is yours.
