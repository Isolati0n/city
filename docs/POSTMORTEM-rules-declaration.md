# Post-mortem: five attempts to write down who owns which file

**Status: history, not instructions.** Nothing below is a task. The work it
describes was on `origin/rules-declaration`, which was never merged; the
decision that replaced it is in `CLAUDE.md`'s "Who owns which file" and the
mechanism is `tools/rules-hook.sh --check`.

**THE BRANCH IS STILL ON `origin` AND THIS ENVIRONMENT CANNOT DELETE IT.**
The local ref is gone. The remote one is not, and the reason is worth
writing down rather than retrying: `git push origin :refs/heads/rules-declaration`
answers **HTTP 403 Forbidden** from the git proxy this session pushes
through, four attempts, while an ordinary push to the same remote succeeds
and the GitHub tools available here expose branch creation and listing but
no deletion. Deleting it is one action for whoever has a shell without this
proxy, or the button on the branch page.

*This paragraph said the branch "was deleted in the push that landed this
document" — written ahead of the action, landed, and false the moment it
landed, because the push it named was refused. It is the characteristic
failure inside the post-mortem about it, and the tell was that the failing
command exited 0. It was run as `git push … 2>&1 | tail -3`, so `$?` was
`tail`'s and not git's, and `tail -3` kept `send-pack: unexpected
disconnect`, `fatal: the remote end hung up unexpectedly` and `Everything
up-to-date` while dropping the line above them: `error: RPC failed; HTTP
403`. Unpiped, git prints that line and returns 1. One pipe discarded the
evidence and supplied the status. `git ls-remote --heads origin` is what
showed it, and is what anyone should run before believing a deletion
happened.*

**`08f6eef`, `8bab9a5` and `222e2e4` resolve for a reader who clones
today, and stop resolving when somebody deletes the branch** — they are
reachable from `origin/rules-declaration` and from nothing else. `bd49568`
is on `main` and is unaffected. `e62c6a6`, further down under *The five
attempts*, already does not resolve for anyone but its author: it was
dropped with `git reset --hard` and never pushed, so a fresh clone answers
`unknown revision or path not in the working tree` and always would have.
Treat the branch's three as names for what happened rather than as things
a reader can fetch.

*The sentence here said `git show` still answers for `e62c6a6` because an
unreachable object is not an unresolvable one. It answers in ONE object
store, the one the commit was made in, which is not a fact about
reachability at all. `CLAUDE.md`'s "the evidence is real and it is about
something else" carries the shape.*

They were verified while the ref was live: `claims` re-checked the parent
chain, the contents of each commit, and that `e62c6a6` was reachable from
no ref. The findings are kept because they are the only record of how five
attempts at one small thing went wrong, and because the way they were found
is worth more than what they found.

## The branch

Based on `bd49568`, pushed to `origin/rules-declaration`. Still there —
see the status note above.

| commit | held |
|---|---|
| `08f6eef` | The pinned declaration: one `<!-- nw-init:owns ... -->` line of repo-relative paths per rules file, the `Scope:` prose stripped of filenames, a gate in `install-agents.sh --check` comparing the two, and `test_rules_hook_delivers_what_the_declaration_says`. |
| `8bab9a5` | The first review round's fixes: the hook's root taken from `$1` passed by `.claude/settings.json`, the collapse case looped over every rules file, a `file_path` precedence probe, and the explanatory block consolidated out of three rules files. |
| `222e2e4` | The handoff this document is built from. |

## The five attempts

1. A second map written into `CLAUDE.md` on 2026-09-11. Contradicted the
   rules files' scopes.
2. Its replacement. Same.
3. `tools/ownership.sh`, written to derive one list from the other. It
   substring-matched filenames out of the `Scope:` prose, so rewrapping that
   line to 72 columns un-owned the whole boot chain — exit 0, no warning
   (`1ac0235`).
4. `e62c6a6`: a machine-readable declaration per rules file with a gate
   between it and the prose. Reproduced the bare substring match (`awn.c`
   and `ids.c` both passed) and widened the parse failure so one undecodable
   byte silently un-owned all three territories. Dropped with
   `git reset --hard`, never pushed.
5. The branch above. Correct in its map, its gate and its test cases, and
   wrong in the instruments around them — see the findings.

## The decision that replaced it

