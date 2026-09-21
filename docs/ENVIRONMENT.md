# What this box cannot do

**Status: measurements, not rules.** Every entry says how it was measured
and where, because an environment claim is the shape `CLAUDE.md`'s
green-suite corollary exists to catch: a capability difference reads as a
code defect when the lab has less than the target, and as a pass when it
has more. Nothing here is a decision; re-measure anything you are about to
rely on.

**Measured on the container this file was written in**, `6.18.44-fc-v37`,
from `/home/user/city`. A different container may differ — the point of
writing the commands down is that you can re-run them rather than trust
the answers.

## Git

- **Deleting a remote ref is refused, and the refusal exits 0 when piped.**
  `git push origin :refs/heads/<name>` answers `error: RPC failed; HTTP 403
  curl 22 The requested URL returned error: 403` and exits 1. Ordinary
  pushes to the same remote succeed, so this is the delete verb and not
  connectivity. **The trap is the pipe**: run as
  `git push … 2>&1 | tail -3`, `$?` is `tail`'s and the 403 line is cut
  above the three lines kept, so the failure reads as success. `git
  ls-remote --heads origin` is the check — not the exit status.
  `CLAUDE.md`'s *the evidence is real and it is about something else*
  carries the episode.

- **No `gh`, no `hub`.** `command -v gh` and `command -v hub` both return
  nothing. GitHub work goes through the MCP tools, which expose branch
  creation and listing and **no deletion** — so a branch this session
  creates, it cannot remove.

## Kernel and filesystems

- **No FAT driver, while `mkfs.vfat` is installed.** `grep -c vfat
  /proc/filesystems` is 0; `command -v mkfs.vfat` is `/usr/sbin/mkfs.vfat`.
  That pair is `harness.md`'s *detect the capability, not a tool that
  implies it*, standing in the tree. It is why `dawn-real-boot` skips its
  `vfat-esp` half on every run here and substitutes ext4.

- **cgroup v2 offers only `hugetlb`.** `cat
  /sys/fs/cgroup/unified/cgroup.controllers` prints `hugetlb` and nothing
  else; `cpu`, `memory`, `cpuset`, `blkio` and the rest are mounted as v1
  hierarchies (`grep -c "^cgroup " /proc/mounts` counts them). So no test
  written against the resource block can enforce anything here — which is
  what `.claude/rules/runtime.md` says, and this is its measurement.

- **Project quota is recorded as unavailable and I did not reproduce it.**
  `runtime.md` states `quotactl` answers `ESRCH` on the root device. My own
  probe returned `EINVAL`, which means the probe was malformed rather than
  that the claim is wrong — recorded this way because a bad measurement
  reported as a contradiction is worse than no measurement.

- **Landlock is ABI 7**, which is a capability rather than a gap and is
  listed because the suite branches on it and names the branch in its `ok`
  line. `print_environment()` reports it every run; do not take it from
  here.

## The lab is not a machine

- **No bootloader boot, no real hardware.** `.claude/rules/harness.md`'s
  *the harness is more capable than the machine* is the inventory, and it
  is longer than this file: a private mount namespace, a `/proc` already
  mounted in the right pid namespace, device nodes that pre-exist, a PID 1
  whose exit is a status rather than a panic. A claim that needs a real
  boot goes to the operator with its fixture, not to a test here.

- **`make proof` must not run beside `make test`.** CBMC is killed under
  CPU or memory pressure, and a full run contending with the suite aborted
  in `leaf_name_dup` on this machine. `cbmc` itself IS installed here
  (`/usr/bin/cbmc`), so `proofs/run.sh` takes its real path rather than the
  SKIP-with-exit-3 path. `CLAUDE.md`'s build section is the record.

- **The container is ephemeral** — reported by the environment, not
  something this file can measure. Work that is not pushed does not
  survive it, which is the concrete reason behind `CLAUDE.md`'s *a tarball
  is not a delivery*. Push side branches rather than keeping them local.

## Small things that cost a cycle

- **No `/usr/bin/time`.** `ls /usr/bin/time` fails. Time a command with
  `date +%s%N` either side, or the shell's `time` keyword.

- **A foreground `sleep` is blocked by the agent harness**, not by the box.
  Waiting on a condition means an `until` loop in a backgrounded command.
  Listed here because it looks like a machine limitation the first time it
  refuses.
