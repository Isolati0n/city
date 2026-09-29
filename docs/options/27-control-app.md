# 27 — control app (phone control panel + plan editor)

Status: **design note. No code in this round.** Gate: Phase 4's `nwctl`
and the grants table (`docs/options/20`, landed) — neither `nwctl` nor
its wire protocol exists yet. Two independent sources already
established this by full-repo search rather than this note re-searching
it: `docs/options/22-lock-unlock.md` — "Neither `nwctl` nor any
'profile' struct, plan field, or RUN/STOP verb pair distinct from the
already-built START/STOP channel exists anywhere in this tree — every
mention is in prose... `driftwm` and the compositor exist nowhere as
code — no binary, no stub, no source file" — and `docs/QUEUE.md`'s own
item 8, on the separate question of the launch path: "the launch path
(`nwctl`/driftwm/compositor) has no subject yet — none of them are
built." Confirmed again directly rather than trusted from either:
`grep -rl "nwctl" --include='*.c' --include='*.py' --include='*.h'
--include=Makefile .` returns nothing. This note designs against that
gap explicitly rather than assuming it closed.

Structural call already made by the operator and not reopened here:
**one web app, two modes (dashboard, plan editor), not two tools.**
Flagged only if this note found a reason to disagree — it did not.
A single app sharing one auth boundary, one transport, and one
`STATUS`/evidence read path for both modes is strictly less to secure
and reason about than two separately-exposed surfaces, and the two
modes' actions overlap enough (both end in "propose an action, show
what it does, let the operator confirm") that splitting them would
duplicate the confirm-and-execute machinery for no isolation benefit —
the phone is already trusted or it isn't, per §3's authentication
question, and that boundary doesn't get stronger by drawing it around
two apps instead of one.

## 1. Where it runs, and what it's granted

**Runs on the machine itself** (the same host `nw-sup`/`nwctl` are on),
not as a separate remote service — the alternative (a cloud-hosted
relay holding the machine's control credentials) multiplies the attack
surface named in §2 for no benefit this project needs, and every other
operator-facing tool in this tree (`nwctl`, the console house) already
assumes on-machine execution with the network layer added on top by
whichever transport question that tool answers, not baked into the
tool's own privilege model.

**What it is granted**: exactly what `nwctl` itself would be granted,
because this app is a client of `nwctl`'s wire protocol (or of
`nw-sup`'s control socket directly, if `nwctl` is not yet a
library-shaped thing this app can call into by the time this is built —
an implementation detail Phase 4 will settle, not this note). **This app
decides nothing beyond what a human operator typing `nwctl` commands
directly could already do** — stated here because it is the same
commitment §6 asks for explicitly, and worth saying early since it
bounds every other answer in this note: nothing about running this over
a phone should grant a NEW capability that a local operator at a
terminal did not already have.

