/* dawn — the mount stage. Runs as the initramfs init, before nw-root.
 *
 * Firmware -> kernel -> dawn -> nw-root. dawn mounts what the machine needs,
 * pivots into the real root, and execs nw-root. Then it is gone.
 *
 * WHY THIS EXISTS AND PID 1 DOES NOT DO IT
 *
 * nw-check must read the plan blob before anything is trusted, so whatever
 * holds the plan must already be mounted before PID 1 runs. PID 1 cannot
 * mount the thing it needs in order to learn what to mount. The only other
 * way out is a hardcoded device or filesystem type compiled into pid1.c --
 * a constant naming hardware, in the trusted core, in the process where a
 * fault does not crash a program but fails to boot a machine. That is the
 * fixed-descriptor-number class in a new costume. Rejected in
 * docs/options/06 as option E; this file is why it stays rejected.
 *
 * pid1.c mounts nothing and must keep mounting nothing. It takes a path and
 * reads it with open/read. Nothing in the TCB below this file learns what a
 * filesystem is.
 *
 * CONFIGURATION comes from the environment, and the bootloader supplies it
 * through the kernel command line: the kernel hands unrecognised key=value
 * parameters to init as environment. The lab harness sets the same variables
 * directly, so production and test take the identical code path and differ
 * only in which filesystem type is named. Nothing is defaulted -- a boot that
 * does not say what to mount fails loudly rather than guessing at hardware.
 *
 *   NW_ROOT, NW_ROOT_FSTYPE   the read-write root
 *   NW_ESP,  NW_ESP_FSTYPE    the ESP, mounted read-only
 *
 * TCB. See docs/options/06 for the layout this implements.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

/* The ONE project header dawn includes, and it is for one constant:
   NW_BRICK_MNT, the directory nw-sup mounts a brick image on. dawn creates
   it and nw-sup mounts on it, so the two must agree; writing the path in
   both files is precisely the drift class invariant 3 exists to remove, and
   blob.h is already where constants two programs share are declared.
   Justified here because a TCB file gaining a dependency needs a reason
   stated, and dawn is the one TCB .c that included no project header:
   it deliberately knows nothing about plans, and it still does not. It
   reads no field of any struct in blob.h and calls none of its three
   `static inline` accessors, so nothing is emitted -- checked, not
   assumed: the disassembly is byte-identical with the include and with a
   literal path in its place, and only the debug info differs, because it
   names the header. (This comment said "declarations only, no code" until
   that check was run. blob.h has three static inline functions; they emit
   nothing here only because nobody calls them, which is a different
   statement and the true one.) */
#include "blob.h"

#define NW_ROOT_MNT   "/sysroot"
#define NW_OLD_ROOT   "oldroot"       /* relative to the new root */
#define NW_ESP_AT     "/efi"          /* after pivot */
#define NW_SLOTS_AT   "/efi/slots"
#define NW_INIT_AT    "/nw/bin/nw-root"

static void say(const char *s, const char *a)
{
    char b[256];
    int n = snprintf(b, sizeof b, "[dawn] %s%s%s\n", s, a ? " " : "", a ? a : "");
    if (n > 0) { ssize_t r = write(2, b, (size_t)n); (void)r; }
}

static void die(const char *s, const char *a)
{
    char b[256];
    int n = snprintf(b, sizeof b, "[dawn] FAIL %s%s%s errno=%d\n",
                     s, a ? " " : "", a ? a : "", errno);
    if (n > 0) { ssize_t r = write(2, b, (size_t)n); (void)r; }
    _exit(80);
}

static const char *need_env(const char *k)
{
    const char *v = getenv(k);
    if (!v || !v[0])
        die("missing kernel parameter", k);
    return v;
}

/* mkdir -p, bounded: at most one level per call, walked left to right. */
static void mkpath(const char *p)
{
    char buf[256];
    size_t n = strlen(p);
    if (n >= sizeof buf) die("path too long", p);
    memcpy(buf, p, n + 1);
    for (size_t i = 1; i <= n; i++) {
        if (buf[i] != '/' && buf[i] != 0) continue;
        char save = buf[i];
        buf[i] = 0;
        if (mkdir(buf, 0755) < 0 && errno != EEXIST)
            die("mkdir", buf);
        buf[i] = save;
    }
}