**One list: `MAP` in `tools/rules-hook.sh`. No second machine-readable copy
and no comparator.**

Comparing prose to a map means parsing prose, which is attempt 3. And a
second list earns a comparator only when the two lists have *different edit
paths* — otherwise the comparator is checking that one edit was made twice.
These do not: `a65286e` rewrapped `MAP`'s whitespace and left its membership
unchanged, and the commits that changed its membership mostly changed the
rules files too. Mostly, not always, and the exceptions matter: two
commits in the series changed membership without touching a rules file, and
the larger of them, `0541867`, added nine names.

*It said six, which was the number that commit added to the `plan` set
alone. The sentence was written at `ad95c23`, where the true figures were
one commit and one name, and `0541867` invalidated it three commits later
in the same round — the counts-and-their-invalidators-are-far-apart shape,
inside the document about getting this wrong. `claims` re-derived it by
parsing `MAP` out of every revision of the hook.*
So the historical claim is weaker than it was stated as, and the argument
that survives is structural — a second list here would exist only to be
compared, so it would be maintained by the comparator rather than by anyone
needing it. Two lists would be one list written twice.

What replaces the comparison is an **absence check against reality**:
enumerate the tracked code files and assert every one is accounted for. Its
input is the filesystem rather than anything an author wrote down, which is
why it can catch the author's blind spot. A comparator between two things
the same person edits in the same commit cannot.

## The hold, and the criterion it was held under

`8bab9a5` was finished, green and unpushed. It was held because a mechanism
it introduced was unpinned: the suite printed `ok rules-hook (...)` while the
hook, invoked the way `.claude/settings.json` invokes it, delivered nothing.

The criterion was written down **before** the round-two findings were read,
so the call could not bend to them:

- **Apply and push** if every finding is a false or imprecise sentence, an
  ack reason, or a message string — things whose repair cannot change what
  the suite detects.
- **Hold** if any finding means a mechanism is unpinned or wrongly pinned.

The line is whether the fix touches an assertion. If it does it is
structural however few lines it is: `e62c6a6` was two bugs in a gate and
looked small.

That criterion is the transferable part. Deciding small-versus-structural
*after* reading the findings is where the motivated reasoning gets in.

## What the review rounds found

Ordered by cost. Every one was measured; the commands are the ones that
produced them.

1. **The production invocation shape was not pinned.** Every probe either
   ran with cwd inside the repo or passed a relative `file_path`. Nothing
   combined an absolute `file_path` with a foreign cwd, which is exactly
   what `settings.json` produces. Deleting `os.path.abspath(_base)` from the
   prefix-stripping pair left the suite green and the real invocation
   silent.

2. **Of the three paths the hook header called rooted, one was pinned.**
   `src` was; the `.reviews` stamp was not; the prefix strip was not. Under
   either stamp mutation the once-per-territory suppression stopped working
   entirely with no stamp written anywhere, silently, because
   `except Exception: pass` turns the misplacement into a no-op.

3. **The fake-repo block read one cause into five silences.** An empty rules
   copy, a missing rules copy, a non-`.md`-only copy, a missing hook copy
   and a stale hook copy all failed with `a .claude/rules one level above
   the repo took over`, and in four of them the decoy took over nothing.
   The block was *not* satisfied by a fixture that was never built — that
   part was sound — but its pairing discipline ran against `ROOT`, not
   against the fake repo.

4. **The precedence probe's `finally` was unpinned, and it ran first.**
   Deleting the restore left the suite green and `runtime.md` modified;
   every later case re-read that file into its own `keep` or copied it into
   the fake repo, so the pollution was baked into their restores. Nothing in
   `make test` checks tree cleanliness.

5. **The gate had no negative control in the suite.** Deleting the entire
   ownership block from `install-agents.sh` gave `make test` EXIT=0 and
   `install-agents: OK 6 briefs`. Its refusals were correct when exercised
   by hand; nothing exercised them.

6. **The hook could deliver an empty rules body, green.** `terr()` read only
   the announcement line, so not one byte of the text the hook exists to
   deliver was asserted. Making `src` always resolve to the alphabetically
   first rules file also stayed green nearly everywhere.

7. **A `$1` that existed but named the wrong repo silently won over the
   correct cwd.** Measured: the foreign repo's rules text delivered while
   the message named the real repo's file, the real repo's own TCB file got
   nothing, and a stamp was written into the foreign tree.

