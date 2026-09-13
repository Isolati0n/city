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
# /var/tmp, not /tmp: tcb-review saw two consecutive listings of a /tmp
# directory return different file sets in this sandbox with no run in
# between, which produced a spurious `make proof` failure. Override with
# NW_PROOF_OUT.
# PER TREE, because the directory was a fixed machine-global path and two
# agents on one machine wrote to it. `fd-auditor` ran mutant proofs from a
# scratch copy and overwrote this tree's artifacts with VERIFICATION FAILED
# output from a deliberately broken nwcheck.c -- and nothing in the files
# said which tree produced them, so the next reader of the .txt draws a
# wrong conclusion about a correct tree. Same shape as the suite's stage
# path, answered the same way: derive it, do not share it.
OUT=${NW_PROOF_OUT:-/var/tmp/nw-proofs-$(printf %s "$(cd "$ROOT" && pwd -P)" \
        | cksum | cut -d' ' -f1)}
rm -rf "$OUT"; mkdir -p "$OUT"

command -v cbmc >/dev/null 2>&1 || {
    echo "proofs: cbmc is not installed -- this is a SKIP, not a pass." >&2
    echo "proofs: nothing below was checked." >&2
    exit 3
}
echo "proofs: $(cbmc --version 2>&1 | head -1)"

python3 proofs/mkcomp.py nwcheck.c "$OUT/nwcheck_comp.c"

# Type-check the caller harness with a real C compiler before handing it to
# CBMC. This is the mechanism that keeps "abstracted" honest: a stub whose
# signature no longer matches the function it replaces is a conflicting
# declaration, so a leaf that changed shape stops the proof.
#
# It has to be gcc. **CBMC does not enforce this** -- it type-checked a stub
# declared `int slot[static NW_DUP_SLOTS]` against a generated declaration
# reading `struct nw_dup_tab *` and went on to solve, while gcc rejects the
# same file outright. proofs/README.md claimed the collision "would fail the
# compile"; that was true of a compiler nothing was running. Found by
# changing name_dup's signature and watching the proof proceed.
# EVERY harness, not just the caller. This ran on caller_nw_check.c alone
# until 2026-09-11, and leaf_name_dup.c was meanwhile calling
# name_dup(int *, ...) after the parameter had become struct nw_dup_tab * --
# an implicit pointer conversion, accepted silently by CBMC, so the proof
# ran against a signature that no longer existed. A gate that covers one of
# four files is the shape of a mechanism that looks like it is working.
# Found by fd-auditor.
echo "proofs: signature check (gcc, not cbmc -- cbmc does not enforce this)"
for h in proofs/leaf_path_ok.c proofs/leaf_name_ok.c proofs/leaf_name_dup.c \
         proofs/caller_nw_check.c; do
    gcc -fsyntax-only -std=gnu11 -Wall -Wextra -Werror -DPROOF_SIGCHECK \
        -Wno-type-limits \
        -D'__CPROVER_havoc_object(x)=((void)(x))' \
        -D'__CPROVER_assume(x)=((void)(x))' \
        -D'__CPROVER_assert(x,y)=((void)(x))' \
        -I"$ROOT" -I"$OUT" "$h" || {
        echo "proofs: $h does not type-check against the code it proves --" >&2
        echo "proofs: a stub or a call site no longer matches the function" >&2
        echo "proofs: it stands for. Fix it; do not run cbmc, which would" >&2
        echo "proofs: accept it." >&2
        exit 1
    }
done

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
#   caller         the unit loop must be bounded and the others must not;
#                  its CBMC id is discovered below rather than written down.

