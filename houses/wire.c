/* unit-wire: docs/options/17-edges.md's fixture house.
 *
 * A wired house finds its peer descriptors already in its table,
 * inherited non-CLOEXEC, identified by NW_WIRE_<fd>=<peer-name> --
 * this fixture discovers its own wires by probing NW_WIRE_3,
 * NW_WIRE_4, ... upward until one is unset, rather than assuming a
 * fixed count, so it works unmodified whether it has zero wires (an
 * unwired house in the same plan) or several.
 *
 * Three modes, chosen by NW_WIRE_MODE (default "echo"):
 *
 *   echo          -- for each wire, write "hello from <name>\n" and
 *                     read one line back, logging both.
 *   bp-writer     -- write NW_WIRE_BYTES (default 4 MiB, comfortably
 *                     past any AF_UNIX SOCK_STREAM socket buffer) to
 *                     its first wire in one blocking write(2) loop,
 *                     logging when the call finally returns. Proves
 *                     real backpressure: a shared file has no buffer
 *                     to fill and could not block a writer this way.
 *   bp-reader     -- sleep NW_WIRE_READER_DELAY_MS (default 500) before
 *                     reading anything, then drain its first wire until
 *                     it has read NW_WIRE_BYTES total, logging the
 *                     total once done.
 *
 * EACH LINE SELF-TAGGED with the house's own name (NW_HOUSE), because
 * PID 1's logger prefixes a write CHUNK and not a line (harness.md).
 *
 * Not in the TCB.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static const char *name;
static struct timespec t0;

static long elapsed_ms(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (t.tv_sec - t0.tv_sec) * 1000 + (t.tv_nsec - t0.tv_nsec) / 1000000;
}

static void logline(const char *s)
{
    char buf[256];
    int n = snprintf(buf, sizeof buf, "[%s] t=%ldms %s\n", name, elapsed_ms(), s);
    if (n > 0) { ssize_t r = write(1, buf, (size_t)n); (void)r; }
}

static void logf2(const char *fmt, long a)
{
    char buf[256];
    int n = snprintf(buf, sizeof buf, "[%s] t=%ldms ", name, elapsed_ms());
    char rest[200];
    snprintf(rest, sizeof rest, fmt, a);
    strncat(buf, rest, sizeof buf - (size_t)n - 2);
    strncat(buf, "\n", sizeof buf - strlen(buf) - 1);
    ssize_t r = write(1, buf, strlen(buf)); (void)r;
}

int main(void)
{
    clock_gettime(CLOCK_MONOTONIC, &t0);
    name = getenv("NW_HOUSE");
    if (!name) name = "?";

    int wire_fd[64];
    int n_wires = 0;
    for (int slot = 3; slot < 3 + 64; slot++) {
        char key[24];
        snprintf(key, sizeof key, "NW_WIRE_%d", slot);
        const char *peer = getenv(key);
        if (!peer) break;
        wire_fd[n_wires] = slot;
        n_wires++;
    }
    logf2("wires=%ld", (long)n_wires);

    /* Mode is chosen by comparing this house's OWN name against two
     * shared env vars naming which house plays which role -- not a
     * per-house NW_WIRE_MODE, because the plan format hands every
     * house in a city the SAME environment (this is real, not a test
     * limitation: nothing in a plan differentiates one house's runtime
     * env from another's), so two houses cannot be given different
     * literal mode strings this way. Comparing identity against a
     * shared pair of names lets one shared env apply to a whole city
     * while each house still picks a distinct role. */
    const char *mode = "echo";
    const char *writer_name = getenv("NW_WIRE_WRITER");
    const char *reader_name = getenv("NW_WIRE_READER");
    if (writer_name && strcmp(name, writer_name) == 0) mode = "bp-writer";
    else if (reader_name && strcmp(name, reader_name) == 0) mode = "bp-reader";

    if (strcmp(mode, "echo") == 0) {
        for (int k = 0; k < n_wires; k++) {
            char msg[64];
            int mn = snprintf(msg, sizeof msg, "hello from %s\n", name);
            ssize_t wr = write(wire_fd[k], msg, (size_t)mn);
            (void)wr;
            char buf[128] = {0};
            ssize_t n = read(wire_fd[k], buf, sizeof buf - 1);
            char line[220];
            snprintf(line, sizeof line, "wire[%d] fd=%d read %ld bytes: %s",
                     k, wire_fd[k], (long)n, n > 0 ? buf : "(none)");
            logline(line);
        }
    } else if (strcmp(mode, "bp-writer") == 0) {
        long total = 4L * 1024 * 1024;
        const char *bs = getenv("NW_WIRE_BYTES");
        if (bs) total = atol(bs);
        static char chunk[65536];
        memset(chunk, 'x', sizeof chunk);
        long left = total;
        while (left > 0) {
            size_t want = (size_t)(left < (long)sizeof chunk ? left : (long)sizeof chunk);
            ssize_t w = write(wire_fd[0], chunk, want);
            if (w <= 0) { logline("write failed"); return 1; }
            left -= w;
        }
        logf2("bp-writer done, wrote %ld bytes", total);
    } else if (strcmp(mode, "bp-reader") == 0) {
        long delay_ms = 500;
        const char *dm = getenv("NW_WIRE_READER_DELAY_MS");
        if (dm) delay_ms = atol(dm);
        logf2("bp-reader sleeping %ldms before reading", delay_ms);
        usleep((useconds_t)(delay_ms * 1000));
        long total = 4L * 1024 * 1024;
        const char *bs = getenv("NW_WIRE_BYTES");
        if (bs) total = atol(bs);
        static char buf[65536];
        long got = 0;
        while (got < total) {
            ssize_t n = read(wire_fd[0], buf, sizeof buf);
            if (n <= 0) break;
            got += n;
        }
        logf2("bp-reader drained %ld bytes", got);
    }

    return 0;
}
