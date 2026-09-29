#ifndef NW_DECIDE_H
#define NW_DECIDE_H

/* nw-sup's supervision decision, factored out as a pure function.
 *
 * Phase 2 (docs/OPERATOR-BRIEF.md Section 3, the pure-core refactor).
 * decide() reads a snapshot of nw-sup's current situation and returns
 * exactly one action for the shell to perform. It does no I/O, starts
 * no clock, and holds no state of its own between calls -- every call
 * is a fresh function of its arguments, which is what makes it provable
 * and what makes an exhaustive test over short input sequences mean
 * something.
 *
 * WHAT STAYS IN THE SHELL, deliberately, and why:
 *
 * - The actual fork(), execv(), wait_house() call, kill() syscall,
 *   write_evidence() call and say() line are all imperative side
 *   effects. decide() never performs them; it only names which one
 *   the shell should perform next.
 *
 * - deaths, budget and every other bookkeeping value are the SHELL's
 *   state, not decide()'s. The shell increments `deaths` itself when
 *   decide() returns NW_DECIDE_RESTART or NW_DECIDE_SPENT, then calls
 *   decide() again (with has_child=0) to learn what to do next.
 *
 * - The asynchronous SIGTERM handler (on_term() in nwsup.c) keeps
 *   forwarding TERM to a live child DIRECTLY, by calling kill() from
 *   inside the handler, exactly as it does today. This is not routed
 *   through decide(): a signal handler cannot wait for the shell's
 *   main loop to next call decide() (that might not happen until an
 *   arbitrarily long wait_house() unblocks), and kill() is async-
 *   signal-safe while a general call-decide()-then-act sequence,
 *   run from inside a handler, would not gain anything decide() can
 *   express -- the handler already knows definitively "there is a
 *   live child and we are shutting down", which is the one input
 *   combination decide() would need to be asked about, and the
 *   handler must act on it as this instant, not the next one decide()
 *   is consulted at. decide()'s NW_DECIDE_TERM_CHILD output governs
 *   the two OTHER places nw-sup sends a child SIGTERM on the main
 *   thread of control, both already present today: immediately after
 *   fork() returns, in case `stopping` raced in during the fork
 *   itself (nwsup.c's post-fork `if (stopping)` check), and inside
 *   handle_ctl_live() when an explicit STOP request arrives while the
 *   child is still running. The signal handler's direct kill is a
 *   necessary, redundant enforcement of the same fact for the case
 *   neither of those two call sites is being executed at the moment
 *   the signal lands.
 *
 * - Whether nw-sup is currently blocked in the idle poll() loop is not
 *   an input to decide() either. The shell infers it from decide()'s
 *   own last answer: after NW_DECIDE_IDLE, the shell blocks in poll()
 *   on the control socket and only calls decide() again once a START
 *   (or STOP, which is idempotent while idle) request arrives -- at
 *   which point has_child is still 0 and start_requested is 1.
 *
 * lock:            1 = LOCKED (today's only reachable behavior, until
 *                  the plan-format bump adds a `lock` field -- until
 *                  then the shell always passes 1 here). 0 = UNLOCKED:
 *                  starts and stays idle except while explicitly
 *                  started, never consumes budget, never reaches
 *                  NW_DECIDE_SPENT.
 * complete_on_0:   1 if this unit is NW_KIND_ONESHOT -- a clean exit(0)
 *                  ends the supervisor rather than being a death.
 * stopping:        the supervisor-wide shutdown flag (on_term() sets
 *                  it; it never clears).
 * stop_requested:  an explicit STOP was received for the current (or
 *                  about-to-be-decided) child and has not yet been
 *                  fully acted on. The shell clears it once the child
 *                  this STOP was for has actually stopped.
 * start_requested: an explicit START was received while idle (has_child
 *                  is 0). Ignored whenever has_child is 1: START on an
 *                  already-running house is always a no-op, for both
 *                  lock states, which is invariant 6.
 * has_child:       there is currently a forked, not-yet-reaped child.
 * child_exited:    only meaningful when has_child is 1: the child has
 *                  just exited and exit_status is valid.
 * exit_status:     a raw wait(2) status, valid only when child_exited
 *                  is 1. decide() reads it with WIFEXITED/WEXITSTATUS,
 *                  never anything requiring interpretation beyond that.
 * deaths:          the number of unrequested LOCKED deaths so far,
 *                  before this call (the shell increments it itself
 *                  after seeing NW_DECIDE_RESTART or NW_DECIDE_SPENT,
 *                  not before).
 * budget:          the configured hard total. budget == 0 means no
 *                  restart is ever allowed -- the first unrequested
 *                  LOCKED death is already spent.
 */

enum nw_decision {
    NW_DECIDE_FORK,       /* no child; start one. */
    NW_DECIDE_WAIT,       /* a child is running and nothing else to do;
                            * block in wait_house(). */
    NW_DECIDE_IDLE,       /* no child, and none should be started yet;
                            * block in poll() on the control socket. */
    NW_DECIDE_RESTART,    /* the child died, unrequested, LOCKED, budget
                            * remains: account for the death, log it,
                            * then the shell calls decide() again to
                            * get NW_DECIDE_FORK. */
    NW_DECIDE_SPENT,      /* the child died, unrequested, LOCKED, budget
                            * is exhausted: account for the death, log
                            * it, and the supervisor exits. Absorbing --
                            * nothing calls decide() again afterward,
                            * because the process is gone. */
    NW_DECIDE_EXIT_SUP,   /* the supervisor itself should exit now: no
                            * live child left to deal with, either
                            * because shutdown reached this point with
                            * none, or because this unit's oneshot
                            * finished cleanly. */
    NW_DECIDE_TERM_CHILD, /* send the live child a SIGTERM right now,
                            * from the shell's ordinary control flow
                            * (not the signal handler -- see above). */
};

enum nw_decision nw_decide(int lock, int complete_on_0, int stopping,
                            int stop_requested, int start_requested,
                            int has_child, int child_exited,
                            int exit_status, int deaths, unsigned budget);

#endif
