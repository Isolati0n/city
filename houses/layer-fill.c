/* unit-layer-fill: writes a large, known amount of data into its own
 * root and reports exactly how far it got and why it stopped.
 *
 * Proves layer_bytes is enforced as a filesystem boundary, not merely
 * declared: a house with a capacity-bounded layer must be able to fill
 * it and see ENOSPC, the same way any other filesystem refuses a write
 * past its own limit. kind=oneshot -- this reports once and exits 0
 * regardless of outcome; the test reads the report, not the exit code.
 *
 * EVERY LINE PADDED TO WIDTH BYTES, for the reason houses/orphan.c
 * gives at length and houses/layer.c already applies: PID 1's logger
 * reads in bounded chunks and appends a newline when a read does not
 * end in one, so an unpadded line can arrive split mid-token.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/statvfs.h>
#include <unistd.h>

#define WIDTH 64

static void say(const char *s)
{
    char b[WIDTH + 1];
    size_t n = strlen(s);
    if (n > WIDTH - 1) n = WIDTH - 1;
    memset(b, ' ', WIDTH);
    memcpy(b, s, n);
    b[WIDTH - 1] = '\n';
    b[WIDTH] = 0;
    (void)!write(1, b, WIDTH);
}

int main(void)
{
    char line[WIDTH * 2];
    char chunk[65536];
    memset(chunk, 'x', sizeof chunk);

    /* Capacity as the house itself sees it -- overlayfs's statvfs(2)
     * reports the UPPER (writable) filesystem's f_blocks/f_frsize on
     * this kernel, which is the loop-mounted, sized store when one is
     * mounted and the plain host root's stats otherwise. Reported
     * before any write below, so a test can compare it against the
     * plan's declared layer_bytes directly rather than infer it from
     * how far the fill got. */
    struct statvfs sv;
    if (statvfs("/", &sv) == 0) {
        snprintf(line, sizeof line, "[fill] capacity_bytes=%llu",
                 (unsigned long long)sv.f_blocks * (unsigned long long)sv.f_frsize);
        say(line);
    } else {
        snprintf(line, sizeof line, "[fill] statvfs=denied(%d)", errno);
        say(line);
    }

    int fd = open("/bigfile", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        snprintf(line, sizeof line, "[fill] open=denied(%d)", errno);
        say(line);
        return 0;
    }
    say("[fill] open=ok");

    long long total = 0;
    int stop_errno = 0;
    for (;;) {
        ssize_t w = write(fd, chunk, sizeof chunk);
        if (w < 0) {
            stop_errno = errno;
            break;
        }
        total += w;
        if (total > (long long)4 * 1024 * 1024 * 1024) {
            /* Refuse to run away past a sane ceiling if something is
             * wrong and the write never fails -- this is a test
             * fixture, not a stress tool. */
            stop_errno = -1;
            break;
        }
    }
    close(fd);

    snprintf(line, sizeof line, "[fill] wrote=%lld", total);
    say(line);
    snprintf(line, sizeof line, "[fill] stopped_errno=%d", stop_errno);
    say(line);
    return 0;
}
