/* unit-layer: writes into its own root, dies, and reads it back.
 *
 * THE FIXTURE THAT JUSTIFIES WRITABLE AREAS. Every house is its sealed
 * brick plus one writable layer, and the claim is that what it writes is
 * still there after it is restarted. Nothing else in the suite can show
 * that: every other brick fixture is oneshot and every other write test
 * asserts a REFUSAL, which was the right assertion when a house's root
 * was the bare image.
 *
 * Each run reads /state, reports what it found, appends this run's number
 * and exits nonzero so nw-sup restarts it. So run 2 reports what run 1
 * wrote, which is persistence across a death rather than within a life --
 * the two are different properties and only the first needs a layer.
 *
 * /id comes from the brick and /state from the layer, and BOTH are read
 * every run. That pairing is the test: seeing /state without /id would
 * mean the house is writing somewhere that is not over its brick, and
 * seeing /id without /state would mean the layer is not mounted. Either
 * alone is satisfied by an arrangement that is not what was asked for.
 *
 * EVERY LINE IS PADDED TO WIDTH BYTES, for the reason houses/orphan.c
 * gives at length: PID 1's logger reads in bounded chunks and appends a
 * newline when a read does not end in one, so an unpadded line can arrive
 * split mid-token and a test counting tokens fails on a correct tree.
 * WIDTH divides the logger's buffer, so every write is one whole line and
 * the pipe only ever holds whole lines.
 */
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>

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

/* Reads at most n-1 bytes and NUL-terminates. Returns 0 when the file is
 * not there, which is run 1 and is not an error. */
static int slurp(const char *path, char *buf, size_t n)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0) return 0;
    ssize_t r = read(fd, buf, n - 1);
    close(fd);
    if (r < 0) r = 0;
    buf[r] = 0;
    for (char *p = buf; *p; p++) if (*p == '\n') *p = ',';
    return 1;
}

int main(void)
{
    char line[WIDTH * 2];
    char id[64] = "absent", state[128] = "";

    slurp("/id", id, sizeof id);
    int had = slurp("/state", state, sizeof state);

    snprintf(line, sizeof line, "[layer] id=%s", id);
    say(line);
    snprintf(line, sizeof line, "[layer] state=%s",
             had ? state : "absent");
    say(line);

    /* Append, so the file records every run rather than the last one --
     * a test that only ever sees one value cannot tell "persisted" from
     * "rewritten from scratch each time". */
    int fd = open("/state", O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0) {
        snprintf(line, sizeof line, "[layer] write=denied(%d)", errno);
        say(line);
        return 9;
    }
    const char *mark = "r";
    (void)!write(fd, mark, 1);
    close(fd);
    say("[layer] write=ok");

    /* Nonzero so nw-sup restarts us and the next run reads this back. */
    return 7;
}
