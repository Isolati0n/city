/* unit-firehose: writes a large, fixed volume of output as fast as
 * possible, then exits. Exists for one control:
 * test_evidence_ring_buffer_is_bounded (docs/options/12-crash-evidence.md)
 * needs a house whose output volume would expose an accidentally-
 * unbounded per-unit buffer in PID 1's logger as growing RSS. A house
 * that writes a fixed, large amount and stops -- not a house that runs
 * forever -- so the test has a natural end rather than a hold-based one.
 *
 * FIREHOSE_BYTES is compiled in rather than read from an environment
 * variable: a house execs with no arguments and a clean environment
 * (invariant 5), so a runtime knob here would be a second, undeclared
 * channel into a supervised process for no reason this fixture needs --
 * the same argument houses/orphan.c's own comment makes for choosing two
 * binaries over one configurable one.
 *
 * Not in the TCB. */
#include <string.h>
#include <unistd.h>

/* 16,384x the 4096-byte ring cap -- decisive enough that an accidentally
 * unbounded buffer shows as tens of megabytes of extra RSS, without the
 * wall-clock cost of a literal gigabyte through a pipe the logger also
 * relays to the console and flushes to disk on every chunk. */
#ifndef FIREHOSE_BYTES
#define FIREHOSE_BYTES (64 * 1024 * 1024)
#endif

int main(void)
{
    char chunk[256];
    memset(chunk, 'x', sizeof chunk - 1);
    chunk[sizeof chunk - 1] = '\n';

    long long remaining = FIREHOSE_BYTES;
    while (remaining > 0) {
        size_t n = (remaining < (long long)sizeof chunk)
            ? (size_t)remaining : sizeof chunk;
        ssize_t w = write(1, chunk, n);
        if (w <= 0) break;
        remaining -= w;
    }
    return 7;
}
