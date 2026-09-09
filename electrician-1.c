#define _GNU_SOURCE
#include "blob.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

static void die(const char *s)
{
    char b[160];
    int n = snprintf(b, sizeof b, "[electrician] FAIL %s errno=%d\n", s, errno);
    if (n > 0) { ssize_t r = write(2, b, (size_t)n); (void)r; }
    _exit(71);
}

static void say(const char *s)
{
    char b[160];
    int n = snprintf(b, sizeof b, "[electrician] %s\n", s);
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
        for (int fd = 0; fd < 512; fd++)
            if (!kept(fd, keep, nkeep)) close(fd);
        return;
    }
    DIR *d = fdopendir(dfd);
    if (!d) {
        close(dfd);
        die("fdopendir");
    }
    int dirfd_n = dirfd(d);
    int doomed[512];
    int nd = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        int fd = atoi(e->d_name);
        if (fd == dirfd_n) continue;
        if (!kept(fd, keep, nkeep) && nd < 512)
            doomed[nd++] = fd;
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

/* Inert filter as a table of allowed nrs, not scattered immediates. */
static void go_inert(void)
{
    static const int allow[] = {
        __NR_pause, __NR_rt_sigreturn, __NR_exit_group, __NR_exit
    };
    struct sock_filter f[2 + 4 + 2];
    unsigned n = 0;
    f[n++] = (struct sock_filter)BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
                                          offsetof(struct seccomp_data, nr));
    for (unsigned i = 0; i < 4; i++)
        f[n++] = (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
                                              (unsigned)allow[i], 4 - i, 0);
    f[n++] = (struct sock_filter)BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS);
    f[n++] = (struct sock_filter)BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW);
    struct sock_fprog prog = { .len = (unsigned short)n, .filter = f };
    say("inert");
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0)
        die("no_new_privs");
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog) < 0)
        die("seccomp");
    for (;;)
        pause();
}

static int pack_kit(int log_w, const int *wires, int nw)
{
    int nullfd = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (nullfd < 0) return -1;
    int logn = fcntl(log_w, F_DUPFD_CLOEXEC, 3);
    if (logn < 0) return -1;
    int parked[NW_MAX_EDGES];
    for (int i = 0; i < nw; i++) {
        parked[i] = fcntl(wires[i], F_DUPFD_CLOEXEC, 3);
        if (parked[i] < 0) return -1;
    }
    int keep[3 + NW_MAX_EDGES];
    int nk = 0;
    keep[nk++] = nullfd;
    keep[nk++] = logn;
    for (int i = 0; i < nw; i++)
        keep[nk++] = parked[i];
    close_others(keep, nk);

    if (dup2(nullfd, 0) < 0) return -1;
    if (dup2(logn, 1) < 0) return -1;
    if (dup2(logn, 2) < 0) return -1;
    if (nullfd > 2) close(nullfd);
    if (logn > 2) close(logn);

    for (int i = 0; i < nw; i++) {
        int dest = 3 + i;
        if (parked[i] != dest) {
            if (dup2(parked[i], dest) < 0) return -1;
            if (parked[i] != dest) close(parked[i]);
        }
        if (clear_cloexec(dest) < 0) return -1;
    }
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
    static unsigned char blob[1 << 16];
    ssize_t n = read(bfd, blob, sizeof blob);
    close(bfd);
    if (n <= 0) die("read blob");
    if (nw_check(blob, (uint32_t)n) != NW_OK) die("blob recheck");

    const struct nw_hdr *h = nw_hdr(blob);
    const struct nw_unit *u = nw_units(blob);
    const struct nw_edge *ed = nw_edges(blob);
    if ((int)h->n_units != nlogs) die("log/unit mismatch");

    int pair[NW_MAX_EDGES][2];
    for (uint32_t i = 0; i < h->n_edges; i++) {
        if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair[i]) < 0)
            die("socketpair");
    }

    pid_t pids[NW_MAX_UNITS];
    for (uint32_t i = 0; i < h->n_units; i++) {
        int wires[NW_MAX_EDGES];
        int nw = 0;
        for (uint32_t e = 0; e < h->n_edges; e++) {
            if (ed[e].a == i) wires[nw++] = pair[e][0];
            else if (ed[e].b == i) wires[nw++] = pair[e][1];
        }
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
            if (pack_kit(logw[i], wires, nw) < 0) die("pack kit");
            char nbuf[8], lbuf[8], bbuf[8], wbuf[8], cbuf[8];
            snprintf(nbuf, sizeof nbuf, "%d", nw);
            snprintf(lbuf, sizeof lbuf, "%u", (unsigned)u[i].lids);
            snprintf(bbuf, sizeof bbuf, "%u", (unsigned)u[i].budget);
            snprintf(wbuf, sizeof wbuf, "%u", (unsigned)u[i].window_s);
            snprintf(cbuf, sizeof cbuf, "%u", (unsigned)u[i].critical);
            setenv("NW_UNIT", u[i].name, 1);
            setenv("NW_HOUSE", u[i].name, 1);
            setenv("NW_WIRES", nbuf, 1);
            setenv("NW_KIT", nbuf, 1);
            setenv("NW_LIDS", lbuf, 1);
            setenv("NW_BUDGET", bbuf, 1);
            setenv("NW_WINDOW", wbuf, 1);
            setenv("NW_CRITICAL", cbuf, 1);
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
        char line[96];
        snprintf(line, sizeof line, "spawned %.31s pid=%d kit=%d lids=%u",
                 u[i].name, (int)house, nw, (unsigned)u[i].lids);
        say(line);
    }

    for (uint32_t i = 0; i < h->n_edges; i++) {
        close(pair[i][0]);
        close(pair[i][1]);
    }
    for (int i = 0; i < nlogs; i++)
        close(logw[i]);

    uint32_t nu = h->n_units;
    if (write(report_fd, &nu, sizeof nu) != (ssize_t)sizeof nu) die("report n");
    if (write(report_fd, pids, sizeof(pid_t) * nu) != (ssize_t)(sizeof(pid_t) * nu))
        die("report pids");
    close(report_fd);

    say("kits filled");
    go_inert();
    return 0;
}
