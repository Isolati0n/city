/* memfdprobe.c -- the experiment the proposal says decides it.
 *
 *   "If this boots a shell, the mechanism exists. If mount refuses the
 *    memfd path, the design is dead and the current design is the right
 *    answer."
 *
 * The proposal: a house is a sealed memfd_create() file holding the baked
 * image. The init holds one sealed fd per distinct image and mounts it as
 * the new root inside a fresh mount namespace. There is no pre-pivot
 * window because the house's root was never the machine root.
 *
 * Four things are tested, in the order they would have to work:
 *
 *  1  memfd_create + write an image + F_ADD_SEALS        (does sealing hold)
 *  2  is the seal real -- can anything still write it
 *  3  mount("/proc/self/fd/N", dir, NULL, MS_BIND)       (the proposal's step 5)
 *  4  if that fails: a loop device backed by the memfd, then mount erofs
 *     -- which is the same mechanism with a loop device reintroduced
 *
 * Note before running: the proposal says to put a CPIO archive in the
 * memfd. A cpio is an archive, not a filesystem, and nothing mounts one --
 * the kernel only unpacks cpio for the initramfs, at boot, from a special
 * path. So the test uses a real erofs image instead, which is the most
 * favourable substitution available and keeps the idea intact.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <stdarg.h>
#include <linux/loop.h>
#include <linux/memfd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>

#ifndef F_ADD_SEALS
#define F_ADD_SEALS 1033
#define F_GET_SEALS 1034
#define F_SEAL_SEAL   0x0001
#define F_SEAL_SHRINK 0x0002
#define F_SEAL_GROW   0x0004
#define F_SEAL_WRITE  0x0008
#endif
#ifndef F_SEAL_FUTURE_WRITE
#define F_SEAL_FUTURE_WRITE 0x0010
#endif

static void say(const char *fmt, ...)
{
    char b[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}

int main(void)
{
    mkdir("/nwmem", 0755);
    const char *img = "/nw/bricks/"
        "df96ed908baee8660abe06445b8dafaa4cefb72a9d274ec520e51370563eab3a.img";

    /* 1 -- memfd, fill with a real erofs image, seal */
    int fd = (int)syscall(__NR_memfd_create, "house", MFD_ALLOW_SEALING);
    if (fd < 0) { say("[m] memfd_create FAILED errno=%d\n", errno); return 1; }
    say("[m] 1 memfd_create ok, fd=%d\n", fd);

    int src = open(img, O_RDONLY);
    if (src < 0) { say("[m] cannot open brick image errno=%d\n", errno); return 1; }
    char buf[65536]; ssize_t n, total = 0;
    while ((n = read(src, buf, sizeof buf)) > 0) {
        if (write(fd, buf, n) != n) { say("[m] write into memfd FAILED\n"); return 1; }
        total += n;
    }
    close(src);
    say("[m] 1 wrote %ld bytes of erofs into the memfd (now resident in RAM)\n", (long)total);

    int seals = F_SEAL_WRITE | F_SEAL_FUTURE_WRITE | F_SEAL_SHRINK | F_SEAL_GROW;
    if (fcntl(fd, F_ADD_SEALS, seals) < 0) {
        say("[m] 1 F_ADD_SEALS FAILED errno=%d (%s)\n", errno, strerror(errno));
    } else {
        say("[m] 1 sealed: WRITE|FUTURE_WRITE|SHRINK|GROW\n");
    }
    int got = fcntl(fd, F_GET_SEALS);
    say("[m] 1 F_GET_SEALS reports 0x%04x\n", got);

    /* 2 -- is the seal real */
    char x = 'X';
    ssize_t w = pwrite(fd, &x, 1, 0);
    say("[m] 2 write to the sealed memfd: %s (errno=%d)\n",
        w < 0 ? "REFUSED -- seal holds" : "ALLOWED -- seal is not holding", w < 0 ? errno : 0);
    void *mp = mmap(NULL, 4096, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    say("[m] 2 mmap it writable:        %s (errno=%d)\n",
        mp == MAP_FAILED ? "REFUSED -- seal holds" : "ALLOWED -- seal is not holding",
        mp == MAP_FAILED ? errno : 0);
    if (mp != MAP_FAILED) munmap(mp, 4096);

    /* 3 -- the proposal's step 5, verbatim in spirit */
    char p[64]; snprintf(p, sizeof p, "/proc/self/fd/%d", fd);
    mkdir("/nwmem/newroot", 0755);
    int e = mount(p, "/nwmem/newroot", NULL, MS_BIND, NULL);
    say("[m] 3 mount(\"%s\", dir, MS_BIND): %s (errno=%d %s)\n", p,
        e == 0 ? "returned 0" : "FAILED", e == 0 ? 0 : errno,
        e == 0 ? "" : strerror(errno));
    if (e == 0) {
        struct stat st;
        int has = stat("/nwmem/newroot/marker", &st) == 0;
        say("[m] 3 is it a FILESYSTEM? marker inside: %s\n",
            has ? "PRESENT -- it is a filesystem" : "ABSENT -- bound the FILE, not a filesystem");
        umount("/nwmem/newroot");
    }

    /* 4 -- the fallback: loop device backed by the memfd */
    int c = open("/dev/loop-control", O_RDWR);
    int idx = c >= 0 ? ioctl(c, LOOP_CTL_GET_FREE) : -1;
    if (c >= 0) close(c);
    if (idx < 0) { say("[m] 4 no free loop device\n"); return 0; }
    char dev[32]; snprintf(dev, sizeof dev, "/dev/loop%d", idx);
    int lf = open(dev, O_RDONLY | O_CLOEXEC);
    if (lf < 0) { say("[m] 4 open %s FAILED %d\n", dev, errno); return 0; }
    if (ioctl(lf, LOOP_SET_FD, fd) < 0) {
        say("[m] 4 LOOP_SET_FD from the memfd FAILED errno=%d (%s)\n", errno, strerror(errno));
        close(lf); return 0;
    }
    close(lf);
    say("[m] 4 loop device %s backed by the sealed memfd: ok\n", dev);
    mkdir("/nwmem/loopmnt", 0755);
    if (mount(dev, "/nwmem/loopmnt", "erofs", MS_RDONLY | MS_NODEV, NULL) < 0) {
        say("[m] 4 mount erofs from the memfd loop FAILED errno=%d (%s)\n",
            errno, strerror(errno));
        return 0;
    }
    int r = open("/nwmem/loopmnt/marker", O_RDONLY);
    char mb[64] = {0};
    if (r >= 0) { (void)!read(r, mb, sizeof mb - 1); close(r); mb[strcspn(mb,"\n")] = 0; }
    say("[m] 4 mounted from RAM; content: '%s'\n", r >= 0 ? mb : "MISSING");
    say("[m] VERDICT: a sealed memfd %s be a house root directly; via a loop device it %s\n",
        "cannot", r >= 0 ? "can" : "cannot");
    umount("/nwmem/loopmnt");
    return 0;
}
