# 15 — Per-house scheduling via sched_ext

Status: **partially built, partially waiting on a prerequisite (`CLAUDE.md`
kind 3), and narrower than the brief's assumed shape.** The reject-path
mechanism — a real, syscall-level check that this kernel lacks
`sched_ext`, wired into `nw-sup`'s spawn-time lid application exactly
like every other lid's refusal — is built and tested this round. An
actually-loaded, accept-path policy is not, because nothing available to
this project can build or verify one; that half is named kind 3 below.
A first draft of this note deferred both halves together and was too
conservative — `claims` found real precedent in this project's own
history (Landlock) for building the reject path before an environment
exists for the accept path, and found the "nothing can be built" framing
overclaimed. Read the measurement first; it is still why the accept path
is deferred, just not why the whole mechanism is.

The reject path is now **built and landed**: `make test` passed clean
across the review cycle (three times: before dispatch, and twice after
fixing findings), `control` and `tcb-review` both reviewed the real code
and found real issues — a HIGH ordering bug (the check ran after a
brick's pivot and could be fooled by the house's own filesystem) and a
smaller set of gaps — all fixed and covered by new regression tests; see
"Review findings" below.

## The measurement, before any design

The brief asked: "does this kernel/QEMU environment actually support
sched_ext (CONFIG_SCHED_CLASS_EXT)? Don't assume — check and state how
you checked." Checked four independent ways, all negative, recorded in
full in `docs/ENVIRONMENT.md`'s new "No `sched_ext`" entry so the next
reader finds it where environment facts live rather than only here:

1. `zcat /proc/config.gz | grep SCHED_CLASS_EXT` → `# CONFIG_SCHED_CLASS_EXT
   is not set`. The running kernel's own build config says the feature
   was left out.
2. `/sys/kernel/sched_ext` does not exist. If the feature were compiled
   in, this directory exists whether or not any scheduler is currently
   loaded — its absence is the runtime confirmation of (1), not a
   duplicate of it.
3. The running kernel's own exported BTF (`/sys/kernel/btf/vmlinux`, the
   type information the kernel publishes about itself) contains no
   `sched_ext_ops` struct and no `scx_enable`/`bpf_scx` symbol anywhere
   in the file. This is the decisive one: even a perfectly-built
   sched_ext BPF object has no type to CO-RE-relocate against and no
   kfunc to call, on this exact running kernel, independent of
   toolchain. Generic BPF struct_ops support does exist in this kernel
   (other subsystems use it), so this is not "struct_ops is absent" —
   it is specifically that sched_ext's own struct_ops type was never
   registered, which is exactly what leaving `CONFIG_SCHED_CLASS_EXT`
   unset produces.
4. No `bpftool`, no `libbpf-dev`, no header anywhere on the filesystem
   whose name contains `sched_ext` (`find / -iname '*sched_ext*'`
   returns nothing). `clang` 18.1.3 and a libbpf **runtime** `.so` are
   present, so a generic BPF program could in principle be built and
   loaded here — just never one that targets sched_ext specifically.

Independently confirmed the brief's own premise rather than taking it
on faith: sched_ext did merge into mainline at Linux 6.12 (`kernel.org`'s
own scheduler documentation and the LWN/Phoronix coverage of the merge
agree on this). This box runs `6.18.44-fc-v37`, newer than 6.12 — so
**this is a build-configuration gap, not a kernel-too-old gap**. Whoever
built this kernel compiled the feature out. `/boot` is a trap here and
is named as one in the `ENVIRONMENT.md` entry: it holds a completely
different kernel's config and `vmlinuz` (`6.8.0-139-generic`) than the
one actually running, so checking there would answer the wrong question.

## Revised after `claims`: the first version of this section was too conservative

The first draft of this note read the measurement as grounds to defer
the *entire* runtime mechanism — field, validation, and the `nw-sup`
consumer — as kind 3, building nothing but this note. `claims` reviewed
that draft and pushed back, correctly, by reading this project's own
history rather than taking the draft's analogy on faith:

**What actually shipped for Landlock, at a time when every machine this
project ran on lacked it**, is not "defer everything." It is a real
plan field, real baker/`nwcheck.c` validation, and a real `nw-sup`
consumer — `sys_landlock_create_ruleset(NULL, 0,
LANDLOCK_CREATE_RULESET_VERSION)`, a raw syscall, `die("landlock
unavailable")` on failure — that took the REJECT branch on every
machine available at the time, genuinely and non-simulated, and was
only trusted to *confine* anything once an operator later reported a
machine with Landlock ABI 7. The reject path was real from day one; only
the accept-path assertion waited on an environment.

