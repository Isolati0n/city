/* Exhaustive event-sequence test for nw_decide() (decide.c), Phase 2.
 * Not a CBMC harness -- proofs/caller_decide.c already proves every
 * single call exhaustively over its full input domain. What THAT proof
 * cannot check is the SHELL: whether threading nw_decide()'s output
 * across a sequence of calls (fork it, wait for it, feed the next
 * event) stays consistent -- deaths only rising the ways it should,
 * SPENT/EXIT_SUP genuinely ending the run rather than being followed
 * by more calls, an unlocked house never once touching the budget.
 * This program is that shell, minimal and built only from decide.h's
 * documented contract (not decide.c's internal branches), driven
 * exhaustively over every short event sequence.
 *
 * SEQLEN=4 over a 5-symbol alphabet (START, STOP, TERM, EXIT_OK,
 * EXIT_BAD) is 625 sequences per (lock, complete_on_0, budget)
 * configuration; budget in 0..3 and both bools give 16 configurations,
 * 10000 sequences total, each at most 4 steps -- enough to reach SPENT
 * at every tested budget via consecutive deaths, to place a TERM at
 * every position in the sequence, and to interleave STOP/START/a death
 * within one run, while finishing in well under a second. */
#include "decide.h"
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>

enum ev { EV_START, EV_STOP, EV_TERM, EV_EXIT_OK, EV_EXIT_BAD, EV_COUNT };

static const char *ev_name(enum ev e)
{
    switch (e) {
    case EV_START: return "START";
    case EV_STOP: return "STOP";
    case EV_TERM: return "TERM";
    case EV_EXIT_OK: return "EXIT_OK";
    case EV_EXIT_BAD: return "EXIT_BAD";
    default: return "?";
    }
}

static int fail(const char *why, int lock, int c0, unsigned budget,
                 enum ev *seq, int n, int upto)
{
    fprintf(stderr, "FAIL: %s\n  lock=%d complete_on_0=%d budget=%u\n"
                    "  sequence:", why, lock, c0, budget);
    for (int i = 0; i < n; i++) {
        fprintf(stderr, " %s%s", ev_name(seq[i]), i == upto ? "<--" : "");
    }
    fprintf(stderr, "\n");
    return 1;
}

/* One run of one sequence against one (lock, complete_on_0, budget)
 * configuration. Returns 0 on success, 1 and prints a diagnostic on
 * the first violated property. */
static int run_one(int lock, int complete_on_0, unsigned budget,
                    enum ev *seq, int n)
{
    int stopping = 0, stop_requested = 0, has_child = 0, deaths = 0;
    int alive = 1;
    int deaths_at_last_check = 0;

    /* Boot: the first decision, with no child yet and nothing pending. */
    enum nw_decision d = nw_decide(lock, complete_on_0, 0, 0, 0,
                                    0, 0, 0, deaths, budget);
    if (lock) {
        if (d != NW_DECIDE_FORK)
            return fail("boot: a LOCKED house must fork immediately",
                        lock, complete_on_0, budget, seq, n, -1);
        has_child = 1;
    } else {
        if (d != NW_DECIDE_IDLE)
            return fail("boot: an UNLOCKED house must start idle",
                        lock, complete_on_0, budget, seq, n, -1);
    }

