/* usernsprobe.c -- can PID 1 stop being root?
 *
 * Everything PID 1 would need privilege for in the setns-restart design is
 * a mount operation: unshare(CLONE_NEWNS), the erofs brick, the overlay,
 * the loop attach, and setns back in. If all of those can happen inside a
 * USER namespace, they need no real capabilities -- a process in its own
 * userns holds CAP_SYS_ADMIN *within that namespace*, and only filesystems
 * marked FS_USERNS_MOUNT will honour it.
 *
 * overlayfs got FS_USERNS_MOUNT in 5.11. erofs, reportedly, never did.
 * That claim decides whether root can leave PID 1 or only shrink, so it is
 * measured here rather than cited.
 *
 * Runs as a house with lids=none, so it starts privileged; it drops into a
 * fresh userns first and everything after that is unprivileged-equivalent.
 */
#define _GNU_SOURCE
#include <sched.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdarg.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>

#define R "/nwuns"

static void say(const char *fmt, ...)
{
    char b[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}

static void wr(const char *p, const char *v)
{
    int fd = open(p, O_WRONLY);
    if (fd < 0) { say("[uns] open %s failed %d\n", p, errno); return; }
    if (write(fd, v, strlen(v)) < 0) say("[uns] write %s failed %d\n", p, errno);
    close(fd);
}

int main(void)
{
    mkdir(R, 0755); mkdir(R "/lower", 0755); mkdir(R "/upper", 0755);
    mkdir(R "/work", 0755); mkdir(R "/mnt", 0755); mkdir(R "/brick", 0755);

    int m = open("/nw/overlay.ko", O_RDONLY);
    if (m >= 0) { syscall(__NR_finit_module, m, "", 0); close(m); }
    m = open("/nw/erofs.ko", O_RDONLY);
    if (m >= 0) { syscall(__NR_finit_module, m, "", 0); close(m); }

    uid_t u = getuid(); gid_t g = getgid();
    say("[uns] starting as uid=%d gid=%d\n", (int)u, (int)g);

    if (unshare(CLONE_NEWUSER | CLONE_NEWNS) < 0) {
        say("[uns] unshare(NEWUSER|NEWNS) FAILED errno=%d (%s)\n", errno, strerror(errno));
        say("[uns] VERDICT: cannot even enter a userns; root cannot leave\n");
        return 1;
    }
    /* map ourselves so we are root *inside* and nobody outside */
    wr("/proc/self/setgroups", "deny");
    char b[64];
    snprintf(b, sizeof b, "0 %d 1\n", (int)u); wr("/proc/self/uid_map", b);
    snprintf(b, sizeof b, "0 %d 1\n", (int)g); wr("/proc/self/gid_map", b);
    say("[uns] in a user namespace: uid=%d (inside), euid=%d\n",
        (int)getuid(), (int)geteuid());

    mount("none", "/", NULL, MS_REC | MS_PRIVATE, NULL);

    /* 1 -- tmpfs, the easy case, FS_USERNS_MOUNT since forever */
    if (mount("tmpfs", R "/mnt", "tmpfs", 0, "size=1M") == 0) {
        say("[uns] tmpfs   : MOUNTED\n");
        umount(R "/mnt");
    } else say("[uns] tmpfs   : failed errno=%d (%s)\n", errno, strerror(errno));

    /* 2 -- overlay, FS_USERNS_MOUNT since 5.11 */
    int fd = open(R "/lower/f", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) { (void)!write(fd, "x\n", 2); close(fd); }
    if (mount("overlay", R "/mnt", "overlay", 0,
              "lowerdir=" R "/lower,upperdir=" R "/upper,workdir=" R "/work") == 0) {
        say("[uns] overlay : MOUNTED\n");
        umount(R "/mnt");
    } else say("[uns] overlay : FAILED errno=%d (%s)\n", errno, strerror(errno));

    /* 3 -- erofs from a loop device: the one that decides it */
    if (mount("/dev/loop0", R "/brick", "erofs", MS_RDONLY, NULL) == 0) {
        say("[uns] erofs   : MOUNTED  <-- root could leave entirely\n");
        umount(R "/brick");
    } else {
        say("[uns] erofs   : FAILED errno=%d (%s)\n", errno, strerror(errno));
        if (errno == EPERM)
            say("[uns]           EPERM = not FS_USERNS_MOUNT. This is the blocker.\n");
    }

    /* 4 -- can an unprivileged userns attach a loop device at all? */
    int lc = open("/dev/loop-control", O_RDWR);
    say("[uns] loop-control open: %s errno=%d\n", lc >= 0 ? "ok" : "FAILED", lc < 0 ? errno : 0);
    if (lc >= 0) close(lc);

    say("[uns] done\n");
    return 0;
}
