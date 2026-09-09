#define _GNU_SOURCE
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>

#include "lids.h"

int nw_apply_house_seccomp(void)
{
    static const int allow[] = {
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
        __NR_rseq, __NR_futex
    };
    enum { NALLOW = sizeof allow / sizeof allow[0] };
    struct sock_filter f[2 + NALLOW + 2];
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
