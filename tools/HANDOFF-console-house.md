# Handoff — console house, blocked

Base: `main` at `92fafc0`, which carries the Landlock non-directory-bind
fix this exploration found and the corrected `docs/options/10-console-house.md`.
Read that file first — it has the full story, including what was measured
and what wasn't. This note is only the pointer.

## What's here

- `houses/console-wrap.c` — the fd-rewiring wrapper. Validated standalone
  against a real pty: rewires fd 0/1/2 onto a bound tty via `close()` +
  `open()` (no `dup2`, no `setsid`, no `ioctl`), execs busybox `sh`, which
  runs interactively and reports "can't access tty; job control turned
  off" exactly as the design predicted.
- `tools/console-boot-test.py` — the QEMU-based positive/negative-control
  test the design note specifies. Builds the wrapper, assembles a brick
  with `bakery/mkbrick.py`, bakes a plan, boots it, sends a token over a
  dedicated `ttyS1`, and reads it back; the control reruns the same plan
  minus `bind=/dev/ttyS1` and requires silence.
- `tools/mkboot.sh`'s `NW_CITY`/`NW_EXTRA_BRICKS` env-var hooks — additive,
  no-op unless set, and what `console-boot-test.py` uses to get mkboot.sh
  to build images for a plan other than the fixed four-probe-house
  default. `--run`/`--check`'s own qemu invocation is untouched.

## Why it's not on main

The console house does not boot yet. `docs/options/10-console-house.md`'s
"What actually happened when this was built" section has the details: an
interactive `busybox sh` over a real tty needs nine syscalls
(`prctl, getuid, rt_sigaction, getppid, uname, ioctl, geteuid, getpgrp,
poll, setpgid`), not the one (`prctl`) the design anticipated, and one of
them — `ioctl` — is exactly what the design said was being avoided,
because the seccomp filter has no per-argument inspection and granting it
means granting the whole `ioctl(2)` surface to every seccomp house. That
is a bigger decision than this exploration gets to make on its own.

## What the operator needs to decide

Whether the full nine-syscall widening (`ioctl` included) is an
acceptable cost for this feature, or whether the wrapper should be
redesigned to avoid needing an interactive `ash` at all — trading away
some of "a normal shell" to keep the syscall surface where it is. Either
answer is buildable from here; neither is decided by this branch.

## To reproduce the failure (or a fix)

```
apt-get install busybox-static   # /bin/busybox, static
sudo python3 tools/console-boot-test.py
```
Currently fails with `FAIL: token not echoed back over ttyS1` because
busybox is SIGSYS-killed on the first syscall past `prctl`. Add the
remaining eight to `lids.c`'s `strict_allow[]` (each with the same
per-syscall justification `runtime.md` requires) to see it pass.
