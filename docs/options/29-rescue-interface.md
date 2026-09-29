# 29 — rescue interface

Status: **design note. No code in this round.** Gate: named by the
operator as "the console house's existing rescue-slot mechanism
(already built)." **Checked against the tree and that premise does not
hold as stated — read the correction below before anything else in this
note**, because the rest of the design has to be built on what actually
exists, not on what the brief assumed exists.

## The premise, checked

**There is no "rescue-slot" mechanism in this tree, built or otherwise
— only two separate, smaller things that could each be part of one.**

1. **PID 1's own rescue mode** (`rescue.c` + `run_rescue()` in
   `pid1.c`, documented at length in `.claude/rules/runtime.md`'s
   "`rescue` — an operator mode" section) is real, built, and TCB —
   but it is the smallest possible thing on purpose: one `write(2)` of
   a fixed string, exit code 3. It is reached only via `--rescue DIR`
   on PID 1's own command line, and **nothing in the real boot chain
   ever passes that flag** — `dawn` always execs `nw-root --slots
   <path>` (`runtime.md`, same section: "the only `--rescue` in the
   tree outside `pid1.c` is in `tests/run.py`"). This is not a slot; it
   is an alternate PID 1 command line that the shipped boot chain
   structurally cannot reach.
2. **The console house** (`docs/options/10-console-house.md`, "resolved
   and built") is a real, tested, interactive busybox `ash` shell
   reachable over `ttyS1`, running as an ordinary house in whatever
   plan declares it. It has no privileged standing and no relationship
   to slot selection — it is one house among others in a booted plan,
   distinguished only by which lids it declares (`newns,landlock,
   newnet`, no seccomp).
3. **A third slot directory, `slots/rescue`, is sketched but not wired
   to anything.** `docs/options/06-disk-layout.md` (status: "options,
   not a decision," 2026-09-10, an early exploratory document, not
   superseded but also not load-bearing the way a later note is) says
   `make stage` creates `slots/A`, `slots/B` and `slots/rescue` as
   directories, and separately speculates "`nw-rescue` *could* run from
   the ESP" — a hypothesis in its own words, not a built mechanism.
   Checked against the current `Makefile`'s actual `stage:` target:
   ```
   $ grep -n 'slots/' Makefile
   176:	mkdir -p $(STAGE)/efi/slots/A $(STAGE)/efi/slots/B $(STAGE)/work
   199:	    --out $(STAGE)/efi/slots/A/plan.blob --lids seccomp
   203:	    --out $(STAGE)/efi/slots/B/plan.blob
   204:	echo A > $(STAGE)/efi/slots/current
   ```
   The `mkdir` line creates only `A` and `B`; the other three hits bake
   into them and write the `current` pointer file, which is lab-fixture
   staging for the test suite, not a shipped mechanism, and none of the
   four touches `slots/rescue`. **`slots/rescue` does not exist in
   the tree that ships today.**

So "console house's existing rescue-slot mechanism" conflates three
things, none of which alone is what the phrase names: a built shell
with no slot relationship, a built PID-1 mode with no console-house
relationship and no reachable path from a real boot, and a slot
directory that was proposed once and never wired up. **This is the one
genuinely open question this note flags for the operator, per the
standing instruction to flag only real ones**: which of these is meant
to be the foundation, or is a fourth thing — a real rescue *slot* that
boots a plan containing the console house, wired the way `slots/A`/`B`
are — meant to be built first, as a prerequisite this note should name
rather than design around. The rest of this note answers the four
sub-questions against the best available reading — **a rescue slot
that boots an ordinary plan containing the console house plus a new,
structured tool, reached the same way `slots/A`/`B` are reached today**
— because that is the only reading under which "distinct from the
interactive shell already in the console house" (the operator's own
framing) makes sense: the interactive shell is the console house's
`ash`; this note's tool is a second program reachable from the same
booted plan, not a replacement for the shell and not dependent on
`rescue.c` at all.

## 1. What it does

Four capabilities, each checked against what already exists to build on
rather than invented fresh:

- **Browse crash evidence.** The shared store already holds this data —
  `nw_store_put()`'s `/nw/evidence/<hex>.evt` files (`store.h`,
  `write_evidence()` in `nwsup.c`). A read-only lister/viewer over that
  directory, no new format: `docs/options/12-crash-evidence.md` already
  defines what an evidence record contains: this tool's "browse" verb
  is a thin read-and-format layer over files that already exist and are
  already named for direct access (no index needed — the directory
  listing *is* the index, same insight `docs/options/30-backup-tool.md`
  §"Why compare hash lists" makes about this same directory).
