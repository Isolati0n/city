/* unit-lastwords: measures whether a house's FINAL output survives
 * shutdown.
 *
 * The hard-total restart budget (CLAUDE.md invariant 4) means a house
 * that exhausts its budget stays dead until reboot. That was only
 * acceptable on one condition: the death is visible. If the lines a
 * house writes on its way out are discarded, an operator gets a black
 * screen and no explanation -- which is the thing that made a hard
 * total unsafe in the first place. So "the last lines survive" is not a
 * nicety; it is a precondition of a decision already taken.
 *
 * On SIGTERM this writes N lines with SEPARATE write(2) calls and then
 * exits. Separate calls matter: one big write could be relayed by a
 * single read in the logger, which would pass while a drain that stops
 * after one chunk is still broken. Each line is self-tagged and
 * numbered, because PID 1's logger prefixes a write CHUNK and not a
 * line (harness.md, the log-chunk trap), so the prefix cannot be used
 * to count them.
 *
 * Signal-safety: write(2) only, no printf, no snprintf in the handler.
 * The line buffers are built before the handler can run.
 *
 * Not in the TCB.
 */
#define _GNU_SOURCE
#include <signal.h>
#include <string.h>
#include <unistd.h>

#define LINES 5

static char lines[LINES][40];
static volatile sig_atomic_t got;

static void on_term(int s)
{
    (void)s;
    for (int i = 0; i < LINES; i++) {
        ssize_t r = write(1, lines[i], strlen(lines[i]));
        (void)r;
    }
    got = 1;
}

int main(void)
{
    for (int i = 0; i < LINES; i++) {
        memcpy(lines[i], "[lastwords] bye N of 5\n", 24);
        lines[i][16] = (char)('1' + i);
    }

    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_term;
    sigaction(SIGTERM, &sa, NULL);

    const char m[] = "[lastwords] up, waiting for TERM\n";
    ssize_t r = write(1, m, sizeof m - 1); (void)r;

    for (int i = 0; i < 600 && !got; i++)
        usleep(20 * 1000);
    return got ? 0 : 3;
}
