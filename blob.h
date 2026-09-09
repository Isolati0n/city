#ifndef NW_BLOB_H
#define NW_BLOB_H

#include <stdint.h>

#define NW_MAGIC        "NWPLAN02"
#define NW_NAME_LEN     32
#define NW_PATH_LEN     128
#define NW_MAX_UNITS    64
#define NW_MAX_EDGES    128
#define NW_FD_RESERVED  8
#define NW_MAX_FDS      1024

_Static_assert(NW_MAX_UNITS * 2 + NW_MAX_EDGES * 2 + NW_FD_RESERVED <= NW_MAX_FDS,
               "derived fd budget");

#define NW_LID_SECCOMP   0x01u
#define NW_LID_LANDLOCK  0x02u
#define NW_LID_NEWNS     0x04u
#define NW_LID_NEWNET    0x08u

struct nw_unit {
    char     name[NW_NAME_LEN];
    char     exec_path[NW_PATH_LEN];
    uint8_t  critical;
    uint8_t  budget;
    uint16_t window_s;
    uint8_t  lids;
    uint8_t  _pad;
} __attribute__((packed));

struct nw_edge {
    uint16_t a;
    uint16_t b;
} __attribute__((packed));

struct nw_hdr {
    char     magic[8];
    uint32_t n_units;
    uint32_t n_edges;
    uint32_t crc32;
} __attribute__((packed));

#define NW_BLOB_SIZE(nu, ne) \
    (sizeof(struct nw_hdr) + (nu) * sizeof(struct nw_unit) + (ne) * sizeof(struct nw_edge))

enum {
    NW_OK = 0,
    NW_E_MAGIC = 1,
    NW_E_UNITS = 2,
    NW_E_EDGES = 3,
    NW_E_SIZE = 4,
    NW_E_CRC = 5,
    NW_E_NAME = 6,
    NW_E_DUPNAME = 7,
    NW_E_PATH = 8,
    NW_E_CRIT = 9,
    NW_E_EIDX = 10,
    NW_E_SELF = 11,
    NW_E_DUPEDGE = 12,
    NW_E_FDBUDGET = 13,
    NW_E_EMPTY = 14,
    NW_E_LIDS = 15
};

const char *nw_errstr(int e);
uint32_t nw_crc32(const void *data, uint32_t len);
int nw_check(const void *blob, uint32_t len);

static inline const struct nw_hdr *nw_hdr(const void *blob)
{
    return (const struct nw_hdr *)blob;
}

static inline const struct nw_unit *nw_units(const void *blob)
{
    return (const struct nw_unit *)((const char *)blob + sizeof(struct nw_hdr));
}

static inline const struct nw_edge *nw_edges(const void *blob)
{
    const struct nw_hdr *h = nw_hdr(blob);
    return (const struct nw_edge *)((const char *)blob + sizeof(struct nw_hdr)
                                    + h->n_units * sizeof(struct nw_unit));
}

#endif
