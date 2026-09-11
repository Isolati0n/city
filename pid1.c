#define _GNU_SOURCE
#include "blob.h"

#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/reboot.h>
#include <sys/signalfd.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <linux/reboot.h>

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
                /* NW_NAME_LEN, not a hand-written precision. "%.31s" was
                  * NW_NAME_LEN - 1 written out at three sites with zero
                  * slack: at NW_NAME_LEN = 33 two distinct houses -- legal,
                  * not duplicates, separate pipes -- log under identical
                  * names, and every line either produces is attributed to
                  * whichever one the reader guesses. Silent wrong routing
                  * on the channel the suite reads to decide which unit did
                  * what, which is bug 13's shape moved from descriptors to
                  * labels. %s is safe because name_ok already guaranteed a
                  * terminator inside the field. fd-auditor. */
                char b[NW_NAME_LEN + 48];
                snprintf(b, sizeof b, "%.*s status=%d", NW_NAME_LEN - 1,
                         houses[i].name, st);
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

/* One bound for the whole shutdown, not one per stage and not grace x
   units. Used twice below: once waiting for houses to exit, once
   waiting for the loggers to drain. Both waits are concurrent over all
   units, so the total stays bounded by two grace periods regardless of
   how many houses there are. */
#define NW_GRACE_MS 400