/* Strict: this mount is ours to make and failing it is fatal. */
static void do_mount(const char *src, const char *tgt, const char *fs,
                     unsigned long flags)
{
    mkpath(tgt);
    if (mount(src, tgt, fs, flags, NULL) < 0)
        die("mount", tgt);
    say("mounted", tgt);
}

/* Ensure-mounted: for the kernel's own filesystems, where the requirement is
 * that they are present, not that dawn is the one who mounted them. A real
 * initramfs hands us an empty /dev and these succeed; a container may have
 * provided them already, and EBUSY then means the requirement is met. Used
 * only for /dev, /proc, /sys and cgroup2 -- never for the root or the ESP,
 * which must be freshly mounted by us or the boot is not what we think. */
static void ensure_mount(const char *src, const char *tgt, const char *fs,
                         unsigned long flags)
{
    mkpath(tgt);
    if (mount(src, tgt, fs, flags, NULL) == 0) {
        say("mounted", tgt);
        return;
    }
    if (errno == EBUSY) {
        say("already mounted", tgt);
        return;
    }
    die("mount", tgt);
}

int main(void)
{
    const char *root    = need_env("NW_ROOT");
    const char *rootfs  = need_env("NW_ROOT_FSTYPE");
    const char *esp     = need_env("NW_ESP");
    const char *espfs   = need_env("NW_ESP_FSTYPE");

    /* /dev first: mount(2) resolves a block device through a path, so the
     * device nodes must exist before the root filesystem can be mounted. */
    ensure_mount("devtmpfs", "/dev", "devtmpfs", 0);

    /* The two real filesystems. ESP read-only: it holds boot artifacts and
     * the A/B slots, and nothing at runtime writes it. */
    do_mount(root, NW_ROOT_MNT, rootfs, 0);
    do_mount(esp, NW_ROOT_MNT NW_ESP_AT, espfs, MS_RDONLY);

    /* Layout skeleton. Directories only: dawn creates the PARENTS and
     * never a per-house child, because a per-house directory depends on
     * the plan and dawn does not read the plan. Staging creates
     * <layer-id>/upper and <layer-id>/work from the plan before the boot
     * that needs them, which is what keeps that true.
     *
     * /nw/stores is GONE, not renamed. The store concept was the writable
     * layer under another name, so it collapsed into it rather than
     * sitting beside it; a directory called `stores` holding overlay
     * upper-dirs would mislead whoever reads it next. docs/options/05's
     * questions dissolve with it -- they were about a thing that no
     * longer exists separately.
     *
     * EDITED ACROSS AN OWNERSHIP LINE. dawn.c is Grok's file and the
     * trunk was not broken, so the narrow "fix it and flag it" rule did
     * not apply -- this is here because the operator asked for it
     * directly as part of writable areas. Flagged loudly per CLAUDE.md,
     * and it is three constants and a deletion. */
    mkpath(NW_ROOT_MNT NW_BRICK_DIR);
    mkpath(NW_ROOT_MNT NW_LAYER_DIR);
    /* Phase 2's mountpoint. An image is a file and cannot be bind-mounted
     * onto itself the way a directory brick is, so lid_brick() needs
     * somewhere to land it. One mkdir here, once, ever -- against a tmpfs
     * per house per boot, which buys nothing because this directory is
     * empty and pivot_root covers it a moment later, and which would still
     * need an existing directory to mount on. docs/plans/01. */
    mkpath(NW_ROOT_MNT NW_BRICK_MNT);
    mkpath(NW_ROOT_MNT "/" NW_OLD_ROOT);

    /* A bootloader-supplied root is MS_SHARED; pivot_root and MS_MOVE
     * both refuse that with EINVAL. unshare --mount (dawn-real-boot)
     * already made the namespace private, so the harness never saw it.
     * This remount is mount-stage work. */
    if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL) < 0)
        die("make-private", "/");

    if (chdir(NW_ROOT_MNT) < 0) die("chdir", NW_ROOT_MNT);
    /* Which branch ran decides whether /oldroot is a mount point. Carry
     * the answer rather than inferring it from an errno afterwards. */
    int oldroot_is_mounted = 0;
    if (syscall(SYS_pivot_root, ".", NW_OLD_ROOT) == 0) {
        say("pivoted", "pivot_root");
        oldroot_is_mounted = 1;
    } else {
        /* The current root is the kernel's initramfs rootfs. pivot_root
         * is defined to fail there (EINVAL: "the current root is on the
         * rootfs mount"). Measured 2026-09-11 under QEMU -kernel/-initrd:
         * both disks mounted, then FAIL pivot_root errno=22. The
         * documented sequence is MS_MOVE of the new root onto / plus
         * chroot. See Documentation/filesystems/ramfs-rootfs-initramfs.rst
         * and pivot_root(2) NOTES. The lab harness is not on rootfs, so
         * dawn-real-boot takes the pivot_root path above and does not
         * exercise this branch. */
        say("pivot_root unavailable, MS_MOVE", NULL);
        if (mount(".", "/", NULL, MS_MOVE, NULL) < 0)
            die("move-root", NULL);
        if (chroot(".") < 0)
            die("chroot", NULL);
        say("pivoted", "MS_MOVE");
    }
    if (chdir("/") < 0) die("chdir", "/");

    /* Kernel filesystems, now in the real root. */
    ensure_mount("proc", "/proc", "proc", 0);
    ensure_mount("sysfs", "/sys", "sysfs", 0);
    ensure_mount("devtmpfs", "/dev", "devtmpfs", 0);

    /* Ephemeral. Per-house scratch is deliberately not provided: the init
     * provisions nothing (CLAUDE.md invariant 5). */
    do_mount("tmpfs", "/run", "tmpfs", 0);
    do_mount("tmpfs", "/tmp", "tmpfs", 0);

    /* cgroup2 is mounted here and used by nothing yet. Houses become real
     * containers in a later piece of work and will need it present; opening
     * the mount code once is cheaper than opening it twice. No cgroup logic
     * exists in nw-sup and none should be added until that work. */
    ensure_mount("cgroup2", "/sys/fs/cgroup", "cgroup2", 0);

    /* No errno is tolerated here, because the branch above already knows
     * the answer this code used to guess at.
     *
     * The condition was `errno != ENOENT && errno != EINVAL &&
     * errno != ENODEV` under a comment saying "the MS_MOVE path leaves no
     * oldroot to detach; ENOENT is that path". The comment named the wrong
     * errno. mkpath() creates NW_ROOT_MNT/oldroot BEFORE either branch
     * runs, so on the MS_MOVE path /oldroot exists as a plain directory
     * that was never mounted on, and umount2 answers EINVAL -- never
     * ENOENT. Tolerating EINVAL was the thing keeping that path alive.
     *
     * Narrowing the list to ENOENT, which is what the comment defended,
     * panicked a real boot on the first try: `[dawn] FAIL umount oldroot
     * errno=22` then `Kernel panic - not syncing: Attempted to kill init`.
     * Measured under `make qemu` 2026-09-11 -- the MS_MOVE branch is the
     * one harness.md records as having no lab coverage, so the suite was
     * green throughout.
     *
     * So: do not tolerate an errno, and do not call umount2 where it is
     * meaningless. On the pivot_root branch /oldroot IS a mount point and
     * any failure is a real one. On the MS_MOVE branch there is nothing
     * mounted there and nothing to detach. */
    if (oldroot_is_mounted && umount2("/" NW_OLD_ROOT, MNT_DETACH) < 0)
        die("umount oldroot", NULL);
    if (rmdir("/" NW_OLD_ROOT) < 0 &&
        errno != EBUSY && errno != ENOTEMPTY && errno != ENOENT)
        die("rmdir oldroot", NULL);

    say("exec", NW_INIT_AT);
    /* Production argv is --slots only. --hold-ms is a lab flag on
     * nw-root, passed by the suite's boot() helper which execs
     * nw-root directly. Dawn used to forward getenv("NW_HOLD_MS"):
     * the kernel hands unrecognised cmdline tokens to init as
     * environment, so NW_HOLD_MS=800 on the bootloader line shut
     * the city and powered off. Detection in mkboot --check was
     * hardened; the mechanism was left alone. The mechanism is
     * gone. dawn-real-boot must SIGTERM PID 1 (or stop going
     * through dawn for the timer) — that test is Claude's file. */
    execl(NW_INIT_AT, "nw-root", "--slots", NW_SLOTS_AT, (char *)0);
    die("exec nw-root", NW_INIT_AT);
    return 80;
}
