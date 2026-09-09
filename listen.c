#define _GNU_SOURCE
#include <unistd.h>
int main(void)
{
    char b[16];
    ssize_t n = read(3, b, sizeof b);
    const char *msg = (n >= 4 && b[0] == 'P') ? "listen got ping\n" : "listen miss\n";
    ssize_t w = write(1, msg, __builtin_strlen(msg));
    (void)w;
    usleep(200 * 1000);
    return n >= 4 ? 0 : 4;
}
