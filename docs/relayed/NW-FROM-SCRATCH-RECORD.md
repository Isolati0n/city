# The from-scratch init — consolidated record

Assembled 2026-09-14. **Status: none of this is decided, scheduled or
dispatched.** It is a record of ground-up designs produced in one session by
three agents working independently, the measurements that tested them, and
the rules that survived. It exists because the alternative is a chat
transcript, and this project's own standard is that a specification held
somewhere the tree cannot disagree with it is a claim about itself.

Provenance is marked throughout. **Measured** means something was run and
the output is quoted. **Checked** means read from the tree at a named
location. Everything else is reasoning, including most of what follows.

Nothing here is a proposal against the current build order.

---

## 1. The central result: three designs, one answer

Three agents were asked, independently and without seeing each other's
work, to design this init from the ground up with no constraints.

**Two of the three reached the same central move: delete the per-house
supervisor and move restart into PID 1.** The third — mine — proposed
dissolving supervision into the kernel via BPF, and rested on a kernel
facility that turned out to be compiled out of both available kernels.

Independent convergence is worth more than agreement. Where the three
diverged, measurement settled it every time, and it settled against the
outlier more often than not.

### What provoked it

**Checked.** `lid_brick` is called at `nwsup.c:613`, **inside the forked
child**, alongside `lid_netns`, `lid_newns`, `lid_landlock` and seccomp, all
before `execv`. The parent sets `child = p` and calls `waitpid`. Nothing
else. `nwsup.c:595` says so in a comment: *Stay. Isolation applies to the
house child, not to wait/restart.*

**And the parent cannot drop its privilege.** The next restart child
inherits the parent's capability set at `fork`. Drop `CAP_SYS_ADMIN` after
the first house starts and a restarted house cannot `unshare` or mount its
brick — silently, on the second death, on exactly the houses that have
budgets.

So the current architecture requires **a permanently root, permanently
capable, resident process per house whose running job is to hold an integer
and be able to fork.** Nobody chose that. It is what userspace supervision
costs, and it was discovered by reading the tree rather than by designing.

Every ground-up design below is a response to that one fact.

---

## 2. The three designs

### 2.1 Solvent — dissolve the init into the kernel

*Author: Claude. Largely refuted; recorded for the parts that survive.*

**The inversion.** Every init puts a process between you and the kernel.
Ask what a userspace init does that Linux cannot do itself: isolation is
namespaces and cgroups, death notification is `cgroup.events`, resource
policy is cgroup controllers and `sched_ext`. What remains is reaping
orphans, the first policy decision, and talking to a human.

So: the supervisor becomes a BPF program on `cgroup.events` plus a map of
`{deaths, budget, last_status}`. Nothing resident, nothing privileged. This
answers the modular pass's M2 objection outright — a single watcher process
is a single point of failure, but **a BPF program has no death**. It is not
a process; it cannot be killed and cannot be the thing that failed.

**Why it fails. Measured.** `CONFIG_SCHED_CLASS_EXT` reads *is not set* on
both available kernels — Ubuntu 6.8.0-139 and the 6.18.44 host. The
resource half of the design rests on a scheduler facility that does not
exist on the machine. `CONFIG_BPF_LSM=y` on 6.8 and not on 6.18, so the
isolation half is also kernel-dependent.

This is exactly the failure another design explicitly refused, having
declined to use `CLONE_EMPTY_MNTNS` on the grounds that *a plausible flag
that is not on the stock kernel you boot is worse than no proposal.*

**What survives.** Generations rather than A/B slots — A/B is a two-element
special case, and N complete tuples give rollback and a bisect search space
for free. The desktop layout inside the tuple, so a generation restores
your desk and not only your software. Remote attestation as a by-product of
having a complete sealed self-description. None of the three depends on
BPF.

### 2.2 The durable-objects design

*Author: Grok. The strongest of the three.*

**The inversion.** *A house is a durable tuple of named kernel objects. The
process is a guest. PID 1 is the plan, compiled, and is the only privileged
process.*

A house is a row in a static table baked into PID 1, a cgroup whose limits
are that row, a persisted set of namespaces, a detached erofs mount plus
overlay, a store generation, and at most one inhabitant named by a `pidfd`.

