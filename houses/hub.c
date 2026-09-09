/* unit-hub: the two-or-more-edge neighbour.
 *
 * Reads EVERY descriptor in its kit (3 .. 3+NW_WIRES-1) and reports which
 * peer identified itself on which fd, by name. Emits exactly ONE line so the
 * per-house log pipe in pid1.c cannot split or coalesce it: a single write()
 * under PIPE_BUF is atomic, and the suite matches on the content, never on
 * the logger's [name] prefix.
 *
 *   hubmap wires=N digest=0x........ map=fd3:tok,fd4:tok,...
 *
 * digest is FNV-1a over the ordered tokens ("tok0\0tok1\0..."), so the full
 * fd->peer permutation is asserted even at large N where the map is elided.
 * Not in the TCB.
 */
#define _GNU_SOURCE
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define MAX_W    128
#define TOK_LEN  40
#define POLL_MS  2000

static char tok[MAX_W][TOK_LEN];

static void sanitize(char *s)
{
    for (char *p = s; *p; p++) {
        unsigned char c = (unsigned char)*p;
        if (c < 33 || c > 126 || c == ',' || c == ':')
            *p = '.';
    }
}

int main(void)
{
    const char *w = getenv("NW_WIRES");
    int nw = w ? atoi(w) : 0;
    if (nw < 0) nw = 0;
    if (nw > MAX_W) nw = MAX_W;

    for (int i = 0; i < nw; i++) {
        int fd = 3 + i;
        struct pollfd pf = { .fd = fd, .events = POLLIN };
        int pr = poll(&pf, 1, POLL_MS);
        if (pr == 0) {
            snprintf(tok[i], TOK_LEN, "TIMEOUT");
            continue;
        }
        if (pr < 0) {
            snprintf(tok[i], TOK_LEN, "POLLERR");
            continue;
        }
        char b[64];
        ssize_t n = read(fd, b, sizeof b - 1);
        if (n <= 0) {
            snprintf(tok[i], TOK_LEN, n == 0 ? "EOF" : "READERR");
            continue;
        }
        b[n] = 0;
        while (n > 0 && (b[n - 1] == '\n' || b[n - 1] == '\r'))
            b[--n] = 0;
        if (n > TOK_LEN - 1) {
            snprintf(tok[i], TOK_LEN, "TOOLONG");
            continue;
        }
        memcpy(tok[i], b, (size_t)n + 1);
        sanitize(tok[i]);
    }

    uint32_t h = 2166136261u;
    for (int i = 0; i < nw; i++) {
        for (const char *p = tok[i]; ; p++) {
            h ^= (unsigned char)*p;
            h *= 16777619u;
            if (!*p) break;
        }
    }

    /* The logger in pid1.c reads the log pipe 256 bytes at a time and prefixes
     * each chunk, so a longer line would be split and re-prefixed. Header is at
     * most "hubmap wires=128 digest=0x12345678 map=" = 39 bytes; keep
     * 39 + map + "\n" <= 255. Beyond that the map elides and the digest, which
     * covers every token in order, carries the assertion. */
    char map[216];
    size_t mo = 0;
    int elided = 0;
    for (int i = 0; i < nw; i++) {
        char one[64];
        int k = snprintf(one, sizeof one, "%sfd%d:%s", i ? "," : "", 3 + i, tok[i]);
        if (k < 0) break;
        if (mo + (size_t)k + 5 > sizeof map - 1) { elided = 1; break; }
        memcpy(map + mo, one, (size_t)k);
        mo += (size_t)k;
    }
    map[mo] = 0;
    if (elided) {
        memcpy(map + mo, ",...", 5);   /* truncated: trust the digest */
        mo += 4;
    }

    char line[512];
    int n = snprintf(line, sizeof line,
                     "hubmap wires=%d digest=0x%08x map=%s\n", nw, h, map);
    if (n > 0) {
        ssize_t r = write(1, line, (size_t)n);
        (void)r;
    }
    usleep(200 * 1000);
    return 0;
}
