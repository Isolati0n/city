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
#include <errno.h>
#include <fcntl.h>
#include <sys/mount.h>
#include <sys/syscall.h>
#include <unistd.h>
#ifndef SYS_finit_module
#define SYS_finit_module 313
#endif
/* ONE WRITER, and it consumes write()'s result rather than casting it to
 * void -- `(void)write(...)` does NOT suppress warn_unused_result. The two
 * older calls in main() used that form and warned; they go through here
 * now, which also retires their hand-counted lengths (27 and 25, each a
 * literal that had to match the string beside it with nothing checking
 * that it did). With those gone the file is clean under -Wall -Wextra
 * -Werror, so the Makefile gate can be -Werror -- the difference between
 * a gate that bites and one that prints two warnings every run until
 * nobody reads it. */
static void emit(const char *s)
{
    const char *p = s;
    while (*p)
        p++;
    ssize_t w = write(2, s, (size_t)(p - s));
    (void)w;
}

/* REPORT A LOAD THAT WAS TRIED AND REFUSED, and stay silent about one that
 * was never staged. The two are different facts and this told neither.
 *
 * An absent file is legitimate: a kernel with the filesystem built in
 * stages nothing, and mkboot.sh cannot tell that from a broken tree by
 * looking -- which is why NW_NLS_BUILTIN and NW_FS_BUILTIN exist. So
 * open() failing stays silent.
 *
 * A file that IS here and does not load is an error, and it was invisible:
 * finit_module's return was cast away, so `erofs: Unknown symbol crc32c
 * (err -2)` reached the console only because the KERNEL printed it. Had it
 * not, the symptom would have been ENODEV at the brick mount with nothing
 * naming the cause -- identical to staging no module at all. With three
 * modules and an ordering dependency between two of them, a silent refusal
 * IS the failure.
 *
 * CLAUDE.md, the corollary added 2026-09-13: never discard a stream you are
 * about to draw a conclusion from, and check a positive artifact rather
 * than reading meaning into a silence. The positive artifact here is that
 * the file opened; the conclusion is whether the kernel took it. */
static void load(const char *path)
{
    int fd = open(path, O_RDONLY);
    if (fd < 0)
        return;                 /* not staged: built in, or not wanted */
    if (syscall(SYS_finit_module, fd, "", 0) < 0) {
        int e = errno;
        char n[12];
        int i = 0;
        if (e < 0)
            e = -e;
        do {
            n[i++] = (char)('0' + e % 10);
            e /= 10;
        } while (e > 0 && i < (int)sizeof n - 1);
        char rev[sizeof n];
        int j = 0;
        while (i-- > 0)
            rev[j++] = n[i];
        rev[j] = '\0';
        emit("[initrd] FAIL load ");
        emit(path);
        emit(" errno=");
        emit(rev);
        emit("\n");
    }
    close(fd);
}
int main(void)
{
    load("/nls_iso8859_1.ko");
    load("/nls_utf8.ko");
    /* Brick houses mount erofs and overlay AFTER the pivot, in nw-sup. A
     * module loaded here is kernel-global and survives the pivot, so this
     * is the right place even though the mounts are not. Both are =m on
     * Ubuntu 6.8 generic; without them mount() returns ENODEV and a brick
     * house cannot start. Measured 2026-09-14. */
    load("/overlay.ko");
    load("/libcrc32c.ko");   /* erofs links against crc32c; must precede it */
    load("/erofs.ko");
    if (mount(NULL, "/", NULL, MS_REC | MS_PRIVATE, NULL) < 0) {
        emit("[initrd] FAIL MS_PRIVATE /\n");
        _exit(80);
    }
    char *const av[] = { "init", 0 };
    execv("/dawn", av);
    emit("[initrd] FAIL exec /dawn\n");
    _exit(80);
}
