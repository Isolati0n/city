/* Boot-glue only. Not TCB.
 *
 * 1. finit_module the NLS charsets vfat needs. Ubuntu 6.8 generic:
 *    VFAT=y, NLS_ISO8859_1=m. vfat's default iocharset is iso8859-1.
 *    Measured 2026-09-11: FAT-fs (vdb): IO charset iso8859-1 not
 *    found, then [dawn] FAIL mount /sysroot/efi errno=22. This load
 *    is why the module is here and not in dawn.c — dawn is TCB and
 *    does not name a distro Kconfig.
 * 2. Recursively mark / private so a shared root cannot sink dawn.
 *    dawn.c now does the same remount (the harness never did; a
 *    bootloader root is MS_SHARED). Doing it twice is cheap; the
 *    comment in dawn.c is the record of why.
 * 3. exec /dawn.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <sys/mount.h>
#include <sys/syscall.h>
#include <unistd.h>
#ifndef SYS_finit_module
#define SYS_finit_module 313
#endif
static void load(const char *path)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0)
        return;
    (void)syscall(SYS_finit_module, fd, "", 0);
    close(fd);
}
int main(void)
{
    load("/nls_iso8859_1.ko");
    load("/nls_utf8.ko");
    if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL) < 0) {
        (void)write(2, "[initrd] FAIL MS_PRIVATE /\n", 27);
        _exit(80);
    }
    char *const av[] = { "init", 0 };
    execv("/dawn", av);
    (void)write(2, "[initrd] FAIL exec /dawn\n", 25);
    _exit(80);
}
