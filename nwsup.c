#define _GNU_SOURCE
#include "blob.h"
#include "lids.h"
#include "sha256.h"
#include "store.h"

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
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/prctl.h>
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

/* docs/options/15-per-house-scheduling.md. Real, syscall/kernel-data
 * level, not simulated -- the same shape sys_landlock_create_ruleset()
 * already uses for Landlock: ask the kernel itself, not a proxy for it.
 *
 * Two checks, cheapest first. /sys/kernel/sched_ext is the kobject the
 * scheduler class creates unconditionally the moment it initialises, so
 * its absence alone is decisive on a kernel where the feature was left
 * out of the build (measured on this project's own sandbox: absent,
 * because CONFIG_SCHED_CLASS_EXT is not set there). Where the directory
 * exists, the second check confirms the specific struct_ops type this
 * mechanism needs is actually registered, by scanning the running
 * kernel's own exported BTF for the literal type name -- the same
 * authoritative source a userspace loader (libbpf, bpftool) has to
 * resolve before it could load a struct_ops program against it, and the
 * same file this feature's own design note measured by hand before a
 * line of this function was written. No allocation: mmap rather than a
 * read into a buffer, scanned in place, unmapped immediately. Bounded:
 * the scan is over exactly the kernel-reported size of the file, no
 * recursion, no unbounded loop. */
#define NW_SCHED_EXT_MARKER "/sys/kernel/sched_ext"
#define NW_SCHED_EXT_BTF    "/sys/kernel/btf/vmlinux"
#define NW_SCHED_EXT_TYPE   "sched_ext_ops"

