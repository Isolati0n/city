#!/bin/sh
# ownership.sh — which tracked files does no territory scope claim?
#
# Derived from `.claude/rules/*.md`, never written down twice. CLAUDE.md
# carried a hand-maintained list of unassigned files for about half an hour
# on 2026-09-11 and it was wrong on two of six entries both times it was
# written: it called `lids.c` unassigned when `runtime.md` names it, called
# `nwcheck_main.c` unassigned when `plan.md` names it, and silently dropped
# `proofs/`, which the table it replaced had owned. A list of files that
# must agree with another list of files is the shape invariant 3 exists to
# prevent; the fix is the same one applied to NW_DUP_SLOTS and NW_BLOB_MAX,
# which is to have one copy and compute the other.
#
#   sh tools/ownership.sh            what no scope claims
#   sh tools/ownership.sh --all      every tracked file and its owner
set -eu
cd "$(dirname "$0")/.."

[ -d .claude/rules ] || { echo "ownership: no .claude/rules" >&2; exit 2; }

# A scope line names its files after "Scope: Owns ...". Take the file-shaped
# tokens; directories are handled by prefix below.
scope_files() {
    grep -h 'Scope:' .claude/rules/*.md \
      | grep -oE '[A-Za-z_][A-Za-z0-9_./*-]*\.(c|h|py|als|tla)' | sort -u
}

owner_of() {   # owner_of <path> -> territory name, or empty
    for r in .claude/rules/*.md; do
        base=$(basename "$r" .md)
        line=$(grep -h 'Scope:' "$r" 2>/dev/null || true)
        # exact filename, or the directory a glob like houses/*.c names
        case "$line" in
            *"$1"*) echo "$base"; return 0 ;;
        esac
        dir=${1%%/*}
        [ "$dir" != "$1" ] && case "$line" in
            *"$dir/"*) echo "$base"; return 0 ;;
        esac
    done
    return 0
}

all=0
[ "${1:-}" = "--all" ] && all=1

unowned=0
git ls-files | while IFS= read -r f; do
    case "$f" in
        .git/*|.reviews/*) continue ;;
    esac
    o=$(owner_of "$f")
    if [ -n "$o" ]; then
        [ "$all" -eq 1 ] && printf '  %-44s %s\n' "$f" "$o"
    else
        if [ "$all" -eq 1 ]; then
            printf '  %-44s %s\n' "$f" "-- no scope claims this"
        else
            printf '  %s\n' "$f"
        fi
        unowned=$((unowned + 1))
    fi
done

if [ "$all" -eq 0 ]; then
    echo
    echo "ownership: the above are claimed by no scope in .claude/rules/."
    echo "ownership: that is a statement about the scopes, not a defect --"
    echo "ownership: docs and tooling are deliberately unclaimed. What"
    echo "ownership: matters is that a file nobody owns is a file two"
    echo "ownership: agents can edit at once. Run --all to see the rest."
fi
