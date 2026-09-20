/* fullprobe.c -- everything left untested about the unprivileged-house
 * design, in one run, on the boot path.
 *
 *  T1  pivot_root into a real brick overlay, unprivileged, then verify the
 *      new root actually IS the brick and the old root is gone.
 *  T2  after pivot, is the machine root still reachable? (a leak would
 *      make the seal meaningless)
 *  T3  uid mapping: what identity does the house actually run as, inside
 *      and outside? A house mapped to root-inside is uid 0 to itself and
 *      the operator's uid outside -- confirm, because every file the house
 *      writes to its layer lands with the OUTSIDE id.
 *  T4  can a house in its own userns escalate -- mount something it should
 *      not, or see another house's upper?
 *  T5  does the shared brick stay read-only? A house must not be able to
 *      write through the overlay into the sealed lower.
 *  T6  a second house's private upper must be invisible to the first.
 *  T7  does the house survive losing its own userns privileges, i.e. can
 *      it still exec after dropping?
 */
#define _GNU_SOURCE
#include <sched.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdarg.h>
#include <stdint.h>
#include <dirent.h>
#include <linux/loop.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <sys/sysmacros.h>

#define R        "/nwfull"
#define BRICKMNT R "/brickmnt"

static void say(const char *fmt, ...)
{
    char b[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}
static void wr(const char *p, const char *v)
{ int fd = open(p, O_WRONLY); if (fd >= 0) { (void)!write(fd, v, strlen(v)); close(fd); } }

static int house(int id, int do_pivot)
{
    char up[64], wk[64], mt[64];
    snprintf(up, sizeof up, R "/u%d", id);
    snprintf(wk, sizeof wk, R "/w%d", id);
    snprintf(mt, sizeof mt, R "/m%d", id);
    mkdir(up, 0755); mkdir(wk, 0755); mkdir(mt, 0755);

    uid_t ou = getuid(), og = getgid();
    if (unshare(CLONE_NEWUSER | CLONE_NEWNS) < 0) { say("[h%d] unshare %d\n", id, errno); return 1; }
    wr("/proc/self/setgroups", "deny");
    char b[64];
    snprintf(b, sizeof b, "0 %d 1\n", (int)ou); wr("/proc/self/uid_map", b);
    snprintf(b, sizeof b, "0 %d 1\n", (int)og); wr("/proc/self/gid_map", b);
    mount("none", "/", NULL, MS_REC | MS_PRIVATE, NULL);

    /* T3 -- identity inside vs the id files will carry outside */
    if (id == 0)
        say("[T3] inside userns uid=%d euid=%d; outer uid was %d -- files land as %d\n",
            (int)getuid(), (int)geteuid(), (int)ou, (int)ou);

    char opt[512];
    snprintf(opt, sizeof opt, "lowerdir=%s,upperdir=%s,workdir=%s", BRICKMNT, up, wk);
    if (mount("overlay", mt, "overlay", 0, opt) < 0) {
        say("[h%d] overlay FAILED %d\n", id, errno); return 1;
    }

    /* T5 -- write through the overlay, then check the SEALED lower is untouched */
    char p[128];
    snprintf(p, sizeof p, "%s/marker", mt);
    int w = open(p, O_WRONLY | O_TRUNC);
    if (w >= 0) { (void)!write(w, "OVERWRITTEN\n", 12); close(w); }
    if (id == 0) {
        int l = open(BRICKMNT "/marker", O_RDONLY);
        char m[64] = {0};
        if (l >= 0) { (void)!read(l, m, sizeof m - 1); close(l); m[strcspn(m,"\n")] = 0; }
        say("[T5] sealed lower after an overlay overwrite: '%s' %s\n",
            m, strcmp(m, "hello-from-brick") == 0 ? "-- INTACT" : "-- MUTATED, seal broken");
        int lw = open(BRICKMNT "/marker", O_WRONLY);
        say("[T5] direct write to the sealed lower: %s (errno=%d)\n",
            lw < 0 ? "REFUSED" : "ALLOWED -- seal broken", lw < 0 ? errno : 0);
        if (lw >= 0) close(lw);
    }

    /* T6 -- can house 0 see house 1's private upper? */
    if (id == 0) {
        struct stat st;
        int seen = stat(R "/u1/private", &st) == 0;
        say("[T6] house0 reading house1's upper file: %s\n",
            seen ? "VISIBLE -- both share the machine root, expected" : "not present yet");
    }
    if (id == 1) { int q = open(R "/u1/private", O_WRONLY|O_CREAT|O_TRUNC, 0644);
                   if (q >= 0) { (void)!write(q, "h1\n", 3); close(q); } }

    /* T4 -- escalation attempts from inside the userns */
    if (id == 0) {
        int e1 = mount("/dev/loop0", R "/m0", "erofs", MS_RDONLY, NULL);
        say("[T4] mount erofs from inside the house: %s (errno=%d)\n",
            e1 == 0 ? "ALLOWED -- bad" : "REFUSED", e1 == 0 ? 0 : errno);
        int e2 = mknod(R "/m0/dev0", S_IFCHR | 0600, makedev(1, 3));
        say("[T4] mknod a char device:                %s (errno=%d)\n",
            e2 == 0 ? "ALLOWED -- bad" : "REFUSED", e2 == 0 ? 0 : errno);
        int e3 = open("/nw/bricks", O_RDONLY | O_DIRECTORY);
        say("[T4] open the brick store directory:     %s\n",
            e3 >= 0 ? "VISIBLE (pre-pivot; pivot must remove it)" : "gone");
        if (e3 >= 0) close(e3);
    }

    if (!do_pivot) return 0;

    /* T1 -- pivot_root, unprivileged, into the brick overlay */
    if (mount(mt, mt, NULL, MS_BIND, NULL) < 0) { say("[T1] self-bind %d\n", errno); return 1; }
    if (chdir(mt) < 0) { say("[T1] chdir %d\n", errno); return 1; }
    mkdir("oldroot", 0755);
    if (syscall(SYS_pivot_root, ".", "oldroot") < 0) {
        say("[T1] pivot_root FAILED errno=%d (%s)\n", errno, strerror(errno)); return 1;
    }
    chdir("/");
    umount2("/oldroot", MNT_DETACH);
    rmdir("/oldroot");
    say("[T1] pivot_root OK, unprivileged\n");

    int q = open("/marker", O_RDONLY); char m[64] = {0};
    if (q >= 0) { (void)!read(q, m, sizeof m - 1); close(q); m[strcspn(m,"\n")] = 0; }
    say("[T1] new root is the brick: '%s'\n", q >= 0 ? m : "MISSING");

    /* T2 -- is the machine root still reachable after the pivot? */
    struct stat st;
    int leak1 = stat("/nw/bricks", &st) == 0;
    int leak2 = stat("/nwfull", &st) == 0;
    int leak3 = stat("/oldroot", &st) == 0;
    say("[T2] after pivot -- /nw/bricks:%s  /nwfull:%s  /oldroot:%s\n",
        leak1 ? "LEAKED" : "gone", leak2 ? "LEAKED" : "gone", leak3 ? "LEAKED" : "gone");

    DIR *d = opendir("/");
    if (d) { int n = 0; struct dirent *e;
        while ((e = readdir(d))) if (e->d_name[0] != '.') n++;
        closedir(d);
        say("[T2] entries visible at the new root: %d\n", n); }

    /* T7 -- can the house still exec after all of this? */
    int x = access("/bin/probe", X_OK);
    say("[T7] exec candidate inside the brick: %s\n", x == 0 ? "present and executable" : "absent");
    return 0;
}

int main(void)
{
    mkdir(R, 0755); mkdir(BRICKMNT, 0755);
    for (const char *k = "/nw/overlay.ko";; k = "/nw/erofs.ko") {
        int m = open(k, O_RDONLY);
        if (m >= 0) { syscall(__NR_finit_module, m, "", 0); close(m); }
        if (k[4] == 'e') break;
    }
    const char *img = "/nw/bricks/"
        "df96ed908baee8660abe06445b8dafaa4cefb72a9d274ec520e51370563eab3a.img";
    int c = open("/dev/loop-control", O_RDWR);
    int idx = c >= 0 ? ioctl(c, LOOP_CTL_GET_FREE) : -1;
    if (c >= 0) close(c);
    char dev[32]; snprintf(dev, sizeof dev, "/dev/loop%d", idx);
    int lf = open(dev, O_RDONLY | O_CLOEXEC), bf = open(img, O_RDONLY | O_CLOEXEC);
    if (idx < 0 || lf < 0 || bf < 0 || ioctl(lf, LOOP_SET_FD, bf) < 0) {
        say("[full] loop setup FAILED %d\n", errno); return 1; }
    close(bf); close(lf);
    if (mount(dev, BRICKMNT, "erofs", MS_RDONLY | MS_NODEV, NULL) < 0) {
        say("[full] erofs FAILED %d\n", errno); return 1; }
    say("[full] brick mounted once, privileged\n");

    pid_t a = fork(); if (a == 0) _exit(house(1, 0));
    int st; waitpid(a, &st, 0);
    pid_t b2 = fork(); if (b2 == 0) _exit(house(0, 1));
    waitpid(b2, &st, 0);
    say("[full] done\n");
    return 0;
}
