#ifndef NW_BLOB_H
#define NW_BLOB_H

#include <stdint.h>

#define NW_MAGIC        "NWPLAN05"
#define NW_NAME_LEN     32
#define NW_PATH_LEN     128
#define NW_BRICK_LEN    96    /* "/nw/bricks/" + 64 hex + NUL */
#define NW_MAX_BINDS    128
#define NW_MAX_UNITS    64
#define NW_FD_RESERVED  8
#define NW_MAX_FDS      1024

_Static_assert(NW_MAX_UNITS * 2 + NW_FD_RESERVED <= NW_MAX_FDS,
               "derived fd budget");

/* How far nwspawn.c's close_others must sweep, and how many descriptors it
 * must be able to hold while doing it. Derived here, beside the budget it
 * follows from, because that is where a reader looks for a limit: the sweep
 * has to cover every descriptor the budget above permits, or it silently
 * leaves some open. It was a bare 512 in three places in nwspawn.c until
 * 2026-09-10 -- a fifth undeclared fd limit that first bit at roughly 254
 * units, inside the range the budget allows. Limits are derived, never
 * declared twice (invariant 3). */
#define NW_FD_SWEEP     NW_MAX_FDS
_Static_assert(NW_MAX_UNITS * 2 + NW_FD_RESERVED <= NW_FD_SWEEP,
               "sweep must cover the whole legal descriptor range");

/* Slots in nwcheck.c's duplicate-name table. Not a plan-format limit -- no
 * blob and no spec mentions it -- but it is a second number that must stay
 * larger than NW_MAX_UNITS, so it lives here beside the first one and the
 * assert holds the relation.
 *
 * name_dup returns 0 from a full table: it inserts nothing and reports no
 * duplicate. That is correct only while the table cannot fill. It was five
 * bare 128s inside nwcheck.c until 2026-09-11, with the relation stated in a
 * comment and enforced by nothing: raising NW_MAX_UNITS to 256 compiled with
 * no warning, every static assert passing, and a 130-unit blob whose units
 * 128 and 129 share a name validated NW_OK -- accepted by the TCB and
 * rejected by the baker, at the one scale nothing in the suite boots.
 * Invariant 3, found by fd-auditor on af03922. Power of two because the
 * probe masks. */
#define NW_DUP_SLOTS    128
_Static_assert(NW_MAX_UNITS < NW_DUP_SLOTS,
               "duplicate-name table must never fill: see name_dup");
_Static_assert((NW_DUP_SLOTS & (NW_DUP_SLOTS - 1)) == 0,
               "duplicate-name table size must be a power of two");

/* There is one seccomp filter and a house does not choose. A second profile
 * (NW_PROF_BUILD, for a toolchain) existed briefly on 2026-09-10 and was
 * removed the same day: its allow-list was written from a table rather than
 * from running a compiler, and it killed gcc on the first exec. See
 * HISTORY.md section 23. When the toolchain house needs one it comes back
 * test-first, with a test that actually compiles something under it. */

/* Whether a clean exit means "done" or "unexpected". Explicit in the plan:
 * there is no default and no inference. Exit 0 used to mean do-not-restart
 * unconditionally, which collided with a reserved fault code meaning
 * do-not-restart-because-something-is-wrong. One channel, two opposite
 * meanings, separated only by which integer — the shape of bug 9. */
#define NW_KIND_ONESHOT  0u   /* exit 0 completes; never restarted */
#define NW_KIND_LONGRUN  1u   /* any exit is unexpected, incl. 0 */

#define NW_LID_SECCOMP   0x01u
#define NW_LID_LANDLOCK  0x02u
#define NW_LID_NEWNS     0x04u
#define NW_LID_NEWNET    0x08u

struct nw_unit {
    char     name[NW_NAME_LEN];
    char     exec_path[NW_PATH_LEN];   /* resolved inside the brick, if any */
    char     brick[NW_BRICK_LEN];      /* "" = no brick: shares the machine root */
    uint8_t  kind;       /* NW_KIND_* — was 'critical' until 2026-09-10 */
    uint8_t  budget;
    uint16_t window_s;
    uint8_t  lids;
    uint8_t  _pad;
} __attribute__((packed));

