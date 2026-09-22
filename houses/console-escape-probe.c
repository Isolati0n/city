/* unit-console-escape-probe: not a house anyone ships. Answers one
 * question directly, per syscall, with no shell and no /proc dependency
 * in between -- docs/options/10-console-house.md's operator-ordered
 * measurement of what lids=newns,landlock,newnet actually lets a house
 * do, with no seccomp lid at all.
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
 * in place of the wrapper, over the same ttyS1 channel.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <sched.h>
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/sysmacros.h>
#include <sys/types.h>
#include <unistd.h>

static void report(const char *what, long rc)
{
    int e = rc < 0 ? errno : 0;
    printf("PROBE %s rc=%ld errno=%d(%s)\n", what, rc, e,
           e ? strerror(e) : "-");
    fflush(stdout);
}

int main(void)
{
    report("mknod_char", mknod("/c", S_IFCHR | 0600, makedev(5, 1)));
    report("mknod_block", mknod("/b", S_IFBLK | 0600, makedev(7, 0)));
    report("mkdir_root", mkdir("/nwnewdir", 0755));
    /* /mnt is pre-baked into the brick as an empty directory, precisely
     * so a MAKE_DIR refusal (already established above) is not what
     * stops this probe from even trying the mount call itself. */
    report("mount_tmpfs", mount("tmpfs", "/mnt", "tmpfs", 0, NULL));
    report("mount_bind_root", mount("/", "/mnt", NULL, MS_BIND, NULL));
    report("unshare_newns", unshare(CLONE_NEWNS));
    report("pivot_root_noop", syscall(SYS_pivot_root, "/", "/"));
    return 0;
}