static int sched_ext_supported(void)
{
    struct stat mst;
    if (stat(NW_SCHED_EXT_MARKER, &mst) < 0) return 0;

    int fd = open(NW_SCHED_EXT_BTF, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    struct stat bst;
    if (fstat(fd, &bst) < 0 || bst.st_size <= 0) { close(fd); return 0; }
    void *m = mmap(NULL, (size_t)bst.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (m == MAP_FAILED) return 0;

    const char *needle = NW_SCHED_EXT_TYPE;
    size_t nlen = strlen(needle);
    const unsigned char *base = m;
    int found = 0;
    for (size_t i = 0; i + nlen <= (size_t)bst.st_size; i++) {
        if (memcmp(base + i, needle, nlen) == 0) { found = 1; break; }
    }
    munmap(m, (size_t)bst.st_size);
    return found;
}

/* This round names exactly one policy, NW_SCHED_EXT_DEFAULT -- the
 * brief's own "no-op, prove the plumbing" policy -- and there is no
 * working BPF artifact for it yet (docs/options/15's own measurement:
 * nothing available to this project can build one). So the only
 * behavior this function can have TODAY is the refusal, which is real:
 * it dies loudly rather than starting a house whose plan says it is
 * scheduled and is not, the same rule every other lid already follows.
 * Adding a real accept path later means adding a branch here, not
 * replacing this one. */
static void apply_sched_ext(unsigned sched_ext)
{
    if (sched_ext == NW_SCHED_EXT_UNSET) return;
    if (!sched_ext_supported())
        die("sched-ext unsupported");
    /* tcb-review's MEDIUM finding: a bare `say()` here, on a kernel that
     * DOES pass sched_ext_supported(), would print the same line every
     * other successful lid application prints while loading no policy
     * at all -- nw_res's own defect ("declared, validated... and doing
     * nothing") reached through a branch nothing available to this
     * project can exercise to notice. There are exactly two honest
     * outcomes for a declared sched-ext=: refused, by name, or loaded
     * and verified. There is no third one, so until a real loader
     * exists this branch is not a success path either -- it dies with
     * a DIFFERENT, distinguishing reason from the capability refusal
     * above, matching the design note's answer to question 5. Replace
     * this whole branch, not the die() call inside it, the day a real
     * policy artifact exists. */
    die("sched-ext no policy artifact");
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

/*
 * Block until house `p` exits, by poll() on its pidfd.
 *
 * extra_fd, if >= 0, is a second poll member. This round nothing
 * real lives there (no start/stop socket, no log pipe). The slot
 * exists so the next fd is an array entry, not a rewrite of the
 * wait. A ready extra fd does not reap the house; we loop.
 *
 * Reap is waitpid(p, ...) after poll says the pidfd is readable.
 * That waitpid does not block. It is not waitpid(-1): the pid is
 * the one we opened the pidfd on.
 *
 * Lids still run in the child after fork, before exec. fork is
 * unchanged; clone3(CLONE_PIDFD) would change how the child is
 * born and is not used.
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
 */
static int stop_requested;

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
            kill(live, SIGTERM);
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
        /* Live socket cannot sit behind blocking waitpid: that
         * re-enables the orphan-and-die path the last review closed.
         * signalfd(SIGCHLD) is the second wakeup, no timeout.
         * If signalfd also fails, poll the socket with a 50ms bound
         * and waitpid WNOHANG. */
        if (extra_fd < 0) {
            int st = 0;
            if (waitpid(p, &st, 0) < 0)
                die("wait house");
            return st;
        }
        sigset_t sc;
        sigemptyset(&sc);
        sigaddset(&sc, SIGCHLD);
        sigprocmask(SIG_BLOCK, &sc, NULL);
        wake = signalfd(-1, &sc, SFD_CLOEXEC);
    }

    struct pollfd pf[2];
    nfds_t n = 1;
    int timeout = -1;
    if (pfd >= 0) {
        pf[0].fd = pfd;
        pf[0].events = POLLIN;
    } else if (wake >= 0) {
        pf[0].fd = wake;
        pf[0].events = POLLIN;
    } else {
        pf[0].fd = extra_fd;
        pf[0].events = POLLIN;
        timeout = 50;
    }
    pf[0].revents = 0;
    if (extra_fd >= 0 && pf[0].fd != extra_fd) {
        pf[1].fd = extra_fd;
        pf[1].events = POLLIN;
        pf[1].revents = 0;
        n = 2;
    }

    for (;;) {
        int pr = poll(pf, n, timeout);
        if (pr < 0) {
            if (errno == EINTR)
                continue;
            if (pfd >= 0) close(pfd);
            if (wake >= 0) close(wake);
            die("poll house");
        }
        if (n == 2 && (pf[1].revents & (POLLIN | POLLHUP | POLLERR))) {
            handle_ctl_live(extra_fd, p);
            pf[1].revents = 0;
        }
        if (timeout == 50) {
            int st = 0;
            pid_t r = waitpid(p, &st, WNOHANG);
            if (r == p) {
                if (pfd >= 0) close(pfd);
                if (wake >= 0) close(wake);
                return st;
            }
            if (pf[0].fd == extra_fd &&
                (pf[0].revents & (POLLIN | POLLHUP | POLLERR))) {
                handle_ctl_live(extra_fd, p);
                pf[0].revents = 0;
            }
            continue;
        }
        if (pf[0].revents & (POLLIN | POLLHUP | POLLERR)) {
            if (pfd >= 0)
                break;
            /* signalfd: drain and reap */
            struct signalfd_siginfo si;
            ssize_t ign = read(wake, &si, sizeof si);
            (void)ign;
            int st = 0;
            pid_t r = waitpid(p, &st, WNOHANG);
            if (r == p) {
                close(wake);
                return st;
            }
            pf[0].revents = 0;
            continue;
        }
        pf[0].revents = 0;
    }

    int st = 0;
    if (waitpid(p, &st, 0) < 0) {
        if (pfd >= 0) close(pfd);
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
static void write_evidence(const char *name, int deaths, unsigned budget,
                            int st)
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

    unsigned char record[256 + NW_EVIDENCE_TAIL_MAX];
    int hdr_len = snprintf((char *)record, 256,
        "NWEVT1\nunit=%s\nreason=%s\nvalue=%d\ndeath=%d\nbudget=%u\n"
        "tail_bytes=%zu\n--\n",
        name, WIFEXITED(st) ? "exit" : "signal",
        WIFEXITED(st) ? WEXITSTATUS(st) : WTERMSIG(st),
        deaths, budget, tail_n);
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
    unsigned sched_ext = NW_SCHED_EXT_UNSET;
    const char *e;
    if ((e = getenv("NW_LIDS"))) lids = (unsigned)atoi(e);
    if ((e = getenv("NW_BUDGET"))) budget = (unsigned)atoi(e);
    if ((e = getenv("NW_KIND"))) kind = (unsigned)atoi(e);
    /* RE-VALIDATED for the same reason NW_BRICK/NW_LAYER are: nw-sup reads
     * its unit from the environment, not the sealed blob, so nothing the
     * baker or nw-check did stands behind this value. */
    if ((e = getenv("NW_SCHED_EXT"))) sched_ext = (unsigned)atoi(e);
    if (sched_ext > NW_SCHED_EXT_MAX) die("sched-ext value");
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

    int deaths = 0;
    if (mkdir("/nw", 0755) < 0 && errno != EEXIST)
        die("ctl parent");
    if (mkdir(NW_CTL_DIR, 0755) < 0 && errno != EEXIST)
        die("ctl dir");
    char sockpath[sizeof(NW_CTL_DIR) + NW_NAME_LEN + 8];
    if (snprintf(sockpath, sizeof sockpath, "%s/%s.sock",
                 NW_CTL_DIR, name) >= (int)sizeof sockpath)
        die("ctl path");
    unlink(sockpath);
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
    if (listen(lfd, 4) < 0) die("ctl listen");

    int stopped = 0;

    for (;;) {
        /* TERM during the previous house, or before this fork: do not
         * start another one so shutdown can finish. */
        if (stopping) {
            unlink(sockpath);
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
                ctl_reply(c, "OK\n");
                close(c);
                stopped = 0;
                continue;
            } else if (n == 5 && memcmp(buf, "STOP\n", 5) == 0) {
                ctl_reply(c, "OK\n");
            } else {
                ctl_reply(c, "ERR bad request\n");
            }
            close(c);
            continue;
        }

        pid_t p = fork();
        if (p < 0) die("fork house");
        if (p > 0)
            child = p;
        if (p == 0) {
            if (lids & NW_LID_NEWNET) lid_netns();
            if (lids & NW_LID_NEWNS) lid_newns();
            /* BEFORE lid_brick(), not after: tcb-review's HIGH finding.
             * sched_ext_supported() asks a question about the MACHINE's
             * kernel (/sys/kernel/sched_ext, /sys/kernel/btf/vmlinux),
             * and lid_brick()'s pivot_root makes the house's `/` the
             * brick -- after which nothing under the machine's /sys is
             * reachable at all unless the plan happens to bind it. Applied
             * after the pivot, EVERY brick house with a declared
             * sched-ext= died with "sched-ext unsupported" regardless of
             * the real host kernel's capability -- a mount-visibility
             * artifact wearing a capability-gap message, on a kernel that
             * might genuinely have sched_ext. NEWNET/NEWNS do not affect
             * /sys's visibility (a fresh mount namespace starts as a copy
             * of the parent's table; nothing here unmounts anything), so
             * this is the earliest point that is still unconditionally
             * correct. */
            apply_sched_ext(sched_ext);
            if (brick) lid_brick(brick, layer, layer_bytes, binds, nbinds);
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
         * between fork returning and wait_house can still signal the
         * house. If stopping is already set, do not block on a house
         * that was never asked to stop: TERM it, then wait via pidfd. */
        int st = 0;
        if (stopping) {
            if (p > 0)
                kill(p, SIGTERM);
            st = wait_house(p, lfd);
            child = 0;
            unlink(sockpath);
            _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 0);
        }
        st = wait_house(p, lfd);
        child = 0;

        /* Same TERM that PID 1 sent to start shutdown. Restarting here
         * races the city closing: the house comes back after it was
         * asked to stop. No extra channel — the signal is the news. */
        if (stopping) {
            unlink(sockpath);
            _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 0);
        }

        if (stop_requested) {
            stop_requested = 0;
            stopped = 1;
            continue;
        }

        /* Only a oneshot is finished by a clean exit. For a longrun, exit 0
         * is as unexpected as any other exit and goes to the budget: a
         * compositor that quits or a daemon that reloads itself should come
         * back, not vanish silently. (D12) */
        if (kind == NW_KIND_ONESHOT && WIFEXITED(st) && WEXITSTATUS(st) == 0) {
            unlink(sockpath);
            _exit(0);
        }

        /* D18: budget is a hard total for the life of this supervisor.
         * There is no window. A death slower than the old window_s
         * reset the tally and never hit the cap — budget=3 window=1
         * dying every 1.2s restarted for as long as anyone watched. */
        deaths++;
        /* The supervisor holds six facts at a death and reported two.
         * `restart X death=1` was byte-identical whether the house
         * segfaulted, was OOM-killed, or returned 1 from main -- WIFEXITED
         * was in scope and the branch that would use it did not exist. The
         * ordinal was bare: death=2 reads as one more chance or nearly
         * spent depending on a budget the reader did not have. And the
         * death that ENDED the house was the only one with no line at all,
         * because the exhaustion path _exit()s before the line below.
         *
         * Nothing new is computed. WIFEXITED(st) selects the form,
         * WEXITSTATUS/WTERMSIG supplies the value, budget is a parameter.
         * No new state, no new syscall, no new field, same say(), same
         * fixed buffer. NW-SUPERVISOR-OBSERVATION proposal 1. */
        char line[96];
        if (budget == 0 || deaths > (int)budget) {
            if (WIFEXITED(st))
                snprintf(line, sizeof line, "spent %s death=%d/%u exit=%d",
                         name, deaths, (unsigned)budget, WEXITSTATUS(st));
            else
                snprintf(line, sizeof line, "spent %s death=%d/%u signal=%d",
                         name, deaths, (unsigned)budget, WTERMSIG(st));
            write_evidence(name, deaths, budget, st);
            say(line);
            unlink(sockpath);
            _exit(WIFEXITED(st) ? WEXITSTATUS(st) : 71);
        }
        if (WIFEXITED(st))
            snprintf(line, sizeof line, "restart %s death=%d/%u exit=%d",
                     name, deaths, (unsigned)budget, WEXITSTATUS(st));
        else
            snprintf(line, sizeof line, "restart %s death=%d/%u signal=%d",
                     name, deaths, (unsigned)budget, WTERMSIG(st));
        write_evidence(name, deaths, budget, st);
        say(line);
    }
}
