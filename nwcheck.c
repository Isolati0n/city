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
    "kind"
};

const char *nw_errstr(int e)
{
    if (e < 0 || e > NW_E_KIND) return "unknown";
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

static int path_ok(const char *s)
{
    if (s[0] != '/') return 0;
    int n = 0;
    for (; n < NW_PATH_LEN && s[n]; n++) {
        unsigned char c = (unsigned char)s[n];
        if (c < 32 || c == 127) return 0;
    }
    if (n < 2 || n >= NW_PATH_LEN) return 0;
    for (int i = n; i < NW_PATH_LEN; i++)
        if (s[i] != 0) return 0;
    return 1;
}

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
    if (h->magic[0] != 'N' || h->magic[1] != 'W' || h->magic[2] != 'P'
        || h->magic[3] != 'L' || h->magic[4] != 'A' || h->magic[5] != 'N'
        || h->magic[6] != '0' || h->magic[7] != '3')
        return NW_E_MAGIC;
    if (h->n_units < 1 || h->n_units > NW_MAX_UNITS) return NW_E_UNITS;
    uint32_t need = (uint32_t)NW_BLOB_SIZE(h->n_units);
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

    /* The runtime fd-budget check was retired on 2026-09-10. With edges gone
     * the worst case is 8 + 2*64 = 136 against a 1024 ceiling, so it could not
     * fire at any legal unit count -- dead code in a TCB file that read as a
     * live safety property. The bound is still enforced where it can actually
     * bite: the _Static_assert in blob.h at compile time, and the baker at
     * bake time. The real binding constraint on unit count is pid_max, which
     * is not a descriptor property and is not modelled here. */

    return NW_OK;
}
