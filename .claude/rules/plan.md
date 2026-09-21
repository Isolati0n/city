# plan — territory rules

<!-- nw-init:install-agents v1 -->
**Not an agent.** This was a dispatchable brief until 2026-09-10 and was
never dispatched once. Its content is reference read at the moment it
applies, so `tools/rules-hook.sh` delivers it on a `PreToolUse` for any
file in this territory. Scope: Owns the sealed plan; `tools/rules-hook.sh --owns plan` lists the files. Use for any change to the blob layout, a limit, a structural check, a NW_E_* code, plan-language syntax, or the specs.

You own what a plan **is** and what makes one acceptable. The Scope line
above says where the file list lives. This paragraph carried a second
copy of that list, naming neither the staging tools, nor the spec-limits
generator, nor the proof harnesses that `--owns plan` returns — the
two-lists problem inside the file the map exists to remove it from.

## Why this is one territory and not three

It was three — a validator agent, a baker agent and a spec agent — until
2026-09-10. The split was wrong, and the repository says so: every
plan-format change in its history touched `blob.h`, `nwcheck.c`,
`bakery/nw-cc.py`, `plan.als` and `Plan.tla` **in a single commit**.
Invariant 3 requires exactly that ("change one, change all four"), so an
agent that owns one of them owns a fraction of every change it will ever be
asked to make, and cannot see whether the other fractions agree.

The boundary that does matter here is not between files, it is **trust**:
`nwcheck.c` and `blob.h` are TCB, the baker and the specs are not.

## Hard rules

- **Verify the seal, do not merely read it.** Bug 1: fuzz-accepted blobs had
  broken integrity because the CRC was read and never compared. Memory-safe
  and wrong is still wrong.
- **CRC32 is diagnostic**, not a tamper defence; the threat model is
  corruption. The structural checks are the real safety property. Do not
  argue for SHA-256 on integrity grounds it does not provide.
- **No malloc, no recursion, bounded loops** in `nwcheck.c`. It was O(n²)
  and took 15.26 s at 64k units; an open-addressed hash brought it to 0.10 s
  at 200,000. Do not reintroduce a nested scan.
- **Field lengths must match the struct.** Bug 12: a 32-byte scan over a
  128-byte field left most of `exec_path` unvalidated. Pass the length.
- **Trailing bytes must be zero.** A NUL-terminated field has a tail after
  its NUL, and every byte of it is verified zero, for the same reason
  `_pad` is: an unvalidated field cannot be given meaning later, because an
  old blob carrying garbage would be accepted by a new checker that reads
  it. Every field that goes through `name_ok` or `path_ok_len` — do not
  enumerate them here, read the calls; the enumeration written on this line
  omitted bind paths within a day of being written.

  **`brick` is NOT one of them, since phase 3.** It is a raw sha256, every
  bit significant, with no tail and no terminator — a nonzero byte after
  the first does not mean "blank with garbage", it means a different hash.
  The rule was lifted off it because its subject went, and `HISTORY.md` §51
  says so at length.

  **What replaced it is sharper: "no brick" is ALL-ZERO, so every reader of
  the field must scan all of it.** Reading `brick[0]` alone silently takes
  one hash in 256 for "no brick". There is exactly one legal way to ask —
  `nw_unit_has_brick()` in `blob.h`, beside the field — and no site may
  open-code it. That is not style: phase 3 converted the unit loop in
  `nwcheck.c` and left the BIND loop reading `brick[0]`, and the CBMC
  caller proof too, so one plan in 256 was refused as `bind unit index` and
  would not boot until the brick's *contents* changed. Three reviewers
  found it independently. Pinned at EVERY byte position, **in both
  directions**, and the phrase is exact rather than decorative:
  `test_checker_rejects_crafted_fields` crafts a blob per position and
  requires a REJECTION; `test_leading_zero_hash_is_a_brick` bakes a plan
  per position and requires an ACCEPTANCE; `test_checker_rejects_crafted_binds`
  pins the bind rules and the bounds guard by their reason string; and
  `test_leading_zero_hash_reaches_the_supervisor` boots one, because
  `nwspawn.c` reads the field too and decides whether a house gets its
  brick at all.

  **Neither direction substitutes for the other, and this is the thing to
  carry to the next field.** The unit loop over-ACCEPTS when it is wrong
  and a rejection test catches that. The bind loop over-REJECTS, and
  nothing in the rejection direction can see it: a rejection test is
  satisfied by a rejection for any reason at all, an acceptance
  postcondition is vacuous because the plan never reaches acceptance, and
  CBMC cannot assert over a path not taken — measured, the defect passed
  the caller proof and a *corrected* assertion passed it too. The only
  instrument for an over-rejection is a legal plan that must be accepted.
  That was a missing DIRECTION, not a missing test, which is why it was
  invisible while every individual brick test was sound. `HISTORY.md` §53. *(This paragraph said "`nwcheck.c` ORs the whole
  field" while one of its two sites did not — a present-tense rule stating
  as done the thing that was half-done, in the file the hook hands the next
  agent to edit that file.)*
