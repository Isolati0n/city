/* tools/fdneed-oracle.c -- ground truth for the fd-need arithmetic.
 *
 * Prints NW_FD_NEED(nu, ne), compiled directly against blob.h's own
 * macro. Nothing here retypes the formula: this file exists precisely
 * because every other consumer of the fd-need arithmetic -- the baker,
 * plan.als's fdNeed, Plan.tla's FdNeed -- had its own hand-typed copy
 * of the multiplier, and plan.md records at length that nothing ever
 * compared those copies against blob.h itself ("a * 3 in the header
 * still runs clean"). tools/gen-spec-limits.py compiles and runs this
 * program at a few concrete (units, edges) points and writes the
 * answers into the generated spec files, so plan.als's
 * FdNeedOracleAgrees and Plan.tla's FdNeedOracleAgrees can each check
 * their own formula against a value that came from blob.h's macro,
 * not from retyping it a fourth and fifth time.
 *
 * Usage: fdneed-oracle NU NE
 *
 * Not in the TCB: a standalone reader of a macro, nothing more.
 */
#include "blob.h"

#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: fdneed-oracle NU NE\n");
        return 2;
    }
    long nu = atol(argv[1]);
    long ne = atol(argv[2]);
    printf("%ld\n", (long)NW_FD_NEED(nu, ne));
    return 0;
}
