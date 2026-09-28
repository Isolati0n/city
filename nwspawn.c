/* nw-spawn — boot-time unit spawner.
 *
 * Forks one supervisor per unit, double-forked so PID 1 adopts the houses,
 * reports their pids, and EXITS.
 *
 * It exists only during boot. PID 1 has no respawn path (see reap_all in
 * pid1.c: a house exit is recorded but never re-execed; nothing a house
 * does halts the city), and restart budgets live in nw-sup, one authority per
 * unit. So spawning happens exactly once per unit and a process whose
 * lifetime is exactly boot matches that need.
 *
 * Its predecessor, the electrician, stayed alive and inert because it held
 * the only copy of the connection graph; its death mid-life was unrecoverable
 * and PID 1 halted on it. Edges are back (docs/options/17-edges.md), and
 * this process does NOT inherit that mid-life: a socketpair's two ends need
 * no live process holding a third reference once each end has been handed
 * to its owning house via fork() inheritance, so every edge's socketpair is
 * created before the per-unit loop, closed here once every unit has forked,
 * and this process still exits normally after the loop, exactly as it did
 * with no edges at all. PID 1 waits for exit 0 rather than watching for
 * death, unchanged.
 *
 * TCB.
 */
#define _GNU_SOURCE
#include "blob.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

static void die(const char *s)
{
    char b[160];
    int n = snprintf(b, sizeof b, "[nw-spawn] FAIL %s errno=%d\n", s, errno);
    if (n > 0) { ssize_t r = write(2, b, (size_t)n); (void)r; }
    _exit(71);
}

static void say(const char *s)
{
    char b[160];
    int n = snprintf(b, sizeof b, "[nw-spawn] %s\n", s);
    if (n > 0) { ssize_t r = write(2, b, (size_t)n); (void)r; }
}

static int kept(int fd, const int *keep, int n)
{
    for (int i = 0; i < n; i++)
        if (keep[i] == fd) return 1;
    return 0;
}

/* No compile-time fd numbers. Sweep whatever the kernel assigned. */
static void close_others(const int *keep, int nkeep)
{
    int dfd = open("/proc/self/fd", O_RDONLY | O_DIRECTORY);
    if (dfd < 0) {
        for (int fd = 0; fd < NW_FD_SWEEP; fd++)
            if (!kept(fd, keep, nkeep)) close(fd);
        return;
    }
    DIR *d = fdopendir(dfd);
    if (!d) {
        close(dfd);
        die("fdopendir");
    }
    int dirfd_n = dirfd(d);
    int doomed[NW_FD_SWEEP];
    int nd = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        int fd = atoi(e->d_name);
        if (fd == dirfd_n) continue;
        if (!kept(fd, keep, nkeep)) {
            /* Never drop one on the floor. The old bound was a bare literal
             * and silently stopped collecting past it, which would have left
             * descriptors open in a house with no error anywhere. */
            if (nd >= NW_FD_SWEEP) { closedir(d); die("fd sweep overflow"); }
            doomed[nd++] = fd;
        }
    }
    closedir(d);
    for (int i = 0; i < nd; i++)
        close(doomed[i]);
}

static int clear_cloexec(int fd)
{
    int fl = fcntl(fd, F_GETFD);
    if (fl < 0) return -1;
    return fcntl(fd, F_SETFD, fl & ~FD_CLOEXEC);
}

