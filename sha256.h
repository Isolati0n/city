#ifndef NW_SHA256_H
#define NW_SHA256_H

/* One function, no other primitives. Vendored per docs/options/12-
 * crash-evidence.md's decision: evidence packages are named by the
 * sha256 of their own content, matching NW_BRICK_HASH's existing
 * convention, and nothing in this tree had a C sha256 before this --
 * bricks are hashed only by the non-TCB Python baker. Correctness is
 * checked against NIST's own FIPS 180-4 test vectors in the suite,
 * independent of anything else this file's caller does. */

#include <stddef.h>

void nw_sha256(const void *data, size_t len, unsigned char out[32]);

#endif
