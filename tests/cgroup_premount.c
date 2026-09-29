/* boot()'s own harness accommodation (tests/run.py), not a fixture and
 * not staged or shipped: mounts a fresh cgroup2 instance at
 * /sys/fs/cgroup, best-effort, then execs its own argv[1..]
 * unconditionally -- see boot()'s comment in tests/run.py for why
 * nw-sup needs this to exist at all in the lab.
 *
 * A single mount(2) plus execv(2), no shell, no external mount(8)
 * process, so it survives the harness's own artificially low
 * RLIMIT_NOFILE tests (fd-preflight and its neighbours) as cheaply as
 * nw-root's own dynamic loading already does. Wrapping nw-root in
 * `sh -c 'mount ...; exec "$0" "$@"'` was tried first and measured to
 * NOT survive fd-preflight's hard=8 case (SIGINT, not the test's own
 * controlled HALT) even though the `;` made the mount's own failure
 * non-fatal there too -- the shell's own startup was the fd cost, not
 * the mount, so the fix is not spawning a shell at all.
 *
 * ALSO clears nw-sup's own cgroup directory before the mount's target
 * is even reached by nw-root, and this is not tidiness. cgroup2 is one
 * real, GLOBAL hierarchy (confirmed empirically: mounting it twice
 * gives two views of the identical tree, not two independent ones), so
 * a lab "reboot" -- another `boot()` call, another pid-namespace
 * teardown -- reuses the SAME underlying cgroup2 state a previous lab
 * boot left behind, unlike a real reboot, which always starts a
 * genuinely empty in-kernel hierarchy with nothing persisted to disk.
 * house_cgroup_open_generation() in nwsup.c dies loudly on EEXIST for
 * exactly this reason -- reusing a directory it did not just create
 * could silently reuse one a PRIOR lab boot had already written
 * cgroup.kill to, reintroducing the bug that function's other comment
 * describes. Measured: test_orphans_across_restarts, which boots the
 * same unit name across four separate `boot()` calls, left
 * /sys/fs/cgroup/nw/orph.0 (and .1, .2, .3) behind after the pid
 * namespace was torn down mid-life rather than through nw-sup's own
 * exit path -- exactly the same "namespace teardown, not graceful
 * exit" gap `.claude/rules/runtime.md`'s Liveness and orphans-at-
 * shutdown sections already describe for houses, here reaching nw-sup's
 * own bookkeeping instead. So: remove every leaf this mount point's
 * "nw" directory already holds, then the directory itself -- nw-sup's
 * own cgroup_parent_setup() tolerates and expects a missing NW_CGROUP_DIR
 * (EEXIST-tolerant on the other side), so removing it here costs
 * nothing production code does not already handle. Best-effort
 * throughout: a leaf that fails to rmdir (a genuinely stuck test from
 * elsewhere, or another agent's suite run sharing this machine, per
 * this project's own accepted concurrency caveat for machine-root
 * paths) is left for nw-sup's own EEXIST die() to report by name,
 * which is a better diagnosis than this helper silently swallowing it.
 */
#include <dirent.h>
#include <stdio.h>
#include <string.h>
#include <sys/mount.h>
#include <unistd.h>

#define NW_CGROUP_DIR "/sys/fs/cgroup/nw"

static void clear_stale_cgroup_dir(void)
{
    DIR *d = opendir(NW_CGROUP_DIR);
    if (!d) return;
    struct dirent *e;
    while ((e = readdir(d)) != NULL) {
        if (strcmp(e->d_name, ".") == 0 || strcmp(e->d_name, "..") == 0)
            continue;
        char path[512];
        int n = snprintf(path, sizeof path, "%s/%s", NW_CGROUP_DIR, e->d_name);
        if (n > 0 && n < (int)sizeof path)
            rmdir(path); /* best-effort */
    }
    closedir(d);
    rmdir(NW_CGROUP_DIR); /* best-effort; nw-sup recreates it (EEXIST-tolerant) */
}

int main(int argc, char **argv)
{
    if (argc < 2) return 127;
    mount("cgroup2", "/sys/fs/cgroup", "cgroup2", 0, NULL);
    clear_stale_cgroup_dir();
    execv(argv[1], argv + 1);
    return 127;
}
