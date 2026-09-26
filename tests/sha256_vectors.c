/* Standalone known-answer test for sha256.c, against NIST's own SHA-256
 * test vectors, independent of anything that uses nw_sha256() -- see
 * docs/options/12-crash-evidence.md. Prints one hex digest per line;
 * tests/run.py compares against the published answers. Not in the TCB,
 * not a house: a build-time correctness check compiled and run by the
 * suite. */
#include "../sha256.h"
#include <stdio.h>
#include <string.h>

static void print_hex(const unsigned char *d)
{
    for (int i = 0; i < 32; i++) printf("%02x", d[i]);
    printf("\n");
}

int main(void)
{
    unsigned char out[32];

    nw_sha256("", 0, out);
    print_hex(out);

    nw_sha256("abc", 3, out);
    print_hex(out);

    const char *m56 =
        "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    nw_sha256(m56, strlen(m56), out);
    print_hex(out);

    static char million_a[1000000];
    memset(million_a, 'a', sizeof million_a);
    nw_sha256(million_a, sizeof million_a, out);
    print_hex(out);

    return 0;
}
