/* sharedbrick.c -- the bottleneck may be self-inflicted.
 *
 * Today every brick house loop-mounts its own erofs image. That means:
 *   - one loop device per house, and CONFIG_BLK_DEV_LOOP_MIN_COUNT=8, with
 *     the contention already recorded at nwsup.c:118
 *   - a real CAP_SYS_ADMIN erofs mount per house, per restart
 *   - the one filesystem that is NOT FS_USERNS_MOUNT is on the hot path
 *
 * But a brick is READ-ONLY and CONTENT-ADDRESSED. Two houses on the same
 * brick are mounting identical immutable bytes. There is no reason to do
 * it twice, and no reason to do it per start.
 *
 * So: mount each DISTINCT brick exactly once, privileged, at boot. A house
 * then unshares -- inheriting those mounts, because a new mount namespace
 * starts as a copy -- and does only overlay + binds + pivot_root.
 *
 * If all three of those work inside a USER namespace, then after boot no
 * house start needs real privilege at all. That is the question.
 *
 *   1  mount the brick once, privileged            (as PID 1 would)
 *   2  enter a user namespace                      (house start)
 *   3  is the brick still visible in there?
 *   4  overlay on top of it, unprivileged
 *   5  bind-mount, unprivileged
 *   6  pivot_root, unprivileged
 */
#define _GNU_SOURCE
#include <sched.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdarg.h>
#include <linux/loop.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>

#define R "/nwshare"
#define BRICKMNT R "/brickmnt"

static void say(const char *fmt, ...)
{
    char b[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}
static void wr(const char *p, const char *v)
{ int fd = open(p, O_WRONLY); if (fd >= 0) { (void)!write(fd, v, strlen(v)); close(fd); } }

static int loop_attach(const char *img)
{
    int c = open("/dev/loop-control", O_RDWR);
    if (c < 0) { say("[sb] loop-control %d\n", errno); return -1; }
    int idx = ioctl(c, 0x4C82 /* LOOP_CTL_GET_FREE */); close(c);
    if (idx < 0) { say("[sb] get-free %d\n", errno); return -1; }
    char dev[32]; snprintf(dev, sizeof dev, "/dev/loop%d", idx);
    int lf = open(dev, O_RDONLY | O_CLOEXEC);
    int bf = open(img, O_RDONLY | O_CLOEXEC);
    if (lf < 0 || bf < 0) { say("[sb] open loop/img %d\n", errno); return -1; }
    if (ioctl(lf, 0x4C00 /* LOOP_SET_FD */, bf) < 0) { say("[sb] set-fd %d\n", errno); return -1; }
    close(bf); close(lf);
    say("[sb] brick attached to %s\n", dev);
    return idx;
}

int main(void)
{
    mkdir(R, 0755); mkdir(BRICKMNT, 0755);
    mkdir(R "/upper", 0755); mkdir(R "/work", 0755); mkdir(R "/mnt", 0755);
    mkdir(R "/bindsrc", 0755); mkdir(R "/oldroot", 0755);
    int f = open(R "/bindsrc/data", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (f >= 0) { (void)!write(f, "bound\n", 6); close(f); }

    for (const char *k = "/nw/overlay.ko";; k = "/nw/erofs.ko") {
        int m = open(k, O_RDONLY);
        if (m >= 0) { syscall(__NR_finit_module, m, "", 0); close(m); }
        if (k[4] == 'e') break;
    }

    /* ---- 1. PID 1's job: mount the distinct brick ONCE, privileged ---- */
    const char *img = "/nw/bricks/"
        "df96ed908baee8660abe06445b8dafaa4cefb72a9d274ec520e51370563eab3a.img";
    int idx = loop_attach(img);
    if (idx < 0) { say("[sb] VERDICT: could not attach\n"); return 1; }
    char dev[32]; snprintf(dev, sizeof dev, "/dev/loop%d", idx);
    if (mount(dev, BRICKMNT, "erofs", MS_RDONLY | MS_NODEV, NULL) < 0) {
        say("[sb] erofs mount FAILED %d (%s)\n", errno, strerror(errno)); return 1;
    }
    say("[sb] 1 brick mounted once, privileged, at " BRICKMNT "\n");

    /* ---- 2. house start: drop into a user namespace ---- */
    uid_t u = getuid(); gid_t g = getgid();
    if (unshare(CLONE_NEWUSER | CLONE_NEWNS) < 0) {
        say("[sb] unshare FAILED %d\n", errno); return 1;
    }
    wr("/proc/self/setgroups", "deny");
    char b[64];
    snprintf(b, sizeof b, "0 %d 1\n", (int)u); wr("/proc/self/uid_map", b);
    snprintf(b, sizeof b, "0 %d 1\n", (int)g); wr("/proc/self/gid_map", b);
    say("[sb] 2 in a user namespace, no real privilege from here on\n");
    mount("none", "/", NULL, MS_REC | MS_PRIVATE, NULL);

    /* ---- 3. did the brick mount come with us? ---- */
    int r = open(BRICKMNT "/marker", O_RDONLY);
    if (r >= 0) { char m[64] = {0}; (void)!read(r, m, sizeof m - 1); close(r);
        m[strcspn(m, "\n")] = 0;
        say("[sb] 3 brick visible inside the userns: '%s'\n", m); }
    else say("[sb] 3 brick NOT visible: errno=%d\n", errno);

    /* ---- 4. overlay on the shared brick, unprivileged ---- */
    char opt[512];
    snprintf(opt, sizeof opt, "lowerdir=%s,upperdir=%s,workdir=%s",
             BRICKMNT, R "/upper", R "/work");
    if (mount("overlay", R "/mnt", "overlay", 0, opt) < 0) {
        say("[sb] 4 overlay FAILED %d (%s)\n", errno, strerror(errno));
        say("[sb] VERDICT: houses still need real privilege\n"); return 1;
    }
    say("[sb] 4 overlay MOUNTED on the shared brick, unprivileged\n");

    /* ---- 5. a bind, unprivileged ---- */
    mkdir(R "/mnt/bound", 0755);
    if (mount(R "/bindsrc", R "/mnt/bound", NULL, MS_BIND, NULL) < 0)
        say("[sb] 5 bind FAILED %d (%s)\n", errno, strerror(errno));
    else say("[sb] 5 bind MOUNTED, unprivileged\n");

    /* ---- 6. pivot_root, unprivileged ---- */
    if (mount(R "/mnt", R "/mnt", NULL, MS_BIND, NULL) < 0)
        say("[sb] 6 self-bind failed %d\n", errno);
    if (chdir(R "/mnt") == 0 && mkdir("oldroot", 0755) >= -1 &&
        syscall(SYS_pivot_root, ".", "oldroot") == 0) {
        say("[sb] 6 pivot_root OK, unprivileged\n");
        chdir("/");
        umount2("/oldroot", MNT_DETACH);
        int q = open("/marker", O_RDONLY);
        char m[64] = {0};
        if (q >= 0) { (void)!read(q, m, sizeof m - 1); close(q); m[strcspn(m,"\n")] = 0; }
        say("[sb]   new root holds the brick: '%s'\n", q >= 0 ? m : "MISSING");
        say("[sb] VERDICT: after the shared brick mount, a house start needs NO real privilege\n");
    } else {
        say("[sb] 6 pivot_root FAILED %d (%s)\n", errno, strerror(errno));
        say("[sb] VERDICT: pivot is the remaining privileged step\n");
    }
    return 0;
}
