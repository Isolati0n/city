/* freezecrash.c -- the claim under test:
 *
 *   "A frozen house is stopped at a point the process chose -- a safe
 *    point, not a crash point -- so on power loss the writable layer is
 *    exactly as consistent as it was at the instant of freeze. That is
 *    strictly better than the running case."
 *
 * The suspicion: cgroup.freeze stops a task at a point safe for the
 * KERNEL (not holding locks), not at a point meaningful to the
 * APPLICATION. A process midway through "append the data, then rename the
 * commit marker" is stopped between those two steps just as readily as a
 * crashing one. And freezing does not sync, so nothing dirty reaches the
 * disk. If so, freezing stops the on-disk state getting WORSE and does
 * not make it CONSISTENT -- a much weaker claim than "strictly better."
 *
 * Method, same shape as the earlier power-loss measurement:
 *   a writer appends a 512-byte chunk carrying a generation, then renames
 *   a marker carrying the same generation. No fsync anywhere.
 *   A controller freezes its cgroup mid-stream and says so.
 *   The harness then SIGKILLs QEMU -- power loss while frozen.
 *   The image is inspected from outside afterwards.
 *
 * If the marker is AHEAD of the data on disk, freezing did not save it.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdarg.h>
#include <signal.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/wait.h>

#define CG   "/sys/fs/cgroup/fz"
#define DIR  "/nwfc"
#define CHUNK 512

static void say(const char *fmt, ...)
{
    char b[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}
static int wrf(const char *p, const char *v)
{ int fd = open(p, O_WRONLY); if (fd < 0) return -1;
  int r = (int)write(fd, v, strlen(v)); close(fd); return r < 0 ? -1 : 0; }

int main(void)
{
    mkdir("/sys/fs/cgroup", 0755);
    mount("cgroup2", "/sys/fs/cgroup", "cgroup2", 0, NULL);
    mkdir(CG, 0755);
    mkdir(DIR, 0755);

    char pd[64], pm[64], pt[64];
    snprintf(pd, sizeof pd, DIR "/d");
    snprintf(pm, sizeof pm, DIR "/m");
    snprintf(pt, sizeof pt, DIR "/m.tmp");
    unlink(pd); unlink(pm); unlink(pt);

    pid_t kid = fork();
    if (kid == 0) {
        int d = open(pd, O_WRONLY | O_CREAT | O_APPEND, 0644);
        if (d < 0) _exit(1);
        char buf[CHUNK];
        for (unsigned gen = 1;; gen++) {
            memset(buf, 0, sizeof buf);
            snprintf(buf, sizeof buf, "GEN %u", gen);
            if (write(d, buf, CHUNK) != CHUNK) continue;      /* the data */
            int t = open(pt, O_WRONLY | O_CREAT | O_TRUNC, 0644);
            if (t < 0) continue;
            char mb[32];
            int n = snprintf(mb, sizeof mb, "%u\n", gen);
            if (write(t, mb, n) != n) { close(t); continue; }
            close(t);
            rename(pt, pm);                                    /* the marker */
        }
        _exit(0);
    }

    char pb[16]; snprintf(pb, sizeof pb, "%d\n", (int)kid);
    if (wrf(CG "/cgroup.procs", pb) < 0) {
        say("[fc] could not place the writer in the cgroup errno=%d\n", errno);
        kill(kid, SIGKILL); return 1;
    }
    say("[fc] writer pid %d in cgroup, appending then renaming, no fsync\n", (int)kid);

    sleep(12);

    if (wrf(CG "/cgroup.freeze", "1\n") < 0) {
        say("[fc] FREEZE FAILED errno=%d\n", errno); kill(kid, SIGKILL); return 1; }
    say("[fc] FROZEN\n");

    /* what does the writer's own view say right now? this is page cache,
     * not disk -- recorded so the two can be compared afterwards */
    char b[64] = {0};
    int mf = open(pm, O_RDONLY);
    if (mf >= 0) { (void)!read(mf, b, sizeof b - 1); close(mf); b[strcspn(b,"\n")] = 0; }
    struct stat st; stat(pd, &st);
    say("[fc] at freeze: marker=%s  d is %lld bytes = %lld chunks\n",
        b, (long long)st.st_size, (long long)(st.st_size / CHUNK));
    say("[fc] READY -- kill the power now; nothing will change from here\n");

    for (;;) pause();
}
