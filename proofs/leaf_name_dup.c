/* CBMC harness: name_dup, the duplicate-name table.
 *
 * This is the function nothing had ever run. NW_E_DUPNAME was unreachable
 * from the suite until 2026-09-11 (the only duplicate test rejected at the
 * baker), and the interesting path -- a duplicate found by probing past an
 * occupied, non-matching slot -- needs a hash collision, which a fuzzer will
 * not stumble into. Hardest to reach and least exercised were the same
 * property here, which is the case for proving it rather than sampling it.
 *
 * The property is the one nw_check relies on and nothing else:
 *
 *     running name_dup over units 0..N-1 in order, against a table that
 *     started empty, reports a duplicate exactly when two of those units
 *     share a name.
 *
 * Both directions matter. "Reports when there is one" is the check; "does
 * not report when there is none" is what stops a checker that answers
 * NW_E_DUPNAME to everything from satisfying it -- the same pairing the
 * suite's test needed, and the same one it was missing on the first draft.
 *
 * ASSUMPTION, and why it is sound: every name is non-empty and NUL-padded
 * to the field width. nw_check calls name_ok on unit i before name_dup, in
 * the same iteration, and name_ok rejects anything else -- proven in
 * leaf_name_ok.c, which asserts exactly this pair of post-conditions so the
 * two runs compose. Without the padding, "equal up to the terminator" (what
 * name_dup compares) and "equal over the field" (what fields_equal below
 * compares) are different questions and the equivalence is false, correctly.
 *
 * BOUNDS: the probe chain is bounded to PROOF_UNITS + 2 iterations, but
 * --unwinding-assertions is on, so that bound is *proven sufficient* for
 * these inputs rather than assumed. It is not a narrowing of the input
 * space: every name is still free across all 2^(8*32) values. What is
 * bounded is N, the number of units -- see proofs/README.md.
 *
 * N MATTERS MORE THAN IT LOOKS, and the control is how that was found. At
 * PROOF_UNITS=2 the probe chain is never needed: equal names have equal
 * hashes, so a duplicate's home slot is exactly where its match sits, and
 * truncating the chain to a single slot leaves this proof SUCCESSFUL.
 * Probing is only required when a *different* name got there first, which
 * takes three units -- two that collide and a third duplicating the
 * displaced one. A control that passes is not good news, and it said the
 * same thing here as it did about the first draft of the suite's test. */
#include "nwcheck.c"

#ifndef PROOF_UNITS
#define PROOF_UNITS 2
#endif

static int fields_equal(const char *a, const char *b)
{
    for (int k = 0; k < NW_NAME_LEN; k++) if (a[k] != b[k]) return 0;
    return 1;
}

int main(void)
{
    static struct nw_unit u[PROOF_UNITS];
    __CPROVER_havoc_object(u);

    for (int i = 0; i < PROOF_UNITS; i++) {
        __CPROVER_assume(u[i].name[0] != 0);
        __CPROVER_assume(u[i].name[NW_NAME_LEN - 1] == 0);
        for (int k = 1; k < NW_NAME_LEN; k++)
            __CPROVER_assume(u[i].name[k - 1] != 0 || u[i].name[k] == 0);
    }

    /* Through the type, and initialised the way nw_check initialises it.
     * This passed a bare int* until 2026-09-11 -- an implicit pointer
     * conversion CBMC accepts silently, so the proof ran against a
     * signature that no longer existed. Found by fd-auditor. */
    struct nw_dup_tab t;
    name_dup_init(&t);

    /* Exactly what nw_check does: scan in order, stop at the first report. */
    int reported = 0;
    for (uint32_t i = 0; i < PROOF_UNITS; i++)
        if (name_dup(&t, u, i)) { reported = 1; break; }

    int exists = 0;
    for (int a = 0; a < PROOF_UNITS; a++)
        for (int b = a + 1; b < PROOF_UNITS; b++)
            if (fields_equal(u[a].name, u[b].name)) exists = 1;

#ifdef PROOF_VACUITY
    __CPROVER_assert(!exists, "VACUITY CONTROL: no two names are ever equal");
#else
    __CPROVER_assert(reported == exists,
                     "a duplicate is reported exactly when one exists");
#endif
    return 0;
}
