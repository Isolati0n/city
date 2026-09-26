/* Standalone probe for store.c's nw_store_put(): dedup, distinct-content
 * naming, and a forced concurrent double-write. Not the TCB; a test-time
 * program compiled and run by the suite. See docs/options/14-shared-
 * store.md.
 *
 * usage: store_probe <dir> <suffix> <content> [gate_path]
 * Prints one line: RESULT rc=<rc> hex=<hex>
 *
 * gate_path, if given, makes this process spin-poll for that path's
 * existence before calling nw_store_put() -- never inside store.c
 * itself, only in this fixture. A sleep-based delay was tried first and
 * rejected: two processes given the same usleep() duration wake up
 * within the scheduler's own jitter of each other, which measured
 * consistently wider than the microseconds nw_store_put() itself takes
 * for a small buffer, so one process's whole stat-write-close sequence
 * finished before the other's timer even elapsed -- no real overlap, in
 * every run tried. Two processes busy-polling the SAME path and
 * released by a single external write (the test creating that path)
 * unblock within the polling granularity of each other instead, which
 * is what actually forces both into nw_store_put() close enough
 * together to interleave. */
#include "../store.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    if (argc < 4 || argc > 5) {
        fprintf(stderr,
                "usage: store_probe <dir> <suffix> <content> [gate_path]\n");
        return 2;
    }
    const char *dir = argv[1];
    const char *suffix = argv[2];
    const char *content = argv[3];
    if (argc == 5) {
        const char *gate = argv[4];
        struct stat gs;
        while (stat(gate, &gs) != 0) {
            /* tight spin, deliberately no sleep in the loop */
        }
    }
    char hex[65];
    int rc = nw_store_put(dir, content, strlen(content), suffix, hex);
    printf("RESULT rc=%d hex=%s\n", rc, hex);
    return 0;
}