/* A path made visible inside a house's brick before it pivots. Bind mounts of
 * paths, not descriptors handed over: the init provisions nothing and the
 * house opens what it needs itself (invariant 5). The path is the same inside
 * and out, so a house uses the name it would have used anyway.
 *
 * The mount point must already exist inside the brick. nw-sup will not create
 * it: a brick is sealed and content-addressed, and mkdir'ing into one to make
 * room for a mount would break the seal to save a bake-time decision. */
struct nw_bind {
    uint16_t unit;
    char     path[NW_PATH_LEN];
} __attribute__((packed));

struct nw_hdr {
    char     magic[8];
    uint32_t n_units;
    uint32_t n_binds;
    uint32_t crc32;
} __attribute__((packed));

#define NW_BLOB_SIZE(nu, nb) \
    (sizeof(struct nw_hdr) + (nu) * sizeof(struct nw_unit) \
                           + (nb) * sizeof(struct nw_bind))

/* The largest a legal blob can be. Every reader of a blob sizes its buffer
 * and its size check from this, so there is nothing to keep in sync: raising
 * NW_MAX_UNITS resizes all of them.
 *
 * It was five hand-written numbers in three TCB files until 2026-09-11 --
 * 1<<16 in pid1.c twice and nwspawn.c once, 1<<20 in nwcheck_main.c twice --
 * none of them related to what the format permits, and two of them
 * disagreeing by a factor of sixteen. fd-auditor measured the input where
 * that bites: at NW_MAX_UNITS = 187 with a full bind table, a plan nw-check
 * accepts makes PID 1 print `plan size` and halt. Every failure was loud, so
 * this is the same class as NW_DUP_SLOTS caught earlier rather than a bug
 * that shipped -- and the class is what invariant 3 is about. */
#define NW_BLOB_MAX ((uint32_t)NW_BLOB_SIZE(NW_MAX_UNITS, NW_MAX_BINDS))

enum {
    NW_OK = 0,
    NW_E_MAGIC = 1,
    NW_E_UNITS = 2,
    NW_E_SIZE = 3,
    NW_E_CRC = 4,
    NW_E_NAME = 5,
    NW_E_DUPNAME = 6,
    NW_E_PATH = 7,
    NW_E_RSV = 8,
    NW_E_LIDS = 9,
    NW_E_KIND = 10,
    NW_E_BRICK = 11,
    NW_E_BRICKNS = 12,
    NW_E_BINDS = 13,
    NW_E_BINDIDX = 14,
    NW_E_BINDPATH = 15,
    NW_E_LLBRICK = 16,
    /* Terminator, not a code. nw_errstr's bound and the length of errs[] in
     * nwcheck.c are both derived from it, so the three things that must
     * agree -- last code, array length, bound -- become one number.
     *
     * They were three separate declarations held together by a sentence in
     * .claude/rules/plan.md until 2026-09-11. Adding a code and updating the
     * bound while forgetting the string built clean under -Wall -Wextra
     * -Werror and segfaulted in nw_errstr, which pid1.c calls at boot on the
     * value nw_check returned. Found by fd-auditor.
     *
     * It must be the terminator and not NW_E_LLBRICK + 1: anchoring on the
     * last code makes the anchor move with the thing it is meant to pin, and
     * that version compiles clean against a drifted enum. Also measured. */
    NW_E__COUNT
};

const char *nw_errstr(int e);
/* CRC over two regions, which is the shape nw_check needs: the header with
 * its crc field zeroed, followed by the body. There is no one-region
 * convenience wrapper -- nw_crc32(data, len) existed, exported, called by
 * nothing, for the whole life of this file, and survived the 2026-09-10
 * extraction as a delegating one-liner that still had no caller. Pass NULL
 * and 0 for the second region. */
uint32_t nw_crc32_split(const void *a, uint32_t na, const void *b, uint32_t nb);
int nw_check(const void *blob, uint32_t len);

static inline const struct nw_hdr *nw_hdr(const void *blob)
{
    return (const struct nw_hdr *)blob;
}

static inline const struct nw_unit *nw_units(const void *blob)
{
    return (const struct nw_unit *)((const char *)blob + sizeof(struct nw_hdr));
}

static inline const struct nw_bind *nw_binds(const void *blob)
{
    const struct nw_hdr *h = nw_hdr(blob);
    return (const struct nw_bind *)((const char *)blob + sizeof(struct nw_hdr)
                                    + h->n_units * sizeof(struct nw_unit));
}

#endif
