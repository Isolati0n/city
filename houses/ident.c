/* unit-ident: a peer that names itself over its wire.
 *
 * Not PING. Each instance writes a distinct identifying string derived from
 * NW_UNIT, so a two-edge neighbour can say *which* peer arrived on a given
 * descriptor rather than merely that something was connected.
 * Exactly one wire is expected (fd 3). Not in the TCB.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

int main(void)
{
    const char *name = getenv("NW_UNIT");
    const char *kit = getenv("NW_WIRES");
    if (!name || !name[0]) name = "anon";
    if (!kit) kit = "?";

    char msg[64];
    int n = snprintf(msg, sizeof msg, "IAM=%s\n", name);
    if (n > 0) {
        ssize_t w = write(3, msg, (size_t)n);
        (void)w;
    }

    char line[128];
    n = snprintf(line, sizeof line, "ident sent IAM=%s on fd3 wires=%s\n", name, kit);
    if (n > 0) {
        ssize_t w = write(1, line, (size_t)n);
        (void)w;
    }
    usleep(250 * 1000);
    return 0;
}
