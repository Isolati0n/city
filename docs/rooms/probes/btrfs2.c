/* btrfs2.c -- three follow-ups to the room design.
 *
 *  B3 (fixed). Last run put the overlay's workdir INSIDE the subvolume it
 *      was using as upperdir, which overlayfs rejects with EINVAL. That was
 *      my setup, not the design. Redone with a sibling workdir on the same
 *      filesystem: can the house snapshot its room while that room is the
 *      live upper of a running overlay?
 *
 *  D1  Deletion. The proposal flags this as a concrete operational failure:
 *      with user_subvol_rm_allowed a house can delete its own snapshots;
 *      without it a house can create snapshots it cannot remove, and the
 *      machine fills up. Test whether an unprivileged house in its userns
 *      can actually delete one.
 *
 *  D2  The launch-pad. The proposal's 6.12 answer to the pre-pivot window:
 *      init builds ONE mount namespace after the image mounts, containing
 *      only the seals and the rooms filesystem, and every visit is born
 *      there. It never sees the machine root because its parent does not.
 *      Test whether a child born in such a namespace can reach the boot
 *      files by any path.
 */
#define _GNU_SOURCE
#include <sched.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdarg.h>
#include <dirent.h>
#include <linux/btrfs.h>
#include <linux/loop.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>

#define R "/nwb2"

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

static int snap(int parentfd, int srcfd, const char *name)
{ struct btrfs_ioctl_vol_args_v2 a; memset(&a, 0, sizeof a);
  a.fd = srcfd; strncpy(a.name, name, sizeof a.name - 1);
  return ioctl(parentfd, BTRFS_IOC_SNAP_CREATE_V2, &a); }
static int mksubvol(int parentfd, const char *name)
{ struct btrfs_ioctl_vol_args_v2 a; memset(&a, 0, sizeof a);
  strncpy(a.name, name, sizeof a.name - 1);
  return ioctl(parentfd, BTRFS_IOC_SUBVOL_CREATE_V2, &a); }
static int rmsubvol(int parentfd, const char *name)
{ struct btrfs_ioctl_vol_args_v2 a; memset(&a, 0, sizeof a);
  strncpy(a.name, name, sizeof a.name - 1);
  return ioctl(parentfd, BTRFS_IOC_SNAP_DESTROY_V2, &a); }

