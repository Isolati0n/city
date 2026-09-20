/* verity2.c -- two questions DeepSeek's answer turns on.
 *
 * Q1  Is the verity digest a function of content ALONE, or of content
 *     plus parameters? Same bytes, block_size 4096 vs 1024. If the
 *     digests differ, the store's address is not content-addressing and
 *     the parameter is a store-wide binding.
 *
 * Q2  Stages a target for the corruption test. DeepSeek claims that under
 *     digest-addressing "there is no way to open the object by that digest
 *     and get a mismatch" -- so corruption presents as ABSENCE, never as
 *     present-and-wrong, and the two become indistinguishable. But a real
 *     store is a filesystem: the name is a path, the digest is metadata,
 *     and nothing makes the kernel compare them. If the bytes under the
 *     path are corrupted after enablement, verity should fail the READ.
 *     That would be present-and-wrong, distinguishable from absent.
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

struct fsverity_enable_arg {
    uint32_t version, hash_algorithm, block_size, salt_size;
    uint64_t salt_ptr; uint32_t sig_size; uint32_t __reserved1;
    uint64_t sig_ptr; uint64_t __reserved2[11];
};
#define ENABLE_VERITY  _IOW('f', 133, struct fsverity_enable_arg)
struct fsverity_digest { uint16_t digest_algorithm, digest_size; uint8_t digest[]; };
#define MEASURE_VERITY _IOWR('f', 134, struct fsverity_digest)

static const char BODY[] =
  "IDENTICAL CONTENT FOR BOTH FILES -- ONLY THE BLOCK SIZE DIFFERS. "
  "Padding so the file spans more than one 1024-byte block, otherwise the "
  "parameter cannot possibly matter and the test proves nothing at all. "
  "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  "ccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
  "ddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
  "fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
  "ggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggggg"
  "hhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhhh"
  "iiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiiii"
  "jjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjjj"
  "kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk"
  "lllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllllll"
  "mmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmmm"
  "nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn"
  "ooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo\n";

static int make(const char *p)
{
    int fd = open(p, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return -1;
    if (write(fd, BODY, sizeof BODY - 1) != (ssize_t)(sizeof BODY - 1)) { close(fd); return -1; }
    fsync(fd); close(fd);
    return 0;
}

static void seal(const char *p, uint32_t bs)
{
    if (make(p) < 0) { printf("[v2] create %s FAILED\n", p); return; }
    int fd = open(p, O_RDONLY);
    struct fsverity_enable_arg a;
    memset(&a, 0, sizeof a);
    a.version = 1; a.hash_algorithm = 1; a.block_size = bs;
    if (ioctl(fd, ENABLE_VERITY, &a) != 0) {
        printf("[v2] block_size=%-5u ENABLE FAILED errno=%d (%s)\n", bs, errno, strerror(errno));
        close(fd); return;
    }
    unsigned char buf[sizeof(struct fsverity_digest) + 64];
    memset(buf, 0, sizeof buf);
    struct fsverity_digest *d = (void *)buf; d->digest_size = 64;
    if (ioctl(fd, MEASURE_VERITY, d) == 0) {
        printf("[v2] block_size=%-5u digest=", bs);
        for (int i = 0; i < d->digest_size; i++) printf("%02x", d->digest[i]);
        printf("\n");
    }
    close(fd);
}

int main(void)
{
    mkdir("/nwverity", 0755);
    printf("[v2] identical content, %zu bytes, two block sizes\n", sizeof BODY - 1);
    seal("/nwverity/bs4096", 4096);
    seal("/nwverity/bs1024", 1024);

    /* target for the corruption test on the next boot */
    seal("/nwverity/target", 4096);
    sync();
    printf("[v2] target staged and synced\n");
    printf("[v2] done\n");
    return 0;
}
