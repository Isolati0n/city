/* CBMC harness: name_ok, over every string of the field width. */
#include "nwcheck.c"

int main(void)
{
    static char buf[NW_NAME_LEN];
    __CPROVER_havoc_object(buf);

    int r = name_ok(buf, NW_NAME_LEN);

#ifdef PROOF_VACUITY
    __CPROVER_assert(!r, "VACUITY CONTROL: no name is ever accepted");
#else
    if (r) {
        __CPROVER_assert(buf[0] != 0, "accepted: non-empty");
        __CPROVER_assert(buf[NW_NAME_LEN - 1] == 0,
                         "accepted: NUL-terminated within the field");
        /* The property name_dup's proof assumes about its input, and the
         * reason the two proofs compose: every byte from the terminator on
         * is zero, so comparing the whole field and comparing up to the
         * terminator are the same question. */
        for (int i = 0; i < NW_NAME_LEN; i++)
            for (int j = i + 1; j < NW_NAME_LEN; j++)
                __CPROVER_assert(buf[i] != 0 || buf[j] == 0,
                                 "accepted: NUL-padded to the field width");
        for (int i = 0; i < NW_NAME_LEN; i++) {
            char c = buf[i];
            __CPROVER_assert(c == 0
                             || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
                             || (c >= '0' && c <= '9') || c == '_' || c == '-',
                             "accepted: every byte is from the name alphabet");
        }
    }
#endif
    return 0;
}
