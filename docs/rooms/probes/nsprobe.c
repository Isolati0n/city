/* nsprobe.c -- Grok's load-bearing bet, run inside the guest on the real
 * boot path rather than in a container on the host kernel.
 *
 *   "kill every process in the mntns, setns back in, write to the overlay"
 *
 * If this holds, a house is a durable set of kernel objects and restart is
 * setns + exec: no remount, no privileged resident parent per house. If it
 * fails, every house needs an unprivileged sleeper to hold the namespace.
 *
 * No unshare(1)/nsenter(1) in the guest, so it is all syscalls here.
 */
#define _GNU_SOURCE
#include <sched.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <signal.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <stdarg.h>
#include <sys/syscall.h>

/* unbuffered: the child _exit()s and printf would never flush */
static void say(const char *fmt, ...)
{
    char b[256]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}

#define R    "/nwns"
#define PIN  R "/pin"
#define MNT  R "/mnt"

static void mk(const char *p) { mkdir(p, 0755); }

int main(void)
{
    mk(R); mk(R "/lower"); mk(R "/upper"); mk(R "/work"); mk(MNT); mk(R "/d");
    int f = open(R "/lower/base", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (f >= 0) { (void)!write(f, "from the lower layer\n", 21); close(f); }
    f = open(PIN, O_WRONLY | O_CREAT, 0644); if (f >= 0) close(f);

    int rdy[2], go[2];
    if (pipe(rdy) || pipe(go)) { printf("[ns] pipe FAILED\n"); return 1; }

    pid_t kid = fork();
    if (kid == 0) {
        close(rdy[0]); close(go[1]);
        /* CONFIG_OVERLAY_FS=m on this kernel and mkboot stages only the two
         * nls modules, so nothing in a QEMU boot has ever had overlay
         * available. Load it here so the experiment can run at all. */
        {
            int mfd = open("/nw/overlay.ko", O_RDONLY);
            if (mfd < 0) say("[ns] overlay.ko missing errno=%d\n", errno);
            else {
                if (syscall(__NR_finit_module, mfd, "", 0) < 0 && errno != EEXIST)
                    say("[ns] finit_module overlay FAILED errno=%d (%s)\n", errno, strerror(errno));
                else say("[ns] overlay.ko loaded\n");
                close(mfd);
            }
        }
        if (unshare(CLONE_NEWNS) < 0) { say("[ns] unshare FAILED errno=%d (%s)\n", errno, strerror(errno)); _exit(1); }
        /* make our copy private so the mount does not propagate back */
        if (mount("none", "/", NULL, MS_REC | MS_PRIVATE, NULL) < 0)
            say("[ns] rprivate warn errno=%d (%s)\n", errno, strerror(errno));
        if (mount("overlay", MNT, "overlay", 0,
                  "lowerdir=" R "/lower,upperdir=" R "/upper,workdir=" R "/work") < 0) {
            say("[ns] overlay mount FAILED errno=%d (%s)\n", errno, strerror(errno)); _exit(1);
        }
        int a = open(MNT "/alive", O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (a >= 0) { (void)!write(a, "written while alive\n", 20); close(a); }
        say("[ns] child: overlay mounted inside its own mntns\n");
        (void)!write(rdy[1], "r", 1);
        char c; (void)!read(go[0], &c, 1);          /* hold until pinned */
        _exit(0);
    }
    close(rdy[1]); close(go[0]);
    char c; if (read(rdy[0], &c, 1) != 1) { printf("[ns] child never readied\n"); return 1; }

    char src[64]; snprintf(src, sizeof src, "/proc/%d/ns/mnt", (int)kid);
    if (mount(src, PIN, NULL, MS_BIND, NULL) < 0) {
        printf("[ns] PIN FAILED errno=%d (%s)\n", errno, strerror(errno));
        kill(kid, SIGKILL); return 1;
    }
    printf("[ns] pinned %s -> %s\n", src, PIN);

    (void)!write(go[1], "g", 1);
    kill(kid, SIGKILL);
    int st; waitpid(kid, &st, 0);
    printf("[ns] every process in that namespace is dead\n");

    int nfd = open(PIN, O_RDONLY);
    if (nfd < 0) { printf("[ns] open pin FAILED errno=%d\n", errno); return 1; }
    if (setns(nfd, CLONE_NEWNS) < 0) {
        printf("[ns] SETNS FAILED errno=%d (%s)\n", errno, strerror(errno));
        printf("[ns] VERDICT: namespace did NOT survive — houses need a holder process\n");
        return 1;
    }
    printf("[ns] setns ok — re-entered the dead namespace\n");

    char buf[64] = {0};
    int r = open(MNT "/alive", O_RDONLY);
    if (r >= 0) { (void)!read(r, buf, sizeof buf - 1); close(r); }
    buf[strcspn(buf, "\n")] = 0;
    printf("[ns] read through the overlay: '%s'\n", buf);

    int w = open(MNT "/after", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (w < 0) { printf("[ns] WRITE AFTER DEATH FAILED errno=%d (%s)\n", errno, strerror(errno)); return 1; }
    (void)!write(w, "written after every process died\n", 33); close(w);
    printf("[ns] write after death: ok\n");
    printf("[ns] VERDICT: namespace SURVIVED with zero processes; restart can be setns+exec\n");
    return 0;
}
