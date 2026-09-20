/* treeprobe.c -- the two checks the review asked for, in order.
 *
 * CHECK A  after the bind, BEFORE overlay: does the bind target contain the
 *          marker, and is it a mount root?
 *            marker present -> case 2: the bind worked, and overlay's
 *                              lowerdir walked onto a covering dir instead
 *                              of the mount root
 *            marker absent  -> case 1: the bind never carried the erofs
 *
 * CHECK B  open_tree(OPEN_TREE_CLONE) on the INHERITED erofs mount, from
 *          inside the house's user namespace, then move_mount it into the
 *          stage. If EPERM, the shared brick cannot be pulled in after the
 *          unshare and must be bound before it -- which puts it back in
 *          the copied table and costs the whole point.
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
#include <linux/loop.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>

#ifndef OPEN_TREE_CLONE
#define OPEN_TREE_CLONE      1
#endif
#ifndef AT_RECURSIVE
#define AT_RECURSIVE         0x8000
#endif
#ifndef MOVE_MOUNT_F_EMPTY_PATH
#define MOVE_MOUNT_F_EMPTY_PATH 0x00000004
#endif

#define R        "/nwtree"
#define BRICKMNT R "/brickmnt"

static void say(const char *fmt, ...)
{
    char b[640]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}
static void wr(const char *p, const char *v)
{ int fd = open(p, O_WRONLY); if (fd >= 0) { (void)!write(fd, v, strlen(v)); close(fd); } }

static int open_tree_(int d, const char *p, unsigned f)
{ return (int)syscall(__NR_open_tree, d, p, f); }
static int move_mount_(int ff, const char *fp, int tf, const char *tp, unsigned f)
{ return (int)syscall(__NR_move_mount, ff, fp, tf, tp, f); }

/* is <path> a mount ROOT in this namespace, per mountinfo? */
static void mountinfo_for(const char *path)
{
    FILE *f = fopen("/proc/self/mountinfo", "r");
    if (!f) { say("      (no mountinfo)\n"); return; }
    char l[1024]; int hit = 0;
    while (fgets(l, sizeof l, f)) {
        char *sp = strstr(l, " - ");
        char mp[256] = {0}, fstype[64] = {0};
        int id, par; char rootfld[128];
        if (sscanf(l, "%d %d %*s %127s %255s", &id, &par, rootfld, mp) != 4) continue;
        if (strcmp(mp, path) != 0) continue;
        if (sp) sscanf(sp + 3, "%63s", fstype);
        say("      mountinfo: %s is a MOUNT ROOT, fstype=%s root=%s\n", mp, fstype, rootfld);
        hit = 1;
    }
    fclose(f);
    if (!hit) say("      mountinfo: %s is NOT a mount root (nothing mounted there)\n", path);
}

static void listdir(const char *p)
{
    DIR *d = opendir(p);
    if (!d) { say("      ls %s: FAILED errno=%d\n", p, errno); return; }
    char buf[256] = {0}; struct dirent *e; int n = 0;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        n++;
        if (strlen(buf) + strlen(e->d_name) + 2 < sizeof buf)
        { strcat(buf, e->d_name); strcat(buf, " "); }
    }
    closedir(d);
    say("      ls %s: %d entries [%s]\n", p, n, buf);
}

static void enter_userns(void)
{
    uid_t u = getuid(); gid_t g = getgid();
    if (unshare(CLONE_NEWUSER | CLONE_NEWNS) < 0) { say("  unshare %d\n", errno); _exit(1); }
    wr("/proc/self/setgroups", "deny");
    char b[64];
    snprintf(b, sizeof b, "0 %d 1\n", (int)u); wr("/proc/self/uid_map", b);
    snprintf(b, sizeof b, "0 %d 1\n", (int)g); wr("/proc/self/gid_map", b);
    mount("none", "/", NULL, MS_REC | MS_PRIVATE, NULL);
}

