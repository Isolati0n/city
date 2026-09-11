#include "blob.h"

#include <stddef.h>
#include <stdint.h>

static const char *errs[] = {
    "ok",
    "bad magic",
    "unit count",
    "size",
    "crc32",
    "name",
    "duplicate name",
    "exec_path",
    "reserved byte nonzero",
    "lids",
    "kind",
    "brick path",
    "brick without NEWNS lid",
    "bind count",
    "bind unit index",
    "bind path",
    "landlock without brick"
};

_Static_assert(sizeof errs / sizeof errs[0] == NW_E__COUNT,
               "one errs[] string per NW_E_* code");

const char *nw_errstr(int e)
{
    if (e < 0 || e >= NW_E__COUNT) return "unknown";
    return errs[e];
}

/* CRC32 over two regions, because nw_check needs the header with its own
 * crc field zeroed followed by the body, and that is not one buffer.
 *
 * This is the ONLY implementation. nw_check carried a second, inlined copy
 * of the same loop until 2026-09-10 while a one-region nw_crc32 sat exported
 * and called by nothing -- two copies of an algorithm in a TCB file, and one
 * dead export beside them, which is what
 * invariant 3 exists to prevent, found by measuring coverage rather than by
 * reading. Extracting it also makes nw_check model-checkable: unrolling
 * ~2,100 symbolic iterations of this loop is what stopped CBMC dead, and a
 * call can be abstracted where an inlined loop cannot. docs/plans/02.
 *
 * b may be NULL when nb is 0; the loop then does not run. */
uint32_t nw_crc32_split(const void *a, uint32_t na, const void *b, uint32_t nb)
{
    const unsigned char *p = a;
    uint32_t c = 0xffffffffu;
    for (uint32_t i = 0; i < na; i++) {
        c ^= p[i];
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xedb88320u & (uint32_t)-(int)(c & 1u));
    }
    p = b;
    for (uint32_t i = 0; i < nb; i++) {
        c ^= p[i];
        for (int k = 0; k < 8; k++)
            c = (c >> 1) ^ (0xedb88320u & (uint32_t)-(int)(c & 1u));
    }
    return c ^ 0xffffffffu;
}

static int name_ok(const char *s, int max)
{
    int n = 0;
    if (!s[0]) return 0;
    for (; n < max && s[n]; n++) {
        char c = s[n];
        int ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
              || (c >= '0' && c <= '9') || c == '_' || c == '-';
        if (!ok) return 0;
    }
    if (n == 0 || n >= max) return 0;
    for (int i = n; i < max; i++)
        if (s[i] != 0) return 0;
    return 1;
}

static int path_ok_len(const char *s, int max)
{
    if (s[0] != '/') return 0;
    int n = 0;
    for (; n < max && s[n]; n++) {
        unsigned char c = (unsigned char)s[n];
        if (c < 32 || c == 127) return 0;
    }
    if (n < 2 || n >= max) return 0;
    for (int i = n; i < max; i++)
        if (s[i] != 0) return 0;
    /* No ".." component. A path is a string whose meaning is assigned by a
     * filesystem this process does not control, and nw-sup hands these
     * strings straight to mount(2) and open(2) -- so a traversal here is a
     * house rooted outside its brick, reported as success. Checked at the
     * one site every path in a plan passes through: exec_path, brick and
     * every bind.
     *
     * This closes traversal and does NOT close symlinks: a brick whose name
     * resolves through a link escapes just as cleanly, and both mount and
     * pivot_root follow links. It is a guard, not the fix. The fix is to
     * stop carrying free-form paths -- docs/options/07. */
    for (int i = 0; i < n; i++) {
        if (s[i] != '/') continue;
        if (s[i + 1] == '.' && s[i + 2] == '.'
            && (s[i + 3] == '/' || s[i + 3] == 0))
            return 0;
    }
    return 1;
}

static int path_ok(const char *s) { return path_ok_len(s, NW_PATH_LEN); }

static uint32_t hash_name(const char *s)
{
    uint32_t h = 2166136261u;
    for (int i = 0; i < NW_NAME_LEN && s[i]; i++) {
        h ^= (unsigned char)s[i];
        h *= 16777619u;
    }
    return h;
}

