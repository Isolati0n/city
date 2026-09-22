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
  Measured on `rules-declaration`:
  `git push origin :refs/heads/rules-declaration` answers `error: RPC
  failed; HTTP 403 curl 22 The requested URL returned error: 403` and
  exits 1, four attempts. Ordinary pushes to the same remote succeed.

  **All outbound HTTPS goes through an agent proxy** — `HTTPS_PROXY` is
  set, `GITHUB_TOKEN` and `GH_TOKEN` read `proxy-injected`, and
  `curl -sS "$HTTPS_PROXY/__agentproxy/status"` reports
  `gitConfigInjection` and `gitSshRewrite` true. Whether the 403 is that
  proxy's policy or the injected token's scope is NOT established here;
  `docs/POSTMORTEM-rules-declaration.md` attributes it to the proxy.
  Either way the effect is the same and the remedy is somebody with a
  shell that does not go through it. **The trap is the pipe**: run as
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

- **Project quota is off on the root device.** The root device is
  `/dev/vda` (`awk '$2=="/"' /proc/mounts`), and
  `quotactl(QCMD(Q_GETINFO, PRJQUOTA), "/dev/vda")` answers `-1` with
  `errno 3`, `ESRCH` — which is what `.claude/rules/runtime.md` records
  and what `tools/HANDOFF-resources.md` carries the probe for.

  *This entry first said the claim was unreproduced, on a probe of mine
  that named `/dev/root` — a device that does not exist here, so it
  answered `ENOENT`. The working probe was already in the tree, a few
  lines from the sentence being doubted. An entry recording a failed
  measurement, in a file about measurement, while the successful one sat
  in the repository. `claims`.*

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
  in `leaf_name_dup` on this machine — that duration is `CLAUDE.md`'s
  build section, not a measurement taken here. `cbmc` itself IS installed
  (`command -v cbmc` → `/usr/bin/cbmc`), and `proofs/run.sh` guards on
  exactly that (`command -v cbmc >/dev/null 2>&1 ||` … `exit 3`), so it
  takes its real path rather than the SKIP arm. Naming the guard is the
  point: this is a tool-presence sentence whose code under test asks the
  same tool-presence question, which is what makes it an argument rather
  than the inference `prereport` flags it as.

- **The container is ephemeral** — reported by the environment, not
  something this file can measure. Work that is not pushed does not
  survive it, which is the concrete reason behind `CLAUDE.md`'s *a tarball
  is not a delivery*. Push side branches rather than keeping them local.

## Costs worth knowing before you plan a round

- **`make test` is about forty minutes.** That is the single largest cost
  this container imposes and the reason `tests/run.py --only <name>`
  exists — a named subset runs in seconds. The full target is still the
  only green that counts before a push; `--only` is for iterating.
  (`make proof` is tens of minutes on top, and `CLAUDE.md` says not to
  budget for it from any written line.)

- **`test_console_house_reachable` costs about fifty seconds of that
  forty minutes, measured**: `time python3 tests/run.py --only
  console-house-reachable` → `real 0m48.725s` on this box. It boots
  three separate QEMU guests under TCG (no `/dev/kvm` here — see below)
  for `tools/console-boot-test.py`'s checks `console-house-reachable`,
  `console-house-control` and `console-house-seccomp-control` — the
  fourth, `console-house-lids-exact`, re-bakes the plan and reads a byte
  offset out of the blob, no boot at all. `--only` for iterating on it
  specifically is still slower than most of the suite combined, so
  prefer reading `tools/console-boot-test.py`'s own output over re-running
  it unless the change is actually near it.

- **What that test needs, each checked the way it checks itself, not
  assumed:** `qemu-system-x86_64` is present
  (`command -v qemu-system-x86_64` → `/usr/bin/qemu-system-x86_64`);
  `mkfs.erofs` is present too (`command -v mkfs.erofs` →
  `/usr/bin/mkfs.erofs`, needed to build the probe brick); a STATIC
  `/bin/busybox` is not, by default — `apt-get install
  busybox-static` installs one, and `file /bin/busybox` must report
  `statically linked` afterward, because the brick this test boots
  carries no loader and the dynamically-linked copy in
  `busybox-initramfs` fails `execve` inside it with `ENOENT` (the exact
  trap `harness.md` names for `dawn-real-boot`, met again here). Root is
  required for the loop mounts `tools/mkboot.sh`'s image build does
  (`os.geteuid() != 0` is the check). All four are asked for by
  `check_environment()` in `tools/console-boot-test.py`, and missing any
  one is a named skip (`SKIP: console-boot-test (...)`), not a silent
  pass or a crash — the same discipline `landlock-confines` already uses
  via `Unavailable`.

- **The agent's batch harness reported a control GREEN once, wrongly, and
  it has not been explained.** A mutation that should have turned a test
  red printed `STILL GREEN` inside a batch while the artifact it guards
  had in fact changed; run again standalone and in a batch afterwards, the
  same mutation is red. Two causes were ruled out by running — a
  pre-corrupted fixture blinding the comparison, and the mutation not
  applying. **This is the entry most likely to cost somebody a wrong
  conclusion**, because a control that passes reads as good news. If a
  batch reports green where you expected red, capture the mutated file and
  the artifact AT THAT MOMENT rather than the summary line.
  `tools/HANDOFF-only-and-env.md` has the episode.

## Small things that cost a cycle

- **No `/usr/bin/time`.** `ls /usr/bin/time` fails. Time a command with
  `date +%s%N` either side, or the shell's `time` keyword.

- **`make prereport` cannot see a file you have not `git add`ed**, because
  its input is `git diff`. A new file scans as absent and the run reports
  clean about it — which is how this file reached `main` unscanned, with a
  clean `prereport` quoted in its commit message. The census has the same
  blind spot for the same reason, and `CLAUDE.md` records that one. `git
  add -N` the file, or run the gate after staging.

- **A long foreground `sleep` used to WAIT is refused by the agent
  harness**, not by the box, and short ones are fine. `sleep 2` and
  `sleep 20` both return 0, chained with another command or alone;
  `sleep 100` followed by a command was refused outright with a message
  naming `Monitor` with an `until` loop, or `run_in_background`, as the
  way to wait. Observed rather than bisected — the threshold is not
  measured here, and the harness's own suggestion uses `sleep 2`.

  *This said a foreground `sleep` is blocked, full stop, which does not
  reproduce in any form. It was taken off a tool description instead of
  run, in the entry whose subject is the one thing this file's header
  does not cover — the harness rather than the container. `claims`.*
