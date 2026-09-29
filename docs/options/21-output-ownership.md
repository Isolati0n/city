# 21 — output ownership

Status: **design note. No code in this round.** Answers
`docs/OPERATOR-BRIEF.md` Section 2 item 1g's one-line scope, "nw-sup owns
each house's output," from the current tree, with file:line citations.
Where that scope touches `pid1.c` or `dawn.c`, this note states the fact
and, where a change is genuinely on the table, hands it to Grok as a
brief rather than proposing an edit — those files are Grok's per
CLAUDE.md's "Who owns which file" (`CLAUDE.md:932-933`: "Grok owns
`pid1.c`, `dawn.c` and the restart loop in `nwsup.c`"). This note edits
neither.

Every claim below was checked against the tree at the time this note was
written; grep the cited lines rather than trusting the prose if this note
and the code ever disagree.

## What "owns" cannot mean, and why that's settled already

The literal reading — nw-sup captures the bytes — is already answered,
in the negative, by `docs/options/12-crash-evidence.md`, and this note
does not reopen it. `nw-sup`'s own fd 1 and fd 2 are the log pipe's
**write** end, handed down by `nw-spawn`'s `pack_kit()` via
`dup2(logn, 1)` / `dup2(logn, 2)` (`nwspawn.c:224-225`) before
`nw-sup`'s own `execl()` (`nwspawn.c:409`) — a write-end fd cannot be
read from, by any process, regardless of how many times it is
duplicated. `grep -n "pipe(\|log_r\|log_w" nwsup.c` returns nothing:
confirmed, `nw-sup` has zero read access to a house's own output today,
structurally, not by omission.

Giving `nw-sup` a second, private copy of the read end was considered
and declined in `docs/options/12` on cost grounds: it would restructure
who owns the log pipe, which is "a much bigger, riskier change to the
already-hardened `wait_house()` poll loop" than capturing where the
bytes already flow (`docs/options/12`, the "assumption the brief made"
section). Nothing found while writing this note reopens that
cost/benefit call, so it stands: **capture stays where it is, in PID 1's
per-unit logger (`spawn_logger`, `pid1.c:267-451`).**

The reason capture cannot move to `nw-sup` is not merely historical —
it is an ordering constraint that is still true today. Every logger is
forked in PID 1's boot-time setup loop (`pid1.c:770-776`), which
completes in full before the spawner (and therefore `nw-spawn` and every
`nw-sup`) is forked at all (`pid1.c:789`, `execv` into `nw-spawn` at
`pid1.c:826`). `blob.h:101-103`'s own comment on `NW_EVIDENCE_DIR` says
so directly: that directory is "created by `dawn.c`... because PID 1's
logger needs it before `nw-sup` exists to create it the way `NW_CTL_DIR`
does." A supervisor cannot own the capture of output from a pipe that
exists before the supervisor itself does.

So this note reads "nw-sup owns each house's output" as a claim about
**interpretation and disposition**, not capture: nw-sup is the one
process that decides what a house's output *means* — where a run
begins, what gets sealed as evidence and under what name, and what (if
anything) is exposed about it through any future operator-facing
interface. Capture mechanics — the ring, the pipe, the tail file's
existence and its `O_TRUNC` scoping — stay exactly where the boot order
requires them to stay, in PID 1's logger.

## 1. What nw-sup already owns, unchanged by this note

- **The only read of tail content anywhere in the tree.** `write_evidence()`
  (`nwsup.c:994-1100`) opens and reads `<NW_EVIDENCE_DIR>/<name>.tail`
  (`nwsup.c:1067-1069`) — the one and only place any process other than
  the logger itself looks at what a house wrote. Confirmed no other
  reader exists: the tail file's only writer is `spawn_logger`
  (`pid1.c:382-451`) and its only reader is `write_evidence()`.
- **The decision of when to seal, and the seal's contents.** Triggered
  at the two `restart`/`spent` sites (`nwsup.c:1489`, `nwsup.c:1500`),
  `write_evidence()` composes `name`, `reason`, `value`, `death`,
  `budget` and the tail bytes into a single record, written
  content-addressed by its own sha256 via temp-name-then-`rename()`
  (`nwsup.c:1090-1099`). Nothing about this changes here.
- **The bounded, clock-free wait for the logger to have flushed**
  before that read — `FIONREAD` on nw-sup's own copy of fd 2 plus
  `sched_yield()`, capped at `NW_EVIDENCE_DRAIN_SPINS` iterations
  (`nwsup.c:1060-1065`). This is metadata (a byte count), never content
  — `docs/options/12`'s COMMITMENT-5 section: "it cannot see what is or
  isn't in the pipe, only how much." Unchanged.