/* A unit's descriptors: /dev/null on 0, its own log pipe on 1 and 2, and
 * (docs/options/17-edges.md) every wire this unit was declared into,
 * contiguous from 3. No BASE + i arithmetic: every slot is either fixed
 * (0/1/2) or assigned by counting up from a scratch allocation the
 * kernel picked, never computed from a unit's table position -- the
 * class behind bugs 5, 9 and 13.
 *
 * INVARIANT 2's DISCIPLINE, NAMED SO A FUTURE EDIT DOES NOT VIOLATE IT:
 * every scratch descriptor this unit needs -- the log pipe AND every
 * wire -- is collected via F_DUPFD_CLOEXEC BEFORE any of them is
 * dup2-ed to a final slot. Interleaving allocate-then-place risks a
 * later scratch fd landing on a target slot an earlier wire already
 * claimed; batching first means every scratch fd is guaranteed fresh
 * and distinct from 0/1/2 and from each other's targets before any
 * placement happens at all, which is what removes the ordering
 * dependency rather than merely getting the order right this time.
 *
 * THREE SEPARATE COLLISION AXES, not one, and an earlier version of
 * this comment covered two of them and claimed that was all of them --
 * `tcb-review` built an isolated harness using this function's own
 * primitives and reproduced the third live, which is why this says
 * three now rather than two.
 *
 * AXIS 1: dup2(scratch, target) where scratch == target (the trivial
 * case, when a wire's own scratch allocation happens to land exactly
 * on its own target). dup2(fd,fd) is a true POSIX no-op and leaves
 * FD_CLOEXEC SET -- verified directly, `dup2(fd,fd)` returns fd
 * unchanged and a following F_GETFD still shows the bit. This function
 * closes it by calling clear_cloexec(target) UNCONDITIONALLY after
 * every dup2(scratch, target), never skipping it when scratch==target.
 * Bug 5's mechanism -- "dup2(fd,fd) doesn't clear CLOEXEC" -- is
 * exactly this gap, and the unconditional call is the attic's own fix
 * for it (docs/options/17-edges.md's account).
 *
 * AXIS 2: a later wire's SCRATCH fd landing on an EARLIER wire's
 * already-placed TARGET. Impossible regardless of batching order:
 * F_DUPFD_CLOEXEC never returns an fd that is currently open, and an
 * earlier target is occupied (by that wire's own placement) by the
 * time a later wire's scratch is requested.
 *
 * AXIS 3 (`tcb-review`'s finding): a later wire's own SOURCE fd --
 * wire_fd[j], an INPUT to this function, not something it allocated --
 * numerically equal to an EARLIER wire's already-placed target. Axis 2
 * says nothing about this: wire_fd[j] was never obtained through
 * F_DUPFD_CLOEXEC inside this function, so that guarantee does not
 * cover it. dup2(scratch, target) at an earlier k silently closes
 * whatever sat at `target`; if wire_fd[j] (j > k, not yet processed)
 * happened to equal that target, its data becomes permanently
 * unreachable with no error anywhere -- reproduced live in an isolated
 * harness built around wire_fd={20,21,3} (n_wires=3, target(0)=3):
 * interleaved allocate-then-place lost wire 2's peer silently; the
 * real batched code, given the identical adversarial input, did not.
 * That is a genuine defect in this function taken IN ISOLATION.
 *
 * WHETHER AXIS 3 IS REACHABLE THROUGH NW-SPAWN'S ACTUAL CALLER is a
 * separate question from whether pack_kit() alone is vulnerable to it,
 * and `tcb-review` flagged not having settled that question as a
 * HYPOTHESIS rather than asserting either way. It does not reach here.
 * main() creates every edge's socketpair() BEFORE the per-unit loop
 * begins, in edge-table order, with nothing closed between successive
 * calls -- so edge_fd values are STRICTLY INCREASING with edge-table
 * index k (the kernel always returns the lowest free descriptors, and
 * nothing frees a lower one in between). A unit's own wire_fd[] is
 * built by scanning k = 0..n_edges-1 and appending at most one value
 * per k for edges touching this unit, so wire_fd[] inherits that same
 * strict increase: wire_fd[0] < wire_fd[1] < ... for any legal plan,
 * with wire_fd[0] >= 3 always (0/1/2 are already occupied when the
 * first edge socketpair is created). For axis 3 to fire, some
 * wire_fd[j] (j>0) would have to equal an earlier target 3+k (k<j) --
 * algebraically, wire_fd[j] - j >= wire_fd[0] >= 3 for a strictly
 * increasing integer sequence, while firing requires wire_fd[j] - j <=
 * 2. Those cannot both hold, for any k, j or edge ordering: the isolated
 * harness's wire_fd={20,21,3} could never arise from main()'s own edge
 * creation, because 3 would have to be wire_fd[0], not wire_fd[2].
 * `tools/fdorder-sweep.py`'s star topology (one hub wired to every
 * leaf) already exercises the most adversarial real shape for this --
 * the hub's wire_fd[] spans the FULL edge table, k=0..n_edges-1, the
 * widest range any single unit's wires can cover -- and passed clean
 * at up to 30 wires under both this code and the interleaved mutation,
 * consistent with the algebra above rather than contradicting it.
 *
 * THIS IS A CLAIM ABOUT TWO FUNCTIONS AGREEING, not a self-contained
 * property of pack_kit(): the invariant that rules axis 3 out --
 * wire_fd[] strictly increasing, floor 3 -- is established by main()'s
 * edge-creation loop, not by anything in this function. If that loop's
 * structure ever changes (edges created per-unit instead of all
 * upfront, or anything closes an fd between socketpair() calls), this
 * paragraph's reasoning needs re-checking against the new code, not
 * just re-trusting. Invariant 2's batch-before-place discipline still
 * stands on its own for axis 2 and is worth keeping regardless -- axis
 * 3 is why it is ALSO the right shape to keep pack_kit() self-contained
 * rather than relying on a caller invariant it does not itself state.
 *
 * `tools/fdorder-sweep.py`'s filler-descriptor technique (below) tests
 * axis 2 directly and does not construct axis 3 on its own -- a filler
 * is disposable, never a sibling wire's real source -- so its clean
 * 783-configuration result is evidence about axis 2, not axis 3; axis
 * 3's evidence is the algebra above plus the star topology's incidental
 * coverage. `tools/cloexec-proof.c` proves axis 1's premise directly. */
