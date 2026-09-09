#define _GNU_SOURCE
#include <unistd.h>
int main(void)
{
    const char m[] = "PING\n";
    ssize_t w = write(3, m, sizeof m - 1);
    (void)w;
    const char ok[] = "talk sent\n";
    w = write(1, ok, sizeof ok - 1);
    (void)w;
    usleep(200 * 1000);
    return 0;
}
