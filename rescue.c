#define _GNU_SOURCE
#include <unistd.h>

int main(void)
{
    static const char msg[] =
        "[rescue] city map not used\n"
        "[rescue] this slot is outside the plan\n"
        "[rescue] pick yesterday or bake a new slot\n";
    ssize_t w = write(2, msg, sizeof msg - 1);
    (void)w;
    return 3;
}
