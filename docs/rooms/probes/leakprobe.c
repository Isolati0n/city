/* leakprobe.c -- the cost of setns-restart, measured.
 *
 * With restart as setns+exec, a house's isolation is built ONCE at first
 * inhabitation and reused for the life of the boot. Today's rebuild-every-
 * restart accidentally scrubs anything the first inhabitant left behind.
 * Removing that scrub is the design's stated risk, in its author's words:
 * "a leaked host bind in a saved mntns stays leaked after the guest is
 * dead... first inhabitation must be the most reviewed code in the system."
 *
 * So: build a namespace, have inhabitant #1 leave traces of four kinds,
 * kill it, then re-enter as inhabitant #2 and enumerate what is visible.
 *
 *   A  a mount #1 made after setup            -> does it persist?
 *   B  a file #1 wrote into the overlay       -> expected to persist (that
 *                                                is the layer working)
 *   C  a file #1 wrote to a tmpfs it mounted  -> persists iff the mount does
 *   D  an open descriptor #1 held             -> must NOT be inherited
 *
 * D is the one that would be a real leak: a descriptor is authority. A and
 * C are "the namespace is a durable object", which is the design working as
 * intended but is also what an unreviewed first inhabitation would leave.
 */
#define _GNU_SOURCE
#include <sched.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <signal.h>
#include <stdarg.h>
#include <dirent.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <sys/syscall.h>

#define R   "/nwleak"
#define PIN R "/pin"
#define MNT R "/mnt"

static void say(const char *fmt, ...)
{
    char b[512]; va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
    if (n > 0) (void)!write(1, b, (size_t)n);
}

static int count_mounts(void)
{
    FILE *f = fopen("/proc/mounts", "r");
    if (!f) return -1;
    int n = 0; char l[512];
    while (fgets(l, sizeof l, f)) n++;
    fclose(f);
    return n;
}

static int has_mount(const char *needle)
{
    FILE *f = fopen("/proc/mounts", "r");
    if (!f) return -1;
    int hit = 0; char l[512];
    while (fgets(l, sizeof l, f)) if (strstr(l, needle)) hit = 1;
    fclose(f);
    return hit;
}

static int count_fds(void)
{
    DIR *d = opendir("/proc/self/fd");
    if (!d) return -1;
    int n = 0; struct dirent *e;
    while ((e = readdir(d))) if (e->d_name[0] != '.') n++;
    closedir(d);
    return n;
}

int main(void)
{
    mkdir(R, 0755); mkdir(R "/lower", 0755); mkdir(R "/upper", 0755);
    mkdir(R "/work", 0755); mkdir(MNT, 0755); mkdir(R "/extra", 0755);
    int f = open(R "/lower/base", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (f >= 0) { (void)!write(f, "lower\n", 6); close(f); }
    f = open(PIN, O_WRONLY | O_CREAT, 0644); if (f >= 0) close(f);

    /* overlay needs the module; mkboot stages it now, but this probe may run
     * on an older image. Load it if present and ignore EEXIST. */
    int m = open("/nw/overlay.ko", O_RDONLY);
    if (m >= 0) { syscall(__NR_finit_module, m, "", 0); close(m); }

    int rdy[2], go[2];
    if (pipe(rdy) || pipe(go)) { say("[leak] pipe FAILED\n"); return 1; }

    pid_t kid = fork();
    if (kid == 0) {
        close(rdy[0]); close(go[1]);
        if (unshare(CLONE_NEWNS) < 0) { say("[leak] unshare FAILED %d\n", errno); _exit(1); }
        mount("none", "/", NULL, MS_REC | MS_PRIVATE, NULL);
        if (mount("overlay", MNT, "overlay", 0,
                  "lowerdir=" R "/lower,upperdir=" R "/upper,workdir=" R "/work") < 0) {
            say("[leak] overlay FAILED %d (%s)\n", errno, strerror(errno)); _exit(1);
        }
        say("[leak] #1 base mounts=%d fds=%d\n", count_mounts(), count_fds());

        /* A -- a mount made AFTER setup, the shape of an accidental leak */
        if (mount("tmpfs", R "/extra", "tmpfs", 0, "size=1M") < 0)
            say("[leak] #1 extra tmpfs FAILED %d\n", errno);
        else
            say("[leak] #1 mounted a tmpfs at " R "/extra\n");

        /* B -- overlay write, expected to persist */
        int a = open(MNT "/from1", O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (a >= 0) { (void)!write(a, "inhabitant one\n", 15); close(a); }

        /* C -- a file inside that tmpfs */
        int t = open(R "/extra/secret", O_WRONLY | O_CREAT | O_TRUNC, 0644);
        if (t >= 0) { (void)!write(t, "tmpfs payload\n", 14); close(t); }

        /* D -- hold a descriptor open and never close it */
        int held = open(R "/lower/base", O_RDONLY);
        say("[leak] #1 holding fd %d, mounts=%d fds=%d\n", held, count_mounts(), count_fds());

        (void)!write(rdy[1], "r", 1);
        char c; (void)!read(go[0], &c, 1);
        _exit(0);                     /* dies WITHOUT closing anything */
    }
    close(rdy[1]); close(go[0]);
    char c; if (read(rdy[0], &c, 1) != 1) { say("[leak] #1 never readied\n"); return 1; }

    char src[64]; snprintf(src, sizeof src, "/proc/%d/ns/mnt", (int)kid);
    if (mount(src, PIN, NULL, MS_BIND, NULL) < 0) {
        say("[leak] PIN FAILED %d\n", errno); kill(kid, SIGKILL); return 1;
    }
    (void)!write(go[1], "g", 1);
    kill(kid, SIGKILL);
    int st; waitpid(kid, &st, 0);
    say("[leak] #1 is dead; namespace pinned\n");

    int nfd = open(PIN, O_RDONLY);
    if (nfd < 0 || setns(nfd, CLONE_NEWNS) < 0) {
        say("[leak] SETNS FAILED %d\n", errno); return 1;
    }
    say("[leak] --- inhabitant #2 has re-entered ---\n");
    say("[leak] #2 sees mounts=%d fds=%d\n", count_mounts(), count_fds());

    say("[leak] A  tmpfs #1 mounted after setup : %s\n",
        has_mount(R "/extra") ? "STILL PRESENT" : "gone");

    char b[64] = {0};
    int r = open(MNT "/from1", O_RDONLY);
    if (r >= 0) { (void)!read(r, b, sizeof b - 1); close(r); b[strcspn(b,"\n")] = 0; }
    say("[leak] B  overlay file from #1         : %s\n", r >= 0 ? b : "gone");

    memset(b, 0, sizeof b);
    r = open(R "/extra/secret", O_RDONLY);
    if (r >= 0) { (void)!read(r, b, sizeof b - 1); close(r); b[strcspn(b,"\n")] = 0; }
    say("[leak] C  file inside that tmpfs       : %s\n", r >= 0 ? b : "gone");

    say("[leak] D  descriptors inherited from #1: %s (fds=%d, expect 0 leaked)\n",
        count_fds() <= 6 ? "none" : "CHECK", count_fds());
    say("[leak] done\n");
    return 0;
}
