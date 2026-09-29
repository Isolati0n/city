# 29 — rescue interface

Status: **design note. No code in this round.** Foundation: `nw-rescue`
(`rescue.c` + `run_rescue()` in `pid1.c`) — real, wired, tested by the
suite today, and a deliberate placeholder; this note is its real
design. Not gated on Phase 4's `nwctl` (§2). One TCB-adjacent question
is genuinely open and named in "A real tension" below, needing the
operator's sign-off before code, not this note's completion.

Originally named by the operator as "the console house's existing
rescue-slot mechanism (already built)." **A first pass of this note
read that premise as not holding, because the specific phrase
"rescue-slot" names nothing built in the tree.** The operator corrected
this by reading `rescue.c` and `run_rescue()` directly. What survives
from the first pass is the two things that were true regardless of
which foundation was meant (the console house has no rescue
relationship, and nothing in the shipped boot chain currently reaches
`nw-rescue`), because those are still checkable facts that the design
has to account for, not premises to relitigate.

## The foundation

**`nw-rescue` exists, is correctly wired, and is exercised by the test
suite today** — the operator's correction, confirmed rather than taken
on trust:

```
$ sed -n '453,467p' pid1.c
static int run_rescue(const char *slot)
{
    char path[512];
    snprintf(path, sizeof path, "%s/nw-rescue", slot);
    say("rescue slot", path);
    pid_t p = fork();
    if (p < 0) halt_now("rescue fork");
    if (p == 0) {
        execl(path, "nw-rescue", (char *)0);
        halt_now("exec rescue");
    }
    int st = 0;
    waitpid(p, &st, 0);
    _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 3);
}
```

This is real, tested code, not a sketch: `tests/run.py`'s `test_rescue`
invokes `nw-root --rescue <dir>` directly and asserts on `nw-rescue`'s
own output and exit code, and that test passes on every `make test`
run. **This is the note's real foundation** — the design below is
`nw-rescue`'s real body, replacing the placeholder message
`rescue.c` writes today, not a fourth mechanism invented beside it.

**Two things from the first pass are still true and still shape the
design, precisely because they are not about whether `nw-rescue`
exists:**

- **The console house has no relationship to any of this.**
  `docs/options/10-console-house.md`'s interactive `ash` shell over
  `ttyS1` is one ordinary house in a booted plan (`lids=newns,
  landlock,newnet`), with no privileged standing and no connection to
  `--rescue`/`slot_from_current`. The operator's original phrasing named
  it as part of the foundation; it is not part of this one, and this
  note's tool is unrelated to that shell rather than an extension of
  it — worth stating plainly so the two are not conflated going
  forward.
