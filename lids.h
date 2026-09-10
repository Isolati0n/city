#ifndef NW_LIDS_H
#define NW_LIDS_H

/* The house seccomp allow-list. One table, in lids.c.
 *
 * nwsup.c applies the filter through this entry point by linking lids.o. It
 * previously carried a verbatim second copy of the table; the two agreed,
 * but nothing kept them in step. Declaring it here means a signature change
 * cannot pass the compiler unnoticed.
 *
 * One filter, no profiles. A house does not choose its allow-list. See
 * HISTORY.md section 23 for the build profile that was removed on the day it
 * was written.
 *
 * Returns 0 on success, -1 if PR_SET_NO_NEW_PRIVS or PR_SET_SECCOMP failed.
 * The caller decides what a failure means; this does not exit.
 *
 * One declaration, one definition. The Rust twin that once called this over
 * FFI (nwsup.rs) was deleted on 2026-09-10 -- it was never built by the
 * Makefile, had no restart loop, and silently ignored NW_LID_LANDLOCK. If a
 * non-C caller is ever reintroduced it declares this signature independently
 * and the compiler cannot check it, so change both in the same commit.
 */
int nw_apply_house_seccomp(void);

#endif
