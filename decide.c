#include "decide.h"

#include <sys/wait.h>

/* See decide.h for the full contract. This function performs no
 * syscall, reads no clock, and holds no state across calls -- every
 * output is a pure function of the arguments given. */
enum nw_decision nw_decide(int lock, int complete_on_0, int stopping,
                            int stop_requested, int start_requested,
                            int has_child, int child_exited,
                            int exit_status, int deaths, unsigned budget)
{
    (void)start_requested; /* only consulted in the !has_child branch below */

    /* Invariant: a shutting-down supervisor never forks or restarts.
     * Checked first and unconditionally, before anything else below
     * can produce FORK/RESTART/WAIT/IDLE -- there is no path back out
     * of this branch. */
    if (stopping) {
        if (has_child && !child_exited)
            return NW_DECIDE_TERM_CHILD;
        return NW_DECIDE_EXIT_SUP;
    }

    if (has_child) {
        if (!child_exited) {
            /* Invariant: START on a running house is a no-op --
             * start_requested is simply never consulted here. */
            if (stop_requested)
                return NW_DECIDE_TERM_CHILD;
            return NW_DECIDE_WAIT;
        }

        /* The child has exited. */

        /* Invariant: STOP never increments deaths. Checked before any
         * budget logic, for both lock states. */
        if (stop_requested)
            return NW_DECIDE_IDLE;

        /* A oneshot's clean exit(0) ends the supervisor -- not a
         * death, regardless of lock. */
        if (complete_on_0 && WIFEXITED(exit_status) &&
            WEXITSTATUS(exit_status) == 0)
            return NW_DECIDE_EXIT_SUP;

        /* Invariant: UNLOCKED never increments deaths. Any other exit
         * of an unlocked house returns to idle with no budget use. */
        if (!lock)
            return NW_DECIDE_IDLE;

        /* LOCKED, unrequested exit: the only path that ever reaches
         * the budget, and the only one that ever increments deaths
         * (the shell does the increment; decide() only names which of
         * the two outcomes this death produced).
         *
         * `deaths` counts deaths BEFORE this one; the death now being
         * decided would make it one higher, and that is spent once
         * the incremented count would exceed budget. The comparison
         * below is written to avoid computing that increment at all
         * -- the first CBMC run of this file found `deaths + 1 >
         * (int)budget` genuinely overflowing at deaths == INT_MAX,
         * which `int deaths` in nwsup.c cannot rule out by its type
         * alone, and the two forms are equivalent over the integers
         * with no such risk. budget == 0 (never restart) falls out of
         * this for free: with deaths never negative (the shell's own
         * invariant, assumed by the harness, not enforced here), the
         * condition below is then always true. */
        if (deaths >= (int)budget)
            return NW_DECIDE_SPENT; /* absorbing: the shell exits and
                                      * never calls decide() again. */
        return NW_DECIDE_RESTART;
    }

    /* No child. An unlocked house stays idle until asked; a locked
     * house always (re)starts immediately -- start_requested has no
     * effect on a locked house, matching today's unconditional
     * restart. */
    if (!lock && !start_requested)
        return NW_DECIDE_IDLE;
    return NW_DECIDE_FORK;
}
