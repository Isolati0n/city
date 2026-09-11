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

# A base equal to HEAD produces an empty diff, an empty file list, and
# "(none)" under TCB files -- a packet that positively asserts nothing
# changed. That happened: the branch was pushed before the review, so
# `@{u}` resolved to HEAD, and two reviewers were handed a packet claiming
# no TCB file had changed while the round's HIGH finding sat in pid1.c.
# Both went and derived the diff themselves, which is the cost this script
# exists to remove.
#
# So when the upstream has caught up with HEAD, fall back to where this
# branch left the default one -- which is what a reviewer means by "this
# change" -- and refuse only when the packet would genuinely be empty. The
# test is on the CONTENT, not on the base: an empty diff and "nothing
# changed" must not be spelled the same way, and that is the same defect as
# the empty environment block below, with the same cause -- a command that
# SUCCEEDS and produces nothing.
if [ -z "${1:-}" ] \
   && [ "$(git rev-parse "$BASE")" = "$(git rev-parse HEAD)" ]; then
    mb=$(git merge-base HEAD origin/main 2>/dev/null || true)
    [ -n "$mb" ] && [ "$mb" != "$(git rev-parse HEAD)" ] && BASE=$mb
fi
if [ -z "$(git diff --name-only "$BASE" 2>/dev/null)" ] \
   && [ -z "$(git status --porcelain 2>/dev/null)" ]; then
    echo "review-pack: there is nothing between $(git rev-parse --short "$BASE")" \
         "and HEAD, and the working tree is clean." >&2
    echo "review-pack: a packet here would say 'no TCB file changed', which" \
         "reads as a fact rather than as 'I could not tell'." >&2
    echo "review-pack: name the base explicitly --" \
         "sh tools/review-pack.sh [-] <base>" >&2
    exit 1
fi

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
# Ask the suite, do not scrape a log. This read NW_SUITE_LOG or /dev/null
# until 2026-09-11: with the variable unset, sed on /dev/null succeeded and
# printed nothing, the `||` fallback never fired, and the packet carried an
# EMPTY block -- so every reviewer was handed a file whose environment
# section said nothing, while CLAUDE.md requires reporting that block with
# any suite result. fd-auditor read the empty block and went and generated
# its own. A fallback that only runs on failure does not cover the case
# where the command succeeds and produces nothing.
env_block=$(NW_STAGE="${NW_STAGE:-/tmp/nw-init-run}" python3 -c '
import runpy, sys
m = runpy.run_path("tests/run.py")
m["print_environment"]()
' 2>&1) || env_block=""
if printf '%s' "$env_block" | grep -q "^== environment =="; then
    printf '%s\n' "$env_block"
else
    echo "COULD NOT DETERMINE THE ENVIRONMENT. This is not an empty"
    echo "environment: it means tests/run.py would not run here, usually"
    echo "because nothing is staged. Run 'make stage' and re-pack. Do not"
    echo "report a suite result against this packet without a real block."
    [ -n "$env_block" ] && printf '%s\n' "$env_block"
fi
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
