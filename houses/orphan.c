/* unit-orphan: makes orphans, on purpose, across a restart cycle.
 *
 * PID 1 reaps with waitpid(-1) and calls anything that is neither a
 * known house nor a known logger an orphan. Nothing in the suite has
 * ever produced one: `test_happy` asserts `orphans=0`, which is the
 * happy path, so the counter and the branch that increments it have
 * never been exercised by a test. `.claude/rules/runtime.md` records
 * that as a known-open gap, and this fixture is the subject it lacked.
 *
 * Each run forks KIDS children and exits WITHOUT waiting for them. The
 * children outlive their parent, so they reparent to the namespace's
 * init -- PID 1 -- which must reap them. Exiting nonzero makes nw-sup
 * restart the house, so the orphans arrive across a restart cycle
 * rather than only at shutdown, which is the part that was untested.
 *
 * The children sleep before exiting so that the reparenting is real: a
 * child that has already exited when its parent dies is reaped by the
 * parent's own exit path in the kernel and never reaches PID 1. The
 * sleep is a lab constant in a fixture, not a timeout in the TCB.
 *
 * Every line is self-tagged and carries the run number, because PID 1's
 * logger prefixes a write CHUNK and not a line (harness.md, the
 * log-chunk trap). The run number comes from a marker file, the same
 * device unit-dieterm uses and for the same reason: the suite removes
 * it before booting, and a stale marker makes the test fail loudly
 * rather than quietly change what is measured.
 *
 * Not in the TCB.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define KIDS 3
#ifndef ORPHAN_SLEEP_MS
#define ORPHAN_SLEEP_MS 400
#endif
#define MARK "/tmp/nw-orphan.mark"

int main(void)
{
    /* Run number = how many times this has started before. */
    int run = 0;
    int fd = open(MARK, O_CREAT | O_RDWR, 0600);
    if (fd >= 0) {
        char b[16];
        ssize_t n = pread(fd, b, sizeof b - 1, 0);
        if (n > 0) { b[n] = 0; run = atoi(b); }
        run++;
        int w = snprintf(b, sizeof b, "%d", run);
        ssize_t r = pwrite(fd, b, (size_t)w, 0); (void)r;
        close(fd);
    }

    char line[96];
    int n = snprintf(line, sizeof line,
                     "[orphan] run=%d forking %d children\n", run, KIDS);
    ssize_t r = write(1, line, (size_t)n); (void)r;

    for (int i = 0; i < KIDS; i++) {
        pid_t p = fork();
        if (p == 0) {
            /* Outlive the parent, then exit. Reparents to PID 1. */
            usleep(ORPHAN_SLEEP_MS * 1000);
            _exit(0);
        }
        if (p > 0) {
            n = snprintf(line, sizeof line,
                         "[orphan] run=%d child=%d pid=%d\n", run, i, (int)p);
            r = write(1, line, (size_t)n); (void)r;
        }
    }

    n = snprintf(line, sizeof line,
                 "[orphan] run=%d leaving %d behind\n", run, KIDS);
    r = write(1, line, (size_t)n); (void)r;

    /* Nonzero: nw-sup restarts, so the next run orphans again. */
    return 1;
}
