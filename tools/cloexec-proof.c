/* tools/cloexec-proof.c -- standalone proof of the POSIX dup2(fd,fd)
 * no-op semantics that nwspawn.c's pack_kit() comment relies on.
 *
 * Not part of `make test`; run by hand:
 *   gcc -O2 -Wall -Wextra -o /tmp/cloexec-proof tools/cloexec-proof.c
 *   /tmp/cloexec-proof
 *
 * Proves two things nwspawn.c's pack_kit() comment (the block above
 * that function) asserts about its own placement loop:
 *
 *   1. dup2(fd, fd) is a true POSIX no-op -- it does NOT clear
 *      FD_CLOEXEC, even though a genuine dup2(a, b) with a != b always
 *      produces a target descriptor with FD_CLOEXEC clear. This is the
 *      gap historical bug 5 fell into: code that assumed dup2 always
 *      clears CLOEXEC on the target silently left it set whenever
 *      scratch == target.
 *
 *   2. pack_kit()'s own defense against that gap -- an UNCONDITIONAL
 *      clear_cloexec(target) call after every dup2(scratch, target),
 *      regardless of whether that dup2 was a same-fd no-op -- clears
 *      it regardless. This is what nwspawn.c's comment names as the
 *      actual mechanism, not the batch-before-place ordering.
 *
 * Not in the TCB -- a proof program, read alongside the code it proves
 * something about, not shipped in any binary.
 */
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>

static int clear_cloexec(int fd)
{
    int fl = fcntl(fd, F_GETFD);
    if (fl < 0) return -1;
    return fcntl(fd, F_SETFD, fl & ~FD_CLOEXEC);
}

int main(void)
{
    int fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (fd < 0) { perror("open"); return 1; }

    int before = fcntl(fd, F_GETFD);
    printf("before: FD_CLOEXEC=%d\n", !!(before & FD_CLOEXEC));

    /* The exact no-op case pack_kit() can hit: dup2(fd, fd). */
    int rc = dup2(fd, fd);
    int after_dup2 = fcntl(fd, F_GETFD);
    printf("dup2(fd,fd) returned %d; after dup2 alone: FD_CLOEXEC=%d "
           "(must still be SET -- dup2(fd,fd) is a true no-op)\n",
           rc, !!(after_dup2 & FD_CLOEXEC));
    if (!(after_dup2 & FD_CLOEXEC)) {
        fprintf(stderr, "FAIL: dup2(fd,fd) cleared FD_CLOEXEC on this "
                "kernel -- pack_kit()'s comment's premise does not hold "
                "here.\n");
        return 1;
    }

    /* pack_kit()'s actual next step: an unconditional clear_cloexec. */
    clear_cloexec(fd);
    int after_clear = fcntl(fd, F_GETFD);
    printf("after explicit clear_cloexec(fd): FD_CLOEXEC=%d "
           "(must now be CLEAR)\n", !!(after_clear & FD_CLOEXEC));
    if (after_clear & FD_CLOEXEC) {
        fprintf(stderr, "FAIL: clear_cloexec did not clear it.\n");
        return 1;
    }

    printf("PASS\n");
    return 0;
}
