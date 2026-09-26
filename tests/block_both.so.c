/* LD_PRELOAD shim: forces BOTH SYS_pidfd_open and signalfd(2) to fail
 * with ENOSYS, every other syscall and symbol passes through
 * unchanged. Used by test_ctl_tier3_fallback_with_socket to prove
 * wait_house()'s THIRD tier -- the bounded 50ms poll()+WNOHANG loop --
 * actually reaps and services the control socket, rather than trusting
 * it because it compiles.
 *
 * tests/block_pidfd.so.c forces pidfd_open alone, which lands on the
 * SECOND tier (signalfd) every time -- `control` confirmed this via
 * strace, and test_ctl_pidfd_fallback_with_socket's own docstring says
 * so. This shim is that one plus a direct override of signalfd(2)
 * itself, so nothing in wait_house() can reach any wakeup but the
 * bounded poll+WNOHANG loop.
 *
 * Not TCB; not a house. The assertion lives in tests/run.py. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <sys/signalfd.h>
#include <sys/syscall.h>
#include <unistd.h>

#ifndef SYS_pidfd_open
#define SYS_pidfd_open 434
#endif

long syscall(long number, ...)
{
    static long (*real)(long, ...);
    if (!real) real = dlsym(RTLD_NEXT, "syscall");

    if (number == SYS_pidfd_open) {
        dprintf(2, "block_both intercepted pidfd_open, forcing ENOSYS\n");
        errno = ENOSYS;
        return -1;
    }

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
    (void)fd; (void)mask; (void)flags;
    dprintf(2, "block_both intercepted signalfd, forcing ENOSYS\n");
    errno = ENOSYS;
    return -1;
}
