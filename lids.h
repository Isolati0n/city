#ifndef NW_LIDS_H
#define NW_LIDS_H

/* The house seccomp allow-list. One table, in lids.c.
 *
 * Both supervisor spellings apply the filter through this entry point: the
 * Rust nwsup.rs via FFI, the C twin nwsup.c by linking lids.o. The C twin
 * previously carried a verbatim second copy of the 33-entry table; the two
 * agreed, but nothing kept them in step. Declaring it here means a signature
 * change cannot pass the compiler unnoticed.
 *
 * Returns 0 on success, -1 if PR_SET_NO_NEW_PRIVS or PR_SET_SECCOMP failed.
 * The caller decides what a failure means; this does not exit.
 */
int nw_apply_house_seccomp(void);

#endif
