#define _GNU_SOURCE
#include "blob.h"

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/signalfd.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static void say(const char *s, const char *a)
{
    char b[256];
    int n = snprintf(b, sizeof b, "[nw-root] %s%s%s\n", s, a ? " " : "", a ? a : "");
    if (n > 0) { ssize_t r = write(2, b, (size_t)n); (void)r; }
}

static void halt_now(const char *why)
{
    say("HALT:", why);
    _exit(70);
}

static long long now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static char *dir_of_self(void)
{
    static char buf[512];
    ssize_t n = readlink("/proc/self/exe", buf, sizeof buf - 1);
    if (n <= 0) return ".";
    buf[n] = 0;
    char *sl = strrchr(buf, '/');
    if (sl) *sl = 0;
    return buf;
}

struct house {
    char  name[NW_NAME_LEN];
    pid_t pid;
    int   log_r, log_w;
    pid_t logger;
};

static struct house houses[NW_MAX_UNITS];
static uint32_t n_houses;
static pid_t spawner;
static int orphans_reaped;
static int houses_reaped;
static int shutting_down;

static void reap_all(int block)
{
    int flags = block ? 0 : WNOHANG;
    for (;;) {
        int st = 0;
        pid_t p = waitpid(-1, &st, flags);
        if (p <= 0) break;
        int known = 0;
        for (uint32_t i = 0; i < n_houses; i++) {
            if (houses[i].pid == p) {
                houses[i].pid = 0;
                houses_reaped++;
                known = 1;
                char b[80];
                snprintf(b, sizeof b, "%.31s status=%d", houses[i].name, st);
                say("house exit", b);
                /* Nothing a house does halts the city. Only two things do:
                 * the plan fails validation at boot, or PID 1 itself dies.
                 * See HISTORY.md section 19. */
            }
            if (houses[i].logger == p) {
                houses[i].logger = 0;
                known = 1;
            }
        }
        if (!known) {
            orphans_reaped++;
            char b[32];
            snprintf(b, sizeof b, "pid=%d", (int)p);
            say("orphan", b);
        }
        flags = WNOHANG;
    }
}

static void shutdown_city(void)
{
    shutting_down = 1;
    say("shutdown", "TERM houses");
    for (uint32_t i = n_houses; i-- > 0; ) {
        if (houses[i].pid > 0) kill(houses[i].pid, SIGTERM);
    }
    long long t0 = now_ms();
    while (now_ms() - t0 < 400) {
        reap_all(0);
        int live = 0;
        for (uint32_t i = 0; i < n_houses; i++)
            if (houses[i].pid > 0) live++;
        if (!live) break;
        poll(NULL, 0, 20);
    }
    for (uint32_t i = 0; i < n_houses; i++) {
        if (houses[i].pid > 0) kill(houses[i].pid, SIGKILL);
    }
    if (spawner > 0) {
        /* Only reachable if boot aborted before the spawner was reaped. */
        kill(spawner, SIGKILL);
        pid_t p = spawner;
        spawner = 0;
        int st;
        waitpid(p, &st, 0);
    }
    for (uint32_t i = 0; i < n_houses; i++) {
        if (houses[i].logger > 0) kill(houses[i].logger, SIGKILL);
    }
    reap_all(0);
    char b[80];
    snprintf(b, sizeof b, "houses_reaped=%d orphans=%d", houses_reaped, orphans_reaped);
    say("closed", b);
    _exit(0);
}

static void spawn_logger(uint32_t i)
{
    pid_t p = fork();
    if (p < 0) halt_now("logger fork");
    if (p == 0) {
        close(houses[i].log_w);
        for (uint32_t j = 0; j < n_houses; j++) {
            if (j == i) continue;
            close(houses[j].log_r);
            close(houses[j].log_w);
        }
        char prefix[64];
        int pn = snprintf(prefix, sizeof prefix, "[%.31s] ", houses[i].name);
        char buf[256];
        for (;;) {
            ssize_t n = read(houses[i].log_r, buf, sizeof buf);
            if (n <= 0) break;
            ssize_t w1 = write(2, prefix, (size_t)pn);
            ssize_t w2 = write(2, buf, (size_t)n);
            ssize_t w3 = 0;
            if (buf[n - 1] != '\n') w3 = write(2, "\n", 1);
            (void)w1; (void)w2; (void)w3;
        }
        _exit(0);
    }
    houses[i].logger = p;
    close(houses[i].log_r);
    houses[i].log_r = -1;
}

static int run_rescue(const char *slot)
{
    char path[512];
    snprintf(path, sizeof path, "%s/nw-rescue", slot);
    say("rescue slot", path);
    pid_t p = fork();
    if (p < 0) halt_now("rescue fork");
    if (p == 0) {
        execl(path, "nw-rescue", (char *)0);
        halt_now("exec rescue");
    }
    int st = 0;
    waitpid(p, &st, 0);
    _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 3);
}

