#define _GNU_SOURCE
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>

#include "blob.h"
#include "lids.h"

/* STRICT: the ordinary application filter. */
static const int strict_allow[] = {
        __NR_read, __NR_write, __NR_close, __NR_fcntl,
        __NR_getsockopt, __NR_setsockopt,
        __NR_nanosleep, __NR_clock_nanosleep, __NR_clock_gettime,
        __NR_exit, __NR_exit_group, __NR_rt_sigreturn,
        __NR_brk, __NR_mmap, __NR_mprotect, __NR_munmap,
        __NR_execve, __NR_execveat,
        __NR_openat, __NR_newfstatat, __NR_fstat, __NR_stat,
        __NR_pread64, __NR_access, __NR_getdents64,
        __NR_arch_prctl, __NR_set_tid_address, __NR_set_robust_list,
        __NR_prlimit64, __NR_getpid, __NR_getcwd,
        __NR_rseq, __NR_futex,
        /* glibc's *static* startup path calls both before main: readlinkat
         * on /proc/self/exe, and getrandom for the stack guard and malloc.
         * A brick carries its own libraries and its binaries are usually
         * linked static, so without these "brick + seccomp" kills every
         * house before it runs a line -- found by running unit-brick, not by
         * reading it. Both are read-only and neither grants a house anything
         * it could not already do: readlinkat resolves a path it can already
         * stat, getrandom reads entropy. Added 2026-09-10. */
        __NR_readlinkat, __NR_getrandom
};

/* BUILD adds to STRICT rather than replacing it, so the two cannot drift:
 * a syscall added to the application filter is automatically in the build
 * one. These are what a compiler and a build driver need and STRICT has no
 * business granting -- process creation, waiting, and the filesystem
 * mutation a toolchain does. Without them any compiler dies instantly, which
 * is the whole reason a second profile exists. */
static const int build_extra[] = {
    __NR_clone, __NR_clone3, __NR_unshare, __NR_wait4, __NR_waitid,
    __NR_mount, __NR_umount2,
    __NR_pipe2, __NR_dup, __NR_dup3, __NR_setpgid, __NR_getpgid,
    __NR_kill, __NR_rt_sigaction, __NR_rt_sigprocmask, __NR_rt_sigtimedwait,
    __NR_unlinkat, __NR_renameat2, __NR_mkdirat, __NR_symlinkat,
    __NR_chdir, __NR_fchdir, __NR_lseek, __NR_ftruncate, __NR_fsync,
    __NR_fchmodat, __NR_faccessat2, __NR_umask,
    __NR_getuid, __NR_geteuid, __NR_getgid, __NR_getegid, __NR_getppid,
    __NR_uname, __NR_sysinfo, __NR_madvise, __NR_mremap, __NR_statx,
    __NR_sched_getaffinity, __NR_sched_yield
};

int nw_apply_house_seccomp(unsigned profile)
{
    enum { NSTRICT = sizeof strict_allow / sizeof strict_allow[0],
           NEXTRA  = sizeof build_extra / sizeof build_extra[0] };
    int allow[NSTRICT + NEXTRA];
    unsigned nallow = 0;
    for (unsigned i = 0; i < NSTRICT; i++) allow[nallow++] = strict_allow[i];
    if (profile == NW_PROF_BUILD)
        for (unsigned i = 0; i < NEXTRA; i++) allow[nallow++] = build_extra[i];

    const unsigned NALLOW = nallow;
    struct sock_filter f[2 + NSTRICT + NEXTRA + 2];
    unsigned n = 0;
    f[n++] = (struct sock_filter)BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
                                          offsetof(struct seccomp_data, nr));
    for (unsigned i = 0; i < NALLOW; i++)
        f[n++] = (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
                                              (unsigned)allow[i], NALLOW - i, 0);
    f[n++] = (struct sock_filter)BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS);
    f[n++] = (struct sock_filter)BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW);
    struct sock_fprog prog = { .len = (unsigned short)n, .filter = f };
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0) return -1;
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog) < 0) return -1;
    return 0;
}