static int pack_kit(int log_w, const int *wire_fd, int n_wires)
{
    int nullfd = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (nullfd < 0) return -1;
    int logn = fcntl(log_w, F_DUPFD_CLOEXEC, 3);
    if (logn < 0) return -1;

    /* ALL scratch allocation first, no placement yet. */
    int wire_scratch[NW_MAX_EDGES];
    for (int k = 0; k < n_wires; k++) {
        wire_scratch[k] = fcntl(wire_fd[k], F_DUPFD_CLOEXEC, 3);
        if (wire_scratch[k] < 0) return -1;
    }

    int keep[2 + NW_MAX_EDGES];
    keep[0] = nullfd;
    keep[1] = logn;
    for (int k = 0; k < n_wires; k++) keep[2 + k] = wire_scratch[k];
    close_others(keep, 2 + n_wires);

    if (dup2(nullfd, 0) < 0) return -1;
    if (dup2(logn, 1) < 0) return -1;
    if (dup2(logn, 2) < 0) return -1;
    if (nullfd > 2) close(nullfd);
    if (logn > 2) close(logn);
    if (clear_cloexec(0) < 0 || clear_cloexec(1) < 0 || clear_cloexec(2) < 0)
        return -1;

    /* Placement, now that every scratch fd is already allocated. Target
     * slots are 3, 4, 5... contiguous, in the order this unit's wires
     * were collected -- the order does not carry meaning (edges are
     * unordered connectivity, not typed ports), it only has to be
     * stable so NW_WIRE_<slot> names the same fd it is set beside. */
    for (int k = 0; k < n_wires; k++) {
        int target = 3 + k;
        if (dup2(wire_scratch[k], target) < 0) return -1;
        if (wire_scratch[k] != target) close(wire_scratch[k]);
        if (clear_cloexec(target) < 0) return -1;
    }
    return 0;
}