**Why the capability problem dissolves.** The measured fact — the parent
must stay privileged because the next child inherits at `fork` — is true of
*that process graph*, not of Linux. PID 1 already lives forever and already
must fork. Give it the table. One privileged process, and it is the
compiled plan.

**The mechanism, and its bet.** First inhabitation creates the mounts;
afterwards the namespaces are pinned by bind-mounting `/proc/<pid>/ns/*`
onto files, the way `ip netns` works, and restart is `setns` + `exec` with
no remount. Two verbs in the table — *inhabit* and *recreate* — and no
third invented at runtime.

The bet was named and unrun: *kill every process in the mntns, setns back
in, write to the overlay.*

**Measured, in the guest, on the real boot path:**

```
[ns] overlay.ko loaded
[ns] child: overlay mounted inside its own mntns
[ns] pinned /proc/80/ns/mnt -> /nwns/pin
[ns] every process in that namespace is dead
[ns] setns ok — re-entered the dead namespace
[ns] read through the overlay: 'written while alive'
[ns] write after death: ok
```

Three supporting results, measured in a container:

- **The pin is what holds it.** Unmount the nsfs bind and re-entry fails
  with `reassociate to namespace 'ns/mnt' failed: Invalid argument`. The
  namespace's lifetime is exactly the bind's, which makes *recreate* a real
  operation rather than a hope.
- **Re-entry is repeatable** — three times into one pinned namespace.
- **A sealed erofs brick survives inside** the persisted namespace: after
  every process died, the brick file still read and the overlay still took
  writes.

**One constraint discovered:** `mount -o loop` **fails inside** an unshared
namespace and works when the loop device is attached outside and the device
mounted within. So loop attachment is PID 1's job at first inhabitation.
On the 6.18 target this stops mattering — `CONFIG_EROFS_FS_BACKED_BY_FILE=y`
there, and not on 6.8. **Measured from both kernel configs.**

**Its stated failure mode:** a correct-looking plan tree whose nsfs handles
no longer name the mounts the operator thinks they do. *The operator will
`ls` and see a house. The inhabitant will start and see a different root.*

### 2.3 The recipe design

*Author: DeepSeek.*

**The inversion.** The compiler emits the isolation sequence as a **data
structure, not code** — a bytecode the child interprets, validated offline
by the checker. PID 1 holds a recipe table, a `pidfd` per house, and an
epoll set. No supervisor.

**Its distinctive contribution: `io_uring` registered files.** The kernel
holds the reference, so the log pipes leave PID 1's descriptor table
entirely and the house ceiling stops being a descriptor count. It is the
only proposal that attacks the ceiling rather than moving it. It flagged
its own uncertainty precisely — whether a pipe read end registered and then
closed remains readable — which is the right question and is untested.

**Where it is weaker.** It accepts that PID 1 *"allocates, it grows."* The
durable-objects design keeps the discipline by having the compiler
**generate** PID 1 with static tables sized by the plan: same work, no
allocation. That is the better answer.

