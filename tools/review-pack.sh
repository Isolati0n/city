#!/bin/sh
# review-pack.sh — build the packet a reviewer is dispatched with.
#
# Reviewers cost roughly 90k tokens each, most of it spent rediscovering the
# repository because the prompt said "go and look". Handing over the diff, the
# files it touched and the suite's own environment block turns a search into a
# read. Written 2026-09-10 to make running a reviewer cheap enough to be the
# default rather than a decision.
#
# It writes a FILE and prints its path, rather than to stdout. That is the
# point: a reviewer is told to read the path, so the diff never passes
# through the dispatching context. Piping it into a prompt would move the
# cost rather than remove it, which is why the stdout version went unused
# the one time there was an opportunity.
#
#   sh tools/review-pack.sh            -> writes .reviews/packet-<id>.md
#   sh tools/review-pack.sh - [base]   -> stdout instead
set -eu
OUT=''
if [ "${1:-}" = "-" ]; then shift; else OUT=auto; fi
BASE=${1:-$(git rev-parse --verify --quiet '@{u}' 2>/dev/null \
            || git rev-parse --verify --quiet origin/main 2>/dev/null \
            || git rev-parse HEAD)}

if [ -n "$OUT" ]; then
    mkdir -p .reviews
    OUT=".reviews/packet-$(git rev-parse --short HEAD)-$(date +%H%M%S).md"
    exec 3>&1 >"$OUT"
fi

echo "# Review packet"
echo
echo "Base: \`$(git rev-parse --short "$BASE")\`  Head: \`$(git rev-parse --short HEAD)\`"
echo "Working tree: $(git status --porcelain | wc -l | tr -d ' ') uncommitted paths"
echo
echo "## Files changed"
echo '```'
{ git diff --stat "$BASE"; git status --short; } | sed '/^$/d'
echo '```'
echo
echo "## TCB files in this change"
echo '```'
{ git diff --name-only "$BASE"; git status --porcelain | cut -c4-; } | sort -u \
  | grep -E '^(dawn|pid1|nwspawn|nwcheck|nwcheck_main|nwsup|lids|rescue)\.c$|^blob\.h$' \
  || echo "(none)"
echo '```'
echo
echo "## Diff"
echo '```diff'
git diff "$BASE"
git diff
echo '```'
echo
echo "## Environment the suite reports"
echo '```'
sed -n '/^== environment ==/,/^$/p' "${NW_SUITE_LOG:-/dev/null}" 2>/dev/null \
  || echo "(run: make test 2>&1 | tee suite.log; NW_SUITE_LOG=suite.log)"
echo '```'
echo
echo "Read your brief in \`.claude/agents/\` and apply it to the diff above."
echo "Do not re-derive the repository; everything you need to review is here."
echo "Every finding must carry the command that shows it and that command's"
echo "verbatim output, or be labelled HYPOTHESIS."

if [ -n "$OUT" ]; then
    exec 1>&3 3>&-
    echo "$OUT"
fi
