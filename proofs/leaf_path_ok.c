/* CBMC harness: path_ok_len, over every string of the field width.
 *
 * Includes the translation unit rather than a copy of the function. The
 * first version of this harness kept the body in a .inc beside it, which is
 * a second copy of TCB code whose whole purpose is to be checked -- it would
 * have gone on verifying the copy after nwcheck.c moved. hash_name is static
 * too, and the same include gives the other harnesses access to it. */
#include "nwcheck.c"

/* path_ok_len takes its field width as a parameter, and it once mattered
 * that nw_check called it at TWO -- NW_PATH_LEN for exec_path and every
 * bind, NW_BRICK_LEN for a brick. This harness ran only at NW_PATH_LEN
 * while the caller proof's stub assumed the accept-postcondition at
 * whichever width it was called with: the one place a stub assumed more
 * than its leaf proof delivered. Injecting
 * `if (max != NW_PATH_LEN) return 1;` left this proof SUCCESSFUL. Found by
 * tcb-review; bug 12's shape, which was also a length that had to match
 * the field.
 *
 * Phase 3 (2026-09-12) retired the second width: a brick is a 32-byte hash
 * and never reaches this function, so exec_path and binds are the only
 * callers and both are NW_PATH_LEN. That mutant is no longer observable
 * -- not because it was fixed, but because the width it exploited stopped
 * existing. proofs/run.sh derives the list from blob.h rather than
 * hardcoding one entry, so a second width would come back on its own. */
#ifndef PROOF_PATH_MAX
#define PROOF_PATH_MAX NW_PATH_LEN
#endif

int main(void)
{
    static char buf[PROOF_PATH_MAX];
    __CPROVER_havoc_object(buf);

    int r = path_ok_len(buf, PROOF_PATH_MAX);

#ifdef PROOF_VACUITY
    /* Control: the assumptions must not be contradictory, and the assertions
     * must be capable of failing. This one must FAIL. */
    __CPROVER_assert(!r, "VACUITY CONTROL: no path is ever accepted");
#else
    if (r) {
        __CPROVER_assert(buf[0] == '/', "accepted: absolute");
        int nul = 0;
        for (int i = 0; i < PROOF_PATH_MAX; i++) if (buf[i] == 0) nul = 1;
        __CPROVER_assert(nul, "accepted: NUL-terminated within the field");
        /* The property the `..` guard exists for. Stated as: no component
         * of an accepted path is exactly "..". Checked at every position,
         * including the s[i+3] reads that had only ever been argued safe
         * by hand -- the bounds checks below cover those. */
        for (int i = 0; i + 3 < PROOF_PATH_MAX; i++)
            __CPROVER_assert(!(buf[i] == '/' && buf[i + 1] == '.'
                               && buf[i + 2] == '.'
                               && (buf[i + 3] == '/' || buf[i + 3] == 0)),
                             "accepted: no '..' component");
    }
#endif
    return 0;
}