    for (int i = 0; i < n && alive; i++) {
        enum ev e = seq[i];

        /* Structural sanity: deaths never falls, an unlocked house's
         * deaths never leaves 0, budget is never silently exceeded. */
        if (deaths < deaths_at_last_check)
            return fail("deaths went backward", lock, complete_on_0,
                        budget, seq, n, i);
        if (!lock && deaths != 0)
            return fail("UNLOCKED deaths is nonzero", lock, complete_on_0,
                        budget, seq, n, i);
        deaths_at_last_check = deaths;

        switch (e) {
        case EV_START:
            if (!has_child) {
                d = nw_decide(lock, complete_on_0, stopping, stop_requested,
                              1, 0, 0, 0, deaths, budget);
                if (d == NW_DECIDE_FORK) {
                    has_child = 1;
                } else if (d == NW_DECIDE_IDLE) {
                    /* stays idle -- only legal if not locked, or if
                     * stopping already ended things (checked below). */
                    if (lock && !stopping)
                        return fail("LOCKED refused a START with no "
                                    "child and no shutdown in progress",
                                    lock, complete_on_0, budget, seq, n, i);
                } else if (d == NW_DECIDE_EXIT_SUP) {
                    alive = 0;
                } else {
                    return fail("START with no child produced an "
                                "unexpected decision", lock, complete_on_0,
                                budget, seq, n, i);
                }
            } else {
                /* Invariant 6, re-checked at the trace level: a live,
                 * not-yet-exited child's outcome must not depend on
                 * start_requested -- compared directly, the same way
                 * proofs/caller_decide.c does, rather than asserting a
                 * fixed output (which would be wrong whenever
                 * stop_requested is independently forcing TERM_CHILD). */
                enum nw_decision with_s = nw_decide(lock, complete_on_0,
                                                     stopping, stop_requested,
                                                     1, 1, 0, 0, deaths,
                                                     budget);
                enum nw_decision without_s = nw_decide(lock, complete_on_0,
                                                        stopping,
                                                        stop_requested, 0, 1,
                                                        0, 0, deaths, budget);
                if (with_s != without_s)
                    return fail("START on a running house was not a no-op",
                                lock, complete_on_0, budget, seq, n, i);
                d = without_s;
            }
            break;

        case EV_STOP:
            if (has_child && !stop_requested) {
                stop_requested = 1;
                d = nw_decide(lock, complete_on_0, stopping, 1, 0,
                              1, 0, 0, deaths, budget);
                if (d != NW_DECIDE_TERM_CHILD)
                    return fail("STOP on a live child did not signal it",
                                lock, complete_on_0, budget, seq, n, i);
            }
            /* idle-STOP and already-requested-STOP: idempotent no-op,
             * matching nwsup.c's handling either way. */
            break;

        case EV_TERM:
            if (!stopping) {
                stopping = 1;
                d = nw_decide(lock, complete_on_0, 1, stop_requested, 0,
                              has_child, 0, 0, deaths, budget);
                if (has_child) {
                    if (d != NW_DECIDE_TERM_CHILD)
                        return fail("TERM with a live child did not "
                                    "signal it", lock, complete_on_0,
                                    budget, seq, n, i);
                } else {
                    if (d != NW_DECIDE_EXIT_SUP)
                        return fail("TERM with no child did not exit "
                                    "the supervisor", lock, complete_on_0,
                                    budget, seq, n, i);
                    alive = 0;
                }
            }
            break;

        case EV_EXIT_OK:
        case EV_EXIT_BAD:
            if (!has_child)
                break; /* nothing to exit -- vacuous in this sequence */
            {
                int status = (e == EV_EXIT_OK) ? 0 /* WIFEXITED, code 0 */
                                                : (1 << 8); /* exit code 1 */
                int was_stop_requested = stop_requested;
                int clean_oneshot = complete_on_0 && e == EV_EXIT_OK;
                d = nw_decide(lock, complete_on_0, stopping, stop_requested,
                              0, 1, 1, status, deaths, budget);
                has_child = 0;

                /* Per-branch checks, not just the generic budget/lock
                 * bookkeeping below -- `control` found that this
                 * sequence test caught an off-by-one in the budget
                 * comparison and an UNLOCKED-reaches-SPENT mutation,
                 * but silently accepted a STOP-counts-as-a-death
                 * mutation and a disabled-oneshot-completion mutation,
                 * both of which the real boot tests
                 * (test_ctl_stop_does_not_count, test_kind_exit0)
                 * still caught, but this file's own docstring claims
                 * to check "deaths only rising the ways they should"
                 * and did not, for these two cases. Asserted here
                 * directly, against decide.h's own documented
                 * priority order (stop_requested first, then a clean
                 * oneshot finish, then lock), not left to the generic
                 * switch below to catch incidentally. */
                if (stopping) {
                    /* Shutdown outranks everything else decide() is
                     * ever asked about -- a child that exited while
                     * stopping ends the supervisor regardless of
                     * stop_requested, complete_on_0 or lock. */
                    if (d != NW_DECIDE_EXIT_SUP)
                        return fail("an exit during shutdown did not "
                                    "exit the supervisor", lock,
                                    complete_on_0, budget, seq, n, i);
                } else if (was_stop_requested) {
                    if (d != NW_DECIDE_IDLE)
                        return fail("a satisfied STOP did not go idle",
                                    lock, complete_on_0, budget, seq, n, i);
                } else if (clean_oneshot) {
                    if (d != NW_DECIDE_EXIT_SUP)
                        return fail("a clean oneshot finish did not exit "
                                    "the supervisor", lock, complete_on_0,
                                    budget, seq, n, i);
                } else if (!lock) {
                    if (d != NW_DECIDE_IDLE)
                        return fail("an UNLOCKED unrequested exit did not "
                                    "go idle", lock, complete_on_0, budget,
                                    seq, n, i);
                } else {
                    if (d != NW_DECIDE_RESTART && d != NW_DECIDE_SPENT)
                        return fail("a LOCKED unrequested exit reached "
                                    "neither RESTART nor SPENT",
                                    lock, complete_on_0, budget, seq, n, i);
                }

                switch (d) {
                case NW_DECIDE_IDLE:
                    stop_requested = 0;
                    break;
                case NW_DECIDE_EXIT_SUP:
                    alive = 0;
                    break;
                case NW_DECIDE_RESTART: {
                    if (!lock)
                        return fail("UNLOCKED reached RESTART",
                                    lock, complete_on_0, budget, seq, n, i);
                    deaths++;
                    if (deaths > (int)budget)
                        return fail("RESTART happened past budget",
                                    lock, complete_on_0, budget, seq, n, i);
                    /* The shell logs, then immediately re-forks. */
                    enum nw_decision d2 = nw_decide(lock, complete_on_0,
                                                     stopping, 0, 0,
                                                     0, 0, 0, deaths, budget);
                    if (d2 != NW_DECIDE_FORK)
                        return fail("RESTART was not followed by a FORK",
                                    lock, complete_on_0, budget, seq, n, i);
                    has_child = 1;
                    break;
                }
                case NW_DECIDE_SPENT:
                    if (!lock)
                        return fail("UNLOCKED reached SPENT",
                                    lock, complete_on_0, budget, seq, n, i);
                    deaths++;
                    if (deaths <= (int)budget)
                        return fail("SPENT happened within budget",
                                    lock, complete_on_0, budget, seq, n, i);
                    alive = 0;
                    break;
                default:
                    return fail("a child exit produced an unexpected "
                                "decision", lock, complete_on_0, budget,
                                seq, n, i);
                }
            }
            break;

        default:
            break;
        }
    }