**The first draft's `nw_res` analogy doesn't hold up against
`CLAUDE.md`'s own definition of that defect**: `nw_res` was "declared in
the format, written by the baker, validated by `nwcheck`, and
**referenced zero times in `nwsup.c`**." A field with a real `nwsup.c`
consumer that attempts the actual mechanism and correctly `die()`s is
referenced a nonzero number of times, with genuine (if currently
one-directional) behavior — a different, and lesser, thing than what
that rule forbids.

**And the first draft's "nothing here can build... regardless of how
the code is written" was an overclaim, falsified by checking rather than
asserting**: `<linux/bpf.h>` and the raw `__NR_bpf` syscall number are
present via `linux-libc-dev`, already installed, no `bpftool` or
`libbpf-dev` required — enough to write a genuine, syscall-level
"does this kernel support the mechanism" check, shaped exactly like
Landlock's own probe. What that sentence should have said, and now
does: nothing here can build a *loadable, working scheduling policy* —
that specific claim is true, measured, and unaffected by this revision.

**Revised recommendation: build the field, the validation, and a real
`nw-sup` reject-path consumer this round. Defer only the accept-path —
an actually-attached policy changing scheduling — as kind 3**, named
`skip()` in the suite exactly as `lid-landlock` was for years, per
`harness.md`'s "detect the capability, not a tool that implies it" and
the standing rule that a `SKIP` must be named, never silent.

## The six questions, answered against the revised scope

**1. Environment support.** Answered above: absent on this box, measured
four ways, a build-configuration gap rather than a version floor.

**2. Plan-level interface.** `sched-ext=<name>` on a house's plan entry,
absent meaning "default." Follows `lids=`'s shape: a small closed set of
names, resolved by `nw-cc.py` the same way `lids=` tokens are, validated
offline by `nwcheck.c` against that same closed set (a new `NW_E_SCHEDEXT`
for an unknown name, the same shape as `NW_E_LIDS`), applied at spawn
time by `nw-sup`, never decided live. This round's closed set is exactly
one name, `default` — the brief's own "no-op, prove the plumbing"
policy — so the set can grow without a format change later; adding a
second name is a baker/checker change, not a blob layout change.

**The key is `sched-ext=`, not `sched=`, and this was caught before code
was written rather than after.** `struct nw_res` already has a
`sched_policy` field, keyed by the plan as `sched=other|batch|idle`
(classic `sched_setscheduler(2)` policy, `NW_E_RESSCHED`/`NW_E_NICEPOL`),
fully baked, checked and named — a completely different mechanism from
sched_ext, sharing nothing but a tempting four-letter word. Reusing
`sched=` for this feature would have collided a new field onto an
existing one's name, the exact shape invariant 3 exists to prevent one
level over (two mechanisms, one name, rather than two copies of one
arithmetic expression). `NW_E_SCHEDEXT` is deliberately spelled
differently from the existing `NW_E_RESSCHED` for the same reason.

**3. Where the BPF program lives.** The brief's own recommendation — a
small, fixed set of pre-built, vetted policies selectable by name, not a
per-house custom-BPF pipeline — stays right, and stays deferred until an
environment can build one: this round's `sched-ext=default` names no BPF
program at all, because there is no working artifact to name yet (see
question 1). When a real policy exists, the content-addressed store
(`docs/options/14-shared-store.md`) is the natural home for it by hash,
the same reasoning that scoped that store to serve future artifact
types.

**4. Who applies it.** `nw-sup`, at spawn time, alongside the other lid
applications, in a new `apply_sched_ext()` checked the same way
`lid_brick()`/`lid_landlock()` check their own preconditions — detection
and refusal are one runtime decision, not a probe-then-decide the way a
non-TCB test harness capability guard is.

**5. Failure mode.** Fail loud, matching every existing lid: no declared
`sched-ext=` name may run unconfined or silently ignored. This round that
means: `sched-ext=default` on a kernel without `CONFIG_SCHED_CLASS_EXT`
`die()`s with a reason distinguishing "this kernel has no sched_ext at
all" from a future "the named policy failed to load" — collapsing the
two would be `CLAUDE.md`'s characteristic failure in miniature, a
message that reads as specific but is not.

