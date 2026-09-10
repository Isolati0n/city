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
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static const char *me = "?";

static void report_file(const char *key, const char *path)
{
    char buf[128];
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        printf("%s %s=absent\n", me, key);
        return;
    }
    ssize_t n = read(fd, buf, sizeof buf - 1);
    close(fd);
    if (n < 0) n = 0;
    buf[n] = 0;
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == ' ')) buf[--n] = 0;
    printf("%s %s=%s\n", me, key, buf);
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
        printf("%s root=unreadable\n", me);
        return;
    }
    struct dirent *e;
    while ((e = readdir(d)) && n < 64) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        names[n++] = strdup(e->d_name);
    }
    closedir(d);
    qsort(names, (size_t)n, sizeof names[0], cmpstr);
    printf("%s root=", me);
    for (int i = 0; i < n; i++)
        printf("%s%s", i ? "," : "", names[i]);
    printf("\n");
}

/* Only meaningful when /proc is bound into the brick, which the suite does
 * for exactly one house. nw-sup opens two directory descriptors to perform
 * the pivot; both are O_CLOEXEC and both are closed before execv, and this
 * is what says so by running rather than by argument. */
static void report_fds(void)
{
    DIR *d = opendir("/proc/self/fd");
    if (!d) {
        printf("%s fds=noproc\n", me);
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
    printf("%s fds_ge3=%d\n", me, n);
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
        printf("%s %s=denied(%d)\n", me, key, errno);
        return;
    }
    ssize_t w = write(fd, "x", 1);
    close(fd);
    printf("%s %s=%s\n", me, key, w == 1 ? "ok" : "openonly");
}

int main(int argc, char **argv)
{
    /* nw-sup execs a house with argv[0] set to its unit name and nothing
     * else. Every line is tagged with it because PID 1's logger prefixes
     * the start of a write chunk, not each line inside one. */
    if (argc > 0 && argv[0] && argv[0][0]) me = argv[0];
    report_file("id", "/id");
    report_root();
    report_fds();
    const char *b = getenv("NW_BIND_0");
    if (!b || !b[0]) {
        printf("%s bind=none\n", me);
    } else {
        char p[512];
        snprintf(p, sizeof p, "%s/token", b);
        report_file("bind", p);
    }
    report_write("wr_root", "");        /* "/probe.tmp" -- its own brick */
    if (b && b[0]) report_write("wr_bind", b);
    fflush(stdout);
    return 0;
}
