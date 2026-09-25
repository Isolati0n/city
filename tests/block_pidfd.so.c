/* LD_PRELOAD shim: forces SYS_pidfd_open to fail with ENOSYS, every
 * other syscall passes through unchanged. Used by
 * test_pidfd_open_failure_falls_back to prove wait_house()'s fallback
 * to plain waitpid(2) actually reaps and accounts for a death, rather
 * than trusting the fallback code because it compiles.
 *
 * Not TCB; not a house. The assertion lives in tests/run.py. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
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
        dprintf(2, "block_pidfd intercepted pidfd_open, forcing ENOSYS\n");
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
