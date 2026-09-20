/* btrfsprobe.c -- the load-bearing check for the "house is a durable COW
 * volume" proposal.
 *
 * The claim: snapshot creation is not CAP_SYS_ADMIN, it is
 * inode_owner_or_capable on the source subvolume. So if a house's room is
 * a btrfs subvolume owned by that house's outer uid, and the visit maps
 * 0 -> that uid, the house can snapshot itself unprivileged. Commit
 * becomes an atomic filesystem object instead of a marker file that can
 * outrun its data.
 *
 * The proposal is explicit that if this returns EPERM in a user namespace
 * the design does not work unprivileged and should not be taken.
 *
 * Tested here, in the guest, on the real boot path:
 *   A  owner snapshot with no namespace at all           (the baseline)
 *   B1 same ioctl inside a userns, fd opened INSIDE
 *   B2 same, fd opened OUTSIDE and inherited across the unshare
 *   B3 the subvolume is the UPPER of a live overlay, ioctl on the raw path
 *   C  does the snapshot actually freeze the tree -- write after snapshot,
 *      confirm the snapshot does not see it
 */
#define _GNU_SOURCE
#include <sched.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdarg.h>
#include <linux/btrfs.h>
#include <linux/loop.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>

#define R "/nwbtr"

static void say(const char *fmt, ...)
{
    char b[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}
static void wrp(const char *p, const char *v)
{ int fd = open(p, O_WRONLY); if (fd >= 0) { (void)!write(fd, v, strlen(v)); close(fd); } }
static void loadmod(const char *p)
{ int m = open(p, O_RDONLY); if (m >= 0) { syscall(__NR_finit_module, m, "", 0); close(m); } }

/* snapshot <srcfd> into <parentfd>/<name> */
static int snap(int parentfd, int srcfd, const char *name)
{
    struct btrfs_ioctl_vol_args_v2 a;
    memset(&a, 0, sizeof a);
    a.fd = srcfd;
    strncpy(a.name, name, sizeof a.name - 1);
    return ioctl(parentfd, BTRFS_IOC_SNAP_CREATE_V2, &a);
}
static int mksubvol(int parentfd, const char *name)
{
    struct btrfs_ioctl_vol_args_v2 a;
    memset(&a, 0, sizeof a);
    strncpy(a.name, name, sizeof a.name - 1);
    return ioctl(parentfd, BTRFS_IOC_SUBVOL_CREATE_V2, &a);
}

static void enter_userns(uid_t outer)
{
    if (prctl(PR_SET_DUMPABLE, 1, 0, 0, 0) < 0) say("   (dumpable failed)\n");
    if (unshare(CLONE_NEWUSER | CLONE_NEWNS) < 0) {
        say("   unshare FAILED errno=%d\n", errno); _exit(1); }
    wrp("/proc/self/setgroups", "deny");
    char b[64];
    snprintf(b, sizeof b, "0 %d 1\n", (int)outer); wrp("/proc/self/uid_map", b);
    snprintf(b, sizeof b, "0 %d 1\n", (int)outer); wrp("/proc/self/gid_map", b);
}

int main(void)
{
    mkdir(R, 0755);
    loadmod("/nw/libcrc32c.ko"); loadmod("/nw/xor.ko");
    loadmod("/nw/raid6_pq.ko");  loadmod("/nw/btrfs.ko");
    loadmod("/nw/overlay.ko");

    /* a btrfs filesystem in a file, on a loop device */
    int f = open(R "/fs.img", O_RDWR | O_CREAT | O_TRUNC, 0644);
    if (f < 0) { say("[b] create fs.img FAILED %d\n", errno); return 1; }
    if (ftruncate(f, 512ull << 20) < 0) { say("[b] ftruncate FAILED %d\n", errno); return 1; }
    close(f);
    say("[b] made a 512 MiB file for btrfs\n");
    /* mkfs is not in the guest -- the harness formats it on the host */

    int c = open("/dev/loop-control", O_RDWR);
    int idx = c >= 0 ? ioctl(c, LOOP_CTL_GET_FREE) : -1;
    if (c >= 0) close(c);
    char dev[32]; snprintf(dev, sizeof dev, "/dev/loop%d", idx);
    int lf = open(dev, O_RDWR | O_CLOEXEC);
    int bf = open("/nw/btrfs.img", O_RDWR | O_CLOEXEC);
    if (idx < 0 || lf < 0 || bf < 0 || ioctl(lf, LOOP_SET_FD, bf) < 0) {
        say("[b] loop setup FAILED errno=%d (need /nw/btrfs.img preformatted)\n", errno);
        return 1;
    }
    close(bf); close(lf);
    mkdir(R "/mnt", 0755);
    if (mount(dev, R "/mnt", "btrfs", 0, "user_subvol_rm_allowed") < 0) {
        say("[b] mount btrfs FAILED errno=%d (%s)\n", errno, strerror(errno));
        return 1;
    }
    say("[b] btrfs mounted from %s\n", dev);

    /* a room, owned by the house's outer uid */
    int top = open(R "/mnt", O_RDONLY | O_DIRECTORY);
    if (mksubvol(top, "room1") < 0) {
        say("[b] SUBVOL_CREATE FAILED errno=%d (%s)\n", errno, strerror(errno)); return 1; }
    say("[b] subvolume 'room1' created\n");
    chown(R "/mnt/room1", 1001, 1001);
    chmod(R "/mnt/room1", 0700);
    int w = open(R "/mnt/room1/first", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (w >= 0) { (void)!write(w, "one\n", 4); fsync(w); close(w); }
    chown(R "/mnt/room1/first", 1001, 1001);
    mkdir(R "/mnt/snaps", 0755); chown(R "/mnt/snaps", 1001, 1001);

    /* ---- A: owner snapshot, NO namespace, as uid 1001 ---- */
    int st;
    pid_t a = fork();
    if (a == 0) {
        if (setgid(1001) || setuid(1001)) { say("[A] setuid FAILED\n"); _exit(1); }
        int src = open(R "/mnt/room1", O_RDONLY | O_DIRECTORY);
        int par = open(R "/mnt/snaps", O_RDONLY | O_DIRECTORY);
        int r = (src < 0 || par < 0) ? -1 : snap(par, src, "A");
        say("[A] owner snapshot, no namespace : %s (errno=%d %s)\n",
            r == 0 ? "OK" : "FAILED", r == 0 ? 0 : errno, r == 0 ? "" : strerror(errno));
        _exit(0);
    }
    waitpid(a, &st, 0);

    /* ---- B1: inside a userns, fd opened INSIDE ---- */
    pid_t b1 = fork();
    if (b1 == 0) {
        if (setgid(1001) || setuid(1001)) _exit(1);
        enter_userns(1001);
        say("[B1] inside userns uid=%d (outer 1001)\n", (int)getuid());
        int src = open(R "/mnt/room1", O_RDONLY | O_DIRECTORY);
        int par = open(R "/mnt/snaps", O_RDONLY | O_DIRECTORY);
        int r = (src < 0 || par < 0) ? -1 : snap(par, src, "B1");
        say("[B1] snapshot, fd opened INSIDE  : %s (errno=%d %s)\n",
            r == 0 ? "OK" : "FAILED", r == 0 ? 0 : errno, r == 0 ? "" : strerror(errno));
        _exit(0);
    }
    waitpid(b1, &st, 0);

    /* ---- B2: fd opened OUTSIDE, inherited across the unshare ---- */
    pid_t b2 = fork();
    if (b2 == 0) {
        if (setgid(1001) || setuid(1001)) _exit(1);
        int src = open(R "/mnt/room1", O_RDONLY | O_DIRECTORY);
        int par = open(R "/mnt/snaps", O_RDONLY | O_DIRECTORY);
        enter_userns(1001);
        int r = (src < 0 || par < 0) ? -1 : snap(par, src, "B2");
        say("[B2] snapshot, fd opened OUTSIDE : %s (errno=%d %s)\n",
            r == 0 ? "OK" : "FAILED", r == 0 ? 0 : errno, r == 0 ? "" : strerror(errno));
        _exit(0);
    }
    waitpid(b2, &st, 0);

    /* ---- B3: the room is the UPPER of a live overlay ---- */
    mkdir(R "/low", 0755); mkdir(R "/merged", 0755);
    mkdir(R "/mnt/room1/.work", 0700); chown(R "/mnt/room1/.work", 1001, 1001);
    char opt[256];
    snprintf(opt, sizeof opt, "lowerdir=%s,upperdir=%s,workdir=%s",
             R "/low", R "/mnt/room1", R "/mnt/room1/.work");
    int ov = mount("overlay", R "/merged", "overlay", 0, opt);
    say("[B3] overlay with the room as upper: %s (errno=%d)\n",
        ov == 0 ? "mounted" : "FAILED", ov == 0 ? 0 : errno);
    if (ov == 0) {
        pid_t b3 = fork();
        if (b3 == 0) {
            if (setgid(1001) || setuid(1001)) _exit(1);
            enter_userns(1001);
            int src = open(R "/mnt/room1", O_RDONLY | O_DIRECTORY);   /* RAW path */
            int par = open(R "/mnt/snaps", O_RDONLY | O_DIRECTORY);
            int r = (src < 0 || par < 0) ? -1 : snap(par, src, "B3");
            say("[B3] snapshot the raw upper     : %s (errno=%d %s)\n",
                r == 0 ? "OK" : "FAILED", r == 0 ? 0 : errno, r == 0 ? "" : strerror(errno));
            _exit(0);
        }
        waitpid(b3, &st, 0);
    }

    /* ---- C: is the snapshot actually frozen ---- */
    int n = open(R "/mnt/room1/second", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (n >= 0) { (void)!write(n, "two\n", 4); close(n); }
    struct stat s2;
    int live = stat(R "/mnt/room1/second", &s2) == 0;
    int inA  = stat(R "/mnt/snaps/A/second", &s2) == 0;
    int oldA = stat(R "/mnt/snaps/A/first", &s2) == 0;
    say("[C] after snapshot A, wrote 'second' to the live room\n");
    say("[C] live room has second: %s | snapshot A has second: %s | snapshot A has first: %s\n",
        live ? "yes" : "no", inA ? "YES -- not frozen" : "no -- frozen correctly",
        oldA ? "yes" : "NO -- snapshot is empty");
    say("[b] done\n");
    return 0;
}
