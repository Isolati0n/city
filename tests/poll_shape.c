#define _GNU_SOURCE
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef SYS_pidfd_open
#define SYS_pidfd_open 434
#endif

/* Two-fd poll: extra slot is serviced without reaping; house death is
 * the pidfd slot. Same shape nw-sup wait_house() uses. */

int main(void)
{
    int extra[2];
    if (pipe(extra) < 0)
        return 10;
    pid_t p = fork();
    if (p < 0)
        return 11;
    if (p == 0) {
        close(extra[0]);
        close(extra[1]);
        usleep(200000);
        _exit(7);
    }
    int pfd = (int)syscall(SYS_pidfd_open, p, 0U);
    if (pfd < 0)
        return 12;
    if (write(extra[1], "x", 1) != 1)
        return 13;

    struct pollfd pf[2] = {
        { .fd = pfd,       .events = POLLIN },
        { .fd = extra[0],  .events = POLLIN },
    };

    int saw_extra = 0, saw_house = 0;
    for (;;) {
        int pr = poll(pf, 2, 2000);
        if (pr < 0) {
            if (errno == EINTR)
                continue;
            return 14;
        }
        if (pr == 0)
            return 15;
        if (pf[1].revents & (POLLIN | POLLHUP)) {
            char c;
            (void)read(extra[0], &c, 1);
            saw_extra = 1;
            pf[1].events = 0;
        }
        if (pf[0].revents & (POLLIN | POLLHUP | POLLERR)) {
            saw_house = 1;
            break;
        }
        pf[0].revents = 0;
        pf[1].revents = 0;
    }
    int st = 0;
    if (waitpid(p, &st, 0) < 0)
        return 16;
    close(pfd);
    close(extra[0]);
    close(extra[1]);
    if (!saw_extra || !saw_house)
        return 17;
    if (!WIFEXITED(st) || WEXITSTATUS(st) != 7)
        return 18;
    fprintf(stderr, "poll-shape extra=1 house=1 status=7\n");
    return 0;
}
