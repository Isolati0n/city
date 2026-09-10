#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(void)
{
    const char *name = getenv("NW_UNIT");
    const char *lids = getenv("NW_LIDS");
    if (!name) name = "?";
    if (!lids) lids = "0";

    /* A unit should hold nothing above stderr. Kept as a descriptor-hygiene
     * assertion after edges were removed: there is no longer anything a unit
     * is supposed to be handed beyond 0, 1 and 2. */
    int live = 0;
    for (int fd = 3; fd < 64; fd++) {
        if (fcntl(fd, F_GETFD) < 0) continue;
        live++;
    }

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
