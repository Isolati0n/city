/* unit-reach-victim: an ordinary, ostensibly-confined house that stays
 * alive long enough for houses/console-escape-probe.c to try reaching
 * it. Not shipped anywhere; a measurement fixture for
 * docs/options/10-console-house.md's process-reach question.
 *
 * Declares lids=seccomp on purpose: seccomp restricts what THIS process
 * may call, not what another process may do TO it. kill(2), ptrace(2)
 * and process_vm_readv(2) are governed by the KERNEL's credential
 * check on the CALLER (same uid, or CAP_SYS_PTRACE), which has nothing
 * to do with this house's own filter -- so a seccomp lid here is not a
 * defence against the probe and the measurement is honest about that
 * rather than picking an undefended victim to make the point look
 * smaller.
 *
 * No brick: this is a plain house, and the question is about process
 * reach, not filesystem confinement -- that is the escape probe's
 * question, tested separately.
 */
#include <unistd.h>

int main(void)
{
    /* Long enough for the probe's PID sweep and its per-pid battery of
     * operations to run to completion; short enough not to stall the
     * boot for anyone reading the log by hand. */
    sleep(8);
    return 0;
}
