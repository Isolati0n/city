# nw-init — working rules

Init system for Nexusweave. Linux kernel underneath. A plan is validated
offline, then a runtime table interpreter executes it. Robustness comes from
*where code lives*, not from how much code exists.

## TCB boundary — memorise this

| component | file(s) | language | in TCB |
|---|---|---|---|
| `dawn` (mount stage) | `dawn.c` | C | **yes** |
| `nw-root` (PID 1) | `pid1.c` + `nwcheck.c` | C | **yes** |
| `nw-spawn` (boot spawner) | `nwspawn.c` + `nwcheck.c` | C | **yes** |
| `nw-check` | `nwcheck_main.c` + `nwcheck.c` | C | **yes** |
| `nw-sup` | `nwsup.c` + `lids.c` | C | **yes** |
| `nw-rescue` | `rescue.c` | C | yes |
| baker (`nw-cc`) | `bakery/nw-cc.py` | Python | **no** |
| brick packer (`mkbrick`) | `bakery/mkbrick.py` | Python | **no** |
| test suite | `tests/run.py` | Python | **no** |
| spec | `plan.als`, `Plan.tla` | Alloy / TLA+ | **no** |

Anything added to a TCB file needs a stated justification. Anything that can
live in the baker instead, does.

## Invariants that must not be broken

Everything in this list is **enforced now** — kind 1 under *How briefs are
written* below — and each is checkable against the code as it stands. Rules
that are refused, or that are waiting on a prerequisite, are in the two
sections after this one and are deliberately not numbered here.

