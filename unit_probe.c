#define _GNU_SOURCE
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

/* Count descriptors above stderr by sweeping /proc/self/fd.
 *
 * This scanned a fixed range, fd 3 to 63, until 2026-09-10. Unit i's log pipe
 * lands on fd 5 + 2i, so the probe went blind at unit index 30 -- more than
 * half of NW_MAX_UNITS -- and it went blind *silently*: a leaked descriptor
 * on a high-numbered unit was reported as fds_ge3=0, so an instrument that
 * undercounts read exactly like a passing test. Logged as a defect against
 * the 2026-09-06 sources and it survived every rebuild since, for that
 * reason.
 *
 * There is no ceiling now, so there is no number to outgrow. Returns -1 when
 * /proc is unavailable rather than 0, because "I could not look" and "I
 * looked and found none" must not be the same answer -- the suite asserts
 * fds_ge3=0 and a -1 fails it loudly. */
static int live_fds(void)
{
    DIR *d = opendir("/proc/self/fd");
    if (!d) return -1;
    int n = 0, self = dirfd(d);
    struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        int fd = atoi(e->d_name);
        if (fd >= 3 && fd != self) n++;
    }
    closedir(d);
    return n;
}

int main(void)
{
    const char *name = getenv("NW_UNIT");
    const char *lids = getenv("NW_LIDS");
    if (!name) name = "?";
    if (!lids) lids = "0";

    /* A unit should hold nothing above stderr. Kept as a descriptor-hygiene
     * assertion after edges were removed: there is no longer anything a unit
     * is supposed to be handed beyond 0, 1 and 2. This is the suite's only
     * general non-provision assertion, and non-provision is what invariant 5
     * rests on. */
    int live = live_fds();

    char line[192];
    int n = snprintf(line, sizeof line,
                     "house=%s fds_ge3=%d lids=%s pid=%d\n",
                     name, live, lids, (int)getpid());
    if (n > 0) {
        ssize_t w = write(1, line, (size_t)n);
        (void)w;
    }
    for (int i = 0; i < 15; i++)
        usleep(50 * 1000);
    return 0;
}
