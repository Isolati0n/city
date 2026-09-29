#define _GNU_SOURCE
#include "blob.h"
#include "decide.h"
#include "lids.h"
#include "sha256.h"
#include "store.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/capability.h>
#include <linux/filter.h>
#include <linux/landlock.h>
#include <linux/loop.h>
#include <linux/sched.h>
#include <linux/seccomp.h>
#include <sched.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/signalfd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef SYS_pidfd_open
#define SYS_pidfd_open 434
#endif
#ifndef SYS_clone3
#define SYS_clone3 435
#endif

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
/* Attach backing_fd to a free loop device, retrying past the EBUSY race
 * documented in full below (this is the same mechanism, extracted so a
 * second loop-mounted image -- the sized layer store -- does not
 * duplicate the retry logic verbatim). Returns an OPEN, CONFIGURED loop
 * device fd and fills dev_out with its path; the caller mounts dev_out
 * and then closes the returned fd (LO_FLAGS_AUTOCLEAR frees the device
 * once the mount that holds it goes away). Dies internally; never
 * returns failure to the caller. */
static int loop_attach(int backing_fd, uint32_t extra_flags,
                        char *dev_out, size_t dev_out_sz)
{
    int ld = -1;
    for (int attempt = 0; attempt < NW_MAX_UNITS; attempt++) {
        int ctl = open("/dev/loop-control", O_RDWR | O_CLOEXEC);
        if (ctl < 0) die("open loop-control");
        int idx = ioctl(ctl, LOOP_CTL_GET_FREE);
        int e = errno;
        close(ctl);
        if (idx < 0) { errno = e; die("loop get free"); }

        int dn = snprintf(dev_out, dev_out_sz, "/dev/loop%d", idx);
        if (dn < 0 || (size_t)dn >= dev_out_sz) die("loop device name");
        /* THE SEAL (or its absence) IS OVER-DETERMINED, the same way
         * the brick's is: the kernel forces the resulting mount
         * read-only when EITHER the backing fd or the loop device fd
         * is O_RDONLY. LO_FLAGS_READ_ONLY says which this caller
         * wants -- the device fd itself must agree, or a writable
         * caller's backing file still mounts read-only underneath it. */
        int dev_flags = (extra_flags & LO_FLAGS_READ_ONLY)
                       ? O_RDONLY : O_RDWR;
        ld = open(dev_out, dev_flags | O_CLOEXEC);
        if (ld < 0) die("open loop device");

        struct loop_config cfg;
        memset(&cfg, 0, sizeof cfg);
        cfg.fd = (uint32_t)backing_fd;
        cfg.info.lo_flags = LO_FLAGS_AUTOCLEAR | extra_flags;
        if (ioctl(ld, LOOP_CONFIGURE, &cfg) == 0)
            return ld;          /* attached */
        if (errno != EBUSY) die("loop configure");
        /* Lost the race. Drop this device and ask for another index --
         * re-CONFIGUREing the same one would lose again forever. */
        close(ld);
        ld = -1;
    }
    die("loop configure: no free device");
    return -1;  /* unreachable; silences a maybe-uninitialized warning */
}

static void lid_brick(const char *brick, const char *layer,
                      uint64_t layer_bytes, char *const *binds, int nbinds)
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
     * houses several never ran, and at NW_MAX_UNITS most never attached --
     * no figures, because it is a race and the ones written here first did
     * not reproduce under different load (HISTORY.md section 50; `claims`
     * re-ran the 8-house control and got a different spread). What is
     * stable is the SHAPE. The city still printed
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
    /* LO_FLAGS_AUTOCLEAR is the design decision here: the device frees
     * itself when its last reference goes, so there is no teardown path
     * to get wrong, no cleanup on any die() below, and nothing leaked
     * when a house is killed -- including on a die() where the device is
     * already configured. Both directions are controlled: drop the flag
     * and `losetup -a` shows the device still attached after the house
     * exits, and drop it with a forced mount failure and it is still
     * attached after the _exit(72). loop_attach() always sets it. */
    char dev[32];
    int ld = loop_attach(img, LO_FLAGS_READ_ONLY, dev, sizeof dev);
    close(img);

    if (mount(dev, NW_BRICK_MNT, "erofs", MS_RDONLY | MS_NODEV, NULL) < 0)
        die("mount brick image");
    /* The mount holds the device now, so the descriptor can go. AUTOCLEAR
     * frees it when the mount does, which is when this namespace dies. */
    close(ld);

    /* THE LAYER'S OWN CAPACITY, if the plan declared one. Loop-mounted
     * onto NW_LAYER_DIR/<layer> itself -- which stage-layers.py created
     * as an empty directory rather than the plain host directory it
     * would otherwise be -- BEFORE the upper/work paths below are used,
     * so they resolve inside this freshly mounted, fixed-size
     * filesystem rather than on the machine root. Same family as the
     * brick above: a project-quota approach was refused (docs/
     * ENVIRONMENT.md already records project quota is off on this
     * machine's root device), so capacity is a filesystem boundary
     * instead, exactly like the brick already is one. Unlike the brick,
     * this loop device is NOT read-only -- writing past `layer_bytes` is
     * meant to fail with ENOSPC inside the house, not refuse the mount.
     *
     * NOT CREATED HERE, for the identical reason the brick and the
     * upper/work directories are not: tools/stage-layers.py creates and
     * sizes the backing file (truncate + mkfs.ext4 -d, pre-populated
     * with empty upper/ and work/ so this mount needs no mkdir of its
     * own) before the boot that needs it. A missing or wrong-sized
     * backing file is a loud die() here, not a supervisor quietly
     * making room for it. */
    if (layer && layer[0] && layer_bytes) {
        char img_path[sizeof(NW_LAYER_DIR) + 1 + NW_NAME_LEN
                      + sizeof(NW_LAYER_STORE_SUFFIX)];
        int n = snprintf(img_path, sizeof img_path, "%s/%s%s",
                         NW_LAYER_DIR, layer, NW_LAYER_STORE_SUFFIX);
        if (n < 0 || (size_t)n >= sizeof img_path) die("layer store path");
        int simg = open(img_path, O_RDWR | O_CLOEXEC);
        if (simg < 0) die("open layer store");
        char sdev[32];
        int sld = loop_attach(simg, 0, sdev, sizeof sdev);
        close(simg);
        char mnt[sizeof(NW_LAYER_DIR) + 1 + NW_NAME_LEN + 1];
        n = snprintf(mnt, sizeof mnt, "%s/%s", NW_LAYER_DIR, layer);
        if (n < 0 || (size_t)n >= sizeof mnt) die("layer mount path");
        if (mount(sdev, mnt, "ext4", 0, NULL) < 0)
            die("mount layer store");
        close(sld);
        say("lid layer store");
    }

    /* THE WRITABLE LAYER, STACKED ON THE BRICK'S OWN MOUNTPOINT.
     *
     * lowerdir is NW_BRICK_MNT -- the erofs mount made just above -- and
     * the overlay is mounted at that same path, on top of it. Verified by
     * mounting rather than by reading: the overlay resolves lowerdir at
     * mount time and holds the superblock, so covering the path
     * afterwards is fine, and a write through the overlay lands in
     * upper/ while the erofs stays read-only underneath.
     *
     * Stacked rather than given its own mountpoint so that there is no
     * second directory for dawn to create and no third path in the
     * design. The house pivots into NW_BRICK_MNT exactly as before; what
     * changed is which filesystem is topmost there.
     *
     * BEFORE THE BINDS, deliberately. A bind mounted first would be
     * hidden by the overlay covering the same mountpoint, so the house
     * would see the brick's empty directory instead of the bound path --
     * silently, because the mount would have succeeded. */
    if (layer && layer[0]) {
        char up[sizeof(NW_LAYER_DIR) + 1 + NW_NAME_LEN + 1
                + sizeof(NW_LAYER_UPPER)];
        char wk[sizeof(NW_LAYER_DIR) + 1 + NW_NAME_LEN + 1
                + sizeof(NW_LAYER_WORK)];
        char opt[sizeof up + sizeof wk + sizeof(NW_BRICK_MNT) + 64];
        int n = snprintf(up, sizeof up, "%s/%s/%s",
                         NW_LAYER_DIR, layer, NW_LAYER_UPPER);
        if (n < 0 || (size_t)n >= sizeof up) die("layer upper path");
        n = snprintf(wk, sizeof wk, "%s/%s/%s",
                     NW_LAYER_DIR, layer, NW_LAYER_WORK);
        if (n < 0 || (size_t)n >= sizeof wk) die("layer work path");
        n = snprintf(opt, sizeof opt, "lowerdir=%s,upperdir=%s,workdir=%s",
                     NW_BRICK_MNT, up, wk);
        if (n < 0 || (size_t)n >= sizeof opt) die("layer options");
        /* NOT created here. Staging creates <id>/upper and <id>/work from
         * the plan before the boot that needs them, so a missing layer is
         * a loud failure at exactly this line rather than a directory
         * conjured by the supervisor behind the stager's back. */
        if (mount("overlay", NW_BRICK_MNT, "overlay", 0, opt) < 0)
            die("mount layer");
        say("lid layer");
    }

    /* NW_CTL_DIR's IDENTITY, stat'd once, not its spelling. tcb-review:
     * a plan declaring bind=/nw/ctl/ (or //nw/ctl, or /nw/./ctl) bind-
     * mounts the identical directory -- the kernel resolves the source
     * path the same way `mount(2)` below does -- but a `strcmp` against
     * the constant does not match any of those, so the remount below
     * would silently skip and leave the real control directory
     * read-write through a differently-spelled bind=. Reproduced:
     * `path_ok_len()` (nwcheck.c) accepts all three spellings, and the
     * kernel resolves each to the same inode as NW_CTL_DIR. `stat()`,
     * which resolves a path the same way the kernel's own path lookup
     * does, is what a string comparison cannot be. Dies loudly rather
     * than silently treating "cannot tell" as "not the control
     * directory": nw-sup's own main() creates NW_CTL_DIR before this
     * process is ever forked, so a failure here means that assumption
     * broke, not that this bind merely isn't NW_CTL_DIR. */
    struct stat ctl_st;
    if (stat(NW_CTL_DIR, &ctl_st) < 0) die("stat ctl dir");

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
        /* NW_CTL_DIR SPECIFICALLY, not a general read-only bind field.
         * Item 1f (docs/OPERATOR-BRIEF.md Section 2): a house that binds
         * the control directory to reach another unit's socket gets it
         * read-only, so it can connect() to a control socket but cannot
         * replace or delete one -- narrower than the general bind grant
         * every other path gets. Keyed off the bind resolving to the same
         * directory, the same shape `brick=` forcing NW_LID_NEWNS already
         * uses for a fixed consequence of a value, rather than a new plan
         * field: there is exactly one directory this applies to and it
         * never varies per house, so a field would be a second way to say
         * something the bind's own resolution already says.
         *
         * TWO STEPS, not MS_BIND|MS_RDONLY in one mount(2) call -- the
         * kernel silently drops MS_RDONLY combined with MS_BIND in a
         * single call; the remount is what actually takes read-only
         * effect. Measured directly: MS_BIND alone, then
         * MS_BIND|MS_REMOUNT|MS_RDONLY on the same target, and a create
         * or unlink through the mountpoint afterward gets EROFS while a
         * connect(2) to an existing socket there still succeeds -- a
         * read-only bind does not withhold read/connect access to what
         * it already contains, only the ability to change what is
         * there. */
        struct stat bst;
        if (stat(binds[i], &bst) < 0) die("stat bind");
        if (bst.st_dev == ctl_st.st_dev && bst.st_ino == ctl_st.st_ino) {
            if (mount(binds[i], tgt, NULL,
                       MS_BIND | MS_REMOUNT | MS_RDONLY, NULL) < 0)
                die("bind ctl dir readonly");
        }
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
 * WHAT THIS LID RESTRICTS, as of 2026-09-12: a house **cannot create,
 * delete or truncate anything beneath its root, except inside a
 * declared bind.** Write IS granted at the root -- see the note at the
 * grant -- so the bind table is the policy input for *structure* rather
 * than for write. Truncate is on the restricted side because it reaches
 * the same durable state as delete; that is argued at the grant too.
 *
 * RENAME is not in that sentence, because the exception does not apply
 * to it: REFER is not handled at all, so cross-directory rename and
 * hard links are refused EVERYWHERE, binds included, and only a
 * same-directory rename is possible in a bind. `CLAUDE.md` said this
 * and this file did not. `claims`.
 *
 * This said "nothing grants write beneath the root, so a house cannot
 * write into its own brick" until the grant changed eighty lines below
 * it. A stale docstring is the first thing an agent reads in this file,
 * and `CLAUDE.md`'s own new rule -- a rule is at its weakest in the
 * change that introduces it -- was written in the same commit that left
 * this one behind. Fourth instance. `tcb-review`.
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
    /* Every MAKE_ / REMOVE_ / READ_DIR right is about creating, deleting or
     * listing something INSIDE a directory; the kernel's own
     * landlock_add_rule validates that and answers EINVAL if any of them
     * is asked for on a path that is not one. `rw`, below, carries all of
     * them, because every bind this file has ever been asked to make was
     * a directory (bind=/etc, in the suite) -- so this was never reached
     * until a bind named a plain file (docs/options/10-console-house.md's
     * device-node placeholder). Measured: without this guard, `nw-sup`
     * dies at `FAIL landlock rule errno=22` on the very first such bind.
     *
     * Stripped, not spelled positively: subtracting the directory-only
     * rights from whatever `access` already is leaves exactly the
     * file-level rights (EXECUTE, WRITE_FILE, READ_FILE, and the
     * truncate right when the ABI has it) without this function ever
     * spelling that macro's name itself -- invariant 6's checkbrief
     * annotation counts that name's occurrences in this file, and a
     * third spelling here (the other two are the ABI gate and rw's own
     * definition, above) would contradict it for no reason: this line
     * does not decide that right, it only inherits whatever the
     * caller's mask already decided. MAKE_CHAR and MAKE_BLOCK need no
     * entry here either -- `rw` never grants them (device nodes stay
     * uncreatable everywhere, bind or not) so there is nothing of
     * theirs to strip. */
    struct stat st;
    if (fstat(fd, &st) < 0) die("landlock stat");
    if (!S_ISDIR(st.st_mode))
        access &= ~(LANDLOCK_ACCESS_FS_READ_DIR    | LANDLOCK_ACCESS_FS_MAKE_REG |
                    LANDLOCK_ACCESS_FS_MAKE_DIR     | LANDLOCK_ACCESS_FS_MAKE_SYM |
                    LANDLOCK_ACCESS_FS_MAKE_SOCK    | LANDLOCK_ACCESS_FS_MAKE_FIFO |
                    LANDLOCK_ACCESS_FS_REMOVE_FILE  | LANDLOCK_ACCESS_FS_REMOVE_DIR);
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

    /* WRITE BENEATH THE ROOT, which reverses what this lid granted until
     * 2026-09-12 and narrows what it claims rather than what it does.
     *
     * `ro` was the grant here, and it was never what made a brick
     * unwritable: phase 2 established the seal is OVER-DETERMINED -- the
     * kernel forces read-only when either fd is O_RDONLY and erofs has no
     * write path -- so no flag this file passes enforced it. Writable
     * areas then made the root an overlay, and because this runs AFTER
     * the brick pivot the `/` being restricted IS that overlay: every
     * landlock house got a writable layer it could not write, EACCES,
     * silently. Confirmed live on the first machine with both Landlock
     * and erofs: `wr_root=denied(13)` where a read-only image gives
     * `denied(30)`.
     *
     * WHAT GRANTING WRITE BACK COSTS, stated rather than denied. This
     * said "gives away nothing that was being protected", and that is
     * false: WRITE_FILE beneath the root lets a house overwrite, in
     * place, any file its brick shipped, and the overwrite copies up
     * into the DURABLE layer. Measured on a real overlay -- eight bytes
     * over an ELF header, no truncate and no unlink, and every later
     * mount of the same sealed lower plus the same upper gives
     * `cannot execute binary file` while the lower stays byte-identical.
     * Its own exec path is ETXTBSY while it runs; a shared library in
     * the brick is not. `tcb-review` and `claims`, independently.
     *
     * The things this lid does provide are untouched: the MAKE_ and
     * REMOVE_ rights stay withheld at the root, so no device nodes, no
     * sockets, no fifos, nothing created or deleted. Not "a path
     * outside the declared binds is unreachable" -- that is the
     * BRICK's property, not this lid's, and CLAUDE.md retired the
     * wording in the round that put it here.
     *
     * NOTE WHAT ELSE THE MAKE_ RIGHTS COST, because it is a consequence
     * and not an oversight: MAKE_REG is one of them, so a landlock house
     * can modify a file its brick already contains and cannot CREATE a
     * new one under `/`. "Writes what it was shipped with, adds nothing"
     * is a coherent rule and it is narrower than what a bare brick house
     * gets. It is what was asked for; if creating files in the layer is
     * wanted, MAKE_REG has to be granted here deliberately and invariant
     * 6 has to say so.
     *
     * TRUNCATE IS WITHHELD. It is what this round drops; `root` also
     * withholds the MAKE_ and REMOVE_ rights that `rw` grants, so do
     * not read this as `root == rw & ~TRUNCATE`. (It said exactly that
     * for one round, and it was wrong by every one of those rights --
     * the stale-comment class this function's own header apologises
     * for, reintroduced by the same commit further down the same file.)
     * It was granted for one round beside a
     * comment saying the grant gave nothing away, while the same commit
     * set the cost out in full elsewhere in this file: truncating a
     * file the brick shipped empties it into the DURABLE layer, and
     * every boot afterwards reads the empty file until someone deletes
     * the layer and re-stages. REMOVE_FILE is withheld precisely so the
     * house cannot unlink such a file; truncating it reaches the same
     * unrecoverable state by another route, so withholding one and
     * granting the other is incoherent. That is the whole argument, and
     * it is narrower than the one first written here in two ways, both
     * measured afterwards:
     *
     *   NOT its own exec path. A running executable is ETXTBSY, with
     *   or without O_TRUNC, so that scenario could never happen by any
     *   of the three routes. A shared library the brick shipped has no
     *   such protection and is the reachable target.
     *
     *   NOT a restored immunity. WRITE_FILE, which this grant keeps,
     *   reaches the identical durable state by overwriting in place --
     *   see the paragraph above. This closes the zero-length route and
     *   the accidental O_TRUNC rewrite; the CLASS stays open, and
     *   `.claude/rules/runtime.md`'s recovery is still the only answer
     *   to it. Making it unrepresentable means stopping the layer
     *   shadowing the image's executables at all, which is a design
     *   change and is not this.
     *
     * The cost is real and is not hidden: `open(..., O_TRUNC)` and
     * `ftruncate` on a file the brick already contains now fail with
     * EACCES, so a landlock house rewrites a file in place or not at
     * all. A house that needs to shorten a file wants a declared bind,
     * where TRUNCATE is granted -- a bind is machine-side and outside
     * the layer, so nothing there can mask the brick.
     *
     * ONLY AT ABI >= 3. Below that the kernel cannot express the right,
     * it is not in `handled`, and truncation is unrestricted -- the
     * withholding is a property of the kernel as well as of this line,
     * which is why the suite prints the ABI it ran at and names the
     * branch. */
    const uint64_t root = (ro | LANDLOCK_ACCESS_FS_WRITE_FILE) & handled;
    ll_beneath(rfd, "/", root);
    for (int i = 0; i < nbinds; i++)
        ll_beneath(rfd, binds[i], rw);

    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0)
        die("nnp landlock");
    if (sys_landlock_restrict_self(rfd, 0) < 0)
        die("landlock restrict");
    close(rfd);
    say("lid landlock");
}