- **Inspect house states from last real boot's records.** Two existing
  sources answer this without new mechanism: the evidence store above
  (crash records) and `nw-sup`'s own control-socket `STATUS` reply
  (`docs/options/11-start-stop-channel.md`) for a house that is
  currently running *in the rescue-slot's own plan* — this tool cannot
  ask the previous, non-rescue boot's `nw-sup` processes anything, since
  those processes no longer exist once the machine has rebooted into
  rescue. "Last real boot's records" therefore means the evidence store
  (persistent) and boot-time output already captured to the log ring
  (`.claude/rules/harness.md`'s log-chunk material), not a live query
  of a boot that is over.
- **Trigger rollback to the other slot.** This is the sharpest gap
  against the existing tree, not a small one: **`tools/stage-candidate.py`
  explicitly, deliberately never writes `<slots>/current`** — its own
  comment: "It also never writes `current`... an earlier version cited
  invariant 7 and over-reached: writing `current`... candidate and
  switch by writing `{slots}/current` yourself." So today, *nothing in
  this tree* flips which slot boots next; it is a manual operator
  action outside any tool. A rescue tool that "triggers rollback" is
  therefore proposing the **first** piece of code in this project that
  writes `<slots>/current` — worth being explicit about, because it is
  new ground, not a wrapper over an existing verb.

  **Whether this is compatible with invariant 7 ("the live city does
  not grow verbs... a new plan is a new slot, never an in-place
  rewrite") is worth stating precisely rather than assuming either
  way.** Invariant 7 is about the *running* plan never being mutated in
  place — it says nothing about which slot the *next* boot reads, and
  `slot_from_current`'s own contract (`pid1.c`, quoted in
  `tools/stage-candidate.py`'s comments: reads at most `NW_NAME_LEN`
  bytes, validated to `[A-Za-z0-9_-]`) is a boot-time read of a small,
  bounded, already-validated file — writing it is not growing a verb in
  the live city, it is choosing which already-sealed, already-validated
  plan the *next* boot will read, which is exactly what promoting a
  candidate already does conceptually, just not yet automated. This
  note's reading: **rollback is in scope and does not reopen invariant
  7**, because it writes a bounded, validated selector between boots,
  never touches a running plan, and is symmetric with what an operator
  already does by hand. Flagged rather than assumed, because it is the
  one place this tool's design touches a rule with a name.
- **Phone-reachable fallback if the main control app is unreachable**
  (cross-ref `docs/options/27-control-app.md`). Deferred entirely to
  §3's transport question below — this capability is not a fourth
  thing to design, it is "the same tool, reached over whatever
  transport §3 settles on," so it costs nothing extra to state as a
  capability here and everything to actually build, which is exactly
  why `docs/options/27` is where the transport question lives.

## 2. Where it lives

**Recommendation: extend `nwctl`'s command set, not a separate minimal
tool — with the caveat spelled out rather than glossed over.**
`docs/OPERATOR-BRIEF.md`'s Phase 4 already commits to a "minimal nwctl
(list, start, stop, small status reply)" shipped in the compositor
brick. A rescue-mode command set (`nwctl rescue-list-evidence`,
`nwctl rescue-status`, `nwctl rescue-rollback <slot>`, or a `nwctl
rescue <subcommand>` prefix) reuses the same wire format, the same
static-binary-in-a-brick shipping shape, and the same "propose, never
decide" posture (§4) `nwctl` already has, rather than inventing a
second CLI with its own conventions for what is conceptually the same
kind of tool talking to the same kind of thing.

**The caveat the "extending nwctl" recommendation needs to survive:**
`nwctl start`/`stop`/`status`/`why`/`times` all talk to a **running**
`nw-sup`'s control socket (`docs/options/11`). This tool's own "browse
evidence" and "rollback" verbs need to run **without** any `nw-sup`
being up at all — the rescue slot might boot with nothing running yet,
or the operator might want to browse evidence and decide to roll back
*before* choosing to start anything in the rescue slot's own plan. So
"extend nwctl" means nwctl grows a mode that does NOT require a control
socket for these two verbs specifically, alongside its existing
control-socket-dependent verbs — a real fork in nwctl's own internal
shape, not a free addition. **This is why the operator's own phrasing
— "recommend extending nwctl if it can run standalone without the rest
of the city up" — is the right question to have asked**: checked
against what nwctl is speced to do so far (a client of a socket that
only exists once a house's `nw-sup` is running), the honest answer is
that *today's speced nwctl* cannot run standalone, and this note's
recommendation requires that gap to close as part of building this
tool, not as a precondition already met.

## 3. Transport: the tunnel, or local-only?

**Recommendation: local-only for this tool, explicitly narrower than
`docs/options/27`'s phone-reachable control app, and for a reason
specific to what "rescue" means.** `docs/options/27`'s own reasoning for
recommending a network tunnel is that the control app's whole point is
convenience from off-machine, normal operation. Rescue mode is the
opposite case by construction: **it implies the normal network path may
not be up at all** — the same reasoning the operator's own sub-question
3 states ("rescue implies possibly no normal network access either").
A tunnel (Tailscale or otherwise) is itself a userspace daemon with its
own dependencies (a working network interface, DNS or a relay reachable,
a valid device key) — every one of which is exactly the kind of thing
that might be the reason the machine needed rescue in the first place.
Building the rescue path's own reachability on the same stack that a
broken machine might have broken is the wrong dependency direction.

So: **direct serial/console access is the right default for this
tool**, matching how the console house itself is already reached
(`ttyS1`, no network stack involved at all). The phone-reachable
fallback capability named in §1 is real but should be read as: *if* the
control app's own tunnel happens to still be up when the main app isn't
reachable for some other reason (the app crashed, a bug in it, the
compositor brick is what's broken), *then* this tool being reachable
over that same tunnel is a bonus, not something this tool's own design
should assume or depend on. **Recommendation: build for serial-only
first; treat "also reachable over the tunnel if it happens to be up"
as free, since it is the same wire protocol nwctl already speaks either
way, and add nothing rescue-specific to the transport layer itself.**

## 4. Commitment check

**Read-only browsing and operator-triggered actions only, nothing
automatic** — matching the operator's own framing and consistent with
`docs/options/27`'s identical commitment for the control app ("this app
never decides anything, only proposes/executes operator-issued
actions"). Concretely: evidence browsing and status inspection are pure
reads with no side effect; rollback is a single, explicit,
operator-issued write to `<slots>/current` (§1), never triggered by a
detected condition, a timeout, or a retry count — this project's
Liveness section already refuses exactly that class of automatic
decision-from-a-guessed-constant, and a rescue tool automatically
deciding to roll back on some detected signal would be freeze detection
wearing a different hat. Nothing here proposes it, and this paragraph
states the refusal explicitly so a future reader does not propose it
either without re-deriving why it was refused.

## Definition of done

This is a docs-only note. No code changes. The open question in
"The premise, checked" — which of the three existing pieces (or a
fourth, newly-built rescue slot) this tool is meant to sit inside — is
handed back to the operator rather than guessed at silently.
`docs/QUEUE.md`'s new-notes entry is updated in the same commit that
lands this note, marked GATED on that answer as well as on Phase 4's
`nwctl`.
