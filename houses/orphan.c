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
 * EVERY LINE IS PADDED TO WIDTH BYTES, and self-tagging is not enough
 * on its own. The comment here used to say it was. PID 1's logger reads
 * 256 bytes and APPENDS a newline when the read does not end in one, so
 * a line straddling a chunk boundary comes out split mid-token:
 * `[orphan] run=3 leav` / `[orph] ing 3 behind`. Self-tagging defends
 * against a missing prefix; it does nothing about that. `control`
 * measured the suite failing 5 times in 12 under load, with a message
 * blaming the restart budget for a defect in how the console was read.
 *
 * WIDTH divides the logger's buffer, and every write here is one padded
 * line, so the pipe only ever holds whole lines and a 256-byte read can
 * only ever return whole lines. The split becomes impossible rather
 * than unlikely.
 *
 * The run number comes from a marker file, the same device
 * unit-dieterm uses. The suite clears it before booting AND asserts
 * that runs 1..4 each appear exactly once, so a stale marker fails
 * loudly. It did not until 2026-09-11: nothing read the run number, so
 * `control` deleted the marker logic entirely and the test passed three
 * times, and planted a stale marker and it passed three more. A fixed
 * path in a fixture is acceptable; one that nothing checks is a
 * decoration claiming to be a guard.
 *
 * Not in the TCB.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define KIDS 3
#define WIDTH 64          /* divides the logger's 256-byte buffer */
#ifndef ORPHAN_SLEEP_MS
#define ORPHAN_SLEEP_MS 400
#endif
#define MARK "/tmp/nw-orphan.mark"

/* One padded line, one write(2). Not signal-safe and does not need to
   be: nothing here runs from a handler. */
static void say_padded(char *buf, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(buf, WIDTH, fmt, ap);
    va_end(ap);
    if (n < 0) n = 0;
    if (n > WIDTH - 1) n = WIDTH - 1;
    memset(buf + n, '.', (size_t)(WIDTH - 1 - n));
    buf[WIDTH - 1] = '\n';
    ssize_t r = write(1, buf, WIDTH); (void)r;
}

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

    char line[WIDTH + 1];
    say_padded(line, "[orphan] run=%d forking %d children", run, KIDS);

    for (int i = 0; i < KIDS; i++) {
        pid_t p = fork();
        if (p == 0) {
            /* Outlive the parent, then exit. Reparents to PID 1. */
            usleep(ORPHAN_SLEEP_MS * 1000);
            _exit(0);
        }
        if (p > 0)
            say_padded(line, "[orphan] run=%d child=%d pid=%d", run, i,
                       (int)p);
    }

    say_padded(line, "[orphan] run=%d leaving %d behind", run, KIDS);

    /* Nonzero: nw-sup restarts, so the next run orphans again. */
    return 1;
}