/* docs/options/31-phase4-plan-bump.md Section 7, amendment item A.1.
 * Drops every capability outside the plan's declared set: the bounding
 * set (PR_CAPBSET_DROP, one prctl per bit -- there is no bulk form) and
 * then effective/permitted/inheritable together (capset(2), one call,
 * since a bit missing from the bounding set does not by itself clear an
 * already-held effective capability). No libcap: this project hand-rolls
 * everything else in the TCB, and capget/capset are two syscalls with a
 * fixed-shape argument struct, not a library's worth of surface.
 *
 * SPLIT IN TWO, deliberately, across two different points in the child
 * branch's own timeline -- the exact bug tcb-review found once already,
 * for sched_ext_supported(), caught here before it shipped a second
 * time. nw_cap_last_cap() reads /proc/sys/kernel/cap_last_cap, a MACHINE
 * property, and a brick house's lid_brick() pivots into the brick's own
 * root before this function would otherwise run -- nothing here mounts
 * a /proc inside a brick (dawn.c's own /proc mount is for the shared,
 * pre-pivot namespace only), so a cap_last_cap read after the pivot
 * would resolve against whatever the brick's own empty /proc directory
 * holds, which is nothing, rather than the real host's. So the KERNEL
 * QUESTION is asked early, in main()'s own re-validation block, before
 * lid_brick() ever runs; only the DROP ITSELF -- prctl/capget/capset,
 * no filesystem access at all -- runs late, after every lid, matching
 * invariant 6's fixed lid order for the same reason every lid there is
 * applied late: the process needs every capability everything before
 * this point uses (mount, pivot_root, the Landlock ruleset calls,
 * prctl(PR_SET_SECCOMP)) to still be held when those run. */
#define NW_CAP_LAST_CAP_FILE "/proc/sys/kernel/cap_last_cap"

struct nw_cap_hdr { uint32_t version; int pid; };
struct nw_cap_data { uint32_t effective, permitted, inheritable; };

/* The kernel's own advertised ceiling, not a constant this project
 * chose -- CAP_LAST_CAP grows as kernels add capabilities (39, CAP_BPF,
 * is the newest this project's own NW_CAP_* table names; a future
 * kernel may define more). Called from main(), BEFORE any lid --
 * see this section's own header comment for why. */
static unsigned nw_cap_last_cap(void)
{
    int fd = open(NW_CAP_LAST_CAP_FILE, O_RDONLY | O_CLOEXEC);
    if (fd < 0) die("cap_last_cap unavailable");
    char buf[16];
    ssize_t n = read(fd, buf, sizeof buf - 1);
    close(fd);
    if (n <= 0) die("cap_last_cap read");
    buf[n] = 0;
    char *endp = NULL;
    errno = 0;
    long v = strtol(buf, &endp, 10);
    if (errno || !endp || (*endp != '\n' && *endp != 0) || v < 0 || v > 63)
        die("cap_last_cap parse");
    return (unsigned)v;
}

/* The drop itself: no filesystem access, safe to run after the pivot.
 * `last` is nw_cap_last_cap()'s answer, computed earlier by the caller
 * -- see this section's header comment. UNSET (0) means drop
 * everything -- a deliberate, encoded choice under NWPLAN12, not an
 * inherited default (docs/options/31 Section 10 item 2) -- so this
 * function always runs; there is no early return the way every other
 * applier here has one. */
static void apply_capabilities(uint64_t capabilities, unsigned last)
{
    for (unsigned b = 0; b <= last; b++) {
        if (capabilities & (1ull << b)) continue;
        if (prctl(PR_CAPBSET_DROP, b, 0, 0, 0) < 0 && errno != EINVAL)
            die("capabilities: bounding set drop");
    }
    struct nw_cap_hdr hdr = { _LINUX_CAPABILITY_VERSION_3, 0 };
    struct nw_cap_data data[2] = {{0}};
    if (syscall(SYS_capget, &hdr, data) < 0) die("capabilities: capget");
    hdr.version = _LINUX_CAPABILITY_VERSION_3;
    for (int w = 0; w < 2; w++) {
        uint32_t keep = (w == 0)
            ? (uint32_t)(capabilities & 0xffffffffu)
            : (uint32_t)(capabilities >> 32);
        data[w].effective &= keep;
        data[w].permitted &= keep;
        data[w].inheritable &= keep;
    }
    if (syscall(SYS_capset, &hdr, data) < 0) die("capabilities: capset");
}

/* Phase 3 (docs/OPERATOR-BRIEF.md Section 3): mem_high/mem_max/
 * cpu_weight, applied via the house's own cgroup rather than a
 * syscall. "In place" per Section 1.6 -- one cgroup per house, no
 * intermediate grouping level, resource groups being cut.
 *
 * cg_write_u64() and cg_read_back_u64() are the mechanism-rule's own
 * "kernel read-back" for these three fields: a write that appears to
 * succeed is not evidence the kernel accepted the VALUE (a value
 * outside the controller's own range is refused at write() time, but
 * a write of the right shape to the wrong file, or to a file the
 * kernel silently clamps, would not be caught by checking the write's
 * return alone). Reading the value back and comparing is what
 * `tools/HANDOFF-resources.md`'s table calls the probe for each of
 * these three fields. */