- **Prefer rejecting at bake time — but any rule the runtime relies on must
  be in `nwcheck.c` too.** The baker is not in the TCB and a blob can
  arrive from anywhere. The cross-field rules — a brick forces
  `NW_LID_NEWNS`, and a bind requires a brick — are each enforced in both
  places independently. (A third, `NW_PROF_BUILD` requires `NW_LID_SECCOMP`,
  went with the profile on 2026-09-10; `HISTORY.md` §23.)
- **The baker refuses; it does not repair.** A brick house that forgot
  `newns` is a bake error, not a plan to quietly add a lid to. A lid nobody
  asked for is a lid nobody reviewed.
- **Every new check needs a new `NW_E_*` code, its string in `errs[]` in
  the same order, and the `nw_errstr` bound updated.** Codes have been
  renumbered when checks were retired — never assume a numeric value, read
  the enum.
- **The lid set is closed.** Unknown bits are `NW_E_LIDS`.
- **`brick` and `layer` are paired, in both directions.** A brick with no
  layer is a house whose writes vanish at exit while the plan says it has
  data; a layer with no brick names a directory nothing mounts. Neither
  errors at runtime, so both are structural: `NW_E_LAYERPAIR` in
  `nwcheck.c` and a refusal in the baker. The id is validated as a NAME,
  not a path — `nw-sup` composes `NW_LAYER_DIR "/" <id> "/" upper` itself,
  so the same argument that retired the brick path applies.
- **The `.layers` sidecar pairs each id with its brick, and the pairing
  is load-bearing.** The baker writes `<layer-id> <brick>` per line;
  `tools/stage-candidate.py` reads field 1 to tell a candidate that
  reuses a live layer over the SAME brick -- an unchanged house keeping
  its data across a plan change, which is what keying a layer by a
  declared id is for -- from one that reuses it over a different brick,
  which after a fold stacks the folded contents over themselves.
  `tools/stage-layers.py` reads field 0 and ignores the rest, because it
  creates directories and the brick does not bear on that.
  `tools/stage-layers.py` and `tools/stage-candidate.py` disagreeing
  about a one-field line is not the asymmetry `control` found before:
  they are answering different questions, and `tools/stage-candidate.py`
  refuses because its question has no answer without the field.

  The writer is the baker; the readers are `tools/stage-layers.py`,
  `tools/stage-candidate.py` and `tests/run.py` — which also WRITES
  sidecars by hand, in the stager test's fixtures. Those hand-written
  copies are not drift: they are what pins the format, because a
  mutation to the baker has to leave them still accepted. Adding a
  field to a file that many programs read is the drift class invariant
  3 is about, so it is written here rather than left to be
  rediscovered.

  **The separator is unpinned and that is fine; the readings are not.**
  Every reader uses `str.split()`, so a tab is genuinely equivalent and
  a baker emitting one leaves `make test` green — `drift` ran it. What
  is worth knowing is that no assertion names the separator, so a
  reader written as `split(" ", 1)` would work against the baker and
  break on a hand-edited sidecar.

  **`tools/mkboot.sh` copies BOTH sidecars into the ESP, and the second
  one is why.** `.layers` alone is not enough: `tools/stage-layers.py` —
  which is `.claude/rules/runtime.md`'s THE RECOVERY — refuses outright
  when a layer sidecar is present and the hash sidecar beside it is not,
  because then nothing says the layer list describes this blob. The
  baker writes an empty-but-EXISTING `.layers` for a brickless city, so
  that refusal was reachable on the common case, not only on a city
  with bricks.

  The burned image was in that state for part of one day — the
  `.layers` copy landed and the hash copy followed it a few commits
  later — and before that it carried NEITHER sidecar and the recovery
  failed earlier, for the reason `HISTORY.md` §74 records. Nobody is
  known to have run the recovery on a burned slot in the window, so
  the refusal was reachable rather than observed. The reasoning is
  worth keeping because it looked sound: `.sha256` was left off on the
  grounds that nothing on the BOOT PATH reads it, which is true and was
  the wrong test. The reader that matters is the recovery
  tool the `.layers` copy exists to feed. Reproduced against the exact
  file set the script copied — `plan.blob` and an empty
  `plan.blob.layers` — `stage-layers` exits 1 naming the missing hash.
  `claims`.

  **`tools/stage-candidate.py` refuses a line that is not exactly an id and a
  brick — not "at least".** So the next field added breaks it for every
  plan that declares a layer, loudly and with a message naming the file
  and the line, until that reader is updated. A brickless plan has an
  empty sidecar and no line to refuse. Deliberate, and the consequence to know:
  adding a field is a change to that tool, not only to the baker.

  **A live slot staged before 2026-09-13 has a one-field sidecar** if
  its plan declares a layer, and `tools/stage-candidate.py` then refuses
  every candidate against it until the live plan is re-baked. A
  brickless live plan is unaffected, which is the common case in the
  suite — so this will not show up there. The refusal says so; `claims` found it by reading
  the guard rather than by hitting it.

  The reason it is a sidecar field rather than an argument from the
  caller is that the fold helper knows which ids it did not fold and
  passing that in would be an override; the brick lets the stager
  establish the property itself.