int main(int argc, char **argv)
{
    /* argv: blob report_fd nlogs logw...   env: NW_SUP */
    if (argc < 4) die("argv");
    const char *blob_path = argv[1];
    int report_fd = atoi(argv[2]);
    int nlogs = atoi(argv[3]);
    if (nlogs < 1 || nlogs > NW_MAX_UNITS) die("nlogs");
    if (argc != 4 + nlogs) die("log argc");

    int logw[NW_MAX_UNITS];
    for (int i = 0; i < nlogs; i++) {
        logw[i] = atoi(argv[4 + i]);
        if (logw[i] < 0) die("log fd");
    }

    const char *sup = getenv("NW_SUP");
    if (!sup || !sup[0]) die("NW_SUP");

    sigset_t mask;
    sigfillset(&mask);
    sigprocmask(SIG_BLOCK, &mask, NULL);

    int bfd = open(blob_path, O_RDONLY);
    if (bfd < 0) die("open blob");
    /* NW_BLOB_BUF, and the size is checked against NW_BLOB_MAX rather than
     * against sizeof blob. The recheck exists to catch a file that is not
     * the one PID 1 read; with a buffer of exactly NW_BLOB_MAX, a maximal
     * legal blob with arbitrary bytes appended truncated to precisely the
     * length nw_check expects and passed. The sentinel byte makes a full
     * read proof of an oversized file. tcb-review, reproduced. */
    static unsigned char blob[NW_BLOB_BUF];
    ssize_t n = read(bfd, blob, sizeof blob);
    close(bfd);
    if (n <= 0) die("read blob");
    if ((uintmax_t)n > (uintmax_t)NW_BLOB_MAX) die("blob size");
    if (nw_check(blob, (uint32_t)n) != NW_OK) die("blob recheck");

    const struct nw_hdr *h = nw_hdr(blob);
    const struct nw_unit *u = nw_units(blob);
    const struct nw_bind *bd = nw_binds(blob);
    const struct nw_edge *ed = nw_edges(blob);
    if ((int)h->n_units != nlogs) die("log/unit mismatch");

    /* docs/options/17-edges.md. Every socketpair created BEFORE the
     * per-unit loop begins, mirroring the attic's own "create every pair
     * first" -- the single most important commitment this design makes:
     * nw-spawn's predecessor stayed alive holding the only copy of the
     * connection graph, and its mid-life death was unrecoverable. A
     * socketpair's two ends need no live process holding a third
     * reference once each end has been handed to its owning house via
     * fork() inheritance, so nw-spawn closes its own copies once every
     * unit has forked (below) and exits normally after the loop, exactly
     * as it always has -- no mid-life, no graph held open. */
    int edge_fd[NW_MAX_EDGES][2];
    for (uint32_t k = 0; k < h->n_edges; k++)
        if (socketpair(AF_UNIX, SOCK_STREAM, 0, edge_fd[k]) < 0)
            die("edge socketpair");

    /* The wiring census, ground truth read from the same table the
     * assignment below is computed from -- not a second, separately
     * maintained list. Every edge must contribute to exactly two units'
     * counts; bug 5's symptom was zero. */
    uint32_t wired_total = 0;

    pid_t pids[NW_MAX_UNITS];
    for (uint32_t i = 0; i < h->n_units; i++) {
        /* This unit's own wires, resolved from the edge table nw_check
         * already bounds-checked (a < n_units, b < n_units, a != b) --
         * so every edge is seen by exactly one of the two units it
         * names, on two different iterations of this same loop. */
        int my_wire_fd[NW_MAX_EDGES];
        uint32_t my_wire_peer[NW_MAX_EDGES];
        int n_wires = 0;
        for (uint32_t k = 0; k < h->n_edges; k++) {
            if (ed[k].a == i) {
                my_wire_fd[n_wires] = edge_fd[k][0];
                my_wire_peer[n_wires] = ed[k].b;
                n_wires++;
            } else if (ed[k].b == i) {
                my_wire_fd[n_wires] = edge_fd[k][1];
                my_wire_peer[n_wires] = ed[k].a;
                n_wires++;
            }
        }
        wired_total += (uint32_t)n_wires;

        int pp[2];
        if (pipe2(pp, O_CLOEXEC) < 0) die("pid pipe");
        pid_t mid = fork();
        if (mid < 0) die("fork");
        if (mid == 0) {
            close(pp[0]);
            pid_t house = fork();
            if (house < 0) die("fork house");
            if (house != 0) {
                if (write(pp[1], &house, sizeof house) != (ssize_t)sizeof house)
                    die("write house pid");
                _exit(0);
            }
            close(pp[1]);
            if (pack_kit(logw[i], my_wire_fd, n_wires) < 0) die("pack kit");
            for (int k = 0; k < n_wires; k++) {
                char envk[24];
                snprintf(envk, sizeof envk, "NW_WIRE_%d", 3 + k);
                setenv(envk, u[my_wire_peer[k]].name, 1);
            }
            char lbuf[8], bbuf[8], kbuf[8], nbuf[8], sxbuf[8];
            snprintf(lbuf, sizeof lbuf, "%u", (unsigned)u[i].lids);
            snprintf(bbuf, sizeof bbuf, "%u", (unsigned)u[i].budget);
            snprintf(kbuf, sizeof kbuf, "%u", (unsigned)u[i].kind);
            /* docs/options/15-per-house-scheduling.md. Decimal, same
             * "0 = unset" convention every other field here uses. */
            snprintf(sxbuf, sizeof sxbuf, "%u", (unsigned)u[i].sched_ext);
            setenv("NW_UNIT", u[i].name, 1);
            setenv("NW_HOUSE", u[i].name, 1);
            setenv("NW_LIDS", lbuf, 1);
            setenv("NW_BUDGET", bbuf, 1);
            setenv("NW_KIND", kbuf, 1);
            setenv("NW_SCHED_EXT", sxbuf, 1);
            /* The brick and the paths bound into it. Names, not descriptors:
             * nw-sup mounts them itself and the house opens what it needs.
             * The init still provisions exactly /dev/null and a log pipe
             * (invariant 5). */
            /* HEX, because the field is 32 raw bytes now and an env var
             * is a NUL-terminated string. nw-sup composes the path from
             * it and re-validates the hex -- see the note there: this
             * handoff is the one place the hash becomes text again, and
             * text is what the traversal class needs. */
            char hex[NW_BRICK_HEX + 1];
            for (int k = 0; k < NW_BRICK_HASH; k++)
                snprintf(hex + 2 * k, 3, "%02x", u[i].brick[k]);
            setenv("NW_BRICK", nw_unit_has_brick(&u[i]) ? hex : "", 1);
            /* The layer id travels as itself: it is already a name from a
             * closed alphabet, so unlike the hash there is no encoding
             * step and nothing to decode. nw-sup re-validates it anyway,
             * because nw-sup reads its unit from the environment and not
             * from the sealed blob. setenv with overwrite=1 so a house
             * without a layer cannot inherit the previous one's. */
            setenv("NW_LAYER", u[i].layer, 1);
            /* Decimal, not hex: this is a byte count, not an opaque id,
             * and nw-sup only ever needs it as a number to size a loop
             * device against. 0 = unset, same convention the field has
             * everywhere else it is read. */
            char lbb[24];
            snprintf(lbb, sizeof lbb, "%llu",
                     (unsigned long long)u[i].res.layer_bytes);
            setenv("NW_LAYER_BYTES", lbb, 1);
            int nb = 0;
            for (uint32_t b = 0; b < h->n_binds; b++) {
                if (bd[b].unit != (uint16_t)i) continue;
                char k[24];
                snprintf(k, sizeof k, "NW_BIND_%d", nb);
                setenv(k, bd[b].path, 1);
                nb++;
            }
            snprintf(nbuf, sizeof nbuf, "%d", nb);
            setenv("NW_NBINDS", nbuf, 1);
            execl(sup, "nw-sup", u[i].exec_path, u[i].name, (char *)0);
            die("exec nw-sup");
        }
        close(pp[1]);
        pid_t house = 0;
        if (read(pp[0], &house, sizeof house) != (ssize_t)sizeof house)
            die("read house pid");
        close(pp[0]);
        if (waitpid(mid, NULL, 0) < 0) die("reap mid");
        pids[i] = house;
        /* Width from NW_NAME_LEN, not a hand-written precision -- see the
         * comment in pid1.c's house-exit line. */
        char line[NW_NAME_LEN + 64];
        snprintf(line, sizeof line, "spawned %.*s pid=%d lids=%u",
                 NW_NAME_LEN - 1, u[i].name, (int)house,
                 (unsigned)u[i].lids);
        say(line);
    }

    for (int i = 0; i < nlogs; i++)
        close(logw[i]);

    /* Every unit has now forked and inherited its own copies via fork()
     * (the same reasoning `pp`, this loop's own pid-reporting pipe,
     * already relies on) -- so nw-spawn's own copies of every edge's
     * socketpair are closed here, in bulk, the same way the log pipes
     * just above are: bulk-close at the end of the loop rather than
     * incrementally per-edge, matching this file's own existing idiom
     * rather than inventing a second one. No wire fd may survive past
     * this point; the fd census a test takes of this process's own
     * /proc/self/fd is what proves it, not this comment. */
    for (uint32_t k = 0; k < h->n_edges; k++) {
        close(edge_fd[k][0]);
        close(edge_fd[k][1]);
    }

    /* The census itself: ground truth built as a side effect of doing
     * the wiring (each unit's own n_wires, summed above), not a second
     * list maintained to compare against. Before the pid report -- the
     * same "a complete report or nothing" contract invariant 4 already
     * requires of this exit path for the ordinary case.
     *
     * WHAT THIS GUARDS AGAINST, SAID HONESTLY: not a bad BLOB -- nw_check
     * already guarantees a < n_units, b < n_units and a != b for every
     * edge before this file's main() ever runs (re-verified above via
     * nw_check(blob, n)), so given a valid blob this sum is 2*n_edges by
     * construction: every edge is seen by exactly one of `ed[k].a == i`
     * or `ed[k].b == i` on exactly two distinct iterations of the outer
     * unit loop. tcb-review flagged this as possibly-unreachable and it
     * is, against nwcheck.c's own guarantee. What it is NOT unreachable
     * against is a future bug in the four lines immediately above this
     * one -- an edit to the `if`/`else if` pair, or to what gets pushed
     * into `my_wire_fd`, that silently drops or double-counts a wire
     * while nw_check's own guarantee stays intact. This is a regression
     * guard on nw-spawn's OWN assignment loop, not a second validation
     * of the blob. */
    if (wired_total != 2 * h->n_edges) die("edge wiring census");

    /* A STRUCTURAL check that the bulk-close above actually worked,
     * rather than trusting that it did -- but checking exactly the
     * descriptors nw-spawn itself created, not every descriptor in its
     * table. An earlier version of this check scanned /proc/self/fd for
     * ANY descriptor whose target starts "socket:" and died on the
     * first one found. That is wrong: nw-spawn never sanitizes its OWN
     * fd 0/1/2 (only a house's, inside pack_kit) -- they are whatever
     * PID 1 inherited, unexamined, all the way from whatever exec'd the
     * boot chain, and in an environment where that happens to be a unix
     * socket (a QEMU serial console backed by `-serial unix:...` is an
     * ordinary way to do this, and this project already builds toward
     * QEMU-based real-boot testing), the check killed every boot with
     * "leaked wire fd" regardless of whether any edge was ever declared.
     * Reproduced and fixed: fd-auditor.
     *
     * The fix checks only the specific fds this process itself opened --
     * every edge_fd[k][0] and edge_fd[k][1] -- and requires each to
     * already be closed (F_GETFD answering EBADF) after the bulk-close
     * loop above. That is exactly "did the close loop above actually
     * close everything it created", with no way to fire on a descriptor
     * nw-spawn never touched. */
    for (uint32_t k = 0; k < h->n_edges; k++) {
        for (int side = 0; side < 2; side++) {
            int fd = edge_fd[k][side];
            int fl = fcntl(fd, F_GETFD);
            if (fl >= 0) {
                /* Genuine leak: the fd is still open, fcntl did not fail,
                 * so errno was never set by this call and may be stale
                 * from something unrelated earlier in the process --
                 * die()'s message always prints errno, and printing a
                 * leftover value here would read as diagnostic of a
                 * cause it has nothing to do with. tcb-review. */
                errno = 0;
                die("leaked wire fd");
            }
            if (errno != EBADF) die("leaked wire fd");
        }
    }

    uint32_t nu = h->n_units;
    if (write(report_fd, &nu, sizeof nu) != (ssize_t)sizeof nu) die("report n");
    if (write(report_fd, pids, sizeof(pid_t) * nu) != (ssize_t)(sizeof(pid_t) * nu))
        die("report pids");
    close(report_fd);

    say("units spawned");
    return 0;
}
