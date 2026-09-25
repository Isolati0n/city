#define _GNU_SOURCE
#include <dlfcn.h>
#include <poll.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>

static int n_poll, n_waitpid;

int poll(struct pollfd *fds, nfds_t n, int timeout)
{
    static int (*real)(struct pollfd *, nfds_t, int);
    if (!real) real = dlsym(RTLD_NEXT, "poll");
    n_poll++;
    return real(fds, n, timeout);
}

pid_t waitpid(pid_t pid, int *st, int flags)
{
    static pid_t (*real)(pid_t, int *, int);
    if (!real) real = dlsym(RTLD_NEXT, "waitpid");
    n_waitpid++;
    if (pid < 0) {
        /* mutation the test is hunting: waitpid(-1) identity */
        dprintf(2, "count_wait WAITPID_ANY\n");
    }
    return real(pid, st, flags);
}

__attribute__((destructor))
static void dump(void)
{
    dprintf(2, "count_wait poll=%d waitpid=%d\n", n_poll, n_waitpid);
}
