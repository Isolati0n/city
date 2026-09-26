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
changing as a result. No environment available to this project can
exercise that direction yet; when one is, `sched_ext_supported()`'s
reject branch does not need to change, only a new accept branch needs
adding beside it, and a new named policy beyond `default`.

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
