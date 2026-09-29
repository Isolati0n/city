/* LD_PRELOAD shim: one reusable interception base, configured entirely
 * by environment variable, replacing three near-identical hand-rolled
 * files (count_wait.so.c, block_pidfd.so.c, block_both.so.c) that each
 * duplicated the same dlsym(RTLD_NEXT, ...) boilerplate for a
 * different syscall. Not TCB; not a house. The assertions live in
 * tests/run.py.
 *
 * `NW_FAULT_ENOSYS`: a comma-separated list drawn from {pidfd_open,
 * signalfd}. Naming one forces it to fail with ENOSYS; naming neither
 * (or leaving the variable unset) passes everything through
 * unchanged. pidfd_open is intercepted via the syscall(2) wrapper
 * because nwsup.c calls it that way -- `syscall(SYS_pidfd_open, ...)`
 * directly, never through glibc's own pidfd_open() symbol, which
 * exists on this machine (glibc >= 2.36) but is simply not what the
 * caller uses, so overriding it would intercept nothing; signalfd(2)
 * is overridden directly because nwsup.c does call that libc symbol.
 *
 * `NW_FAULT_LOG_WAITPID=1`: announce every waitpid() call, and a
 * second line for pid<0 specifically -- the exact `count_wait CALL
 * waitpid pid=%d` / `count_wait WAITPID_ANY` text tests/run.py greps
 * for, kept byte-for-byte so the tests that read it did not have to
 * change. No destructor here, deliberately, for the same reason
 * count_wait.so.c never had one: nwsup.c ends every path in _exit(2)
 * (grepped -- there is no plain return from main() and no libc exit(3)
 * call anywhere in it), and _exit(2) skips atexit handlers, stdio
 * flushing and ELF destructors entirely. A dump printed from an
 * __attribute__((destructor)) would never fire against this binary, so
 * it would prove nothing about whether this shim ever loaded -- the
 * unpaired-absence shape `control` found when this file was
 * destructor-only. Each call announces itself immediately instead, so
 * the caller can require a positive line before trusting any absence.
 *
 * poll(2) is overridden as a pure passthrough unconditionally,
 * matching count_wait.so.c's own original shape exactly -- nothing
 * configures it and nothing here changes that. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/signalfd.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#ifndef SYS_pidfd_open
#define SYS_pidfd_open 434
#endif

/* Comma-separated membership test against NW_FAULT_ENOSYS -- no
 * strtok/strdup, so no allocation and no mutation of getenv()'s own
 * buffer, in case a caller reads it again concurrently. */
static int fault_enosys_wants(const char *name)
{
    const char *list = getenv("NW_FAULT_ENOSYS");
    if (!list)
        return 0;
    size_t nlen = strlen(name);
    const char *p = list;
    while (*p) {
        const char *comma = strchr(p, ',');
        size_t seglen = comma ? (size_t)(comma - p) : strlen(p);
        if (seglen == nlen && memcmp(p, name, nlen) == 0)
            return 1;
        if (!comma)
            break;
        p = comma + 1;
    }
    return 0;
}

int poll(struct pollfd *fds, nfds_t n, int timeout)
{
    static int (*real)(struct pollfd *, nfds_t, int);
    if (!real)
        real = dlsym(RTLD_NEXT, "poll");
    return real(fds, n, timeout);
}

pid_t waitpid(pid_t pid, int *st, int flags)
{
    static pid_t (*real)(pid_t, int *, int);
    if (!real)
        real = dlsym(RTLD_NEXT, "waitpid");
    if (getenv("NW_FAULT_LOG_WAITPID")) {
        dprintf(2, "count_wait CALL waitpid pid=%d\n", (int)pid);
        if (pid < 0) {
            /* mutation the test is hunting: waitpid(-1) identity */
            dprintf(2, "count_wait WAITPID_ANY\n");
        }
    }
    return real(pid, st, flags);
}

long syscall(long number, ...)
{
    static long (*real)(long, ...);
    if (!real)
        real = dlsym(RTLD_NEXT, "syscall");

    if (number == SYS_pidfd_open && fault_enosys_wants("pidfd_open")) {
        dprintf(2, "fault_inject intercepted pidfd_open, forcing ENOSYS\n");
        errno = ENOSYS;
        return -1;
    }

    /* Passthrough for every other syscall(2) caller in the process.
     * Six args covers every syscall glibc's own <sys/syscall.h> users
     * make through this wrapper; extras are harmless if the real
     * syscall ignores them. */
    va_list ap;
    va_start(ap, number);
    long a1 = va_arg(ap, long);
    long a2 = va_arg(ap, long);
    long a3 = va_arg(ap, long);
    long a4 = va_arg(ap, long);
    long a5 = va_arg(ap, long);
    long a6 = va_arg(ap, long);
    va_end(ap);
    return real(number, a1, a2, a3, a4, a5, a6);
}

int signalfd(int fd, const sigset_t *mask, int flags)
{
    if (fault_enosys_wants("signalfd")) {
        dprintf(2, "fault_inject intercepted signalfd, forcing ENOSYS\n");
        errno = ENOSYS;
        return -1;
    }
    static int (*real)(int, const sigset_t *, int);
    if (!real)
        real = dlsym(RTLD_NEXT, "signalfd");
    return real(fd, mask, flags);
}