- **`lids=` is required in a city file, and `none` is how a bare house
  is declared.** Omitting it used to bake the same byte `lids=none`
  bakes, so a deliberate bare house and a forgotten one were
  indistinguishable — measured: the two blobs are byte-identical.
  The floor's height is not the question; its height being unrecorded
  was.

  **Bake time only, and it cannot be otherwise.** The blob has one lids
  byte and `_pad` is required zero, so distinguishing an omission from a
  declaration needs a layout change and a magic bump. Nothing at runtime
  relies on declaredness — `nwspawn.c` passes the VALUE on — so the
  "any rule the runtime relies on must be in `nwcheck.c` too" clause
  has no subject here.

  **A city file written before 2026-09-13 with no `lids=` no longer
  bakes**, and the two tools that re-bake a user's city turn that into
  their own message rather than the baker's: `tools/fold-house.py`
  reports `does not bake`, `tools/stage-candidate.py` reports `the baker
  refused this city`. Recorded for the same reason the one-field sidecar
  above is — self-diagnosing, but only if you know to read past the
  wrapper. `claims`.

- **The resource block: unset is zero, and zero is never a limit.** Every
  field of `struct nw_res` is 0 when the plan declares nothing, and 0
  means *no limit declared* rather than a limit of zero. A default would
  be a number nobody chose, failing in the direction hardest to
  diagnose, so there are none — and the absence is made visible instead:
  the baker prints `no resource block: <names>` for every house that
  declares nothing, **named rather than counted**.

  **The consequence is that a declared zero cannot be represented, so it
  is refused — at BAKE TIME ONLY, and there is deliberately no error
  code for it.** `cpu-weight=0` and an omitted `cpu-weight` are the same
  byte, so `nwcheck.c` has no subject: a code for it would be
  unreachable, which is the characteristic failure wearing an enum. The
  same structural argument as `lids=` two bullets up, and `blob.h` says
  so where the code would have gone. `test_baker_refuses_bad_resources`
  is the only thing covering that class, along with every fault that
  never becomes bytes at all — `cpus=3-1`, `mem-high=2X`, `sched=fifo`.

