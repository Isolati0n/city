/* Fails on its first-ever invocation against a given writable layer,
 * succeeds on every one after -- a fixture for the crash-and-relaunch
 * control that needs a house whose failure is transient/environmental
 * rather than reproducible, so a relaunch must correctly report "did
 * not reproduce" rather than a false positive.
 *
 * No argument, no environment variable: the marker is a fixed,
 * absolute path, meant to be baked into a BRICK (see make_brick(...,
 * exe="unit-firstfail")) so that after the pivot "/" is that brick
 * plus its layer, and "/ff-marker" lands in the layer's own writable
 * upper -- isolated per layer id, and copyable exactly the way
 * question 1's layer copy already works. Whether "first run" or "later
 * run" depends on what the layer already holds, which the test
 * controls by choosing which layer id (and, for the relaunch tool
 * itself, whether the copy is fresh or as-it-was) each invocation
 * runs against -- the same mechanism this project already uses for
 * the exec path and unit name, not a new undeclared channel. */
#include <fcntl.h>
#include <unistd.h>

int main(void)
{
    int fd = open("/ff-marker", O_CREAT | O_EXCL, 0644);
    if (fd >= 0) {
        close(fd);
        return 17;
    }
    return 0;
}
