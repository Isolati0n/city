/* unit-bindfile: reads its declared bind AS A FILE, not a directory, and
 * reports what it found. Exists to pin nwsup.c's ll_beneath() fix for a
 * non-directory Landlock bind target -- CLAUDE.md invariant 6, and
 * docs/options/10-console-house.md, where the fix was found.
 *
 * Every existing brick fixture's bind is a directory (bind=/etc,
 * bind={shared} in test_landlock_confines); this one is the first bind
 * that IS the file, not a directory containing one, and the whole point
 * is that starting at all is the assertion. Before the fix, nw-sup died
 * at `FAIL landlock rule errno=22` applying the Landlock rule for this
 * bind, before this binary ever ran a line.
 *
 * EVERY LINE IS PADDED TO WIDTH BYTES, same reason houses/layer.c gives:
 * PID 1's logger reads in bounded chunks and a short, unpadded line can
 * arrive split mid-token.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <stdlib.h>

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
    const char *b = getenv("NW_BIND_0");
    char line[WIDTH];
    if (!b || !b[0]) {
        say("bindfile bind=none");
        return 0;
    }
    int fd = open(b, O_RDONLY);
    if (fd < 0) {
        snprintf(line, sizeof line, "bindfile open=denied(%d)", errno);
        say(line);
        return 0;
    }
    char buf[40];
    ssize_t n = read(fd, buf, sizeof buf - 1);
    close(fd);
    if (n < 0) n = 0;
    buf[n] = 0;
    for (char *p = buf; *p; p++)
        if (*p == '\n') { *p = 0; break; }
    snprintf(line, sizeof line, "bindfile content=%s", buf);
    say(line);
    return 0;
}
