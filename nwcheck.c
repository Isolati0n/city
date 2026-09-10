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
    "bind path"
};

const char *nw_errstr(int e)
{
    if (e < 0 || e > NW_E_BINDPATH) return "unknown";
    return errs[e];
}

uint32_t nw_crc32(const void *data, uint32_t len)
{
    const unsigned char *p = data;
    uint32_t c = 0xffffffffu;
    for (uint32_t i = 0; i < len; i++) {
        c ^= p[i];
        for (int b = 0; b < 8; b++)
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
    const char *after = (const char *)blob + sizeof(struct nw_hdr);
    uint32_t rest = len - (uint32_t)sizeof(struct nw_hdr);
    uint32_t c = 0xffffffffu;
    for (size_t i = 0; i < sizeof(struct nw_hdr); i++) {
        c ^= tmp_hdr[i];
        for (int b = 0; b < 8; b++)
            c = (c >> 1) ^ (0xedb88320u & (uint32_t)-(int)(c & 1u));
    }
    for (uint32_t i = 0; i < rest; i++) {
        c ^= (unsigned char)after[i];
        for (int b = 0; b < 8; b++)
            c = (c >> 1) ^ (0xedb88320u & (uint32_t)-(int)(c & 1u));
    }
    c ^= 0xffffffffu;
    if (c != h->crc32) return NW_E_CRC;

    const struct nw_unit *u = nw_units(blob);

    int slot[128];
    for (int i = 0; i < 128; i++) slot[i] = -1;

    for (uint32_t i = 0; i < h->n_units; i++) {
        if (!name_ok(u[i].name, NW_NAME_LEN)) return NW_E_NAME;
        if (!path_ok(u[i].exec_path)) return NW_E_PATH;
        if (u[i].kind != NW_KIND_ONESHOT && u[i].kind != NW_KIND_LONGRUN)
            return NW_E_KIND;
        /* brick is optional; when present it must be a well-formed absolute
         * path, and it forces NEWNS -- a house cannot pivot into its own root
         * without a private mount namespace, and nw-sup must not quietly
         * supply the lid the plan failed to declare. */
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
        uint32_t hv = hash_name(u[i].name);
        int s = (int)(hv & 127u);
        for (int p = 0; p < 128; p++) {
            int k = (s + p) & 127;
            if (slot[k] < 0) { slot[k] = (int)i; break; }
            const char *a = u[slot[k]].name;
            const char *b = u[i].name;
            int same = 1;
            for (int n = 0; n < NW_NAME_LEN; n++) {
                if (a[n] != b[n]) { same = 0; break; }
                if (!a[n]) break;
            }
            if (same) return NW_E_DUPNAME;
        }
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
