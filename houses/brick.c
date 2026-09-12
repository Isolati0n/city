/* unit-brick — a house that reports which root it is living in.
 *
 * Built static on purpose. A brick carries its own libraries; linking this
 * against the machine's would mean the loader had followed a path out of the
 * brick, and a test that passes for that reason proves nothing. Static
 * removes the question.
 *
 * It reports three things and asserts none of them -- the suite does the
 * asserting, so a failure prints what was actually seen:
 *   id=      the contents of /id, the file every brick carries at the same
 *            path with different contents
 *   root=    the top-level entries of /, sorted
 *   bind=    the contents of $NW_BIND_0/token, if the plan declared a bind.
 *            The path comes from the environment because a bind is the same
 *            path inside and out, so the fixture cannot know it in advance.
 *
 * Not in the TCB. Test fixture.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/sysmacros.h>
#include <sys/syscall.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/*  EVERY LINE ITS OWN PADDED WRITE. brick.c used printf with a single
 * fflush at exit, so the whole report left as one block and PID 1's
 * 256-byte read split it mid-line -- `one mk_bind` / `[one] =ok(0)`,
 * which broke the field parser. It had been under the limit by luck and
 * three added probes pushed it over. WIDTH divides the logger's buffer,
 * so every write is one whole line and a bounded read can only return
 * whole lines. houses/orphan.c and houses/layer.c are the precedent. */
#define WIDTH 64
static void nw_emit(const char *fmt, ...)
{
    char t[WIDTH * 4], b[WIDTH + 1];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(t, sizeof t, fmt, ap);
    va_end(ap);
    size_t n = strlen(t);
    if (n > WIDTH - 1) n = WIDTH - 1;
    memset(b, ' ', WIDTH);
    memcpy(b, t, n);
    b[WIDTH - 1] = '\n';
    (void)!write(1, b, WIDTH);
}


static const char *me = "?";

static void report_file(const char *key, const char *path)
{
    char buf[128];
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        nw_emit("%s %s=absent", me, key);
        return;
    }
    ssize_t n = read(fd, buf, sizeof buf - 1);
    close(fd);
    if (n < 0) n = 0;
    buf[n] = 0;
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == ' ')) buf[--n] = 0;
    nw_emit("%s %s=%s", me, key, buf);
}

static int cmpstr(const void *a, const void *b)
{
    return strcmp(*(const char *const *)a, *(const char *const *)b);
}

static void report_root(void)
{
    char *names[64];
    int n = 0;
    DIR *d = opendir("/");
    if (!d) {
        nw_emit("%s root=unreadable", me);
        return;
    }
    struct dirent *e;
    while ((e = readdir(d)) && n < 64) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        names[n++] = strdup(e->d_name);
    }
    closedir(d);
    qsort(names, (size_t)n, sizeof names[0], cmpstr);
    /* Built into one buffer and emitted as ONE padded line -- it was
     * three printf calls, and a line assembled from several writes is
     * the split this whole conversion is about. */
    char row[WIDTH * 4];
    int k = snprintf(row, sizeof row, "%s root=", me);
    for (int i = 0; i < n && k > 0 && (size_t)k < sizeof row; i++)
        k += snprintf(row + k, sizeof row - (size_t)k, "%s%s",
                      i ? "," : "", names[i]);
    nw_emit("%s", row);
}

/* Only meaningful when /proc is bound into the brick, which the suite does
 * for exactly one house. nw-sup opens two directory descriptors to perform
 * the pivot; both are O_CLOEXEC and both are closed before execv, and this
 * is what says so by running rather than by argument. */
static void report_fds(void)
{
    DIR *d = opendir("/proc/self/fd");
    if (!d) {
        nw_emit("%s fds=noproc", me);
        return;
    }
    int n = 0, self = dirfd(d);
    struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        int fd = atoi(e->d_name);
        if (fd >= 3 && fd != self) n++;
    }
    closedir(d);
    nw_emit("%s fds_ge3=%d", me, n);
}