/* ---------------- CHECK A ---------------- */
static int check_a(void)
{
    enter_userns();
    mkdir("/nwstage", 0755);
    if (mount("tmpfs", "/nwstage", "tmpfs", 0, "size=4M") < 0) {
        say("[A] tmpfs FAILED %d\n", errno); return 1; }
    mkdir("/nwstage/brick", 0755);

    say("[A] source before binding:\n");
    listdir(BRICKMNT);
    mountinfo_for(BRICKMNT);

    int e = mount(BRICKMNT, "/nwstage/brick", NULL, MS_BIND | MS_REC, NULL);
    say("[A] bind %s -> /nwstage/brick : %s (errno=%d)\n",
        BRICKMNT, e ? "FAILED" : "ok", e ? errno : 0);
    if (e) return 1;

    say("[A] target after binding:\n");
    listdir("/nwstage/brick");
    mountinfo_for("/nwstage/brick");

    struct stat st;
    int marker = stat("/nwstage/brick/marker", &st) == 0;
    say("[A] VERDICT: marker at the bind target is %s -> %s\n",
        marker ? "PRESENT" : "ABSENT",
        marker ? "case 2, the bind carried the erofs; lowerdir was the problem"
               : "case 1, the bind never had the erofs");
    if (marker) {
        int o = mount("overlay", "/nwstage", "overlay", 0,
                      "lowerdir=/nwstage/brick,upperdir=/nwstage,workdir=/nwstage");
        say("[A] (sanity: overlay directly on the bind target %s)\n",
            o ? "refused, as expected for a bad upper/work" : "mounted");
    }
    return 0;
}

/* ---------------- CHECK B ---------------- */
static int check_b(void)
{
    enter_userns();
    mkdir("/nwstage", 0755);
    if (mount("tmpfs", "/nwstage", "tmpfs", 0, "size=4M") < 0) {
        say("[B] tmpfs FAILED %d\n", errno); return 1; }
    mkdir("/nwstage/brick", 0755);

    int t = open_tree_(AT_FDCWD, BRICKMNT, OPEN_TREE_CLONE | AT_RECURSIVE);
    if (t < 0) {
        say("[B] open_tree(OPEN_TREE_CLONE) on the inherited erofs: FAILED errno=%d (%s)\n",
            errno, strerror(errno));
        say("[B] VERDICT: the brick cannot be pulled in after unshare;\n");
        say("[B]          it must be bound before, back into the copied table\n");
        return 1;
    }
    say("[B] open_tree(OPEN_TREE_CLONE) on the inherited erofs: ok, fd=%d\n", t);

    int m = move_mount_(t, "", AT_FDCWD, "/nwstage/brick", MOVE_MOUNT_F_EMPTY_PATH);
    say("[B] move_mount into the stage: %s (errno=%d)\n", m ? "FAILED" : "ok", m ? errno : 0);
    close(t);
    if (m) return 1;

    listdir("/nwstage/brick");
    mountinfo_for("/nwstage/brick");
    struct stat st;
    say("[B] VERDICT: marker after move_mount is %s -- %s\n",
        stat("/nwstage/brick/marker", &st) == 0 ? "PRESENT" : "ABSENT",
        stat("/nwstage/brick/marker", &st) == 0
          ? "the house can pull PID 1's mount in after unshare"
          : "moved but empty; something else is wrong");

    /* and can overlay use it as a proper lower? */
    mkdir("/nwstage/up", 0755); mkdir("/nwstage/wk", 0755); mkdir("/nwstage/mnt", 0755);
    int o = mount("overlay", "/nwstage/mnt", "overlay", 0,
                  "lowerdir=/nwstage/brick,upperdir=/nwstage/up,workdir=/nwstage/wk");
    say("[B] overlay on the moved brick: %s (errno=%d)\n", o ? "FAILED" : "ok", o ? errno : 0);
    if (!o) {
        int r = open("/nwstage/mnt/marker", O_RDONLY); char b[64] = {0};
        if (r >= 0) { (void)!read(r, b, sizeof b - 1); close(r); b[strcspn(b,"\n")] = 0; }
        say("[B] brick readable through the overlay: '%s'\n", r >= 0 ? b : "MISSING");
    }
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
        say("[tree] loop FAILED %d\n", errno); return 1; }
    close(bf); close(lf);
    if (mount(dev, BRICKMNT, "erofs", MS_RDONLY | MS_NODEV, NULL) < 0) {
        say("[tree] erofs FAILED %d\n", errno); return 1; }
    say("[tree] PID 1 mounted the brick once at %s\n", BRICKMNT);

    int st;
    pid_t a = fork(); if (a == 0) _exit(check_a()); waitpid(a, &st, 0);
    pid_t b = fork(); if (b == 0) _exit(check_b()); waitpid(b, &st, 0);
    say("[tree] done\n");
    return 0;
}
