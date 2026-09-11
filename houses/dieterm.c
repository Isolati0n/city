/* unit-dieterm: dies once, then behaves like unit-term.
 *
 * Exists for one reason. test_shutdown_does_not_restart used a house that
 * had never died, so `nw-sup`'s shutdown guard could be narrowed to
 * `if (stopping && deaths == 0)` and the test stayed green -- the guard
 * broken for every house that had restarted even once, which is the only
 * kind of house the guard matters for. `control` found that by running the
 * mutant. A house whose supervisor already has deaths > 0 when SIGTERM
 * arrives is what makes the assertion mean what its name says.
 *
 * First run: exit 1 immediately, so nw-sup restarts it and `deaths` becomes
 * nonzero. Every run after: install a TERM handler and wait, exactly as
 * unit-term does.
 *
 * The run is distinguished by a marker file. That is a fixed path in a
 * fixture, which is fine here and would not be in the TCB: the suite
 * removes it before booting, and a stale marker makes the test fail loudly
 * (no `restart` line) rather than quietly changing what is measured.
 * Not in the TCB.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#define MARK "/tmp/nw-dieterm.mark"

static volatile sig_atomic_t got;

static void on_term(int s)
{
    (void)s;
    got = 1;
    const char m[] = "[dieterm] SIGTERM handler ran\n";
    ssize_t r = write(1, m, sizeof m - 1); (void)r;
}

int main(void)
{
    int fd = open(MARK, O_CREAT | O_EXCL | O_WRONLY, 0600);
    if (fd >= 0) {
        close(fd);
        const char m[] = "[dieterm] first run, dying to bank a death\n";
        ssize_t r = write(1, m, sizeof m - 1); (void)r;
        return 1;
    }

    const char m2[] = "[dieterm] restarted, now waiting for TERM\n";
    ssize_t r = write(1, m2, sizeof m2 - 1); (void)r;

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_term;
    sigaction(SIGTERM, &sa, NULL);

    for (int i = 0; i < 300 && !got; i++)
        usleep(20 * 1000);

    if (got) {
        const char m[] = "[dieterm] exiting cleanly after TERM\n";
        r = write(1, m, sizeof m - 1); (void)r;
        return 0;
    }
    const char m[] = "[dieterm] never got TERM\n";
    r = write(1, m, sizeof m - 1); (void)r;
    return 3;
}
