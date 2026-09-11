#!/bin/sh
# Run the CBMC proofs of the validator, and their controls.
#
# Until 2026-09-11 these harnesses existed only in a session scratch
# directory while docs/plans/02 recorded "Tier A -- ACHIEVED" in the present
# tense. nwcheck.c was then edited twice more and nothing re-ran anything.
# That is the characteristic failure of this project with a proof in the
# place where the sentence usually goes, so the harnesses live here now and
# this script is what makes the claim checkable.
#
# Every proof runs with --unwinding-assertions. A bound that is too small
# then FAILS rather than silently truncating the exploration: run 1 of the
# original attempt reported "1 of 354 failed" on its own fill loop, which
# meant the last 82 bytes of the blob stayed concrete zeros, and it was read
# as a nuisance. It is the tool saying the input space was narrowed.
#
# Each proof is followed by a control that must FAIL. A proof nobody has
# seen fail is a proof nobody has tested -- same rule as the suite's
# negative controls, and the reason the leaf runs carry a PROOF_VACUITY
# variant: it catches contradictory assumptions, which would otherwise make
# every assertion pass for free.
#
# Usage: sh proofs/run.sh [name ...]     (default: all)
set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)
OUT=${NW_PROOF_OUT:-/tmp/nw-proofs}
rm -rf "$OUT"; mkdir -p "$OUT"

command -v cbmc >/dev/null 2>&1 || {
    echo "proofs: cbmc is not installed -- this is a SKIP, not a pass." >&2
    echo "proofs: nothing below was checked." >&2
    exit 3
}
echo "proofs: $(cbmc --version 2>&1 | head -1)"

python3 proofs/mkcomp.py nwcheck.c "$OUT/nwcheck_comp.c"

CHECKS="--bounds-check --pointer-check --signed-overflow-check
        --undefined-shift-check --div-by-zero-check --unwinding-assertions"

# Loop bounds, one line each, with why.
#
#   leaf_path_ok   path_ok_len scans NW_PATH_LEN and NW_PATH_LEN-3.
#   leaf_name_ok   name_ok scans NW_NAME_LEN; main's pairwise check is n^2.
#   leaf_name_dup  the probe chain is bounded to PROOF_UNITS+2 and the
#                  unwinding assertion PROVES that is enough -- with an
#                  empty table and N units at most N slots are taken, so a
#                  probe cannot walk further. Not an assumption.
#   caller         nw_check.4 is the unit loop and is what must be bounded;
#                  nw_check.2 is the slot init and bounding THAT costs a run
#                  to a spurious unwinding failure.

# Read the limits from blob.h rather than restating them. They were bare
# literals here until 2026-09-11 -- a 128 and five 33s, which are
# NW_DUP_SLOTS and NW_NAME_LEN + 1 under other names, in a file the drift
# check does not cover. `drift` measured that a stale bound fails loudly
# (--unwinding-assertions turns it into a FAILURE, not a truncated search),
# so this was never going to be silent; deriving it means it is not wrong
# either. Invariant 3 does not stop at the TCB.
blob_h() { awk -v k="$2" '$1=="#define" && $2==k {print $3}' "$1"; }
DUP_SLOTS=$(blob_h blob.h NW_DUP_SLOTS)
NAME_LEN=$(blob_h blob.h NW_NAME_LEN)
[ -n "$DUP_SLOTS" ] && [ -n "$NAME_LEN" ] || {
    echo "proofs: blob.h has no NW_DUP_SLOTS or no NW_NAME_LEN" >&2; exit 1; }

LEAF_PATH_UNW="--unwind 200"
LEAF_NAME_UNW="--unwind 200"
DUP_UNITS=${PROOF_UNITS:-2}
N1=$((NAME_LEN + 1))
DUP_UNW="--unwind 5 --unwindset name_dup.1:$((DUP_UNITS + 2)),name_dup.0:$N1,hash_name.0:$N1,fields_equal.0:$N1,main.0:$N1,main.1:$N1,main.2:$((DUP_SLOTS + 1))"
CALLER_UNW="--unwind 200 --unwindset nw_check.4:2"

expect() {       # expect PASS|FAIL NAME cbmc-args...  -> $OUT/NAME.txt
    want=$1; name=$2; shift 2
    printf '  %-26s ' "$name"
    start=$(date +%s)
    if cbmc "$@" $CHECKS -I"$ROOT" -I"$OUT" > "$OUT/$name.txt" 2>&1
    then got=PASS; else got=FAIL; fi
    end=$(date +%s)
    props=$(grep -oE '^\*\* [0-9]+ of [0-9]+ failed' "$OUT/$name.txt" | head -1)
    echo "$got (want $want)  ${props:-NO RESULT LINE}  $((end - start))s"
    if [ "$got" != "$want" ]; then
        echo "proofs: $name gave $got, expected $want -- $OUT/$name.txt" >&2
        grep -E ': FAILURE|VERIFICATION|^Killed' "$OUT/$name.txt" \
            | head -20 >&2
        exit 1
    fi
    # A run that produced no "** n of m failed" line did not finish -- CBMC
    # killed for memory exits non-zero and would otherwise read as a control
    # that correctly failed.
    if [ -z "$props" ]; then
        echo "proofs: $name produced no result line: it did not finish" >&2
        exit 1
    fi
}


sel="$*"
wanted() {
    [ -z "$sel" ] && return 0
    for w in $sel; do [ "$w" = "$1" ] && return 0; done
    return 1
}

echo "proofs: writing to $OUT"

if wanted path_ok; then
    expect PASS leaf_path_ok  $LEAF_PATH_UNW proofs/leaf_path_ok.c
    expect FAIL leaf_path_ok_vacuity $LEAF_PATH_UNW -DPROOF_VACUITY \
        proofs/leaf_path_ok.c
fi

if wanted name_ok; then
    expect PASS leaf_name_ok  $LEAF_NAME_UNW proofs/leaf_name_ok.c
    expect FAIL leaf_name_ok_vacuity $LEAF_NAME_UNW -DPROOF_VACUITY \
        proofs/leaf_name_ok.c
fi

if wanted name_dup; then
    expect PASS leaf_name_dup $DUP_UNW -DPROOF_UNITS=$DUP_UNITS \
        proofs/leaf_name_dup.c
    expect FAIL leaf_name_dup_vacuity $DUP_UNW -DPROOF_UNITS=$DUP_UNITS \
        -DPROOF_VACUITY proofs/leaf_name_dup.c
fi

if wanted caller; then
    expect PASS caller_nw_check $CALLER_UNW proofs/caller_nw_check.c
    expect FAIL caller_nw_check_vacuity $CALLER_UNW -DPROOF_VACUITY \
        proofs/caller_nw_check.c
fi

echo "proofs: every proof verified and every control failed."
echo "proofs: this is a BOUNDED result -- read proofs/README.md for what is"
echo "proofs: quantified over all inputs and what is fixed to a small N."
