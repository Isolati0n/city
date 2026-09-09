#include <unistd.h>
int main(void)
{
    const char m[] = "boom\n";
    ssize_t w = write(1, m, sizeof m - 1);
    (void)w;
    _exit(99);
}