# CBMC loop ids, discovered rather than written down. They are positional
# within a function, so ADDING OR REMOVING ANY LOOP RENUMBERS THE REST --
# which has now bitten twice in one day, both times silently:
#
#   * moving the slot init into name_dup_init turned the caller's unit loop
#     from nw_check.4 into nw_check.3, so the hardcoded bound landed on the
#     bind loop and the caller proof went from 81 s to a 366-second death
#     with no result line;
#   * the same edit renumbered main's loops in leaf_name_dup.c, so the
#     128-iteration init loop lost its bound and the proof failed on an
#     unwinding assertion.
#
# A loop id written down is a second declaration of where the code is. This
# resolves each one from the loop's source line, so the numbering can move.
loop_id() {   # loop_id <file> <needle> [extra cbmc args...]
    f=$1; needle=$2; shift 2
    python3 - "$f" "$needle" "$OUT" "$@" <<'PY'
import re, subprocess, sys
f, needle, out = sys.argv[1], sys.argv[2], sys.argv[3]
extra = sys.argv[4:]
# The needle may live in the harness or in the translation unit it includes.
cands = [f, out + "/nwcheck_comp.c", "nwcheck.c"]
want = None
for c in cands:
    try:
        for n, line in enumerate(open(c), 1):
            if needle in line:
                want, where = n, c
                break
    except OSError:
        continue
    if want:
        break
if want is None:
    sys.exit("run.sh: no loop matching %r for %s" % (needle, f))
p = subprocess.run(["cbmc", "--show-loops", "-I" + out, "-I."] + extra + [f],
                   capture_output=True, text=True)
# THE SAME SHAPE check_unwindset WAS FIXED FOR, three functions below,
# on the same day and left here. capture_output discards cbmc's stderr,
# so a cbmc that FAILED TO RUN produced no stdout, `hit` stayed None,
# and the refusal below blamed the SOURCE -- "is at nwcheck.c:194 but no
# loop reports that line" -- for a loop that is there. Reproduced by
# `tcb-review` with a stand-in cbmc exiting 137 on `out of memory`.
# Never discard a stream you are about to draw a conclusion from.
if p.returncode != 0 or not p.stdout.strip():
    sys.exit("run.sh: cbmc --show-loops did not produce a loop list "
             "(exit %d). This is the TOOL failing, not a needle that is "
             "absent. Its output:\n%s%s"
             % (p.returncode, p.stderr, p.stdout))
cur = hit = None
for line in p.stdout.splitlines():
    m = re.match(r"^Loop (\S+?):", line)
    if m:
        cur = m.group(1)
    elif cur and re.search(r"\bline %d\b" % want, line):
        hit = cur
        break
if not hit:
    sys.exit("run.sh: %r is at %s:%d but no loop reports that line"
             % (needle, where, want))
print(hit)
PY
}

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
# Three, not two. leaf_name_dup.c's own docstring says the probe chain is
# never needed at two units -- equal names have equal hashes, so a
# duplicate's match is always in the slot it hashes to -- and the default
# was two, so `make proof` ran the one N the harness says cannot exercise
# the case it exists for. Truncating the chain to a single slot left the
# proof SUCCESSFUL at 2 and fails it at 3. Found by `control`, which is
# the second time a comment in this tree described a gap that nothing then
# closed.
DUP_UNITS=${PROOF_UNITS:-3}
N1=$((NAME_LEN + 1))
# One global bound large enough for every constant-trip loop here (the
# NW_NAME_LEN scans and the NW_DUP_SLOTS init), and a tight bound on the
# one loop that is expensive and data-dependent: the probe chain. Naming
# each loop by id is what broke when the init moved into a function.
DUP_GLOBAL=$((DUP_SLOTS > NAME_LEN ? DUP_SLOTS + 1 : NAME_LEN + 1))
DUP_PROBE=$(loop_id proofs/leaf_name_dup.c "for (int p = 0; p < NW_DUP_SLOTS; p++)" \
            -DPROOF_UNITS=$DUP_UNITS)
echo "proofs: probe loop $DUP_PROBE (discovered)"
DUP_UNW="--unwind $DUP_GLOBAL --unwindset ${DUP_PROBE}:$((DUP_UNITS + 2))"

# Two units, not one. At one unit every per-unit check is pinned for u[0]
# only: changing `u[i].kind` to `u[0].kind` and `u[i].lids` to `u[0].lids`
# left this proof VERIFICATION SUCCESSFUL, and the suite green, while the
# same byte on any later unit validated. At two units both assertions fail.
# `control` measured it; the extra unit costs a couple of seconds.
CU=${PROOF_UNITS:-2}
CB=${PROOF_BINDS:-0}
UNIT_LOOP=$(loop_id proofs/caller_nw_check.c "i < h->n_units" \
            -DPROOF_UNITS=1u -DPROOF_BINDS=0u)
BIND_LOOP=$(loop_id proofs/caller_nw_check.c "i < h->n_binds" \
            -DPROOF_UNITS=1u -DPROOF_BINDS=0u)
echo "proofs: unit loop $UNIT_LOOP, bind loop $BIND_LOOP (discovered)"
caller_unw() {   # caller_unw <units> <binds>
    echo "--unwind 200 --unwindset ${UNIT_LOOP}:$(($1 + 1)),${BIND_LOOP}:$(($2 + 1))"
}
# BOTH, and this is the point: __CPROVER_assume constrains the solver, not
# the unroller, so a loop whose trip count is only pinned by an assumption
# still unwinds to the global bound. Leaving the bind loop free cost a
# 366-second run with no result line.
CALLER_UNW=$(caller_unw "$CU" "$CB")

