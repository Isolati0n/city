/* freezetests.c -- the three open questions about freeze, in one run.
 *
 *  T1  SYNC BEFORE FREEZE. The last run showed freezing does not make the
 *      layer durable: power loss right after a freeze left the marker at
 *      5129 and the data file EMPTY. The proposed fix is one syncfs before
 *      the freeze. Does it actually make the on-disk state consistent?
 *      (The harness cuts power immediately after READY and inspects.)
 *
 *  T2  PROCESS TREES. The freeze was proved on one process. A house is
 *      several programs. Does the cgroup stop all of them, including a
 *      child forked after the cgroup was populated?
 *
 *  T3  THE COST. "Twenty frozen houses" needs a number. Measure a house's
 *      memory.current before and after freezing, so the arithmetic the
 *      operator is supposed to be able to see actually exists.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdarg.h>
#include <stdlib.h>
#include <signal.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <sys/syscall.h>

#define CGROOT "/sys/fs/cgroup"
#define DIR    "/nwft"
#define CHUNK  512

static void say(const char *fmt, ...)
{
    char b[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}
static int wrf(const char *p, const char *v)
{ int fd = open(p, O_WRONLY); if (fd < 0) return -1;
  int r = (int)write(fd, v, strlen(v)); close(fd); return r < 0 ? -1 : 0; }
static long readnum(const char *p)
{ int fd = open(p, O_RDONLY); if (fd < 0) return -1;
  char b[64] = {0}; (void)!read(fd, b, sizeof b - 1); close(fd); return atol(b); }
static void catf(const char *p, const char *label)
{ int fd = open(p, O_RDONLY); if (fd < 0) { say("      %s: unreadable\n", label); return; }
  char b[256] = {0}; (void)!read(fd, b, sizeof b - 1); close(fd);
  for (char *q = b; *q; q++) if (*q == '\n') *q = ' ';
  say("      %s: %s\n", label, b); }

int main(void)
{
    mkdir(CGROOT, 0755);
    mount("cgroup2", CGROOT, "cgroup2", 0, NULL);
    /* delegate memory so memory.current exists in children */
    wrf(CGROOT "/cgroup.subtree_control", "+memory\n");
    mkdir(DIR, 0755);

    /* ---------------- T2 + T3: a house that is a process TREE --------- */
    mkdir(CGROOT "/tree", 0755);
    int sync_pipe[2]; if (pipe(sync_pipe)) return 1;

    pid_t root = fork();
    if (root == 0) {
        close(sync_pipe[0]);
        /* allocate something measurable, then fork two children AFTER we
         * are already in the cgroup -- they should be captured too */
        size_t sz = 24u << 20;
        char *mem = malloc(sz);
        if (mem) for (size_t i = 0; i < sz; i += 4096) mem[i] = (char)i;
        for (int i = 0; i < 2; i++) {
            if (fork() == 0) {
                int fd = open(DIR "/child", O_WRONLY | O_CREAT | O_APPEND, 0644);
                for (unsigned long n = 0;; n++) {
                    char b[32]; int k = snprintf(b, sizeof b, "%d:%lu\n", i, n);
                    (void)!pwrite(fd, b, k, 0);
                    for (volatile int s = 0; s < 150000; s++) { }
                }
            }
        }
        (void)!write(sync_pipe[1], "r", 1);
        for (unsigned long n = 0;; n++) {
            int fd = open(DIR "/root", O_WRONLY | O_CREAT | O_TRUNC, 0644);
            if (fd >= 0) { char b[32]; int k = snprintf(b, sizeof b, "%lu\n", n);
                           (void)!write(fd, b, k); close(fd); }
            for (volatile int s = 0; s < 150000; s++) { }
        }
    }
    char pb[16]; snprintf(pb, sizeof pb, "%d\n", (int)root);
    wrf(CGROOT "/tree/cgroup.procs", pb);
    close(sync_pipe[1]);
    char c; (void)!read(sync_pipe[0], &c, 1);
    sleep(2);

    /* how many tasks did the cgroup capture? */
    int n_tasks = 0;
    FILE *f = fopen(CGROOT "/tree/cgroup.procs", "r");
    if (f) { char l[32]; while (fgets(l, sizeof l, f)) n_tasks++; fclose(f); }
    long mem_before = readnum(CGROOT "/tree/memory.current");
    say("[T2] cgroup captured %d tasks (1 forked before, 2 after joining)\n", n_tasks);
    say("[T3] memory.current while running: %ld bytes (%.1f MB)\n",
        mem_before, mem_before / 1048576.0);

    char r1[32] = {0}, r2[32] = {0}, k1[32] = {0}, k2[32] = {0};
    int rf = open(DIR "/root", O_RDONLY);  if (rf >= 0) { (void)!pread(rf, r1, 31, 0); close(rf); }
    int kf = open(DIR "/child", O_RDONLY); if (kf >= 0) { (void)!pread(kf, k1, 31, 0); close(kf); }

    wrf(CGROOT "/tree/cgroup.freeze", "1\n");
    usleep(400000);
    catf(CGROOT "/tree/cgroup.events", "cgroup.events");
    sleep(2);
    rf = open(DIR "/root", O_RDONLY);  if (rf >= 0) { (void)!pread(rf, r2, 31, 0); close(rf); }
    kf = open(DIR "/child", O_RDONLY); if (kf >= 0) { (void)!pread(kf, k2, 31, 0); close(kf); }
    for (char *q = r1; *q; q++) if (*q=='\n') *q=0;
    for (char *q = r2; *q; q++) if (*q=='\n') *q=0;
    for (char *q = k1; *q; q++) if (*q=='\n') *q=0;
    for (char *q = k2; *q; q++) if (*q=='\n') *q=0;
    say("[T2] parent  counter: %s -> %s  %s\n", r1, r2,
        strcmp(r1,r2)==0 ? "STOPPED" : "still running");
    say("[T2] child   counter: %s -> %s  %s\n", k1, k2,
        strcmp(k1,k2)==0 ? "STOPPED" : "STILL RUNNING -- tree not captured");

    long mem_frozen = readnum(CGROOT "/tree/memory.current");
    say("[T3] memory.current while frozen : %ld bytes (%.1f MB)\n",
        mem_frozen, mem_frozen / 1048576.0);
    say("[T3] so 20 such frozen houses hold about %.1f MB\n",
        20.0 * mem_frozen / 1048576.0);

    wrf(CGROOT "/tree/cgroup.freeze", "0\n");
    usleep(200000);
    kill(root, SIGKILL);

    /* ---------------- T1: syncfs before freeze ------------------------ */
    mkdir(CGROOT "/w", 0755);
    unlink(DIR "/d"); unlink(DIR "/m"); unlink(DIR "/m.tmp");
    pid_t w = fork();
    if (w == 0) {
        int d = open(DIR "/d", O_WRONLY | O_CREAT | O_APPEND, 0644);
        char buf[CHUNK];
        for (unsigned gen = 1;; gen++) {
            memset(buf, 0, sizeof buf);
            snprintf(buf, sizeof buf, "GEN %u", gen);
            if (write(d, buf, CHUNK) != CHUNK) continue;
            int t = open(DIR "/m.tmp", O_WRONLY | O_CREAT | O_TRUNC, 0644);
            if (t < 0) continue;
            char mb[32]; int n = snprintf(mb, sizeof mb, "%u\n", gen);
            (void)!write(t, mb, n); close(t);
            rename(DIR "/m.tmp", DIR "/m");
        }
    }
    snprintf(pb, sizeof pb, "%d\n", (int)w);
    wrf(CGROOT "/w/cgroup.procs", pb);
    say("[T1] writer %d running, append-then-rename, no fsync\n", (int)w);
    sleep(12);

    /* THE PROPOSED FIX: sync the layer, THEN freeze */
    int dirfd = open(DIR, O_RDONLY | O_DIRECTORY);
    say("[T1] syncfs before freeze...\n");
    if (dirfd >= 0) { syncfs(dirfd); close(dirfd); }
    wrf(CGROOT "/w/cgroup.freeze", "1\n");
    usleep(300000);

    char mb[32] = {0}; struct stat st;
    int m = open(DIR "/m", O_RDONLY);
    if (m >= 0) { (void)!read(m, mb, sizeof mb - 1); close(m); mb[strcspn(mb,"\n")] = 0; }
    stat(DIR "/d", &st);
    say("[T1] at freeze (in memory): marker=%s d=%lld chunks\n",
        mb, (long long)(st.st_size / CHUNK));
    say("[T1] READY -- cut power now\n");
    for (;;) pause();
}
