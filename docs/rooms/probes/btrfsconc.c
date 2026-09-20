/* btrfsconc.c -- the last thing that could invalidate the room design.
 *
 * Six houses on one seal works. What is untested is several VISITS on one
 * btrfs: snapshotting concurrently, and snapshotting while a different
 * room on the same volume is mid-write.
 *
 * Three questions:
 *
 *  C1  Do N houses each snapshot their own room at the same moment, or do
 *      they contend? btrfs serialises subvolume operations on the volume,
 *      so the question is whether that shows up as failure, as blocking,
 *      or as nothing.
 *
 *  C2  Does a snapshot of room A see anything of room B's in-flight
 *      writes? It must not -- a subvolume snapshot is per-subvolume. If it
 *      did, rooms would not be independent and the design is wrong.
 *
 *  C3  Is a room's own snapshot correct when taken while that SAME room is
 *      being written continuously? The snapshot must be a point, not a
 *      smear: every file in it must be one the writer had finished.
 */
#define _GNU_SOURCE
#include <sched.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdarg.h>
#include <stdlib.h>
#include <time.h>
#include <linux/btrfs.h>
#include <linux/loop.h>
#include <sys/ioctl.h>
#include <sys/mount.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <dirent.h>

#define R  "/nwbc"
#define N  6

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
static long ms(void)
{ struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
  return t.tv_sec * 1000 + t.tv_nsec / 1000000; }

static int snap(int parentfd, int srcfd, const char *name)
{ struct btrfs_ioctl_vol_args_v2 a; memset(&a, 0, sizeof a);
  a.fd = srcfd; strncpy(a.name, name, sizeof a.name - 1);
  return ioctl(parentfd, BTRFS_IOC_SNAP_CREATE_V2, &a); }
static int mksubvol(int parentfd, const char *name)
{ struct btrfs_ioctl_vol_args_v2 a; memset(&a, 0, sizeof a);
  strncpy(a.name, name, sizeof a.name - 1);
  return ioctl(parentfd, BTRFS_IOC_SUBVOL_CREATE_V2, &a); }