1. **No allocation, no parsing, no recursion after start in PID 1.** The blob
   is already validated; PID 1 reads a table, it does not interpret text.
   **PID 1 mounts nothing** — `grep` for `mount` in `pid1.c` returns only
   comments; there is no `mount(2)` call. (This said "one hit" until
   2026-09-11, when there were two. A count in an invariant is a hostage,
   which this file's own rule says and this line disproved twice.)
   `dawn` mounts and hands PID 1 a path; nothing in the TCB below `dawn`
   learns what a filesystem is. The one text PID 1 reads is
   `<slots>/current`, at boot, bounded to `NW_NAME_LEN` and validated to
   `[A-Za-z0-9_-]` so it cannot escape the slots directory.
   **PID 1 has no restart budget and must not grow one** — `grep` for
   `budget`, `restart` or `respawn` in `pid1.c` returns nothing. Budgets live
   in `nw-sup`; see invariant 4.

   Annotated for `tools/checkbrief.py`. `mount(` rather than `mount`,
   because the bare form has hits here and they are the comments this
   paragraph is about — scope an absence to syntax prose cannot produce
   (`docs/checkbrief.md`):
   <<absent-in:pid1.c:mount(>>
   <<absent-in:pid1.c:budget>>
   <<absent-in:pid1.c:restart>>
   <<absent-in:pid1.c:respawn>>
   <<absent-in:pid1.c:malloc>>
   These pin the absences only. "No parsing, no recursion" has no token
   whose absence means it, and is not annotated rather than annotated
   badly.
2. **No compile-time file descriptor numbers alongside dynamic allocation.**
   This produced bugs 5, 9 and 13. Sweep `/proc/self/fd`; do not hardcode.
3. **Limits are derived, never declared twice.** `NW_MAX_UNITS` feeds the
   `_Static_assert` fd budget in `blob.h`. The same *arithmetic* appears in
   `bakery/nw-cc.py`, `plan.als` (`fdNeed`) and `Plan.tla` (`FdNeed`) —
   change one, change all four, or they drift.

   Annotated for `tools/checkbrief.py`, one per site, each naming that
   file's own spelling — the symbol is written four ways and a grep for
   any one of them finds a quarter of it:
   <<count:blob.h:NW_MAX_UNITS * 2 + NW_FD_RESERVED:2>>
   <<filecontains:bakery/nw-cc.py:FD_RESERVED + len(houses) * 2>>
   <<filecontains:plan.als:plus[nwReserved[], 2.mul[#House]]>>
   <<filecontains:Plan.tla:FdNeed == Reserved + 2 * n>>
   and the two second copies that pin the others rather than being sites
   themselves, because losing one loses the only thing checking equality:
   <<filecontains:plan.als:assert FdArithmetic>>
   <<filecontains:Plan.tla:FdNeedAgrees ==>>

   **The brick width is the same class and is annotated for the same
   reason.** `tools/stage-candidate.py` checks a sidecar's brick field
   against `NW_BRICK_HASH * 2` read from `blob.h`. The first version
   spelled `64`, `claims` found it under the docstring forbidding a
   second copy of a limit, and `control` then showed that nothing
   distinguishes the derived form from the literal: replacing the read
   with `64` leaves `make test` green, because every real sidecar
   satisfies either. So the derivation is pinned by text, which is what
   this annotation form can do and all it can do:
   <<filecontains:tools/stage-candidate.py:_blob_h("NW_BRICK_HASH")>>
   It catches the site ceasing to read the header. It does not catch
   the header and the site disagreeing about a value, which is the
   limitation this whole paragraph is about.

   **What that buys, measured rather than reasoned, because the first
   version of this paragraph was wrong.** It said the annotations catch a
   site disappearing and not a site disagreeing, so `* 3` in `blob.h`
   would pass all six. It does not — the annotations name the arithmetic
   *verbatim*, so changing the multiplier in a named file removes the
   named text and is caught. Run:

   - delete the `plan.als` site → `contradicted`, naming that annotation
   - `* 2` → `* 3` in `blob.h` → `contradicted`, exit 1

   What genuinely survives *here* is a disagreement that leaves every
   named string intact, and the sharpest case is a **value**: set
   `NW_FD_RESERVED` to 16 in `blob.h` while the baker keeps
   `FD_RESERVED = 8`, and all six annotations still pass — the
   *expressions* are untouched and only what they evaluate to has
   diverged. Verified: `1 verified, 0 contradicted`.

   **That remains true of the annotations and is no longer true of the
   suite.** `test_baker_constants_match_the_header` compares every
   constant the baker holds against the **compiler's** answer for the
   same `#define`, so exactly that mutation is red now — measured, the
   run says `the baker holds NW_FD_RESERVED = 8 and the compiler says
   16`. It arrived from a different direction: the baker had
   hand-written the scheduler policies, `tcb-review` swapped two of them
   in `blob.h` alone and got a green suite with a plan saying
   `sched=batch` baking to the byte the TCB calls IDLE, and the test
   written to close that closes this too. `drift` ran the fd case
   against it. `HISTORY.md` §75.

   So the honest scope of the ANNOTATION is **text, not arithmetic**,
   and it always was. What pins the
   arithmetic is `FdArithmetic` and `FdNeedAgrees`, each against a second
   hand-written copy in its own file, and what pins the values is
   `tools/gen-spec-limits.py`, which generates them out of `blob.h` so
   the specs cannot hold a stale one, plus the constants test for the
   baker's copies. The annotation covers the third
   thing neither of those does: a site quietly ceasing to exist.

   The *values* are still written in two places — `blob.h` and
   `bakery/nw-cc.py` — and the second is now checked against the first
   rather than merely believed. Both specs read theirs from `specs/limits.als` and
   `specs/Plan.cfg`, generated out of `blob.h` by
   `tools/gen-spec-limits.py`, so those two cells cannot disagree with
   the header — the drift class is removed there rather than checked.
   **Do not count what is left hand-written here.** Two attempts on
   2026-09-11 both got it wrong: "one hand-written number" ignored
   `plan.als`'s `for 8` (written three times, and tracking nothing,
   because that file declares no bound on `#House`); "one that must
   track the header" then ignored the fd *multiplier*. `claims` changed
   `* 2` to `* 3` in both of this header's `_Static_assert`s and both
   specs ran clean — the generator emits the four limit values and the
   two lid bits, and no arithmetic. So the arithmetic really is the
   four-place change the paragraph above says it is, and only the
   values were removed from that class.

   **Say what a number is pinned *against*, or the sentence is wrong
   again.** The bitwidth is the only one pinned against `blob.h`.
   Others are pinned against a second hand-written copy in their own
   file — `plan.als`'s `2.mul[#House]` against `assert FdArithmetic`,
   `Plan.tla`'s `2 * n` against `FdNeedAgrees` — which is a weaker pin
   and a real one: each turns `make test` red on its own. And at least
   one is pinned in neither direction: `Plan.tla`'s `2 * MaxUnits` in
   `LargestCityFits`, which `claims` changed to `* 3` for a clean run.
   The fifth attempt at this sentence over-claimed what was *unpinned*
   after four attempts over-claimed what was *derived*; the fix is the
   preposition, not another count. (`Plan.tla` also hand-copied the lid bits until
   2026-09-11; nothing checked them and their only consumers are
   unchecked predicates, so `LidNewNS == 999` ran clean. Generated now.)
4. **`nw-spawn` exits; its death is not a failure mode.** It forks one
   supervisor per unit, double-forked so PID 1 adopts the houses, reports the
   pids and exits 0. PID 1 requires a complete report *and* a clean exit —
   successful termination is the completion signal, not something to watch
   for. Spawning is boot-time only: PID 1 has no respawn path and restart
   budgets live in `nw-sup`. Do not give the spawner a mid-life.

   The budget belongs to `nw-sup`, where it is **a hard total of deaths for
   that supervisor's life** — `int deaths` in `nwsup.c`, compared against
   `budget`, never reset by a time window.
   <<filecontains:nwsup.c:int deaths = 0>>
   <<count:nwsup.c:deaths++:1>>
   <<count:nwsup.c:deaths > (int)budget:1>>
   <<count:nwsup.c:deaths = 0:1>>
   The last one is how "never reset" is pinned, and it is a **count**
   rather than an absence for a reason: `deaths = 0` must appear exactly
   once, at the declaration. A reset added anywhere makes it two and
   contradicts. An `absent-in` could not express this, and `window_s` —
   the obvious token — has a hit in this very file, in the comment
   recording its removal, so annotating it would report a contradiction
   while the claim is true. `docs/checkbrief.md`. `window_s` left the blob on
   2026-09-11 (`HISTORY.md` §35, D18): deaths slower than the window were
   unbounded, so a house could die forever. Budgets are **never nested**: bug 3 was a
   supervisor giving up, PID 1 restarting it with a fresh budget, and the pair
   looping. One budget authority per unit, and PID 1 is not it.

   *This said "a ring of timestamps, never a counter" until 2026-09-10, then
   "a counter over a sliding window" until 2026-09-11. Both were present-tense
   invariants the code did not honour, and the second was worse than a wrong
   description: the window it described made the budget unbounded. `grep -w`
   for `window_s` across the C sources returns only comments recording its
   removal, in `blob.h` and `nwsup.c` — the same shape invariant 1 uses for
   `mount` in `pid1.c`, and for the same reason: a count here is a hostage. There is no `window_s` field
   and no reset. (This retraction previously claimed the grep returned
   nothing, which the commit that wrote it had already made false;
   `claims` ran it.)*
5. **The init provisions no DESCRIPTORS — and that is the whole of what
   this invariant says.** Every house gets `/dev/null` on 0 and its own
   log pipe on 1 and 2. `close_others` sweeps the rest. There is no third
   thing, and no mechanism for granting one.

   **It does not DROP capability, which is a different act and was
   stated here as if it were the same one.** Nothing in the TCB lowers
   privilege — `grep -nE "setuid|setgid|setresuid|capset" *.c` returns
   nothing — so a house is uid 0 with what it inherited. That much is
   checkable in this tree, which is what this list requires. What a
   house can then DO is invariant 6's subject, and the syscall evidence
   is the operator's: on a real boot (2026-09-13) a house declaring
   `lids=newns` and nothing else ran mknod, mount, unshare and chroot,
   all returning 0.

   **A `lids=none` house is worse than that measurement, not equal to
   it.** `CLONE_NEWNS` appears once in the TCB, gated on the bit, so a
   house with no lids has no mount namespace of its own and those verbs
   act on the CITY's. The first version of this paragraph carried
   "inside its own namespace" over from a `newns` measurement to the
   bare case, which is the one place the qualifier does not hold.
   `claims`.

   What follows from it is invariant 6's business and not this one's:
   the lid set is the only thing that lowers it, and `lids=` is required
   in a city precisely so the height is declared rather than defaulted
   into. (This replaces the pre-2026-09-10
   statement "wiring is non-provision, not enforcement", which concerned
   declared edges. Edges are erased permanently — `HISTORY.md` §17.)

   **A declared bind is not a third thing.** `bind=` makes a path *visible*
   inside a house's brick; the house then opens it itself, with the name it
   would have used anyway, because a bind is the same path inside and out.
   Nothing is handed over. The invariant is about the descriptor table a house
   is born with, and that is still exactly three descriptors.

   **Neither is a writable layer.** Every house with a brick roots in that
   brick plus one writable area, so it can write beneath `/` — and create
   files there too, unless it declares `landlock`, which withholds
   `MAKE_REG` at the root (invariant 6). Either way it opens them itself,
   by name, with no descriptor passed in. The layer
   changes what a house can *keep*, not what it is *given*. Three
   descriptors, still, and `test_brick_is_a_root`'s census asserts it by
   name on a house that has one.
6. **Lids are the only thing that decides what a house can *do*; a brick
   decides what it can *see*, and its layer is what it can *keep*.**
   Seccomp, Landlock and namespaces are applied
   per unit by `nw-sup` before `execv`. A unit with `brick=` also
   `pivot_root`s into it first, so its `/` is its own tree — its own
   libraries and toolchain, at the same paths, invisible to every other house
   and to the machine. That `/` is the brick with the house's **writable
   layer** stacked over it: reads fall through to the sealed image, writes
   land in `/nw/layers/<layer-id>/upper` and are still there when the house
   is restarted. Not opt-in — `brick=` requires `layer=` and the reverse,
   refused in the baker and in `nwcheck.c` (`NW_E_LAYERPAIR`). The layer is
   keyed by a declared id and not by the house name: under name-keying,
   renaming a house hands it an empty layer while its data sits orphaned
   and nothing reports an error.
   <<filecontains:nwcheck.c:NW_E_LAYERPAIR>>
   <<filecontains:nwsup.c:lowerdir=%s,upperdir=%s,workdir=%s>>
   `brick=` forces `NW_LID_NEWNS`; `nwcheck.c` returns
   `NW_E_BRICKNS` otherwise, because pivoting outside a private mount
   namespace repoints the machine's root. Cybersecurity is not a goal;
   containerization applies to apps.

   Order is fixed and is not a style choice: namespaces, then the brick pivot,
   then Landlock, then seccomp. The allow-list has no `mount`, no `unshare`
   and no `pivot_root`, so a house sealed first could not enter its own root.
   <<absent-in:lids.c:__NR_mount>>
   <<absent-in:lids.c:__NR_unshare>>
   <<absent-in:lids.c:__NR_pivot_root>>
   <<filecontains:lids.c:strict_allow>>
   <<filecontains:nwcheck.c:NW_E_BRICKNS>>
   The `__NR_` prefix is the scoping: bare `mount` and `unshare` appear in
   this file's prose and in the comment explaining why they are excluded.
   **The ORDER is not annotated** — it is a property of the sequence of
   calls in `nwsup.c` and no token's presence or absence expresses it.
   `test_brick_is_a_root` and the seccomp test are what pin it.
   There is **one** allow-list and a house does not choose it; the second
   profile that briefly existed is `HISTORY.md` §23.

   **RESOLVED 2026-09-12 by narrowing what this lid CLAIMS, not what it
   does.** It granted read-and-execute beneath `/` and that was never
   what made a brick unwritable: phase 2 established the seal is
   over-determined — the kernel forces read-only when either fd is
   `O_RDONLY` and erofs has no write path — so no flag `nwsup.c` passed
   enforced it. Writable areas then made the root an overlay, and because
   Landlock runs *after* the brick pivot the `/` being restricted **is**
   that overlay: every landlock house got a writable layer it could not
   write. Confirmed live on the first machine with both Landlock and
   erofs — `wr_root=denied(13)`, EACCES from the lid, where a read-only
   image gives `denied(30)`.

   Write is granted beneath the root now, and **`TRUNCATE` is not.**
   No *other* house can see the overlay, so nothing is exposed by it.

   `TRUNCATE` was granted for one round, beside a sentence saying the
   grant gave nothing away, in the same commit that set the cost out in
   full: truncating a file the brick shipped empties it into the
   **durable** layer, and every boot thereafter reads the empty file
   until the layer is deleted and re-staged. `REMOVE_FILE` is withheld
   precisely so the house cannot *unlink* such a file, and truncating
   reaches the same unrecoverable state by another route — so
   withholding one while granting the other is incoherent. That is the
   argument, and the decision is `HISTORY.md` §57.

   **It is narrower than it was first written, in two ways that were
   measured after the sentence was published.**

   *Not its own exec path.* A running executable is `ETXTBSY`, with or
   without `O_TRUNC`, so that scenario — the one the prose led with —
   could never happen by any of the three routes. A shared library the
   brick shipped has no such protection and is the reachable target.

   *Not a restored immunity.* `WRITE_FILE`, which this grant keeps,
   reaches the identical durable state by overwriting a brick file in
   place: measured on a real overlay, eight bytes over an ELF header,
   no truncate and no unlink, and every later mount of the same sealed
   image plus the same layer is unrunnable while the image stays
   byte-identical. Withholding `TRUNCATE` closes the zero-length route
   and the accidental `O_TRUNC` rewrite. **The class stays open**, and
   the recovery in `.claude/rules/runtime.md` is still the only answer
   to it. Making it unrepresentable means stopping the layer shadowing
   the image's executables at all, which is a design change and is not
   this one. `tcb-review` and `claims` reproduced it independently, each
   from the other direction.

   The cost of the withholding, stated rather than discovered:
   `open(..., O_TRUNC)` and `ftruncate` on a file the brick shipped now
   fail with EACCES, so a landlock house rewrites a file in place or not
   at all. A bind is where truncation is granted, and a bind is
   machine-side — outside the layer, so nothing there can mask the
   brick.

   **Only at ABI ≥ 3.** The right did not exist before that, so
   `nwsup.c` does not put it in `handled` and truncation is
   unrestricted on such a kernel. That is a property of the kernel and
   not only of the grant, which is why `test_landlock_confines` branches
   on the ABI and *names the branch in its `ok` line* rather than
   printing the same green for a machine that cannot enforce it.

   Annotated after all, and the first attempt at this paragraph said it
   could not be. `LANDLOCK_ACCESS_FS_TRUNCATE` must stay in `handled`
   (or the right is unrestricted everywhere) and in the bind grant
   `rw`, and must not appear in `root` — which is a claim about *which*
   variables hold it, so neither `filecontains` nor `absent-in` says
   it. A **count** does, because every mutation that changes which
   variables hold it changes how many times the token appears:
   <<count:nwsup.c:LANDLOCK_ACCESS_FS_TRUNCATE:2>>
   `claims` ran both of the controls named below against it and got
   `contradicted`, exit 1, from each — granting it at the root makes
   three, withholding it in binds makes one. That is weaker than
   naming the variables and it is not nothing, and it is the reason
   this paragraph no longer states a count in prose: the count is the
   annotation, where being wrong turns `make checkbrief` red.

   It matters more than usual here because the pin the prose prefers —
   `test_landlock_confines`'s truncate pair — **cannot run on a machine
   without Landlock**, and the annotation runs everywhere. The pair is
   still the real pin, and it is the *pair*: the root half alone is
   satisfied by a lid that granted truncate nowhere.

   **The Landlock lid is for a house in a brick**, and requires one
   (`NW_E_LLBRICK`). It grants read, execute and **write** beneath the
   house's own root — which is the brick with its layer over it, since it
   runs after the pivot — so any linkage works without a list of library
   paths guessed at in the TCB, and the layer is writable as every other
   brick house's is.

   **So what the lid provides is not "the house cannot write". It is: a
   house cannot create, delete, rename or truncate anything beneath its
   root, except inside a declared bind.** The `MAKE_*`, `REMOVE_*` and
   `TRUNCATE` rights are withheld at the root, and granted in binds
   **except for `MAKE_CHAR` and `MAKE_BLOCK`, which are granted
   nowhere** — the exception two paragraphs down, said here so the
   generalisation and its exception do not contradict each other in one
   section. That makes the bind table the policy input for *structure*
   rather than for write. Truncate sits on the structure side because it
   reaches the same durable state as delete, not because it resembles
   one.

   *Not* "cannot reach a path it was not given" — that was the wording
   for one round and it is the **brick's** property, not the lid's. After
   the pivot the reachable namespace *is* `/`, and `test_brick_is_a_root`
   proves it with `lids=newns,seccomp` and no Landlock at all. Claiming
   it here repeats exactly what this section retired: a claim standing
   next to a mechanism already doing the work. What the lid does add over
   the pivot is that Landlock refuses mount, umount and pivot_root for
   any domain, which matters for a house with no seccomp.

   Two exceptions, both older than this change and neither previously
   written down: device nodes are refused **even in a bind**
   (`MAKE_CHAR` and `MAKE_BLOCK` are handled and never granted, which
   `nwsup.c` has said all along), and `REFER` is not handled at all, so
   cross-directory rename and hard links are refused everywhere. The
   first was a written-down exception that nothing exercised until
   2026-09-12; `test_landlock_confines` now makes the same probe answer
   three ways inside one bind — device node refused, fifo and socket
   allowed — which pins the exception and supplies the paired positive
   the root-side refusals lacked. That is narrower than the old claim and it is
   true; the old one asserted both halves of a contradiction.

   A consequence worth knowing rather than discovering: `MAKE_REG` is one
   of the withheld rights, so a landlock house can modify a file its
   brick already contains and cannot create a new one **outside a
   declared bind**. (It said "under `/`", which this change's own test
   contradicts — a bind *is* under `/`, and assertion 3 requires creating
   a file in one to succeed.) Granting `MAKE_REG` at the root is a
   deliberate change here and in this paragraph, not a fix.
   `HISTORY.md` §26 and §56.

   **Lids are not advisory.** If a declared lid cannot be applied, that house
   does not start: every lid path in `nwsup.c` ends in `die()`, never in a log
   line and a return. A house that runs unconfined while the plan says it is
   confined is the plan lying, which is worse than a house that does not run,
   and it is the same defect as a brick that roots on the machine while
   logging `lid brick`. The house then burns its restart budget and stays
   down — deliberately, because a do-not-restart signal would be a second
   meaning on the exit-status channel (bug 9). Everything else boots normally;
   nothing a house does halts the city. `HISTORY.md` §25.

   The honest consequence, recorded because it is load-bearing: **reachability
   has moved out of the sealed plan and into the lid set.** A `lids=none` house
   can open its own socket — nothing structural stops it. `__NR_socket` is
   absent from the `lids.c` allow-list, so a `lids=seccomp` house is killed for
   trying, and that is a live test. The plan no longer says what a house can
   reach; only its lids do.
7. **The live city does not grow verbs.** A new plan is a new slot (A/B), never
   an in-place rewrite. This is now an operational rule only: `NoLiveRewrite`
   was withdrawn from `Plan.tla` when edges were removed, because the
   remaining variables have no runtime mutation path and the predicate would
   have been vacuous. See `HISTORY.md` §17.
8. **CRC32 is diagnostic** (threat model is corruption, not tampering). The
   structural checks in `nwcheck.c` are the actual safety property. The seal
   must be *verified*, not merely read — that was bug 1.
9. **A fold of a live machine's layer goes through
   `tools/fold-house.py`, which establishes that no SUPERVISOR exists
   for the unit — not that no house process is running.** Moved here
   from *Waiting on a prerequisite* on 2026-09-13, when that tool gave
   the rule a subject. The distinction is the rule: "the house is not
   running" is satisfied by a longrun house between restarts, which is
   about to write again, while "no supervisor" is exactly "this unit
   will not run again before the next boot".

   **`bakery/fold.py` IS NOT COVERED AND IS NOT MEANT TO BE.** It is the
   fold engine, it has its own CLI, and it checks nothing: `grep` for
   `scan_environ`, `NotClosed` or `NW_LAYER` in it returns nothing, and
   `claims` ran it against a layer whose supervisor was live with both
   scans non-empty and it exited 0. The invariant is about the *caller*,
   and the engine is unguarded by design so the tree-level tests can
   exercise merging with no privileges at all. The first version of this
   invariant said "a fold" without the qualifier — the kind-3 bullet it
   replaced had carried it, and the promotion dropped it, which is the
   promotion losing the one clause that made the sentence true.

   Two scans, because they answer different questions and neither
   answers both. No process carrying `NW_LAYER=<id>` covers the
   supervisor and the house together — `nwspawn.c` sets it and the house
   inherits it, and `clearenv()` cannot remove it because
   `/proc/pid/environ` is served from the exec-time VMA. No process
   whose `mountinfo` shows that layer's `upperdir` covers a grandchild
   that exec'd with a fresh environment, which the first scan cannot see
   at all: measured, `scan_environ` returns `[]` for exactly the process
   `scan_mountinfo` finds.
   <<count:tools/fold-house.py:_paired_environ_probe:2>>
   <<count:tools/fold-house.py:_paired_mountinfo_probe:2>>
   A **count**, definition plus call site, because that is the mutation
   the record says was green: dropping a pairing from `require_closed`
   while both scans still work leaves the suite passing unless the suite
   supplies a broken scan, which it now does. `filecontains` on the two
   scan names stood here for one round and pinned only that two names
   exist — `claims` gutted both function bodies with the names intact
   and `checkbrief` reported `ok`. Nothing annotatable pins what the
   scans *do*; `test_fold_house_refuses_a_house_that_is_not_closed` is
   what does, and it is red under every mutation named in its docstring.

   **Each scan is an absence and each is paired**, or an empty result
   would mean "closed" and "broken" identically. The environ pairing
   execs a child *with* the variable and requires the scan to find it —
   and it costs a fork, which the design did not expect: setting the
   variable in the helper's own environment is invisible to
   `/proc/self/environ` and would additionally have made every
   subprocess the fold spawns look like a live house. The mountinfo
   pairing mounts a marker overlay in its own namespace and requires
   the scan to see it. Removing either pairing while both scans work
   leaves everything green, so the suite supplies a broken scan on
   purpose.

   **This depends on invariant 1 and the dependence is silent.** A
   supervisor that has exited cannot come back only because PID 1 has no
   respawn path. If PID 1 ever grows one, nothing here starts failing —
   the check becomes a race that reports nothing and the fold
   occasionally captures a live layer. Invariant 1's `absent-in`
   annotations for `respawn` and `restart` are the guard.
   `HISTORY.md` §65, §66 and §73.

## Adding a field to the plan — the mechanism rule

Adopted 2026-09-14, after `nw_res` was found declared in the format, written
by the baker, validated by `nwcheck`, and referenced zero times in
`nwsup.c`. A setting with three signatures on it that does nothing. This rule
exists to make that unrepresentable, and it is written as clauses a reviewer
can check rather than as a principle.

The unit is **the bit, not the field**. `lids` packs four independent
declarations into one `uint8_t`; each bit has its own mechanism, and applying
this rule to the byte would let the whole byte through on one answer.

1. **Every configuration field has exactly one mechanism that makes it true
   at runtime, named where the field is declared.** Name the syscall or the
   file: `cgroup.cpu.max`, `unshare(CLONE_NEWNS)`, a Landlock rule, a
   descriptor the house holds. "The checker validates it" is not a mechanism
   — validation is what refuses a plan, not what makes a statement true.

2. **Every mechanism named in the plan serves at least one field**, and

3. **no field is served by more than one mechanism** — two mechanisms for one
   statement is where the two disagree at three in the morning.

   Note that the converse is false and the rule does not claim it: one
   mechanism legitimately serves several fields. A mount namespace carries
   isolation, path visibility and the bind set; a cgroup carries CPU, memory
   and IO. This is a function from fields to mechanisms, not a bijection, and
   an earlier draft of this rule said bijection and was wrong.

4. **A field whose mechanism is a single translation unit must name that unit
   and have a test that exercises the consumer.** This clause is the one that
   earns its place. An audit of the format proposed exempting "numbers whose
   interpretation is code" — `lids`, `kind`, `budget` — and that class, as
   stated, also contained `nw_res`, the defect this rule exists to prevent.
   A category defined by what it lacks is a hole. Defined by what it
   requires, it is a contract.

5. **Descriptive metadata is a separate syntactic class and is not a
   configuration field.** A build timestamp is inert and should be. The
   disease was never inertness; it was a field whose class was ambiguous at
   review time.

**And one obligation that is not a format rule, because it cannot be checked
by reading the plan.** Every configuration field must have at least one test
that *runs* and fails when its mechanism is absent. **A `SKIP` is a failure,
not a neutral outcome.** Eight tests in this suite reported `SKIP` for weeks
on a missing `mkfs.erofs` and every reader took it to mean *not applicable*
rather than *no evidence* — among them `landlock-confines`, so the main
isolation mechanism had never executed anywhere the project ran. That belongs
in the test plan and is stated here because this is where field authors look.

*Renumbered 2026-09-10: the old invariant 7 left the enforced list (see
Waiting on a prerequisite, below), so old 8 → 7 and old 9 → 8. Invariants 1–6
kept their numbers. A pre-2026-09-10 reference to "invariant 8" means the
live-city rule, now 7; there were no references to old 9.*

## Refused deliberately

Statements here are decisions not to build something. They are not gaps and
not backlog. Reopening one is a design decision, and the reasoning is recorded
where the work would land so it cannot be undone by someone being helpful.

- **Freeze detection.** A house that goes silent but never exits is undetected
  by anything. Every form of detection needs a guessed constant, and the rule
  has been attempted and wrong every time. See the Liveness section of
  `.claude/rules/runtime.md`, which carries the full reasoning. (It was
  `.claude/agents/` until the territories became rules; the path here
  went stale in the same move.) (It was in
  `supervisor.md` until 2026-09-10; that brief merged into `runtime.md`.)
- **Nothing a house does halts the city.** Exactly two things halt it: the
  plan fails validation at boot, or PID 1 dies. The `critical` flag was
  removed rather than repaired — `HISTORY.md` §19.

## Waiting on a prerequisite

Statements here are real rules with no subject yet. They become enforceable
the moment the prerequisite lands, and they are recorded so nobody has to
rediscover them.

- **Authoritative state never auto-restarts on an integrity fault.** Inherited
  from `HISTORY.md` §11 and still correct. It has no subject today: there is
  no persistent state anywhere in the design, and no integrity-fault channel —
  `grep` for `integrity` or `authoritative` across the **C sources** returns
  nothing (it appears in prose, which is why the scope matters),
  and `nw-sup` handles every nonzero exit identically. Both prerequisites are
  what `docs/options/05` Q4 exists to answer. **This becomes enforceable the
  moment storage lands**, and it belongs back in the numbered list on that
  day, not before. It left the list because a list of enforced invariants that
  contains an unenforceable one teaches a reader that the list is decorative.

## Build and test

```
make            # all binaries
make stage      # stages to /tmp/nw-init-run
make test       # stage + install-agents.sh --check + nw-check
                # + tests/run.py + bakery/test_fold.py
                # + tools/coverage-tcb.sh (the floor is the script's own
                #   default -- read it there, not here)
                # Read the target, not this line: the fold suite was
                # missing from it for as long as the list was written
                # by hand, and this list exists to be run step by step.
make proof      # the CBMC proofs of the validator, and their controls
```

`tests/run.py` boots via `unshare --pid --fork --mount-proc` so `nw-root` is
genuine PID 1 and orphan reaping is actually exercised.

`make proof` needs `cbmc` and is **tens of minutes**, dominated by
`leaf_name_dup`, so it is not part of `make test`. Do not budget for it
from this line — read the per-proof seconds the script prints — and do not
run it beside `make test`: CBMC is killed under CPU or memory pressure,
and a full run contending with the suite aborted in that same proof on this
machine. `proofs/run.sh` refuses a run that produced no result line rather
than reading it as a pass, so a `NO RESULT LINE` means it did not finish,
not that a property failed. (This said "minutes rather than seconds", which
reads like two or three; `claims` and `fd-auditor` both timed it.) When `cbmc` is absent, **`proofs/run.sh` exits 3** and
says SKIP rather than passing — `make proof` reports that as `Error 3` and
exits 2, so a caller that needs to tell SKIP from FAIL must run the script,
not the target. (This paragraph said `make proof` exits 3 until 2026-09-11;
`claims` ran it and it exits 2. The distinguished code exists exactly so a
caller can tell the two apart, and through `make` no caller could.) **A proof kept outside the tree is a sentence** — that is the
rule `proofs/` exists to enforce, and `HISTORY.md` §28 is why. Read
`proofs/README.md` before quoting a result from it: every run there is
bounded to a small number of units, and a bounded proof reported without its
bound is a kind-1 statement that is not checkable.

## Delivering work: the trunk is `main`, and a tarball is not a delivery

**This applies to every agent working on this repository, whichever model
you are.**

1. **Pull before you start.** `git fetch origin && git log --oneline
   origin/main -1`. Another agent may have landed since your last look.
2. **Push when you finish.** Work that exists only in your sandbox does not
   exist.
3. **Deliver as commits on `main`.** Not as a tarball, not as a patch in a
   message, not as a branch you leave unpushed.
4. **State the commit you based on, in every report.** One line, the short
   hash, at the top.

This is a rule because it has already cost a divergence. On 2026-09-11
there was no agreed trunk: `origin/main` was at
`1974a08` (local `main` was further along and unpushed, which is the same
defect in miniature), the pushed branch carried everything since, and a
full PID 1 stay-up pass — production reap loop, `RB_POWER_OFF` shutdown,
a budget fix, the shutdown-restart race, a `make qemu` target — existed
only inside a tarball based on `d84da58`. Each place had a part of the
work and none had all of it, and **none of that was visible until someone
cloned the repository and looked.** A tarball hides divergence in a way a branch
does not: nothing about it shows up in `git log`, `git status`, or a
fetch.

The branch half is resolved: `main` was fast-forwarded to it — no rebase,
no squash, no force-push, deliberately, because the tarball names its
base by hash and rewriting history would have stranded it.

**Both halves are resolved.** The tarball was merged as `2ed4a45`, whose
commit message names `4e11c20` as the base it was written against; the
reap loop, the `RB_POWER_OFF` shutdown, the budget fix, the
shutdown-restart race and `make qemu` are all in the tree, and
`HISTORY.md` §36 records the landing.

The rule stands because of what it cost, not because anything is still
missing. *This paragraph said the opposite until 2026-09-11 — that none of
that work was in the tree and it was "one agent's next task" — and it named
`grep` for `RB_POWER_OFF`, `reboot(` or `qemu` as the evidence. Run
verbatim, that grep returns hits in `dawn.c`, `pid1.c` and the
`Makefile` -- line numbers deliberately not quoted, because the ones
this sentence gave went stale within one commit when `sync()` shifted
them, and `dawn.c` was missed off this list for exactly as long as the
list was written from memory rather than run. `claims`
found it. A stale sentence here is worse than elsewhere: it instructs, so
an agent that believes it re-does work that is already committed. Note
what it is *not* replaced with — a new present-tense claim about what
remains outstanding. That is the form that has now been wrong twice.*

### Who owns which file

**`MAP` in `tools/rules-hook.sh` is the map. Do not write a second one
anywhere.** Attempts to keep one here are named and dated in
`docs/POSTMORTEM-rules-declaration.md`, which is where the tally belongs;
the ones worth knowing by name are `1ac0235`, the tool that derived
ownership by substring-matching filenames out of a `Scope:` line and
un-owned the whole boot chain when that line was rewrapped, and `e62c6a6`,
which put a machine-readable declaration in each rules file with a gate
comparing it to the prose and reproduced two of `1ac0235`'s bugs.

**No comparator, and the argument does not rest on counting commits.**
Comparing a map to prose means parsing prose, which is what `1ac0235` did.
And a comparator earns its place by catching a divergence between two lists
somebody maintains for their own reasons — a second list here would exist
only to be compared, so it would be maintained by the comparator's
complaints rather than by anyone needing it. That is one list written
twice, plus a gate that makes you write it twice.

*An earlier version of this paragraph argued the same point from a commit
count, and `claims` disproved it: the count was wrong, and the commit that
introduced the sentence changed `MAP` without touching `.claude/rules/`, so
the claim's own commit was its counterexample.*

**What replaces the comparison is an absence check against reality.**
`sh tools/rules-hook.sh --check` enumerates the tracked code files and
requires each one to be classified into a territory or listed in `UNOWNED`
with a reason; a blank reason is refused. It runs inside
`install-agents.sh --check`, which `make test` runs before the suite,
because the hook's event path exits 0 always and cannot refuse anything.
The check and the delivery share one `classify()` — a check that re-read
the map from outside would be the dropped attempt's own title, "the hook
never read the map it claimed to", with the parties swapped.

**It DOWNGRADES the failure rather than removing it, and that is the honest
claim.** What it catches is a code file nobody has thought about: added,
tracked, owned by no territory and named in no exemption.

**It does not catch a file owned by the WRONG territory, and no check can
without becoming the thing this decision refuses.** `control` moved most of
`MAP`'s members to another territory one at a time and the target stayed
green for all but the few the anchors pin — an agent editing `dawn.c` would
be handed `plan.md`, and both the gate and the suite print OK. The fix that
suggests itself is an assertion listing where each file belongs, and that
assertion is a second copy of `MAP`: the two-lists problem, arriving as a
test. Checking an assignment needs a second opinion about the assignment,
and a second opinion is a second list. So this is residue, deliberately,
and the anchors exist to catch a classifier that has stopped working rather
than a map that is wrong.

What it also does not catch is a file that is owned and *undescribed*, and
there is more than one. Read them off the tree rather than a list here:

    for t in $(sh tools/rules-hook.sh --territories); do
      for f in $(sh tools/rules-hook.sh --owns $t); do
        grep -qF -- "$f" .claude/rules/$t.md || echo "$t: $f"
      done
    done

`lids.h` is the oldest and the one `1ac0235`'s message flagged when it said
"the whole boot chain" was false because `lids.h` was claimed by no scope.
Most of the rest were added to the map by the change that wrote this
paragraph, which is why the singular it first claimed was wrong on the day
it landed. The rules are delivered for every one of them and the prose is
silent about them, and the census says OK because ownership is all it asks
about. The residue is a
documentation gap, and **nothing checks prose** — deliberately, because
checking prose is what attempt three did.

Further limits, stated rather than discovered. The census cannot see a file
that has not been `git add`ed, because its input is `git ls-files`.
`attic/` is skipped, which is a second exemption channel beside `UNOWNED`
and carries its reason where the skip happens. And measuring "undescribed"
by whether a rules file names the basename over-reports: every `houses/*.c`
fixture comes back undescribed, and `harness.md` says in as many words to
read `houses/` rather than any list written down in it. That is a
deliberate non-description, not a gap.

**A file may be owned by two territories**, declared in `SHARED` beside
`MAP`, and the hook then delivers both rules files with suppression still
keyed per territory. `tools/stage-layers.py` is the case: `plan.md`'s
sidecar rules and `runtime.md`'s THE RECOVERY are both statements about it.
Leaving such a file unowned silences both, and first-match silences one
invisibly, so two owners has to be declarable — and declared, because
`--check` refuses a name in two sets that `SHARED` does not name.

What the map does not settle, because it is not a map question:

- **Territories are not workstreams, and the collision is inside one.**
  `runtime` owns the boot chain, so the collision is not two territories
  meeting at an edge — *bricks* and *lifecycle* are concurrent
  workstreams that both land in that single territory, and specifically
  in `nwsup.c` (which holds `lid_brick()` and the bind mounts beside the
  restart budget and death handling) and `nwspawn.c` (which never mounts,
  and hands the brick and binds on through `setenv("NW_BRICK",…)` and
  `NW_BIND_n` beside the budget handoff and the mid-fork reap). **A
  territory map does not stop two agents colliding inside a territory.**
- **`bakery/nw-cc.py` cannot move out of `plan`.** Invariant 3 makes a
  limit change atomic across `blob.h`, the baker, `plan.als` and
  `Plan.tla`; `plan.md`'s scope names all four, and any split puts a
  required-atomic change across a boundary.

**Who owns what, this week.** Grok owns `pid1.c`, `dawn.c` and the
restart loop in `nwsup.c`. Claude owns the baker and the mount path in
`nwsup.c`. Nobody else touches `plan.als` or `Plan.tla`.

**Fix it and flag it — and the narrow version is the rule.** When a
handoff breaks the boot, fix it in the other agent's file rather than
handing it back. A trunk that panics on real hardware is worse than an
ownership violation: the boundaries exist to stop two agents colliding,
not to protect a broken trunk. Ratified 2026-09-11 after a fix delivered
for `dawn.c` panicked on the first real boot and was repaired across the
line.

The narrow version and the general version look identical in a commit
log, which is why the distinction is written down rather than left to
judgement. Narrow means: **the trunk is broken, the fix is minimal, you
say so loudly, and the owner reviews afterwards.** It is not a licence
to edit another agent's files because you were there and it was quicker.
If the trunk still boots, hand it back.

*The finding underneath this matters more than the rule.* The break was
a comment that had named the wrong errno for years — `umount2` answers
EINVAL on the MS_MOVE path, never the ENOENT the comment claimed — and a
reviewer's instruction to make comment and condition agree was followed
by trusting the comment. That is this project's characteristic failure
with teeth for the first time: previously a true-looking sentence
misled a reader, and this time it panicked a machine.

The ownership assignment above is deliberately not derived from
anything. It is a **scheduling fact about who is working on what**, not a
property of the code, so it changes when the work changes and a generated
list will always be either stale or wrong. Every attempt to produce it
another way has been wrong — twice written by hand, once derived by a
tool, each found by `claims` — which proved the point the expensive way.
Edit the sentences above when the assignment moves; do not build a
mechanism. (This said "three attempts to compute it" and "edit these
three sentences": two counts, in the file whose own rule is never to put
a count in a brief. Only one attempt computed anything, and the second
count goes wrong the moment a fourth owner is added, which is the one
thing this paragraph exists to invite.)

## Dispatching agents

`sh install-agents.sh --list` prints the roster, derived from the brief
files themselves; the dispatch table below is repeated here because this
file is always loaded and the briefs are not. (`--list` printed a
hand-written roster naming three dispatchable territories and a `repro`
agent that no longer exists, for a day after this file said territories
are rules. A second copy of a roster rots the same way a second copy of
a limit does. `claims`.)

**Dispatch before you push, not after.** Both HIGH findings of 2026-09-10 —
the `..` traversal and the profile that killed compilers — were found in code
that was already committed and pushed, because the reviewers ran afterwards.
Same tokens, same findings, different blast radius. `tools/review-gate.sh
--check` makes this mechanical: it fails while a review is owed, keyed to the
*content* of the files under review, so reviewing and then editing does not
count. Record a completed review with `--record <agent>`.

| when | dispatch | why |
|---|---|---|
| a TCB file changed | `tcb-review` + `fd-auditor`, in parallel | read-only, independent, cannot break anything |
| a test was added or changed | `control` | the negative controls, run mechanically instead of by hand |
| a limit or the blob layout changed | `drift` | invariant 3 otherwise depends on someone remembering |
| a brief, this file, `.claude/rules/*.md`, `HISTORY.md`, or an environment claim changed | `claims` | kind-1 statements rot silently, and a diff of pure prose is not a safe diff — every such round here has come back with findings |
| a speed or scale claim was made | `measurement` | never report a single sample |
| any diff, before the first push | `make prereport` | not an agent; five shapes that have each cost a round |
| a numbered invariant here changed | `make checkbrief` | not an agent; verifies the annotations, and exits 1 when the tree contradicts one |

**`make prereport`'s calibration number: four, on `4e22204`'s diff.** Keep
a number here and change it when the patterns change. A heuristic tool
without a stated expectation is unfalsifiable in use — you cannot tell a
clean run from a broken matcher. The `which`-matches-English false
positive that shipped in the first version was found *only* because the
expected count was four and the run said seven; three rounds of using it
had not surfaced it. A calibration is a count kept on purpose; so is
the `five shapes` in the table above. Neither is exempt for being
special — they are the ones worth the hostage, and `five shapes` is the
weaker, because **no run prints it**, so being wrong there is silent. A
calibration is falsifiable only because a run prints the number beside
it.

**Re-measure it with the target, giving it the range:**

    make prereport PREREPORT_BASE="4e22204^ 4e22204"

which reproduces `4`. Two rounds got this wrong the same way — first
"`4e22204` predates the target so the number cannot be re-measured",
then "the target diffs the working tree so it cannot address a
historical commit, but the script can". `PREREPORT_BASE` is `?=` and
substituted unquoted, so it takes a range. The second version diagnosed
the first as reasoning about a target without running it, and then
reasoned about the target without running it. `HISTORY.md` §63.

**`prose-count` is nearly blind on markdown, and the missing word is
not why.** Adding `hit` to `COUNTED` changes nothing — control run.
The gate is `_is_comment()`, which passes only lines whose first
non-space character is `#` or `*`, plus anything containing a C comment
marker anywhere. Most of this file is ordinary wrapped prose and is
never examined; the measured proportion is in `HISTORY.md` §63, not
here. Whether a count in a brief is caught depends on where the line
wraps.

**There is a further blindness beside the gate, and widening the gate
does not touch it.** `COUNTED` is a vocabulary, so a count of a noun it does not list
is invisible even on a line the gate passes: `two checks` and `three
files` match, `two constants` and `three greps` do not. Both of those
went into the falsifiable-surface paragraph and `prereport` returned
`no shapes matched`; `claims` found them by reading. So a clean run
says a count is not there in a shape it knows, and nothing more — the
gate decides which lines are read and the vocabulary decides which
counts exist. Not fixed here, for the reason the next paragraph gives.

A mechanism reading as working, in the tool bought to catch those. Not
fixed in the round that found it — a matcher change is its own change.
The sequence: widen the gate, re-measure with the command above, write
the new number down, dispatch a reviewer.

**Run `make prereport` before the FIRST PUSH, not before the report.**
That is a change from 2026-09-13 and it is the cheaper end of the same
tool. Its shapes are ones a reviewer would otherwise find, so running it
after the work is done and before the reviewers go out moves them from
round two to round zero. Running it last means the reviewers spend a
round on what a build step already knew.

It also has to be the LAST thing before the push rather than the last
thing before the prose: `dee1516`'s message said `prereport clean` while
`make prereport PREREPORT_BASE="dee1516^ dee1516"` reports
`HISTORY.md` lines that same commit added, because the gates ran, then
the HISTORY sections were written, then it was committed. A gate result
is about the tree that existed when it ran.

**And a clean final run is what makes this compatible with the review
gate.** `tools/review-gate.sh` keys on file CONTENT, so an edit prompted
by that last `prereport` invalidates a recorded review and re-owes it.
If the final run is not clean, the fix costs another review round —
which is the cost this rule exists to avoid, arriving from the other
side. `claims`.

It reads the diff and asks five questions that have each cost a
review round here: a comment claiming a mechanism is load-bearing, an
absence assertion with no paired positive, a count in prose, a capability
inferred from an installed tool, and a new test with no control language
beside it. It **exits 0 always** — a heuristic wired into a build gets
routed around within a week, and then the signal is gone rather than
merely ignored. Acks live in `.prereport-ack` with a reason each, and an
ack is a claim that someone looked, not that anything was fixed.

It is a heuristic and it is wrong sometimes; its value is that being wrong
is cheap and being silent is not. On its first run here it found a TCB
comment still carrying a claim a reviewer had falsified — corrected in one
file and not the other, which is this repository's most common defect —
and it found a false-positive class in its own source that its handover
note described as already fixed.

**Always build the packet first: `sh tools/review-pack.sh`.** It writes a
file and prints the path; the dispatch prompt tells the reviewer to read that
path. Not optional — a reviewer sent to "go and look" spends most of ~90k
tokens rediscovering the repository, and the packet is the diff, the TCB
files it touched and the suite's environment block, already assembled.

Give the reviewer the *path*, never the packet's contents: piping it into the
prompt moves the cost into this context instead of removing it. That is
exactly why the first version of this script went unused the one time there
was an opportunity to use it.

**Every reviewer shares one reporting contract:** a finding carries the
command that shows it and that command's verbatim output, or it is labelled
`HYPOTHESIS`. There was a separate `repro` agent for this and it was never
dispatched once, because the discipline belongs inside the reviewers rather
than beside them.

### Territories are rules, not agents

`plan`, `runtime` and `harness` were dispatchable briefs until 2026-09-10 and
were dispatched **zero times**: the changes here are cross-cutting, so the
authoring happens in the main thread where the whole picture is. Their
content is still load-bearing — the liveness refusal, the seal rules, the
staging trap — so it lives in `.claude/rules/` and `tools/rules-hook.sh`
delivers it on a `PreToolUse` for any file in that territory. Reference read
at the moment it applies, rather than a worker spawned to do the work.

What that leaves is the shape the evidence supports: **read-only reviewers
that fan out, plus rules that arrive when they are relevant.** Reviewers are
the part that pays — they need no shared context and they cannot break
anything.

**Edit a territory file with `Edit` or `Write`, not a Bash heredoc.** The
hook matches `Bash` too and will search a command string for a territory
filename, but that is a backstop and it is guessable-around. The dedicated
tools name the file, so the match is exact.

This is worth a rule because the first version of that hook matched only
`Edit|Write`, worked perfectly, and **never fired once** — nearly every edit
here goes through Bash with a python heredoc, so it never matched. A
mechanism that is correct and routed around is worse than a broken one: it
looks like it is working. The diagnosis was wrong too, and wrongly confident
— "settings load at session start" was inferred from a missing stamp and
reported as the cause without testing it. Hooks and agent definitions both
refresh live; that was checked afterwards, by running a probe.

## How briefs are written

`CLAUDE.md` and the agent briefs in `.claude/agents/` are read by agents that
**act on them**, not by people who can tell aspiration from fact. Nearly every
defect found in the 2026-09-10 brief audit came from one cause: a statement
that read as fact was actually a refusal or a plan, and nothing marked which.
Liveness, scale testing and the integrity-fault rule were all found this way,
each separately, each costing a round trip.

So every statement in a brief is exactly one of three kinds, and it must be
written so a reader can tell which without checking:

1. **Enforced now.** The code does this today. **A statement of this kind must
   be checkable against the code as it stands** — if you cannot point at the
   file and line that makes it true, it is not this kind. Write it in the
   present tense and expect it to be verified.
2. **Refused deliberately.** A decision not to build something. State the
   refusal, the reasoning, and that reopening it is a design decision rather
   than an implementation task. Do not delete these: a reader who finds no
   mention will assume nobody considered it and propose building it.
3. **Waiting on a prerequisite.** A real rule with no subject yet. Name the
   prerequisite and where the decision lives. Keep it out of any list of
   things that are enforced.

Two habits follow from this:

- **Never put a count in a brief.** Not the number of checks, tests, difftest
  inputs, or bugs. A count is a hostage to the next commit, and this file's
  record is mostly counts that went stale. Point at the code or quote the
  suite's own output. A bug *identifier* (bug 5, bug 13) is a name, not a
  count, and is fine.

  **A number saying how many instances of something exist is worse: it is a
  claim about how hard someone looked, not about the tree.** Name them and
  the number beside them is redundant but checkable, which is fine. Without
  names, give no number. Two counts are kept anyway and argued for where they
  live — the `prereport` calibration, which a run prints beside itself, and
  a count inside a test assertion, where being wrong makes something fail.
  Neither is an instance-count.

  *(The sentence above was first written as "the only honest forms are a
  named list or nothing", inserted directly in front of "has been wrong
  three separate times in one day" — an unnamed instance-count, in the same
  bullet, which the tree cannot verify. It also contradicted both stated
  exemptions. `claims`. The unverifiable count is gone; so are the others
  the rule implies, which nobody had swept for.)*

  **And a count survives review in a way a wrong fact does not.** On
  2026-09-12 a report listed three annotatable invariants correctly and
  then summarised them as four in the next sentence; the reviewer read
  both, replied "the four annotatable invariants", and the wrong number
  was now in two places written by two parties. Neither counted. A
  reviewer checks claims against the code, and a count is not a claim
  about the code — it is a claim about the list directly above it, which
  is exactly the thing a reader's eye slides over because it was just
  read. **That is what makes a miscount durable: not making it, having
  it repeated.** Every other defect in this file's record was found by
  somebody re-running something; this one round-tripped through a
  reviewer whose job was to catch it.

  **An ORDINAL is worse than a count.** It asserts an ordering as well
  as a number, so it can be wrong in two ways, and neither is checkable
  without re-reading everything it counts — which is the work nobody
  does. `HISTORY.md` runs several such series and they have drifted;
  `HISTORY.md` §62 and §63 hold the survey, including one that lives in
  a rules file the hook delivers while its series is numbered in a
  different document — the same failure with an extra hop. **Name the instance; do not number
  it**, and when you go looking for a series, grep for the *pattern
  name* in every spelling it has been written in — two successive
  surveys here found more each time by widening the spelling, which is
  why no total is given.

  **Counts and their invalidators can be FAR APART**, and the distance
  is what defeats re-reading. Three shapes, none of them the only one:
  a heading, above an addition and out of view by the time it is
  written; an ordinal series spanning sections, where the missing
  member is adjacent to nothing; and a correction applied to the top of
  a section and not its foot.

  **Do not put a number in a heading that can change.** The test is
  whether the thing counted can grow: a date cannot, nor can a number
  naming a split that already happened, so `plan.md`'s "Why this is one
  territory and not three" and `runtime.md`'s "and not two" stay.
  `HISTORY.md` is exempt throughout as a record. The sweep must be
  case-insensitive and spell the words out; its alternation stops at
  twelve, so extend it if a heading ever needs more:

      grep -niE "^#{1,6} .*\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|[0-9]+)\b" CLAUDE.md .claude/rules/*.md

  Read each hit for which kind it is; the grep cannot tell them apart.
- **When something is removed, re-file the rule rather than deleting it.**
  Move it to kind 2 or kind 3 with the reasoning intact. A rule deleted is a
  rule someone re-derives badly later.

- **But when a SENTENCE is wrong, delete it. Do not retract it in place.**
  That is the opposite instruction to the one above and the distinction is
  the point: a *rule* re-files, a *false statement* goes. A retraction is
  new prose, and new prose carries new claims — a distance in lines, a
  count of characters, an "only place" — so retracting in place trades one
  wrong sentence for a smaller wrong sentence and grows the file. Four
  review rounds on one passage here found their defects almost entirely in
  the retractions rather than in what was retracted, and the fix that
  finally worked was deleting rather than rewording.

  Keep the correction only where a reader would otherwise re-make the
  mistake — the retractions still in this file earn their place that way,
  and each is the shortest form that does. When the reason is already in
  `HISTORY.md`, the brief gets the corrected sentence and nothing else.

  *This bullet's own change was four deletions and no rewording, which is
  the check it prescribes run against the diff that introduces it.*

- **PROSE GETS ONE REVIEW ROUND.** Not four. This file reached that
  conclusion and then the next prose change took four rounds anyway,
  which is the weakest-rule pattern applied to a rule about rounds.

  The rule: dispatch `claims` once. A second round is still worth
  running; what changes is what you may do with its findings — and that
  splits exactly the way the two bullets above split it, which the first
  version of this rule flattened.

  A **false statement** a second round finds is deleted, not reworded.
  That is where the measured evidence is: across the rounds on one
  passage the retractions accrued defects and the rule being corrected
  did not.

  A **rule** a second round finds is re-filed to kind 2 or kind 3, with
  the reasoning intact, exactly as the bullet above requires. Deleting a
  rule because a reviewer found something in it is the instruction this
  file spends two bullets forbidding, and the first version of this one
  prescribed it for everything. `claims` caught it in the round that
  introduced it.

## The characteristic failure

**This project does not produce crashes. It produces a true-looking sentence
sitting next to code that does not do what it says.** Every defect found here
so far has that shape, and knowing the shape is most of the defence.

The record, which is the argument:

- **Bugs 4, 9 and 13** were silent wrong routing. Not one returned an error.
  Descriptors went to the wrong place and every process reported success.
- **D11** was a TERM handler that could never fire, installed on a blocked
  signal, sitting beside a comment saying the supervisor handled TERM. The
  city shut down "gracefully" by timing out into SIGKILL.
- **`supervisor.md`** specified a liveness rule in the present tense. Nothing
  in the tree had ever implemented it.
- **`baker.md`** claimed typing, ordering, cycle detection and capability-flow
  analysis. None had ever existed, and after §17 none were even definable.
- **`lids.c`** stated as fact that its build profile carried "what a compiler
  and a build driver need" and that "without them any compiler dies
  instantly". It killed `gcc` on the first `exec`. `HISTORY.md` §23.
- **Invariant 4 in this file** said the restart budget was a ring of
  timestamps, never a counter. It is a counter. `grep -w` for `ring` across
  the C sources has always returned nothing — and it needs the `-w`, because
  a plain `grep` matches `string` in a dozen places, which is how a check
  that looks decisive gives the wrong answer. The replacement description
  ("a counter over a sliding window") was then wrong in a way that mattered:
  the window made the budget unbounded. Three tellings, two wrong.
- **`path_ok_len`** validated a path that could contain `..`, under a comment
  and an invariant both asserting that a house cannot see outside its brick.
  A traversing brick baked clean, passed `nw-check`, booted, and logged
  `lid brick` while rooted on the machine. *The guard added for it is still
  there and still required — `exec_path` and binds are paths. Its **brick**
  case was deleted on 2026-09-12, and that is not a regression: phase 3 made
  a brick a 32-byte hash, which cannot express a traversal, so the input
  class went rather than the check being dropped. `HISTORY.md` §51 carries
  the argument, because a deleted security check with no record reads as
  exactly the thing this list is about.*
- **A test said it planted a hash collision and did not.** Its probe asked
  `nwcheck.c`'s real hash and then applied the slot mask itself — a second
  copy of an expression that also lives in `name_dup`. Change the derivation
  in one and the other answers for the old one: the pair no longer collided,
  the case that exists to be controlled stopped being controlled, and the
  line still read `a real collision on slot 80`. `HISTORY.md` §30. The same
  round found the packet handed to every reviewer carrying an **empty**
  environment block, because its fallback only ran on failure and `sed` on
  `/dev/null` succeeds.

**A recovery mechanism turns a defect into a delay, and a test that asserts
final state cannot see a delay.** This is a *variant* of the shape above and
it is worth separating, because every case before it was a claim the code
contradicted, and this one is a correct mechanism working exactly as designed
while concealing a defect behind it.

Phase 2 shipped with `LOOP_CTL_GET_FREE` treated as if it reserved an index.
It does not, so concurrent brick houses collided and all but one got `EBUSY`.
The restart budget then did its job: the loser restarted and succeeded. Two
houses produced one failure on *every* run of the suite and the suite printed
`ok`, because the test asserted the end state and the end state was correct.
At eight houses the budget ran out and houses vanished; at `NW_MAX_UNITS`
most never attached — and the city still printed its close line saying
every house was reaped and nothing was orphaned.

*No figures, deliberately. The ones that were here did not reproduce: a
race measured under one load is not a measurement, and `claims` re-ran the
eight-house control and got a different spread from the one the correction
had put here. `HISTORY.md` §50.*

Nothing lied. The budget is a hard total and behaved like one; the test
asserted what it said it asserted. The defect lived in the gap between them.

So the question to ask of **every retry, every budget, every fallback** in
this tree is: *what does this convert a failure into, and would a test that
checks the outcome still see it?* If the answer is "a delay" and "no", assert
the intermediate — this suite now asserts zero `EBUSY` and zero restarts, not
merely that every house ran. The second-order cost is the reason it matters:
a budget spent on a transient race is a hard total a longrun house no longer
has for a real crash.

Corollaries, earned the expensive way:

- **Put the test at the scale where the defect is visible, not the smallest
  scale that reproduces the mechanism.** Two houses collide, but the budget
  hides it; the test is pinned where it shows instead — read the number off
  `test_many_brick_houses_all_start`, not off this line. A test at two would
  have gone green against the broken tree.
- **Refuse a proposed check that cannot fail for the reason it names.** A
  reviewer suggested an fd census would catch a dropped `O_CLOEXEC`. Both
  controls were run: dropping `O_CLOEXEC` passes, dropping the `close()`
  calls passes, because either alone keeps the table clean. That is correct
  redundancy, and saying so beats inventing an assertion that appears to
  separate them.

Notice what is common. In every case the code was memory-safe, the tests were
green, and the prose was confident. Nothing was reviewing the *relationship*
between the sentence and the behaviour, because reading them together is
exactly the thing that feels like it has already been done.

**So: a sentence describing behaviour is worth nothing without a test that
fails when the behaviour is removed.**

That is the whole rule, and it applies to comments, to briefs, to this file,
and to commit messages. Write the sentence if it helps a reader — but the
sentence is not the evidence. The test that fails without the mechanism is the
evidence, and until it exists, the behaviour is a hypothesis however carefully
it is worded.

**And that rule goes quiet exactly where writing escapes scrutiny**, because
removing the behaviour requires a behaviour to remove. It does not fall
silent — it returns the same verdict, *hypothesis*, for everything unwritten,
which is the same as ranking nothing. A careful specification and an
unfalsifiable one score identically under it, so neither is inspected.
("Nothing to say about it" stood here for a round and contradicted the
sentence closing the section above, which says in as many words that such
writing is a hypothesis. The retraction first gave that sentence's distance
in lines and got it wrong — a positional count, in a file that records
positional counts as a shape it retired. `claims`, twice.)

The test that applies earlier: **could any existing thing falsify a single
sentence of this?** Ask it of a design note, a plan, a critique, a set of
expectations — anything written before the thing it describes.

It is *not* "how far ahead of the implementation is it", which is a weaker
question and a different one. A document can be a year ahead and still name a
constant, a file, or a grep whose answer would refute it; another can describe
next week's work and rest on nothing checkable at all. Distance is not the
variable. **When the answer is *nothing can*, the writing has stopped being a
claim about the world and become a claim about itself.**

That is not a reason to delete it, and deleting it would lose the reasons,
which are the part that does not survive implementation. It is a reason to
**label it, at the top rather than as a caveat at the end** — plus, per
statement, what would have to exist for it to be checked.
That is kind 3 from *How briefs are written* applied at the scale of a whole
document, and it sharpens kind 3: a kind-3 statement names its prerequisite,
and this test asks whether *any* statement in the document has one that exists
yet.

**Measure the falsifiable surface rather than asserting it is small — and
never say you have measured all of it.** `docs/NW-EXPECTATIONS-UNANCHORED.md`
(its status is line 3, under the title) is the worked instance. Checked
against the tree and holding: the `SIGCHLD`/`waitpid`/`signalfd` counts in
`pid1.c` and `nwsup.c`, the absence of a layer-size bound, and
`NW_KIND_ONESHOT` and `NW_KIND_LONGRUN` in `blob.h`.

**Name what you checked and stop there.** This paragraph claimed those were
its "whole falsifiable surface"; the correction claimed a number instead;
both were wrong and the second was wrong in the same shape as the first,
because a count and a completeness claim are both statements about how hard
somebody looked. The omission that mattered was the document's own thesis —
it says nothing in the tree can disagree with any sentence in it *or confirm
one*. The list above confirms three, and a house with a declared bind
contradicts a fourth: the document says a container's state is bounded to
one directory, and a bind gives it durable state in two. So the sentence the
whole label rests on is answerable, in both directions. `HISTORY.md` §71 has
the episode.

**And it keeps becoming answerable as the tree grows, which is the part
worth watching.** The document names a queue — the fold helper, then
resource blocks — and `tools/fold-house.py` exists now, so that sentence
is checkable and stale. The document itself is NOT edited: it was
relayed and committed verbatim with its provenance, and rewriting it
would destroy the record of what was written before the thing it
describes. The tracking belongs here, where the label is argued for.
`claims` found it while checking what this round had made false.

**Every absence is over a set somebody chose, and the chooser is part of the
claim.** "No layer-size bound" is the obvious case — on a kernel with an
on-disk quota format it would be true of the codebase and false of the
machine, which is a description of the model presented as a description of
the machine. But the greps are no different: `pid1.c` and `nwsup.c` were
picked and `nwspawn.c`, `dawn.c` and `rescue.c` were not. Half of `nwsup.c`'s
count is comment, too — invariant 1's own recorded weakness, adopted here
uncritically as a headline measurement.

*The test comes from `docs/NW-EXPECTATIONS-UNANCHORED.md`, written by another
agent from a review with a second and relayed by the operator; `a27563d`
carries the attribution. Which of the two agents originated it is not
something the tree records, and this sentence does not claim to know — an
argument's provenance is checkable exactly as far as something wrote it down.*

The discipline already exists here and should be named as such: the negative
controls. `brick-is-a-root` was believed only after removing `lid_brick()`
made it fail, *and* after keeping the log line while skipping the
`pivot_root` syscall made it fail too — the second control is the one that
matters, because the first would pass against a supervisor that announced the
lid and did nothing. `path-traversal-refused` was believed only after deleting
the component check made it fail. Do that every time. A test that has never
been seen failing is a test that has never been tested.

Corollaries worth stating, each because it has been got wrong. (This
read "two corollaries" while the list beneath it grew past that and
kept growing — a count of the list directly below it, which is the
shape this file names as the durable kind.)

- **A green suite is evidence only against a stated environment.** The
  Landlock lid never worked, in any environment, for its whole life: every
  machine it ran on lacked Landlock, so it took an early return and the suite
  printed green. `tests/run.py` prints `print_environment()` before the first
  test and refuses to print `ALL TESTS PASSED` when anything was skipped.
  **Report that block whenever you report a suite result** — and never report
  a green line as evidence about a feature the machine cannot execute.
- **A control that passes is not good news.** It means the test is bad, or the
  control is. The first control on `brick-is-a-root` passed because the suite
  runs staged binaries and `make` alone had not restaged — the harness was
  lying, and the reading "it works" was available and wrong.
- **Green does not mean covered.** A test can pin the conjunction of two
  guards while pinning neither. `fds_ge3=0` inside a brick stays green if
  `O_CLOEXEC` is dropped and stays green if the `close()` calls are dropped;
  only removing both fails it. Ask what single change would still leave it
  passing.
- **A TEST THAT DESTROYS ITS OWN EVIDENCE**, and it has two failure
  modes that need telling apart. The cause is one thing — a step
  earlier in the test wrote, removed or restored what a later assertion
  reads — and what happens next splits.

  **Blinded: the assertion cannot see, and reads as one that passed.**
  `_stage()` was given a comparison of the candidate slot's bytes so no
  refusal could write it unnoticed, and the symlink case had already
  `rmtree`'d that slot and recreated it empty, so the captured "before"
  was `None` at every later call and the comparison silently did
  nothing. The ill-formed-`current` case restored the file before
  asserting on it, which made that half of `_live_intact` vacuous.

  **Corrupted: the assertion reads the wrong thing and accuses correct
  code.** The fixture probing a writable layer wrote its byte over
  `/id`, which three of `test_brick_is_a_root`'s assertions read,
  turning `id=brick-two` into `id=xrick-two`. That one goes red, and it
  blames the brick.

  **The diagnostic differs with the mode, and only the first is the
  hard one.** A corrupted assertion announces itself — the ordinary
  suite run is enough, which is how the `/id` probe was found. A
  blinded one is reachable only by running a control and then asking
  *why the answer was not what the mutation implied*: when a mutation
  you expected to bite does not, the first suspect is not the assertion
  but what ran before it. `claims` separated these; the first telling
  of this bullet gave both the blinded one's diagnostic, which would
  have sent the next reader hunting for a mutation to find a defect the
  suite already prints.

- **A claim with parts is covered when every part is, and reads as
  covered when one is.** The three-noun claim is `nwsup.c`'s grant
  comment and `.claude/rules/runtime.md`'s restatement of it — "no
  device nodes, sockets or fifos" — and *not* invariant 6, which states
  it generically as the withheld `MAKE_*` rights. (The bullet said
  invariant 6; `claims` grepped and the string is not in this file
  except where this bullet quotes it. Sharpens the point rather than
  blunting it: the enumeration and the generalisation live in different
  files, so a reader checking either one alone sees a covered claim.)
  The fixture probed the first noun, and granting `MAKE_FIFO` or
  `MAKE_SOCK` at the root left the suite green. A fifo probe was then
  added and the socket still was not, so the same claim came back
  half-covered a second time.

  **The general case, at `16a763b` — name the tree, because the split
  moves.** Of the rights that commit's lid withholds at a house's root,
  `MAKE_REG`, `MAKE_CHAR`, `MAKE_FIFO`, `MAKE_SOCK` and `TRUNCATE` were
  probed, while `MAKE_DIR`, `MAKE_SYM`, `MAKE_BLOCK`, `REMOVE_FILE` and
  `REMOVE_DIR` changed no field any fixture emitted — so adding any of
  those back to the grant passed. One commit earlier the split is
  different again (`MAKE_SOCK` unprobed, `TRUNCATE` not withheld at
  all), which is why the sentence names its tree: a coverage claim with
  no commit attached is read against whichever tree the reader is
  holding. **`MAKE_BLOCK` was named in the failure string of the
  assertion standing beside it**, whose probe was char-only — the
  sentence and the check disagreeing inside one `expect()`. `control`.

  So when a claim enumerates, enumerate the probe. The reading to
  distrust is the one where a claim's *first* item is tested and the
  claim's name goes green.

- **The word-versus-symbol trap is not only an annotation problem.** A
  `checkbrief` annotation must name a symbol rather than an English
  word, because prose produces hits. The same failure arrives in a
  fixture as a *comment standing in for a probe*: "REMOVE_FILE is
  withheld precisely so the house cannot unlink its own exec path" was
  the premise the whole truncate argument rested on, in several files,
  and `grep` for `unlink` in the fixture returned nothing but a comment,
  explaining why the probe was absent, for a reason (the seccomp
  allow-list) that applies to a house the test does not boot.
  A true comment, in the right file, answering a question nobody asked.
  `control` found it by grepping for the behaviour and reading what came
  back rather than counting the hits.

- **A test has a DIRECTION, and a suite can be blind in one while looking
  thorough from either side.** A rejection test is satisfied by a
  rejection for any reason at all; an acceptance postcondition says
  *accepted implies P* and is vacuous when the input never reaches
  acceptance; and CBMC cannot assert over a path not taken, so a
  soundness harness is blind to an over-strict checker by construction —
  it accepts a strict subset, and every blob it accepts satisfies every
  post-condition for free. So **the only instrument that detects an
  over-rejection is a legal input that must be accepted.**

  `nwcheck.c`'s bind loop refused one legal plan in 256 and every one of
  those three missed it, including a test written about that exact field.
  That is not a missing test — a missing test is visible by reading the
  list. It is a **missing direction**, and it is invisible from either
  side, because the tests that exist are sound, controlled, and pass for
  the right reasons in the direction they cover. `HISTORY.md` §53.

  So ask of any check, beside *what single change would leave this
  passing*: **which direction is this test in, and what covers the other
  one?**

- **A fix is a change like any other and inherits the same standard.**
  Phase 3 produced three fixes in a row, each tested, each with a control
  run and quoted, and each defective in a way the previous fix
  introduced: a shared-helper fold that turned a proof into an identity,
  an enumeration that pinned a loop the crafted cases never reached, and
  a repair that left a TCB bounds guard deletable green. Every one was
  caught by the next round and none by reading. The output of a review
  round is **unreviewed code**; re-dispatching against the fixes is not
  belt-and-braces, it is this rule applied once more. "It fixes a
  reviewer's finding" is not evidence about the fix. `HISTORY.md` §53.

- **A rule is at its weakest in the change that introduces it**, because
  the author is thinking *about* the rule rather than *applying* it. The
  record, and it keeps growing — the log-chunk rule was broken by an
  assertion written on the logger's prefix in the same round the rule was
  restated; `unit_layout()` was written to retire "an offset error wearing
  a rule violation's message" and read the source tree while its
  neighbour reads the stage, reproducing that exact message one level
  down; the fixture probing whether a layer is writable wrote its byte
  over `/id`, which three of that test's assertions read; the
  commit that narrowed the Landlock claim left `lid_landlock()`'s own
  docstring asserting the opposite; and the commit that added
  `harness.md`'s "reset once per run" section added bind-side probes to a
  directory nothing resets, so a second run of the documented workflow
  reports `EEXIST` as a lid regression; and — in a *process step*
  rather than in prose or code, which none of the others is —
  `git checkout -- .` run to tidy up after a control, in a tree holding
  uncommitted work, during the round that added the cleanup rules to
  `harness.md` and `runtime.md` (`HISTORY.md` §66, which files it as
  the destructive-cleanup class and applies the weakest-rule label to a
  different item in the same round). Nothing was lost — the commit was
  intact and the edits were redone, which is `HISTORY.md` §66's own
  wording and worth keeping exactly: the commit made redoing them cheap,
  it did not preserve them. That is luck, not the rule working. Note the fit
  is by analogy: no rule about destructive cleanup was *introduced* by
  that change, so this is the pattern's shape without its usual
  mechanism.

  *(This paragraph said "three times now" and was a count in the file
  whose own rule forbids one. It is not corrected to a larger number —
  the instances are named instead, which is what the rule prescribes and
  what makes the next one cheap to add.)*

  **Do not number these.** `HISTORY.md` labelled one "Fourth instance"
  and a later one "Sixth", with no fifth recorded under that name —
  the count-in-prose failure occurring inside the record of the
  count-in-prose failure. The instances above are named; the ordinals
  are retired. `claims`.

  **The strongest form arrived on 2026-09-12: the rule and its violation
  in ONE DIFF.** `harness.md`'s new section and the probes that break it
  were the same commit, and the section's own worked example is a fixture
  writing where an assertion reads. So this is not a rule decaying over
  time and being caught later — it is a rule that was never true of the
  change that shipped it.

  **And a rules-only diff is where the pattern is easiest to see.** The
  round after that was a diff whose entire content was rules about
  writing prose, and `claims` found it broke those rules repeatedly,
  including inside the sentences stating them; the round after *that*
  did it again, and again. `HISTORY.md` §60, §62 and §63 carry the
  tallies, where a count belongs because those episodes are closed and
  dated. The reading to take is not a measured quantity — nothing
  computes a density and the word was doing rhetorical work. It is
  this: **the same defects spread across a month read as carelessness,
  and in one diff about carefulness they cannot.** So a diff of pure
  prose is not a safe diff; the dispatch table's `claims` row names
  those files for exactly this reason. (No ranking against other kinds
  of change is offered, because none has been measured — two attempts
  to state one were superlatives with nothing behind them.)

  **And look at how they are caught.** Every one was found **by running
  something, never by reading** — including the times the author had
  just finished writing the rule down, and including the ones the author
  found themselves. That is the argument against "be more careful":
  careful is the state they were all written in, and reading them again
  in that state is what does not work.

  *(This said "not one by the author", which is false and was caught by
  `claims` within the round: `HISTORY.md` §54 carries the heading "Four
  defects in writing the fixture, all mine, all found by running", and
  the probe-over-`/id` instance is inside it. Author-found by running,
  which is the half that matters. Reviewers found some and the author
  found others; what none of them was, was found by re-reading. The
  sentence reached for the more dramatic claim and lost the durable
  one.)*

  **Naming a failure mode in a correction does not inoculate the
  correction against it**, because the author writing the name is in
  exactly the state the name describes. `HISTORY.md` §63.

  The defence is mechanical — run the new rule's own check against the
  change that introduces it, the way `make prereport` is run on its own
  diff, and dispatch the reviewer *before* the push rather than after.

- **Silence is the expensive failure, not noise.** A mechanism that is
  correct and routed around is worse than a broken one, because it looks
  like it is working. The first `tools/rules-hook.sh` matched only
  `Edit|Write`, was correct, and **fired zero times** — nearly every edit
  here goes through `Bash` with a python heredoc, so it never matched.
  Nothing failed; nothing was reported; the territory rules simply never
  arrived. It was found by noticing an absence, not a fault.

  This generalises well past hooks, and every instance in this file's
  record is a case of it: a test that never runs (`lid-landlock`, green on
  every machine for its whole life), a guard that cannot fail (round five's
  line-count assertion, an identity in front of the desync it was written
  for), a probe that certifies a name instead of a behaviour (the TLC
  probes at `MaxFds = 16`), a proof kept where it cannot be re-run, a
  control that passes. **Every one of those reads as working.**

  **A prescription is a mechanism too**, and one shipped here could not
  find the heading the rule was derived from — a sweep command with a
  literal `…` and no `-i`. Whether it was ever run is not a fact the
  tree holds; that it cannot work is. `HISTORY.md` §63.

  So the question to ask of any mechanism is not "does it pass?" but
  **"when did it last fire, and what made it fire?"** If the answer is
  "never", that is the finding — not the reassurance it resembles.

- **A SOUNDNESS ARGUMENT IS A CLAIM ABOUT WHERE ITS ASSUMPTIONS COME
  FROM, AND MOVING THE HARNESS REASSIGNS THOSE ORIGINS SILENTLY.** No
  code changes; the sentence changes meaning because its subject moved.

  `proofs/leaf_name_dup.c` assumed its field is non-empty and NUL-padded
  and said both halves come from `name_ok`. True of `name`. It was then
  parameterised by offset and run at `layer`, where padding still comes
  from `name_ok` and **non-emptiness comes from a guard in `nw_check`
  that `proofs/` does not prove and only reads**. The assumption held;
  the argument for it did not.

  Those two are indistinguishable from a passing run, and only one is a
  defect — which is why the question is not "is this proof vacuous" but
  **"for each assumption, what establishes it, and is that thing proven
  or merely read?"** An assumption resting on unproven code is fine and
  common; recording it as resting on proven code is the defect, because
  the dependence then has nothing watching it. State it as a dependence
  and name what would have to be re-checked if that code moves.
  `HISTORY.md` §78.

- **A SUCCESS SIGNAL CONFIRMS THE STEP THAT RAN, NOT THE STEP THAT
  MATTERED — and the failure is invisible because the wrong stream was
  discarded.** Two instances on 2026-09-13, different tools, one
  structure.

  `proofs/run.sh`'s `check_unwindset` ran `cbmc --show-loops … 2>/dev/null`.
  cbmc failed to run; the empty result was read as "the loop is absent";
  and the guard printed `--unwindset names field_dup.1, which is not a
  loop in this program` while 25 such loops exist in both builds.
  **Tool-didn't-run and bound-names-nothing produce identical evidence**,
  and the guard reported the second, with a message whose every clause
  was about something else. `HISTORY.md` §78.

  The operator reports the same shape from the other side on the same
  day: `gcc` failed, the `cp` after it succeeded, `staged on root`
  printed, and two boot results were read off a binary that was never
  compiled. Written as **reported**, because nothing here can check a
  claim about that machine — but the structure is checkable and it is
  the same one.

  **The tell is always in the part of the output nobody was reading.**
  A mode-`0644` binary; an empty loop list. Neither is the line the
  reader is looking at, and in both cases every line they *were* looking
  at was true. So when a diagnosis names a cause, ask what else produces
  exactly that evidence — and never discard a stream you are about to
  draw a conclusion from. `2>/dev/null` on a command whose *silence* you
  will interpret is the specific form to grep for.

  **The fix is to check a positive artifact, not to interpret an
  absence**, and `tools/coverage-tcb.sh` is the worked example already
  in this tree. It discards `gcov`'s stderr too — and then asserts
  `[ -f nwcheck.c.gcov ]` and refuses with `gcov produced nothing`, and
  separately refuses a percentage it cannot parse. A `gcov` that fails
  there produces a correct diagnosis rather than a confident wrong one,
  because the script asks whether the output EXISTS instead of reading
  meaning into its silence. Same discarded stream, opposite outcome; the
  difference is the whole rule.

  **And FIRING is not the same as being obeyed, which `tools/review-gate.sh`
  demonstrated from both sides in successive days.** On 2026-09-11 it showed a
  review owed and three commits were pushed anyway — a stop hook asking
  for a push, an ephemeral container, and neither is what the rule is
  about (`HISTORY.md` §31). That is its first firing, and it is a case
  of the silence rule rather than an exception to it: the mechanism
  worked and was routed around, which looks identical to it not
  existing. On 2026-09-12 it printed the same line and a commit was
  **held unpushed** because of it, and the review it forced came back
  non-empty.

  So the question has more answers than "does it fire". Never fired:
  the guard is decorative. Fired and was routed around: worse, because
  the record now shows a guard that was consulted. **Fired and changed
  what happened: that is where the value is measured rather than
  assumed**, and that is the event worth dating — not the firing.
  (This paragraph called 2026-09-12 the first firing, which `claims`
  disproved from §31. And "first" here can only ever mean first
  *recorded*: the gate writes no log, so nothing in the tree evidences
  a firing that nobody wrote down.)

## The rule that matters most

Every bug found in this codebase so far was found by running, and none by
reading. Do not report a change as working until it has been built and run.
Prefer designing the problem out over checking for it: no ordering list to get
wrong, no counter to overflow, no second limit to drift, no channel to
impersonate, no path for a filesystem to reinterpret.
