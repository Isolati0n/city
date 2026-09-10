/* unit-term: proves the inherited signal mask lets a house catch SIGTERM.
 *
 * Reports whether SIGTERM is blocked in the mask it inherited through
 * fork+exec, installs a handler, and waits. A test that only checks the
 * process is gone proves nothing -- SIGKILL achieves that too. This prints
 * from inside the handler so the handler is observably reached.
 * Not in the TCB. */
#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static volatile sig_atomic_t got;

static void on_term(int s)
{
    (void)s;
    got = 1;
    const char m[] = "[term-house] SIGTERM handler ran\n";
    ssize_t r = write(1, m, sizeof m - 1); (void)r;
}

int main(void)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_term;
    sigaction(SIGTERM, &sa, NULL);

    sigset_t cur;
    sigprocmask(SIG_BLOCK, NULL, &cur);
    char b[96];
    int n = snprintf(b, sizeof b, "[term-house] sigterm_blocked=%d\n",
                     sigismember(&cur, SIGTERM));
    ssize_t r = write(1, b, (size_t)n); (void)r;

    for (int i = 0; i < 300 && !got; i++)
        usleep(20 * 1000);

    if (got) {
        const char m[] = "[term-house] exiting cleanly after TERM\n";
        r = write(1, m, sizeof m - 1); (void)r;
        return 0;
    }
    const char m[] = "[term-house] never got TERM\n";
    r = write(1, m, sizeof m - 1); (void)r;
    return 3;
}
