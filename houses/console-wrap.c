/* unit-console-wrap: rewires fd 0/1/2 onto a bound tty, then execs a shell.
 *
 * docs/options/10-console-house.md is the design this implements. It exists
 * because nwsup.c's house-exec call is `execv(path, av)` with
 * `av[0] == name` (the plan's declared house name) and no way to pass a
 * second argument -- so a house cannot simply name /bin/busybox as its
 * exec_path and expect busybox's argv[0]-based applet dispatch to find
 * "sh" unless the house happens to be named "sh". This wrapper chooses
 * its own argv when it execs busybox, so the plan's house name is free to
 * be anything.
 *
 * It also exists because the only way to give an interactive shell a
 * terminal on fd 0/1/2 without dup2/dup3 or setsid/ioctl -- none of which
 * are in lids.c's strict_allow[] -- is close() the fd, then open() the
 * same path again: the kernel hands back the lowest free descriptor,
 * which is exactly the one just closed. Three independent opens of one
 * character device, not three dups of one open file description; whether
 * that is fine under QEMU's 16550 emulation is the design note's one
 * unmeasured assumption this fixture exists to settle.
 *
 * Deliberately NOT calling setsid() or any ioctl: doing either would give
 * this process a controlling terminal, and since nothing in this tree
 * calls setsid() anywhere else, that terminal would attach to PID 1's own
 * session (every process here shares it), which is exactly the precondition
 * pid1.c's shutdown comment says must not happen without a matching fix
 * there. Going without a controlling terminal costs job control (no ^C,
 * no ^Z) and buys avoiding that TCB change entirely.
 *
 * Not TCB. Baked into the console house's brick like any other userland;
 * nw-sup does not know this file exists.
 */
#include <fcntl.h>
#include <unistd.h>

#ifndef TTY_PATH
#define TTY_PATH "/dev/ttyS1"
#endif

int main(void)
{
    for (int fd = 0; fd < 3; fd++) {
        close(fd);
        int got = open(TTY_PATH, O_RDWR);
        if (got != fd)
            _exit(97); /* did not land on the fd just closed */
    }
    char *argv[] = { "sh", (char *)0 };
    execv("/bin/busybox", argv);
    _exit(98); /* exec itself failed */
}