/* Read the live slot name from <slots>/current and build that slot's path.
 *
 * This is the one place PID 1 reads text, and it is at boot, in the same
 * phase as loading the blob -- not "after start", which is what invariant 1
 * forbids. It is bounded and validating rather than parsing: at most
 * NW_NAME_LEN bytes, and every byte must be in [A-Za-z0-9_-]. A name with a
 * slash or a dot cannot get through, so the result cannot escape <slots>.
 *
 * Justified per the TCB rule because the alternative is worse: before this,
 * slots/current was written by `make stage` and read by nothing, while PID 1
 * took --slot from argv. Two sources of truth with one ignored is the shape
 * behind several past bugs, and it made A/B a directory layout rather than a
 * mechanism. PID 1 mounts nothing here and still learns nothing about
 * filesystems -- it opens a path it was handed. */
static int slot_from_current(const char *slots, char *out, size_t outsz)
{
    char cur[512];
    if (snprintf(cur, sizeof cur, "%s/current", slots) >= (int)sizeof cur)
        return -1;
    int fd = open(cur, O_RDONLY);
    if (fd < 0) return -1;
    char nm[NW_NAME_LEN];
    ssize_t n = read(fd, nm, sizeof nm - 1);
    close(fd);
    if (n <= 0) return -1;
    /* trim trailing newline or space; make_stage writes "A\n" */
    while (n > 0 && (nm[n - 1] == '\n' || nm[n - 1] == '\r' || nm[n - 1] == ' '))
        n--;
    if (n <= 0) return -1;
    nm[n] = 0;
    for (ssize_t i = 0; i < n; i++) {
        char c = nm[i];
        int ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
              || (c >= '0' && c <= '9') || c == '_' || c == '-';
        if (!ok) return -1;
    }
    if (snprintf(out, outsz, "%s/%s", slots, nm) >= (int)outsz) return -1;
    return 0;
}