- **The run-boundary marker**, designed already in
  `docs/options/22-lock-unlock.md` §2 (lines 92-140 there) and not
  reopened by this note: nw-sup writes a distinctive line into the same
  log stream immediately before every fork (via its own `say()`, which
  already writes into the pipe it holds as fd 1/2), and
  `write_evidence()` trims the tail it captures to start after the last
  such marker, found by a backward string search over bytes it already
  has in memory. `docs/options/22` states explicitly that this needs
  "no new channel and no cross-process coordination" — it is entirely
  nw-sup's own doing, against a pipe it already writes into. **This is
  the concrete answer to what "own" means for run-scoped output: nw-sup
  decides where a run's output starts, using only mechanism it already
  has.**

## 2. What nw-sup does not, and structurally cannot, own

- **The tail file's location, creation and lifetime scoping.**
  `NW_EVIDENCE_DIR` is created by `dawn.c`, not `nw-sup` (`blob.h:101-103`),
  for the ordering reason above. The file itself is opened, truncated
  and written exclusively by `spawn_logger` (`pid1.c:382-451`).
- **The ring buffer's size and scope.** `NW_EVIDENCE_TAIL_MAX` (4096,
  `blob.h:107`) bounds "the ring buffer AND the tail file AND the
  package's tail region — one constant, not three" (`blob.h:104-105`
  comment). It is process-lifetime (one logger, spanning every restart
  of that unit), not run-lifetime — stated directly in
  `docs/options/22-lock-unlock.md:94-96`. Nothing in this note proposes
  changing that scope; the marker line in §1 above is what makes a
  process-lifetime ring safe to read as if it were run-scoped, without
  changing the ring itself.
- **Whether stdout and stderr are distinguishable.** `pack_kit()` dup2's
  the *same* log pipe write end onto both fd 1 and fd 2
  (`nwspawn.c:224-225`), so a house's stdout and stderr are already
  merged into one stream before either PID 1's logger or `nw-sup` ever
  sees a byte of it. This is existing, unrelated-to-this-note behavior;
  nw-sup has no more ability to separate them after the fact than the
  logger does, and this note does not propose to change it.

## 3. Whether any pid1.c change is needed — none is, and this is the finding to hand to Grok

Given §1, the run-boundary problem that motivated this note's framing
("nw-sup owns each house's output") is already fully solved inside
`nw-sup`'s own code, per `docs/options/22` §2, with no new channel and
no `pid1.c` change. Checked directly: nothing else surfaced while
writing this note that requires PID 1's logger to behave differently
than it does today. So **the brief for Grok is a negative result,
stated plainly rather than left implicit**: this note does not ask for
a `pid1.c` or `dawn.c` change.

One adjacent question was considered and is recorded here, unresolved,
in case Grok or the operator wants to pick it up — it is a question,
not a request, and no code should follow from it without a separate
decision:

> Today the tail file is opened once, `O_TRUNC`, at logger startup
> (`pid1.c:369-383`), scoped to "since this logger opened it" — i.e.
> since boot, not since the current run. `docs/options/22`'s marker-line
> design deliberately does *not* trim the on-disk tail file per run; it
> only trims what `write_evidence()` reads out of it into memory
> (`docs/options/22-lock-unlock.md:135-136`, "it does not shrink or
> reset the on-disk tail file itself per run"). If a future consumer ever
> needs the on-disk file itself scoped to the current run — for
> example, an operator tool that tails `<name>.tail` directly without
> going through nw-sup — that would require PID 1's logger to re-open or
> truncate the file on some signal meaning "a new run started," which
> reintroduces exactly the cross-process coordination `docs/options/22`
> avoided by keeping the trim in-memory and in-nw-sup. **Recommended
> default: don't build this.** No consumer identified anywhere in this
> tree reads the on-disk `.tail` file directly except `write_evidence()`
> itself (§1 above), so there is no known reader this would serve.

## 4. Cross-references checked

- **`docs/options/11-start-stop-channel.md`**: grepped for
  output/logger/tail/stdout/stderr/evidence — no matches. It does not
  touch output ownership; its only connection to this area is the
  separately-decided cross-generation misdirection race, which is
  unrelated.
- **`docs/options/22-lock-unlock.md`**: directly relevant, covered in
  §1 and §3 above. Its `STATUS` verb (`docs/options/22-lock-unlock.md`
  §6 "The idle state", lines 292-298, the `state=<idle|running>
  deaths=<n> last_exit=<none|code|crash>` line) reports state and exit
  disposition, never output content or a last line — confirmed by
  reading its exact format string. This note does not propose adding
  output content to `STATUS`, or to `nwctl why` (the amendment's item B,
  whose own worked examples — "spent after 3 deaths, last exit signal
  11, record `<hash>`" — already name the evidence record by hash
  rather than by quoting its content, which this note treats as the
  right scope to keep).
- **No existing note anywhere in `docs/options/*.md` uses "owns" beside
  "output"/"logger"/"capture"** before this one — checked by direct
  grep. This is the first place the concept is named as such; §1 and §2
  above are that naming, applied to the mechanism that already exists
  rather than to a new one.

## Definition of done

This is a docs-only note. Nothing here changes `pid1.c`, `dawn.c` or
`nwsup.c`. `docs/QUEUE.md`'s item 1g entry for this note is updated in
the same commit that lands it.
