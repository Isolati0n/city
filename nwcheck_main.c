#include "blob.h"

#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "usage: nw-check <plan.blob>\n");
        return 2;
    }
    int fd = open(argv[1], O_RDONLY);
    if (fd < 0) { perror(argv[1]); return 2; }
    struct stat st;
    if (fstat(fd, &st) < 0) { perror("stat"); return 2; }
    if (st.st_size <= 0 || st.st_size > 1 << 20) {
        fprintf(stderr, "blob size\n");
        return 1;
    }
    static unsigned char buf[1 << 20];
    ssize_t n = read(fd, buf, (size_t)st.st_size);
    close(fd);
    if (n != st.st_size) { fprintf(stderr, "short read\n"); return 2; }
    int e = nw_check(buf, (uint32_t)n);
    if (e != NW_OK) {
        fprintf(stderr, "REJECT %s (%d)\n", nw_errstr(e), e);
        return 1;
    }
    const struct nw_hdr *h = nw_hdr(buf);
    printf("OK units=%u binds=%u crc=0x%08x\n", h->n_units, h->n_binds, h->crc32);
    return 0;
}
