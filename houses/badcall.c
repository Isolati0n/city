#define _GNU_SOURCE
#include <sys/socket.h>
#include <unistd.h>
int main(void)
{
    int s = socket(AF_INET, SOCK_STREAM, 0);
    const char m[] = "badcall survived\n";
    if (s >= 0) {
        ssize_t w = write(1, m, sizeof m - 1);
        (void)w;
        return 0;
    }
    return 1;
}
