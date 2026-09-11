/* nw-spawn — boot-time unit spawner.
 *
 * Forks one supervisor per unit, double-forked so PID 1 adopts the houses,
 * reports their pids, and EXITS.
 *
 * It exists only during boot. PID 1 has no respawn path (see reap_all in
 * pid1.c: a house exit is recorded but never re-execed; nothing a house
 * does halts the city), and restart budgets live in nw-sup, one authority per
 * unit. So spawning happens exactly once per unit and a process whose
 * lifetime is exactly boot matches that need.
 *
 * Its predecessor, the electrician, stayed alive and inert because it held
 * the only copy of the connection graph; its death mid-life was unrecoverable
 * and PID 1 halted on it. With edges removed there is no graph to hold, so
 * this process has no mid-life and normal exit is the success path. PID 1
 * waits for exit 0 rather than watching for death.
 *
 * TCB.
 */
#define _GNU_SOURCE
#include "blob.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

static void die(const char *s)
{
    char b[160];
    int n = snprintf(b, sizeof b, "[nw-spawn] FAIL %s errno=%d\n", s, errno);
    if (n > 0) { ssize_t r = write(2, b, (size_t)n); (void)r; }
    _exit(71);
}

static void say(const char *s)
{
    char b[160];
    int n = snprintf(b, sizeof b, "[nw-spawn] %s\n", s);
    if (n > 0) { ssize_t r = write(2, b, (size_t)n); (void)r; }
}

static int kept(int fd, const int *keep, int n)
{
    for (int i = 0; i < n; i++)
        if (keep[i] == fd) return 1;
    return 0;
}

/* No compile-time fd numbers. Sweep whatever the kernel assigned. */
static void close_others(const int *keep, int nkeep)
{
    int dfd = open("/proc/self/fd", O_RDONLY | O_DIRECTORY);
    if (dfd < 0) {
        for (int fd = 0; fd < NW_FD_SWEEP; fd++)
            if (!kept(fd, keep, nkeep)) close(fd);
        return;
    }
    DIR *d = fdopendir(dfd);
    if (!d) {
        close(dfd);
        die("fdopendir");
    }
    int dirfd_n = dirfd(d);
    int doomed[NW_FD_SWEEP];
    int nd = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        int fd = atoi(e->d_name);
        if (fd == dirfd_n) continue;
        if (!kept(fd, keep, nkeep)) {
            /* Never drop one on the floor. The old bound was a bare literal
             * and silently stopped collecting past it, which would have left
             * descriptors open in a house with no error anywhere. */
            if (nd >= NW_FD_SWEEP) { closedir(d); die("fd sweep overflow"); }
            doomed[nd++] = fd;
        }
    }
    closedir(d);
    for (int i = 0; i < nd; i++)
        close(doomed[i]);
}

static int clear_cloexec(int fd)
{
    int fl = fcntl(fd, F_GETFD);
    if (fl < 0) return -1;
    return fcntl(fd, F_SETFD, fl & ~FD_CLOEXEC);
}

/* A unit's descriptors: /dev/null on 0, its own log pipe on 1 and 2, nothing
 * else. With edges gone there is no BASE + i arithmetic left here at all --
 * the class behind bugs 5, 9 and 13 went out with the wiring. */
static int pack_kit(int log_w)
{
    int nullfd = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (nullfd < 0) return -1;
    int logn = fcntl(log_w, F_DUPFD_CLOEXEC, 3);
    if (logn < 0) return -1;

    int keep[2];
    keep[0] = nullfd;
    keep[1] = logn;
    close_others(keep, 2);

    if (dup2(nullfd, 0) < 0) return -1;
    if (dup2(logn, 1) < 0) return -1;
    if (dup2(logn, 2) < 0) return -1;
    if (nullfd > 2) close(nullfd);
    if (logn > 2) close(logn);
    if (clear_cloexec(0) < 0 || clear_cloexec(1) < 0 || clear_cloexec(2) < 0)
        return -1;
    return 0;
}