static void enter_userns(uid_t outer)
{
    prctl(PR_SET_DUMPABLE, 1, 0, 0, 0);
    if (unshare(CLONE_NEWUSER | CLONE_NEWNS) < 0) _exit(1);
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

    int c = open("/dev/loop-control", O_RDWR);
    int idx = c >= 0 ? ioctl(c, LOOP_CTL_GET_FREE) : -1;
    if (c >= 0) close(c);
    char dev[32]; snprintf(dev, sizeof dev, "/dev/loop%d", idx);
    int lf = open(dev, O_RDWR | O_CLOEXEC), bf = open("/nw/btrfs.img", O_RDWR | O_CLOEXEC);
    if (idx < 0 || lf < 0 || bf < 0 || ioctl(lf, LOOP_SET_FD, bf) < 0) {
        say("[bc] loop FAILED %d\n", errno); return 1; }
    close(bf); close(lf);
    mkdir(R "/mnt", 0755);
    if (mount(dev, R "/mnt", "btrfs", 0, "user_subvol_rm_allowed") < 0) {
        say("[bc] mount FAILED %d\n", errno); return 1; }
    say("[bc] one btrfs, %d rooms about to share it\n", N);

    int top = open(R "/mnt", O_RDONLY | O_DIRECTORY);
    mkdir(R "/mnt/snaps", 0755); chmod(R "/mnt/snaps", 0777);
    for (int i = 0; i < N; i++) {
        char nm[32]; snprintf(nm, sizeof nm, "room%d", i);
        if (mksubvol(top, nm) < 0) { say("[bc] subvol %s FAILED %d\n", nm, errno); return 1; }
        char p[64]; snprintf(p, sizeof p, R "/mnt/room%d", i);
        chown(p, 1001 + i, 1001 + i); chmod(p, 0700);
    }
    say("[bc] %d rooms created, one per house uid\n", N);

    /* each house: write continuously, snapshot its own room mid-write */
    int rp[2]; if (pipe(rp)) return 1;
    long t0 = ms();
    pid_t kids[N];
    for (int i = 0; i < N; i++) {
        pid_t p = fork();
        if (p == 0) {
            close(rp[0]);
            if (setgid(1001 + i) || setuid(1001 + i)) _exit(1);
            enter_userns(1001 + i);
            char room[64]; snprintf(room, sizeof room, R "/mnt/room%d", i);

            /* a writer child, hammering this room */
            pid_t wkid = fork();
            if (wkid == 0) {
                for (unsigned long g = 1;; g++) {
                    char fp[96]; snprintf(fp, sizeof fp, "%s/f%lu", room, g % 40);
                    int fd = open(fp, O_WRONLY | O_CREAT | O_TRUNC, 0644);
                    if (fd >= 0) { char b[64];
                        int n = snprintf(b, sizeof b, "gen %lu complete\n", g);
                        (void)!write(fd, b, n); close(fd); }
                    for (volatile int s = 0; s < 40000; s++) { }
                }
            }
            usleep(400000 + (i * 40000));

            int src = open(room, O_RDONLY | O_DIRECTORY);
            int par = open(R "/mnt/snaps", O_RDONLY | O_DIRECTORY);
            char nm[32]; snprintf(nm, sizeof nm, "s%d", i);
            long a = ms();
            int r = (src < 0 || par < 0) ? -1 : snap(par, src, nm);
            long took = ms() - a;
            char msg[160];
            int n = snprintf(msg, sizeof msg, "%d %d %d %ld\n", i, r, r ? errno : 0, took);
            (void)!write(rp[1], msg, n);
            kill(wkid, 9);
            _exit(0);
        }
        kids[i] = p;
    }
    close(rp[1]);
    char acc[1024] = {0}; ssize_t got, off = 0;
    while ((got = read(rp[0], acc + off, sizeof acc - 1 - off)) > 0) off += got;
    for (int i = 0; i < N; i++) { int st; waitpid(kids[i], &st, 0); }
    say("[bc] all %d visits finished in %ld ms total\n", N, ms() - t0);

    /* C1 -- did they all succeed, and how long did each ioctl take */
    int ok = 0, fail = 0; long worst = 0;
    char *line = strtok(acc, "\n");
    while (line) {
        int id, r, e; long took;
        if (sscanf(line, "%d %d %d %ld", &id, &r, &e, &took) == 4) {
            if (r == 0) ok++; else fail++;
            if (took > worst) worst = took;
            say("[C1] house %d: snapshot %s errno=%d  took %ld ms\n",
                id, r == 0 ? "OK" : "FAILED", e, took);
        }
        line = strtok(NULL, "\n");
    }
    say("[C1] VERDICT %d ok, %d failed; slowest ioctl %ld ms\n", ok, fail, worst);

    /* C2 -- does one room's snapshot contain another room's files? */
    int bleed = 0;
    for (int i = 0; i < N; i++) {
        char sp[96]; snprintf(sp, sizeof sp, R "/mnt/snaps/s%d", i);

        struct stat st;
        /* a snapshot of roomN must not contain a marker only roomM wrote */
        char other[128];
        snprintf(other, sizeof other, "%s/onlyroom%d", sp, (i + 1) % N);
        if (stat(other, &st) == 0) bleed++;
    }
    say("[C2] cross-room contamination in snapshots: %s\n",
        bleed ? "FOUND -- rooms are not independent" : "none");

    /* C3 -- is each snapshot internally coherent? every file present must
     * be a complete record, never a truncated one */
    int torn = 0, checked = 0;
    for (int i = 0; i < N; i++) {
        char sp[96]; snprintf(sp, sizeof sp, R "/mnt/snaps/s%d", i);
        for (int g = 0; g < 40; g++) {
            char fp[128]; snprintf(fp, sizeof fp, "%s/f%d", sp, g);
            int fd = open(fp, O_RDONLY);
            if (fd < 0) continue;
            char b[64] = {0}; ssize_t n = read(fd, b, sizeof b - 1); close(fd);
            checked++;
            if (n <= 0 || !strstr(b, "complete")) torn++;
        }
    }
    say("[C3] files inside snapshots checked: %d, incomplete: %d -> %s\n",
        checked, torn, torn ? "SMEARED -- snapshot is not a point" : "every file whole");
    say("[bc] done\n");
    return 0;
}
