#!/bin/sh
# review-gate.sh — REPORT that a change no reviewer has seen is owed one.
#
# It refuses nothing. Nothing invokes it: .git/hooks holds only samples,
# core.hooksPath is unset, and neither the Makefile nor .claude/ calls it.
# Obeying it is a discipline. This line
# said "refuse to push" and a HISTORY section repeated the claim as
# mechanism -- the wrong reading was generated here, which is where an
# agent reads it from. `claims`. HISTORY.md 72.
#
# Both HIGH findings of 2026-09-10 -- the ".." traversal and the profile that
# killed compilers -- were found in code that was already committed and
# pushed, because the reviewers ran after the push rather than before it.
# Same tokens, same findings, different blast radius. This makes the ordering
# mechanical instead of remembered.
#
# A record is keyed by the *content* of the files it covers, so any further
# edit invalidates it: reviewing, then changing the code, then pushing is the
# same failure with an extra step.
#
#   sh tools/review-gate.sh --check          nonzero if a review is owed
#   sh tools/review-gate.sh --record <agent> record that <agent> reviewed
#   sh tools/review-gate.sh --status         what is owed and why
set -eu

DIR=.reviews
TCB='dawn.c pid1.c nwspawn.c nwcheck.c nwcheck_main.c nwsup.c lids.c blob.h rescue.c'
SUITE='tests/run.py unit_probe.c houses bakery/test_fold.py tools/test_checkbrief.py'
# CLAUDE.md's dispatch table has owed a `claims` review for a brief or an
# environment claim since it was written, and this gate did not watch a
# single prose file -- so "dispatch before you push" was mechanical for
# code and honour-system for the statements that tell an agent what the
# code does. `claims` demonstrated the gap by watching a CLAUDE.md change
# get pushed, gate reporting ok throughout, while its own review of that
# change was still running. Kind-1 statements rot silently; that is the
# whole reason the reviewer exists.
#
# HISTORY.md WAS LEFT OUT OF THAT WIDENING, and it is named in the same
# dispatch-table row that motivated it. Measured 2026-09-12: a diff whose
# prose was a whole new HISTORY section, with a `claims` review dispatched
# and still running, got `review-gate: ok`. The rule at its weakest in the
# change that introduces it, in the guard written against honour-system
# review -- found by reading the gate after it green-lit a push it should
# have held, which is the silence failure's own diagnostic question: not
# "does it pass" but "what made it fire, and what cannot".
#
# The suite list had the same shape of hole: it named tests/run.py and the
# fixture houses, so a NEW test file was watched by nothing. bakery/fold.py
# arrived with its own suite and `control` was owed for it under the
# dispatch table's "a test was added or changed" row.
#
# STILL UNWATCHED, deliberately rather than by oversight, and recorded so
# the next reader does not take silence for coverage: non-test code outside
# the TCB -- the baker, tools/, the stagers. No reviewer in the table owns
# them, so adding a component would mean inventing an owner. Read this list
# rather than assuming a path is covered.
PROSE='CLAUDE.md .claude/rules .claude/agents HISTORY.md'
TCB_REVIEWERS='tcb-review fd-auditor'
SUITE_REVIEWERS='control'
PROSE_REVIEWERS='claims'

id_of() {   # sha of the current content of a component's files
    for f in $1; do [ -e "$f" ] && find "$f" -type f -exec cat {} +; done \
        | sha256sum | cut -c1-16
}

base() {
    git rev-parse --verify --quiet '@{u}' 2>/dev/null && return
    git rev-parse --verify --quiet origin/main 2>/dev/null && return
    git rev-parse --verify --quiet HEAD
}

changed() {   # every path differing from the push base, committed or not
    b=$(base)
    { git diff --name-only "$b" 2>/dev/null || true
      git status --porcelain 2>/dev/null | cut -c4-; } | sort -u
}

touches() {   # $1 = space-separated prefixes; reads changed paths on stdin
    while read -r p; do
        for g in $1; do
            case "$p" in "$g"|"$g"/*) return 0 ;; esac
        done
    done
    return 1
}

owed=''
CH=$(changed)
if printf '%s\n' "$CH" | touches "$TCB"; then
    for a in $TCB_REVIEWERS; do
        [ -e "$DIR/tcb.$(id_of "$TCB").$a" ] || owed="$owed tcb:$a"
    done
fi
if printf '%s\n' "$CH" | touches "$SUITE"; then
    for a in $SUITE_REVIEWERS; do
        [ -e "$DIR/suite.$(id_of "$SUITE").$a" ] || owed="$owed suite:$a"
    done
fi
if printf '%s\n' "$CH" | touches "$PROSE"; then
    for a in $PROSE_REVIEWERS; do
        [ -e "$DIR/prose.$(id_of "$PROSE").$a" ] || owed="$owed prose:$a"
    done
fi

case "${1:---check}" in
--record)
    [ $# -ge 2 ] || { echo "review-gate: --record needs an agent name" >&2; exit 2; }
    mkdir -p "$DIR"
    shift
    for a in "$@"; do
        case " $TCB_REVIEWERS " in *" $a "*) : > "$DIR/tcb.$(id_of "$TCB").$a" ;; esac
        case " $PROSE_REVIEWERS " in *" $a "*) : > "$DIR/prose.$(id_of "$PROSE").$a" ;; esac
        case " $SUITE_REVIEWERS " in *" $a "*) : > "$DIR/suite.$(id_of "$SUITE").$a" ;; esac
    done
    echo "review-gate: recorded$(printf ' %s' "$@")"
    ;;
--status)
    echo "review-gate: base $(base | cut -c1-8)"
    printf '%s\n' "$CH" | sed 's/^/  changed: /' | head -20
    [ -n "$owed" ] && echo "  OWED:$owed" || echo "  nothing owed"
    ;;
--check)
    if [ -n "$owed" ]; then
        echo "review-gate: REVIEW OWED BEFORE PUSH:$owed" >&2
        echo "  build the packet first: sh tools/review-pack.sh" >&2
        echo "  dispatch them, then: sh tools/review-gate.sh --record <agent>..." >&2
        exit 1
    fi
    echo "review-gate: ok"
    ;;
*) echo "review-gate: unknown option $1" >&2; exit 2 ;;
esac
