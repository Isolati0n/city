/* Read one unit's fields out of a sealed plan blob, for the
 * crash-and-relaunch tool (docs/options/13-crash-relaunch.md). NOT
 * TCB: no boot-time caller links this in, the same classification
 * tools/initrd-init.c already has despite also being C (CLAUDE.md's
 * TCB table names the boot-chain files specifically, not "anything in
 * C"). Read-only and decides nothing -- it prints fields, an operator
 * tool reads them.
 *
 * The reason this exists at all: tools/stage-layers.py's own docstring
 * states the rule it is bound by -- "this tool does not parse the
 * blob: a third copy of the unit layout... is the drift class
 * invariant 3 is about." This tool obeys that rule the same way
 * nwcheck.c and nwsup.c already do: by #include-ing blob.h and using
 * its own accessors (nw_check, nw_units, nw_binds, nw_unit_has_brick),
 * never a hand-derived struct layout of its own. Verifies the seal
 * before reading anything (bug 1: verify, don't merely read), the same
 * way nwcheck_main.c does.
 *
 * Prints the exact fields nwspawn.c already derives into environment
 * variables before exec-ing nw-sup, in the same shapes (hex brick,
 * decimal everything else) -- not because this file re-derives that
 * logic, but because the caller (the relaunch orchestrator) is going
 * to set those same environment variables itself, so the values need
 * to already be in that form.
 *
 *     unit-info <plan.blob> <unit-name>
 *
 * Exit 0 and a KEY=VALUE block on stdout if found; exit 1 if the blob
 * fails nw_check (reason on stderr); exit 2 on a usage error; exit 3
 * if the blob is valid but names no unit with that name. */
#include "blob.h"

#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    if (argc != 3) {
        fprintf(stderr, "usage: unit-info <plan.blob> <unit-name>\n");
        return 2;
    }
    const char *path = argv[1];
    const char *want = argv[2];

    int fd = open(path, O_RDONLY);
    if (fd < 0) { perror(path); return 2; }
    struct stat st;
    if (fstat(fd, &st) < 0) { perror("stat"); close(fd); return 2; }
    if (st.st_size <= 0 || st.st_size > (off_t)NW_BLOB_MAX) {
        fprintf(stderr, "blob size\n");
        close(fd);
        return 1;
    }
    static unsigned char buf[NW_BLOB_BUF];
    ssize_t n = read(fd, buf, (size_t)st.st_size);
    close(fd);
    if (n != st.st_size) { fprintf(stderr, "short read\n"); return 2; }

    int e = nw_check(buf, (uint32_t)n);
    if (e != NW_OK) {
        fprintf(stderr, "REJECT %s (%d)\n", nw_errstr(e), e);
        return 1;
    }

    const struct nw_hdr *h = nw_hdr(buf);
    const struct nw_unit *u = nw_units(buf);
    const struct nw_bind *bd = nw_binds(buf);

    for (uint32_t i = 0; i < h->n_units; i++) {
        /* name[NW_NAME_LEN] is not guaranteed NUL-terminated by the
         * struct alone -- nw_check() already required it to be
         * (name_ok), so this comparison is safe on a blob that passed,
         * which is the only kind reaching this line. */
        if (strncmp(u[i].name, want, NW_NAME_LEN) != 0)
            continue;
        if (strnlen(u[i].name, NW_NAME_LEN) != strlen(want))
            continue;

        printf("UNIT=%s\n", want);
        printf("EXEC_PATH=%s\n", u[i].exec_path);
        printf("KIND=%u\n", (unsigned)u[i].kind);
        printf("BUDGET=%u\n", (unsigned)u[i].budget);
        printf("LIDS=%u\n", (unsigned)u[i].lids);

        if (nw_unit_has_brick(&u[i])) {
            char hex[NW_BRICK_HEX + 1];
            for (int k = 0; k < NW_BRICK_HASH; k++)
                snprintf(hex + 2 * k, 3, "%02x", u[i].brick[k]);
            printf("BRICK=%s\n", hex);
        } else {
            printf("BRICK=\n");
        }
        printf("LAYER=%s\n", u[i].layer);
        printf("LAYER_BYTES=%llu\n",
               (unsigned long long)u[i].res.layer_bytes);

        int nb = 0;
        for (uint32_t b = 0; b < h->n_binds; b++) {
            if (bd[b].unit != (uint16_t)i) continue;
            printf("BIND_%d=%s\n", nb, bd[b].path);
            nb++;
        }
        printf("NBINDS=%d\n", nb);
        return 0;
    }

    fprintf(stderr, "no such unit: %s\n", want);
    return 3;
}