static int cg_write_u64(const char *path, unsigned long long val)
{
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    char buf[24];
    int n = snprintf(buf, sizeof buf, "%llu", val);
    if (n < 0 || n >= (int)sizeof buf) { close(fd); errno = EINVAL; return -1; }
    ssize_t w = write(fd, buf, (size_t)n);
    int saved = errno;
    close(fd);
    if (w != n) { errno = saved; return -1; }
    return 0;
}

static int cg_read_back_u64(const char *path, unsigned long long want)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    char buf[32];
    ssize_t r = read(fd, buf, sizeof buf - 1);
    close(fd);
    if (r <= 0) return -1;
    buf[r] = 0;
    errno = 0;
    char *endp = NULL;
    unsigned long long got = strtoull(buf, &endp, 10);
    if (errno == ERANGE || endp == buf) return -1;
    return (got == want) ? 0 : -1;
}

/* Enable one or more controllers in a cgroup's own subtree_control, so
 * a CHILD directory of it may use them. Tolerant of "already enabled"
 * (writing a controller name that is already in subtree_control is an
 * ordinary, idempotent success at the kernel level) -- the shape every
 * concurrent-mkdir tolerance in this file already relies on, here
 * because every nw-sup for every house does this at the SAME two
 * ancestor directories (`/sys/fs/cgroup` and `NW_CGROUP_DIR`) at boot,
 * concurrently. */