int main(int argc, char **argv)
{
    int hold_ms = 800;
    const char *plan = NULL;
    const char *slot = NULL;
    const char *slots = NULL;
    const char *rescue = NULL;
    int kill_spawner_test = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--hold-ms") && i + 1 < argc)
            hold_ms = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--kill-spawner"))
            kill_spawner_test = 1;
        else if (!strcmp(argv[i], "--slot") && i + 1 < argc)
            slot = argv[++i];
        else if (!strcmp(argv[i], "--slots") && i + 1 < argc)
            slots = argv[++i];
        else if (!strcmp(argv[i], "--rescue") && i + 1 < argc)
            rescue = argv[++i];
        else if (argv[i][0] != '-')
            plan = argv[i];
    }

    if (rescue && !plan && !slot && !slots)
        return run_rescue(rescue);

    /* Precedence, most explicit first, and it is deliberate:
     *   --plan FILE   an exact blob; wins over everything.
     *   --slot DIR    an exact slot directory; overrides the live slot.
     *   --slots DIR   the normal boot path: DIR/current names the live slot.
     * dawn passes --slots. --slot survives only as an explicit override for
     * the harness and for rescue, and when both are given --slot wins so that
     * an operator can boot a slot that is not the current one without
     * rewriting the file that records which slot is current. */
    char slotbuf[384];
    if (!plan && !slot && slots) {
        if (slot_from_current(slots, slotbuf, sizeof slotbuf) < 0)
            halt_now("slots/current");
        slot = slotbuf;
        say("live slot", slot);
    }

    char planbuf[512];
    if (slot && !plan) {
        if (snprintf(planbuf, sizeof planbuf, "%s/plan.blob", slot)
            >= (int)sizeof planbuf)
            halt_now("slot path too long");
        plan = planbuf;
    }
    if (!plan) halt_now("no plan");

    sigset_t mask;
    sigemptyset(&mask);
    sigaddset(&mask, SIGCHLD);
    sigaddset(&mask, SIGTERM);
    sigaddset(&mask, SIGINT);
    sigaddset(&mask, SIGHUP);
    sigaddset(&mask, SIGPIPE);
    sigprocmask(SIG_BLOCK, &mask, NULL);

    int sfd = signalfd(-1, &mask, SFD_CLOEXEC);
    if (sfd < 0) halt_now("signalfd");

    int fd = open(plan, O_RDONLY);
    if (fd < 0) halt_now("open plan");
    struct stat st;
    if (fstat(fd, &st) < 0) halt_now("stat plan");
    /* NW_BLOB_MAX, not a hand-written ceiling: this and nwcheck_main.c
     * disagreed by a factor of sixteen, and neither matched what the
     * format permits. blob.h computes it. */
    if (st.st_size <= 0 || (uint32_t)st.st_size > NW_BLOB_MAX)
        halt_now("plan size");
    static unsigned char blob[NW_BLOB_MAX];
    ssize_t n = read(fd, blob, (size_t)st.st_size);
    close(fd);
    if (n != st.st_size) halt_now("plan read");

    int chk = nw_check(blob, (uint32_t)n);
    if (chk != NW_OK) {
        say("nw-check reject:", nw_errstr(chk));
        halt_now("plan");
    }
    say("plan sealed", plan);

    const struct nw_hdr *h = nw_hdr(blob);
    const struct nw_unit *u = nw_units(blob);
    n_houses = h->n_units;
    for (uint32_t i = 0; i < n_houses; i++) {
        memcpy(houses[i].name, u[i].name, NW_NAME_LEN);
        houses[i].pid = 0;
        int pfd[2];
        if (pipe2(pfd, O_CLOEXEC) < 0) halt_now("log pipe");
        houses[i].log_r = pfd[0];
        houses[i].log_w = pfd[1];
    }

    for (uint32_t i = 0; i < n_houses; i++)
        spawn_logger(i);

    int report[2];
    if (pipe2(report, O_CLOEXEC) < 0) halt_now("report pipe");

    spawner = fork();
    if (spawner < 0) halt_now("fork spawner");
    if (spawner == 0) {
        char *dir = dir_of_self();
        char elec[520], sup[520];
        if (snprintf(elec, sizeof elec, "%s/nw-spawn", dir) >= (int)sizeof elec)
            halt_now("path");
        if (snprintf(sup, sizeof sup, "%s/nw-sup", dir) >= (int)sizeof sup)
            halt_now("path");
        setenv("NW_SUP", sup, 1);

        char rbuf[16], nbuf[16];
        snprintf(rbuf, sizeof rbuf, "%d", report[1]);
        snprintf(nbuf, sizeof nbuf, "%u", n_houses);
        char *av[8 + NW_MAX_UNITS];
        char logstr[NW_MAX_UNITS][16];
        int a = 0;
        av[a++] = "nw-spawn";
        av[a++] = (char *)plan;
        av[a++] = rbuf;
        av[a++] = nbuf;
        for (uint32_t i = 0; i < n_houses; i++) {
            snprintf(logstr[i], sizeof logstr[i], "%d", houses[i].log_w);
            av[a++] = logstr[i];
        }
        av[a] = 0;
        close(report[0]);
        {
            int fl = fcntl(report[1], F_GETFD);
            if (fl >= 0)
                fcntl(report[1], F_SETFD, fl & ~FD_CLOEXEC);
            for (uint32_t i = 0; i < n_houses; i++) {
                fl = fcntl(houses[i].log_w, F_GETFD);
                if (fl >= 0)
                    fcntl(houses[i].log_w, F_SETFD, fl & ~FD_CLOEXEC);
            }
        }
        execv(elec, av);
        halt_now("exec spawner");
    }
    close(report[1]);
    if (kill_spawner_test) {
        say("test", "killing spawner before it reports");
        kill(spawner, SIGKILL);
    }
    for (uint32_t i = 0; i < n_houses; i++) {
        close(houses[i].log_w);
        houses[i].log_w = -1;
    }

    uint32_t nu = 0;
    if (read(report[0], &nu, sizeof nu) != (ssize_t)sizeof nu)
        halt_now("spawn report");
    if (nu != n_houses) halt_now("report count");
    pid_t pids[NW_MAX_UNITS];
    if (read(report[0], pids, sizeof(pid_t) * nu) != (ssize_t)(sizeof(pid_t) * nu))
        halt_now("spawn pids");
    close(report[0]);
    for (uint32_t i = 0; i < nu; i++)
        houses[i].pid = pids[i];

    /* The spawner is expected to exit. Reap it here, before the poll loop can
     * see it, and require a clean exit: a non-zero status or a signal means
     * boot did not complete, even though the report arrived. */
    {
        int st = 0;
        if (waitpid(spawner, &st, 0) < 0) halt_now("reap spawner");
        spawner = 0;
        if (!WIFEXITED(st) || WEXITSTATUS(st) != 0) halt_now("spawner exit");
    }

    {
        char b[96];
        snprintf(b, sizeof b, "houses=%u slot=%.63s",
                 n_houses, slot ? slot : "-");
        say("city open", b);
    }

    long long t_end = now_ms() + hold_ms;
    while (now_ms() < t_end) {
        struct pollfd p = { .fd = sfd, .events = POLLIN };
        int wait = (int)(t_end - now_ms());
        if (wait < 1) wait = 1;
        int pr = poll(&p, 1, wait);
        if (pr > 0 && (p.revents & POLLIN)) {
            struct signalfd_siginfo si;
            if (read(sfd, &si, sizeof si) == sizeof si) {
                if (si.ssi_signo == SIGCHLD)
                    reap_all(0);
                if (si.ssi_signo == SIGTERM || si.ssi_signo == SIGINT)
                    break;
            }
        } else {
            reap_all(0);
        }
    }

    shutdown_city();
    return 0;
}
