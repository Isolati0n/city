# checkbrief

Checks a brief's numbered invariants against a source tree.

## Run

    python3 test_checkbrief.py          # 14 checks, expect all pass, exit 0
    python3 checkbrief.py --brief CLAUDE.md --tree . --exclude checkbrief.py

Exit: 0 all verified or nothing checkable, 1 something contradicted,
2 usage error, 3 no `Invariants` section found.

## What it does

Reads numbered items under a heading whose **first word** is `Invariants`.
Refusals and waiting-on-a-prerequisite sections are excluded by section,
not by judgement, so they are never candidates.

For each invariant it looks for inline annotations of the form
`<<kind:argument>>` and verifies them. Reports per invariant:
verified, contradicted, or uncheckable.

## Annotation forms

    <<file:PATH>>                     path exists
    <<filecontains:PATH:TOKEN>>       PATH contains TOKEN
    <<absent-in:PATH:TOKEN>>          PATH does not contain TOKEN
    <<grep-absent:TOKEN>>             TOKEN absent from the whole tree
    <<grep-present:TOKEN>>            TOKEN present somewhere in the tree
    <<symbol:PATH:NAME>>              PATH *defines* NAME (not merely uses it)
    <<count:PATH:TOKEN:N>>            PATH contains TOKEN exactly N times

## Two things that matter more than the forms

**The token is a symbol, not a word.** A symbol is a name the code uses —
`NW_MAX_UNITS`, `fds_ge3`. A word is English prose that appears near the
concept — "budget", "restart". A symbol appears in code and rarely in
prose; a word appears in both. An annotation on a word is contradicted by
a comment and is a check of the comment, not of the code.

**Every form pins presence or absence of text. None pins agreement.**
`verified` means the thing the annotation names is where it says it is.
It never means the prose claim is true. Where a claim is that several
places agree, the annotation catches a site disappearing and not a site
disagreeing — and the report should say so, or `8 - 1 uncheckable` reads
as `8 - 1 verified`.

## This repository falsifies absence annotations on purpose

`absent-in` and `grep-absent` are the forms most likely to go wrong here,
and not by accident: **when something is removed, this project's rule is
to re-file the reasoning rather than delete it** (`CLAUDE.md`, *How briefs
are written*). So a removed thing leaves a comment naming it, and a
comment naming it contradicts an absence annotation while the code claim
is perfectly true.

Measured 2026-09-12, and note it is not where it was predicted to be:

    budget    pid1.c   0 hits   -- absent-in verifies
    restart   pid1.c   0 hits   -- absent-in verifies
    respawn   pid1.c   0 hits   -- absent-in verifies
    mount(    pid1.c   0 hits   -- verifies, and is STRONGER than the
                                   brief's own "returns only comments"
    window_s  nwsup.c  1 hit    -- the comment recording its REMOVAL, so
                                   absent-in is CONTRADICTED while
                                   invariant 4's claim is true

The warning had been aimed at `budget` in `pid1.c` on the grounds that it
is an English word. It is a symbol there and it is absent. The one that
bites is `window_s` — an unambiguous symbol, absent from the code,
present in the prose that records its absence.

So the rule is narrower than "use a symbol, not a word": **for an absence
claim, pick a token the removal note would not contain, or scope it to a
syntax prose cannot produce.** `mount(` rather than `mount` in `pid1.c` —
measured, the bare form has 3 hits and the scoped form 0, and invariant 1
itself has to say "returns only comments" because of exactly that.

*The second example this paragraph first offered — `__NR_unshare` rather
than `unshare` in `lids.c` — does not demonstrate the point: both are 0
there, so both verify and neither shows the trap. Measured after writing
it. An illustration that illustrates nothing is the same defect one level
down, in the file explaining how not to make it.*

## The ceiling, named

`verified` is about text. Invariant 8's "the seal must be *verified*, not
merely read" is the cleanest thing this tool cannot express: there is no
token whose presence or absence distinguishes a CRC that is compared from
one that is computed and dropped. That claim is pinned by
`test_difftest` and by `proofs/`, and it should stay pinned there. An
annotation that appeared to cover it would be worse than none.

## Self-match

The tool matches its own patterns if its source is inside the tree. Pass
`--exclude` with its path. This is not hypothetical: the sibling tool
`prereport.py` hit it in the same session.
