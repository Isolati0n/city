#define _GNU_SOURCE
#include <dlfcn.h>
#include <poll.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/wait.h>

/* No destructor here, deliberately: nwsup.c ends every path in
 * _exit(2) (grepped -- there is no plain return from main() and no
 * libc exit(3) call anywhere in it), and _exit(2) skips atexit
 * handlers, stdio flushing and ELF destructors entirely. A dump
 * printed from an __attribute__((destructor)) would never fire against
 * this binary, so it would prove nothing about whether this shim ever
 * loaded -- exactly the unpaired-absence shape `control` found when
 * this file was destructor-only. Each call now announces itself
 * immediately instead, so the caller can require a positive line
 * before trusting any absence. */
int poll(struct pollfd *fds, nfds_t n, int timeout)
{
    static int (*real)(struct pollfd *, nfds_t, int);
    if (!real) real = dlsym(RTLD_NEXT, "poll");
    return real(fds, n, timeout);
}

pid_t waitpid(pid_t pid, int *st, int flags)
{
    static pid_t (*real)(pid_t, int *, int);
    if (!real) real = dlsym(RTLD_NEXT, "waitpid");
    dprintf(2, "count_wait CALL waitpid pid=%d\n", (int)pid);
    if (pid < 0) {
        /* mutation the test is hunting: waitpid(-1) identity */
        dprintf(2, "count_wait WAITPID_ANY\n");
    }
    return real(pid, st, flags);
}
