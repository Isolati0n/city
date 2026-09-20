/* freezeprobe.c -- cgroup.freeze as the "waiting" state for a house.
 *
 * The desktop wants a closed window to come back instantly with nothing
 * lost. Freeze-to-disk (CRIU) is the version that survives a reboot and is
 * hard. cgroup.freeze is the version that survives a session and is nearly
 * free: every task in the cgroup is stopped at a safe point, holds its
 * memory, and resumes exactly where it was.
 *
 * Tested here:
 *   1  a house's work stops when frozen                (does it actually stop)
 *   2  it resumes from where it was when thawed        (nothing lost)
 *   3  the freeze is observable                        (cgroup.events)
 *   4  a frozen house cannot be woken by a signal      (it is stopped, not
 *                                                       merely ignored)
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdarg.h>
#include <signal.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/wait.h>

#define CG "/sys/fs/cgroup/frz"

static void say(const char *fmt, ...)
{
    char b[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}
static int wrf(const char *p, const char *v)
{
    int fd = open(p, O_WRONLY);
    if (fd < 0) return -1;
    int r = (int)write(fd, v, strlen(v));
    close(fd);
    return r < 0 ? -1 : 0;
}
static void catf(const char *p, const char *label)
{
    int fd = open(p, O_RDONLY);
    if (fd < 0) { say("      %s: (unreadable %d)\n", label, errno); return; }
    char b[256] = {0}; (void)!read(fd, b, sizeof b - 1); close(fd);
    for (char *q = b; *q; q++) if (*q == '\n') *q = ' ';
    say("      %s: %s\n", label, b);
}

int main(void)
{
    mkdir("/sys/fs/cgroup", 0755);
    if (mount("cgroup2", "/sys/fs/cgroup", "cgroup2", 0, NULL) < 0 && errno != EBUSY)
        say("[frz] cgroup2 mount note errno=%d\n", errno);

    if (mkdir(CG, 0755) < 0 && errno != EEXIST) {
        say("[frz] mkdir cgroup FAILED errno=%d (%s)\n", errno, strerror(errno));
        return 1;
    }
    say("[frz] cgroup created at %s\n", CG);

    int counter = open("/nwcount", O_RDWR | O_CREAT | O_TRUNC, 0644);
    if (counter < 0) { say("[frz] counter file failed\n"); return 1; }

    pid_t kid = fork();
    if (kid == 0) {
        /* the "house": count upward forever, recording progress */
        for (unsigned long i = 1;; i++) {
            char b[32];
            int n = snprintf(b, sizeof b, "%lu\n", i);
            (void)!pwrite(counter, b, n, 0);
            for (volatile int s = 0; s < 200000; s++) { }
        }
        _exit(0);
    }

    char pidbuf[16];
    snprintf(pidbuf, sizeof pidbuf, "%d\n", (int)kid);
    if (wrf(CG "/cgroup.procs", pidbuf) < 0) {
        say("[frz] could not put the house in the cgroup errno=%d\n", errno);
        kill(kid, SIGKILL); return 1;
    }
    say("[frz] house pid %d is in the cgroup\n", (int)kid);

    static char b1[32], b2[32], b3[32];
    sleep(2);
    (void)!pread(counter, b1, sizeof b1 - 1, 0);
    say("[frz] running, counter = %s", b1);

    /* 1 -- freeze */
    if (wrf(CG "/cgroup.freeze", "1\n") < 0) {
        say("[frz] FREEZE FAILED errno=%d (%s)\n", errno, strerror(errno));
        kill(kid, SIGKILL); return 1;
    }
    usleep(300000);
    catf(CG "/cgroup.events", "cgroup.events");
    memset(b2, 0, sizeof b2);
    (void)!pread(counter, b2, sizeof b2 - 1, 0);
    say("[frz] frozen,  counter = %s", b2);
    sleep(2);
    memset(b3, 0, sizeof b3);
    (void)!pread(counter, b3, sizeof b3 - 1, 0);
    say("[frz] still frozen after 2s, counter = %s", b3);
    say("[frz] 1 did the work stop? %s\n",
        strcmp(b2, b3) == 0 ? "YES -- identical after 2 seconds" : "NO, it kept going");

    /* 4 -- a signal must not wake it */
    kill(kid, SIGCONT);
    usleep(300000);
    char b4[32] = {0};
    (void)!pread(counter, b4, sizeof b4 - 1, 0);
    say("[frz] 4 after SIGCONT while frozen: counter = %s", b4);
    say("[frz]   signal woke it? %s\n", strcmp(b3, b4) == 0 ? "NO -- genuinely stopped" : "YES");

    /* 2 -- thaw and check it resumes from where it was */
    if (wrf(CG "/cgroup.freeze", "0\n") < 0) { say("[frz] THAW FAILED %d\n", errno); }
    sleep(2);
    char b5[32] = {0};
    (void)!pread(counter, b5, sizeof b5 - 1, 0);
    say("[frz] thawed, counter = %s", b5);
    unsigned long before = strtoul(b3, NULL, 10), after = strtoul(b5, NULL, 10);
    say("[frz] 2 resumed from where it stopped? %s (%lu -> %lu)\n",
        after > before ? "YES" : "NO", before, after);

    catf(CG "/cgroup.events", "cgroup.events after thaw");

    kill(kid, SIGKILL); waitpid(kid, NULL, 0);
    say("[frz] VERDICT: freeze is %s as a 'waiting' state for a house\n",
        (strcmp(b2, b3) == 0 && after > before) ? "usable" : "NOT usable");
    return 0;
}