**One factual error, measured against the tree.** It argued *ext4 with
data=ordered would not produce this* about a crash result. The recovery
boot's own line: `EXT4-fs (vda): mounted filesystem ... r/w with ordered
data mode`. It did produce it. `data=ordered` orders file data before the
metadata referencing it, within a file; it says nothing about one file's
data against another file's rename.

---

## 3. What the machine said

Every number below was produced by running something. Guest results are
Ubuntu `6.8.0-139-generic` under TCG with no KVM; container results are
`6.18.44`.

### 3.1 The descriptor ceiling is arithmetic

Measured on two independent machines: last open **510** houses, first
failure **511**, against a soft `RLIMIT_NOFILE` of 1024.

The model is stdio (3) plus two descriptors per house — `floor((1024-3)/2)`
is exactly 510, with no slack at either end. **Checked:** PID 1 opens the
plan at `pid1.c:447` and closes it at `469`, so only stdio is held at the
pipe loop.

Post-interleave the peak is `6 + n`, counted from source and bounded by
measurement — last open 1018, first fail 1020 on `report pipe`.

### 3.2 `NW_FD_RESERVED` was chosen, not counted

The constant is 8. The counted peak is `6 + n`. The check compares `8 + n`
against `rlim_max`, which `getrlimit` measured. An unlabelled 8 beside a
measured 1024 reads as though both were obtained the same way.

Decided: the compile-time asserts stay at `2n + 8`, deliberately, recorded
rather than corrected. At `NW_MAX_FDS = 1024` that caps units at 508 where
the count allows 1018 — revisit when a plan needs the room.

### 3.3 The writable layer loses data silently

A program appended data, then renamed a commit marker. No `fsync`. QEMU
`SIGKILL`ed mid-write.

```
last chunk with real data:   GEN 34194
marker m =                   36101
trailing all-zero chunks:    6
tail = 0
```

**The marker outlived its data by 1,907 generations.** And six blocks at
the end exist, are counted in the file's size, and are entirely zeros —
well-formed, correctly-sized records full of nothing. `tail = 0`, so the
file ends exactly on a boundary and a length check reports it intact.

Consequence: a layer declares `ephemeral` or `synced`. There is no middle
value, because ordering without durability is not something the filesystem
provides.

### 3.4 fs-verity works, and the digest is not content alone

Guest, on a root with `tune2fs -O verity`:

```
ENABLE  ok
MEASURE ok  alg=1 size=32  digest=0c6e57b0…a1c45d17
reopen for write  REFUSED  errno=1 (Operation not permitted)
```

Byte-identical 1271-byte files, different parameters:

```
block_size=4096   digest=0a365f0e…c78bc8
block_size=1024   digest=9a30c81c…78edce
```

So a store addressed by verity digests has a **store-wide constant** that
must be fixed before any object is sealed and cannot change without
re-sealing everything.

### 3.5 Corruption gives three distinguishable states

| state | signal |
|---|---|
| object absent | `ENOENT` at open |
| data corrupted after sealing | open succeeds, read `EIO` |
| verity metadata corrupted | open fails `EINVAL` |

Data corruption: `fs-verity (vda, inode 39): FILE CORRUPTED! want_hash=…
real_hash=…`. Metadata corruption: `Wrong data_size: 7233194814767134812
(desc) != 1271 (inode)`.

The kernel logs both hashes, so attribution is *this is what it should have
been*, not merely *this is wrong*.

### 3.6 A GUI app's working set

Package closure over-counts, runtime trace under-counts, and both were
measured.

```
galculator     182 packages   489 MB    (apt follows every OR-alternative)
runtime trace  221 files      251 MB    (25s startup, no user interaction)

  136.9 MB  libLLVM.so.20.1      GTK inits a GL context; Mesa software fallback
   41.4 MB  libgallium…so
   29.4 MB  libicudata.so.74
    7.8 MB  libgtk-3.so.0        the actual UI
