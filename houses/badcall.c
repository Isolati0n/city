#define _GNU_SOURCE
#include <sys/socket.h>
#include <unistd.h>
/* The seccomp test used to assert only that "badcall survived" was absent.
 * That passes if the house never ran at all -- for any reason, including the
 * filter never being applied and the exec failing. So say so first: the test
 * asserts the house started AND that the call did not survive, which
 * together mean the filter did the killing. */
int main(void)
{
    const char started[] = "badcall started\n";
    ssize_t w0 = write(1, started, sizeof started - 1);
    (void)w0;

    int s = socket(AF_INET, SOCK_STREAM, 0);
    const char m[] = "badcall survived\n";
    if (s >= 0) {
        ssize_t w = write(1, m, sizeof m - 1);
        (void)w;
        return 0;
    }
    return 1;
}