/* Has u[i].name already appeared among u[0..i-1]?
 *
 * Open-addressed, NW_DUP_SLOTS slots, carried across calls in slot[]. It
 * replaced a nested scan that took 15.26 s at 64k units; do not reintroduce
 * one.
 *
 * Extracted from nw_check on 2026-09-10 for the same two reasons as the CRC
 * above. It is the single largest obstruction to model-checking the caller
 * -- CBMC unwound this loop 10,265 times against 320 for the next worst --
 * and until 2026-09-11 it was code no test had ever reached: NW_E_DUPNAME
 * had never been produced by nw-check, because the only duplicate-name test
 * rejected at the baker. test_dupname_refused reaches it now, and
 * proofs/leaf_name_dup.c proves it. Past tense on purpose -- this sentence
 * was left in the present by the same change that falsified it, which is
 * this file's characteristic failure inside a comment about it.
 * Hardest to verify and least exercised were the same property here:
 * the interesting path needs a hash collision, which a fuzzer will not
 * stumble into and a solver cannot bound cheaply. As a function it can be
 * proven and tested on its own.
 *
 * Returns 1 for a duplicate, 0 otherwise. A full table returns 0 -- the
 * behaviour the inline version had, preserved deliberately: silently
 * changing it here would be a second meaning for a full table. It is sound
 * only because the table cannot fill, and that is now the _Static_assert in
 * blob.h rather than this sentence. It was this sentence alone until
 * 2026-09-11, and the sentence was not load-bearing enough: see NW_DUP_SLOTS.
 *
 * The table is a struct so the caller has no size to write. It was
 * `int slot[static NW_DUP_SLOTS]` for half a day, which makes a mismatch a
 * -Wstringop-overflow -- but a warning in a build with no -Werror, and the
 * caller still spelled NW_DUP_SLOTS twice, once for the array and once for
 * the init loop. Prefer designing the problem out over checking for it:
 * with a type there is no number at the call site to get wrong. */
struct nw_dup_tab { int slot[NW_DUP_SLOTS]; };

static void name_dup_init(struct nw_dup_tab *t)
{
    for (int i = 0; i < NW_DUP_SLOTS; i++) t->slot[i] = -1;
}

static int name_dup(struct nw_dup_tab *t, const struct nw_unit *u, uint32_t i)
{
    int *slot = t->slot;
    uint32_t hv = hash_name(u[i].name);
    int s = (int)(hv & (uint32_t)(NW_DUP_SLOTS - 1));
    for (int p = 0; p < NW_DUP_SLOTS; p++) {
        int k = (s + p) & (NW_DUP_SLOTS - 1);
        if (slot[k] < 0) { slot[k] = (int)i; return 0; }
        const char *a = u[slot[k]].name;
        const char *b = u[i].name;
        int same = 1;
        for (int n = 0; n < NW_NAME_LEN; n++) {
            if (a[n] != b[n]) { same = 0; break; }
            if (!a[n]) break;
        }
        if (same) return 1;
    }
    return 0;
}

