/* unit-console-escape-probe: not a house anyone ships. Answers two
 * questions directly, per syscall, with no shell and no /proc dependency
 * in between -- docs/options/10-console-house.md's operator-ordered
 * measurements of what lids=newns,landlock,newnet actually lets a house
 * do, with no seccomp lid at all: what it can reach in the FILESYSTEM,
 * and what it can reach in the PROCESS TABLE.
 *
 * busybox's `mount`/`unshare` applets go through ash's standalone-shell
 * re-exec path, which (as measured) reads something under /proc that a
 * house's own pivoted root never mounts -- so busybox reported
 * "not found" for those two, which is true and is not an answer to the
 * question asked. This calls the syscalls directly and prints errno,
 * so a "not found" ambiguity cannot happen here.
 *
 * Not TCB. Not shipped in the real console house's brick -- a
 * measurement tool for this one investigation, exec'd by its own city
 * in place of the wrapper, over the same ttyS1 channel (filesystem
 * probes) or logged plainly to fd 1/2 (process-reach probes, which need
 * no tty at all).
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <sched.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/ptrace.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/sysmacros.h>
#include <sys/types.h>
#include <sys/uio.h>
#include <sys/wait.h>
#include <unistd.h>

static void report(const char *what, long rc)
{
    int e = rc < 0 ? errno : 0;
    printf("PROBE %s rc=%ld errno=%d(%s)\n", what, rc, e,
           e ? strerror(e) : "-");
    fflush(stdout);
}

static void fs_probes(void)
{
    report("mknod_char", mknod("/c", S_IFCHR | 0600, makedev(5, 1)));
    report("mknod_block", mknod("/b", S_IFBLK | 0600, makedev(7, 0)));
    report("mkdir_root", mkdir("/nwnewdir", 0755));
    /* /mnt is pre-baked into the brick as an empty directory, precisely
     * so a MAKE_DIR refusal (already established above) is not what
     * stops this probe from even trying the mount call itself. */
    report("mount_tmpfs", mount("tmpfs", "/mnt", "tmpfs", 0, NULL));
    report("mount_bind_root", mount("/", "/mnt", NULL, MS_BIND, NULL));
    /* SAME mknod, run again inside /mnt rather than at "/". The first
     * pair could be read two ways: Landlock refused MAKE_CHAR/MAKE_BLOCK
     * (the claimed mechanism), or something about "/" itself -- the
     * pivoted root's own mountpoint, rather than an ordinary directory
     * beneath it -- behaved specially. /mnt is an ordinary directory
     * inside the same writable overlay, ruling that out: if Landlock is
     * what refuses this, the refusal is identical here; if "/" was
     * special, it would not be. */
    report("mknod_char_in_layer", mknod("/mnt/c2", S_IFCHR | 0600, makedev(5, 1)));
    report("mknod_block_in_layer", mknod("/mnt/b2", S_IFBLK | 0600, makedev(7, 0)));
    report("unshare_newns", unshare(CLONE_NEWNS));
    report("pivot_root_noop", syscall(SYS_pivot_root, "/", "/"));
}

/* Process reach: this house's pivoted root has no /proc mounted in it at
 * all, and there is no pid namespace lid -- every process in the city
 * shares one flat pid space, the same numbers everywhere. So "what else
 * is running" cannot be listed; it can only be asked about, one pid at a
 * time, with kill(pid, 0) -- ESRCH means nothing is there, anything else
 * means something is, and the operator's instruction is to walk a range
 * rather than assume which number belongs to which house.
 *
 * Starting the sweep at 2 was the first attempt, and it was wrong,
 * found by running it rather than by reasoning about it: pids 2
 * upward are kernel threads (kthreadd and its children) on a fresh
 * boot, indistinguishable from a real process by kill(pid, 0) alone,
 * and this house's own supervisor and log-relaying process also sit
 * below this house's own pid -- SIGKILLing one of those broke this
 * probe's own log pipe and killed it with SIGPIPE mid-sweep before it
 * ever reached the victim. `nw-spawn` forks units in PLAN ORDER and
 * pids are allocated monotonically, so a unit declared after this one
 * (the victim) is guaranteed a HIGHER pid than this process's own --
 * starting the sweep at `self + 1` reaches only the victim's own
 * processes and nothing this house depends on for its own output.
 *
 * pid 1 is still never touched beyond the existence check even though
 * the range above rules it out anyway: killing it halts the whole city
 * (CLAUDE.md's "nothing a house does halts the city" names exactly two
 * exceptions and PID 1 dying is one of them), which is a different,
 * more destructive experiment than the one asked for here. */