**6. COMMITMENT CHECK.** Confirmed: selecting a policy is a plan-time
declaration, resolved once at bake time and once at spawn time, with no
live decision anywhere — matching `lids=` exactly, and unaffected by
this revision.

## What was built this round, honestly bounded

**The reject-path consumer, real and syscall-level, not simulated.**
`nwsup.c` gains `sched_ext_supported(void)`: `stat("/sys/kernel/sched_ext",
...)` as the kernel's own first-line existence signal (the kobject the
scheduler class creates unconditionally when the feature initializes),
then, only if that exists, `mmap()`s `/sys/kernel/btf/vmlinux` read-only
and scans it for the literal type name `"sched_ext_ops"` — the same
authoritative, kernel-published signal this note's own measurement used
by hand, harder to fake than a config file or a directory's mere
presence, and the same "ask the kernel, not a proxy for the kernel"
shape `sys_landlock_create_ruleset()` already uses. No allocation:
`mmap` rather than a read-into-buffer, scanned in place, unmapped
immediately. Bounded: the scan is over `st_size` bytes of a file the
kernel itself sizes, no recursion, no unbounded loop.

A house declaring `sched-ext=default` calls this at spawn time, in
`apply_sched_ext()`, alongside the other lid applications; on this
kernel it always returns false, and `apply_sched_ext()` `die()`s with
`"sched-ext unsupported"`, matching every other lid's refusal shape
exactly. **This is the real, measured, non-simulated behavior this
round can prove**, and the control below shows it failing when the
check is removed — the same discipline `test_brick_is_a_root`'s two
controls already established for a different lid.

**This forced a magic bump, NWPLAN09 -> NWPLAN10, which was not
anticipated when this note argued the reuse was safe.** The spare byte
being renamed rather than merely repurposed in place means
`test_magic_moves_with_the_layout` — which hashes every `NW_AT`/`NW_TYPE`
declaration's own text, name included, against a ledger in
`plan-formats.txt` — correctly saw a changed declaration and required a
new row. The safety argument in `blob.h`'s own comment on the field (an
old blob's `_pad=0` and the new field's `UNSET=0` coincide, so nothing
is reinterpreted) is still true and is a reason a bump was not *needed*
for correctness; it is not a reason to skip the one the ledger's own
mechanism asks for, and this note does not skip it. `plan-formats.txt`
carries the new row and the reasoning for why it exists despite the
safety argument.

**Deferred, named as kind 3**: an actual policy being loaded and
attached, and any assertion about `/sys/kernel/sched_ext`'s state
changing as a result. No environment available to this project could
exercise that direction at the time this was written; that has narrowed
since — see the next section — but not closed: when a working policy
binary is available, `sched_ext_supported()`'s reject branch does not
need to change, only a new accept branch needs adding beside it, and a
new named policy beyond `default`.

## A free, real-kernel environment exists for the accept path — measured, not assumed

Measured 2026-09-26, outside this sandbox: a one-off GitHub Actions
workflow (`.github/workflows/sched-ext-probe.yml`, pushed, triggered,
its real job logs read back and reported, then deleted once the
question was answered either way — not kept, per the same "a proof kept
outside the tree is a sentence" reasoning `CLAUDE.md` applies to
`proofs/`, and this was never a proof, only a measurement tool with a
one-time job). The workflow file itself is gone, but the runs it
produced are not this project's to keep or delete — GitHub retains a
run's history and logs independently of whether the workflow that
produced it still exists in the tree, so the claims below are named by
run rather than left to rest on this note's paraphrase alone: three
runs on GitHub-hosted `ubuntu-24.04` (kernel `6.17.0-1022-azure`, image
`20260920.314.1`), run ids `36274019998`, `36274390526` and
`36274470200` in that order on `isolati0n/city`:

- **`/proc/config.gz` is unreadable on that runner** — not a negative
  answer, an absent one. The FIRST run's workflow gated its branching on
  this check alone, read "unreadable" as "no", and printed a false
  "does NOT have sched_ext" conclusion without ever attempting the next
  step. Caught by reading the run's own raw log rather than its printed
  conclusion, and fixed by gating on the next check instead — the same
  "a success/failure signal confirms the step that ran, not the step
  that mattered" shape `CLAUDE.md` names for `proofs/run.sh`'s
  `--show-loops` guard, here with an *inconclusive* signal read as a
  negative one instead of a passing one.
