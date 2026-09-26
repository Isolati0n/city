/* docs/options/14-shared-store.md. See store.h for the contract. */
#define _GNU_SOURCE
#include "store.h"
#include "sha256.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#define NW_STORE_PATH_MAX 256
#define NW_STORE_CMP_CHUNK 4096

/* A name match at the content-addressed path is not proof of a content
 * match -- plan.md's bug 1 is the same shape one file over: "the seal
 * must be VERIFIED, not merely read." The name is derived from bytes a
 * caller composed, and for at least one real caller (write_evidence())
 * those bytes are fully deterministic and computable in advance from
 * the plan a house was baked from, so an unconfined co-resident house
 * (no brick, no Landlock -- the common case; see CLAUDE.md invariants
 * 5/6) can plant arbitrary content at the exact path its own next
 * evidence record will land at, before that record is ever written.
 * Trusting stat()'s success alone would let that planted content
 * silently and permanently suppress the real write, forever, since
 * nothing revisits an already-"present" name. Bounded to a fixed chunk
 * regardless of len, so this never needs malloc or a variable-length
 * stack buffer. */
static int nw_store_content_matches(int fd, const void *data, size_t len)
{
    const unsigned char *p = data;
    size_t remaining = len;
    unsigned char chunk[NW_STORE_CMP_CHUNK];
    while (remaining > 0) {
        size_t want = remaining < sizeof chunk ? remaining : sizeof chunk;
        ssize_t r = read(fd, chunk, want);
        if (r < 0 || (size_t)r != want) return 0;
        if (memcmp(chunk, p, want) != 0) return 0;
        p += want;
        remaining -= want;
    }
    return 1;
}

int nw_store_put(const char *dir, const void *data, size_t len,
                  const char *suffix, char out_hex[65])
{
    unsigned char hash[32];
    nw_sha256(data, len, hash);
    for (int i = 0; i < 32; i++)
        snprintf(out_hex + i * 2, 3, "%02x", hash[i]);
    out_hex[64] = '\0';

    char finalpath[NW_STORE_PATH_MAX];
    if (snprintf(finalpath, sizeof finalpath, "%s/%s%s",
                 dir, out_hex, suffix) >= (int)sizeof finalpath)
        return -1;

    /* The real dedup: if this exact content is ALREADY on disk under
     * its own hash, verified byte-for-byte rather than assumed from
     * the name matching, say so and do nothing. A size mismatch, a
     * content mismatch, or a file that cannot even be opened to check
     * are all treated identically to "not really there" and fall
     * through to the unconditional write+rename below, which is
     * always self-healing regardless of what previously occupied the
     * name -- restoring exactly the property the hand-rolled code this
     * replaces already had. This stat is a plain existence probe, not
     * a lock, and it can race a concurrent writer of the SAME content
     * -- see the comment on the rename below for why that race is
     * safe rather than merely tolerated. */
    struct stat st;
    if (stat(finalpath, &st) == 0) {
        if ((size_t)st.st_size == len) {
            int rfd = open(finalpath, O_RDONLY | O_CLOEXEC);
            if (rfd >= 0) {
                int matches = nw_store_content_matches(rfd, data, len);
                close(rfd);
                if (matches) return 0;
            }
        }
    } else if (errno != ENOENT) {
        return -1;
    }

    char tmppath[NW_STORE_PATH_MAX];
    if (snprintf(tmppath, sizeof tmppath, "%s/.tmp-XXXXXX",
                 dir) >= (int)sizeof tmppath)
        return -1;

    int fd = mkostemp(tmppath, O_CLOEXEC);
    if (fd < 0) return -1;
    ssize_t w = write(fd, data, len);
    close(fd);
    if (w < 0 || (size_t)w != len) { unlink(tmppath); return -1; }

    /* Unconditional, same as mkbrick.py's os.replace and the write_
     * evidence() rename this replaces: the destination, if another
     * writer won the race, already holds the identical content -- the
     * name IS the hash of the bytes. Two producers writing the same
     * content at nearly the same time both pass the stat() above (both
     * see ENOENT), both write their own temp file, and both rename onto
     * the same final path; rename(2) is atomic on a single filesystem,
     * so the result is always one complete file with these exact
     * bytes, never a partial or corrupted one, whichever renamed
     * second. Neither writer needs to know it was racing, and no lock
     * is taken anywhere in this function. */
    if (rename(tmppath, finalpath) < 0) { unlink(tmppath); return -1; }
    return 1;
}
