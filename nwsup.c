#define _GNU_SOURCE
#include "blob.h"
#include "lids.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/filter.h>
#include <linux/landlock.h>
#include <linux/loop.h>
#include <linux/seccomp.h>
#include <sched.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sys/ioctl.h>
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
 *
 * PHASE 2: the brick is an EROFS IMAGE, not a directory. A directory was
 * bind-mounted onto itself because pivot_root needs a mount point and a
 * directory can be its own; a file cannot, so the image is attached to a
 * loop device and mounted on NW_BRICK_MNT, which dawn created.
 *
 * What that buys is the seal, and it is the reason the phase exists: a
 * directory brick is writable by the house that roots in it unless a lid
 * says otherwise, and an image is not writable at all.
 *
 * THE SEAL IS OVER-DETERMINED and no flag below is what enforces it. The
 * kernel forces read-only when either the backing fd or the loop fd is
 * O_RDONLY, and erofs has no write path. Measured: with both fds O_RDWR
 * and neither flag set, it still mounts ro and the house's write is still
 * refused. So do not read LO_FLAGS_READ_ONLY or MS_RDONLY as the
 * mechanism, and do not "control" this by removing one of them -- dropping
 * MS_RDONLY fails at the MOUNT, which is a different failure wearing the
 * right result. The control that works runs from the other side: make the
 * brick a directory bind-mounted onto itself, the pre-phase-2 code, and
 * the write succeeds. docs/plans/01.
 */
