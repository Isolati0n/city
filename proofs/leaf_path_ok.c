/* CBMC harness: path_ok_len, over every string of the field width.
 *
 * Includes the translation unit rather than a copy of the function. The
 * first version of this harness kept the body in a .inc beside it, which is
 * a second copy of TCB code whose whole purpose is to be checked -- it would
 * have gone on verifying the copy after nwcheck.c moved. hash_name is static
 * too, and the same include gives the other harnesses access to it. */
#include "nwcheck.c"

int main(void)
{
    static char buf[NW_PATH_LEN];
    __CPROVER_havoc_object(buf);

    int r = path_ok_len(buf, NW_PATH_LEN);

#ifdef PROOF_VACUITY
    /* Control: the assumptions must not be contradictory, and the assertions
     * must be capable of failing. This one must FAIL. */
    __CPROVER_assert(!r, "VACUITY CONTROL: no path is ever accepted");
#else
    if (r) {
        __CPROVER_assert(buf[0] == '/', "accepted: absolute");
        int nul = 0;
        for (int i = 0; i < NW_PATH_LEN; i++) if (buf[i] == 0) nul = 1;
        __CPROVER_assert(nul, "accepted: NUL-terminated within the field");
        /* The property the `..` guard exists for. Stated as: no component
         * of an accepted path is exactly "..". Checked at every position,
         * including the s[i+3] reads that had only ever been argued safe
         * by hand -- the bounds checks below cover those. */
        for (int i = 0; i + 3 < NW_PATH_LEN; i++)
            __CPROVER_assert(!(buf[i] == '/' && buf[i + 1] == '.'
                               && buf[i + 2] == '.'
                               && (buf[i + 3] == '/' || buf[i + 3] == 0)),
                             "accepted: no '..' component");
    }
#endif
    return 0;
}