# Every --unwindset name must be a loop CBMC actually reports. CBMC
# SILENTLY IGNORES a name that matches nothing, so a bound left behind by a
# refactor reads as a bound and is not one: `main.2:129` survived the slot
# init moving into a function, named nothing, and the 128-iteration loop it
# used to cover fell back to the global bound of 5. Found by tcb-review.
# The unwinding assertion caught the consequence; nothing caught the cause.
# A SUBSHELL FUNCTION -- the parentheses are the fix, not decoration.
# POSIX sh has no local scope, so this function's `f=` reached its
# CALLER: run.sh's field loop is `for f in layer name`, expect() calls
# this, and by the next line $f held "proofs/leaf_name_dup.c". The
# vacuity run was then handed -DPROOF_FIELD_NAME=proofs/leaf_name_dup.c
# and compiled `u[i].proofs/leaf_name_dup.c[0]`.
#
# Both field_dup vacuity controls therefore NEVER RAN, and neither did
# either `name`-offset proof -- and at the default bound the caller
# proofs never ran either, because the run dies after the 2595-second
# layer PASS. HISTORY.md 78 recorded that stall as a cbmc failing under
# memory pressure and the missing controls as a cost problem. It was
# this, reproducible in 65 s at PROOF_UNITS=2 with no memory pressure,
# introduced by the same diff that fixed the guard below. `tcb-review`.
#
# Subshell rather than renaming `f`: a rename fixes the instance and
# leaves the class, and the next loop variable is the same bug. Here
# every assignment is local by construction and a caller variable
# cannot be reached. expect() cannot take this form -- its `exit 1`
# has to abort the run -- so its locals are prefixed instead.
check_unwindset() (   # check_unwindset <file> <args...>
    f=""; set_arg=""
    for a in "$@"; do
        case "$a" in *.c) f=$a ;; esac
    done
    prev=""
    for a in "$@"; do
        [ "$prev" = "--unwindset" ] && set_arg=$a
        prev=$a
    done
    [ -n "$set_arg" ] && [ -n "$f" ] || return 0
    # STDERR KEPT, AND THE EXIT STATUS CHECKED. This was
    # `cbmc --show-loops ... 2>/dev/null`, so a cbmc that FAILED TO RUN
    # produced no output and the guard below reported the failure as
    # "--unwindset names X, which is not a loop in this program" --
    # blaming the bound for the tool not running, with the diagnosis
    # printed and the real error thrown away.
    #
    # Measured 2026-09-13: immediately after leaf_field_dup_layer's
    # 2595-second, 3.1 GB solve, this invocation produced nothing and the
    # run stopped with `Loops here:` followed by an empty list. A program
    # with NO loops at all is impossible here -- there are 25 -- so the
    # empty list was the tell, and it was the only one, because the
    # message it printed was about something else entirely.
    #
    # That is this repository's characteristic failure inside the guard
    # written to catch a silent one: a true-looking sentence next to a
    # tool that did not do what it says.
    loops_raw=$(cbmc --show-loops -I"$ROOT" -I"$OUT" "$@" 2>&1)
    loops_rc=$?
    loops=$(printf '%s\n' "$loops_raw" | sed -n 's/^Loop \(.*\):$/\1/p')
    if [ $loops_rc -ne 0 ] || [ -z "$loops" ]; then
        echo "proofs: cbmc --show-loops did not produce a loop list" >&2
        echo "proofs: (exit $loops_rc). This is the TOOL failing, not a" >&2
        echo "proofs: bound naming a loop that is absent -- every program" >&2
        echo "proofs: here has loops. Its output:" >&2
        printf '%s\n' "$loops_raw" | sed 's/^/proofs:   /' >&2
        return 1
    fi
    echo "$set_arg" | tr ',' '\n' | while IFS= read -r pair; do
        [ -n "$pair" ] || continue
        nm=${pair%%:*}
        printf '%s\n' "$loops" | grep -qx -- "$nm" || {
            echo "proofs: --unwindset names $nm, which is not a loop in" >&2
            echo "proofs: this program. CBMC ignores it silently, so the" >&2
            echo "proofs: bound is not applied. Loops here:" >&2
            printf '%s\n' "$loops" | sed 's/^/proofs:   /' >&2
            exit 1
        }
    done
)