```

Three files are 84% of it. **0.26 MB is the app's own files** — 99.9%
shareable by content.

Resident during a real run: `libLLVM` 68.9%, `libgallium` 11.5%. So eager
verification costs ~1.5× the read, not the 20× a demand-paging argument
assumes. A `sha256` of `libLLVM` takes **401 ms at 342 MB/s**.

### 3.7 The virtio status register does not attest the guest

`x-query-virtio-status` over QMP is empty before the guest probes and shows
all four statuses after boot. But a guest at a kernel panic screen reports
`DRIVER_OK` with QEMU reporting `running`.

So the signal moves the attested boundary from `exec` to *early kernel* — a
few hundred milliseconds — and not to *booted*, which was the use proposed
for it. The fact is true; the inference is not.

### 3.8 The brick path had never run in a boot

**Checked and then fixed.** `mkboot.sh` staged two modules,
`nls_iso8859-1` and `nls_utf8`. `CONFIG_OVERLAY_FS=m` and
`CONFIG_EROFS_FS=m` on the kernel it boots. So `mount("overlay", …)`
returned `ENODEV` and no brick house could start under QEMU — while the
suite's five brick tests passed, because they run in the host harness on
the host kernel.

Staging both was still not enough: `erofs: Unknown symbol crc32c (err -2)`,
because `erofs.ko` references `crc32c` and `libcrc32c.ko` exports it —
while `crc32c-intel` and `crc32c_generic` **are** builtin, so a reasonable
reader checks the config, sees crc32c, and misses the one module that
matters.

With `libcrc32c`, `overlay` and `erofs` staged in load order, a brick house
booted:

```
[nw-sup] lid newns
erofs: (device loop0): mounted with root inode @ nid 36.
[nw-sup] lid layer
[nw-sup] lid brick
[nw-sup] lid seccomp
[nw-root] house exit brickhouse status=0
```

---

## 4. Rules adopted

### 4.1 The mechanism rule

In `CLAUDE.md`. Adopted after `nw_res` was found declared in the format,
written by the baker, validated by `nwcheck`, and referenced zero times in
`nwsup.c`.

The unit is **the bit, not the field** — `lids` packs four independent
declarations into one byte.

1. Every configuration field has exactly one mechanism that makes it true
   at runtime, named where the field is declared. *Validation is not a
   mechanism* — it refuses a plan; it does not make a statement true.
2. Every mechanism named in the plan serves at least one field, and
3. no field is served by more than one mechanism.
   The converse is **false** and the rule does not claim it: one mechanism
   legitimately serves several fields. This is a function from fields to
   mechanisms, **not a bijection** — an earlier draft said bijection and
   was wrong, because a mount namespace carries isolation, path visibility
   and the bind set at once.
4. A field whose mechanism is a single translation unit must name that unit
   and have a test exercising the consumer.
5. Descriptive metadata is a separate syntactic class and is not a
   configuration field.

Plus a test obligation that is deliberately **not** a format rule, because
it cannot be checked by reading a plan: every configuration field has a
test that runs and fails when its mechanism is absent, and **a `SKIP` is a
failure.**

**Why clause 4 earns its place.** An audit proposed exempting *numbers
whose interpretation is code* — and that class, as stated, contained
`nw_res`, the defect the rule exists to prevent. A category defined by what
it lacks is a hole; defined by what it requires, it is a contract.

### 4.2 Capabilities, not paths

`blob.h` already argues this for one field: the plan carries a brick
**hash** rather than a path, because a brick that traversed out with `..`
baked clean, passed `nw-check`, booted, and logged `lid brick` while rooted
on the machine. A fixed-width hash **cannot express** a traversal.

`nwcheck.c:109` names the two fields that have not had the fix: *an
exec_path or a bind whose name resolves through a link escapes just as
cleanly… the fix is to stop carrying free-form paths.*

An audit of the whole format found **two changes, not seven**:

- **`exec_path`** stays a name, and its comment was wrong by omission.
  *Resolved inside the brick, if any* reads as a safety argument and is
  not one. It fails for a no-brick house, where the field is a plain
  machine path. And it fails for **layer shadowing**: resolution happens in
  the overlay, not the sealed lower, so a symlink planted in the writable
  layer over `exec_path` is followed, and a restart execs through it again.
  Content-addressing the brick does not freeze the path the second time.
  *(Comment added; no format change.)*
- **`nw_bind.path`** should become an `O_PATH` on the **source**, opened in
  `nw-spawn` after the per-house fork and passed in the kit. The target
  inside the brick stays a name, because it resolves in a sealed image.
  **Not in PID 1** — one bind per house turns `6 + n` into `6 + n + B` and
  `NW_MAX_BINDS` is 128, which a reserved 8 cannot absorb.

`name`, `brick` and `nw_bind.unit` need no change. `layer` is a name on the
wrong axis — a handle would pin it against a rename and would not give it
crash semantics.

**And a third category the rule does not cover:** `lids`, `kind`, `budget`
are numbers whose interpretation is code. Name-versus-handle does not
apply. Naming the category is only safe under clause 4 above.

---

## 5. What was refuted, and by what

Recorded because the pattern is the most useful thing in this document:
**conclusions survived and arguments frequently did not.**

| Claim | By | Refuted by |
|---|---|---|
| `sched_ext` available for the resource half | Claude | Kernel config: *is not set*, both kernels |
| Drop the parent's caps after fork | Claude | Capability inheritance — restart could not isolate |
| Demand paging makes eager hashing catastrophic | Claude | 68.9% of libLLVM resident anyway |
| `DRIVER_OK` closes the attestation gap | Addendum 5 | Panicked guest reports `DRIVER_OK` |
| Content store has no canonical configuration | DeepSeek | A hash reference cannot resolve to two things |
| Corruption and absence become one symptom | DeepSeek | Three states, three errnos |
| `data=ordered` would not produce that crash | DeepSeek | The mount line says `ordered data mode` |
| Checksum is arithmetic the writer is barred from | DeepSeek | `CLAUDE.md:32` bars allocation, parsing, recursion |
| M1 is the largest TCB reduction available | Modular pass | Already the tree's structure |
| The rule is a bijection | Claude | One mechanism serves many fields |

Two of the most expensive errors were not in any design. A specification
was written without the addenda that already answered it, contradicting
them in five places — and two independent reviews passed over it, because
neither reviewer had those addenda either. And eight tests reported `SKIP`
for weeks and every reader took it to mean *not applicable*.

---

## 6. Other systems, assessed

**Genode / Sculpt.** Structurally has what this design pursues by
discipline: capabilities not paths, budgets that cannot be declared and
unapplied, recursive init with nesting free, no ambient authority, a TCB
small enough to prove. Sculpt 25.10 runs on real PC hardware with drivers
aligned to Linux 6.12 LTS — **Intel only**. No AMD driver, no Linux
application ABI, VM story roughly a decade behind. Its drivers are Linux
drivers re-ported continuously, so adopting it does not leave the Linux
treadmill; it adds a layer above it.

Worth stealing: **resource trading** — a parent allocates quota from its
own and cannot over-commit — and **nesting by default**.

One sharp divergence: Sculpt 24.10 made the construction plan *live*,
rewireable on the fly. This design seals the plan and adopts at reboot.
Both coherent; opposite answers.

**Qubes.** Xen isolation and a mature qrexec policy model worth reading
before specifying any file-transfer mechanism — including QSB-118, August
2026, a dom0 code-execution bug in `qvm-copy-to-vm`'s **error** path. But
dom0 is Fedora, templates are mutable distro installs, `qubesd` interprets
mutable config at runtime, and there is no compile step anywhere.

**Fuchsia.** Capability-routed components with explicit manifests, and
Google's, with no desktop. Starnix reimplements the Linux syscall surface,
which is the treadmill rather than an escape.

**QNX.** One idea better than anything else here: **adaptive
partitioning** — a guaranteed CPU share under contention, redistributed
when idle, declared statically. That is the gaming-district problem solved
without a broker, and cgroup v2's `cpu.weight` already has the behaviour.
Also worth taking: priority inheritance, and bounded-everything as a
discipline. Its path-registering resource managers cut against
capabilities-not-paths.

---

## 7. Open, and untouched

**The network.** The vocabulary has a word — *water* — and nothing else.
It is the hardest instance of every problem here, because a network
destination is a name that resolves at runtime to an answer that changes,
against a database nobody owns. It is the first thing a plan could say
whose meaning is not determined at bake time.

**Where the explanation lives.** Setup failures are held only by the forked
child, which is required to die. `die()` exits 72 at every lid stage. The
parent sees a status; PID 1 sees a supervisor exit. A channel makes PID 1
parse; a second writer on the boot record is barred. The best answer so far
is to split the claim: plan-shaped failures are refused offline by the
checker, where all the facts are; runtime setup failures carry a stage name
and nothing more.

**The reader side of the boot record.** Named as the format's one
load-bearing seam. Four decisions answered — raw records not a projection,
filter only on fields as written, comparison and summary as reads rather
than format properties, stale reads reported as *not in the retained
volume* rather than *never*.

**Store integrity placement.** Bake time catches the bakery's own bugs.
fs-verity at open covers corruption and tampering at bounded cost and is
one config line away. *Nowhere* is defensible only for the threats verity
cannot cover, and should be stated as refusing them rather than implied.

**Sharing across sealed images.** Three options, not two: a declared
substrate, no sharing, or content-addressed file-level sharing. The third
was verified to work — `metacopy=on`, `redirect_dir=on` and stacked lowers
all accepted — and the store's population remains a **union**, not a
resolution, because union has no alternatives to select between. What it
costs is legibility: nothing says *why* an object is in the store, only
that some image named it.

---

## 8. What none of this establishes

Every guest result is Ubuntu `6.8.0-139-generic` under TCG with no KVM, on
one machine. Container results are `6.18.44` in a container. Neither is the
target.

The verity probe sealed a file a house wrote, not an object placed by a
bakery. Who enables verity and when is untested, and the whole store design
turns on it.

The `io_uring` registered-files proposal is unrun.

Nothing here has booted on real hardware.

And the three ground-up designs share one failure mode, predicted
independently by all three authors: a machine that is sealed,
content-addressed, reproducible, and **confidently wrong** — the project's
signature defect promoted from a class to an architecture.
