#define _GNU_SOURCE
#include "blob.h"
#include "lids.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/filter.h>
#include <linux/landlock.h>
#include <linux/seccomp.h>
#include <sched.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static void die(const char *s)
{
    char b[160];
    int n = snprintf(b, sizeof b, "[nw-sup] FAIL %s errno=%d\n", s, errno);
    if (n > 0) { ssize_t r = write(2, b, (size_t)n); (void)r; }
    _exit(72);
}

static void say(const char *s)
{
    char b[160];
    int n = snprintf(b, sizeof b, "[nw-sup] %s\n", s);
    if (n > 0) { ssize_t r = write(2, b, (size_t)n); (void)r; }
}

static int sys_landlock_create_ruleset(struct landlock_ruleset_attr *a, size_t sz, uint32_t flags)
{
    return (int)syscall(__NR_landlock_create_ruleset, a, sz, flags);
}

static int sys_landlock_add_rule(int fd, enum landlock_rule_type t, const void *u, uint32_t flags)
{
    return (int)syscall(__NR_landlock_add_rule, fd, t, u, flags);
}

static int sys_landlock_restrict_self(int fd, uint32_t flags)
{
    return (int)syscall(__NR_landlock_restrict_self, fd, flags);
}

static void lid_netns(void)
{
    if (unshare(CLONE_NEWNET) < 0)
        die("unshare net");
    say("lid newnet");
}

static void lid_newns(void)
{
    if (unshare(CLONE_NEWNS) < 0)
        die("unshare ns");
    say("lid newns");
}

static void lid_landlock(const char *exec_path)
{
    struct landlock_ruleset_attr attr = {
        .handled_access_fs =
            LANDLOCK_ACCESS_FS_EXECUTE |
            LANDLOCK_ACCESS_FS_READ_FILE |
            LANDLOCK_ACCESS_FS_READ_DIR
    };
    int abi = sys_landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION);
    if (abi < 0) {
        say("landlock unavailable");
        return;
    }
    int rfd = sys_landlock_create_ruleset(&attr, sizeof attr, 0);
    if (rfd < 0) {
        say("landlock ruleset skipped");
        return;
    }
    int pathfd = open(exec_path, O_PATH | O_CLOEXEC);
    if (pathfd >= 0) {
        struct landlock_path_beneath_attr pb = {
            .allowed_access = LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_READ_FILE,
            .parent_fd = pathfd
        };
        sys_landlock_add_rule(rfd, LANDLOCK_RULE_PATH_BENEATH, &pb, 0);
        close(pathfd);
    }
    int devnull = open("/dev/null", O_PATH | O_CLOEXEC);
    if (devnull >= 0) {
        struct landlock_path_beneath_attr pb = {
            .allowed_access = LANDLOCK_ACCESS_FS_READ_FILE,
            .parent_fd = devnull
        };
        sys_landlock_add_rule(rfd, LANDLOCK_RULE_PATH_BENEATH, &pb, 0);
        close(devnull);
    }
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0)
        die("nnp landlock");
    if (sys_landlock_restrict_self(rfd, 0) < 0)
        say("landlock restrict skipped");
    else
        say("lid landlock");
    close(rfd);
}

static long long now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static pid_t child;

static void on_term(int sig)
{
    (void)sig;
    if (child > 0)
        kill(child, SIGTERM);
}

int main(int argc, char **argv)
{
    if (argc < 3) die("argv");
    const char *path = argv[1];
    const char *name = argv[2];
    unsigned lids = 0, budget = 0, window_s = 2, critical = 0;
    const char *e;
    if ((e = getenv("NW_LIDS"))) lids = (unsigned)atoi(e);
    if ((e = getenv("NW_BUDGET"))) budget = (unsigned)atoi(e);
    if ((e = getenv("NW_WINDOW"))) window_s = (unsigned)atoi(e);
    if ((e = getenv("NW_CRITICAL"))) critical = (unsigned)atoi(e);

    /* Stay. Isolation applies to the house child, not to wait/restart. */
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);

    int deaths = 0;
    long long win0 = now_ms();

    for (;;) {
        pid_t p = fork();
        if (p < 0) die("fork house");
        if (p == 0) {
            if (lids & NW_LID_NEWNET) lid_netns();
            if (lids & NW_LID_NEWNS) lid_newns();
            if (lids & NW_LID_LANDLOCK) lid_landlock(path);
            if (lids & NW_LID_SECCOMP) {
                if (nw_apply_house_seccomp() < 0) die("house seccomp");
                say("lid seccomp");
            }
            char *av[] = { (char *)name, NULL };
            execv(path, av);
            die("exec house");
        }
        child = p;
        int st = 0;
        if (waitpid(p, &st, 0) < 0) die("wait house");
        child = 0;

        if (WIFEXITED(st) && WEXITSTATUS(st) == 0)
            _exit(0);

        if (critical)
            _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 71);

        long long t = now_ms();
        if (t - win0 > (long long)window_s * 1000) {
            deaths = 0;
            win0 = t;
        }
        deaths++;
        if (budget == 0 || deaths > (int)budget)
            _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 71);
        char line[80];
        snprintf(line, sizeof line, "restart %s death=%d", name, deaths);
        say(line);
    }
}