- **Nothing in the shipped boot chain reaches `nw-rescue` on a real
  boot, and that is a separate, smaller gap from what the first pass
  called an absence.** `pid1.c`'s own dispatch — `if (rescue && !plan
  && !slot && !slots) return run_rescue(rescue);` — checks only that
  none of `--plan`/`--slot`/`--slots` is also given, not that
  `--rescue DIR` is literally the only argument on the line (a stray
  `--hold-ms`/`--kill-spawner` alongside it still reaches
  `run_rescue()`, confirmed directly: `unshare --pid --fork
  --mount-proc -- nw-root --rescue <dir> --hold-ms 400` still prints
  `rescue slot ...` and exits 3) — and `dawn.c` unconditionally
  execs `nw-root --slots <path>` with no conditional path that ever
  passes `--rescue` (`dawn.c:281`). So the mechanism this note designs
  the *inside* of is not, today, reachable from a real power-on —
  reaching it needs a boot-chain change (a kernel command-line switch,
  a bootloader menu entry, or dawn choosing `--rescue` under some
  condition), which is `dawn.c`/`pid1.c` territory
  (`CLAUDE.md`'s "Who owns which file": Grok's) and a different task
  from designing what `nw-rescue` does once reached. This note designs
  the tool; reachability is named here as a real, connected, but
  out-of-scope follow-up, not solved silently by assuming it and not
  hidden by staying quiet about it.

## A real tension this correction surfaces, and the recommended resolution

**Growing `nw-rescue`'s own body into a structured tool is in direct
tension with an already-enforced rule about that exact file, and this
note has to say so rather than build past it.** `.claude/rules/
runtime.md`'s "`rescue` — an operator mode" section states, in the
present tense, checked and current:

> `rescue.c` is the smallest thing in the TCB and the rule is to keep
> it that way. It writes a fixed string to fd 2 and returns 3: one
> `write(2)` IN THE SOURCE, no parsing, and `int main(void)`, so no
> argument handling at all... Anything that makes the SOURCE need a
> second syscall is a request to put logic in the one component whose
> value is that it has none.

This is a kind-1, enforced-now rule about `rescue.c` specifically, with
its own adversarial review already run against it ("`claims` measured
it"). Writing the browse/status/rollback logic §2 below describes
directly into `rescue.c` — argument parsing, directory listing, socket
I/O — would make every one of those clauses false at once. This note
does not have standing to quietly override a rule that specific and
that recently checked; it names the conflict and proposes the
resolution that keeps both things true, rather than picking a side by
default.

**Recommended resolution: `rescue.c` gains exactly one new capability
— execing a designated non-TCB companion tool — and stays otherwise as
minimal as today.** Concretely: `rescue.c` still has no argument
parsing of its own (it is exec'd with a fixed, single argv slot by
`run_rescue()`, unchanged), but instead of `write(2)`ing a fixed string
it does one unconditional `execv()` of a fixed, compiled-in path to a
companion binary (or, if that binary is missing — a burned image built
before this lands — falls back to today's fixed message, so an old
image degrades exactly as it does now rather than failing differently).
That companion binary carries every byte of real logic (§2), and needs
its own justification for the one thing `rescue.c` gains: `execv()` is
a single, named, auditable addition — not "a second syscall" in the
open-ended sense the rule warns about, but the *specific*, minimal
syscall that turns "the binary is a message" into "the binary is a
messenger," which is the smallest change that gets from a placeholder
to a real tool without moving the real tool's logic into the TCB.

**Why the companion binary would be non-TCB, stated precisely rather
than by a loose analogy — a first version of this paragraph got the
analogy wrong and it is worth recording why.** It first argued this
from `docs/options/13-crash-relaunch.md`'s "not TCB — no boot-time
caller links it in, the same classification `tools/initrd-init.c`
already has despite also being C." That premise doesn't hold for
`tools/initrd-init.c` itself: the kernel invokes it automatically as
the initrd's own `init`, unconditionally, and it is itself the direct
caller that `exec`s into `dawn` — a real boot-time caller, not an
absent one. So "no boot-time caller" cannot be what keeps
`initrd-init.c` out of the TCB, and citing it as this note's precedent
for a binary that (unlike `unit-info.c`, which truly has no caller
anywhere) *would* be `execv()`'d automatically and unconditionally by
`rescue.c` on every rescue entry was the wrong half of that file's own
sentence to lean on. What actually classifies `initrd-init.c` as
non-TCB is `CLAUDE.md`'s TCB table itself: a closed, explicit
enumeration of specific files ("the specific files in the boot chain,
not 'anything written in C'"), not a rule derived from whether
something has a caller or runs automatically. Read that way, the
precedent is real and stronger than the first version claimed: a
binary invoked automatically, unconditionally, by a TCB file, and
itself doing nothing more than dispatching into the next stage, is
already exactly `initrd-init.c`'s shape and is already, today, outside
`CLAUDE.md`'s table — so the proposed companion binary being non-TCB
rests on the same table staying an explicit enumeration that does not
grow to include it by role, which is the actual thing `tcb-review`
and the operator's amendment (below) need to confirm, not "no caller."

**This is the one genuinely open question left for the operator**,
raised because it touches an already-stated, currently-enforced rule
rather than because the note is unsure what to build: landing this
design needs `.claude/rules/runtime.md`'s rescue section amended to
carry this one exception (the exec, and why it is scoped the way it
is), which is a TCB-adjacent rule change and should get `tcb-review`
alongside the code that implements it, not merely a documentation
edit — and `tcb-review` should specifically re-examine the classification
argument just corrected, since it is the load-bearing part of the
recommendation and this note found its own first version of it wrong
once already. The recommendation above is this note's default answer,
offered so the note does not sit blocked on the question; the
operator's own sign-off is what the standing rule ("no reason for the
note to reopen what the operator has already decided") would treat
this as needing, since it changes a sentence CLAUDE.md's own
discipline currently reads
as a live invariant.

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
- **Inspect house states from last real boot's records.** Checked
  against `run_rescue()`'s own control flow (quoted in "The foundation"
  above) rather than assumed: `pid1.c`'s dispatch returns from
  `run_rescue()` **before** the normal plan-loading path runs at all —
  rescue mode boots no plan and starts no houses, so there is no live
  `nw-sup` and no control socket for this tool to query, structurally,
  not merely "not yet." (An earlier draft of this section assumed a
  "rescue-slot's own plan" with houses in it and a live `STATUS` reply
  to read; that was wrong for the same reason the old foundation
  section was — it modeled rescue mode as an ordinary boot rather than
  reading what `run_rescue()` actually does.) So "inspect house states"
  can only mean **persisted** records: the evidence store (crash
  records from whichever boot wrote them) and, separately, reading the
  currently-sealed plan blobs directly out of `efi/slots/A` and `/B`
  the same way `tools/unit-info.c` already does for
  `docs/options/13-crash-relaunch.md` — showing what the *last bake*
  declared for a unit, not what it was doing while running. No live
  query is possible in this mode, so none is designed.
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
  already does by hand. Flagged rather than assumed, because it is a
  point where this tool's design touches a rule with a name — the
  other is "A real tension" above, and the two are independent: this
  one reads as resolved by the argument just given, that one is left
  open for the operator.
- **Phone-reachable fallback if the main control app is unreachable**
  (cross-ref `docs/options/27-control-app.md`). Deferred entirely to
  §3's transport question below — this capability is not a fourth
  thing to design, it is "the same tool, reached over whatever
  transport §3 settles on," so it costs nothing extra to state as a
  capability here and everything to actually build, which is exactly
  why `docs/options/27` is where the transport question lives.

## 2. Where it lives

**Recommendation, reversed from this note's first draft by the
correction above: a separate, purpose-built companion tool — the one
`rescue.c` execs (per "A real tension" above) — not `nwctl`'s own
command set, because the two run in operating contexts that do not
overlap at all, not merely contexts that differ by degree.**

`nwctl start`/`stop`/`status`/`why`/`times` (`docs/OPERATOR-BRIEF.md`
Phase 4) are every one of them a client of a **running** `nw-sup`'s
control socket. §1's correction establishes that rescue mode boots no
plan and starts no houses at all — there is no socket for `nwctl`'s
existing verbs to dial in this mode, ever, not until the operator
separately chooses to boot a real plan. So this is not "does `nwctl`
need a standalone mode alongside its control-socket verbs" (the
question this note's first draft asked, following the operator's own
phrasing) — it is "does `nwctl`'s entire reason to exist apply here at
all," and the honest answer is no: every one of `nwctl`'s speced verbs
needs exactly the thing rescue mode does not have.

**What the companion tool shares with `nwctl` is convention, not
identity:** the same wire-format style if one is ever needed (there is
no live socket to need one against, here — evidence/blob reads are
local file I/O, and rollback is a local file write), the same CLI
shape, and code it can genuinely share — `tools/unit-info.c`'s
already-built pattern for reading a sealed blob's fields without a
second hand-written struct layout (`docs/options/13-crash-relaunch.md`)
is exactly the library this tool's "read the last bake's declarations"
capability should reuse, not reimplement. Building it as a distinct,
small binary rather than a mode of `nwctl` also keeps `nwctl` itself
simpler: it never has to answer "what do I do when there's no socket,"
because that question belongs entirely to this other tool.

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
(`ttyS1`, no network stack involved at all) — and §2's correction
makes the case stronger than first drafted, not just analogous to it:
rescue mode boots no plan at all, so there is no house anywhere that
could be running a tunnel daemon in this mode even in principle. The
phone-reachable fallback capability named in §1 is therefore real only
in a narrower sense than "this tool, reached over the tunnel" — it
means the *ordinary, plan-booted* control app (`docs/options/27`) can
itself surface evidence and a rollback action through its own tunnel
when it's up, sharing this tool's underlying logic (§2) without this
tool's own rescue-mode invocation ever touching a network stack.
**Recommendation: serial/console only for `nw-rescue`'s own invocation
of this tool; the control app is free to call the same underlying
logic over its own tunnel as a separate, later capability, and nothing
here should be built to expect a network interface to exist.**

## 4. Commitment check

**Read-only browsing and operator-triggered actions only, nothing
automatic** — matching the operator's own framing and consistent with
`docs/options/27`'s identical commitment for the control app ("this app
never decides anything, only proposes/executes operator-issued
actions"). Concretely: evidence browsing and reading the last bake's
declarations (§1) are pure reads with no side effect; rollback is a single, explicit,
operator-issued write to `<slots>/current` (§1), never triggered by a
detected condition, a timeout, or a retry count — this project's
Liveness section already refuses exactly that class of automatic
decision-from-a-guessed-constant, and a rescue tool automatically
deciding to roll back on some detected signal would be freeze detection
wearing a different hat. Nothing here proposes it, and this paragraph
states the refusal explicitly so a future reader does not propose it
either without re-deriving why it was refused.

## Definition of done

This is a docs-only note. No code changes. The foundation question from
the first draft is resolved (`nw-rescue`, per the operator's own
correction). What remains open, per "A real tension" above, is narrower
and TCB-adjacent rather than a foundation question: `.claude/rules/
runtime.md`'s rescue section needs an explicit, reviewed amendment
before `rescue.c` may gain the one `execv()` this design recommends,
and that amendment (plus the code implementing it) should get
`tcb-review` alongside the usual `claims` pass, since it changes a
currently-enforced TCB rule rather than merely documenting one. Not
gated on Phase 4's `nwctl` — §2's correction establishes this tool does
not depend on it. `docs/QUEUE.md`'s new-notes entry is updated in the
same commit that lands this note.
