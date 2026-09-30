/* unit-ignoreterm: traps TERM/INT/HUP/QUIT, prints that it reached the
 * handler (the same "print from inside the handler so it is observably
 * reached" discipline anysig.c and term.c already use), and then never
 * exits -- for docs/options/32's `grace_period` escalation, which needs
 * a house that genuinely ignores its stop signal rather than one that
 * merely takes its time (anysig.c, term.c) to prove the SIGKILL fires
 * because the declared window elapsed, not because the house happened
 * to stop on its own around the same time.
 *
 * EVERY LINE PADDED to a divisor of the logger's chunk buffer (64), the
 * same reason houses/lastwords.c and houses/orphan.c do -- harness.md's
 * log-chunk trap. Not in the TCB. */
#define _GNU_SOURCE
#include <signal.h>
#include <string.h>
#include <unistd.h>

static volatile sig_atomic_t got_sig;

static void on_sig(int s)
{
    got_sig = s;
    char b[64];
    memset(b, ' ', sizeof b);
    b[sizeof b - 1] = '\n';
    memcpy(b, "[ignoreterm-house] handler ran, ignoring", 40);
    ssize_t r = write(1, b, sizeof b); (void)r;
}

int main(void)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_sig;
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
    sigaction(SIGQUIT, &sa, NULL);

    for (;;)
        usleep(20 * 1000);
}