static void shutdown_city(void)
{
    shutting_down = 1;
    say("shutdown", "TERM houses");
    for (uint32_t i = n_houses; i-- > 0; ) {
        if (houses[i].pid > 0) kill(houses[i].pid, SIGTERM);
    }
    long long t0 = now_ms();
    while (now_ms() - t0 < NW_GRACE_MS) {
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
    /* LET THE LOGGERS DRAIN, then kill what is left. SIGKILLing a
     * reader with data still in its pipe discards it, and the last
     * thing a house writes is the thing worth keeping -- nw-sup's
     * death count is a hard total, so a house that exhausts it stays
     * down until reboot, which was only acceptable because that is
     * visible.
     *
     * This used to be an unconditional SIGKILL here, racing the drain.
     * It won at small sizes and lost at large ones: measured by
     * `tcb-review` at 2500 final lines (~53 KiB), three runs relayed
     * 2500, 2433 and 2451, and the loss was a CONTIGUOUS TAIL -- the
     * end of the output, which is the part that says why the machine
     * is going down. At five lines it never lost anything, which is
     * exactly the size the suite pinned.
     *
     * No new constant and no new mechanism: PID 1 closed its own write
     * ends at boot, so once the last house is reaped the logger reads
     * EOF and exits by itself. This waits for that, bounded by the same
     * grace period already used above and concurrently across all
     * units, so shutdown stays bounded by the grace period rather than
     * grace x units. SIGKILL becomes the deadline action instead of the
     * first one. */
    long long t1 = now_ms();
    while (now_ms() - t1 < NW_GRACE_MS) {
        reap_all(0);
        int live = 0;
        for (uint32_t i = 0; i < n_houses; i++)
            if (houses[i].logger > 0) live++;
        if (!live) break;
        poll(NULL, 0, 20);
    }
    for (uint32_t i = 0; i < n_houses; i++) {
        if (houses[i].logger > 0) kill(houses[i].logger, SIGKILL);
    }
    reap_all(0);
    char b[80];
    snprintf(b, sizeof b, "houses_reaped=%d orphans=%d", houses_reaped, orphans_reaped);
    say("closed", b);
    /* Production end: power off. Do not return — returning is
     * Attempted to kill init. reboot(RB_POWER_OFF) on real hardware
     * does not return.
     *
     * Measured 2026-09-11 inside unshare --pid --fork (the suite):
     * reboot() does not return there either; the pid namespace is
     * zapped and the unshare parent exits 130 (SIGINT). Same shape
     * as unshare --mount: the lab syscall is not the machine syscall.
     * The suite accepts 130 after "closed", not as a second shutdown
     * mode. A probe run *outside* a pid namespace powered the
     * sandbox off.
     *
     * getpid() == 1 is the mechanical rule. A comment that says
     * "never call this except as PID 1" is the failure mode this
     * codebase keeps losing to. Inside the suite the child is PID 1
     * of its namespace, so the guard passes and reboot still zaps
     * the ns. Outside one, a stray call now HALTs instead of
     * powering the host off.
     *
     * sync(2) first. reboot(RB_POWER_OFF) does not flush the page
     * cache: kernel_power_off runs notifiers, device_shutdown and
     * machine_power_off. systemd / busybox / util-linux halt all
     * sync first. Without this every clean shutdown is an unclean
     * ext4. The lab cannot see it: reboot inside a pid ns only
     * zaps the ns. */
    if (getpid() != 1)
        halt_now("reboot: not PID 1");
    sync();
    if (reboot(RB_POWER_OFF) < 0)
        halt_now("reboot failed");
}

static void spawn_logger(uint32_t i)
{
    pid_t p = fork();
    if (p < 0) halt_now("logger fork");
    if (p == 0) {
        /* Logger is a long-lived fork, not an exec. It must not keep
         * PID 1's blocked TERM/INT: a later drain pass that SIGTERMs
         * the logger would otherwise sit pending forever (D11's
         * shape). Default action is terminate; shutdown still
         * SIGKILLs. Unblock here so that pass is not a trap. */
        sigset_t allow;
        sigemptyset(&allow);
        sigaddset(&allow, SIGTERM);
        sigaddset(&allow, SIGINT);
        sigprocmask(SIG_UNBLOCK, &allow, NULL);
        /* OWN PROCESS GROUP, so a signal aimed at the CITY cannot take
         * the loggers with it. Unblocking TERM just above is what makes
         * a future drain pass possible (D11: a blocked TERM makes that
         * pass a silent no-op) and it is also what makes a logger die on
         * a group-directed TERM, where the default action is terminate.
         * Measured, 6 runs each: a TERM to PID 1 alone relays 5 of a
         * house's 5 final lines; the same TERM sent to the process group
         * relays 0 of 5. The house still writes them -- its reader is
         * simply gone.
         *
         * That matters beyond tidiness. nw-sup's death count is a hard
         * total (invariant 4), so a house that exhausts it stays down
         * until reboot, and that was only acceptable because the death
         * is VISIBLE. Discard the last lines and it is a black screen
         * with no explanation, which is the property that made a hard
         * total unsafe before.
         *
         * setpgid, not SIG_IGN and not re-blocking: both of those would
         * survive the group signal by making an explicit TERM do nothing
         * too, which is D11's exact shape and is laid directly across
         * the natural fix. Here the signal never arrives, while
         * `kill(logger, SIGTERM)` from a drain pass still works.
         *
         * Not reachable on real hardware as far as this can be shown:
         * nothing this tree ships sends a group-directed signal, and a
         * bootloader handoff places nothing above PID 1 to send one.
         * The grep that establishes the first half is in
         * .claude/rules/runtime.md and deliberately NOT written out
         * here -- a comment naming the tokens it greps for is a hit for
         * its own check, which is how invariant 1's `mount` check got
         * weakened to "returns only comments" and stopped being
         * decisive. Keep the lexical check in the brief, where the
         * source grep cannot see it.
         * (This said "PID 1 there is its own session", which nothing
         * establishes: PID 1 inherits its session from the kernel and
         * every descendant stays in it. Right conclusion, unbacked
         * premise; `tcb-review`.) It IS
         * reachable in the lab and under any supervisor that signals a
         * group, and the cost of being wrong is silence.
         *
         * PRECONDITION, because a console is a plausible next change
         * and the suite can never see it: this is safe while PID 1 has
         * no controlling terminal. Give it one and two things flip at
         * once. A `^C` becomes a kernel-generated group signal, which
         * makes this fix load-bearing -- and the loggers, no longer in
         * the foreground group, become subject to SIGTTOU on write(2)
         * if TOSTOP is set. SIGTTOU is not in the set blocked below,
         * so the default action applies and the logger STOPS: the pipe
         * fills, the house blocks in write forever, and freeze
         * detection is refused by design, so nothing notices.
         * Demonstrated standalone on a pty by `tcb-review`, same
         * program either side, only this call differing. If a console
         * lands, block SIGTTOU/SIGTTIN here. */
        setpgid(0, 0);
        close(houses[i].log_w);
        for (uint32_t j = 0; j < n_houses; j++) {
            if (j == i) continue;
            close(houses[j].log_r);
            close(houses[j].log_w);
        }
        char prefix[NW_NAME_LEN + 4];
        int pn = snprintf(prefix, sizeof prefix, "[%.*s] ",
                          NW_NAME_LEN - 1, houses[i].name);
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
    /* 0 is production: poll until SIGTERM/SIGINT, then shutdown_city
     * which reboot(RB_POWER_OFF)s and does not return.
     * --hold-ms N is the same loop with a deadline. One body. Splitting
     * the loops was how --hold-ms 800 reached production and panicked.
     * N must be a positive decimal. atoi("-1") and atoi("foo") used
     * to become 0 and take the forever loop with no diagnostic. */
    int hold_ms = 0;
    const char *plan = NULL;
    const char *slot = NULL;
    const char *slots = NULL;
    const char *rescue = NULL;
    int kill_spawner_test = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--hold-ms") && i + 1 < argc) {
            const char *s = argv[++i];
            int v = 0;
            if (!s || !s[0])
                halt_now("hold-ms");
            for (const char *p = s; *p; p++) {
                if (*p < '0' || *p > '9')
                    halt_now("hold-ms");
                int next = v * 10 + (*p - '0');
                if (next < v)
                    halt_now("hold-ms");
                v = next;
            }
            if (v <= 0)
                halt_now("hold-ms");
            hold_ms = v;
        }
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

    int fd = open(plan, O_RDONLY);
    if (fd < 0) halt_now("open plan");
    struct stat st;
    if (fstat(fd, &st) < 0) halt_now("stat plan");
    /* NW_BLOB_MAX, not a hand-written ceiling: this and nwcheck_main.c
     * disagreed by a factor of sixteen, and neither matched what the
     * format permits. blob.h computes it.
     *
     * Compared in off_t, NOT through a cast to uint32_t. The cast was here
     * for one day and it truncated: st_size is 64-bit, so every size in
     * [2^32, 2^32 + NW_BLOB_MAX] -- and the same window at every 4 GiB
     * multiple -- compared small and was accepted. A 4 GiB sparse file made
     * PID 1 abort inside read(), which on a real boot is `Attempted to kill
     * init`, where the code it replaced printed HALT: plan size. The abort
     * was luck: the distro predefines _FORTIFY_SOURCE, and without it the
     * same source silently wrote 3372 bytes past this object and then
     * halted naming `plan read`. Found by tcb-review, reproduced. */
    if (st.st_size <= 0 || st.st_size > (off_t)NW_BLOB_MAX)
        halt_now("plan size");
    /* One byte more than any legal blob: see NW_BLOB_BUF. */
    static unsigned char blob[NW_BLOB_BUF];
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

    /* After the loggers exist. signalfd is SFD_CLOEXEC, which does
     * nothing for a child that never execs; creating it first left
     * every logger holding PID 1's signalfd for the life of the
     * machine. Signals are already blocked, so anything raised in
     * the gap stays pending and lands when this fd is created. */
    int sfd = signalfd(-1, &mask, SFD_CLOEXEC);
    if (sfd < 0) halt_now("signalfd");

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

    /* One loop. hold_ms only changes the poll timeout. SIGTERM, SIGINT
     * and a lab deadline all take the same break into shutdown_city. */
    long long t_end = hold_ms > 0 ? now_ms() + hold_ms : 0;
    for (;;) {
        struct pollfd p = { .fd = sfd, .events = POLLIN };
        int wait = -1;
        if (hold_ms > 0) {
            long long left = t_end - now_ms();
            if (left <= 0)
                break;
            wait = left > 1000000 ? 1000000 : (int)left;
            if (wait < 1)
                wait = 1;
        }
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
}
