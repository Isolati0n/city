/* windowprobe.c -- two claims from the review, both testable.
 *
 * CLAIM 1 (severity). My T6 measured stat. The review says that with a
 * 0 -> 0 uid map the house is host root on the copied tree for the whole
 * pre-pivot window, so it is not "can stat house 1" but "can WRITE house
 * 1's layer, and the boot record." Test the write, not the stat.
 *
 * CLAIM 2 (the fix). Proposed order:
 *     kit fds in -> unshare(NEWUSER|NEWNS) -> MS_REC|MS_PRIVATE
 *     -> tmpfs stage -> bind the fds into the stage
 *     -> PIVOT INTO THE STAGE -> overlay, binds, Landlock, seccomp, exec
 * After the staging pivot the city root is gone BEFORE the overlay is
 * built, so the window shrinks to the few syscalls before the pivot and
 * Landlock is applied against the small tree rather than the city.
 *
 * Does the staging pivot work unprivileged, and is the city actually gone
 * after it?
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

#define R        "/nwwin"
#define BRICKMNT R "/brickmnt"

static void say(const char *fmt, ...)
{
    char b[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}
static void wr(const char *p, const char *v)
{ int fd = open(p, O_WRONLY); if (fd >= 0) { (void)!write(fd, v, strlen(v)); close(fd); } }

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

/* ---- CLAIM 1: how bad is the window really? ---- */
static int severity(void)
{
    enter_userns();
    say("[sev] inside userns as uid=%d, outer was host root\n", (int)getuid());

    int w = open(R "/u_victim/private", O_WRONLY | O_APPEND);
    if (w >= 0) { ssize_t n = write(w, "CLOBBERED BY ANOTHER HOUSE\n", 27); close(w);
        say("[sev] write another house's layer file : %s\n",
            n > 0 ? "SUCCEEDED -- the review is right" : "write failed"); }
    else say("[sev] write another house's layer file : refused errno=%d\n", errno);

    int t = open(R "/u_victim/planted", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    say("[sev] create a NEW file in their layer   : %s (errno=%d)\n",
        t >= 0 ? "SUCCEEDED" : "refused", t < 0 ? errno : 0);
    if (t >= 0) close(t);

    int b = open(R "/bootrecord", O_WRONLY | O_APPEND);
    say("[sev] append to the boot record          : %s (errno=%d)\n",
        b >= 0 ? "SUCCEEDED" : "refused", b < 0 ? errno : 0);
    if (b >= 0) { (void)!write(b, "forged\n", 7); close(b); }

    int e = open("/efi/slots/current", O_RDONLY);
    say("[sev] read the live-slot pointer         : %s\n", e >= 0 ? "VISIBLE" : "gone");
    if (e >= 0) close(e);
    return 0;
}

/* ---- CLAIM 2: does the staging pivot close it early? ---- */
static int staged(void)
{
    /* kit fds, resolved BEFORE the unshare, as pack_kit would hand them */
    int fd_brick = open(BRICKMNT, O_PATH | O_CLOEXEC);
    int fd_upper = open(R "/u_stage", O_PATH | O_CLOEXEC);
    int fd_work  = open(R "/w_stage", O_PATH | O_CLOEXEC);
    if (fd_brick < 0 || fd_upper < 0 || fd_work < 0) { say("[stg] kit fds failed %d\n", errno); return 1; }
    say("[stg] kit: brick=%d upper=%d work=%d (O_PATH, before unshare)\n",
        fd_brick, fd_upper, fd_work);

    enter_userns();

    /* tmpfs stage -- the mountpoint must exist; the root image has no /mnt */
    mkdir("/nwstage", 0755);
    if (mount("tmpfs", "/nwstage", "tmpfs", 0, "size=4M") < 0) {
        say("[stg] tmpfs stage FAILED %d (%s)\n", errno, strerror(errno)); return 1; }
    say("[stg] tmpfs stage mounted at /nwstage\n");

    mkdir("/nwstage/brick", 0755); mkdir("/nwstage/upper", 0755);
    mkdir("/nwstage/work", 0755);  mkdir("/nwstage/root", 0755); mkdir("/nwstage/old", 0755);

    /* NOTE: binding via /proc/self/fd/N returned EINVAL for all three.
     * Plain-path binds inside a userns DO work (measured earlier), so the
     * fd-as-bind-source is the part that fails, not binds themselves. That
     * is an fd-passing detail; the ORDER is what is under test here, so
     * bind by path and record the fd finding separately. */
    int e1 = mount(BRICKMNT,     "/nwstage/brick", NULL, MS_BIND | MS_REC, NULL);
    int e2 = mount(R "/u_stage", "/nwstage/upper", NULL, MS_BIND, NULL);
    int e3 = mount(R "/w_stage", "/nwstage/work",  NULL, MS_BIND, NULL);
    say("[stg] bind kit fds into the stage: brick=%s upper=%s work=%s\n",
        e1 ? "FAIL" : "ok", e2 ? "FAIL" : "ok", e3 ? "FAIL" : "ok");
    if (e1 || e2 || e3) { say("[stg] errno=%d (%s)\n", errno, strerror(errno)); return 1; }

    /* PIVOT INTO THE STAGE -- city root gone from here on */
    if (mount("/nwstage", "/nwstage", NULL, MS_BIND, NULL) < 0) { say("[stg] self-bind %d\n", errno); return 1; }
    if (chdir("/nwstage") < 0) { say("[stg] chdir %d\n", errno); return 1; }
    if (syscall(SYS_pivot_root, ".", "old") < 0) {
        say("[stg] STAGING PIVOT FAILED errno=%d (%s)\n", errno, strerror(errno)); return 1; }
    (void)!chdir("/");
    umount2("/old", MNT_DETACH); rmdir("/old");
    say("[stg] STAGING PIVOT OK -- unprivileged\n");

    struct stat st;
    say("[stg] city now: /nwwin:%s  /nw:%s  /efi:%s\n",
        stat("/nwwin", &st) == 0 ? "LEAKED" : "gone",
        stat("/nw", &st)    == 0 ? "LEAKED" : "gone",
        stat("/efi", &st)   == 0 ? "LEAKED" : "gone");
    DIR *d = opendir("/");
    if (d) { int n = 0; struct dirent *x;
        while ((x = readdir(d))) if (x->d_name[0] != '.') n++;
        closedir(d); say("[stg] entries at the new root: %d\n", n); }

    /* now build the real overlay, city already gone */
    if (mount("overlay", "/root", "overlay", 0,
              "lowerdir=/brick,upperdir=/upper,workdir=/work") < 0) {
        say("[stg] overlay after staging pivot FAILED %d (%s)\n", errno, strerror(errno));
        return 1; }
    say("[stg] overlay built AFTER the city was gone -- ok\n");
    int r = open("/root/marker", O_RDONLY); char m[64] = {0};
    if (r >= 0) { (void)!read(r, m, sizeof m - 1); close(r); m[strcspn(m,"\n")] = 0; }
    say("[stg] brick readable through it: '%s'\n", r >= 0 ? m : "MISSING");
    say("[stg] VERDICT: window is now the syscalls between unshare and the staging pivot\n");
    return 0;
}

int main(void)
{
    mkdir(R, 0755); mkdir(BRICKMNT, 0755);
    mkdir(R "/u_victim", 0755); mkdir(R "/u_stage", 0755); mkdir(R "/w_stage", 0755);
    int f = open(R "/u_victim/private", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (f >= 0) { (void)!write(f, "house1 data\n", 12); close(f); }
    f = open(R "/bootrecord", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (f >= 0) { (void)!write(f, "boot 1\n", 7); close(f); }

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

    pid_t a = fork(); if (a == 0) _exit(severity());
    int st; waitpid(a, &st, 0);
    say("[sev] victim file now: ");
    int v = open(R "/u_victim/private", O_RDONLY);
    char vb[128] = {0};
    if (v >= 0) { (void)!read(v, vb, sizeof vb - 1); close(v); }
    for (char *q = vb; *q; q++) if (*q == '\n') *q = '|';
    say("'%s'\n", vb);

    pid_t b = fork(); if (b == 0) _exit(staged());
    waitpid(b, &st, 0);
    say("[win] done\n");
    return 0;
}
