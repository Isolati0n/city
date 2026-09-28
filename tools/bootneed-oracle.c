/* tools/bootneed-oracle.c -- ground truth for the boot-need arithmetic.
 * Was tools/fdneed-oracle.c, renamed alongside blob.h's NW_FD_NEED ->
 * NW_BOOT_NEED rename (docs/OPERATOR-BRIEF.md Section 1.3).
 *
 * Prints NW_BOOT_NEED(n, e), compiled directly against blob.h's own
 * macro. Nothing here retypes the formula: this file exists precisely
 * because every other consumer of the boot-need arithmetic -- the baker,
 * plan.als's fdNeed, Plan.tla's FdNeed -- had its own hand-typed copy
 * of the coefficients, and plan.md records at length that nothing ever
 * compared those copies against blob.h itself ("a * 3 in the header
 * still runs clean"). tools/gen-spec-limits.py compiles and runs this
 * program at a few concrete (units, edges) points and writes the
 * answers into the generated spec files, so plan.als's
 * FdNeedOracleAgrees and Plan.tla's FdNeedOracleAgrees can each check
 * their own formula against a value that came from blob.h's macro,
 * not from retyping it a fourth and fifth time.
 *
 * Usage: bootneed-oracle N E
 *
 * Not in the TCB: a standalone reader of a macro, nothing more.
 */
#include "blob.h"

#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: bootneed-oracle N E\n");
        return 2;
    }
    long n = atol(argv[1]);
    long e = atol(argv[2]);
    printf("%ld\n", (long)NW_BOOT_NEED(n, e));
    return 0;
}
