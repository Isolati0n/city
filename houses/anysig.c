/* unit-anysig: reports WHICH signal it received, for docs/options/31's
 * `stop_signal` field -- term.c only ever traps SIGTERM, and this field
 * needs a fixture that can tell TERM/INT/HUP/QUIT apart, the same "print
 * from inside the handler so it is observably reached" discipline
 * term.c already uses. Not in the TCB. */
#define _GNU_SOURCE
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static volatile sig_atomic_t got_sig;

static void on_sig(int s)
{
    got_sig = s;
    const char m[] = "[anysig-house] handler ran\n";
    ssize_t r = write(1, m, sizeof m - 1); (void)r;
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

    for (int i = 0; i < 300 && !got_sig; i++)
        usleep(20 * 1000);

    char b[64];
    int n = snprintf(b, sizeof b, "[anysig-house] got=%d\n", (int)got_sig);
    ssize_t r = write(1, b, (size_t)n); (void)r;
    return got_sig ? 0 : 3;
}
