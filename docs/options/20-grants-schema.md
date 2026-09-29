# 20 — grants table, schema generator, machine hash, provides/needs

Status: **design note. No code in this round.** Answers
`docs/OPERATOR-BRIEF.md` Section 2 item 1g's scope for this note ("grants
table; schema generator with the completeness rule that every field
names an applier, an observer, a reject path and a test; the machine
hash under decision 4") plus the amendment's provides/needs item
(`docs/OPERATOR-BRIEF.md` amendment, Phase-4 list item under Section 3:
"Services declared provides/needs follow note 20: the baker resolves
them, requires exactly one provider per service, and PRINTS the edges it
added.").

Every claim below was checked against the tree at the time this note was
written; grep the cited lines rather than trusting the prose if this
note and the code ever disagree.

## 1. The four-part schema against the existing mechanism rule

CLAUDE.md already has a rule for this — "Adding a field to the plan —
the mechanism rule" (`CLAUDE.md:546-600`), adopted 2026-09-14 after
`nw_res` shipped declared, baked and validated with no runtime consumer.
Its five clauses: (1) every field names exactly one mechanism that
makes it true at runtime; (2) every named mechanism serves at least one
field; (3) no field is served by more than one mechanism; (4) a field
whose mechanism is a single translation unit must name that unit and
have a test exercising the consumer; (5) descriptive metadata is a
separate class and not a configuration field. Plus one obligation
outside the five: every field needs a test that *runs* and fails when
its mechanism is absent — a `SKIP` counts as a failure, not a neutral
result.

This note's four-part vocabulary — **applier, observer, reject path,
test** — is not a replacement for that rule. It is a stricter,
separately-checkable decomposition of the same requirement, and two of
its four parts have no clause of their own in the existing rule:

- **Applier** and **test** are already there: clause 1 is the applier
  requirement in different words, and clause 4 plus the closing
  obligation are the test requirement, close to verbatim.
- **Reject path** — a field that cannot yet be applied must be refused
  by name rather than silently ignored — is practiced widely in this
  tree (every `NW_E_*` code in `nwcheck.c` and every baker-side refusal,
  e.g. `apply_sched_ext()`'s real `die("sched-ext unsupported")`/
  `die("sched-ext no policy artifact")`, `nwsup.c:688-706`) but is not
  one of the five numbered clauses, and is not yet practiced for every
  field: `.claude/rules/runtime.md`'s "a resource limit is not
  advisory... the house does not start" is explicitly introduced there
  as "proposed and not yet ratified," and today's resource-block fields
  (§2's table below) have range checks but no applicability refusal —
  exactly the gap that rule is meant to close once it is. Nothing in the
  mechanism rule as written would flag a field that has an applier and a
  test but silently accepts a value it cannot enforce.
- **Observer** — a kernel or `/proc` read-back confirming the applier
  actually took effect, as opposed to a test that only checks the
  *behavioral consequence* — is likewise unnamed in the five clauses,
  and is unevenly practiced today: `CapBnd`/`CapEff`/`CapPrm`/`CapAmb`
  read-back is explicit for the not-yet-built capabilities field
  (`docs/OPERATOR-BRIEF.md` amendment item A.1), and the brick hash is
  explicitly re-validated at the supervisor
  (`test_brick_hash_revalidated_at_the_supervisor`), but most existing
  fields (`kind`, `budget`, the lids) are checked only by their
  behavioral effect, with no distinct read-back step.

**Recommended default: keep CLAUDE.md's rule as the prose statement of
intent, and let a schema generator (§2 below) be the machine-checkable
form of a stricter version that additionally requires a reject path and
an observer, named per field.** This note does not edit CLAUDE.md — the
five clauses are still true and still the right prose statement of why
the rule exists (the `nw_res` incident). Widening CLAUDE.md itself to
name reject-path and observer as first-class clauses is a legitimate
follow-up, but it is a change to the master invariant file itself and is
left to a separate, explicitly-authorized edit rather than folded into a
docs-only note about a different file.

## 2. The grants table — every current field, against all four parts

"Grants table" is read here as: an inventory of every plan field that
grants a house some capability or resource, against applier / observer /
reject path / test. This is that inventory, checked directly against
the tree (`struct nw_unit`, `blob.h:425-467`; `struct nw_res`,
`blob.h:407-423`; `struct nw_bind`, `blob.h:477-480`; `struct nw_edge`,
`blob.h:497-500`).

| Field | Applier | Observer | Reject path | Test |
|---|---|---|---|---|
| `lids` (each bit) | yes — `nwsup.c:1396-1416` branches per bit | partial — Landlock has none ("can't be inspected any other way", `docs/OPERATOR-BRIEF.md` §41); behavior-tested instead | yes — `NW_E_LIDS` (unknown bits), `NW_E_LLBRICK`/`NW_E_BRICKNS` (bit-pair contradictions) | yes — `test_lids_are_not_advisory`, `test_brick_needs_newns`, `test_landlock_bind_to_a_file` |
| `kind` | yes — `nwsup.c:1112,1458` (oneshot/longrun restart logic) | none distinct — behavior-tested via exit handling | yes — `NW_E_KIND` | yes — `test_kind_required`, `test_kind_exit0` |
| `budget` | yes — `nwsup.c:1111,1482-1500` (death count, spent/restart) | none | none dedicated — full uint8_t range is legal (0 = no restart) | yes — `test_budget_is_hard_total`, `test_relaunch_does_not_count_against_the_real_budget` |
| `sched_ext` | no accept path by design — `apply_sched_ext()` (`nwsup.c:688-706`) only ever `die()`s; **being deleted in Phase 4** | n/a — deleted field | yes — `NW_E_SCHEDEXT` plus supervisor-side `die()` | yes, for the reject side only |
| `brick`/`layer` | yes — `nwsup.c` mounts the erofs image and the layer store | yes — brick hash re-validated at the supervisor | yes — `NW_E_LAYER`, `NW_E_LAYERPAIR`, `NW_E_CAPNOLAYER`, `NW_E_LAYERDUP` | yes — `test_brick_is_a_root`, `test_brick_image_is_sealed`, others |
| `layer_bytes` | yes — loop-mounted fixed-size ext4 (`.claude/rules/runtime.md`'s layer-capacity section) | yes — capacity enforced and measured via `statvfs(2)` from inside the house | yes — `NW_E_CAPNOLAYER`; representation-switch refusal at stage time | yes — `test_layer_bytes_enforces_capacity`, `test_layer_bytes_representation_switch_refused`, `test_layer_bytes_without_layer_dies_at_the_supervisor` |
| `cpu_mask`, `mem_high`, `mem_max`, `cpu_weight`, `nice`, `sched_policy` | **none** — confirmed, `grep -n "res\." nwsup.c` returns nothing; Phase 3, NOT STARTED | none | partial — range/relation checks exist (`NW_E_RESWEIGHT`, `NW_E_RESSCHED`, `NW_E_RESNICE`, `NW_E_MEMORDER`, `NW_E_NICEPOL`) but these validate legality, not applicability, and clause 1 of the mechanism rule says validation is not a mechanism | reject-side only — `test_baker_refuses_bad_resources`, `test_checker_rejects_crafted_resources`; nothing tests applying any of these at runtime, because nothing applies them yet |
| `io_rbps`, `io_wbps` | none, **being deleted in Phase 4** | n/a | same partial reject-only state as above | reject-side only |
| binds (`struct nw_bind`) | yes — `MS_BIND` mounts, two-step read-only remount for `NW_CTL_DIR` | partial — a read-only bind's write-refusal is an automated test assertion (`test_ctl_dir_hardening`, `EROFS` on write); the connect-still-succeeds half is a one-time measurement recorded in prose (`.claude/rules/runtime.md`, `docs/OPERATOR-BRIEF.md` item 1f), not a repeatable suite assertion | yes — `NW_E_BINDIDX`, `NW_E_BINDPATH` | yes — `test_checker_rejects_crafted_binds`, the control-socket hardening tests |
| edges (`struct nw_edge`) | yes — `socketpair()` wiring in `nwspawn.c` before the fork loop | yes — bidirectional exchange is directly tested | yes — `NW_E_EDGEIDX`, `NW_E_EDGEDUP`, `NW_E_EDGES` (count cap) | yes — `test_edge_bidirectional_exchange`, `test_edge_unwired_house_gets_no_wire`, `test_many_houses_many_edges_no_collision`, `test_edge_backpressure`, `test_checker_rejects_crafted_edges` |

**The gap this table makes visible, precisely**: every `struct nw_res`
field except `layer_bytes` has a reject path but no applier, no
observer, and no test on the applying side — which is not a new
finding (`.claude/rules/runtime.md`'s "The resource block is in the
plan and nothing applies it" already says so, and Phase 3 is the
scheduled fix) but is now stated as a table row rather than prose, which
is the form a schema generator (§3) can check mechanically. `sched_ext`
and the two `io_*` fields are the mirror case — correctly reject-only,
because they're being deleted rather than completed.

**On the historical `nw_res` defect versus today's struct**: they are
the same struct, not a naming collision. `HISTORY.md` §75 (2026-09-13)
introduces the resource block; CLAUDE.md's mechanism rule is adopted the
next day, citing `nw_res` by name as the motivating defect
(`CLAUDE.md:548-550`); and `nwsup.c:696`'s own comment still calls it
out directly today ("nw_res's own defect... declared, validated... and
doing nothing"). `layer_bytes` is the one field that has since escaped
the defect class, by gaining a real applier. The rest are the still-open
instance of the exact thing the rule exists to prevent, currently
excused only by being explicitly scheduled (Phase 3) rather than
forgotten.

## 3. The schema generator — design, not code

Modeled on `tools/gen-spec-limits.py`, which already generates spec
values out of `blob.h` rather than letting them drift (invariant 3's
mechanism). This generator would do the analogous thing for the
mechanism rule: read one manifest naming, per field, which function
applies it, which read-back (if any) observes it, which `NW_E_*` code
(or explicit "none — full range legal") rejects a bad value, and which
test name exercises the consumer — and fail the build when a field is
declared with any of the four left blank and no stated reason (clause 5
of the mechanism rule already carves out descriptive metadata as
needing none of this; the generator's manifest would need the same
carve-out, named per field rather than inferred).

**What it checks that a person reading `CLAUDE.md`'s rule today has to
check by hand**: that the named applier function still exists and still
references the field (not merely that a comment claims it does — this
is exactly `nw_res`'s failure mode, and exactly why clause 4 requires
naming a translation unit rather than a category); that the named
`NW_E_*` code still exists in the enum and in `errs[]`, in order,
per `.claude/rules/plan.md`'s own rule for adding a check; and that the
named test function exists in `tests/run.py` and is not skipped.

**What it does not and cannot check**, stated because this project's
own rule ("say what a check catches and what it doesn't") demands it:
it cannot verify that the named applier does the *right* thing, only
that a function by that name touching that field exists — the same
limitation `tools/rules-hook.sh --check`'s territory census has for
file ownership, applied here to fields instead of files. Getting the
applier *correct* is still `control`'s and `tcb-review`'s job, not a
generator's.

**Where it would live**: non-TCB, alongside `tools/gen-spec-limits.py`,
under `plan`'s territory (`.claude/rules/plan.md`'s scope already covers
"a structural check" and the existing generator). This week's ownership
line (`CLAUDE.md:932-934`) names specific files — `pid1.c`/`dawn.c`/the
restart loop for Grok, the baker/the mount path for Claude — and does
not separately grant either agent the whole `plan` territory, so it is
not itself the source of an ownership claim here; the file class this
generator would join (the baker's own tooling, which the ownership line
does name) is the reason to expect no conflict, not a blanket grant.

**Not built this round.** This note is the design; `docs/QUEUE.md`
tracks the build as a Phase-4-adjacent item, since the manifest is most
useful once Phase 4's new fields (capabilities, task cap, OOM priority)
land with their own applier/observer/reject-path/test already named in
the amendment text — building the generator before those fields exist
would have nothing but the existing, already-audited table above to
check.

## 4. The machine hash (decision 4)

Decision 4, quoted in full: "Machine hash: computed over the exact blob
bytes already in hand (in nw-spawn), printed at boot and stamped into
evidence. NO sidecar hash file. The baker prints the same value at
bake."

**What already exists and needs no change**: the baker's bake-time
print. `bakery/nw-cc.py:498-500` prints `sha256=<digest>` where
`digest = hashlib.sha256(blob).hexdigest()` (`bakery/nw-cc.py:466`) over
exactly `prefix + crc32 + unit table + bind table + edge table`
(`bakery/nw-cc.py:459-463`) — the identical bytes written to the plan
file. Since `nw-spawn` reads that same on-disk file verbatim
(`nwspawn.c:268-281`, `read(bfd, blob, sizeof blob)` then
`nw_check(blob, n)`), a boot-time sha256 over `blob[0..n)` would produce
the identical digest the baker already prints. **The baker half of
decision 4 is already satisfied by existing behavior.**

**What is new**: `nw-spawn` does not hash anything today —
`nwspawn.c` does not include `sha256.h` and calls no hash function on
`blob`. Neither does the boot path print such a value, nor does
`write_evidence()`'s record (`nwsup.c:1078-1080`,
`"NWEVT1\nunit=%s\nreason=%s\nvalue=%d\ndeath=%d\nbudget=%u\ntail_bytes=%zu\n--\n"`)
carry a hash field. Both are genuinely new code: compute the sha256 (the
primitive already exists — `sha256.c`/`sha256.h`, already used for the
evidence record's own filename) over the blob bytes `nw-spawn` already
holds, print it at the point `nw-spawn` reports boot success, and pass
it down to `nw-sup` (the same way `NW_LAYER`/`NW_BRICK` etc. already
travel, as an environment string) so `write_evidence()` can add it as a
new field.

**The one genuine open question, flagged rather than decided silently**:
"NO sidecar hash file" reads naturally as "don't invent a new sidecar to
carry the boot-time machine hash" — compute it from bytes already in
hand instead of trusting a file beside them, which is the same
integrity posture as invariant 8 (verify, don't merely read). It does
**not**, on this note's reading, mean removing the *existing*
`<blob>.sha256` sidecar the baker already writes
(`bakery/nw-cc.py:467`), because that file serves a different, already
load-bearing purpose: `tools/stage-candidate.py:429-432` and
`tools/stage-layers.py:130-134` both read it and compare it against a
freshly computed hash of the live blob — "checked through `.sha256`
rather than by parsing the blob" (`tools/stage-candidate.py:424`
comment) — and its exact rename ordering relative to `.layers` is itself
pinned by tests (`tests/run.py:9777-9795`, `test_candidate_stager_...`
family, `tests/run.py:9363-9371`, `tests/run.py:11014-11017`). Removing
it would mean reworking tested staging machinery that decision 4 never
mentions and that solves a different problem (staging-time identity
before boot, versus decision 4's boot-time machine-hash-in-evidence).
**Recommended default: decision 4's "no sidecar" is scoped to the new
boot-time machine hash only; the existing bakery/staging `.sha256`
sidecar is untouched.** This is the one item in this note that rests on
reading an operator decision rather than on a code fact, so it is named
here explicitly rather than folded silently into the design.

**Not built this round** — this is Phase 4 territory (the plan-format
bump's evidence-format version bump already covers adding a field to
the record); this note states the design so that work does not have to
re-derive it.

## 5. `provides`/`needs` service resolution

No existing design sketch exists anywhere in the tree — checked by
grepping the whole repository (`docs/`, `.claude/rules/*.md`, and the
code) for "provides"/"needs" in a plan/service sense; the only two
occurrences of the exact phrase are one-line forward references to
this very note (`docs/OPERATOR-BRIEF.md`'s amendment and
`docs/QUEUE.md`'s Phase-4 list), neither of which elaborates a
mechanism. This is that mechanism's first design.

**It compiles down to the existing edge mechanism and adds nothing to
the blob.** `provides=<name>` and `needs=<name>` are baker-side syntax
only: a unit may declare `provides=<name>` zero or more times (a house
can offer more than one named service) and `needs=<name>` zero or more
times. Before emitting the blob, the baker resolves every declared
`needs=<name>` to the unique unit declaring `provides=<name>` in the
same plan and adds an ordinary edge between the two, exactly as if the
plan had spelled that edge out by unit name directly — "the baker
resolves them... and PRINTS the edges it added"
(`docs/OPERATOR-BRIEF.md` amendment) is precisely this: the blob's edge
table is the only representation that ever reaches `nwcheck.c` or
`nw-spawn`, and it is unchanged by this feature. **No new `NW_E_*` code,
no new blob field, no `nwcheck.c` change, no runtime change** — the
existing edge cap (`NW_E_EDGES`) and duplicate check (`NW_E_EDGEDUP`)
already cover whatever the resolver produces, because what it produces
is just more rows in the same table.

**Refused at bake time, by name, with the exact count the brief asks
for**: zero units declaring `provides=<name>` for a needed `<name>` is
refused ("`needs <name> matches no declared provides`"); more than one
unit declaring the same `provides=<name>` is refused
("`provides <name> declared by more than one unit`") — "exactly one
provider per service" from the brief, enforced where the name resolves,
which is bake time only, since the name itself never survives into the
blob.

**This is not the deferred `gate` field, and does not reopen it.**
`.claude/rules/plan.md`'s "Waiting on a prerequisite" section already
defers `gate` — "a unit naming another whose socket must exist before
it is forked" — explicitly because it needs a pre-fork wait mechanism
with a bounded constant, which is unbuilt and has "no house that needs
it." `provides`/`needs` as designed here carries no ordering semantic
at all: it produces an undirected connectivity edge, wired the same way
every other edge is (`nwspawn.c`'s `socketpair()` calls, all completed
before any unit's fork loop begins, per `docs/options/17-edges.md`), and
says nothing about which end starts first. A house that `needs` a
service and is forked before its provider gets a live socket end with
nothing listening on the other side yet — exactly the situation every
edge already tolerates today, since edges carry no dependency semantics
by design (`.claude/rules/plan.md`'s cycle-detection refusal: "an
undirected connectivity edge has no dependency semantics"). If a future
plan genuinely needs "wait for the provider's socket before forking,"
that is `gate`, unchanged and still deferred, not this feature.

**Not built this round** — Phase 4 territory, tracked in
`docs/QUEUE.md`'s Phase 4 list already.

## Definition of done

This is a docs-only note. No code changes. `docs/QUEUE.md`'s item 1g
entry is updated in the same commit that lands this note.