/* A CENSUS, not a count. `fds_ge3=0` pins the conjunction of O_CLOEXEC and
 * the close() calls in lid_brick and pins NEITHER: `fd-auditor` dropped
 * O_CLOEXEC from all three new descriptors and the test passed, dropped all
 * three close() calls and it passed, and only removing both turned it red.
 * Phase 2 took that unpinned conjunction from two descriptors to five.
 *
 * Saying WHAT each descriptor is makes a single change visible: an
 * inherited image fd shows up as fd3=/nw/bricks/<hash>.img rather than as a
 * count that went from 0 to 1. It is also the bug 4/9/13 check done
 * properly -- those were silently wrong ROUTING, so the question is not how
 * many descriptors a house holds but which one is where. Reporting the
 * pipe's dev/ino lets the suite assert that fd 1 and fd 2 are the same pipe
 * within a house and different pipes between houses, which a count cannot
 * express at all. */
static void report_census(void)
{
    DIR *d = opendir("/proc/self/fd");
    if (!d) {
        nw_emit("%s census=noproc", me);
        return;
    }
    int self = dirfd(d);
    struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        int fd = atoi(e->d_name);
        if (fd == self) continue;
        /* readlink ONLY -- no fstat. The link target for a pipe is
         * already "pipe:[INODE]", so it carries the identity this census
         * exists to compare, and fstat costs a syscall the house cannot
         * make: glibc routes it through statx, which is not in the
         * allow-list, so seccomp killed the house before it printed a
         * line. Widening the filter for a fixture is exactly what
         * runtime.md forbids -- "adding a syscall to the allow-list
         * requires naming the unit that needs it" -- and no unit needs
         * it. readlinkat is already allowed. */
        /* readlinkAT, called directly, NOT glibc's readlink(). The
         * allow-list has __NR_readlinkat and not __NR_readlink, and on
         * x86-64 glibc's readlink() wrapper issues the legacy __NR_readlink
         * -- so the house was killed by SIGSYS before printing a line.
         * nw-sup reported that as status=18176, which is 71 << 8, its code
         * for "did not exit normally", and it read as a mysterious early
         * death rather than as a blocked syscall. Bisected by removing the
         * call. The fix is to use the syscall the filter already permits;
         * widening the filter for a fixture is what runtime.md forbids. */
        char link[256], target[256];
        snprintf(link, sizeof link, "/proc/self/fd/%d", fd);
        ssize_t k = syscall(SYS_readlinkat, AT_FDCWD, link, target,
                            sizeof target - 1);
        if (k < 0) k = 0;
        target[k] = 0;
        nw_emit("%s fd%d=%s", me, fd, target);
    }
    closedir(d);
}

/* Try to create a file and say what happened. This is how confinement is
 * demonstrated rather than asserted: under the landlock lid the house's own
 * brick is read-only and a declared bind is not, so the two answers must
 * differ. A test that only proves the house started proves nothing about
 * what it can touch. */
static void report_write(const char *key, const char *dir)
{
    char p[512];
    snprintf(p, sizeof p, "%s/probe.tmp", dir);
    int fd = open(p, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) {
        nw_emit("%s %s=denied(%d)", me, key, errno);
        return;
    }
    ssize_t w = write(fd, "x", 1);
    close(fd);
    nw_emit("%s %s=%s", me, key, w == 1 ? "ok" : "openonly");
}

/* WRITE AN EXISTING FILE, as opposed to creating one. The two are
 * different rights under Landlock -- WRITE_FILE versus MAKE_REG -- and
 * report_write() above only ever probed the second, because it passes
 * O_CREAT. Until 2026-09-12 that did not matter: a house's root was the
 * bare erofs image and both failed with EROFS. Now the root is an
 * overlay and the lid grants one and withholds the other, so a probe
 * that conflates them cannot see what the lid does. */
