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
#include <sys/mount.h>
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

/* Pivot into the house's brick: its own root, its own libraries, its own
 * toolchain. The same move dawn makes at the system level, one layer down.
 *
 * Runs after the NEWNS unshare (it needs the private mount namespace) and
 * before Landlock and seccomp: the strict filter has no mount, no unshare
 * and no pivot_root, so a house sealed first could not pivot at all.
 */
static void lid_brick(const char *brick, char *const *binds, int nbinds)
{
    /* Without this the mounts below propagate back to the machine and every
     * house sees every other house's binds. A brick that is visible outside
     * the house is not a root, it is a directory. */
    if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL) < 0)
        die("make rprivate");

    /* pivot_root requires the new root to be a mount point, and a brick is a
     * plain directory. Binding it onto itself mounts *at* the brick; it does
     * not write into it, so the seal is untouched. */
    if (mount(brick, brick, NULL, MS_BIND | MS_REC, NULL) < 0)
        die("bind brick");

    for (int i = 0; i < nbinds; i++) {
        char tgt[NW_BRICK_LEN + NW_PATH_LEN];
        int n = snprintf(tgt, sizeof tgt, "%s%s", brick, binds[i]);
        if (n < 0 || (size_t)n >= sizeof tgt)
            die("bind target too long");
        /* The mount point must already exist inside the brick. nw-sup will
         * not mkdir into a sealed content-addressed tree to make room for a
         * mount: a missing target is a bake error and fails loudly here
         * rather than being created behind the baker's back. */
        if (mount(binds[i], tgt, NULL, MS_BIND | MS_REC, NULL) < 0)
            die("bind");
    }

    /* pivot_root(".", ".") -- new_root and put_old are the same directory.
     * The old root is left stacked on top of the new one and detached
     * through a descriptor opened beforehand. The ordinary form needs a
     * put_old directory inside the new root, which here would mean either
     * baking an empty /oldroot into every brick or mkdir'ing into a sealed
     * tree. This form needs neither. */
    int oldroot = open("/", O_DIRECTORY | O_RDONLY | O_CLOEXEC);
    if (oldroot < 0) die("open oldroot");
    int newroot = open(brick, O_DIRECTORY | O_RDONLY | O_CLOEXEC);
    if (newroot < 0) die("open brick");
    if (fchdir(newroot) < 0) die("fchdir brick");
    if (syscall(SYS_pivot_root, ".", ".") < 0) die("pivot_root");
    if (fchdir(oldroot) < 0) die("fchdir oldroot");
    if (mount(NULL, ".", NULL, MS_REC | MS_PRIVATE, NULL) < 0)
        die("oldroot rprivate");
    if (umount2(".", MNT_DETACH) < 0) die("detach oldroot");
    close(oldroot);
    close(newroot);
    if (chdir("/") < 0) die("chdir new root");
    say("lid brick");
}

/* LIDS ARE NOT ADVISORY. If a declared lid cannot be applied, this house does
 * not start. Every lid path in this file ends in die(); none of them logs and
 * continues. A house that runs unconfined while the plan says it is confined
 * is the plan lying, and invariant 6 says a lid is the thing that decides
 * what a house can do.
 *
 * THIS LID IS FOR A HOUSE IN A BRICK. That is a decision, taken 2026-09-10,
 * and it decides what the rules below grant.
 *
 * The previous ruleset granted EXECUTE|READ_FILE on the exec path and
 * READ_FILE on /dev/null, and nothing else. **It had never worked.** A
 * dynamically linked house cannot start under it -- the loader and libc are
 * unreadable, so execv returns EACCES before the house runs a line. Nobody
 * saw it because every environment it was ever exercised in lacked Landlock
 * and took the early-return path above; making that path fatal is what
 * finally surfaced it. A confinement feature that claimed to work, was never
 * run where it applies, and granted too little to start anything.
 *
 * So: grant read and execute beneath the house's own root. This runs after
 * the brick pivot, so "/" is the brick. A brick carries its own loader and
 * libraries, which is why this works for any linkage without a list of
 * library paths to guess at -- and guessing a list of paths in the TCB is the
 * fixed-descriptor-number class wearing a third costume.
 *
 * The restriction that remains is write. Nothing grants write beneath the
 * root, so **a house cannot write into its own brick** -- which is a property
 * the mount namespace never gave us, and the seal a content-addressed brick
 * is supposed to have. Declared binds get read and write: the plan already
 * says which paths are the house's to modify, so the bind table is the policy
 * input and nothing new is invented.
 *
 * Device nodes are deliberately not creatable even in a bind (MAKE_CHAR and
 * MAKE_BLOCK are handled and never granted).
 *
 * A landlock house therefore requires a brick. Without one, "/" is the
 * machine root and granting read and execute beneath it confines nothing --
 * a lid that decides nothing while claiming to, which is the defect this
 * whole function just stopped having. nwcheck.c returns NW_E_LLBRICK; this
 * file re-checks it because it reads its unit from the environment.
 */
static void ll_beneath(int rfd, const char *path, uint64_t access)
{
    int fd = open(path, O_PATH | O_CLOEXEC);
    if (fd < 0) die("landlock open");
    struct landlock_path_beneath_attr pb = {
        .allowed_access = access,
        .parent_fd = fd
    };
    if (sys_landlock_add_rule(rfd, LANDLOCK_RULE_PATH_BENEATH, &pb, 0) < 0)
        die("landlock rule");
    close(fd);
}

