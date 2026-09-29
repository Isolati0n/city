/* CBMC harness: nw_decide(), over every value of every argument.
 *
 * decide() has no loop and touches no buffer, so nothing here is
 * narrowed the way caller_nw_check.c narrows n_units/n_binds -- every
 * argument is genuinely free, including the full-width int/unsigned
 * arguments (deaths, budget, exit_status). Six named invariants, seven
 * `__CPROVER_assert`s -- "deaths rise only on an unrequested LOCKED
 * exit" is split into its own condition plus the clean-oneshot
 * exclusion, each independently falsifiable and each named in its own
 * message, so a failing run says which one broke. None of the seven
 * assertions mentions `budget`'s VALUE (only whether it was consulted
 * at all, via `lock`) -- the RESTART/SPENT threshold itself is not
 * something this harness checks; that is tests/decide_seq.c's job,
 * over its own bounded domain. Phase 2, docs/OPERATOR-BRIEF.md
 * Section 3. */
#include "../decide.c"

int nondet_int(void);
unsigned nondet_uint(void);

int main(void)
{
    int lock = nondet_int();
    int complete_on_0 = nondet_int();
    int stopping = nondet_int();
    int stop_requested = nondet_int();
    int start_requested = nondet_int();
    int has_child = nondet_int();
    int child_exited = nondet_int();
    int exit_status = nondet_int();
    int deaths = nondet_int();
    unsigned budget = nondet_uint();

    /* Only meaningful, well-formed calls: child_exited/exit_status are
     * only asked about when a child actually exists, matching every
     * real caller in nwsup.c. An unconstrained call with has_child=0
     * and child_exited=1 has no real-shell counterpart to check
     * against, so it is excluded rather than given a made-up meaning. */
    __CPROVER_assume(has_child || !child_exited);
    /* deaths is a count the shell has already accumulated: never
     * negative. */
    __CPROVER_assume(deaths >= 0);

    enum nw_decision d = nw_decide(lock, complete_on_0, stopping,
                                    stop_requested, start_requested,
                                    has_child, child_exited, exit_status,
                                    deaths, budget);

    /* 1. STOP never increments deaths: whenever a death would
     * otherwise be counted (a child that exited), a satisfied STOP
     * request bypasses RESTART/SPENT entirely. */
    if (has_child && child_exited && stop_requested) {
        __CPROVER_assert(d != NW_DECIDE_RESTART && d != NW_DECIDE_SPENT,
                          "STOP never increments deaths");
    }

    /* 2. UNLOCKED never increments deaths, for any exit that is not
     * already covered by invariant 1. */
    if (has_child && child_exited && !lock) {
        __CPROVER_assert(d != NW_DECIDE_RESTART && d != NW_DECIDE_SPENT,
                          "UNLOCKED never increments deaths");
    }

    /* 3. SPENT is LOCKED-only. (Its absorbing property -- nothing
     * calls decide() again afterward -- is a shell-structure fact,
     * not something a stateless function can be asked about; pinned
     * instead by the event-sequence test / the shell's own
     * `_exit()` immediately after SPENT.) */
    if (d == NW_DECIDE_SPENT) {
        __CPROVER_assert(lock, "SPENT is LOCKED-only");
    }

    /* 4. deaths rise only on an unrequested LOCKED exit: the converse
     * of 1+2 stated directly, plus excluding a clean oneshot finish. */
    if (d == NW_DECIDE_RESTART || d == NW_DECIDE_SPENT) {
        __CPROVER_assert(has_child && child_exited && lock &&
                          !stop_requested,
                          "deaths rise only on an unrequested LOCKED exit");
        __CPROVER_assert(!(complete_on_0 && WIFEXITED(exit_status) &&
                            WEXITSTATUS(exit_status) == 0),
                          "a clean oneshot finish never counts as a death");
    }

    /* 5. A shutting-down supervisor never forks or restarts. */
    if (stopping) {
        __CPROVER_assert(d != NW_DECIDE_FORK && d != NW_DECIDE_RESTART,
                          "a shutting-down supervisor never forks or restarts");
    }

    /* 6. START on a running house is a no-op: start_requested must not
     * change the outcome while a child is live and has not exited. */
    if (has_child && !child_exited) {
        enum nw_decision with_start =
            nw_decide(lock, complete_on_0, stopping, stop_requested,
                      /*start_requested=*/1, has_child, child_exited,
                      exit_status, deaths, budget);
        enum nw_decision without_start =
            nw_decide(lock, complete_on_0, stopping, stop_requested,
                      /*start_requested=*/0, has_child, child_exited,
                      exit_status, deaths, budget);
        __CPROVER_assert(with_start == without_start,
                          "START on a running house is a no-op");
    }

    return 0;
}
