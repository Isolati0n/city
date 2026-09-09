#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>

int main(void)
{
    const char *name = getenv("NW_UNIT");
    const char *kit  = getenv("NW_WIRES");
    const char *lids = getenv("NW_LIDS");
    if (!name) name = "?";
    if (!kit) kit = "?";
    if (!lids) lids = "0";

    int live = 0;
    int socks = 0;
    for (int fd = 3; fd < 64; fd++) {
        if (fcntl(fd, F_GETFD) < 0) continue;
        live++;
        int t = 0;
        socklen_t sl = sizeof t;
        if (getsockopt(fd, SOL_SOCKET, SO_TYPE, &t, &sl) == 0)
            socks++;
    }

    char line[192];
    int n = snprintf(line, sizeof line,
                     "house=%s kit_env=%s fds_ge3=%d socket_wires=%d lids=%s pid=%d\n",
                     name, kit, live, socks, lids, (int)getpid());
    if (n > 0) {
        ssize_t w = write(1, line, (size_t)n);
        (void)w;
    }
    for (int i = 0; i < 15; i++)
        usleep(50 * 1000);
    return 0;
}
