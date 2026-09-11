#include <unistd.h>

/* D18 fixture: exit after 1.2s so a 1s sliding window (the old
 * budget) would reset between deaths. Not a brick. */
int main(void)
{
    usleep(1200 * 1000);
    return 1;
}