int main(int argc, char **argv)
{
    /* argv: blob report_fd nlogs logw...   env: NW_SUP */
    if (argc < 4) die("argv");
    const char *blob_path = argv[1];
    int report_fd = atoi(argv[2]);
    int nlogs = atoi(argv[3]);
    if (nlogs < 1 || nlogs > NW_MAX_UNITS) die("nlogs");
    if (argc != 4 + nlogs) die("log argc");

    int logw[NW_MAX_UNITS];
    for (int i = 0; i < nlogs; i++) {
        logw[i] = atoi(argv[4 + i]);
        if (logw[i] < 0) die("log fd");
    }

    const char *sup = getenv("NW_SUP");
    if (!sup || !sup[0]) die("NW_SUP");

    sigset_t mask;
    sigfillset(&mask);
    sigprocmask(SIG_BLOCK, &mask, NULL);

    int bfd = open(blob_path, O_RDONLY);
    if (bfd < 0) die("open blob");
    /* NW_BLOB_BUF, and the size is checked against NW_BLOB_MAX rather than
     * against sizeof blob. The recheck exists to catch a file that is not
     * the one PID 1 read; with a buffer of exactly NW_BLOB_MAX, a maximal
     * legal blob with arbitrary bytes appended truncated to precisely the
     * length nw_check expects and passed. The sentinel byte makes a full
     * read proof of an oversized file. tcb-review, reproduced. */
    static unsigned char blob[NW_BLOB_BUF];
    ssize_t n = read(bfd, blob, sizeof blob);
    close(bfd);
    if (n <= 0) die("read blob");
    if ((uintmax_t)n > (uintmax_t)NW_BLOB_MAX) die("blob size");
    if (nw_check(blob, (uint32_t)n) != NW_OK) die("blob recheck");

    const struct nw_hdr *h = nw_hdr(blob);
    const struct nw_unit *u = nw_units(blob);
    const struct nw_bind *bd = nw_binds(blob);
    if ((int)h->n_units != nlogs) die("log/unit mismatch");

    pid_t pids[NW_MAX_UNITS];
    for (uint32_t i = 0; i < h->n_units; i++) {
        int pp[2];
        if (pipe2(pp, O_CLOEXEC) < 0) die("pid pipe");
        pid_t mid = fork();
        if (mid < 0) die("fork");
        if (mid == 0) {
            close(pp[0]);
            pid_t house = fork();
            if (house < 0) die("fork house");
            if (house != 0) {
                if (write(pp[1], &house, sizeof house) != (ssize_t)sizeof house)
                    die("write house pid");
                _exit(0);
            }
            close(pp[1]);
            if (pack_kit(logw[i]) < 0) die("pack kit");
            char lbuf[8], bbuf[8], kbuf[8], nbuf[8];
            snprintf(lbuf, sizeof lbuf, "%u", (unsigned)u[i].lids);
            snprintf(bbuf, sizeof bbuf, "%u", (unsigned)u[i].budget);
            snprintf(kbuf, sizeof kbuf, "%u", (unsigned)u[i].kind);
            setenv("NW_UNIT", u[i].name, 1);
            setenv("NW_HOUSE", u[i].name, 1);
            setenv("NW_LIDS", lbuf, 1);
            setenv("NW_BUDGET", bbuf, 1);
            setenv("NW_KIND", kbuf, 1);
            /* The brick and the paths bound into it. Names, not descriptors:
             * nw-sup mounts them itself and the house opens what it needs.
             * The init still provisions exactly /dev/null and a log pipe
             * (invariant 5). */
            setenv("NW_BRICK", u[i].brick, 1);
            int nb = 0;
            for (uint32_t b = 0; b < h->n_binds; b++) {
                if (bd[b].unit != (uint16_t)i) continue;
                char k[24];
                snprintf(k, sizeof k, "NW_BIND_%d", nb);
                setenv(k, bd[b].path, 1);
                nb++;
            }
            snprintf(nbuf, sizeof nbuf, "%d", nb);
            setenv("NW_NBINDS", nbuf, 1);
            execl(sup, "nw-sup", u[i].exec_path, u[i].name, (char *)0);
            die("exec nw-sup");
        }
        close(pp[1]);
        pid_t house = 0;
        if (read(pp[0], &house, sizeof house) != (ssize_t)sizeof house)
            die("read house pid");
        close(pp[0]);
        if (waitpid(mid, NULL, 0) < 0) die("reap mid");
        pids[i] = house;
        /* Width from NW_NAME_LEN, not a hand-written precision -- see the
         * comment in pid1.c's house-exit line. */
        char line[NW_NAME_LEN + 64];
        snprintf(line, sizeof line, "spawned %.*s pid=%d lids=%u",
                 NW_NAME_LEN - 1, u[i].name, (int)house,
                 (unsigned)u[i].lids);
        say(line);
    }

    for (int i = 0; i < nlogs; i++)
        close(logw[i]);

    uint32_t nu = h->n_units;
    if (write(report_fd, &nu, sizeof nu) != (ssize_t)sizeof nu) die("report n");
    if (write(report_fd, pids, sizeof(pid_t) * nu) != (ssize_t)(sizeof(pid_t) * nu))
        die("report pids");
    close(report_fd);

    say("units spawned");
    return 0;
}
