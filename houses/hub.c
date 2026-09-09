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
 *
 * It ALSO binds by name, which is the real criterion. Before reading anything
 * it resolves each peer named in NW_HUB_EXPECT to a descriptor by scanning
 * NW_WIRE_<fd>, and afterwards asserts that descriptor delivered that peer's
 * identity. A second line reports it:
 *
 *   hubbind expect=N ok=N status=OK resolved=north:fd3,south:fd4
 *
 * The resolved fds differ between two edge orderings and status stays OK --
 * that is the point. Position carries no meaning; the name does. Reading fd 3
 * and hoping would pass the hubmap check and fail this one.
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

    /* Resolve name -> descriptor from the environment BEFORE any read. */
    const char *wname[MAX_W];
    for (int i = 0; i < nw; i++) {
        char kbuf[24];
        snprintf(kbuf, sizeof kbuf, "NW_WIRE_%d", 3 + i);
        wname[i] = getenv(kbuf);
    }
    char expect[MAX_W][TOK_LEN];
    int  exp_idx[MAX_W];
    int  nexp = 0;
    const char *ev = getenv("NW_HUB_EXPECT");
    if (ev && *ev) {
        const char *p = ev;
        while (*p && nexp < MAX_W) {
            const char *c = strchr(p, ',');
            size_t len = c ? (size_t)(c - p) : strlen(p);
            if (len > 0 && len < TOK_LEN) {
                memcpy(expect[nexp], p, len);
                expect[nexp][len] = 0;
                exp_idx[nexp] = -1;
                for (int i = 0; i < nw; i++)
                    if (wname[i] && strcmp(wname[i], expect[nexp]) == 0) {
                        exp_idx[nexp] = i;
                        break;
                    }
                nexp++;
            }
            if (!c) break;
            p = c + 1;
        }
    }

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
    /* The logger reads the pipe 256 bytes at a time and re-prefixes each
     * chunk. The hubmap line alone approaches that, so let it drain before
     * the second line rather than have the two split across one read. */
    usleep(150 * 1000);

    /* Binding assertion: the peer named must be the peer that arrived. */
    if (nexp > 0) {
        char rmap[176];
        size_t ro = 0;
        int ok = 0, rel = 0;
        for (int j = 0; j < nexp; j++) {
            int i = exp_idx[j];
            /* expect[j] is bounded by construction: len < TOK_LEN. */
            char want[4 + TOK_LEN];
            size_t el = strlen(expect[j]);
            memcpy(want, "IAM=", 4);
            memcpy(want + 4, expect[j], el + 1);
            int good = (i >= 0 && strcmp(tok[i], want) == 0);
            if (good) ok++;
            char one[16 + TOK_LEN];
            int k;
            if (i < 0)
                k = snprintf(one, sizeof one, "%s%s:UNRESOLVED",
                             ro ? "," : "", expect[j]);
            else if (good)
                k = snprintf(one, sizeof one, "%s%s:fd%d",
                             ro ? "," : "", expect[j], 3 + i);
            else
                k = snprintf(one, sizeof one, "%s%s:MISMATCH@fd%d",
                             ro ? "," : "", expect[j], 3 + i);
            /* Display elision must never stop the counting: an earlier
             * version broke out of this loop when rmap filled, so ok topped
             * out at whatever fitted and a passing run read as FAIL. */
            if (k < 0) continue;
            if (rel || ro + (size_t)k + 5 > sizeof rmap - 1) { rel = 1; continue; }
            memcpy(rmap + ro, one, (size_t)k);
            ro += (size_t)k;
        }
        rmap[ro] = 0;
        if (rel) { memcpy(rmap + ro, ",...", 5); ro += 4; }
        char bl[512];
        int bn = snprintf(bl, sizeof bl,
                          "hubbind expect=%d ok=%d status=%s resolved=%s\n",
                          nexp, ok, ok == nexp ? "OK" : "FAIL", rmap);
        if (bn > 0) { ssize_t r2 = write(1, bl, (size_t)bn); (void)r2; }
    }

    usleep(200 * 1000);
    return 0;
}