- **`/sys/kernel/sched_ext` exists**, populated: `ls -la` on it there
  shows `enable_seq`, `hotplug_seq`, `nr_rejected`, `state` and
  `switch_all`. The local measurement above only asserts that this
  directory would exist at all if the feature were compiled in — it
  names no files inside it, because on this sandbox's kernel the
  directory itself is absent and there was nothing to list. The
  directory's mere existence is the kernel's own unconditional signal
  that `CONFIG_SCHED_CLASS_EXT` is compiled in and the feature
  initialized — more direct than the config file, and independent of
  it; the five filenames are what a populated instance of that same
  signal looks like, not a prediction this note made in advance.
- **`sched_ext_ops` is present in that kernel's own exported BTF**
  (`/sys/kernel/btf/vmlinux`), the same decisive signal this project's
  local measurement used to establish absence, here confirming presence.

So **GitHub Actions' free, hosted `ubuntu-24.04` runner has a genuine
`sched_ext`-capable kernel**, confirmed two independent ways, with the
one inconclusive check (`config.gz`) correctly set aside rather than
misread as a third vote. This falsifies the assumption that a capable
environment requires self-hosting: it does not — nothing about this
measurement addresses cost, so no claim about payment is made either
way.

**What is still missing is a scheduler binary, not the environment.**
`apt-cache show scx` succeeds on that runner, but the package is
Microsoft's OMI-based System Center monitoring agent
(`packages.microsoft.com`) — confirmed by installing it and reading its
file list, which contains no `scx_simple` anywhere, only
`/opt/microsoft/scx/...` and `omi` service files. A pure name collision,
not sched_ext's scheduler suite. `scx-scheds` — the real package name on
distros that carry one — was checked independently, not assumed absent
because the first name matched something: `apt-cache show scx-scheds`
fails outright; it is not in that runner's apt sources at all. Per this
round's own instructions, the probe stopped there rather than building
one from source, so no policy was ever loaded. The script exits
immediately once it fails to find a `scx_simple` binary, so only one of
its three planned checkpoints ever ran: `/sys/kernel/sched_ext/state`
read `disabled` at that single "before" checkpoint, in both runs that
reached it (the run before the gating fix never got this far at all).
The "during" and "after" checkpoints the script also prints are dead
code on every run so far, for the same reason nothing loaded — naming
that rather than implying all three ran and agreed.