static void report_write_existing(const char *key, const char *path)
{
    /* NOT /id. The first version probed that, and writing one byte over
     * the first byte of the file every other assertion reads turned
     * `id=brick-two` into `id=xrick-two` -- a probe that corrupts the
     * evidence the test is built on. make_brick bakes /w for this. */
    int fd = open(path, O_WRONLY);
    if (fd < 0) {
        nw_emit("%s %s=denied(%d)", me, key, errno);
        return;
    }
    ssize_t w = write(fd, "x", 1);
    int e = errno;
    close(fd);
    nw_emit("%s %s=%s(%d)", me, key, w == 1 ? "ok" : "writefail",
           w == 1 ? 0 : e);
}

/* A device node. This is what the lid still refuses after the write
 * grant, and the reason the MAKE_ rights stay withheld.
 *
 * ONLY WHEN SECCOMP IS NOT ON, and the guard is not squeamishness. The
 * allow-list in lids.c has no `mknod` and no `mknodat`, so under
 * lids=...,seccomp the filter kills this house before Landlock is
 * consulted -- measured, status 18176, which is the 71<<8 signature the
 * census probe produced for `readlink` in phase 2. Widening the filter
 * to let a FIXTURE ask a question is exactly what runtime.md forbids:
 * adding a syscall requires naming the unit that needs it, and no house
 * needs mknod. So the probe skips instead, and says it skipped -- an
 * absent line would read the same as a refusal.
 *
 * NW_LIDS is nw-sup's own input, inherited by the house; bit 0 is
 * NW_LID_SECCOMP. */
static void report_mknod(const char *key, const char *path)
{
    const char *lv = getenv("NW_LIDS");
    if (lv && (atoi(lv) & 1)) {
        nw_emit("%s %s=skipped-seccomp", me, key);
        return;
    }
    if (mknod(path, S_IFCHR | 0600, makedev(1, 3)) == 0) {
        nw_emit("%s %s=ok", me, key);
        return;   /* no unlink, same reason as mk_bind below */
    }
    nw_emit("%s %s=denied(%d)", me, key, errno);
}

int main(int argc, char **argv)
{
    /* nw-sup execs a house with argv[0] set to its unit name and nothing
     * else. Every line is tagged with it because PID 1's logger prefixes
     * the start of a write chunk, not each line inside one. */
    if (argc > 0 && argv[0] && argv[0][0]) me = argv[0];
    report_census();
    report_file("id", "/id");
    report_root();
    report_fds();
    const char *b = getenv("NW_BIND_0");
    if (!b || !b[0]) {
        nw_emit("%s bind=none", me);
    } else {
        char p[512];
        snprintf(p, sizeof p, "%s/token", b);
        report_file("bind", p);
    }
    report_write("wr_root", "");        /* creates -- needs MAKE_REG */
    if (b && b[0]) report_write("wr_bind", b);
    /* THE THREE THAT SEPARATE THE LID FROM THE FILESYSTEM, added for the
     * Landlock decision of 2026-09-12. Writing a file the brick already
     * contains must SUCCEED (the layer is writable); a device node at the
     * root must be refused (the MAKE_ rights are withheld); and creating
     * a plain file is the pair that shows the scoping still discriminates
     * -- refused at the root, allowed in a declared bind. */
    report_write_existing("wr_existing", "/w");
    report_mknod("mknod_root", "/probe.dev");
    if (b && b[0]) {
        char q[512];
        snprintf(q, sizeof q, "%s/mk.tmp", b);
        int fd = open(q, O_WRONLY | O_CREAT | O_TRUNC, 0600);
        nw_emit("%s mk_bind=%s(%d)", me, fd >= 0 ? "ok" : "denied",
               fd >= 0 ? 0 : errno);
        /* No unlink: `unlink`/`unlinkat` are not in the seccomp
         * allow-list either, and a fixture tidying up is not a unit that
         * needs a syscall. The file lands in a bind under the suite's
         * scratch and goes with it. Measured the other way first --
         * status 18176 again, in the house that HAS binds. */
        if (fd >= 0) close(fd);
    }
    fflush(stdout);
    return 0;
}
