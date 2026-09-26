#ifndef NW_STORE_H
#define NW_STORE_H

/* One function: write a buffer once, content-addressed, deduping by
 * hash. docs/options/14-shared-store.md.
 *
 * mkbrick.py (bake-side) and write_evidence() in nwsup.c (live-side, in
 * the TCB) each already hashed their own content, named the result by
 * that hash, and arrived at the final name with a temp-file-then-rename
 * so a half-written file never occupies a content-addressed path. Two
 * independent implementations of one pattern -- imitation, not shared
 * code. This is the live-side half of that pattern, lifted out once so
 * a third producer does not reinvent it a third way. The bake-side half
 * stays in Python (mkbrick.py) because nothing here can be called from
 * there; the pattern is documented in the design note so a future
 * bake-side artifact type can follow the same shape deliberately.
 *
 * No policy: no cleanup, no eviction, no preference between artifact
 * types. It answers exactly one question -- does <dir>/<hex(sha256 of
 * data)><suffix> already hold THIS CONTENT, verified byte-for-byte, not
 * merely a name that matches -- and if not, writes it. A file already
 * at that name whose bytes differ (planted by anything else with write
 * access to `dir`, which for at least one real caller is not a
 * trusted-only set -- see docs/options/14-shared-store.md's review
 * findings) is treated exactly like nothing was there and is
 * overwritten with the real content. That is a mechanical existence-
 * and-content check, not a decision.
 *
 * out_hex is always filled in, even on failure, so a caller can still
 * name what it was trying to write in a diagnostic. Returns:
 *   -1  hard failure (dir unusable, write failed, rename failed).
 *    0  already present at that hash; nothing was written.
 *    1  this call wrote it.
 * A caller that only cares whether the content is now on disk can
 * treat any non-negative return as success. */

#include <stddef.h>

int nw_store_put(const char *dir, const void *data, size_t len,
                  const char *suffix, char out_hex[65]);

#endif