static void lid_brick(const char *brick, char *const *binds, int nbinds)
{
    /* Without this the mounts below propagate back to the machine and every
     * house sees every other house's binds. A brick that is visible outside
     * the house is not a root, it is a directory. */
    if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL) < 0)
        die("make rprivate");

    /* O_RDONLY here is half of why the seal cannot be got wrong: the kernel
     * marks the device read-only from the backing descriptor regardless of
     * the flags requested below. */
    int img = open(brick, O_RDONLY | O_CLOEXEC);
    if (img < 0) die("open brick image");

    /* LOOP_CTL_GET_FREE REPORTS A FREE INDEX; IT DOES NOT RESERVE ONE.
     * Between the GET_FREE and the CONFIGURE below, every other house doing
     * the same thing gets the SAME index, and all but one get EBUSY. Every
     * brick house runs lid_brick concurrently -- nw-spawn waits only on the
     * double-fork intermediary, not on the house reaching here -- so this is
     * the common case, not a rare interleaving.
     *
     * Measured 2026-09-12, and it was already firing in the committed suite:
     * two brick houses produced one `FAIL loop configure errno=16` on every
     * run, hidden because the default budget absorbed the restart. At 8
     * houses only 5-6 of 8 ever ran; at 64 -- NW_MAX_UNITS -- 6 to 15 of 64
     * attached on the first attempt. The city still printed
     * `closed houses_reaped=N orphans=0`, so half a city could be missing
     * and the close line looked healthy. Found by `tcb-review` and
     * `fd-auditor` independently.
     *
     * So: RETRY, re-doing GET_FREE each time, because the index is stale the
     * moment it is returned. This is a bounded check rather than a
     * design-out, and it is the honest word for it -- but the two
     * structural-looking alternatives are both worse and are refused here so
     * they are not rediscovered:
     *
     *   - Deriving the index from the unit's table index is literally
     *     BASE + i on a device number. That is bugs 9 and 13 in a third
     *     costume, and it collides with loop0..loopN that already exist and
     *     with any other tenant of the machine.
     *   - Serialising the attach in nw-spawn breaks AUTOCLEAR's anchor:
     *     whoever attaches must hold the fd until the house mounts, and
     *     nw-spawn exits at boot. Passing that fd down is a fourth
     *     descriptor and breaks invariant 5.
     *
     * THE BOUND IS DERIVED, NOT GUESSED, which matters because a guessed
     * constant is what the Liveness refusal is about. At most NW_MAX_UNITS-1
     * other houses can be contending for a device, so NW_MAX_UNITS attempts
     * is enough for every one of them to have taken theirs. Nothing here
     * sleeps: each attempt is a fresh GET_FREE, and losing means some other
     * house won that index, which is progress.
     *
     * max_loop is not a ceiling -- it is how many devices exist at module
     * load, and GET_FREE allocates past it. Measured: 4096 attached on a
     * kernel reporting 8, no ceiling found. docs/plans/01. */
    char dev[32];
    int ld = -1;
    for (int attempt = 0; attempt < NW_MAX_UNITS; attempt++) {
        int ctl = open("/dev/loop-control", O_RDWR | O_CLOEXEC);
        if (ctl < 0) die("open loop-control");
        int idx = ioctl(ctl, LOOP_CTL_GET_FREE);
        int e = errno;
        close(ctl);
        if (idx < 0) { errno = e; die("loop get free"); }

        int dn = snprintf(dev, sizeof dev, "/dev/loop%d", idx);
        if (dn < 0 || (size_t)dn >= sizeof dev) die("loop device name");
        ld = open(dev, O_RDONLY | O_CLOEXEC);
        if (ld < 0) die("open loop device");

        /* LO_FLAGS_AUTOCLEAR is the design decision here: the device frees
         * itself when its last reference goes, so there is no teardown path
         * to get wrong, no cleanup on any die() below, and nothing leaked
         * when a house is killed -- including on the die() just below, where
         * the device is already configured. Both directions are controlled:
         * drop the flag and `losetup -a` shows the device still attached
         * after the house exits, and drop it with a forced mount failure and
         * it is still attached after the _exit(72). */
        struct loop_config cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.fd = (uint32_t)img;
        cfg.info.lo_flags = LO_FLAGS_AUTOCLEAR | LO_FLAGS_READ_ONLY;
        if (ioctl(ld, LOOP_CONFIGURE, &cfg) == 0)
            break;              /* attached */
        if (errno != EBUSY) die("loop configure");
        /* Lost the race. Drop this device and ask for another index --
         * re-CONFIGUREing the same one would lose again forever. */
        close(ld);
        ld = -1;
    }
    if (ld < 0) die("loop configure: no free device");
    close(img);

    if (mount(dev, NW_BRICK_MNT, "erofs", MS_RDONLY | MS_NODEV, NULL) < 0)
        die("mount brick image");
    /* The mount holds the device now, so the descriptor can go. AUTOCLEAR
     * frees it when the mount does, which is when this namespace dies. */
    close(ld);

    for (int i = 0; i < nbinds; i++) {
        char tgt[sizeof(NW_BRICK_MNT) + NW_PATH_LEN];
        int n = snprintf(tgt, sizeof tgt, "%s%s", NW_BRICK_MNT, binds[i]);
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
    /* The new root is the MOUNTPOINT now, not the brick path: the brick is
     * a file and the house roots in what was mounted from it. */
    int newroot = open(NW_BRICK_MNT, O_DIRECTORY | O_RDONLY | O_CLOEXEC);
    if (newroot < 0) die("open brick mountpoint");
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

static pid_t child;
static volatile sig_atomic_t stopping;

static void on_term(int sig)
{
    (void)sig;
    stopping = 1;
    if (child > 0)
        kill(child, SIGTERM);
}

int main(int argc, char **argv)
{
    if (argc < 3) die("argv");
    const char *path = argv[1];
    const char *name = argv[2];
    unsigned lids = 0, budget = 0, kind = NW_KIND_LONGRUN;
    const char *e;
    if ((e = getenv("NW_LIDS"))) lids = (unsigned)atoi(e);
    if ((e = getenv("NW_BUDGET"))) budget = (unsigned)atoi(e);
    if ((e = getenv("NW_KIND"))) kind = (unsigned)atoi(e);
    /* NW_BRICK IS 64 HEX CHARACTERS, AND THIS RE-VALIDATES THEM. The sealed
     * plan carries 32 raw bytes, which cannot express a path traversal at
     * all -- but nw-spawn has to turn them into text to cross an env var,
     * and text is exactly what the traversal class needs. nw-sup reads its
     * unit from the environment rather than from the blob (the same reason
     * it re-checks NEWNS below), so without this check a hand-set NW_BRICK
     * of "../../etc" would compose a path out of the brick directory and
     * the phase-3 argument would be false in the one place it matters.
     *
     * Fixed length and a closed alphabet: no separator can appear, no
     * relative component can appear, and the composed path is under
     * NW_BRICK_DIR by construction. */
    const char *hex = getenv("NW_BRICK");
    char brickbuf[sizeof(NW_BRICK_DIR) + 1 + NW_BRICK_HEX
                  + sizeof(NW_BRICK_SUFFIX)];
    const char *brick = NULL;
    if (hex && hex[0]) {
        size_t hl = strlen(hex);
        if (hl != NW_BRICK_HEX) die("brick hash length");
        for (size_t k = 0; k < hl; k++)
            if (!((hex[k] >= '0' && hex[k] <= '9') ||
                  (hex[k] >= 'a' && hex[k] <= 'f')))
                die("brick hash not hex");
        int bn = snprintf(brickbuf, sizeof brickbuf, "%s/%s%s",
                          NW_BRICK_DIR, hex, NW_BRICK_SUFFIX);
        if (bn < 0 || (size_t)bn >= sizeof brickbuf) die("brick path");
        brick = brickbuf;
    }
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

    for (;;) {
        /* TERM during the previous house, or before this fork: do not
         * start another one so shutdown can finish. */
        if (stopping)
            _exit(0);
        pid_t p = fork();
        if (p < 0) die("fork house");
        if (p > 0)
            child = p;
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
        /* child = p is set before this point so a TERM that arrives
         * between fork returning and waitpid can still signal the
         * house. If stopping is already set, do not block in waitpid
         * on a house that was never asked to stop. */
        int st = 0;
        if (stopping) {
            if (p > 0)
                kill(p, SIGTERM);
            if (waitpid(p, &st, 0) < 0) die("wait house");
            child = 0;
            _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 0);
        }
        if (waitpid(p, &st, 0) < 0) die("wait house");
        child = 0;

        /* Same TERM that PID 1 sent to start shutdown. Restarting here
         * races the city closing: the house comes back after it was
         * asked to stop. No extra channel — the signal is the news. */
        if (stopping)
            _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 0);

        /* Only a oneshot is finished by a clean exit. For a longrun, exit 0
         * is as unexpected as any other exit and goes to the budget: a
         * compositor that quits or a daemon that reloads itself should come
         * back, not vanish silently. (D12) */
        if (kind == NW_KIND_ONESHOT && WIFEXITED(st) && WEXITSTATUS(st) == 0)
            _exit(0);

        /* D18: budget is a hard total for the life of this supervisor.
         * There is no window. A death slower than the old window_s
         * reset the tally and never hit the cap — budget=3 window=1
         * dying every 1.2s restarted for as long as anyone watched. */
        deaths++;
        if (budget == 0 || deaths > (int)budget)
            _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 71);
        char line[80];
        snprintf(line, sizeof line, "restart %s death=%d", name, deaths);
        say(line);
    }
}