- **Every number in the block is a property of the PLAN, and that is
  structural rather than labelled.** A blob has carried no
  machine-derived number since it existed — no `getrlimit`, no device
  number, no CPU count — so there is nothing to label and no labelling
  path that could diverge from the measuring path. **A field saying
  "plan" beside a value computed from a machine is worse than no field,
  because it reads as verification.**

  Two consequences, both visible in what the block does NOT carry:
  `io.max` is keyed by device major:minor in cgroup v2 and a device
  number is a machine property, so the block carries the *rate* and
  resolving the device belongs to whatever applies it, on the machine it
  applies it to; and `cpu_mask` names indices that mean different things
  on different machines, but it is declared policy rather than a number
  obtained from one.

  **What happens to a mask naming a CPU the machine lacks is undecided,
  and nothing refuses it today** — `cpus=63` on a four-CPU machine bakes
  clean and validates clean, because the plan language bounds a CPU
  index by `cpu_mask`'s width and by nothing else. This bullet asserted
  a refusal for one round; `claims` ran it. The choice belongs with the
  code that applies the block, and `tools/HANDOFF-resources.md` puts it
  there.

  **Test the rule when the next field lands**: ask how the value was
  OBTAINED, not what it is called. That is the whole of it.

- **The cross-field rules are in both places, like every other pair.**
  `mem_high` below `mem_max` (`NW_E_MEMORDER`), `nice` only under
  `NW_SCHED_OTHER` or no declared policy (`NW_E_NICEPOL`), a layer
  capacity only with a layer (`NW_E_CAPNOLAYER`) — each refused by the
  baker and independently by `nwcheck.c`, because every one of these
  numbers is destined for a cgroup file or a scheduler call, whatever
  ends up writing it will not re-derive it, and a blob can arrive from
  anywhere. **Nothing writes them yet** — `grep -n "res\." nwsup.c`
  returns nothing, and `.claude/rules/runtime.md` carries that as kind
  3. The rule is about where a check belongs, not about a reader that
  exists; stating it the other way round was a kind-3 sentence written
  as kind 1, in three files at once. `claims`.

  **`mem_high == mem_max` is the case to keep.** A throttle at its
  backstop can never fire, so the plan declares a warning pass the house
  does not get — which is exactly what omitting `mem-high` would have
  given. It is the one a `>` instead of a `>=` lets through, and it is
  almost always the two numbers written the wrong way round.

  **A capacity depends on a lid, transitively, and nothing states it
  directly.** `layer-bytes=` requires `layer=`, which requires `brick=`,
  which requires `lids=...,newns`. Three rules in a chain, each pinned
  on its own; the chain is not. Stated here rather than added as a
  fourth check, because a direct rule would be a second statement of
  something already enforced and would go stale the day one link moves.
  It is not annotated either: `make checkbrief` reads `CLAUDE.md` and
  only its numbered invariants, so an annotation here would be a
  mechanism that never fires — which reads as working, and is the thing
  this project pays for most often.

- **The block is FLAT and it is a BLOCK, and both halves are for the
  same future.** Loose fields on `struct nw_unit` would make a group
  level a layout migration; a nested block would make it a reshape. As
  one flat block, a group level is a table of groups plus a group id
  *in this block* — units do not move and the plan is not reshaped.
  Adding a level later must be adding a level, not rewriting the plan.

- **`bakery/nw-cc.py` reads the block's bounds out of `blob.h`** through
  `mkbrick._define`, rather than spelling `10000` and `-20` a second
  time. That is the drift class of invariant 3 one level down, and it is
  removed rather than checked — `nwcheck.c` and the baker quote one
  source. Do not add a literal beside them.

  The *offsets* are a different story and still a genuine second copy:
  `pack_res`'s `struct.pack` format decides what lands on disk and
  `blob.h`'s `NW_AT(nw_res, ...)` lines only pin the reader.
  `test_baker_writes_the_declared_layout` reads every field back at its
  declared offset **and its declared width** on a house whose values are
  all distinct. The mutation that needs it: swapping `io_rbps` and
  `io_wbps` in the baker alone is legal in every direction — both u64,
  both unconstrained, no cross-field rule touches either — so a plan
  capping reads gets its writes capped instead, bakes clean, validates
  clean, and nothing but a byte position can see it.

- **Check the struct sizes, do not eyeball them.** The Python
  `struct.pack` format and the C struct must agree. Take the format from
  `bake()` in the baker, run `struct.calcsize` on it, and compare against
  `sizeof(struct nw_unit)` and `sizeof(struct nw_bind)` from a compiled
  throwaway. Do not copy the format string into a brief or a comment: the one
  that used to be here went stale on 2026-09-10, the day the format changed. A
  mismatch surfaces as a size error from `nw-check`, not as a Python
  exception, so it will look like a corrupt blob rather than a bug in you.

