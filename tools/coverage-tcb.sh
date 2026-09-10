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
# 2026-09-10; measured 71% after a bare `make stage`, which is the same code
# with fewer inputs.
FLOOR=${1:-79}
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
if [ ! -f "$STAGE/efi/slots/A/plan.blob" ]; then
    echo "coverage-tcb: no blobs at $STAGE -- run 'make stage' first." >&2
    echo "  (measuring now would report 0%, which is not the same as" >&2
    echo "   the tests covering nothing)" >&2
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
gcov -o "$W" "$ROOT/nwcheck.c" >/dev/null 2>&1 || true
[ -f nwcheck.c.gcov ] || { echo "coverage-tcb: gcov produced nothing" >&2; exit 1; }

pct=$(awk -F'[:.]' '/^ *#####/{u++} /^ *[0-9]+:/{c++} END{printf "%d", (c*100)/(c+u)}' nwcheck.c.gcov)
echo "coverage-tcb: nwcheck.c ${pct}% of executable lines (floor ${FLOOR}%)"
echo "  never executed:"
grep "#####" nwcheck.c.gcov | sed 's/^ *#####: *//' | sed 's/^/    /' | head -40
if [ "$pct" -lt "$FLOOR" ]; then
    echo "coverage-tcb: FAIL ${pct}% is below the floor of ${FLOOR}%" >&2
    exit 1
fi
