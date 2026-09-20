/* uidprobe.c -- the last open question about the unprivileged-house design.
 *
 * Every test so far mapped 0 -> 0: the house was host root outside its own
 * userns. That is why the pre-pivot window was so bad -- a house could
 * overwrite another house's layer and forge the boot record, because it
 * WAS root on the shared tree for those few syscalls.
 *
 * The obvious fix is to map each house to a distinct UNPRIVILEGED outer
 * uid. Then during the window it is an ordinary user, and ordinary file
 * permissions apply. But that only helps if the house can still do all the
 * work with an unprivileged outer identity.
 *
 * Runs the whole startup twice as two different houses:
 *
 *   H1  outer uid 1001, and it OWNS its upper/work dirs
 *   H2  outer uid 1002, and it does NOT own H1's dirs
 *
 * and asks, for each:
 *   A  does the userns map at all with a non-zero outer uid
 *   B  can it still overlay / bind / pivot (the whole startup)
 *   C  can it still reach the brick (shared, root-owned, mode 0755)
 *   D  can it WRITE its own layer -- files must land as its outer uid
 *   E  can it write ANOTHER house's layer  <- the window, closed or not
 *   F  can it forge the boot record        <- the window, closed or not
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
#include <sys/prctl.h>
#include <sys/wait.h>

#define R        "/nwuid"
#define BRICKMNT R "/brickmnt"

static void say(const char *fmt, ...)
{
    char b[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}
static void wr(const char *p, const char *v)
{ int fd = open(p, O_WRONLY); if (fd >= 0) { (void)!write(fd, v, strlen(v)); close(fd); } }

static int house(int id, uid_t outer)
{
    char up[64], wk[64], mt[64], mine[96], theirs[96];
    snprintf(up,  sizeof up,  R "/u%d", id);
    snprintf(wk,  sizeof wk,  R "/w%d", id);
    snprintf(mt,  sizeof mt,  R "/m%d", id);
    snprintf(mine,   sizeof mine,   R "/u%d/own", id);
    snprintf(theirs, sizeof theirs, R "/u%d/own", id == 1 ? 2 : 1);

    /* become the unprivileged outer identity BEFORE unsharing */
    /* After setuid() without exec, /proc/self/* stay owned by the OLD uid
     * and become unwritable -- so the uid_map write silently fails and the
     * process lands on the overflow uid 65534. PR_SET_DUMPABLE re-owns
     * them to the current uid. This is the classic trap and the first run
     * fell straight into it. */
    if (setgid(outer) < 0 || setuid(outer) < 0) {
        say("[h%d] setuid(%d) FAILED errno=%d -- cannot drop\n", id, (int)outer, errno);
        return 1;
    }
    if (prctl(PR_SET_DUMPABLE, 1, 0, 0, 0) < 0)
        say("[h%d] PR_SET_DUMPABLE failed errno=%d\n", id, errno);
    say("[h%d] dropped to outer uid %d\n", id, (int)getuid());

    /* A -- map it */
    if (unshare(CLONE_NEWUSER | CLONE_NEWNS) < 0) {
        say("[h%d] A unshare FAILED errno=%d (%s)\n", id, errno, strerror(errno));
        return 1;
    }
    wr("/proc/self/setgroups", "deny");
    char b[64];
    snprintf(b, sizeof b, "0 %d 1\n", (int)outer); wr("/proc/self/uid_map", b);
    snprintf(b, sizeof b, "0 %d 1\n", (int)outer); wr("/proc/self/gid_map", b);
    say("[h%d] A inside uid=%d, outside uid=%d  %s\n", id, (int)getuid(), (int)outer,
        getuid() == 0 ? "-- map took" : "-- MAP FAILED, on the overflow uid");

    mount("none", "/", NULL, MS_REC | MS_PRIVATE, NULL);

    /* E -- the window: another house's layer */
    int e = open(theirs, O_WRONLY | O_APPEND);
    say("[h%d] E write ANOTHER house's layer : %s (errno=%d)%s\n", id,
        e >= 0 ? "SUCCEEDED" : "REFUSED", e < 0 ? errno : 0,
        e >= 0 ? "   <-- window OPEN" : "   <-- window CLOSED");
    if (e >= 0) { (void)!write(e, "clobbered\n", 10); close(e); }

    /* F -- the window: the boot record */
    int f = open(R "/bootrecord", O_WRONLY | O_APPEND);
    say("[h%d] F forge the boot record       : %s (errno=%d)%s\n", id,
        f >= 0 ? "SUCCEEDED" : "REFUSED", f < 0 ? errno : 0,
        f >= 0 ? "   <-- window OPEN" : "   <-- window CLOSED");
    if (f >= 0) { (void)!write(f, "forged\n", 7); close(f); }

    /* C -- the shared brick, root-owned, read-only */
    int cfd = open(BRICKMNT "/marker", O_RDONLY);
    char m[64] = {0};
    if (cfd >= 0) { (void)!read(cfd, m, sizeof m - 1); close(cfd); m[strcspn(m,"\n")] = 0; }
    say("[h%d] C read the shared brick       : %s\n", id, cfd >= 0 ? m : "REFUSED");

    /* B -- the whole startup */
    char opt[512];
    snprintf(opt, sizeof opt, "lowerdir=%s,upperdir=%s,workdir=%s", BRICKMNT, up, wk);
    if (mount("overlay", mt, "overlay", 0, opt) < 0) {
        say("[h%d] B overlay FAILED errno=%d (%s)\n", id, errno, strerror(errno));
        return 1;
    }
    say("[h%d] B overlay ok\n", id);

    /* D -- write its own layer, and check the owner the file lands with */
    char p[128]; snprintf(p, sizeof p, "%s/wrote", mt);
    int d = open(p, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (d < 0) { say("[h%d] D write own layer FAILED errno=%d\n", id, errno); }
    else {
        (void)!write(d, "ok\n", 3); close(d);
        struct stat st; char q[128];
        snprintf(q, sizeof q, "%s/wrote", up);
        if (stat(q, &st) == 0)
            say("[h%d] D wrote own layer; file owned by uid %d outside\n",
                id, (int)st.st_uid);
        else say("[h%d] D wrote, but upper file not found\n", id);
    }

    if (mount(mt, mt, NULL, MS_BIND, NULL) == 0 && chdir(mt) == 0) {
        mkdir("old", 0755);
        if (syscall(SYS_pivot_root, ".", "old") == 0) {
            (void)!chdir("/"); umount2("/old", MNT_DETACH);
            struct stat st;
            say("[h%d] B pivot ok; city gone: %s\n", id,
                stat("/nwuid", &st) == 0 ? "NO -- leaked" : "yes");
        } else say("[h%d] B pivot FAILED errno=%d (%s)\n", id, errno, strerror(errno));
    }
    return 0;
}