    /* Absorption, checked structurally: once alive is false the loop
     * above stopped calling nw_decide() at all for the rest of the
     * sequence, by construction (the `&& alive` in the for-condition).
     * A run that reaches here with alive==0 having still processed
     * every remaining event as a no-op is exactly invariant 3 (and the
     * SPENT/EXIT_SUP half of invariant 5) demonstrated across a real
     * trace, which proofs/caller_decide.c's own per-call model cannot
     * do. Nothing further to assert; reaching this line at all, for
     * every one of the 10000 sequences, is the result. */
    return 0;
}

int main(void)
{
    unsigned total = 0;
    for (int lock = 0; lock <= 1; lock++)
    for (int c0 = 0; c0 <= 1; c0++)
    for (unsigned budget = 0; budget <= 3; budget++) {
        enum ev seq[4];
        for (int a = 0; a < EV_COUNT; a++)
        for (int b = 0; b < EV_COUNT; b++)
        for (int c = 0; c < EV_COUNT; c++)
        for (int e = 0; e < EV_COUNT; e++) {
            seq[0] = (enum ev)a; seq[1] = (enum ev)b;
            seq[2] = (enum ev)c; seq[3] = (enum ev)e;
            if (run_one(lock, c0, budget, seq, 4))
                return 1;
            total++;
        }
    }
    printf("decide_seq: %u sequences, all clean\n", total);
    return 0;
}