expect() {       # expect PASS|FAIL NAME cbmc-args...  -> $OUT/NAME.txt
    # PREFIXED, for the reason check_unwindset is a subshell: this
    # function cannot be one, because its `exit 1` must abort the run
    # rather than a subshell. So its locals are spelled so they cannot
    # be a caller's loop variable.
    _x_want=$1; _x_name=$2; shift 2
    check_unwindset "$@" || exit 1
    printf '  %-26s ' "$_x_name"
    _x_start=$(date +%s)
    # STAMPED, so a result file says which tree and which TCB bytes it was
    # produced from. A .txt with no provenance is a proof kept where it
    # cannot be re-run, in miniature: it reads as a result about whatever
    # tree the reader happens to be in.
    {
        echo "### proofs/run.sh: $_x_name"
        echo "### tree:    $(cd "$ROOT" && pwd -P)"
        # EVERY FILE CBMC IS HANDED, not a hand-written two. It cksummed
        # nwcheck.c and blob.h only, so a mutated proofs/caller_nw_check.c
        # -- exactly the mutation `control` performs -- produced a stamp
        # byte-identical to an honest run. A hand-listed pair beside a
        # generated one is the drift class, in the provenance line.
        echo "### sources: $(cat "$ROOT/nwcheck.c" "$ROOT/blob.h" \
                             "$ROOT"/proofs/*.c "$ROOT"/proofs/*.py \
                             "$ROOT/proofs/run.sh" \
                             | cksum | tr -s ' ' | cut -d' ' -f1,2)"
        # AGAINST HEAD, not the index. `git diff --quiet` compares the
        # worktree to the INDEX, so `git add` made a mutant read clean --
        # the guard worked for the careless case and not the tidy one,
        # which is the wrong way round.
        echo "### git:     $(cd "$ROOT" && git rev-parse --short HEAD \
                             2>/dev/null || echo none)$(cd "$ROOT" \
                             && git diff HEAD --quiet 2>/dev/null \
                             || echo '+dirty')"
        echo "### when:    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$OUT/$_x_name.txt"
    # ONE WRITE AND ONE READ, from the same bytes. This ran cbmc with
    # `>> file` and then re-opened the file BY NAME to grep the result
    # line it had just produced -- a second path to the same data, in a
    # sandbox this script's own header records returning different
    # directory listings for consecutive reads. `tcb-review` caught it
    # flaking one run in six: a control that had finished correctly
    # (`** 1 of 381 failed`, `VERIFICATION FAILED`) was reported as
    # `NO RESULT LINE ... it did not finish`, which is the diagnosis
    # reserved for a killed solve and the one people answer by
    # budgeting more machine.
    #
    # `|| _x_rc=$?` rather than a bare assignment: under `set -e` a
    # command substitution whose command fails aborts the script, and
    # a FAILING cbmc is the expected outcome for every control here.
    _x_rc=0
    _x_out=$(cbmc "$@" $CHECKS -I"$ROOT" -I"$OUT" 2>&1) || _x_rc=$?
    printf '%s\n' "$_x_out" >> "$OUT/$_x_name.txt"
    if [ "$_x_rc" -eq 0 ]; then _x_got=PASS; else _x_got=FAIL; fi
    _x_end=$(date +%s)
    _x_props=$(printf '%s\n' "$_x_out" \
               | grep -oE '^\*\* [0-9]+ of [0-9]+ failed' | head -1)
    echo "$_x_got (want $_x_want)  ${_x_props:-NO RESULT LINE}  $((_x_end - _x_start))s"
    if [ "$_x_got" != "$_x_want" ]; then
        echo "proofs: $_x_name gave $_x_got, expected $_x_want -- $OUT/$_x_name.txt" >&2
        grep -E ': FAILURE|VERIFICATION|^Killed' "$OUT/$_x_name.txt" \
            | head -20 >&2
        exit 1
    fi
    # A run that produced no "** n of m failed" line did not finish -- CBMC
    # killed for memory exits non-zero and would otherwise read as a control
    # that correctly failed.
    if [ -z "$_x_props" ]; then
        echo "proofs: $_x_name produced no result line: it did not finish" >&2
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
    # Every width nw_check calls it at. There were TWO -- NW_PATH_LEN and
    # NW_BRICK_LEN -- and running at one of them let a
    # `if (max != NW_PATH_LEN) return 1;` mutant pass. Phase 3 made the
    # brick a 32-byte hash, so nothing calls path_ok_len at any other
    # width: exec_path and every bind are NW_PATH_LEN and there is no
    # second one left to miss. Derive the list rather than writing it, so
    # a future second width arrives here by itself.
    for w in "$(blob_h blob.h NW_PATH_LEN)"; do
        expect PASS "leaf_path_ok_$w" $LEAF_PATH_UNW \
            -DPROOF_PATH_MAX=$w proofs/leaf_path_ok.c
        expect FAIL "leaf_path_ok_${w}_vacuity" $LEAF_PATH_UNW \
            -DPROOF_PATH_MAX=$w -DPROOF_VACUITY proofs/leaf_path_ok.c
    done
fi

if wanted name_ok; then
    expect PASS leaf_name_ok  $LEAF_NAME_UNW proofs/leaf_name_ok.c
    expect FAIL leaf_name_ok_vacuity $LEAF_NAME_UNW -DPROOF_VACUITY \
        proofs/leaf_name_ok.c
fi

if wanted name_dup; then
    # BOTH OFFSETS. 0a42f63 generalised name_dup into field_dup by adding
    # an `off` argument, and nw_check now runs the pass over `name` and
    # over `layer`. `name` is at offset 0, where `(const char *)&u[i] +
    # off` is a no-op -- so the run that had always existed exercises
    # none of the arithmetic the generalisation introduced. Running only
    # `name` would be a proof passing at the one value where the thing it
    # is about does nothing.
    #
    # The layer run is first because it is the one that can fail for the
    # new reason; the name run is kept because offset 0 is a real call
    # site and not merely the degenerate one.
    for f in layer name; do
        case $f in
        layer) off="offsetof(struct nw_unit, layer)" ;;
        name)  off="offsetof(struct nw_unit, name)" ;;
        esac
        expect PASS leaf_field_dup_$f $DUP_UNW -DPROOF_UNITS=$DUP_UNITS \
            -DPROOF_FIELD_OFF="$off" -DPROOF_FIELD_NAME=$f \
            proofs/leaf_name_dup.c
        expect FAIL leaf_field_dup_${f}_vacuity $DUP_UNW \
            -DPROOF_UNITS=$DUP_UNITS \
            -DPROOF_FIELD_OFF="$off" -DPROOF_FIELD_NAME=$f \
            -DPROOF_VACUITY proofs/leaf_name_dup.c
    done
