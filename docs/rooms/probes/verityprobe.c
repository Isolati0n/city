/* verityprobe.c -- runs as a house. Tests the three things DeepSeek's
 * layering argument turns on:
 *
 *   1. can fs-verity be enabled on a file in a house's writable area
 *   2. does FS_IOC_MEASURE_VERITY return a digest the IMAGE could name
 *   3. is the file genuinely immutable afterwards
 *
 * (3) is DeepSeek's point about GC and restore being constrained.
 * (2) is the interesting one: if the image names the VERITY DIGEST rather
 * than a plain content hash, then "the name is honest" and "the content
 * is honest" stop being two separate checks -- the kernel's own digest is
 * the name. That would collapse the two halves of its chain into one.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdint.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/types.h>

#ifndef FS_IOC_ENABLE_VERITY
struct fsverity_enable_arg {
    uint32_t version, hash_algorithm, block_size, salt_size;
    uint64_t salt_ptr; uint32_t sig_size; uint32_t __reserved1;
    uint64_t sig_ptr; uint64_t __reserved2[11];
};
#define FS_IOC_ENABLE_VERITY _IOW('f', 133, struct fsverity_enable_arg)
#endif
#ifndef FS_IOC_MEASURE_VERITY
struct fsverity_digest { uint16_t digest_algorithm, digest_size; uint8_t digest[]; };
#define FS_IOC_MEASURE_VERITY _IOWR('f', 134, struct fsverity_digest)
#endif

#define P "/nwverity/obj"

int main(void)
{
    mkdir("/nwverity", 0755);
    int fd = open(P, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) { printf("[verity] create FAILED errno=%d\n", errno); return 1; }
    const char body[] = "sealed object content v1\n";
    if (write(fd, body, sizeof body - 1) < 0) { printf("[verity] write FAILED\n"); return 1; }
    close(fd);
    printf("[verity] wrote %s\n", P);

    /* 1 -- enable */
    fd = open(P, O_RDONLY);
    struct fsverity_enable_arg a;
    memset(&a, 0, sizeof a);
    a.version = 1; a.hash_algorithm = 1 /* SHA-256 */; a.block_size = 4096;
    if (ioctl(fd, FS_IOC_ENABLE_VERITY, &a) != 0) {
        printf("[verity] ENABLE FAILED errno=%d (%s)\n", errno, strerror(errno));
        close(fd); return 1;
    }
    printf("[verity] ENABLE ok\n");

    /* 2 -- measure: the digest the image could name */
    unsigned char buf[sizeof(struct fsverity_digest) + 64];
    memset(buf, 0, sizeof buf);
    struct fsverity_digest *d = (void *)buf;
    d->digest_size = 64;
    if (ioctl(fd, FS_IOC_MEASURE_VERITY, d) != 0) {
        printf("[verity] MEASURE FAILED errno=%d\n", errno);
    } else {
        printf("[verity] MEASURE ok alg=%u size=%u digest=", d->digest_algorithm, d->digest_size);
        for (int i = 0; i < d->digest_size && i < 32; i++) printf("%02x", d->digest[i]);
        printf("\n");
    }
    close(fd);

    /* 3 -- immutability */
    int w = open(P, O_WRONLY);
    if (w < 0) printf("[verity] reopen for write REFUSED errno=%d (%s)\n", errno, strerror(errno));
    else {
        ssize_t n = write(w, "X", 1);
        printf("[verity] write after enable: %s errno=%d\n", n < 0 ? "REFUSED" : "ALLOWED", errno);
        close(w);
    }

    /* read still works */
    char rb[64] = {0};
    int r = open(P, O_RDONLY);
    if (r >= 0) { read(r, rb, sizeof rb - 1); close(r); }
    rb[strcspn(rb, "\n")] = 0;
    printf("[verity] read back: '%s'\n", rb);
    printf("[verity] done\n");
    return 0;
}
