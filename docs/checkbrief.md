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

## Self-match

The tool matches its own patterns if its source is inside the tree. Pass
`--exclude` with its path. This is not hypothetical: the sibling tool
`prereport.py` hit it in the same session.