static void enter_userns(uid_t outer)
{
    prctl(PR_SET_DUMPABLE, 1, 0, 0, 0);
    if (unshare(CLONE_NEWUSER | CLONE_NEWNS) < 0) { say("   unshare %d\n", errno); _exit(1); }
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
    loadmod("/nw/overlay.ko");   loadmod("/nw/erofs.ko");

    int c = open("/dev/loop-control", O_RDWR);
    int idx = c >= 0 ? ioctl(c, LOOP_CTL_GET_FREE) : -1;
    if (c >= 0) close(c);
    char dev[32]; snprintf(dev, sizeof dev, "/dev/loop%d", idx);
    int lf = open(dev, O_RDWR | O_CLOEXEC), bf = open("/nw/btrfs.img", O_RDWR | O_CLOEXEC);
    if (idx < 0 || lf < 0 || bf < 0 || ioctl(lf, LOOP_SET_FD, bf) < 0) {
        say("[b2] loop FAILED %d\n", errno); return 1; }
    close(bf); close(lf);
    mkdir(R "/mnt", 0755);
    if (mount(dev, R "/mnt", "btrfs", 0, "user_subvol_rm_allowed") < 0) {
        say("[b2] mount btrfs FAILED %d (%s)\n", errno, strerror(errno)); return 1; }
    say("[b2] btrfs mounted with user_subvol_rm_allowed\n");

    int top = open(R "/mnt", O_RDONLY | O_DIRECTORY);
    mksubvol(top, "room");
    chown(R "/mnt/room", 1001, 1001); chmod(R "/mnt/room", 0700);
    mkdir(R "/mnt/snaps", 0755); chown(R "/mnt/snaps", 1001, 1001);
    /* workdir must be a SIBLING on the same fs, not inside the upper */
    mkdir(R "/mnt/work", 0700); chown(R "/mnt/work", 1001, 1001);
    mkdir(R "/mnt/low", 0755);
    int q = open(R "/mnt/low/base", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (q >= 0) { (void)!write(q, "lower\n", 6); close(q); }
    mkdir(R "/merged", 0755); chown(R "/merged", 1001, 1001);

    char opt[320];
    snprintf(opt, sizeof opt, "lowerdir=%s,upperdir=%s,workdir=%s",
             R "/mnt/low", R "/mnt/room", R "/mnt/work");
    int ov = mount("overlay", R "/merged", "overlay", 0, opt);
    say("[B3] overlay, workdir a SIBLING of the upper: %s (errno=%d %s)\n",
        ov == 0 ? "mounted" : "FAILED", ov == 0 ? 0 : errno, ov == 0 ? "" : strerror(errno));

    if (ov == 0) {
        int w = open(R "/merged/live", O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (w >= 0) { (void)!write(w, "written through the overlay\n", 28); close(w); }
        int st;
        pid_t p = fork();
        if (p == 0) {
            if (setgid(1001) || setuid(1001)) _exit(1);
            enter_userns(1001);
            int src = open(R "/mnt/room",  O_RDONLY | O_DIRECTORY);   /* RAW */
            int par = open(R "/mnt/snaps", O_RDONLY | O_DIRECTORY);
            int r = (src < 0 || par < 0) ? -1 : snap(par, src, "live1");
            say("[B3] snapshot the raw upper of a LIVE overlay: %s (errno=%d %s)\n",
                r == 0 ? "OK" : "FAILED", r == 0 ? 0 : errno, r == 0 ? "" : strerror(errno));
            _exit(r == 0 ? 0 : 1);
        }
        waitpid(p, &st, 0);
        struct stat s;
        say("[B3] did the overlay write land in the snapshot? %s\n",
            stat(R "/mnt/snaps/live1/live", &s) == 0 ? "YES" : "no");
    }

    /* ---- D1: can the house DELETE its own snapshot ---- */
    int st;
    pid_t d = fork();
    if (d == 0) {
        if (setgid(1001) || setuid(1001)) _exit(1);
        enter_userns(1001);
        int par = open(R "/mnt/snaps", O_RDONLY | O_DIRECTORY);
        int src = open(R "/mnt/room",  O_RDONLY | O_DIRECTORY);
        snap(par, src, "doomed");
        int r = rmsubvol(par, "doomed");
        say("[D1] house deletes its own snapshot: %s (errno=%d %s)\n",
            r == 0 ? "OK -- it can clean up after itself"
                   : "FAILED -- snapshots accumulate with no way out",
            r == 0 ? 0 : errno, r == 0 ? "" : strerror(errno));
        _exit(0);
    }
    waitpid(d, &st, 0);

    /* ---- D2: the launch-pad namespace ---- */
    say("[D2] building a launch-pad: only the rooms fs, nothing else\n");
    pid_t l = fork();
    if (l == 0) {
        if (unshare(CLONE_NEWNS) < 0) { say("[D2] unshare %d\n", errno); _exit(1); }
        mount("none", "/", NULL, MS_REC | MS_PRIVATE, NULL);
        mkdir("/nwpad", 0755);
        if (mount("tmpfs", "/nwpad", "tmpfs", 0, "size=8M") < 0) {
            say("[D2] pad tmpfs FAILED %d\n", errno); _exit(1); }
        mkdir("/nwpad/rooms", 0755); mkdir("/nwpad/old", 0755);
        if (mount(R "/mnt", "/nwpad/rooms", NULL, MS_BIND | MS_REC, NULL) < 0) {
            say("[D2] bind rooms FAILED %d\n", errno); _exit(1); }
        if (mount("/nwpad", "/nwpad", NULL, MS_BIND, NULL) < 0) _exit(1);
        if (chdir("/nwpad") < 0) _exit(1);
        if (syscall(SYS_pivot_root, ".", "old") < 0) {
            say("[D2] pad pivot FAILED %d (%s)\n", errno, strerror(errno)); _exit(1); }
        (void)!chdir("/"); umount2("/old", MNT_DETACH); rmdir("/old");
        say("[D2] launch-pad built and pivoted into\n");

        struct stat s;
        say("[D2] from the pad -- /nw:%s  /efi:%s  /nwb2:%s  /rooms:%s\n",
            stat("/nw", &s) == 0 ? "VISIBLE" : "gone",
            stat("/efi", &s) == 0 ? "VISIBLE" : "gone",
            stat("/nwb2", &s) == 0 ? "VISIBLE" : "gone",
            stat("/rooms", &s) == 0 ? "present (intended)" : "MISSING");
        DIR *dd = opendir("/");
        if (dd) { int n = 0; char b[128] = {0}; struct dirent *e;
            while ((e = readdir(dd))) { if (e->d_name[0] == '.') continue; n++;
                if (strlen(b) + strlen(e->d_name) + 2 < sizeof b)
                { strcat(b, e->d_name); strcat(b, " "); } }
            closedir(dd);
            say("[D2] entries at the pad root: %d [%s]\n", n, b); }

        /* a visit born HERE */
        pid_t v = fork();
        if (v == 0) {
            enter_userns(1001);
            struct stat s2;
            say("[D2] a visit born in the pad -- /nw:%s /efi:%s\n",
                stat("/nw", &s2) == 0 ? "VISIBLE -- window still open" : "gone",
                stat("/efi", &s2) == 0 ? "VISIBLE -- window still open" : "gone");
            say("[D2] VERDICT: the pre-pivot window is %s for a visit born in the pad\n",
                (stat("/nw", &s2) != 0) ? "CLOSED" : "still open");
            _exit(0);
        }
        int vs; waitpid(v, &vs, 0);
        _exit(0);
    }
    waitpid(l, &st, 0);
    say("[b2] done\n");
    return 0;
}