/* The battery for one discovered pid: existence already reported by the
 * caller. Non-destructive operations first, so a later SIGKILL cannot
 * be blamed for a ptrace/read result that never got a chance to run. */
static void reach_battery(pid_t pid)
{
    char tag[40];

    long seize = ptrace(PTRACE_SEIZE, pid, NULL, NULL);
    snprintf(tag, sizeof tag, "reach_ptrace_seize_pid%d", pid);
    report(tag, seize);
    if (seize == 0) {
        /* This is a measurement, not a hold: let it go immediately. */
        ptrace(PTRACE_DETACH, pid, NULL, NULL);
    }

    /* No /proc means no way to learn a VALID address in the target from
     * here either, so this reads one byte from an arbitrary address
     * instead of a real one. That still answers the question:
     * process_vm_readv's permission check (ptrace_may_access, the same
     * gate ptrace(2) uses) runs before the kernel ever tries to resolve
     * the address, so EPERM means "refused before it looked" and
     * anything else (EFAULT included) means permission was granted and
     * only the address was bad. */
    char buf[1];
    struct iovec local = { .iov_base = buf, .iov_len = sizeof buf };
    struct iovec remote = { .iov_base = (void *)0x10000, .iov_len = sizeof buf };
    long rd = process_vm_readv(pid, &local, 1, &remote, 1, 0);
    snprintf(tag, sizeof tag, "reach_vm_readv_pid%d", pid);
    report(tag, rd);

    long kk = kill(pid, SIGKILL);
    snprintf(tag, sizeof tag, "reach_sigkill_pid%d", pid);
    report(tag, kk);
}

static void reach_probes(void)
{
    /* Three wrong guesses, each found by running, none by reading:
     *
     * First: scanning up from pid 2 hit kernel threads and this
     * house's OWN supervisor and log-relaying process, both allocated
     * before nw-spawn gets to a later-declared unit at all -- killing
     * one of them broke this probe's own log pipe and ended it with
     * SIGPIPE before it ever reached the victim.
     *
     * Second: scanning up from `self + 1` only, on the assumption a
     * later-declared unit always gets a higher pid. Measured false:
     * `nw-spawn` forks every unit's SUPERVISOR first, in plan order,
     * and only afterward does each supervisor apply its own lids and
     * fork+exec the house -- and this house's lid chain
     * (newns+landlock+newnet) is longer than the victim's (seccomp
     * alone), so the victim's simpler supervisor can win the race and
     * exec its house child at a LOWER pid than this one ends up with.
     * Logged: `spawned probe pid=80`, `spawned victim pid=82`, this
     * process's own pid 84 -- the victim's house pid (83) sits between
     * its own supervisor and this process, below `self`, not above it.
     *
     * Third: retrying the same one-directional range with a wider span
     * and a longer wait did not help, because the direction was the
     * bug, not the distance.
     *
     * The fix: a SYMMETRIC window around `self`, excluding this
     * process's own supervisor by name (`getppid()`, which for an
     * exec'd house is exactly its own nw-sup) rather than by position,
     * and staying well clear of the low pids where kernel threads and
     * this house's own logger live (measured to bite by "attempt one"
     * above, at pid 7). One settle wait, not a retry ladder -- the
     * failure was direction, not timing, so a second guess at the same
     * wrong shape would not have found it either. */
    pid_t self = getpid();
    pid_t my_sup = getppid();
    usleep(1500 * 1000);

    int found = 0;
    for (pid_t pid = self - 30; pid <= self + 30; pid++) {
        if (pid < 10) continue;      /* kernel threads, this house's logger */
        if (pid == 1) continue;      /* never touched, see the comment above */
        if (pid == self) continue;
        if (pid == my_sup) continue; /* this house's OWN supervisor */
        if (kill(pid, 0) < 0) continue; /* ESRCH (usually): not there */

        char tag[40];
        snprintf(tag, sizeof tag, "reach_exists_pid%d", pid);
        report(tag, 0);
        reach_battery(pid);
        found = 1;
    }
    if (!found)
        report("reach_nothing_found", -1);
}

int main(void)
{
    fs_probes();
    reach_probes();
    return 0;
}