fi

if wanted caller; then
    # PROOF_UNITS / PROOF_BINDS were documented as raising this and were
    # passed to nothing: the harness's own #ifndef defaults always won, so
    # `PROOF_UNITS=3 sh proofs/run.sh caller` produced a byte-identical
    # one-unit run that read like a three-unit one. Found by `claims`.
    expect PASS caller_nw_check $CALLER_UNW \
        -DPROOF_UNITS=${CU}u -DPROOF_BINDS=${CB}u proofs/caller_nw_check.c
    expect FAIL caller_nw_check_vacuity $CALLER_UNW \
        -DPROOF_UNITS=${CU}u -DPROOF_BINDS=${CB}u -DPROOF_VACUITY \
        proofs/caller_nw_check.c

    # And once with a bind, because at PROOF_BINDS=0 the bind assertions sit
    # in a zero-trip loop: `__CPROVER_assert(0, ...)` passes there, and two
    # of the caller's nine SUCCESS lines were saying nothing while
    # docs/plans/02 counted one of them as a post-condition it had grown.
    # An assertion that has never been reached is the unpaired-absence shape
    # from harness.md, in a proof. Found by `claims`.
    # Its own bounds: the bind loop needs one more iteration than the
    # default run allows, and --unwinding-assertions turned reusing the
    # default into a FAILURE rather than a quietly truncated search. Which
    # is the mechanism working -- a bound carried over from another
    # configuration is exactly the silent narrowing it exists to catch.
    BIND_UNW=$(caller_unw 1 1)   # one unit, one bind
    expect PASS caller_nw_check_bind $BIND_UNW \
        -DPROOF_UNITS=1u -DPROOF_BINDS=1u proofs/caller_nw_check.c
    expect FAIL caller_nw_check_bind_reached $BIND_UNW \
        -DPROOF_UNITS=1u -DPROOF_BINDS=1u -DPROOF_BIND_REACHED \
        proofs/caller_nw_check.c
fi

echo "proofs: every proof verified and every control failed."
echo "proofs: this is a BOUNDED result -- read proofs/README.md for what is"
echo "proofs: quantified over all inputs and what is fixed to a small N."