int main(void)
{
    mkdir(R, 0755); mkdir(BRICKMNT, 0755);
    /* two houses, each owning its own dirs, mode 0700 */
    for (int i = 1; i <= 2; i++) {
        char up[64], wk[64], mt[64], own[96];
        snprintf(up, sizeof up, R "/u%d", i); snprintf(wk, sizeof wk, R "/w%d", i);
        snprintf(mt, sizeof mt, R "/m%d", i); snprintf(own, sizeof own, R "/u%d/own", i);
        mkdir(up, 0700); mkdir(wk, 0700); mkdir(mt, 0755);
        int f = open(own, O_WRONLY | O_CREAT | O_TRUNC, 0600);
        if (f >= 0) { (void)!write(f, "layer data\n", 11); close(f); }
        chown(up, 1000 + i, 1000 + i); chown(wk, 1000 + i, 1000 + i);
        chown(mt, 1000 + i, 1000 + i); chown(own, 1000 + i, 1000 + i);
    }
    int f = open(R "/bootrecord", O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (f >= 0) { (void)!write(f, "boot 1\n", 7); close(f); }
    say("[uid] layers are mode 0700 owned by 1001 and 1002; record is 0600 root\n");

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
    if (idx >= 0 && lf >= 0 && bf >= 0 && ioctl(lf, LOOP_SET_FD, bf) == 0) {
        close(bf); close(lf);
        mount(dev, BRICKMNT, "erofs", MS_RDONLY | MS_NODEV, NULL);
    }
    say("[uid] brick mounted once by the boss\n\n");

    int st;
    pid_t a = fork(); if (a == 0) _exit(house(1, 1001)); waitpid(a, &st, 0);
    say("\n");
    pid_t b = fork(); if (b == 0) _exit(house(2, 1002)); waitpid(b, &st, 0);

    say("\n[uid] victim layer now: ");
    int v = open(R "/u1/own", O_RDONLY); char vb[128] = {0};
    if (v >= 0) { (void)!read(v, vb, sizeof vb - 1); close(v); }
    for (char *q = vb; *q; q++) if (*q == '\n') *q = '|';
    say("'%s'\n", vb);
    say("[uid] boot record now : ");
    v = open(R "/bootrecord", O_RDONLY); memset(vb, 0, sizeof vb);
    if (v >= 0) { (void)!read(v, vb, sizeof vb - 1); close(v); }
    for (char *q = vb; *q; q++) if (*q == '\n') *q = '|';
    say("'%s'\n", vb);
    return 0;
}
