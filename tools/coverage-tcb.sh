#!/bin/sh
# coverage-tcb.sh — how much of the validator do the tests actually execute?
#
# Nobody had measured this until 2026-09-10. The answer was 83.33% of
# nwcheck.c, and the *uncovered list* was worth more than the number: it
# showed nw_crc32 is exported and called by nothing while nw_check inlines
# its own copy, and that NW_E_DUPNAME has never been produced by nw-check at
# all -- the only duplicate-name test rejects at the baker.
#
# So this prints the uncovered lines every run. The floor only stops the
# number sliding; the list is what finds things.
#
#   sh tools/coverage-tcb.sh [floor-percent]
set -eu
# The floor is the measurement, not an aspiration -- it starts where the
# code actually is and only ratchets up. Note the number depends on the
# corpus, so this is meant to run in one context: immediately after
# tests/run.py, over the blobs that run left behind. Measured 79% there on
# 2026-09-10, once the percentage was read from gcov rather than recomputed
# wrongly. A bare `make stage` gives less -- the same code with fewer inputs
# -- which is what the corpus guard below exists to distinguish.
# Ratcheted to 99 on 2026-09-11. What the uncovered list bought, in one day:
# nw_crc32 was a dead export and is gone; NW_E_DUPNAME, NW_E_KIND,
# NW_E_LIDS and NW_E_LLBRICK were checks no test had ever reached, because
# the baker refuses all four and every blob the suite had came from the
# baker -- crafted-blob tests reach them now. Leaving the floor at 83 while
# the measurement climbed to 99 would have let sixteen points slide out
# silently, which is the defect this script exists to catch, one layer up;
# tcb-review found it. If this fails on your machine, that is a finding to
# report, not a number to lower.
#
# The one line still uncovered is name_dup's full-table return, and it is
# uncovered because blob.h asserts at compile time that the table cannot
# fill. Unreachable by construction is the right reason for a line to be
# cold; "no test does that" is not.
#
# READ THE NUMBER HONESTLY. This is LINE coverage, and a line that is both
# a condition and its consequence counts as covered when the condition runs
# -- `if (u[i]._pad != 0) return NW_E_RSV;` is marked executed by every
# unit with a clean spare byte, with the return never taken. So 99% does
# not mean every rejection path has fired. The worked example used to be
# the brick path check; phase 3 deleted that line, and tcb-review had
# already shown that removing it left the suite, this floor AND the CBMC
# caller proof all green.
#
# 2026-09-12 sharpens it: nwcheck.c's bind loop tested `brick[0]` instead
# of scanning the hash, refusing one legal plan in 256 -- a fully covered
# line, at 99%, with a defect in it that three reviewers found by reading
# and no coverage number could have. The uncovered LIST is still what finds things; the percentage only
# stops it sliding. A branch-coverage pass (gcov -b) would say more and has
# not been done.
FLOOR=${1:-99}
ROOT=$(pwd)
W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT

gcc --coverage -O0 -I"$ROOT" -c -o "$W/nwcheck.o" "$ROOT/nwcheck.c"
gcc --coverage -O0 -I"$ROOT" -c -o "$W/main.o"    "$ROOT/nwcheck_main.c"
gcc --coverage -o "$W/nw-check" "$W/nwcheck.o" "$W/main.o"

# Every blob the suite produced, plus byte-flips of a good one.
STAGE=${NW_STAGE:-/tmp/nw-init-run}

# Without a stage there are no inputs, and the run reports 0% -- which reads
# as "the tests cover nothing" rather than "you gave me nothing to measure".
# An instrument that cannot tell those apart is the defect this project keeps
# finding, so say which one it is.
# The first version checked only for A/plan.blob, which `make stage` always
# writes -- so it could never fire from `make test`, and the case that does
# happen (a stage tests/run.py never ran against) slipped past it and was
# reported as a coverage regression. The suite's contribution to the corpus
# is work/*.blob, so that is what to look for.
if [ ! -f "$STAGE/efi/slots/A/plan.blob" ]; then
    echo "coverage-tcb: no stage at $STAGE -- run 'make stage' first." >&2
    exit 2
fi
if [ -z "$(find "$STAGE/work" -maxdepth 1 -name '*.blob' -print -quit 2>/dev/null)" ]
then
    echo "coverage-tcb: $STAGE has no work/*.blob, so tests/run.py has not" >&2
    echo "  run against it. Measuring now reports a low number that looks" >&2
    echo "  like a coverage regression and means the wrong corpus." >&2
    exit 2
fi
for f in "$STAGE"/efi/slots/A/plan.blob "$STAGE"/efi/slots/B/plan.blob \
         "$STAGE"/work/*.blob; do
    [ -f "$f" ] && "$W/nw-check" "$f" >/dev/null 2>&1 || true
done
if [ -f "$STAGE/efi/slots/A/plan.blob" ]; then
    python3 - "$W" "$STAGE/efi/slots/A/plan.blob" <<'PY'
import subprocess, sys
w, good = sys.argv[1], open(sys.argv[2], 'rb').read()
for i in range(400):
    d = bytearray(good); d[i % len(d)] ^= 1 + (i % 7)
    open(w + '/f.blob', 'wb').write(bytes(d))
    subprocess.run([w + '/nw-check', w + '/f.blob'], capture_output=True)
PY
fi

cd "$W"
GCOV=$(gcov -o "$W" "$ROOT/nwcheck.c" 2>/dev/null || true)
[ -f nwcheck.c.gcov ] || { echo "coverage-tcb: gcov produced nothing" >&2; exit 1; }

# Take gcov's own figure rather than re-deriving it. The first version
# counted lines with an awk regex of ^ *[0-9]+: and reported 79%, while gcov
# said 83.33% for the same run. Cause: gcov marks a line whose branches are
# only partly taken as "403*:", the regex missed the asterisk, and 24 of
# nwcheck.c's 108 executable lines fell out of BOTH numerator and
# denominator -- so "of executable lines" was false as printed. Worse, the
# 79-versus-83 gap was then explained away as a corpus difference instead of
# investigated. One number, from the tool that computes it.
pct=$(printf '%s\n' "$GCOV" | awk '
    /^File .*nwcheck\.c/            { f = 1; next }
    f && /^Lines executed:/          { sub(/.*:/, ""); sub(/[.%].*/, "");
                                       print; exit }')
case "$pct" in
    ''|*[!0-9]*)
        echo "coverage-tcb: could not read a percentage from gcov" >&2
        exit 1 ;;
esac
echo "coverage-tcb: nwcheck.c ${pct}% of executable lines (floor ${FLOOR}%)"
echo "  never executed:"
grep "#####" nwcheck.c.gcov | sed 's/^ *#####: *//' | sed 's/^/    /' | head -40
if [ "$pct" -lt "$FLOOR" ]; then
    echo "coverage-tcb: FAIL ${pct}% is below the floor of ${FLOOR}%" >&2
    exit 1
fi