**Whether it is a house in the sealed plan, and which lids, is settled
by §2's tunnel decision, not left open here.** The tunnel daemon (§2)
and this app's own listener have to share one network namespace — see
§2's own reasoning — so the natural shape is one house declaring both
processes, `lids=newns,landlock,newnet`, the same combination
`docs/options/10-console-house.md` already uses and for the same root
cause: both the tunnel daemon (route/interface configuration via
`AF_NETLINK` sockets and `ioctl(TUNSETIFF)` on `/dev/net/tun`) and this
app's own listener (an ordinary `socket()`/`bind()`/`accept()` server)
need syscalls `lids.c`'s `strict_allow[]` does not carry — `__NR_socket`
and `__NR_ioctl` are both absent from it, deliberately (`.claude/rules/
runtime.md`: "`__NR_socket` is absent from the `lids.c` allow-list, so a
`lids=seccomp` house is killed for trying"). So this house cannot
declare `seccomp` at all, exactly like the console house cannot — this
is not a gap this note is opening, it is the same, already-precedented
gap the console house already lives with, extended to a second house
for the same structural reason.

**One gap worth naming rather than assuming closed**: `docs/options/11`'s
own "Authorization" section states the control socket has none today —
"no `SO_PEERCRED` inspection, no token, no per-caller [anything]." So
whatever this app talks to (raw socket or a future `nwctl` wrapping it)
inherits complete trust in whoever can reach that socket. That is fine
for a process running as the same operator on the same machine; it
means **this app's own authentication (§3) is the only real boundary
between "anyone with the URL" and "start/stop any house, edit the
plan"** — the underlying channel provides none of its own to fall back
on. Restated because it changes how seriously §3 has to be taken: there
is no second layer underneath it.

## 2. Transport and exposure — the hardest question

**This is the first thing in this project reachable from outside the
machine at all.** Every other network-touching design in this tree
(edges between houses, the control socket, `nwctl`) is explicitly
machine-local. Getting this wrong is categorically worse than getting
anything else in this project wrong, because everything else fails
closed onto "this machine only" and this is the first design that fails
open onto "the internet" if built carelessly.

**Decided by the operator, not merely recommended: a tunnel (e.g.
Tailscale) is the transport, full stop.** Not a recommendation with an
alternative left standing beside it — there is no local-network-only
mode to fall back to as a primary path, and no port opened to the raw
internet under any configuration. This note designs the app around the
tunnel as its only transport rather than presenting it as one option
among several.

The reasoning below is kept because it is still the argument for the
decision, not because the decision is still open:

- "Local network only" is a claim about topology the app cannot verify
  or enforce — it depends on the operator's home network having no
  other untrusted device, no compromised IoT gadget, no guest network
  bridged to the main one, none of which this app can detect. A tunnel
  (Tailscale, WireGuard, or equivalent) authenticates the *device*
  before any packet reaches this app at all, at a layer below this
  app's own control — the network interface the app listens on simply
  does not exist to anything outside the tunnel's own device list.
  "Local network" trusts every device on that L2 segment by default;
  a tunnel trusts nothing by default and grants explicitly.
- It solves the phone's own reachability problem for free: a phone on
  cellular data, away from home, still reaches the tunnel's virtual
  interface exactly as if it were on the LAN — "local network only"
  would make the whole feature useless the moment the phone leaves the
  house, which defeats the stated purpose (a *phone* control panel).
- §3 answers what happens once a device IS on the tunnel — and, per
  that section's own decision, the answer is that tunnel membership
  is itself treated as sufficient, not that a second check follows.
  A device the operator authorized once and forgot about is exactly
  §3's own named residual risk, not a gap this bullet's reasoning
  papers over.

**Where the tunnel daemon runs: alongside the control app, in the same
house — not as a separate plan unit.** This is a real fork with a
concrete reason to pick one side, not a detail deferred to
implementation:

- The tunnel software (`tailscaled` or equivalent) creates a virtual
  network interface and this app's own listener has to bind on, or at
  least be reachable through, that same interface. There is no
  mechanism anywhere in this plan format for two separate houses to
  share one network namespace — every unit gets its own private
  namespace, gated on the `newnet` lid bit, with no sharing primitive —
  so splitting the tunnel daemon into its own house would leave this
  app's listener in a *different* network namespace than the interface
  the tunnel creates, unreachable through it. Inventing a cross-house
  namespace-sharing mechanism to work around that would be new TCB
  surface with no other consumer, which is exactly the class of thing
  the mechanism rule (`CLAUDE.md`, "Adding a field to the plan") asks to
  be justified by a real, load-bearing need before it exists — and one
  house sharing its own single namespace with its own two processes
  needs no new mechanism at all.
- The syscall argument in §1 already puts them in the same lid class
  (`newns,landlock,newnet`, no seccomp), so there is no isolation this
  note would be giving up by co-locating them that a separate house
  would have bought — neither process can be sandboxed tighter than the
  other given what both need to do.

**No port open to the raw internet, under any configuration this note
proposes.** This is stated as a hard constraint rather than a
recommendation with an escape hatch, because the entire risk profile of
this feature (§1's "start/stop any house, edit the plan") is
unacceptable exposed to unauthenticated internet scanning at any
timeout, rate limit, or clever-auth-scheme's mercy. If a future
operator wants that anyway, it is a deliberate, separately-argued
decision to override this note, not a configuration flag this note
ships.

## 3. Authentication

**Decided, not left as the per-boot-token design this note's first
draft recommended: the tunnel's own identity is the sole
authentication.** No separate app-level token, password, or login
screen — reaching this app at all already means the tunnel (§2)
authenticated the calling device, and that authentication is what this
note relies on rather than duplicating with a second credential.

**This is a deliberate, named decision, not an omission, and the
precedent is a real one, checked against its own primary source rather
than assumed.** Home Assistant's authentication docs are explicit that
login is required by default: "Home Assistant comes with a built-in
authentication provider enabled by default," using a username and
password, and the docs' own warning is that omitting the default
provider "means anyone on your trusted network can log in by selecting
a username" with no password at all
(home-assistant.io/docs/authentication/providers/). That is
`trusted_networks`, a **named, opt-in, second auth provider** —
network membership treated as sufficient identity, exactly this note's
own move — added to Home Assistant's core in
home-assistant/core#15812, "Add trusted networks auth provider."
**Checked, not assumed: that PR's own commits are dated July–August
2018, and it merged in 2018, not 2017.** The date given for this
precedent needs correcting rather than repeating uncorrected — the
mechanism itself (a named, sanctioned exception where the operator
explicitly opts a specific network into being sufficient identity, with
the general default left requiring a password) is exactly what is
cited here, and the shape holds regardless of which year it shipped.

**The residual risk, stated plainly rather than left implicit**: this
trusts every device already on the tunnel, and offers no protection at
all if one of them is compromised — a stolen or malware-infected phone
that was legitimately added to the tunnel months ago for something
unrelated can start/stop any house and edit the plan, with nothing
here to stop it. That is not a smaller risk than a token would close;
it is the same risk `docs/options/11-start-stop-channel.md`'s own
Authorization section already names for the control socket underneath
this one layer down ("no `SO_PEERCRED` inspection, no token, no
per-caller allow-list") — this note accepts the equivalent risk one layer up,
explicitly, rather than adding a token that would only re-create the
same trust boundary (whoever holds the token) under a different name,
while costing the operator a second secret to manage and losing the
"reachable at all" signal the tunnel already gives for free. Revoking a
compromised device is a tunnel-admin action (removing it from the
tailnet or equivalent), not an app-level one — worth stating since it
means this app has no *revoke* feature of its own to design, because
the tunnel already is that feature.

**Verified against Tailscale's own current documentation, not taken on
faith, for the one place this note considered going further**: Tailscale
Serve adds three identity headers to proxied requests —
`Tailscale-User-Login` (e.g. `alice@example.com`), `Tailscale-User-Name`
and `Tailscale-User-Profile-Pic` — and Tailscale's own docs state the
exact caveat that matters here: these headers are populated only for
real tailnet users, **not for tagged devices**, and "it's best practice
to only have the service listen on localhost" when trusting them,
"otherwise, any user that can call your service directly... could
trivially provide their own values for these HTTP headers" — Serve
strips any client-supplied copies of these headers before adding its
own, specifically to prevent that spoof
(tailscale.com/docs/features/tailscale-serve). This is directly
usable here as an *optional, additional* app-layer check — restricting
this app to one named tailnet login, beyond "reachable on the tunnel at
all" — but only if the control app is reached through `tailscale
serve` specifically (a local reverse proxy tailscaled itself runs),
not by binding directly to the tailscale interface's own address,
which gets no header injection at all. Using Serve this way also means
the app's own listener binds `localhost` only, matching Tailscale's own
stated best practice, and composes cleanly with this note's existing
decision (§1) that the tunnel daemon and the app share one house and
one network namespace. Recommended as a real hardening step precisely
because it was checked against the primary source rather than
remembered — the header names, the tagged-device exclusion and the
localhost caveat are the three things worth getting exactly right if
this is built, not approximated from a general sense of how Tailscale
identity works.

## 4. The exact flow

**Edit → validate via `nw-cc`/`nw-check` → show diff (reusing the
permission-diff tool named in Phase 4: "permission-diff tool (baker-
side; reports widenings separately)," itself not yet built) → confirm →
bake → new slot → reboot when ready. The editor never hand-edits the
sealed blob.**

Each step, checked against what already exists to build on:

1. **Edit**: the app presents the city file (the bakery's plain-text
   source input) as structured fields, not raw text — the grants table
   design (`docs/options/20` §2, "every current field, against all four
   parts") is exactly the schema this editor's form fields would be
   generated from, which is why this note names note 20 as a gate
   rather than reinventing a field list.
2. **Validate**: run the existing baker (`bakery/nw-cc.py`) and
   `nw-check` against the edited city file — both already exist and
   already refuse a malformed plan by named reason (`plan.md`'s "the
   baker refuses; it does not repair" rule). No new validation logic;
   this step is calling the two tools this project already trusts for
   exactly this question.
3. **Show diff**: the permission-diff tool is Phase 4 scope, not built
   yet — this note's dependency on it is real, not decorative: without
   it, "show diff" degrades to a raw before/after text diff of the city
   file, which does not answer the question that actually matters
   ("what capabilities does this change grant or remove"). **This step
   is where the gate is load-bearing**, not merely nominal.
4. **Confirm**: an explicit operator action in the app, distinct from
   "edit" — matching §6's "propose, never decide" commitment.
5. **Bake**: run the baker for real, producing a new blob.
6. **New slot**: stage it as a *candidate*, using
   `tools/stage-candidate.py`'s existing mechanism (already built,
   already the tool `docs/options/29-rescue-interface.md` also builds
   on for its own rollback question) — never write the live slot in
   place, matching invariant 7.
7. **Reboot when ready**: an operator-issued action, not automatic —
   the app can propose "reboot now" or "reboot later," but nothing here
   triggers a reboot from a timer or a detected condition, for the same
   reason `docs/options/29`'s rollback is operator-triggered only.

## 5. Read-only fallback

**Answered by `docs/options/29-rescue-interface.md`, cross-referenced
rather than re-designed here**: if this app or the machine it needs
(the compositor brick, since `nwctl` ships there per Phase 4) can't run
— the compositor is down, the app has a bug, the machine won't boot the
main plan at all — the rescue interface's own tool is the fallback,
reached over serial/console by design (`docs/options/29` §3's own
reasoning: rescue implies possibly no normal network access either, so
it deliberately does not depend on this app's tunnel). This app being
"read-only capable" as a fallback mode of *itself* was considered and
rejected: a read-only mode of a broken app is still a mode of the
broken app, and does nothing for the case where the app itself, not
just its write path, is what's unreachable. The rescue interface is a
genuinely separate program for exactly that reason.

## 6. Commitment check

**This app never decides anything, only proposes/executes
operator-issued actions — same as `nwctl`.** Every write path named in
§4 (edit, bake, stage-candidate, reboot) is a step the app performs
*because the operator explicitly asked for that specific step*, not
because the app inferred it should happen. No step in §4 is triggered
by a timer, a detected failure, or a retry — matching this project's
standing refusal of automatic decisions from a guessed signal
(the Liveness section's refusal, restated for the same reason
`docs/options/29`'s commitment check restates it). Where this note's
design allows the app to *suggest* an action (e.g. "this house has
crashed three times, consider editing its budget"), the suggestion is
advisory text, never a pre-filled confirm button that only needs a tap
— the distinction is deliberate and is the whole of what "propose,
never decide" means in practice.

## Definition of done

This is a docs-only note. No code changes — both gates (`nwctl`, the
permission-diff tool) are unbuilt, so nothing here can be implemented
yet. `docs/QUEUE.md`'s new-notes entry is updated in the same commit
that lands this note, marked GATED.