int nw_check(const void *blob, uint32_t len)
{
    if (len < sizeof(struct nw_hdr)) return NW_E_SIZE;
    const struct nw_hdr *h = nw_hdr(blob);
    /* Compare against NW_MAGIC itself. These were eight byte literals until
     * 2026-09-10, so NW_MAGIC was defined and used nowhere: changing the
     * constant changed nothing, and a format bump could have moved the
     * definition while leaving the check behind. The _Static_assert pins the
     * width so the loop and the constant cannot disagree either. */
    {
        static const char want[] = NW_MAGIC;
        _Static_assert(sizeof want == sizeof h->magic + 1, "magic width");
        for (size_t i = 0; i < sizeof h->magic; i++)
            if (h->magic[i] != want[i]) return NW_E_MAGIC;
    }
    if (h->n_units < 1 || h->n_units > NW_MAX_UNITS) return NW_E_UNITS;
    if (h->n_binds > NW_MAX_BINDS) return NW_E_BINDS;
    uint32_t need = (uint32_t)NW_BLOB_SIZE(h->n_units, h->n_binds);
    if (len != need) return NW_E_SIZE;

    unsigned char tmp_hdr[sizeof(struct nw_hdr)];
    const unsigned char *raw = blob;
    for (size_t i = 0; i < sizeof(struct nw_hdr); i++) tmp_hdr[i] = raw[i];
    tmp_hdr[offsetof(struct nw_hdr, crc32) + 0] = 0;
    tmp_hdr[offsetof(struct nw_hdr, crc32) + 1] = 0;
    tmp_hdr[offsetof(struct nw_hdr, crc32) + 2] = 0;
    tmp_hdr[offsetof(struct nw_hdr, crc32) + 3] = 0;
    uint32_t c = nw_crc32_split(tmp_hdr, (uint32_t)sizeof(struct nw_hdr),
                                (const unsigned char *)blob
                                    + sizeof(struct nw_hdr),
                                len - (uint32_t)sizeof(struct nw_hdr));
    if (c != h->crc32) return NW_E_CRC;

    const struct nw_unit *u = nw_units(blob);

    struct nw_dup_tab dup;
    name_dup_init(&dup);

    for (uint32_t i = 0; i < h->n_units; i++) {
        if (!name_ok(u[i].name, NW_NAME_LEN)) return NW_E_NAME;
        if (!path_ok(u[i].exec_path)) return NW_E_PATH;
        if (u[i].kind != NW_KIND_ONESHOT && u[i].kind != NW_KIND_LONGRUN)
            return NW_E_KIND;
        /* brick is optional; when present it must be a well-formed absolute
         * path, and it forces NEWNS -- a house cannot pivot into its own root
         * without a private mount namespace, and nw-sup must not quietly
         * supply the lid the plan failed to declare. */
        /* Landlock grants read and execute beneath the house's root. That is
         * a restriction only when the root is a brick; on the machine root it
         * confines nothing, which is a lid that decides nothing while
         * claiming to. See the decision in nwsup.c's lid_landlock. */
        if ((u[i].lids & NW_LID_LANDLOCK) && !u[i].brick[0])
            return NW_E_LLBRICK;
        if (u[i].brick[0]) {
            if (!path_ok_len(u[i].brick, NW_BRICK_LEN)) return NW_E_BRICK;
            if (!(u[i].lids & NW_LID_NEWNS)) return NW_E_BRICKNS;
        } else {
            for (int k = 0; k < NW_BRICK_LEN; k++)
                if (u[i].brick[k] != 0) return NW_E_BRICK;
        }
        /* The remaining spare byte must be zero. An unvalidated spare cannot
         * be given meaning later: an old blob carrying garbage would be
         * accepted by a new checker that reads it. */
        if (u[i]._pad != 0) return NW_E_RSV;
        if (u[i].lids & ~(uint8_t)(NW_LID_SECCOMP | NW_LID_LANDLOCK
                                   | NW_LID_NEWNS | NW_LID_NEWNET))
            return NW_E_LIDS;
        if (name_dup(&dup, u, i)) return NW_E_DUPNAME;
    }

    const struct nw_bind *b = nw_binds(blob);
    for (uint32_t i = 0; i < h->n_binds; i++) {
        if (b[i].unit >= h->n_units) return NW_E_BINDIDX;
        if (!path_ok(b[i].path)) return NW_E_BINDPATH;
        /* A bind only means anything for a house that has its own root. */
        if (!u[b[i].unit].brick[0]) return NW_E_BINDIDX;
    }

    /* The runtime fd-budget check was retired on 2026-09-10. With edges gone
     * the worst case is 8 + 2*64 = 136 against a 1024 ceiling, so it could not
     * fire at any legal unit count -- dead code in a TCB file that read as a
     * live safety property. The bound is still enforced where it can actually
     * bite: the _Static_assert in blob.h at compile time, and the baker at
     * bake time. The real binding constraint on unit count is pid_max, which
     * is not a descriptor property and is not modelled here. */

    return NW_OK;
}
