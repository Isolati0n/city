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
 * On SIGTERM this writes N lines and exits.
 *
 * EACH LINE IS PADDED TO WIDTH BYTES, and that is the whole mechanism.
 * Separate write(2) calls were the first attempt and they buy NOTHING:
 * a pipe coalesces, five 23-byte lines are 115 bytes, and the logger's
 * 256-byte read takes all five at once -- so a drain that stops after
 * one chunk relayed every line and passed. `control` installed exactly
 * that defect and this fixture passed nine times out of nine, under an
 * ok line asserting the property it had just failed to check.
 *
 * WIDTH divides the logger's buffer, so 5 * 64 = 320 bytes cannot fit
 * in one 256-byte read and the drain must come back for more. Choosing
 * a divisor rather than any large number also means a chunk boundary
 * always lands ON a line boundary: the logger appends a newline when a
 * read does not end in one, which otherwise splits a line mid-token --
 * see houses/orphan.c, where that turned into a real flake.
 *
 * Each line is self-tagged and numbered because PID 1's logger prefixes
 * a write CHUNK and not a line (harness.md, the log-chunk trap), so the
 * prefix cannot be used to count them.
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

#ifndef LINES
#define LINES 5
#endif
#define WIDTH 64          /* divides the logger's 256-byte buffer */

static char lines[LINES][WIDTH];

static volatile sig_atomic_t got;

static void on_term(int s)
{
    (void)s;
    for (int i = 0; i < LINES; i++) {
        ssize_t r = write(1, lines[i], WIDTH);
        (void)r;
    }
    got = 1;
}

int main(void)
{
    for (int i = 0; i < LINES; i++) {
        memset(lines[i], '.', WIDTH);
        /* snprintf into a scratch buffer, then copy without its NUL:
           the index has to survive past 9, which a single-character
           patch could not do. */
        char t[WIDTH];
        int n = snprintf(t, sizeof t, "[lastwords] bye %d of %d ",
                         i + 1, LINES);
        if (n > WIDTH - 1) n = WIDTH - 1;
        memcpy(lines[i], t, (size_t)n);
        lines[i][WIDTH - 1] = '\n';
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