## Refused deliberately

- **Cycle detection.** Undefined, not deferred. A plan is a flat list of
  units with no relations — there is no graph, so there is nothing to have a
  cycle in. This file once claimed a counting-sort adjacency index for it.
  Wanting it back means proposing a plan format with relations in it, which
  is a design decision, not a restoration. `HISTORY.md` §16 and §17.
- **Typing, ordering, capability-flow analysis.** None were ever built and
  after §17 none are definable. Fields are range-checked, which is not
  typing.

## Waiting on a prerequisite

Kind 3, in the sense `CLAUDE.md` gives it: real rules with no subject in
this territory yet, and this is where they should go.

Not where they all are. *Hard rules* above already carries kind-3
material — the resource block's "Nothing writes them yet", and the CPU
mask whose out-of-range case nothing refuses — so this section is
somewhere to move such items TO, not a boundary the file already keeps.
Claiming it kept one would have been an assertion about editorial
practice contradicted by the two items named above, which is the defect
this section exists to record. `claims` caught that sentence, and then
caught the distance-in-lines the first correction reached for.

- **A plan hash in `struct nw_hdr` waits for a reader that holds the blob
  WITHOUT the file beside it.** Proposed 2026-09-20 as half of a magic
  increment and deferred, for a reason that is not the migration cost.

  The magic itself is not spelled here, and that is
  `install-agents.sh --check` refusing it rather than taste: a brief
  holding a copy of a literal that lives in the code is one more place
  to drift, and the first draft of this bullet spelled both the old
  value and the new one inside a section arguing that a second copy is
  the drift class. `NW_MAGIC` is in `blob.h`; read it there.

  **The plan's identity already exists, and it is recomputed rather
  than trusted.** The baker writes `<blob>.sha256` beside the blob;
  `tools/stage-layers.py` and `tools/stage-candidate.py` each read the
  blob's bytes, hash them and compare — neither reads the sidecar and
  believes it. At STAGING time, by two non-TCB tools. Nothing verifies
  it at boot and nothing proposed here would.

  Name both readers or the omission bites: the first version of this
  paragraph named only `tools/stage-candidate.py`, and the one it left
  out is precisely the one the burned-image defect above turns on.
  Naming it would have surfaced that defect while writing this
  sentence. Read the calls rather than a line number: this file quoted
  a pair into the specs and they had drifted to unrelated prose by the
  time anyone looked. The spec section below names them now, and says
  so rather than describing the drift as something that happened
  elsewhere.

  So a header field would be a SECOND copy of an identity that exists,
  and the two could not be reconciled: the proposal hashes the blob with
  the hash field zeroed, the sidecar hashes the blob, and neither is
  derivable from the other without running the algorithm. That is
  invariant 3's drift class pointed at an identity instead of a limit.

  `tools/mkboot.sh` is NOT evidence for this, and an earlier draft of
  this bullet cited it as though it were. It omitted `.sha256` from the
  ESP reasoning that nothing on the boot path reads it — an omission
  that was a defect rather than an argument, because the recovery tool
  reads it. The sidecar bullet in *Hard rules* carries it. A file on an
  ESP is not a plan field either, so the mechanism rule was being
  stretched past its own heading to justify a `cp`.

  **Clause 5 does not rescue it.** Descriptive metadata is exempt from
  needing a runtime mechanism; that is not the same as making a second
  copy of an existing identity into a first one.

  **The trigger, which is the whole point of this entry: the day
  something reads the plan's name without the file beside it.** That is
  PID 1, recording the identity of what it booted. No such consumer
  exists in this tree — no code reads a plan hash, because there is no
  plan hash to read.

  **Its SPECIFICATION does exist**, and this bullet said otherwise for
  the few commits between being written and being read.
  `docs/relayed/NW-BOOT-RECORD.md` designs the boot record, gives it a
  `BOOT_OPEN` record per boot carrying the plan hash as 32 raw bytes,
  and names the header field a bump would add. Its own third line is
  "Written 2026-09-14. Not in the tree", and the directory it sits in
  says on its first line that none of those documents is in the tree,
  scheduled, or decided.

  That STRENGTHENS the deferral rather than weakening it, which is why
  it is written out rather than patched over. The trigger asks for the
  field to arrive with its reader; the reader's design is now in the
  tree with a status line on it, so the pairing is a thing someone can
  pick up rather than invent. What is still missing is the code and the
  decision to build it, and neither is here.

  On that day the header field is right, the SIDECAR becomes the
  redundant copy, and the bump should carry that consumer with it so
  the field arrives with its reader rather than ahead of it.

  **Invariant 8 is untouched by any of this and must stay that way in the
  same change.** CRC32 stays DIAGNOSTIC, the structural checks in
  `nwcheck.c` stay the safety property, and the threat model stays
  corruption rather than tampering. Calling CRC32 "the integrity
  mechanism" stood here for one round and promoted it to the role both
  `CLAUDE.md` and this file's own Hard rules deny it, in a paragraph
  about a SHA-256 header field, while the Hard rules bullet says not to
  argue for SHA-256 on integrity grounds it does not provide. A header hash that `nwcheck`
  VERIFIES is a different proposal: it rewrites that claim and brings bug
  1 — the seal must be verified, not merely read — to bear on a new
  field. Whoever wants that should want it on its own terms, not inside a
  format bump.