static void lid_landlock(char *const *binds, int nbinds)
{
    int abi = sys_landlock_create_ruleset(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION);
    if (abi < 0) die("landlock unavailable");

    /* Handle every filesystem right the kernel and this header both know, so
     * anything not granted below is denied. Masked by the ABI the kernel
     * reports rather than by a version assumed at build time: asking to
     * handle a right an older kernel does not know is EINVAL. */
    uint64_t handled =
        LANDLOCK_ACCESS_FS_EXECUTE     | LANDLOCK_ACCESS_FS_WRITE_FILE |
        LANDLOCK_ACCESS_FS_READ_FILE   | LANDLOCK_ACCESS_FS_READ_DIR   |
        LANDLOCK_ACCESS_FS_REMOVE_DIR  | LANDLOCK_ACCESS_FS_REMOVE_FILE|
        LANDLOCK_ACCESS_FS_MAKE_CHAR   | LANDLOCK_ACCESS_FS_MAKE_DIR   |
        LANDLOCK_ACCESS_FS_MAKE_REG    | LANDLOCK_ACCESS_FS_MAKE_SOCK  |
        LANDLOCK_ACCESS_FS_MAKE_FIFO   | LANDLOCK_ACCESS_FS_MAKE_BLOCK |
        LANDLOCK_ACCESS_FS_MAKE_SYM;
    if (abi >= 3) handled |= LANDLOCK_ACCESS_FS_TRUNCATE;

    const uint64_t ro = LANDLOCK_ACCESS_FS_EXECUTE
                      | LANDLOCK_ACCESS_FS_READ_FILE
                      | LANDLOCK_ACCESS_FS_READ_DIR;
    const uint64_t rw = (ro
                      | LANDLOCK_ACCESS_FS_WRITE_FILE
                      | LANDLOCK_ACCESS_FS_MAKE_REG
                      | LANDLOCK_ACCESS_FS_MAKE_DIR
                      | LANDLOCK_ACCESS_FS_MAKE_SYM
                      | LANDLOCK_ACCESS_FS_MAKE_SOCK
                      | LANDLOCK_ACCESS_FS_MAKE_FIFO
                      | LANDLOCK_ACCESS_FS_REMOVE_FILE
                      | LANDLOCK_ACCESS_FS_REMOVE_DIR
                      | LANDLOCK_ACCESS_FS_TRUNCATE) & handled;

    struct landlock_ruleset_attr attr = { .handled_access_fs = handled };
    int rfd = sys_landlock_create_ruleset(&attr, sizeof attr, 0);
    if (rfd < 0) die("landlock ruleset");

    ll_beneath(rfd, "/", ro);
    for (int i = 0; i < nbinds; i++)
        ll_beneath(rfd, binds[i], rw);

    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0)
        die("nnp landlock");
    if (sys_landlock_restrict_self(rfd, 0) < 0)
        die("landlock restrict");
    close(rfd);
    say("lid landlock");
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
    unsigned lids = 0, budget = 0, window_s = 2, kind = NW_KIND_LONGRUN;
    const char *e;
    if ((e = getenv("NW_LIDS"))) lids = (unsigned)atoi(e);
    if ((e = getenv("NW_BUDGET"))) budget = (unsigned)atoi(e);
    if ((e = getenv("NW_WINDOW"))) window_s = (unsigned)atoi(e);
    if ((e = getenv("NW_KIND"))) kind = (unsigned)atoi(e);
    const char *brick = getenv("NW_BRICK");
    if (brick && !brick[0]) brick = NULL;
    /* Landlock grants beneath the house's root, which is only a restriction
     * if that root is a brick. nw-check returns NW_E_LLBRICK; re-checked here
     * because nw-sup reads its unit from the environment. */
    if ((lids & NW_LID_LANDLOCK) && !brick) die("landlock without brick");
    char *binds[NW_MAX_BINDS];
    int nbinds = 0;
    if (brick) {
        /* nw-check rejects a brick house without NEWNS (NW_E_BRICKNS), so
         * this cannot happen from a sealed plan. It is checked anyway
         * because nw-sup reads its unit from the environment, and pivoting
         * without a private namespace would repoint the machine's root. */
        if (!(lids & NW_LID_NEWNS)) die("brick without newns");
        if ((e = getenv("NW_NBINDS"))) nbinds = atoi(e);
        if (nbinds < 0 || nbinds > NW_MAX_BINDS) die("nbinds");
        for (int i = 0; i < nbinds; i++) {
            char k[24];
            snprintf(k, sizeof k, "NW_BIND_%d", i);
            char *v = getenv(k);
            if (!v || !v[0]) die("missing bind");
            binds[i] = v;
        }
    }

    /* nw-spawn blocks every signal before its first fork, and a signal mask
     * survives both fork and exec -- so without this the supervisor and every
     * house start fully masked. Installing a handler on a blocked signal does
     * nothing: it stays pending and never runs. That made on_term below dead
     * code and meant graceful shutdown did not exist anywhere: PID 1 sent
     * TERM, nothing answered, and the grace window expired into SIGKILL.
     * Clear the mask before installing anything. (D11) */
    sigset_t empty;
    sigemptyset(&empty);
    sigprocmask(SIG_SETMASK, &empty, NULL);

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
            if (brick) lid_brick(brick, binds, nbinds);
            if (lids & NW_LID_LANDLOCK) lid_landlock(binds, nbinds);
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

        /* Only a oneshot is finished by a clean exit. For a longrun, exit 0
         * is as unexpected as any other exit and goes to the budget: a
         * compositor that quits or a daemon that reloads itself should come
         * back, not vanish silently. (D12) */
        if (kind == NW_KIND_ONESHOT && WIFEXITED(st) && WEXITSTATUS(st) == 0)
            _exit(0);

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