**What this narrows, precisely.** The accept path's blocker was never
"no environment exists" in the sense of "the kernel feature is
unreachable anywhere this project can get to" — it is "no working
policy artifact exists yet", which is a smaller, different gap: closed
by a scheduler binary (built from source, or vendored, or found
packaged on some other distro's runner), not by finding or paying for a
different kernel. Nothing about this changes what was built this round
or its scope; it only means the deferred accept path in the paragraph
above has a real, free, currently-idle place to eventually run, rather
than none. Not pursued further right now — this round's task was the
measurement, and the reject path it supports is already built and
reviewed above.

## Closing the scheduler-binary gap — attempted, not closed

Measured 2026-09-28, same environment as above. The prior round stopped
at "no working policy artifact exists yet" by instruction. This round
tried to close it: build a stock scheduler from source on the same
GitHub-hosted `ubuntu-24.04` runner and actually load it, capped at
five push-and-read iterations per the round's own instruction. **Real
progress was made and the cap was hit before a scheduler ever ran**,
so this section names exactly where it got stuck rather than claiming
success. One workflow file
(`.github/workflows/sched-ext-build-probe.yml`, pushed, iterated,
deleted afterward — same "not kept" convention as the previous probe),
five runs on `isolati0n/city`, run ids `36370410442`, `36371337617`,
`36371601052`, `36371749674`, `36371863904` in that order:

1. **`sudo apt-get install -y ... bpftool ...` refuses outright**:
   `E: Package 'bpftool' has no installation candidate` — it is a
   virtual package on this image, provided by `linux-tools-common`;
   naming it directly rather than through a providing package is the
   defect. Fixed by dropping the explicit name.
2. **The clone target was wrong.** `sched-ext/scx` no longer carries
   C schedulers at all — its own README says so in as many words: "C
   schedulers like `scx_simple` were previously included in this
   repository but have since been moved to
   [scx-c-examples](https://github.com/sched-ext/scx-c-examples). The
   schedulers in this repository now use Rust for userspace
   components." `find . -iname "*scx_simple*"` against the freshly
   cloned `scx` repo returned nothing, confirming the README rather
   than resting on it alone. Fixed by cloning
   `sched-ext/scx-c-examples` instead, whose own README was checked
   before writing the fix, not assumed: it names `make all` as the
   build command and `build/scheds/c/scx_simple` as the output path.
3. **The installed `bpftool` wrapper does not work for the runner's
   actual kernel.** The BPF object compiled clean
   (`clang ... scx_simple.bpf.c -> scx_simple.bpf.o`), then skeleton
   generation printed `WARNING: bpftool not found for kernel
   6.17.0-1022` and the build died there. `linux-tools-generic` on
   this image resolves to tools built for the image's own baked-in
   kernel build (`6.8.0-142`), not the actual running
   `6.17.0-1022-azure` kernel `uname -r` reports — a mismatch between
   the image's package set and its running kernel, not a token this
   workflow got wrong.
4. **The exact fix the warning names does not fix it.** The warning
   itself lists `linux-tools-6.17.0-1022-azure` as the package to
   install. Installing it: `apt-get` reports
   `linux-tools-6.17.0-1022-azure is already the newest version
   (6.17.0-1022.22)` — already present — and `bpftool version`
   immediately afterward still printed the identical "not found"
   warning. Verified by re-invoking the tool after install, not
   assumed from the install succeeding — that much is checked. The
   likeliest reading is a gap in this runner image's cloud-kernel
   packaging (the Azure-flavoured `linux-tools` package for this exact
   kernel build not shipping a working `bpftool` binary), but that is
   an inference from the symptom, not something separately confirmed —
   no `dpkg -L` or equivalent was run to look inside the package
   before iteration 5 moved to a different fix, so it is named here as
   the working theory rather than an established fact.
5. **Building a standalone `bpftool` from source got further than
   apt could, and hit a new, different failure — not the same one
   again.** Cloned `libbpf/bpftool` (which vendors its own `libbpf`
   via git submodule, so it does not depend on this runner's system
   `libbpf-dev` version), built it, installed it ahead of the broken
   wrapper on `PATH`, and confirmed with `bpftool version` before
   proceeding — this succeeded, and skeleton generation's own
   "not found" warning did not recur. The BPF object compiled, the
   skeleton generated clean, and the build reached the final
   userspace link step:

       cc ... scx_simple.c -o build/scheds/c/scx_simple -lbpf -lelf -lz -lzstd -lpthread
       scx_simple.bpf.skel.h: In function 'scx_simple__create_skeleton':
       scx_simple.bpf.skel.h:294:12: error: 'struct bpf_map_skeleton' has no member named 'link'
         294 |         map->link = &obj->links.simple_ops;

   Both halves of the version skew are directly quoted from this exact
   run's own log (job `108769724560`), not inferred: an earlier step
   in the same run shows `Setting up libbpf-dev:amd64
   (1:1.3.0-2build2) ...` — the header the final `cc -lbpf` link step
   used — and the self-built `bpftool`, in the step right after
   building it, printed its own version banner: `bpftool v7.8.0`,
   `using libbpf v1.8`. (Iteration 1's own
   install step never reached the `libbpf-dev` line at all — `apt-get
   install` under `set -e` aborts the whole package list on the first
   unresolvable name, which is what it hit — so the version comes from
   this run, the one whose link failure it explains, not an earlier
   one.) `v1.8` generating a skeleton and `1.3.0`'s headers linking it
   is the version skew directly, not an inference from the compile
   error alone: the error naming a struct member (`link`) that an
   older `struct bpf_map_skeleton` lacks is corroborating detail, not
   the only evidence. Skeleton generator and link-time library
   disagree about a struct layout — a version-skew defect, not a
   missing-tool one. Closing it
   needs the userspace compile to link against the *same* `libbpf`
   the self-built `bpftool` used to generate the skeleton (its
   submodule checkout, or a matching installed version), which is a
   real next step this round's five-iteration budget did not reach.

**Be exact about what this does and does not show, per this round's
own instruction.** It shows a stock, unmodified `scx` scheduler's BPF
half compiles clean against this runner's real kernel headers, and
that the only blocker any of this round's runs actually reached is a
build-tooling version mismatch this round diagnosed precisely but did
not fix — not that it is the only one that exists. Nothing this round
ran past a successful link, so whether the runner would even permit
loading a BPF struct_ops program (privilege, `CAP_BPF` or equivalent
in that environment) is untested and unclaimed either way. **It does
not show a scheduler engaging `/sys/kernel/sched_ext/state`** — no run
ever reached the point of executing a built binary, so none of the
three checkpoints (before / while running / after) were captured this
round; reporting any of them would be the same shape of overclaim this
doc's own history already records twice (see the corrections above).
**It does not prove Nexusweave's plan-driven loader**, which remains
entirely unbuilt and unaddressed by either round's measurement.

The workflow file is deleted, per the same "not kept" convention as
the previous probe; the five run ids above are what this section's
claims rest on, the same way the previous section names its three.

## Attempt 3: a packaged binary reaches the kernel, and the kernel refuses it

Measured 2026-09-29, per `docs/OPERATOR-BRIEF.md` Section 4: a third,
differently-shaped attempt at the same accept path, bounded to three
push iterations, measurement only (Phase 4 deletes the `sched_ext`
field regardless of the result). Where attempt 2 tried building a
scheduler from source and got stuck at a userspace link step, this
attempt used a distro's own **packaged** binary specifically to route
around that class of failure — confirmed to exist before spending any
push on it, per the brief's own instruction: `scx_c_schedulers` is a
real Fedora package (`packages.fedoraproject.org`, carried in Fedora
42/43/44 stable) that ships `scx_central`, `scx_flatcg`, `scx_nest`,
`scx_pair`, `scx_qmap`, `scx_simple` and `scx_userland` as prebuilt
binaries.

**Environment**: the same free `ubuntu-24.04` GitHub-hosted runner as
both prior attempts, this time running a `--privileged --pid=host`
Fedora 42 container (`fedora:42`) rather than building on the runner's
own Ubuntu userspace directly. `uname -a` inside the container reports
`6.17.0-1022-azure` — the runner's real host kernel, confirming the
container shares it rather than running under a private one, which is
what makes a struct_ops load attempt inside the container a genuine
test of the runner's actual kernel.

**Two runs, because the first workflow iteration had its own bug.**
Run 1 (workflow run `36511747418`) installed the package, located
`scx_simple`, ran it, and got a real result — then the workflow's own
`kill`/`wait` sequence on the already-exited background process errored
before the final "state after" step could run, so the job reported
`failure` despite having already captured the substantive answer. Run 2
(workflow run `36511898441`, after fixing the wait logic and marking
that one step `continue-on-error`) reproduced the identical result and
additionally captured the "after" checkpoint, completing green. The
quotes below are from run 2, the corrected one; run 1's console output
matches it byte-for-byte on every line that both runs share.

**`/sys/kernel/sched_ext/state` before running anything**: `disabled`.
The directory holds `enable_seq`, `hotplug_seq`, `nr_rejected`, `state`
and `switch_all` — no `root/` subdirectory exists under it on this
kernel, which the workflow's first draft assumed and which produced no
error only because that step was itself guarded.

**`scx_simple` was actually invoked, and libbpf actually attempted to
resolve it against the running kernel — the first time in three
attempts across two rounds that a load was attempted at all — and it
was refused, quoted verbatim:**

```
libbpf: extern (func ksym) 'scx_bpf_consume': not found in kernel or module BTFs
libbpf: failed to load object 'scx_simple'
libbpf: failed to load BPF skeleton 'scx_simple': -22
../scheds/c/scx_simple.c:77 [scx panic]: Invalid argument
Failed to load skel
```

The process exited on its own, immediately, before either run's own
`kill -0` check found it still alive — confirmed by that check itself
reporting "already exited" rather than by inference.
`/sys/kernel/sched_ext/state` read `disabled` both while `scx_simple`
was (intendedly) running and after it exited: the state never
transitioned, because the failure happened during libbpf's own
BTF-resolution pass, before the kernel's struct_ops registration is
ever reached.

**What this decisively answers**: struct_ops loading is **permitted**
in this container — nothing in the CI environment's privilege,
namespace or seccomp posture blocked the attempt before the kernel's
own compatibility check ran. That question was open after both prior
attempts (attempt 1 found no binary to test with; attempt 2 never
linked one). This is the first attempt to actually reach that check.

**What refused it is a kfunc mismatch, not a permission or build-tool
one — a different layer of the same underlying class attempt 2 hit.**
`scx_bpf_consume` is a kernel-side BPF kfunc the packaged binary's
compiled BTF references and this kernel's BTF does not expose under
that name. This is a version-pairing problem exactly like attempt 2's
link-time libbpf/bpftool skew, one layer further down the stack:
attempt 2's mismatch was between a self-built userspace tool and its
own generated skeleton; this one is between a prebuilt binary's
expectations and the exact kernel API surface it was compiled against.
**Which upstream `scx`/kernel version introduced or renamed this kfunc
was not researched from primary sources and is left unresolved here** —
stating a specific version boundary without checking it against
`kernel.org` or the `scx` project's own changelog would be exactly the
uncited-number defect this project's own history already records for
the erofs kernel-floor claim (`docs/options/23`), and this note does
not repeat it.

**What remains exactly as unmeasured as before, for a different
reason**: whether a scheduler can actually engage
`/sys/kernel/sched_ext/state` (transition it to `enabled`) on this
runner is still untested — attempt 2 never linked a binary to try it
with, and this attempt linked one but it failed a compatibility check
before reaching that transition. Three attempts, three different
stopping points, the same unanswered question.

Both workflow run ids (`36511747418`, `36511898441`) and both commits
(`07ebd9f`, `695e62a`) are what this section's claims rest on. The
workflow file is deleted, per the same "not kept" convention as both
previous probes.

## Review findings, and what changed because of them

**`tcb-review` — HIGH, reproduced, fixed.** `apply_sched_ext()` originally
ran AFTER `lid_brick()`'s `pivot_root`, so for any house with `brick=`
it was asking whether the HOUSE's own root has sched_ext, not the
machine's — and after a pivot, nothing under the machine's `/sys` is
reachable unless the plan happens to bind it. Reproduced with a real
staged brick: a plan combining `brick=` with `sched-ext=default` died
with `"sched-ext unsupported"` on every kernel, including a
hypothetically capable one, because the check was reading a fresh,
empty brick's non-existent `/sys`, not the host's real one — the exact
"a message that reads as specific and is not" shape this project
watches for, landed in code written this same round. **Fixed** by
moving `apply_sched_ext()` before `lid_brick()` (right after
`NW_LID_NEWNET`/`NW_LID_NEWNS`, which don't affect `/sys`'s visibility —
a fresh mount namespace starts as a copy of the parent's table and
nothing here unmounts anything). `test_sched_ext_check_survives_the_
brick_pivot` is the regression test: it plants convincing fake marker
files (`/sys/kernel/sched_ext` as a directory, `/sys/kernel/btf/vmlinux`
containing the literal bytes `sched_ext_ops`) INSIDE a real brick, and
requires the real host's genuine absence to still win. Verified red
under the original ordering (the house instead dies with `"sched-ext no
policy artifact"`, meaning it trusted the brick's planted claim) and
green against the fix.

**`tcb-review` — MEDIUM, fixed.** The accept branch (reached when
`sched_ext_supported()` returns true, which nothing available to this
project can trigger) was a bare `say("lid sched-ext")` with no policy
actually loaded — on a real sched_ext-capable kernel, before an accept
path is written, this would silently announce success while scheduling
nothing, `nw_res`'s own defect shape reached through an untestable
branch. **Fixed**: the branch now `die("sched-ext no policy artifact")`
instead, so there are exactly two honest outcomes — refused by name, or
loaded and verified — never a third, silent one. (This is also the
message the HIGH finding's control above distinguishes from the
ordinary capability refusal.)

**`control` — a decorative assertion, and a real, untested guard,
both addressed.** `unit_layout()`'s presence check
(`for want in (..., "sched_ext")`) currently gates nothing for this
field — no test reads `at["sched_ext"]` or `unit_layout()["sched_ext"]`
directly, unlike `name`/`exec_path`/`brick`/`layer`, which are looked up
that way. Left in place (harmless, and future-proofing for a consumer
that does read it directly), but not claimed as pinning anything it
does not. More substantively: nothing exercised nw-sup's own
out-of-range re-validation of `NW_SCHED_EXT` (`if (sched_ext >
NW_SCHED_EXT_MAX) die("sched-ext value")`) — on this kernel, EVERY
nonzero value, legal or illegal, was refused by `apply_sched_ext()`'s
capability check first, masking whether the earlier, separate
re-validation guard did anything at all, the same "recovery path hides
a defect behind it" shape `CLAUDE.md`'s own record describes for a
different mechanism. **Fixed** with
`test_sched_ext_out_of_range_dies_at_the_supervisor`, which drives
nw-sup directly with an illegal value and requires the DISTINCT
`"sched-ext value"` reason, not the capability one — proving the two
guards are actually independent rather than one silently standing in
for the other.

**Everything else `control` and `tcb-review` checked was confirmed
accurate**: the `_pad`→`sched_ext` rename's safety argument for old
blobs holds (no blob's old-reject/new-accept boundary changed meaning,
only one new legal value was added); `NW_MAGIC` is used only as the
format-identity tag `nwcheck.c` compares, nothing else in the TCB
depends on its value; the error-code renumbering (`NW_E_RSV` →
`NW_E_SCHEDEXT`, same slot) has no stale references anywhere;
`sched-ext=` and the pre-existing, unrelated `sched=` (classic
`sched_setscheduler(2)` policy) share no grammar, blob field, or
reachable error code; `sched_ext_supported()`'s `mmap`/`munmap` pairing
and bounds are correct on every path, with no async-signal-safety
concern (called from an ordinary forked child, never a handler); the
`NW_SCHED_EXT` re-validation is present and correctly placed, matching
the `NW_BRICK`/`NW_LAYER` pattern; ordering relative to seccomp is
correct (`__NR_bpf` is absent from `lids.c`'s allow-list, and the check
runs before seccomp is applied); and the CBMC harness change is a
faithful, equal-strength postcondition, confirmed by `goto-cc`
compiling it — a full `cbmc` run was not performed, consistent with
`CLAUDE.md`'s own guidance not to run `make proof` casually alongside
other work; this is named here as an open item rather than a completed
check.

## What was changed

- `docs/ENVIRONMENT.md`: the sched_ext measurement.
- This note.
- `blob.h`: `sched_ext` field on `struct nw_unit` (renamed from the
  spare `_pad` byte, same offset/size/type), `NW_SCHED_EXT_*` name
  constants, `NW_E_SCHEDEXT` (renamed in place from `NW_E_RSV`, same
  numeric code), `NW_MAGIC` bumped `NWPLAN09` -> `NWPLAN10`.
- `plan-formats.txt`: the new `NWPLAN10` ledger row, required by
  `test_magic_moves_with_the_layout` once the spare byte's declaration
  text changed.
- `nwcheck.c`: closed-set validation, `NW_E_SCHEDEXT` in `errs[]`.
- `bakery/nw-cc.py`: `sched-ext=` parsing against the same closed set,
  `NWPLAN10` in the baked prefix.
- `nwspawn.c`: `NW_SCHED_EXT` env var, alongside `NW_LIDS`/`NW_BUDGET`.
- `nwsup.c`: `sched_ext_supported()` and `apply_sched_ext()`, wired in
  alongside the other lid applications; re-validates `NW_SCHED_EXT` the
  same way `NW_BRICK`/`NW_LAYER` already are.
- `proofs/caller_nw_check.c`, `proofs/README.md`,
  `tools/coverage-tcb.sh`: updated to the renamed field/check so the
  CBMC harness still compiles (checked with `goto-cc`, not a full
  `cbmc` run — out of scope for this round per `CLAUDE.md`) and the
  documentation quoting the old line is no longer stale.
- `tests/run.py`: `test_sched_ext_unsupported_refuses_at_the_supervisor`
  (the real refusal on this kernel); `test_baker_refuses_unknown_sched_
  ext` (the baker-side rejection); illegal/legal `sched_ext` values
  added to the existing crafted-fields closed-set test; `unit_layout()`'s
  and `test_baker_writes_the_declared_layout`'s `_pad` references
  renamed; `test_sched_ext_out_of_range_dies_at_the_supervisor` and
  `test_sched_ext_check_survives_the_brick_pivot` (added after review,
  see above).

`control` and `tcb-review` are dispatched — this is a real TCB change in
`nwsup.c`/`nwcheck.c`. `fd-auditor` is not: the only new descriptor
handling is one `open`+`mmap`+`munmap` of a read-only file, closed
immediately, no `dup2`, no fixed number, no lifetime crossing a fork or
exec.
