/* LD_PRELOAD shim: delays execv(2) by NW_TEST_EXEC_DELAY_MS milliseconds
 * (default 300) before calling through to the real one. Used to force,
 * deterministically rather than by luck, the window between fork() and
 * execv() that nw-sup's exec fence exists to close -- a FREEZE sent
 * during that window must be refused, not merely usually refused.
 *
 * Not TCB; not a house. The assertion lives in tests/run.py. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

int execv(const char *path, char *const argv[])
{
    static int (*real)(const char *, char *const *);
    if (!real) real = dlsym(RTLD_NEXT, "execv");

    long ms = 300;
    const char *e = getenv("NW_TEST_EXEC_DELAY_MS");
    if (e && *e) ms = atol(e);

    dprintf(2, "delay_exec: delaying execv by %ldms\n", ms);
    struct timespec ts;
    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (ms % 1000) * 1000000L;
    nanosleep(&ts, NULL);
    dprintf(2, "delay_exec: calling the real execv now\n");

    return real(path, argv);
}