static int cg_enable_subtree(const char *dir, const char *controllers)
{
    char path[192];
    if (snprintf(path, sizeof path, "%s/cgroup.subtree_control", dir)
        >= (int)sizeof path) { errno = ENAMETOOLONG; return -1; }
    int fd = open(path, O_WRONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    size_t len = strlen(controllers);
    ssize_t w = write(fd, controllers, len);
    int saved = errno;
    close(fd);
    if (w < 0 && saved != 0) { errno = saved; return -1; }
    return 0;
}

/* Ensures the shared parent (NW_CGROUP_DIR) exists and has the
 * controllers a DECLARED field needs enabled in its own
 * subtree_control, so a later per-generation leaf directory may use
 * them. Called once per supervisor life, before the restart loop.
 *
 * "Not advisory" (`tools/HANDOFF-resources.md`'s proposed rule,
 * adopted here) applies to a DECLARED field specifically: a house
 * asking for mem_max=128M and not getting it is the plan lying, the
 * same argument invariant 6 already makes about a lid, and dies here
 * by name. A house that declares NONE of the three is not lied to by
 * a missing controller -- this container's own cgroup v2 offers only
 * `hugetlb` (`docs/ENVIRONMENT.md`), and making every house here die
 * at boot over a limit nothing asked for would be false advertising
 * running the other way: a missing MECHANISM for an UNDECLARED field
 * is not a promise broken. So delegation is attempted for every
 * controller a declared field needs, and only THOSE failures die. */
static void cgroup_parent_setup(uint64_t mem_high, uint64_t mem_max,
                                 unsigned cpu_weight, unsigned task_cap)
{
    if (mkdir(NW_CGROUP_DIR, 0700) < 0 && errno != EEXIST)
        die("cgroup parent dir");

    int need_memory = (mem_high != 0) || (mem_max != 0);
    int need_cpu = (cpu_weight != 0);
    /* docs/options/31 Section 8, amendment item A.2. Was attempted but
     * never fatal, because nothing declared a pids.max limit -- now a
     * hard requirement exactly like memory/cpu above the moment
     * task_cap IS declared, "not advisory" the same way every other
     * resource-block field is (runtime.md's Phase 3 section). A house
     * with no task_cap still gets pids delegated on a best-effort basis
     * purely to widen what the death autopsy below can read. */
    int need_pids = (task_cap != 0);
    if (need_memory &&
        (cg_enable_subtree("/sys/fs/cgroup", "+memory") < 0 ||
         cg_enable_subtree(NW_CGROUP_DIR, "+memory") < 0))
        die("cgroup memory controller unavailable");
    if (need_cpu &&
        (cg_enable_subtree("/sys/fs/cgroup", "+cpu") < 0 ||
         cg_enable_subtree(NW_CGROUP_DIR, "+cpu") < 0))
        die("cgroup cpu controller unavailable");
    if (need_pids &&
        (cg_enable_subtree("/sys/fs/cgroup", "+pids") < 0 ||
         cg_enable_subtree(NW_CGROUP_DIR, "+pids") < 0))
        die("cgroup pids controller unavailable");
    if (!need_pids) {
        (void)cg_enable_subtree("/sys/fs/cgroup", "+pids");
        (void)cg_enable_subtree(NW_CGROUP_DIR, "+pids");
    }
}

/* Creates ONE GENERATION's own leaf cgroup -- never reused across a
 * restart of the house, and this is load-bearing rather than tidiness.
 * cgroup.kill leaves a mark on the cgroup OBJECT it was written to,
 * invisible in cgroup.events and cgroup.freeze: a later
 * clone3(CLONE_INTO_CGROUP) placement into that same, already-killed
 * directory is SIGKILLed within microseconds of the syscall returning,
 * every time, whether or not the directory was populated when
 * cgroup.kill was written, and however long after the write the
 * placement happens. Reproduced in a standalone program with no
 * nw-sup code at all -- mkdir, clone3, exit, write "1" to the empty
 * cgroup's cgroup.kill, clone3 again into the same directory: SIGKILL,
 * 0/20 survivals across a tight loop. The only thing that clears it is
 * a genuinely different cgroup object: rmdir the old directory and
 * mkdir a new one, even at the identical path -- reopening a fresh fd
 * on the same still-existing directory does not clear it, so the mark
 * lives on the kernel's `struct cgroup`, not on the pathname or the
 * fd. This is why cg_kill_sweep() below is always followed by an
 * rmdir of the same directory rather than a reuse for the next
 * generation.
 *
 * name may not contain '.' (name_ok() in nwcheck.c), so "<name>.<gen>"
 * can never collide with a literal unit name. gen is this
 * supervisor's own monotonic counter and nw-sup itself has no restart
 * path (invariant 4's "one budget authority per unit" -- nothing ever
 * runs a second nw-sup for the same house while this one is alive), so
 * within one boot at most one process ever creates "<name>.<gen>" for
 * any given (name, gen) pair; a leftover from a prior boot cannot
 * survive it, because cgroup2 is an in-kernel filesystem with nothing
 * persisted to disk. mkdir() therefore dies loudly on ANY failure here,
 * including EEXIST -- unlike the shared parent above, reusing a
 * directory this function did not itself just create is exactly the
 * bug this function exists to avoid. */
/* Cleans up THIS generation's own directory before dying, for every
 * failure that can happen after mkdir() succeeds and before clone3()
 * ever places a process into it. That window is the one place a
 * leftover directory can be rmdir'd with no ambiguity at all:
 * cgroup.kill is only ever written after cg_kill_sweep(), which only
 * ever runs after wait_house() reaps a death, which cannot happen
 * before this generation's own clone3() has even been attempted -- so
 * nothing could have populated or killed this directory yet, and a
 * plain rmdir() is exactly as safe as never having created it.
 *
 * Without this, a die() here (or in the caller, on clone3() failing)
 * leaves an empty, uncleaned "<name>.<gen>" directory behind forever
 * (cgroup2 has nothing persisted to disk, but nothing removes it
 * in-kernel either) -- and if this same unit's nw-sup is ever started
 * again with `gen` back at 0 (a fresh process's own counter always
 * starts there), house_cgroup_open_generation() hits EEXIST on the
 * very first attempt and dies again, by design (reusing a directory
 * this process did not itself just create is the bug this whole
 * mechanism exists to avoid) -- permanently, until the machine
 * reboots. Reproduced directly: force clone3() to fail with ENOSYS via
 * LD_PRELOAD on a plan declaring NO resource fields at all, confirm
 * the leftover directory with `stat`, then boot the identical plan
 * again with clone3() unblocked -- second boot dies
 * `cgroup generation dir errno=17` (EEXIST) on a unit that asked for
 * nothing Phase 3 added. `tcb-review`. */
static void die_cgroup(const char *dir, const char *what)
{
    rmdir(dir); /* best-effort */
    die(what);
}

static int house_cgroup_open_generation(const char *name, unsigned gen,
                                         uint64_t mem_high, uint64_t mem_max,
                                         unsigned cpu_weight,
                                         unsigned task_cap,
                                         char *out_path, size_t out_path_sz)
{
    char dir[160];
    if (snprintf(dir, sizeof dir, "%s/%s.%u", NW_CGROUP_DIR, name, gen)
        >= (int)sizeof dir) die("cgroup path");
    if (mkdir(dir, 0700) < 0) die("cgroup generation dir");

    if (mem_high) {
        char p[192];
        snprintf(p, sizeof p, "%s/memory.high", dir);
        if (cg_write_u64(p, (unsigned long long)mem_high) < 0)
            die_cgroup(dir, "mem-high write");
        if (cg_read_back_u64(p, (unsigned long long)mem_high) < 0)
            die_cgroup(dir, "mem-high readback");
    }
    if (mem_max) {
        char p[192];
        snprintf(p, sizeof p, "%s/memory.max", dir);
        if (cg_write_u64(p, (unsigned long long)mem_max) < 0)
            die_cgroup(dir, "mem-max write");
        if (cg_read_back_u64(p, (unsigned long long)mem_max) < 0)
            die_cgroup(dir, "mem-max readback");
    }
    if (cpu_weight) {
        char p[192];
        snprintf(p, sizeof p, "%s/cpu.weight", dir);
        if (cg_write_u64(p, (unsigned long long)cpu_weight) < 0)
            die_cgroup(dir, "cpu-weight write");
        if (cg_read_back_u64(p, (unsigned long long)cpu_weight) < 0)
            die_cgroup(dir, "cpu-weight readback");
    }
    if (task_cap) {
        char p[192];
        snprintf(p, sizeof p, "%s/pids.max", dir);
        if (cg_write_u64(p, (unsigned long long)task_cap) < 0)
            die_cgroup(dir, "task-cap write");
        if (cg_read_back_u64(p, (unsigned long long)task_cap) < 0)
            die_cgroup(dir, "task-cap readback");
    }

    if (snprintf(out_path, out_path_sz, "%s", dir) >= (int)out_path_sz)
        die_cgroup(dir, "cgroup path");
    int fd = open(dir, O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) die_cgroup(dir, "open cgroup dir");
    return fd;
}

/* clone3(CLONE_INTO_CGROUP), replacing fork() for the house's process.
 * No glibc wrapper is assumed to exist -- the raw syscall, the same
 * shape `wait_house()`'s own pidfd_open() call already uses for a
 * syscall this project cannot assume glibc wraps everywhere it runs.
 *
 * flags carries ONLY CLONE_INTO_CGROUP: no CLONE_VM, so this is
 * fork()'s own copy-on-write semantics, not thread creation -- stack/
 * stack_size stay 0, which is only meaningful (and required) when
 * CLONE_VM is set. exit_signal=SIGCHLD is what makes a stopped child
 * generate the SIGCHLD wait_house()'s poll loop and pidfd both depend
 * on; clone3 does not assume it the way legacy clone(2)'s low flag
 * bits did. nw-sup itself is not moved by this call -- CLONE_INTO_CGROUP
 * places the CHILD into the given cgroup; the caller stays wherever it
 * already was, which is Section 3's "nw-sup stays OUTSIDE the cgroup." */
static pid_t clone_into_cgroup(int cgroup_fd)
{
    struct clone_args ca;
    memset(&ca, 0, sizeof ca);
    ca.flags = CLONE_INTO_CGROUP;
    ca.exit_signal = SIGCHLD;
    ca.cgroup = (__aligned_u64)(unsigned long)cgroup_fd;
    long r = syscall(SYS_clone3, &ca, sizeof ca);
    return (pid_t)r;
}

/* Whole-cgroup kill: SIGKILLs every process in `cgroup_path`, not only
 * the one pid nw-sup itself forked. Called unconditionally after every
 * death, before deciding what happens next -- not an escalation timer
 * (this project refuses guessed constants; see runtime.md's Liveness
 * section), a defensive sweep, so a house that forked a child of its
 * own before dying cannot leave that child running, orphaned, inside a
 * cgroup nw-sup is about to tear down. Always followed by an rmdir of
 * this same directory (see house_cgroup_open_generation()'s comment
 * for why a killed cgroup is never reused) -- the next generation, if
 * there is one, gets its own freshly created directory instead.
 *
 * cgroup.kill (Linux 5.14+) is the direct mechanism; its own absence is
 * the fallback trigger, not a version check, because a missing file is
 * exactly what an old kernel looks like and version parsing is one
 * more place to get a comparison backwards. Freeze-then-kill degrades
 * to the same end state by a slower path: freeze stops every process
 * in the cgroup from running further (so nothing forks a NEW child
 * while this sweeps), SIGKILL each pid cgroup.procs lists, then
 * unfreeze so the now-dead processes are reaped rather than left
 * frozen. Untested on any kernel actually lacking cgroup.kill -- this
 * container's own kernel (`uname -r`, not the unrelated build config
 * under /boot -- see docs/ENVIRONMENT.md) has it, so the fallback
 * branch is read, not run, the same honest gap `docs/options/15`
 * records for its own untestable branch. */
static void cg_kill_sweep(const char *cgroup_path)
{
    char p[192];
    snprintf(p, sizeof p, "%s/cgroup.kill", cgroup_path);
    int fd = open(p, O_WRONLY | O_CLOEXEC);
    if (fd >= 0) {
        ssize_t w = write(fd, "1", 1);
        (void)w;
        close(fd);
        return;
    }
    if (errno != ENOENT) return; /* best-effort: a real error here is not fatal */

    snprintf(p, sizeof p, "%s/cgroup.freeze", cgroup_path);
    int ffd = open(p, O_WRONLY | O_CLOEXEC);
    if (ffd < 0) return;
    ssize_t fw = write(ffd, "1", 1);
    (void)fw;
    close(ffd);

    snprintf(p, sizeof p, "%s/cgroup.procs", cgroup_path);
    int pfd = open(p, O_RDONLY | O_CLOEXEC);
    if (pfd >= 0) {
        char buf[4096];
        ssize_t r = read(pfd, buf, sizeof buf - 1);
        close(pfd);
        if (r > 0) {
            buf[r] = 0;
            char *save = NULL;
            for (char *tok = strtok_r(buf, "\n", &save); tok;
                 tok = strtok_r(NULL, "\n", &save)) {
                pid_t victim = (pid_t)atoi(tok);
                if (victim > 0) kill(victim, SIGKILL);
            }
        }
    }

    snprintf(p, sizeof p, "%s/cgroup.freeze", cgroup_path);
    int ufd = open(p, O_WRONLY | O_CLOEXEC);
    if (ufd >= 0) {
        ssize_t uw = write(ufd, "0", 1);
        (void)uw;
        close(ufd);
    }
}

/* memory.events / pids.events: "key value\n" lines, one counter per
 * key. Reads the single named key's CURRENT cumulative value. -1 means
 * the file or the controller is unavailable, distinct from a genuine
 * 0 -- "unavailable" reports as unavailable in evidence, never as a
 * false zero, per Section 3's own requirement. */
static int cg_read_counter(const char *cgroup_path, const char *file,
                            const char *key, unsigned long long *out)
{
    char p[192];
    snprintf(p, sizeof p, "%s/%s", cgroup_path, file);
    int fd = open(p, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    char buf[512];
    ssize_t r = read(fd, buf, sizeof buf - 1);
    close(fd);
    if (r <= 0) return -1;
    buf[r] = 0;
    size_t klen = strlen(key);
    for (char *line = buf; line && *line; ) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = 0;
        if (strncmp(line, key, klen) == 0 && line[klen] == ' ') {
            errno = 0;
            char *endp = NULL;
            unsigned long long v = strtoull(line + klen + 1, &endp, 10);
            if (errno != ERANGE && endp != line + klen + 1) {
                *out = v;
                return 0;
            }
            return -1;
        }
        line = nl ? nl + 1 : NULL;
    }
    return -1;
}

static pid_t child;
static volatile sig_atomic_t stopping;
/* docs/options/31 Section 5. Set once in main(), from NW_STOP_SIGNAL,
 * BEFORE on_term() below is installed as a handler and never written
 * again -- reading it inside that handler is as signal-safe as reading
 * `child` already is. UNSET behaves as SIGTERM, matching every
 * kill() site's pre-Phase-4 hardcoded value exactly. Declared here,
 * beside `child`/`stopping`, rather than with nw_lock and the rest of
 * this file's other plan-derived globals below, because on_term()
 * needs it and on_term() is defined immediately below this point. */
static int nw_stop_signal = SIGTERM;

static void on_term(int sig)
{
    (void)sig;
    stopping = 1;
    if (child > 0)
        kill(child, nw_stop_signal);
}

/*
 * Block until house `p` exits, by poll() on its pidfd.
 *
 * extra_fd, if >= 0, is a second poll member. Both call sites below
 * now always pass the unit's listening control socket (docs/options/11),
 * live for the house's whole run -- see "THAT FALLBACK IS NO LONGER THE
 * WHOLE STORY" further down for what that changed. This comment used to
 * say "This round nothing real lives there (no start/stop socket, no
 * log pipe)", true when the parameter was added and false since the
 * control channel landed; a reader hitting that sentence first and this
 * function's own later paragraph second was reading a contradiction
 * inside one function. A ready extra fd does not reap the house; we loop.
 *
 * Reap is waitpid(p, ...) after poll says the pidfd is readable.
 * That waitpid does not block. It is not waitpid(-1): the pid is
 * the one we opened the pidfd on.
 *
 * Lids still run in the child after birth, before exec -- unchanged by
 * Phase 3's own clone3(CLONE_INTO_CGROUP) (`clone_into_cgroup()`,
 * replacing the plain fork() this comment used to describe here):
 * CLONE_INTO_CGROUP only changes which cgroup the child is born into,
 * not fork()'s own copy-on-write semantics or anything below this
 * point. CLONE_PIDFD (a single syscall producing both the pid and a
 * pidfd together) is a different flag, still not used -- pidfd_open()
 * two paragraphs above is still how this file gets one, kept
 * independent of which clone flag places the child.
 *
 * pidfd_open NEEDS LINUX >= 5.3 AND CAN FAIL ON A KERNEL THAT HAS
 * IT: ENOSYS on an older or filtered kernel, EMFILE/ENFILE on
 * descriptor exhaustion. The old plain waitpid(2) this replaces has
 * no such dependency -- it consumes no descriptor and exists on
 * every kernel this project has ever targeted. Dying unconditionally
 * on that failure, as an earlier version of this function did, would
 * have taken down the whole per-house supervisor OUTSIDE the
 * deaths/budget accounting the rest of this file is built around --
 * no restart line, no spent line, and the house itself left running,
 * forked and immediately orphaned. Measured live with an LD_PRELOAD
 * forcing ENOSYS: nw-sup exited 72 with no accounting at all, and the
 * house ran on UNSUPERVISED -- reparented to whatever is PID 1, which
 * in the real boot chain is nw-root's own continuous orphan-reap loop
 * (pid1.c's reap_all()), not a bare test harness with nothing above
 * it. That loop does eventually reap it when it exits on its own, so
 * "permanent zombie" overstated the real-boot consequence -- the
 * actual, still-serious defect is the budget/restart accounting for
 * that house being silently lost for the rest of its life, invisible
 * to nw-sup and to anyone reading its output. So a pidfd_open failure
 * falls back to the exact pre-pidfd mechanism instead of dying:
 * ordinary blocking waitpid(p, &st, 0), which is what this function
 * replaced and what every death/restart/budget line downstream of
 * this call already expects to have happened.
 *
 * THAT FALLBACK IS NO LONGER THE WHOLE STORY, since the start/stop
 * control channel gave extra_fd a real caller: both call sites below
 * now always pass the unit's listening control socket, live for the
 * house's whole run rather than -1. A pidfd_open failure can no
 * longer take the plain-waitpid early return above at all once a
 * live socket exists to service -- dying here would mean a STOP
 * request arriving during exactly this window blocks behind an
 * unbounded waitpid the way invariant 1's Liveness section already
 * refuses to reopen. So the fallback below tries signalfd(SIGCHLD)
 * next, and only drops to a bounded 50ms poll()-plus-WNOHANG loop if
 * that too fails -- never an unconditional die().
 *
 * Item 1c (docs/OPERATOR-BRIEF.md Section 2) removed docs/options/16's
 * FREEZE/CONT verbs, which for one round made signalfd(SIGCHLD) run
 * UNCONDITIONALLY alongside pidfd rather than only as its fallback --
 * pidfd_open(2) is documented to become poll-readable solely on
 * genuine termination, never on a ptrace-stop, and a frozen house
 * needed a channel that could see one. With FREEZE/CONT gone, nothing
 * here ever ptrace-attaches a house, so there is no stop for pidfd to
 * be blind to, and signalfd is back to being purely the fallback this
 * paragraph already describes. docs/options/16 is superseded, not
 * deleted; `HISTORY.md` and that document carry the retired reasoning.
 */
static int stop_requested;

/* Phase 2 (docs/OPERATOR-BRIEF.md Section 3): file-scope, alongside
 * `child`/`stopping`/`stop_requested` above, for the same reason those
 * are -- handle_ctl_live() needs them to call nw_decide() the same way
 * main()'s own loop does, and it is a separate function rather than
 * inline code in main(). nw_budget and nw_complete_on_0 are set once,
 * near the top of main(), from the unit's environment; nw_lock is
 * hardcoded, since there is no plan field for it yet.
 *
 * `deaths` keeps its pre-Phase-2 name and its pre-Phase-2 shape
 * deliberately: CLAUDE.md invariant 4 pins "initialised once,
 * incremented once, nothing else assigns it" by grepping nwsup.c for
 * exactly this token, and test_budget_is_hard_total's own structural
 * half does the same. Moving the BUDGET COMPARISON into decide.c (a
 * stateless function that never assigns to anything) does not change
 * what this pins; it makes the claim strictly easier to keep true,
 * since there is now nowhere in nwsup.c a comparison against `budget`
 * happens at all, only the two assignments below. */
static unsigned nw_budget;
static int deaths = 0;
static int nw_complete_on_0;
/* docs/options/22-lock-unlock.md, docs/options/31. Set once in main()
 * from NW_LOCK, INVERTED from the blob byte's own sense
 * (NW_LOCK_LOCKED=0/NW_LOCK_UNLOCKED=1) into nw_decide()'s convention
 * (1=LOCKED/0=UNLOCKED, decide.h) -- the one place that translation
 * happens; every nw_decide() call site below reads this variable and
 * needs to know nothing about the blob's own encoding. nw_decide()'s
 * UNLOCKED rows were proven (proofs/caller_decide.c) and exhaustively
 * tested (tests/decide_seq.c) before this field existed to select
 * them. */
static int nw_lock = 1;

static void ctl_reply(int c, const char *s)
{
    ssize_t n = write(c, s, strlen(s));
    (void)n;
}

/* Listen fd ready: one connection, one request, one reply, close.
 * START/STOP while the child is live. START on a live house is OK
 * (no second fork). STOP sets stop_requested and SIGTERMs. */
static void handle_ctl_live(int listen_fd, pid_t live)
{
    int c = accept(listen_fd, NULL, NULL);
    if (c < 0)
        return; /* EAGAIN if poll raced a withdrawn connect */
    char buf[16];
    ssize_t n = read(c, buf, sizeof buf);
    if (n == 6 && memcmp(buf, "START\n", 6) == 0) {
        ctl_reply(c, "OK\n");
    } else if (n == 5 && memcmp(buf, "STOP\n", 5) == 0) {
        if (live > 0 && !stop_requested) {
            stop_requested = 1;
            /* Phase 2: routed through nw_decide() rather than an
             * inline kill(), so this call site and the post-fork one
             * below share one decision. has_child=1, child_exited=0,
             * stop_requested=1 has exactly one outcome regardless of
             * lock/complete_on_0/deaths/budget -- TERM_CHILD -- and
             * the assertion is that this is still true, not a
             * decision this call site is making on its own. */
            enum nw_decision d = nw_decide(nw_lock, nw_complete_on_0,
                                            stopping, stop_requested, 0,
                                            /*has_child=*/1,
                                            /*child_exited=*/0, 0,
                                            deaths, nw_budget);
            if (d == NW_DECIDE_TERM_CHILD)
                kill(live, nw_stop_signal);
        }
        ctl_reply(c, "OK\n");
    } else {
        ctl_reply(c, "ERR bad request\n");
    }
    close(c);
}

static int wait_house(pid_t p, int extra_fd)
{
    int pfd = (int)syscall(SYS_pidfd_open, p, 0U);
    int wake = -1;
    if (pfd < 0) {
        /* signalfd(SIGCHLD) is the FALLBACK for a failed pidfd_open,
         * not a channel run alongside it. Item 1c (docs/OPERATOR-BRIEF.md
         * Section 2, superseding docs/options/16): FREEZE/CONT once made
         * this unconditional, because pidfd_open(2) is documented to
         * become poll-readable only on genuine termination, never on a
         * ptrace-stop, and a frozen house needed a channel that could
         * see one. Nothing here ptrace-attaches a house any more, so
         * there is no stop for pidfd to be blind to, and pidfd alone
         * sees every real termination this fallback exists to catch. */
        sigset_t sc;
        sigemptyset(&sc);
        sigaddset(&sc, SIGCHLD);
        sigprocmask(SIG_BLOCK, &sc, NULL);
        wake = signalfd(-1, &sc, SFD_CLOEXEC);
    }

    if (pfd < 0 && wake < 0) {
        /* Both primary channels failed. Live socket cannot sit behind
         * blocking waitpid: that re-enables the orphan-and-die path
         * the last review closed. Otherwise, poll the socket with a
         * 50ms bound and waitpid WNOHANG. */
        if (extra_fd < 0) {
            int st = 0;
            if (waitpid(p, &st, 0) < 0)
                die("wait house");
            return st;
        }
        struct pollfd pf;
        pf.fd = extra_fd;
        pf.events = POLLIN;
        pf.revents = 0;
        for (;;) {
            int pr = poll(&pf, 1, 50);
            if (pr < 0) {
                if (errno == EINTR)
                    continue;
                die("poll house");
            }
            int st = 0;
            pid_t r = waitpid(p, &st, WNOHANG);
            if (r == p)
                return st;
            if (pf.revents & (POLLIN | POLLHUP | POLLERR)) {
                handle_ctl_live(extra_fd, p);
                pf.revents = 0;
            }
        }
    }

    /* Exactly one of pidfd/signalfd is available -- the two are mutually
     * exclusive now that signalfd is only created when pidfd_open failed
     * above. Poll it plus the ctl socket, up to two members, tracked by
     * index rather than by a fixed position since which one exists
     * varies. */
    struct pollfd pf[2];
    nfds_t n = 0;
    int pfd_i = -1, wake_i = -1, extra_i = -1;
    if (pfd >= 0) {
        pfd_i = (int)n;
        pf[n].fd = pfd; pf[n].events = POLLIN; pf[n].revents = 0;
        n++;
    }
    if (wake >= 0) {
        wake_i = (int)n;
        pf[n].fd = wake; pf[n].events = POLLIN; pf[n].revents = 0;
        n++;
    }
    if (extra_fd >= 0) {
        extra_i = (int)n;
        pf[n].fd = extra_fd; pf[n].events = POLLIN; pf[n].revents = 0;
        n++;
    }

    for (;;) {
        int pr = poll(pf, n, -1);
        if (pr < 0) {
            if (errno == EINTR)
                continue;
            if (pfd >= 0) close(pfd);
            if (wake >= 0) close(wake);
            die("poll house");
        }
        if (extra_i >= 0 &&
            (pf[extra_i].revents & (POLLIN | POLLHUP | POLLERR))) {
            handle_ctl_live(extra_fd, p);
            pf[extra_i].revents = 0;
        }
        if (wake_i >= 0 &&
            (pf[wake_i].revents & (POLLIN | POLLHUP | POLLERR))) {
            struct signalfd_siginfo si;
            ssize_t ign = read(wake, &si, sizeof si);
            (void)ign;
            int st = 0;
            pid_t r = waitpid(p, &st, WNOHANG);
            if (r == p) {
                close(wake);
                return st;
            }
            pf[wake_i].revents = 0;
        }
        if (pfd_i >= 0 && (pf[pfd_i].revents & (POLLIN | POLLHUP | POLLERR)))
            break; /* pidfd: real termination, the only thing it reports. */
    }

    int st = 0;
    if (waitpid(p, &st, 0) < 0) {
        if (pfd >= 0) close(pfd);
        if (wake >= 0) close(wake);
        die("wait house");
    }
    if (pfd >= 0) close(pfd);
    if (wake >= 0) close(wake);
    return st;
}

/* NW_EVIDENCE_DRAIN_SPINS is the hard ceiling on waiting for the logger
 * to drain the log pipe before nw-sup reads its tail file -- an
 * ITERATION count, not a time bound, and that is not a style choice.
 * runtime.md's Liveness section requires nwsup.c to never regain a
 * timing primitive -- "a budget that can read a clock can reset on
 * one" -- and tests/run.py's test_budget_no_reset enforces it by
 * grepping this file for clock_gettime/now_ms/alarm/nanosleep/usleep
 * and their relatives. A first version of this wait used nanosleep()
 * between checks and that test caught it immediately (make test:
 * "nwsup.c has regained a timing primitive: ['nanosleep']") -- not a
 * near-miss on the letter of a denylist, a real instance of exactly
 * what the rule refuses: this file reading elapsed time for any
 * purpose, not only for the restart budget. The fix is not to phrase
 * the wait so the grep misses it, it is to make the wait genuinely read
 * no clock at all: sched_yield() gives up the remainder of a timeslice
 * without reading or being told any duration, so a loop bounded by an
 * iteration counter and built only from FIONREAD and sched_yield() has
 * no way to compute elapsed wall-clock time even in principle, which is
 * the actual property the rule protects, not merely its token list. */
#define NW_EVIDENCE_DRAIN_SPINS 200000

/* docs/options/12-crash-evidence.md. Called at the exact point this
 * process already has every field the restart/spent line needs, but
 * BEFORE say(line) writes that line -- deliberately the opposite order
 * from the first version of this function, which called say(line)
 * first. That order needed this wait to win a race against the
 * logger's OWN handling of the very byte just written a moment
 * earlier -- the tightest possible case, since nothing but a handful of
 * kernel-then-userspace instructions separates "written" from
 * "flushed", and FIONREAD only reports the former. Measured against
 * that order: even after the logger had visibly drained the pipe
 * (FIONREAD back to 0) and several dozen further sched_yield() calls,
 * the tail was still empty in roughly 1 of 6 runs of the silent-house
 * case -- not rare enough to accept, and no bound on yields closed it,
 * because FIONREAD cannot observe "flushed", only "drained", so no
 * amount of polling it proves the thing this needed to prove.
 *
 * Reordering removes the need to win that race at all. What this
 * function has to wait for now is only the HOUSE's own prior output
 * (and, for a second-or-later death, the previous death's own
 * restart/spent line, written a full house-lifetime before this call) --
 * both already sitting in the pipe with ordinary real wall-clock time
 * behind them by the time nw-sup gets here, not written an instant ago.
 * nw-sup's OWN line for the death being recorded right now is written
 * AFTER this call returns, so it is never inside this package's own
 * tail -- it shows up, if anything does, at the head of the NEXT
 * death's tail for this unit, or not at all if there is no next death.
 * That is a real, stated narrowing of what this feature captures, not
 * an oversight: see docs/options/12-crash-evidence.md.
 *
 * Best-effort either way: every failure here is silent and never
 * affects the restart/spent decision or its console line, which say()
 * still prints immediately after this returns regardless of what
 * happened here. */
/* Phase 3: two more fields, each with its own availability flag rather
 * than defaulting an unreadable counter to 0 -- Section 3's own
 * requirement ("anything unavailable reports unavailable, never 0").
 * oom_kill_delta/pids_max_delta are already deltas by the time they
 * reach here (computed at the one call site, against a baseline this
 * function does not itself track); this function only formats them. */
static void write_evidence(const char *name, int deaths, unsigned budget,
                            int st, unsigned long long oom_kill_delta,
                            int oom_kill_avail,
                            unsigned long long pids_max_delta,
                            int pids_max_avail)
{
    unsigned char tail[NW_EVIDENCE_TAIL_MAX];
    size_t tail_n = 0;
    char tailpath[sizeof(NW_EVIDENCE_DIR) + NW_NAME_LEN + 8];
    if (snprintf(tailpath, sizeof tailpath, "%s/%s.tail",
                 NW_EVIDENCE_DIR, name) < (int)sizeof tailpath) {
        /* PID 1's logger is a separate process with no synchronization
         * to nw-sup: nothing orders "the logger has processed every
         * byte currently in the pipe" before "nw-sup reads what it
         * wrote". tcb-review measured this unsynchronized read missing
         * a death's own final output in ~1 of 8 runs under ordinary
         * load, and in the large majority of runs under heavy load;
         * `control` independently reproduced the same gap by
         * deliberately delaying the logger. The reorder above (see this
         * function's own comment) already removes the tightest instance
         * of the race; this wait narrows what remains for output that
         * predates this call by less comfortable a margin.
         *
         * FIONREAD on fd 2 -- nw-sup's OWN copy of the log pipe's write
         * end -- answers precisely, not by inference from file
         * metadata: it is the same pipe the logger reads, and FIONREAD
         * on a pipe reports pending bytes from either end (measured). A
         * stat()/mtime comparison was tried first here and was wrong:
         * it cannot tell "nothing has been written yet" from "written
         * but not yet flushed", so it declared quiescence after a
         * single unchanged reading even when a flush was still pending.
         *
         * Zero means the logger has already READ everything written to
         * this pipe so far -- not that it has flushed it. sched_yield()
         * between checks gives the logger's own userspace a chance to
         * actually run flush_tail() for what it just read, rather than
         * nw-sup racing straight back into the ioctl (or the tail read
         * below) on the same CPU. A "keep waiting while pending is
         * decreasing, give up once it stalls" refinement was tried and
         * rejected: FIONREAD's pending count stays perfectly FLAT for
         * the logger's whole delay, whether that delay is a genuine
         * permanent stall or an ordinary descheduling about to end,
         * then drops all at once once it is next scheduled and drains
         * everything in one read() -- "unchanged for N ticks" cannot
         * tell those apart, so it is not a usable signal here.
         *
         * BOUNDED, NOT UNCONDITIONAL, and that is a stated limit, not an
         * oversight: at most NW_EVIDENCE_DRAIN_SPINS checks, self-
         * terminating the moment the pipe drains, never an indefinite
         * wait -- this is a diagnostic write path, not a reopening of
         * the refused freeze-detection question in runtime.md. A logger
         * delayed past the ceiling still gets a stale/empty tail.
         * Nothing here claims a fixed wall-clock equivalent for this
         * spin count on any given machine -- there is no clock to make
         * that claim against, which is the whole point; a spin count is
         * a ceiling on how many times this checks, not a promise about
         * how much wall-clock time that takes.
         *
         * fd 2's identity as the log pipe's write end is established by
         * nwspawn.c's dup2() before this process's own exec and never
         * re-verified here -- exactly the shape invariant 2 asks a
         * reviewer to distrust, a fixed descriptor number trusted on an
         * assumption maintained in another file (fd-auditor). The
         * fstat/S_ISFIFO check below designs that out rather than
         * documenting it: if fd 2 is ever not a pipe, this skips the
         * wait entirely (falling back to no-wait, the same degraded-but-
         * safe behaviour a FIONREAD failure already produces below,
         * never a crash or a wait on the wrong descriptor). */
        struct stat fd2st;
        int fd2_is_pipe = (fstat(2, &fd2st) == 0) && S_ISFIFO(fd2st.st_mode);
        for (int i = 0; fd2_is_pipe && i < NW_EVIDENCE_DRAIN_SPINS; i++) {
            int pending = -1;
            if (ioctl(2, FIONREAD, &pending) < 0 || pending == 0)
                break;
            sched_yield();
        }
        int tfd = open(tailpath, O_RDONLY | O_CLOEXEC);
        if (tfd >= 0) {
            ssize_t r = read(tfd, tail, sizeof tail);
            if (r > 0) tail_n = (size_t)r;
            close(tfd);
        }
    }
    /* A read failure or a house that never wrote anything both mean
     * "no tail available" and produce the identical, valid, empty-tail
     * record -- no special case for either. */

    /* "unavailable", never a false 0 -- Section 3's own requirement,
     * and the reason these are formatted as strings rather than
     * numbers: a number has no way to also mean "not measured". */
    char oom_kill_s[24], pids_max_s[24];
    if (oom_kill_avail)
        snprintf(oom_kill_s, sizeof oom_kill_s, "%llu", oom_kill_delta);
    else
        snprintf(oom_kill_s, sizeof oom_kill_s, "unavailable");
    if (pids_max_avail)
        snprintf(pids_max_s, sizeof pids_max_s, "%llu", pids_max_delta);
    else
        snprintf(pids_max_s, sizeof pids_max_s, "unavailable");

    unsigned char record[256 + NW_EVIDENCE_TAIL_MAX];
    /* NWEVT1 -> NWEVT2: two fields added (oom_kill, pids_max), Phase 3.
     * A version bump, not a silent format change -- a reader keyed to
     * "NWEVT1\n" at the top would otherwise be handed two unexpected
     * lines before tail_bytes= with nothing announcing it. */
    int hdr_len = snprintf((char *)record, 256,
        "NWEVT2\nunit=%s\nreason=%s\nvalue=%d\ndeath=%d\nbudget=%u\n"
        "oom_kill=%s\npids_max=%s\ntail_bytes=%zu\n--\n",
        name, WIFEXITED(st) ? "exit" : "signal",
        WIFEXITED(st) ? WEXITSTATUS(st) : WTERMSIG(st),
        deaths, budget, oom_kill_s, pids_max_s, tail_n);
    if (hdr_len < 0 || hdr_len >= 256) return;
    memcpy(record + hdr_len, tail, tail_n);
    size_t total = (size_t)hdr_len + tail_n;

    /* Was: hash the record, compose a mkostemp temp name in
     * NW_EVIDENCE_DIR, write, unconditional rename to <hex>.evt -- the
     * same temp-then-rename shape mkbrick.py uses for bricks, hand-
     * written here a second time. nw_store_put() (store.c) is that
     * shape lifted out once. docs/options/14-shared-store.md: this is
     * its first live-side caller, and the only change here is that a
     * second write of a death this project has already seen once now
     * skips the write instead of redoing it -- the on-disk result, the
     * path, and the naming are all unchanged. */
    char hex[65];
    (void)nw_store_put(NW_EVIDENCE_DIR, record, total, ".evt", hex);
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
    /* Mirrored into file scope for nw_decide()'s other caller,
     * handle_ctl_live() -- see the comment beside nw_budget's own
     * declaration. */
    nw_budget = budget;
    nw_complete_on_0 = (kind == NW_KIND_ONESHOT);
    /* docs/options/22-lock-unlock.md, docs/options/31. RE-VALIDATED for
     * the same reason NW_BRICK/NW_LAYER are: nw-sup reads its unit from
     * the environment, not the sealed blob. NW_LOCK carries the blob's
     * own byte, un-translated; this is the one point that inverts it
     * into nw_decide()'s convention (see nw_lock's own declaration). */
    unsigned lock_byte = NW_LOCK_LOCKED;
    if ((e = getenv("NW_LOCK"))) lock_byte = (unsigned)atoi(e);
    if (lock_byte > NW_LOCK_MAX) die("lock value");
    nw_lock = (lock_byte == NW_LOCK_UNLOCKED) ? 0 : 1;
    unsigned stop_signal_byte = NW_STOPSIG_UNSET;
    if ((e = getenv("NW_STOP_SIGNAL"))) stop_signal_byte = (unsigned)atoi(e);
    if (stop_signal_byte > NW_STOPSIG_MAX) die("stop-signal value");
    nw_stop_signal = stop_signal_byte == NW_STOPSIG_TERM ? SIGTERM
                    : stop_signal_byte == NW_STOPSIG_INT  ? SIGINT
                    : stop_signal_byte == NW_STOPSIG_HUP  ? SIGHUP
                    : stop_signal_byte == NW_STOPSIG_QUIT ? SIGQUIT
                    : SIGTERM; /* NW_STOPSIG_UNSET, or anything else --
                                 * unreachable past the range check above,
                                 * but a closed switch needs a default. */
    /* docs/options/31 Section 5, Section 6. Neither field has an
     * applier yet (the escalation mechanism and the death-policy
     * vocabulary are their own later design-note rounds) -- refused
     * here, by name, at supervisor startup, the same shape
     * apply_capabilities()'s neighbour sched_ext used to refuse a
     * declared-but-unbuildable policy. A blob baked today starts
     * working the day the applier lands, with no rebake. */
    unsigned grace_period = 0;
    if ((e = getenv("NW_GRACE_PERIOD"))) grace_period = (unsigned)atoi(e);
    if (grace_period != 0)
        die("grace-period: declared but not yet applied (docs/options/31 Section 5)");
    unsigned supervisor_death_policy = 0;
    if ((e = getenv("NW_SUPERVISOR_DEATH_POLICY")))
        supervisor_death_policy = (unsigned)atoi(e);
    if (supervisor_death_policy != 0)
        die("supervisor-death-policy: reserved, no plan syntax exists yet "
            "(docs/options/31 Section 6)");
    unsigned nofile = 0;
    if ((e = getenv("NW_NOFILE"))) nofile = (unsigned)atoi(e);
    uint64_t capabilities = 0;
    if ((e = getenv("NW_CAPABILITIES"))) {
        for (const char *p = e; *p; p++)
            if (*p < '0' || *p > '9') die("capabilities not a number");
        errno = 0;
        char *endp = NULL;
        unsigned long long v = strtoull(e, &endp, 10);
        if (errno == ERANGE || !endp || *endp) die("capabilities range");
        capabilities = (uint64_t)v;
    }
    /* THE KERNEL QUESTION, ASKED HERE RATHER THAN INSIDE
     * apply_capabilities() -- see that function's own header comment
     * for the full reasoning: a brick house's later pivot makes /proc
     * unreliable, so this runs in the PARENT, once, well before any
     * child ever calls lid_brick(). A declared bit past this ceiling
     * names a capability the RUNNING kernel does not have, which bake
     * time on a different machine cannot see. */
    unsigned cap_last = nw_cap_last_cap();
    /* fd-auditor: cap_last == 63 makes cap_last + 1 == 64, and a shift
     * of a uint64_t by its own width is undefined behaviour in C, not
     * merely "big but defined" -- unreachable on any kernel measured so
     * far (today's real ceiling is ~40, CAP_CHECKPOINT_RESTORE) and
     * even under UB the practical case has no security consequence
     * (nothing can name a bit past 63 in a 64-bit set either way), but
     * an -O0 build or a different compiler is free to make the shift
     * NOT reduce to shift-by-0, which would refuse a perfectly legal
     * capabilities= declaration and burn the restart budget for it.
     * Special-cased rather than relying on the shift never reaching
     * the type's width. */
    if (cap_last == 63 ? 0 : capabilities >> (cap_last + 1))
        die("capabilities: a declared bit names a capability past this "
            "kernel's own cap_last_cap");
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
    /* THE LAYER ID, RE-VALIDATED HERE for the same reason the hash is:
     * nw-sup reads its unit from the environment rather than from the
     * sealed blob, so nothing the baker or nw-check did stands behind
     * this value. A name from a closed alphabet cannot express a
     * traversal, and that is only true if it is checked to be one. */
    const char *layer = getenv("NW_LAYER");
    if (layer && layer[0]) {
        size_t ll = strlen(layer);
        if (ll >= NW_NAME_LEN) die("layer id length");
        for (size_t k = 0; k < ll; k++) {
            char c = layer[k];
            int ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
                  || (c >= '0' && c <= '9') || c == '_' || c == '-';
            if (!ok) die("layer id not a name");
        }
    }

    /* THE CAPACITY, RE-VALIDATED HERE for the same reason the hash and
     * the layer id are: nw-sup reads its unit from the environment, so
     * nothing the baker or nw-check did stands behind this value
     * either. Decimal digits only, and must fit uint64_t -- a value
     * nw-check already bounded to that width, so an env var claiming
     * more is not a legal handoff and is refused rather than
     * truncated. Empty or absent means 0 (unset), the same convention
     * the field has in the blob. */
    uint64_t layer_bytes = 0;
    if ((e = getenv("NW_LAYER_BYTES")) && e[0]) {
        for (const char *p = e; *p; p++)
            if (*p < '0' || *p > '9') die("layer bytes not a number");
        errno = 0;
        char *endp = NULL;
        unsigned long long v = strtoull(e, &endp, 10);
        if (errno == ERANGE || !endp || *endp) die("layer bytes range");
        layer_bytes = (uint64_t)v;
    }

    /* Phase 3 (docs/OPERATOR-BRIEF.md Section 3): the six resource-block
     * fields this process applies -- mem_high/mem_max/cpu_weight via the
     * house's own cgroup, cpu_mask/nice/sched_policy via a direct syscall
     * on this process before exec. io_rbps/io_wbps/layer_bytes are not
     * here -- io limits are cut for this phase (Section 1.6), and
     * layer_bytes already has its own validation above.
     *
     * RE-VALIDATED HERE for the same reason NW_BRICK/NW_LAYER/
     * NW_LAYER_BYTES are: nw-sup reads its unit from the environment,
     * not the sealed blob, so nothing nwcheck.c did stands behind these
     * values. The bounds and cross-field rules mirror nwcheck.c's own
     * (NW_E_RESWEIGHT, NW_E_RESSCHED, NW_E_RESNICE, NW_E_MEMORDER,
     * NW_E_NICEPOL) exactly, because a forged environment is exactly
     * the class those checks exist to close. */
    uint64_t cpu_mask = 0, mem_high = 0, mem_max = 0;
    unsigned cpu_weight = 0, sched_policy = NW_SCHED_UNSET;
    int nice_val = 0;
    if ((e = getenv("NW_CPU_MASK")) && e[0]) {
        for (const char *p = e; *p; p++)
            if (*p < '0' || *p > '9') die("cpu mask not a number");
        errno = 0;
        char *endp = NULL;
        unsigned long long v = strtoull(e, &endp, 10);
        if (errno == ERANGE || !endp || *endp) die("cpu mask range");
        cpu_mask = (uint64_t)v;
    }
    if ((e = getenv("NW_MEM_HIGH")) && e[0]) {
        for (const char *p = e; *p; p++)
            if (*p < '0' || *p > '9') die("mem high not a number");
        errno = 0;
        char *endp = NULL;
        unsigned long long v = strtoull(e, &endp, 10);
        if (errno == ERANGE || !endp || *endp) die("mem high range");
        mem_high = (uint64_t)v;
    }
    if ((e = getenv("NW_MEM_MAX")) && e[0]) {
        for (const char *p = e; *p; p++)
            if (*p < '0' || *p > '9') die("mem max not a number");
        errno = 0;
        char *endp = NULL;
        unsigned long long v = strtoull(e, &endp, 10);
        if (errno == ERANGE || !endp || *endp) die("mem max range");
        mem_max = (uint64_t)v;
    }
    if ((e = getenv("NW_CPU_WEIGHT"))) cpu_weight = (unsigned)atoi(e);
    if (cpu_weight > NW_CPU_WEIGHT_MAX) die("cpu weight range");
    if ((e = getenv("NW_NICE"))) nice_val = atoi(e);
    if (nice_val < NW_NICE_MIN || nice_val > NW_NICE_MAX) die("nice range");
    if ((e = getenv("NW_SCHED_POLICY"))) sched_policy = (unsigned)atoi(e);
    if (sched_policy > NW_SCHED_MAX) die("sched policy value");
    if (mem_high && mem_max && mem_high >= mem_max) die("mem high/max order");
    if (nice_val && sched_policy != NW_SCHED_OTHER)
        die("nice without sched=other");
    /* docs/options/31 Section 8. task_cap: pids.max on the house's own
     * cgroup, applied alongside mem_high/mem_max/cpu_weight below.
     * oom_score_adj: a direct /proc write on this process, applied in
     * the early "process attribute" phase with cpu_mask/nice/
     * sched_policy. Both RE-VALIDATED for the same reason every other
     * resource-block field above is. */
    unsigned task_cap = 0;
    if ((e = getenv("NW_TASK_CAP"))) task_cap = (unsigned)atoi(e);
    int oom_score_adj = 0;
    if ((e = getenv("NW_OOM_SCORE_ADJ"))) oom_score_adj = atoi(e);
    if (oom_score_adj < NW_OOM_ADJ_MIN || oom_score_adj > NW_OOM_ADJ_MAX)
        die("oom-score-adj range");

    /* Landlock grants beneath the house's root, which is only a restriction
     * if that root is a brick. nw-check returns NW_E_LLBRICK; re-checked here
     * because nw-sup reads its unit from the environment. */
    if ((lids & NW_LID_LANDLOCK) && !brick) die("landlock without brick");
    /* THE PAIRING, RE-CHECKED HERE TOO, under exactly the argument the
     * layer-id validation above gives and then did not finish: nw-sup
     * reads its unit from the environment, so nothing the baker or
     * nw-check did stands behind it. Its two neighbours -- landlock
     * without brick, brick without newns -- both do the full job; this
     * one checked the alphabet and stopped.
     *
     * Measured with NW_LAYER unset and NW_BRICK set: the house started
     * rooted on the bare read-only image, no `lid layer`, no die, exit 0,
     * `write=denied(30)`. That is precisely the state NW_E_LAYERPAIR
     * exists to forbid -- writes vanishing while the plan says the house
     * has data -- reached through the one door the comment above names
     * as the reason the other checks exist. `tcb-review`. */
    if (brick && !(layer && layer[0])) die("brick without layer");
    if (!brick && layer && layer[0]) die("layer without brick");
    /* THE CAPACITY'S OWN PAIRING, missed by the first version of this
     * re-validation block: nw-check's NW_E_CAPNOLAYER is
     * `r->layer_bytes && !has_layer`, and neither "brick without layer"
     * nor "layer without brick" above covers it -- a forged or buggy
     * environment with NW_LAYER empty and NW_LAYER_BYTES nonzero fell
     * through to `lid_brick()`'s
     * `if (layer && layer[0] && layer_bytes)` guard, which simply
     * skipped the capacity block rather than dying. Fails safe (no
     * capacity silently means no enforcement, not a wrong one), but
     * inconsistent with its two neighbours here, which die rather than
     * silently drop the field they can't act on. `tcb-review`. */
    if (layer_bytes && !(layer && layer[0])) die("layer bytes without layer");
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

    if (mkdir("/nw", 0755) < 0 && errno != EEXIST)
        die("ctl parent");
    /* 0700, not 0755: item 1e (docs/OPERATOR-BRIEF.md Section 2, item
     * 1f) hardens the control-socket directory. Every house is uid 0
     * with no privilege dropped (invariant 5), so this does not change
     * what a house on THIS machine can reach -- root bypasses directory
     * permission bits everywhere. What it closes is a mapped, non-root
     * uid entering /nw/ctl at all: measured, `stat -c '%a %U'` on a
     * socket made under this scheme reads 600 root, and connecting as
     * "nobody" to it gets EACCES (errno 13), while root's own connect
     * through the exact same path still succeeds.
     *
     * CHMOD UNCONDITIONALLY, not only on the branch that just created
     * it. `/nw` is on the persistent root, not tmpfs, so a directory
     * this binary's PREDECESSOR made at 0755 survives an upgrade to
     * this binary untouched by the `mkdir` EEXIST tolerance above --
     * `mkdir` never retroactively narrows a mode it did not choose.
     * Every nw-sup enforces the mode on every boot instead of trusting
     * whichever supervisor happened to create the directory first. */
    if (mkdir(NW_CTL_DIR, 0700) < 0 && errno != EEXIST)
        die("ctl dir");
    /* fchmod on an O_NOFOLLOW-opened fd, not chmod(2) by path. chmod(2)
     * follows a symlink; if /nw/ctl were ever replaced by one (a stale
     * leftover, or between the mkdir above and here) chmod would narrow
     * the SYMLINK'S TARGET instead, silently leaving the intended
     * directory at whatever mode it already had. `fd-auditor`. Inert
     * under today's threat model -- every house is uid 0 (invariant 5),
     * so nothing here would gain from planting such a symlink that it
     * could not already do directly -- but it is the categorical fix
     * rather than a check for one attack shape, and it costs one open(). */
    int ctl_dir_fd = open(NW_CTL_DIR, O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (ctl_dir_fd < 0) die("open ctl dir");
    if (fchmod(ctl_dir_fd, 0700) < 0)
        die("ctl dir mode");
    close(ctl_dir_fd);
    char sockpath[sizeof(NW_CTL_DIR) + NW_NAME_LEN + 8];
    if (snprintf(sockpath, sizeof sockpath, "%s/%s.sock",
                 NW_CTL_DIR, name) >= (int)sizeof sockpath)
        die("ctl path");
    unlink(sockpath);
    /* The socket FILE's mode comes from the process umask at bind(2)
     * time, the same as any other file creation -- socket(2) and
     * bind(2) take no mode argument. Narrowed to exactly this pair so
     * nothing else nw-sup creates (loop devices, layer mounts) is
     * affected by a process-wide umask change; restored immediately
     * after, not left for the rest of the process's life. */
    mode_t ctl_old_umask = umask(0077);
    int lfd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (lfd < 0) die("ctl socket");
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof addr);
    addr.sun_family = AF_UNIX;
    if (snprintf(addr.sun_path, sizeof addr.sun_path, "%s", sockpath)
        >= (int)sizeof addr.sun_path)
        die("ctl path");
    if (bind(lfd, (struct sockaddr *)&addr, sizeof addr) < 0)
        die("ctl bind");
    umask(ctl_old_umask);
    if (listen(lfd, 4) < 0) die("ctl listen");

    /* Phase 3: the shared parent and its controller delegation are set
     * up once, here. Each individual generation gets its own leaf
     * cgroup below, immediately before that generation's clone3() --
     * never reused across a restart; see house_cgroup_open_generation()
     * for why. cg_gen is this supervisor's own monotonic counter,
     * naming each generation's directory. */
    cgroup_parent_setup(mem_high, mem_max, cpu_weight, task_cap);
    unsigned cg_gen = 0;

    /* docs/options/22-lock-unlock.md Section 6. Reuses the STOP'd idle
     * branch verbatim -- an unlocked house starts in exactly the state
     * a locked house reaches after an explicit STOP, with no new code
     * path. A locked house's behavior is byte-for-byte unchanged
     * (nw_lock=1 -> stopped=0 -> immediate fork, today's only path). */
    int stopped = !nw_lock;

    for (;;) {
        /* TERM during the previous house, or before this fork: do not
         * start another one so shutdown can finish. Routed through
         * nw_decide() like every other decision point below, even
         * though has_child=0 at loop top makes the answer always
         * NW_DECIDE_EXIT_SUP -- a live child is always either reaped
         * or never forked before control returns here, and
         * proofs/caller_decide.c proves that combination has no other
         * outcome. */
        if (stopping) {
            enum nw_decision d = nw_decide(nw_lock, nw_complete_on_0,
                                            stopping, stop_requested, 0,
                                            /*has_child=*/0,
                                            /*child_exited=*/0, 0,
                                            deaths, nw_budget);
            if (d != NW_DECIDE_EXIT_SUP)
                die("decide: unexpected decision while idle and stopping");
            unlink(sockpath);
            /* No cgroup to remove here: has_child=0 at this point means
             * whatever generation last ran already had its own
             * directory swept and rmdir'd immediately after it died
             * (see the death handling below), and no later generation
             * has been created yet. */
            _exit(0);
        }

        if (stopped) {
            struct pollfd pf;
            pf.fd = lfd;
            pf.events = POLLIN;
            pf.revents = 0;
            int pr = poll(&pf, 1, -1);
            if (pr < 0) {
                if (errno == EINTR) continue;
                die("poll ctl");
            }
            int c = accept(lfd, NULL, NULL);
            if (c < 0) continue; /* EAGAIN: poll readiness withdrawn */
            char buf[16];
            ssize_t n = read(c, buf, sizeof buf);
            if (n == 6 && memcmp(buf, "START\n", 6) == 0) {
                /* Phase 2: nw_decide() decides FORK-vs-stay-idle here
                 * rather than an unconditional `stopped = 0`.
                 * nw_lock==1 (the only value reachable today) always
                 * answers FORK, so this is byte-identical to the code
                 * it replaces; an unlocked house would get
                 * NW_DECIDE_IDLE back and stay in this branch, which
                 * proofs/caller_decide.c and tests/decide_seq.c already
                 * prove and test ahead of a plan field being able to
                 * select it. */
                enum nw_decision d = nw_decide(nw_lock, nw_complete_on_0,
                                                stopping, stop_requested,
                                                /*start_requested=*/1,
                                                0, 0, 0, deaths,
                                                nw_budget);
                ctl_reply(c, "OK\n");
                close(c);
                if (d == NW_DECIDE_FORK) {
                    stopped = 0;
                    continue;
                }
                if (d == NW_DECIDE_EXIT_SUP) {
                    /* Same reasoning as the `stopping` branch above:
                     * `stopped` is only reached after a generation's
                     * own directory was already swept and rmdir'd, and
                     * none has been created since. */
                    unlink(sockpath);
                    _exit(0);
                }
                continue; /* NW_DECIDE_IDLE: stay idle */
            } else if (n == 5 && memcmp(buf, "STOP\n", 5) == 0) {
                ctl_reply(c, "OK\n");
            } else {
                ctl_reply(c, "ERR bad request\n");
            }
            close(c);
            continue;
        }

        char cgroup_path[160];
        int cgroup_fd = house_cgroup_open_generation(name, cg_gen++, mem_high,
                                                       mem_max, cpu_weight,
                                                       task_cap,
                                                       cgroup_path,
                                                       sizeof cgroup_path);
        pid_t p = clone_into_cgroup(cgroup_fd);
        close(cgroup_fd); /* clone3 has already placed the child (or not
                            * placed it at all, on failure); nothing later
                            * in this iteration needs the fd again. */
        /* clone3() failing means this generation's cgroup was never
         * populated -- die_cgroup() rather than die(), for the same
         * reason house_cgroup_open_generation()'s own internal
         * failures use it: nothing could have written cgroup.kill to
         * a directory clone3() never successfully placed anything
         * into, so removing it before dying is unambiguously safe and
         * leaves nothing for a later attempt at this same generation
         * number to collide with. */
        if (p < 0) die_cgroup(cgroup_path, "clone house");
        if (p > 0) {
            child = p;
        }
        if (p == 0) {
            /* A genuinely pre-existing bug, found by this round's own
             * deterministic pre-exec-window test, not introduced by it:
             * fork() copies signal DISPOSITION, so this child inherits
             * nw-sup's own on_term handler for SIGTERM/SIGINT -- installed
             * once in main(), long before any of this generation's code
             * runs. A STOP (or shutdown TERM) arriving before execv()
             * below does not kill this process at all; it runs on_term()
             * -- which sets `stopping` and forwards to whatever stale
             * pid `child` held at this fork's own start -- and the
             * pre-exec copy simply continues running. execv() itself
             * would reset this on success (exec resets any installed
             * handler to default), so the gap is exactly the fork-to-exec
             * window -- normally microseconds and never hit by chance,
             * which is why nothing found it before a shim existed that
             * could land inside it deterministically. Reset explicitly
             * here rather than relying on execv() to clean up after
             * itself, since STOP must still work during this same
             * window the exec fence introduces a name for. */
            signal(SIGTERM, SIG_DFL);
            signal(SIGINT, SIG_DFL);

            /* A second, independent leak `tcb-review` found while auditing
             * the above: the signal MASK, not just disposition, also
             * survives fork -- and exec resets neither one's blocked-ness.
             * D11's own mask clear (top of main(), `empty` declared there)
             * runs exactly ONCE, before this restart loop starts. It is
             * not what keeps later generations clean. What actually does,
             * silently, every generation after the first: wait_house()
             * calls sigprocmask(SIG_BLOCK, &sc, NULL) to arm its SIGCHLD
             * signalfd, and never unblocks it afterward -- so by the time
             * THIS fork happens, nw-sup's own mask already has SIGCHLD
             * blocked, and this child inherits that. Uncaught, it rides
             * straight through execv() into the house's own image: any
             * longrun house that forks and waits on its own children,
             * expecting default SIGCHLD delivery, silently stops being
             * able to after this unit's first restart -- for the rest of
             * the supervisor's life, with nothing anywhere reporting it.
             * `empty` is the same all-zero set D11 built above; reusing
             * it here resets every blocked signal in one call rather than
             * naming SIGCHLD specifically, so the same fix also covers
             * anything else a future wait_house() change blocks and
             * forgets to unblock. */
            sigprocmask(SIG_SETMASK, &empty, NULL);

            if (lids & NW_LID_NEWNET) lid_netns();
            if (lids & NW_LID_NEWNS) lid_newns();

            /* cpu_mask/nice/sched_policy: syscalls on this process, not
             * cgroup files -- `tools/HANDOFF-resources.md`'s own table.
             * Applied here, before any lid, because these affect the
             * CALLING process's own attributes, which execv() preserves,
             * so the house inherits them without ever calling these
             * syscalls itself -- no seccomp allow-list entry is needed
             * for any of the three. "Not advisory": a declared value
             * the kernel refuses dies here, by name, rather than
             * running the house unbounded. */
            if (cpu_mask) {
                cpu_set_t set;
                CPU_ZERO(&set);
                for (int b = 0; b < 64 && b < CPU_SETSIZE; b++)
                    if (cpu_mask & (1ULL << b)) CPU_SET(b, &set);
                if (sched_setaffinity(0, sizeof set, &set) < 0)
                    die("cpu mask");
            }
            if (sched_policy != NW_SCHED_UNSET) {
                int real_policy = (sched_policy == NW_SCHED_OTHER) ? SCHED_NORMAL
                                 : (sched_policy == NW_SCHED_BATCH) ? SCHED_BATCH
                                 : SCHED_IDLE;
                struct sched_param sp;
                memset(&sp, 0, sizeof sp);
                if (sched_setscheduler(0, real_policy, &sp) < 0)
                    die("sched policy");
            }
            if (nice_val) {
                if (setpriority(PRIO_PROCESS, 0, nice_val) < 0)
                    die("nice");
            }
            /* docs/options/31 Section 8, amendment item A.3. Written
             * before capabilities are dropped -- confirmed by fault
             * injection (Section 10 item 3) that a negative value below
             * this process's current floor needs CAP_SYS_RESOURCE, which
             * this early in the child branch it still holds regardless
             * of what the plan's capabilities= is about to drop. A
             * write of 0 is skipped entirely (0 = unset), matching every
             * other resource-block field's convention -- the process
             * already starts at oom_score_adj=0 by kernel default, so
             * skipping is observably identical to writing 0. */
            if (oom_score_adj) {
                char buf[8];
                int bn = snprintf(buf, sizeof buf, "%d", oom_score_adj);
                int fd = open("/proc/self/oom_score_adj", O_WRONLY | O_CLOEXEC);
                if (fd < 0 || bn < 0
                    || write(fd, buf, (size_t)bn) != (ssize_t)bn)
                    die("oom score adj");
                close(fd);
            }
            /* docs/options/31 Section 9, corrected by fd-auditor: a
             * SINGLE unconditional `rl.rlim_cur = nofile` here can also
             * LOWER the limit, and lid_brick()/lid_landlock() (next)
             * still need to open several more descriptors after this
             * point -- a loop-control fd and a loop-device fd (two, for
             * a sized layer), the brick image fd, a Landlock ruleset fd
             * plus one O_PATH fd per bind. A declared `nofile=` tight
             * enough to starve that reachable via a baker-accepted
             * plan (nothing bounds `nofile` against kit size or lid
             * needs) produced `FAIL open loop-control errno=24` or
             * `FAIL open brick image errno=24` -- misdiagnosed as a
             * loop/brick defect, burning the restart budget on every
             * retry. So only the RAISE runs here, while CAP_SYS_
             * RESOURCE is still held (same ordering reason as
             * oom_score_adj above) and before anything else opens a
             * descriptor; a LOWER is deferred past every lid that still
             * needs headroom -- see the second half, just before
             * execv. Named errno either way, not a silent clamp --
             * pid1.c's own "named shortfall" convention for the
             * city-wide case. */
            int nofile_lower = 0;
            if (nofile) {
                struct rlimit rl;
                if (getrlimit(RLIMIT_NOFILE, &rl) < 0) die("nofile getrlimit");
                if ((rlim_t)nofile > rl.rlim_cur) {
                    rl.rlim_cur = nofile;
                    if (rl.rlim_max != RLIM_INFINITY && nofile > rl.rlim_max)
                        rl.rlim_max = nofile;
                    if (setrlimit(RLIMIT_NOFILE, &rl) < 0) die("nofile setrlimit");
                } else if ((rlim_t)nofile < rl.rlim_cur) {
                    nofile_lower = 1;
                }
            }

            if (brick) lid_brick(brick, layer, layer_bytes, binds, nbinds);
            if (lids & NW_LID_LANDLOCK) lid_landlock(binds, nbinds);
            /* docs/options/31 Section 7. BEFORE seccomp, not after --
             * this note's first draft put it after every lid, matching
             * the amendment's literal "after ... lids" wording, and
             * that broke every seccomp house immediately: the drop
             * itself needs prctl(PR_CAPBSET_DROP)/capget/capset, none
             * of which are in lids.c's strict_allow[], so a house with
             * `lids=seccomp` (declaring no capabilities= at all --
             * UNSET still runs this function, it drops everything)
             * died SIGSYS the moment apply_capabilities() ran after the
             * filter was installed. Measured directly: `--only happy`
             * killed every default-city house with `signal=31` before
             * this reordering.
             *
             * Moving it here instead of widening the allow-list is the
             * right fix, not merely the convenient one: seccomp's
             * allow-list governs the HOUSE's own process too, once
             * exec'd, so adding prctl/capget/capset to it would let a
             * confined house call them -- a real widening of what the
             * program itself can do, not just an nw-sup implementation
             * detail, and exactly the kind of addition runtime.md's
             * "adding a syscall to the allow-list requires naming the
             * unit that needs it and why" exists to gate. Landlock does
             * not need any capability this drop would remove --
             * landlock_restrict_self is designed to be callable by an
             * unprivileged process -- so nothing between the brick
             * pivot and this point is disturbed by moving it earlier.
             * seccomp stays the LAST, tightest gate, which is still the
             * property invariant 6's fixed lid order is about; this
             * class of field just cannot be the one thing after it. */
            apply_capabilities(capabilities, cap_last);
            /* The deferred half of `nofile`: only a LOWER reaches here
             * (the raise already happened above, before lid_brick()/
             * lid_landlock() needed their own headroom) -- and a lower
             * needs no privilege, so it is safe this late, after every
             * lid that opens a descriptor of its own has already run. */
            if (nofile_lower) {
                struct rlimit rl;
                if (getrlimit(RLIMIT_NOFILE, &rl) < 0) die("nofile getrlimit");
                rl.rlim_cur = nofile;
                if (setrlimit(RLIMIT_NOFILE, &rl) < 0) die("nofile setrlimit");
            }
            if (lids & NW_LID_SECCOMP) {
                if (nw_apply_house_seccomp() < 0) die("house seccomp");
                say("lid seccomp");
            }
            char *av[] = { (char *)name, NULL };
            execv(path, av);
            die("exec house");
        }
        /* child = p is set before this point so a TERM that arrives
         * between fork returning and wait_house can still signal the
         * house. Phase 2: nw_decide() names the one thing the main
         * thread of control ever needs to do about that here -- send
         * the TERM itself, for the fork-to-`child=p` race window
         * on_term()'s handler cannot see (decide.h explains why the
         * handler's own direct kill does not cover this case). Every
         * other window is covered by that handler, asynchronously;
         * this call site and the one in handle_ctl_live() are the only
         * two places nw_decide()'s NW_DECIDE_TERM_CHILD governs a kill
         * issued from ordinary control flow. */
        int st = 0;
        {
            enum nw_decision d = nw_decide(nw_lock, nw_complete_on_0,
                                            stopping, stop_requested, 0,
                                            /*has_child=*/1,
                                            /*child_exited=*/0, 0,
                                            deaths, nw_budget);
            if (d == NW_DECIDE_TERM_CHILD && p > 0)
                kill(p, nw_stop_signal);
        }
        st = wait_house(p, lfd);
        child = 0;

        /* Death autopsy, read BEFORE the sweep below: cgroup.kill sends
         * plain SIGKILL and does not touch memory.events, so reading
         * order does not matter for correctness, but reading "what
         * happened" before "clean up" is the more honest sequence to
         * read back later. This generation's cgroup was created fresh
         * by house_cgroup_open_generation() and never reused (see its
         * comment), so memory.events/pids.events start at 0 for every
         * generation -- the raw cumulative count read here already IS
         * this generation's own delta, with no baseline to subtract.
         * That is what satisfies the required control from Section 3:
         * a house OOM-killed once and then crashing plainly reads
         * oom_kill=0 on that second, different generation's own
         * cgroup, not a stale count carried over from the first. */
        unsigned long long oom_kill_delta = 0, pids_max_delta = 0;
        int oom_kill_avail = (cg_read_counter(cgroup_path, "memory.events",
                                               "oom_kill", &oom_kill_delta) == 0);
        int pids_max_avail = (cg_read_counter(cgroup_path, "pids.events",
                                               "max", &pids_max_delta) == 0);

        /* The defensive sweep: whatever this generation forked and did
         * not reap itself before dying does not survive past this
         * point. Unconditional, not an escalation-after-a-timeout
         * (this project refuses guessed constants; see runtime.md's
         * Liveness section) -- see cg_kill_sweep()'s own comment for
         * why that distinction matters here. Followed immediately by
         * rmdir: this generation's directory is never reused (see
         * house_cgroup_open_generation()), so cleanup happens now
         * rather than being deferred to whichever exit path below is
         * taken -- there is no cgroup_path left to remove by the time
         * any of them runs. */
        cg_kill_sweep(cgroup_path);
        rmdir(cgroup_path);

        /* The one decision point every child exit reaches, whatever
         * ended it: shutdown (this generation's own TERM or PID 1's),
         * an explicit STOP satisfied by this exit, a oneshot's clean
         * finish, or the restart budget. Same six facts, same
         * evidence record, same say() lines as before -- nw_decide()
         * only names which of them applies now. */
        enum nw_decision d = nw_decide(nw_lock, nw_complete_on_0, stopping,
                                        stop_requested, 0, /*has_child=*/1,
                                        /*child_exited=*/1, st, deaths,
                                        nw_budget);
        switch (d) {
        case NW_DECIDE_EXIT_SUP:
            /* Covers both of the two cases the code before this
             * refactor exited separately for: shutdown (TERM sent
             * before or during this run), and an unlocked oneshot's
             * clean exit(0) -- unreachable today since nw_lock is
             * always 1, exercised instead by tests/decide_seq.c and
             * proofs/caller_decide.c. Neither writes evidence or logs
             * a line, matching both of the originals. This generation's
             * cgroup was already swept and rmdir'd above. */
            unlink(sockpath);
            _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 0);

        case NW_DECIDE_IDLE:
            /* An explicit STOP, now satisfied by this exit -- the
             * house goes idle rather than being restarted or counted
             * against the budget. */
            stop_requested = 0;
            stopped = 1;
            continue;

        case NW_DECIDE_SPENT:
        case NW_DECIDE_RESTART: {
            /* D18: budget is a hard total for the life of this
             * supervisor. There is no window. A death slower than the
             * old window_s reset the tally and never hit the cap --
             * budget=3 window=1 dying every 1.2s restarted for as long
             * as anyone watched. */
            deaths++;
            /* The supervisor holds six facts at a death and reported
             * two. `restart X death=1` was byte-identical whether the
             * house segfaulted, was OOM-killed, or returned 1 from
             * main -- WIFEXITED was in scope and the branch that would
             * use it did not exist. The ordinal was bare: death=2
             * reads as one more chance or nearly spent depending on a
             * budget the reader did not have. And the death that
             * ENDED the house was the only one with no line at all,
             * because the exhaustion path _exit()s before the line
             * below.
             *
             * Nothing new is computed. WIFEXITED(st) selects the
             * form, WEXITSTATUS/WTERMSIG supplies the value, budget is
             * a parameter. No new state, no new syscall, no new
             * field, same say(), same fixed buffer.
             * NW-SUPERVISOR-OBSERVATION proposal 1. */
            char line[96];
            const char *verb = (d == NW_DECIDE_SPENT) ? "spent" : "restart";
            if (WIFEXITED(st))
                snprintf(line, sizeof line, "%s %s death=%d/%u exit=%d",
                         verb, name, deaths, nw_budget,
                         WEXITSTATUS(st));
            else
                snprintf(line, sizeof line, "%s %s death=%d/%u signal=%d",
                         verb, name, deaths, nw_budget, WTERMSIG(st));
            write_evidence(name, deaths, nw_budget, st,
                           oom_kill_delta, oom_kill_avail,
                           pids_max_delta, pids_max_avail);
            say(line);
            if (d == NW_DECIDE_SPENT) {
                /* This generation's cgroup was already swept and
                 * rmdir'd above; nothing left to remove here. */
                unlink(sockpath);
                _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 71);
            }
            continue; /* NW_DECIDE_RESTART: loop back to the top, where a
                        * fresh generation gets its own new cgroup */
        }

        default:
            /* WAIT/FORK/TERM_CHILD are not reachable from a
             * has_child=1,child_exited=1 call -- proved directly by
             * proofs/caller_decide.c's exhaustive run. A death this
             * project has already paid for twice (bugs 4/9/13): dying
             * by name beats silently misrouting into some other
             * branch. */
            die("decide: unexpected decision after a child exit");
        }
    }
}
