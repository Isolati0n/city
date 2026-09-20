/* concprobe.c -- the three open questions from the shared-brick result.
 *
 *  Q1 CONCURRENCY. Several houses start at once against ONE shared erofs
 *     mount, each building its own overlay on it. The old design gave each
 *     house its own loop device and hit `FAIL loop configure errno=16`
 *     under contention (nwsup.c:118). Sharing should remove that, but N
 *     overlays over one lower is untested.
 *
 *  Q2 LANDLOCK IN A USERNS. landlock_restrict_self needs CAP_SYS_ADMIN in
 *     the caller's userns OR no_new_privs. Inside a fresh userns we have
 *     the former by construction -- but does the ruleset still BITE, or
 *     does a userns weaken it?
 *
 *  Q3 SECCOMP IN A USERNS. Same question for the filter.
 *
 * A "no" on Q2 or Q3 kills the unprivileged-house design outright: the
 * whole point is the house confining itself.
 */
#define _GNU_SOURCE
#include <sched.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdarg.h>
#include <signal.h>
#include <stdint.h>
#include <linux/loop.h>
#include <linux/landlock.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <linux/audit.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>

#define R        "/nwconc"
#define BRICKMNT R "/brickmnt"
#define NHOUSE   6

static void say(const char *fmt, ...)
{
    char b[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}
static void wr(const char *p, const char *v)
{ int fd = open(p, O_WRONLY); if (fd >= 0) { (void)!write(fd, v, strlen(v)); close(fd); } }

static int ll_create(struct landlock_ruleset_attr *a, size_t s, uint32_t f)
{ return (int)syscall(__NR_landlock_create_ruleset, a, s, f); }
static int ll_add(int fd, enum landlock_rule_type t, const void *a, uint32_t f)
{ return (int)syscall(__NR_landlock_add_rule, fd, t, a, f); }
static int ll_self(int fd, uint32_t f)
{ return (int)syscall(__NR_landlock_restrict_self, fd, f); }

/* one house: userns, overlay on the SHARED brick, pivot, landlock, seccomp */
static int house(int id)
{
    char up[64], wk[64], mt[64];
    snprintf(up, sizeof up, R "/u%d", id);
    snprintf(wk, sizeof wk, R "/w%d", id);
    snprintf(mt, sizeof mt, R "/m%d", id);
    mkdir(up, 0755); mkdir(wk, 0755); mkdir(mt, 0755);

    uid_t u = getuid(); gid_t g = getgid();
    if (unshare(CLONE_NEWUSER | CLONE_NEWNS) < 0) {
        say("[c%d] unshare FAILED %d\n", id, errno); return 1;
    }
    wr("/proc/self/setgroups", "deny");
    char b[64];
    snprintf(b, sizeof b, "0 %d 1\n", (int)u); wr("/proc/self/uid_map", b);
    snprintf(b, sizeof b, "0 %d 1\n", (int)g); wr("/proc/self/gid_map", b);
    mount("none", "/", NULL, MS_REC | MS_PRIVATE, NULL);

    char opt[512];
    snprintf(opt, sizeof opt, "lowerdir=%s,upperdir=%s,workdir=%s", BRICKMNT, up, wk);
    if (mount("overlay", mt, "overlay", 0, opt) < 0) {
        say("[c%d] overlay FAILED errno=%d (%s)\n", id, errno, strerror(errno));
        return 1;
    }

    /* prove the shared lower is readable and the private upper is writable */
    char p[96]; snprintf(p, sizeof p, "%s/marker", mt);
    int r = open(p, O_RDONLY); char m[64] = {0};
    if (r >= 0) { (void)!read(r, m, sizeof m - 1); close(r); m[strcspn(m,"\n")] = 0; }
    snprintf(p, sizeof p, "%s/mine", mt);
    int w = open(p, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    int wok = (w >= 0); if (w >= 0) { (void)!write(w, "x", 1); close(w); }
    say("[c%d] overlay ok  lower='%s'  upper-write=%s\n", id, r >= 0 ? m : "MISSING",
        wok ? "ok" : "FAILED");

    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0) say("[c%d] nnp failed\n", id);

    /* Q2 -- landlock: allow reads under the overlay, nothing else */
    struct landlock_ruleset_attr ra = {
        .handled_access_fs = LANDLOCK_ACCESS_FS_READ_FILE |
                             LANDLOCK_ACCESS_FS_WRITE_FILE };
    int lr = ll_create(&ra, sizeof ra, 0);
    if (lr < 0) { say("[c%d] LANDLOCK create FAILED %d\n", id, errno); }
    else {
        int dir = open(mt, O_PATH | O_CLOEXEC);
        struct landlock_path_beneath_attr pb = {
            .allowed_access = LANDLOCK_ACCESS_FS_READ_FILE, .parent_fd = dir };
        ll_add(lr, LANDLOCK_RULE_PATH_BENEATH, &pb, 0);
        close(dir);
        if (ll_self(lr, 0) < 0) say("[c%d] LANDLOCK restrict_self FAILED %d\n", id, errno);
        else {
            /* does it bite? a write under the overlay must now be refused */
            snprintf(p, sizeof p, "%s/after-landlock", mt);
            int t = open(p, O_WRONLY | O_CREAT, 0644);
            say("[c%d] landlock: write after restrict = %s (errno=%d)\n",
                id, t < 0 ? "REFUSED" : "ALLOWED -- NOT BITING", t < 0 ? errno : 0);
            if (t >= 0) close(t);
        }
        close(lr);
    }

    /* Q3 -- seccomp: kill on socket(), then call it */
    struct sock_filter f[] = {
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, 4),                       /* arch */
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, 0),                       /* nr */
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_socket, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    struct sock_fprog prog = { .len = sizeof f / sizeof f[0], .filter = f };
    if (syscall(__NR_seccomp, SECCOMP_SET_MODE_FILTER, 0, &prog) < 0)
        say("[c%d] SECCOMP install FAILED %d\n", id, errno);
    else {
        int s = socket(AF_INET, SOCK_STREAM, 0);
        say("[c%d] seccomp: socket() = %s (errno=%d)\n",
            id, s < 0 ? "REFUSED" : "ALLOWED -- NOT BITING", s < 0 ? errno : 0);
        if (s >= 0) close(s);
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

    /* PID 1's job: attach and mount the ONE brick, privileged */
    const char *img = "/nw/bricks/"
        "df96ed908baee8660abe06445b8dafaa4cefb72a9d274ec520e51370563eab3a.img";
    int c = open("/dev/loop-control", O_RDWR);
    int idx = c >= 0 ? ioctl(c, LOOP_CTL_GET_FREE) : -1;
    if (c >= 0) close(c);
    char dev[32]; snprintf(dev, sizeof dev, "/dev/loop%d", idx);
    int lf = open(dev, O_RDONLY | O_CLOEXEC), bf = open(img, O_RDONLY | O_CLOEXEC);
    if (idx < 0 || lf < 0 || bf < 0 || ioctl(lf, LOOP_SET_FD, bf) < 0) {
        say("[conc] loop setup FAILED %d\n", errno); return 1;
    }
    close(bf); close(lf);
    if (mount(dev, BRICKMNT, "erofs", MS_RDONLY | MS_NODEV, NULL) < 0) {
        say("[conc] erofs FAILED %d\n", errno); return 1;
    }
    say("[conc] ONE brick on %s, shared by %d houses\n", dev, NHOUSE);

    pid_t kids[NHOUSE];
    for (int i = 0; i < NHOUSE; i++) {
        pid_t p = fork();
        if (p == 0) _exit(house(i));
        kids[i] = p;
    }
    int ok = 0, bad = 0;
    for (int i = 0; i < NHOUSE; i++) {
        int st; waitpid(kids[i], &st, 0);
        if (WIFEXITED(st) && WEXITSTATUS(st) == 0) ok++; else bad++;
    }
    say("[conc] VERDICT %d/%d houses started concurrently on one brick, %d failed\n",
        ok, NHOUSE, bad);
    say("[conc] loop devices used: 1 (was %d under the per-house design)\n", NHOUSE);
    return 0;
}