- **`gate` — a unit naming another whose socket must exist before it is
  forked — was proposed in the same increment and deferred harder.** It
  has no subject at all: there is no house that needs it, the driver
  being a desktop that does not exist here. Recorded because the next
  person to want ordering will reach for the same field.

  The scope the proposal understated: the field is nothing without the
  pre-fork wait that enforces it, and a format-only landing is `nw_res`
  again — declared, baked, validated, read by nobody — which is the
  defect the mechanism rule was adopted to prevent. It also needs a
  socket path field, because nothing declares a socket today, and without
  one the refusal it most wants (a gate whose named socket is unreachable
  from the dependent's namespace) has no input. That check turns on
  `NW_LID_NEWNS` and the bind set, not on `NW_LID_NEWNET`: a unix socket
  on a filesystem path does not care about a network namespace.

  And the wait needs a bounded constant, which `.claude/rules/runtime.md`
  refuses without the constant, its owner, and what happens when it is
  wrong. A boot-time wait may well be a different animal from freeze
  detection; that argument has to be made rather than assumed.

  **Cycle detection is TCB-legal only while `gate` is single-valued**, and
  this is the sentence to carry into the field's comment on the day it
  exists. One parent per unit is a functional graph, so the check is a
  chain walk with a step counter bounded by `NW_MAX_UNITS` — no
  allocation, no recursion, no nested scan, all of which `nwcheck.c`
  forbids. Make `gate` a list and you need a real graph algorithm in the
  TCB against a rule that does not permit one.

  Landing it means re-filing TWO refusals above, not one. Cycle
  detection names this route without endorsing it — "a design decision,
  not a restoration" states a cost rather than granting permission. And
  the bullet directly beneath it refuses *ordering* outright, on the
  ground that after §17 it is not definable; a `gate` field makes it
  definable and falsifies that clause. Both re-filings belong in the
  change, not in a paragraph attached to a format bump.

## The specs, honestly

**They run.** `tools/jars/` holds TLC and Alloy, and
`test_specs_are_checked` in `tests/run.py` executes both inside
`make test`. `TypeOK`, `FdBudgetCovers`, `FdNeedAgrees` and
`LargestCityFits` are TLC invariants; `FdArithmetic` and `Sealed` are
Alloy checks. **Every Alloy check is shown failing on every run** — the
suite breaks the thing each one is about and requires a counterexample.
`Sealed` gets two probes, one per conjunct, because the bind half admits
no counterexample at the scope the fd half fails at.

**The TLC invariants have probes too, as of 2026-09-11**, one per
predicate, each listing only its own invariant in the cfg so a sibling
cannot answer for it. Until that day they had none, and `control` showed
the price: `LargestCityFits == TRUE` in `Plan.tla` left the entire suite
green, under an `ok` line that read "4 invariants incl. the boundary".
All four mutations — each invariant replaced by `TRUE`, and `TypeOK`'s
`kind` range widened — now turn the suite red. A TLC run is under a
second, which is the whole reason this was cheap and the reason its
absence was indefensible.

*Their hand-run controls are still in `HISTORY.md` §39 **and** §41, and
that sentence has now been wrong twice: it pointed at §39, was
"corrected" to §41 alone, and the correction was wrong — §39 carries a
"Controls, all run" table with `NW_MAX_FDS = 100` violating
`FdBudgetCovers`. "Run by hand once" was wrong in a second way: two
rounds, not one. `claims` found the correction, having not been asked to
check the thing being corrected* to.

The limits both specs use are generated from `blob.h` by
`tools/gen-spec-limits.py` — including the lid bits, as of 2026-09-11.
**One hand-written number remains that must track the header**, Alloy's
`but 12 Int` bitwidth in `plan.als`, and `test_specs_are_checked`
asserts it covers `NW_MAX_FDS`. The qualifier is load-bearing: `for 8`
is hand-written three times in the same file and is *not* covered by
that sentence, because nothing in `blob.h` corresponds to it — this
file declares no bound on `#House`. The suite requires the three
commands to agree and imposes a floor of 2 (below which the binds
must-fail probe cannot reach a counterexample); it does not derive the
value, and the prose copies of the scope are pinned by nothing — do not
enumerate them here, because an enumeration is a count and the first one
written was already short by two. Nor is a grep the answer: `grep -rn
"scope 8"` was offered as one and misses `tools/jars/README.md`, which
writes it as "Alloy's scope is 8 of each signature". Two attempts to
avoid a count both failed, so: **read the commands in `plan.als`, and
treat any number in prose as unverified.**

**And the qualifier is still too generous — but say what it is pinned
*against*.** `plan.als` hand-writes the fd multiplier
(`plus[nwReserved[], 2.mul[#House]]`) and so does `Plan.tla`
(`FdNeed == Reserved + 2 * n`), and that `2` must track `blob.h`, which
is exactly what invariant 3's "the arithmetic appears in four places"
says. Nothing pins it **against the header**: `claims` changed `* 2` to
`* 3` in both of `blob.h`'s `_Static_assert`s and both specs ran clean,
because `tools/gen-spec-limits.py` emits the four limit values and the
two lid bits and no arithmetic at all — the generated files come out
byte-identical.

Each *is* pinned against a second hand-written copy in its own file:
`assert FdArithmetic` for Alloy, `FdNeedAgrees` for TLC, and each turns
`make test` red on its own.

**Do not audit this with a grep for one spelling.** The same symbol is
written four ways and the multiplier two, so the obvious search finds
half of it:

```
$ grep -rln FD_RESERVED blob.h bakery/nw-cc.py plan.als Plan.tla
blob.h
bakery/nw-cc.py
```

`blob.h` has `NW_FD_RESERVED`, the baker `FD_RESERVED`, `plan.als`
`nwReserved[]`, `Plan.tla` `Reserved`; the multiplier is `* 2` in three
places and `2.mul[...]` in Alloy. Read the sites, do not search for a
name. This was got wrong twice in one exchange on 2026-09-12 — once by
concluding the arithmetic had shrunk to two places, and once by
concluding `Plan.tla` had dropped it, which `sed -n 42p Plan.tla`
disproves.

And when you count them, **`assert FdArithmetic` in `plan.als` and
`FdNeedAgrees` in `Plan.tla` are not sites.** They are the deliberate
second copies that pin the other two; counting them as drift is
flagging the guard. Those were written here as `plan.als:137` and
`Plan.tla:152`, correct when written and pointing at unrelated prose by
2026-09-20 — the stale pair this file elsewhere cites as a lesson while
leaving it standing. Named rather than numbered now, which is the only
form that cannot drift. That is a weaker pin and a real one, and
"nothing pins it" — written here for one round without the preposition
— reads as licence to change a spec to match a `* 3` header and then be
surprised by a red suite.

**`LargestCityFits`'s `2 * MaxUnits` was pinned in neither direction,
and half of that is now fixed.** `claims` changed it to `* 3` for a
clean run; `control` then weakened it the other way — to
`Reserved + MaxUnits <= MaxFds`, and to `MaxUnits <= MaxFds` — and got a
green suite from each, along with `FdBudgetCovers == n <= MaxFds`. The
probe lowered `MaxFds` to 16, where *every* form is false, so it
certified the invariant's name and nothing about its arithmetic.

**The fix is the probe's constant, and it is derived:**
`Reserved + 2*MaxUnits - 1`, where the honest predicate misses by
exactly one and every weakening still holds. All five mutations now
turn the suite red. **A second-way invariant was tried first and does
not work** — `LargestCityFits = (Reserved + MaxUnits + MaxUnits <=
MaxFds)` is true at the real limits whatever the weakening, because both
forms hold whenever `MaxFds` is large. *Two predicates that agree
throughout the legal range cannot pin each other; only a constant that
separates them can.* That is the general lesson and it is worth more
than the fix.

What remains open is the other direction: nothing propagates `blob.h`'s
arithmetic into either spec, so a `* 3` in the header still runs clean.
Deriving the multiplier the way the limits are derived would close it,
and it is unbuilt.

(This said "neither file holds a limit to drift", which the same round's
own work disproved three lines later in `plan.als`; then "one
hand-written number remains", which `claims` disproved by pointing at
`for 8`; then "one that must track the header", which `claims` disproved
again by pointing at the multiplier. Correcting a sentence into an
absolute is how every one of these went wrong, and the pattern is
now the most reliable thing in this file: **if a sentence here counts
something, it is probably wrong.**)

*This section said the opposite until 2026-09-11 — "you cannot verify by
running … no `alloy`, no `tlc`, and nothing in the `Makefile` or
`tests/run.py` references either file" — for a day after all three
clauses became false. `plan.als`'s own copy of that paragraph was
corrected and this one was not, which is the survived-by-being-moved
shape, in the file `tools/rules-hook.sh` hands to the next agent to
touch a spec. `claims` found it.*

**What running them found immediately** is the reason to keep saying
this out loud: neither file PARSED. Alloy refused `plan.als` for a
missing scope; TLC refused `Plan.tla` for a use-before-definition. And
`fdNeed` did not add — Alloy's `+` on `Int` is set union, so the fd
formula, one of the four places invariant 3 names, had never computed
the fd budget. `HISTORY.md` §39.

**What is still not checked, so do not cite it:**

- **TLC's `BindNeed` is only ever evaluated at 0.** It is reachable only
  through `TypeOK`, and `Init` gives every house an empty bind set.
  Measured: adding `BindNeed = 0` as a `TypeOK` conjunct holds in every
  state TLC explores, and `BindNeed # 0` is violated in the initial
  state. Replacing the whole recursion with `BindNeed == 0` runs clean.

  *This was written as a TLA+ limitation, and it was not: `control`
  deleted the bind conjunct from Alloy's `pred sealed` -- half the
  predicate the check is named for -- and the suite passed, because
  `#binds` is capped by the scope times itself while `nwMaxBinds` is
  `NW_MAX_BINDS`, and at today's values the first cannot exceed the
  second, so no counterexample exists at any legal header value.* The
  Alloy half is pinned now, by a third must-fail probe that lowers
  `nwMaxBinds` below what the scope can reach. The TLA+ half is still
  unpinned; exercising it needs an `Init` that ranges over bind sets.
- `plan.als`'s three cross-field facts (`brickNeedsNewNS`,
  `bindsNeedBrick`, `landlockNeedsBrick`) are facts, not assertions, so
  they constrain instances rather than being tested. `control` inverted
  `brickNeedsNewNS` into the plan `nwcheck.c` rejects and every check
  stayed green. Their enforcement is `nwcheck.c` plus
  `test_checker_rejects_crafted_fields`.
- Both results are bounded: Alloy at the scope and bitwidth written on
  the commands in `plan.als` (8 and 12 today, and the suite asserts the
  bitwidth covers `NW_MAX_FDS`), TLC at one state per legal unit count.
  Read the numbers off the commands, not off this line — see the
  `but 12 Int` note above for why the scope has nothing to track.

So a spec still says nothing about descriptor handling at runtime, which
is where every real bug in this project has been. It now says something
checkable about the format, which it did not before.

## Definition of done

`make test` passes, quoted from its own output. **A check you added must be
shown *rejecting* a crafted bad blob**, not merely accepting good ones — see
`test_brick_needs_newns` in `tests/run.py`, which clears a lid bit by
hand and repairs the CRC to build a blob the baker would never emit.