8. **Nothing stated or checked the `$1` contract against `settings.json`.**
   Two prose assertions about what that file passes, no tool reading it. If
   the argument were dropped the cwd fallback would usually answer, so the
   drift was invisible in the common case — the second-copy class the change
   existed to remove, reproduced on its own contract.

9. **A second list, in the change whose thesis was one list.**
   `install-agents.sh` iterated a hardcoded `RULES='plan runtime harness'`
   while the test globbed `.claude/rules/*.md`, so a fourth rules file would
   have been checked by the test and not the gate. Reported as HYPOTHESIS —
   reasoned from reading, not run.

10. **A behaviour regression nothing recorded.** `Edit`/`Write` on a copy of
    a TCB file *outside* the project root stopped delivering rules, while
    the Bash branch still did via `_hit`'s basename alternative. This
    project prescribes copying the tree to a scratch directory for controls,
    so it bit reviewers first. That basename alternative was itself
    deletable green.

11. **Smaller, each touching an assertion or a message.** The collapse
    case's victim-silent half pinned a conjunction of two independent reads
    and neither. `rel == t` weakened to `endswith` was green. `--list`'s
    greedy `sed` and the hook's non-greedy regex read opposite ends of a
    line carrying two declarations, and `--check` passed. An undecodable
    rules file was a broken declaration that `--check` accepted. The same
    token twice within one territory was refused with a message naming a
    cause that did not happen.

### Prose findings

- "the fourth attempt" was the fifth, in the hook header and restated in all
  three rules files.
- The pointer block arguing for one copy was byte-identical in all three
  rules files, with nothing comparing them.
- `plan.md` said the deleted sentence "used to repeat the six of them" — a
  count of the ownership list, in a brief.
- `runtime.md`'s new paragraph duplicated the "state flows down this chain"
  sentence still in the file below it.
- Two mis-descriptions of the gate's own pattern, in a comment rewritten
  that round because the previous one was false: only the extension was
  lowercase-only, and the greedy `sed` read the last `Scope:` match, not the
  first.
- The rules files stated the `Scope:` guard unconditionally where the
  guard's own comment recorded its misses.
- The test docstring said the test wrote to one file in three cases; it
  wrote five cases over three tracked files, and the commit that wrote the
  sentence is the one that made it false.
- `.prereport-ack` carried a dead entry whose keyed text no longer existed.

## What was good

Confirmed independently by both reviewers, by running:

- The map, the gate's refusals and the `--list` rendering were correct when
  exercised. The TCB completeness parse found 8 files in `CLAUDE.md`'s table
  and all were declared.
- The `__file__` diagnosis was exactly right and the fix worked from a
  foreign cwd.
- The collapse loop over every rules file genuinely closed round one's
  finding. Four bail-out variants including reverse iteration order were all
  red, and the argument is general: any fixed order makes some file first,
  and the case corrupts each in turn.
- The fake-repo block tested the hook under review, and a stale copy was
  caught.
- Of its four root assertions, three were load-bearing with a mutation that
  reddened each alone; the fourth was never red under ten mutations.
- Nothing a reader needed was lost from `runtime.md`'s deleted enumeration.

## The pattern, which is why this document exists

**Every defect, across three rounds and five attempts, was an instrument
that could not distinguish its subject from its own absence.**

- A hook root that resolved to a directory which did not exist, so the
  fallback did all the work and the mechanism read as working.
- A collapse case satisfied by the defect, because its victim sorted last
  and a bail-out kept the earlier territories.
- A control that passed because its mutation did not apply — the
  replacement string's indentation did not match the source.
- A pin that asserted silence where the failure mode is also silence.
- A fake-repo block whose five malformed fixtures all reported that the
  decoy took over.
- A `.gitignore` pattern claiming in its own comment to catch "the products
  and no source", which caught `bakery/nw-cc.py` — found only because a
  control fixture built with `git init && git add -A` enumerated one fewer
  code file than the tree held.

**None was found by reading.** Each was found by running something and then
asking why the answer was not what the mutation implied. That is the
argument against "be more careful": careful is the state every one of them
was written in.

The question that finds this class, asked of any check: **what else produces
exactly this evidence?** If a mechanism working and a mechanism never
running look the same from outside, the check is not finished — whatever it
prints.
