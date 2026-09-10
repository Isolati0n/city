#!/bin/sh
# review-gate.sh — refuse to push a change no reviewer has seen.
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
SUITE='tests/run.py unit_probe.c houses'
TCB_REVIEWERS='tcb-review fd-auditor'
SUITE_REVIEWERS='control'

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

case "${1:---check}" in
--record)
    [ $# -ge 2 ] || { echo "review-gate: --record needs an agent name" >&2; exit 2; }
    mkdir -p "$DIR"
    shift
    for a in "$@"; do
        case " $TCB_REVIEWERS " in *" $a "*) : > "$DIR/tcb.$(id_of "$TCB").$a" ;; esac
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
