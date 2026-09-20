#ifndef NW_BLOB_H
#define NW_BLOB_H

#include <stddef.h>
#include <stdint.h>

/* Bumped 05 -> 06 on 2026-09-11, when window_s left struct nw_unit.
 * The layout changed and the magic did not, so every pre-change blob was
 * refused as NW_E_SIZE ("size") -- correct verdict, wrong diagnosis. An
 * operator reads "size" as a truncated or corrupt file and goes looking
 * for a bad copy; the truth was "this slot holds a plan the previous
 * nw-cc baked". NW_E_MAGIC is the code that names that, and it had been
 * made unreachable for the one class it exists for: one channel carrying
 * two meanings, separated only by which integer, which is bug 9's shape.
 * Found independently by drift and tcb-review, each with the reproduction.
 * The magic and the layout MUST move together or the diagnosis lies --
 * a rule for whoever changes the layout, not something the code enforces.
 * The asserts below say so in every message they print, because that is
 * the one moment a reader is guaranteed to be looking. */
#define NW_MAGIC        "NWPLAN09"
#define NW_NAME_LEN     32
#define NW_PATH_LEN     128
/* PHASE 3: THE PLAN CARRIES A HASH, NOT A PATH. `brick` is 32 raw bytes of
   sha256 over the image file's contents, and nw-sup builds
   NW_BRICK_DIR "/" <64 hex> NW_BRICK_SUFFIX itself. All-zero means no brick.

   The point is not the 64 bytes saved. A path is a free-form string in the
   TCB's input path, and `path_ok_len` had to defend it -- a brick that
   traversed out with `..` baked clean, passed nw-check, booted, and logged
   `lid brick` while rooted on the machine (CLAUDE.md, the characteristic
   failure). A fixed-width hash CANNOT EXPRESS a traversal: there is no
   separator and no relative component, so every value that names anything
   names a file under one directory. The check is not removed, the input
   class it defended against is.

   ONE VALUE NAMES NOTHING, and this sentence said "every one of the 2^256
   values" until `tcb-review` pointed at the line three above it: all-zero
   is spent on "no brick". A plan writing 64 zeros therefore declares a
   brick and gets a house on the machine root, with no `lid brick` line and
   every reader reporting success. Refused in bakery/nw-cc.py, which is the
   ONLY place it can be refused -- once the blob exists, 32 zero bytes is
   the no-brick encoding and nothing here can tell the two apart.

   The directory and suffix are declared, not described, because the program
   that WRITES an image and the program that MOUNTS it must agree on where
   it lives, and a path written in both places is the drift class invariant
   3 is about -- the same argument NW_BRICK_MNT makes below.

   Do not enumerate the readers here. This comment listed three and was
   wrong about two of them on the day it was written: tests/run.py derives
   its stage limit from NW_PATH_LEN and not from these at all, and
   bakery/mkbrick.py -- the only tool that writes an image for a real
   machine -- defaulted its output directory to a LITERAL "/nw/bricks".
   Changing the #define moved nw-sup and left the packer behind, with a
   clean compile, no assert, and a green suite, because the suite's own
   make_brick reads the header and follows nw-sup wherever it goes. On
   hardware that is every brick house dying at `open brick image`. Found by
   `tcb-review` and `fd-auditor` independently; mkbrick reads it now.

   KNOWN UNCONVERTED as of 2026-09-12: dawn.c:158 creates the directory with
   `mkpath(NW_ROOT_MNT "/nw/bricks")`, a literal, two lines from a mkpath
   that does use its constant. It is in another agent's file and the trunk
   boots, so it is flagged rather than fixed here -- but it is the same
   class, in the TCB, and it is the reason this paragraph stops counting
   readers and starts naming the rule. */
/* WHERE A HOUSE'S DATA LIVES. Every house with a brick also gets exactly
   one writable layer, and the two together are what it roots in: the brick
   is what it can SEE, the layer is what it can KEEP. Not opt-in -- there is
   no plan that has a brick and no layer, and nwcheck.c refuses one.

   <layer-id>/upper is the house's data and <layer-id>/work is overlayfs's
   scratch. The pair answers the work-dir requirement BY CONSTRUCTION:
   overlayfs needs a work dir on the same filesystem as upper and not
   inside it, and two siblings under one parent are that, with no third
   path for anyone to reason about or get wrong.

   This replaces /nw/stores, renamed rather than reused. A directory called
   `stores` holding overlay upper-dirs is a name that misleads whoever
   reads it in six months, and the store concept is gone rather than
   renamed: it was this mechanism under another name, so collapsing them
   removes a thing instead of adding one beside it.

   KEYED BY A DECLARED layer-id, NOT BY THE HOUSE NAME. Under name-keying,
   renaming a house silently hands it an empty layer while its data sits
   orphaned under the old name -- no error anywhere, which is bugs 4, 9 and
   13's shape applied to a house's data instead of its descriptors. One
   field avoids it. */
#define NW_LAYER_DIR    "/nw/layers"
#define NW_LAYER_UPPER  "upper"
#define NW_LAYER_WORK   "work"
#define NW_BRICK_DIR    "/nw/bricks"
#define NW_BRICK_SUFFIX ".img"
#define NW_BRICK_HASH   32    /* raw sha256, not hex */
/* Derived, not declared: the hex spelling is the same hash, so `64` written
   here as a literal is a second copy of `32` and invariant 3's drift class
   in miniature. nw-spawn sizes its hex buffer from this and nw-sup checks
   the length it receives against it; a hash width change must not be able
   to leave either behind. */
#define NW_BRICK_HEX    (NW_BRICK_HASH * 2)
#define NW_MAX_BINDS    128
#define NW_MAX_UNITS    64
/* Chosen, not counted, and the difference is the point. Nothing below
 * enumerates eight descriptors, because nothing ever did: 8 was a budget
 * line written beside the assert, slack for "PID 1's own table" that no
 * one itemised.
 *
 * TRUE OF THIS TREE after the interleave and the fd pre-flight:
 *   - counted peak is 6 + n -- stdio, one log_w per house, signalfd,
 *     two report-pipe ends. Bounded 2026-09-14: last open 1018, first
 *     fail 1020 on `report pipe`. The interleave is why it is n not 2n.
 *   - pid1.c reads this constant and compares NW_FD_RESERVED + n to
 *     rlim_max from getrlimit. Soft is not the ceiling. What is off
 *     the correctness path is the DECISION -- refuse or accept, made
 *     against rlim_max and unaffected by the raise that follows.
 *     THE RAISE ITSELF IS ON IT, and this bullet said otherwise for
 *     one round: delete only the setrlimit and a plan this check
 *     ACCEPTED halts `report pipe` whenever soft < need <= hard.
 *     Measured at n=3 soft=8 hard=20, and at n=6 soft=10.
 *   - that check sits two above the counted peak and refuses two
 *     houses a 1024-descriptor machine would honour, a cost taken
 *     deliberately. The 8 is labelled because it now stands beside a
 *     measured rlim_max; an unlabelled 8 next to a measured 1024
 *     reads as though both were obtained the same way. They were not.
 *
 * WAS TRUE of the tree before that stack, measured on two machines:
 * 3 + 2n at the pipe loop -- stdio only, plan fd already closed.
 * Last open 510, first fail 511 against soft 1024, no slack,
 * floor((1024-3)/2) = 510.
 *
 * The asserts below still spell the peak 2n + 8, the shape from
 * before the interleave. At NW_MAX_FDS = 1024 that caps
 * NW_MAX_UNITS at 508 where the count allows 1018. DECIDED
 * 2026-09-14, landed as 1f11c37 on 2026-09-20: they stay
 * conservative and are NOT
 * corrected to follow the count. Tightening them would relax a
 * bound to fix a problem nobody has. Whoever raises NW_MAX_UNITS
 * past 508 revisits this, with a plan that needs the room. */
#define NW_FD_RESERVED  8
#define NW_MAX_FDS      1024

/* Where nw-sup mounts a brick image. A directory brick was bind-mounted
   onto itself and was therefore its own mount point; an image is a file
   and cannot be, so phase 2 needs somewhere to land it. Declared here
   because TWO programs must agree on it -- dawn creates it, nw-sup mounts
   on it -- and a path written in both places is the drift class invariant
   3 is about. dawn prefixes NW_ROOT_MNT because it runs before the pivot;
   nw-sup uses it as-is because it runs after. docs/plans/01. */
#define NW_BRICK_MNT    "/nw/mnt"

_Static_assert(NW_MAX_UNITS * 2 + NW_FD_RESERVED <= NW_MAX_FDS,
               "derived fd budget");

/* AND BOUNDED FROM BELOW, which the two asserts above and below are not.
 * They cap this constant; nothing capped it the other way, and pid1.c's
 * pre-flight is sound only because 8 >= the counted peak's constant 6.
 * Set it to 4 and the check ACCEPTS a city that then dies at
 * `HALT: report pipe` -- measured, reserved=4 n=4 hard=8. The suite goes
 * red on that mutation for the wrong reason (the fd test's synthetic
 * soft=8 collides with hard_lo=7 and it CRASHES rather than failing an
 * assertion), and at reserved=7 there is no collision and it passes
 * green. So the only honest detector was a fixture literal. This puts
 * the counted 6 in the compiler, which is the one place "counted peak is
 * 6 + n" stops being a comment. `tcb-review`. */
_Static_assert(NW_FD_RESERVED >= 6,
               "pre-flight must cover the counted peak 6 + n");

/* How far nwspawn.c's close_others must sweep, and how many descriptors it
 * must be able to hold while doing it. Derived here, beside the budget it
 * follows from, because that is where a reader looks for a limit: the sweep
 * has to cover every descriptor the budget above permits, or it silently
 * leaves some open. It was a bare 512 in three places in nwspawn.c until
 * 2026-09-10 -- a fifth undeclared fd limit that first bit at roughly 254
 * units, inside the range the budget allows. Limits are derived, never
 * declared twice (invariant 3). */
#define NW_FD_SWEEP     NW_MAX_FDS
_Static_assert(NW_MAX_UNITS * 2 + NW_FD_RESERVED <= NW_FD_SWEEP,
               "sweep must cover the whole legal descriptor range");

/* Slots in nwcheck.c's duplicate-name table. Not a plan-format limit -- no
 * blob and no spec mentions it -- but it is a second number that must stay
 * larger than NW_MAX_UNITS, so it lives here beside the first one and the
 * assert holds the relation.
 *
 * name_dup returns 0 from a full table: it inserts nothing and reports no
 * duplicate. That is correct only while the table cannot fill. It was five
 * bare 128s inside nwcheck.c until 2026-09-11, with the relation stated in a
 * comment and enforced by nothing: raising NW_MAX_UNITS to 256 compiled with
 * no warning, every static assert passing, and a 130-unit blob whose units
 * 128 and 129 share a name validated NW_OK -- accepted by the TCB and
 * rejected by the baker, at the one scale nothing in the suite boots.
 * Invariant 3, found by fd-auditor on af03922. Power of two because the
 * probe masks. */
#define NW_DUP_SLOTS    128
_Static_assert(NW_MAX_UNITS < NW_DUP_SLOTS,
               "duplicate-name table must never fill: see name_dup");
_Static_assert((NW_DUP_SLOTS & (NW_DUP_SLOTS - 1)) == 0,
               "duplicate-name table size must be a power of two");

/* There is one seccomp filter and a house does not choose. A second profile
 * (NW_PROF_BUILD, for a toolchain) existed briefly on 2026-09-10 and was
 * removed the same day: its allow-list was written from a table rather than
 * from running a compiler, and it killed gcc on the first exec. See
 * HISTORY.md section 23. When the toolchain house needs one it comes back
 * test-first, with a test that actually compiles something under it. */

/* Whether a clean exit means "done" or "unexpected". Explicit in the plan:
 * there is no default and no inference. Exit 0 used to mean do-not-restart
 * unconditionally, which collided with a reserved fault code meaning
 * do-not-restart-because-something-is-wrong. One channel, two opposite
 * meanings, separated only by which integer — the shape of bug 9. */
#define NW_KIND_ONESHOT  0u   /* exit 0 completes; never restarted */
#define NW_KIND_LONGRUN  1u   /* any exit is unexpected, incl. 0 */

#define NW_LID_SECCOMP   0x01u
#define NW_LID_LANDLOCK  0x02u
#define NW_LID_NEWNS     0x04u
#define NW_LID_NEWNET    0x08u

/* Scheduler policy for a house. 0 is UNSET, and it means NO POLICY IS
 * DECLARED -- the house keeps whatever it inherits. That is deliberately
 * not a promise about which policy that is: nothing here knows what
 * nw-sup was started under, and a comment saying "SCHED_OTHER" would be
 * a claim about a machine written into a file that carries only
 * properties of the plan.
 *
 * The consequence is NW_E_NICEPOL's shape. `nice` is meaningful only
 * under SCHED_OTHER, so it requires a DECLARED sched=other rather than
 * merely not-BATCH-and-not-IDLE: under an undeclared policy nobody here
 * can say whether the kernel would keep the number or discard it, and a
 * rule enforced against an assumption the header refuses to make is not
 * a rule. `tcb-review` found the exemption and the contradiction that
 * made it unsafe, in the same diff. */
#define NW_SCHED_UNSET   0u
#define NW_SCHED_OTHER   1u
#define NW_SCHED_BATCH   2u
#define NW_SCHED_IDLE    3u
#define NW_SCHED_MAX     NW_SCHED_IDLE

/* cgroup v2's own range for cpu.weight and the kernel's for nice. Named
 * here so the checker and the baker are quoting the same bound rather
 * than each spelling a literal -- the drift class invariant 3 is about,
 * one level down from a limit.
 *
 * NW_CPU_WEIGHT_MIN'S ONLY READER IS THE BAKER, and that is not an
 * oversight. 0 is unset, so the checker's `> MAX` already accepts
 * exactly {0} union [MIN, MAX] while MIN is 1; a floor test there
 * would be a branch nothing can enter, which is the unreachable-code
 * shape NW_E_RESZERO was removed for. It becomes the checker's the day
 * the floor moves off 1, and not before. `tcb-review` found the
 * comment claiming all four bounds were quoted by the checker. */
#define NW_CPU_WEIGHT_MIN 1
#define NW_CPU_WEIGHT_MAX 10000
#define NW_NICE_MIN      (-20)
#define NW_NICE_MAX      19

/* THE RESOURCE BLOCK. One per house, flat, and every field UNSET by
 * default -- 0 means "no limit declared", never a limit of zero. A
 * default here would be a number nobody chose, failing in the direction
 * hardest to diagnose; the baker reports which houses have no block
 * instead, so the absence is visible without inventing a value.
 *
 * A BLOCK RATHER THAN LOOSE FIELDS, and flat rather than nestable. When
 * a group level lands it is a table of groups plus a group id in this
 * block: units do not move and the plan is not reshaped. Loose fields in
 * nw_unit would have made that a layout migration instead.
 *
 * EVERY NUMBER HERE IS A PROPERTY OF THE PLAN, never of a machine, and
 * that is structural rather than labelled. A blob has carried no
 * machine-derived number since it existed -- no getrlimit, no device
 * number, no CPU count -- so there is nothing to label and no labelling
 * path that can diverge from the measuring path. A field saying "plan"
 * beside a value computed from the machine is worse than no field,
 * because it reads as verification.
 *
 * Two consequences of that rule, both visible in what is NOT here:
 *
 *   - io.max is keyed by device major:minor in cgroup v2, and a device
 *     number is a machine property. The block carries the RATE only.
 *     Resolving the device is a job for whatever applies the block,
 *     on the machine it applies it to; nothing does yet.
 *   - `cpu_mask` names CPU indices, which mean different things on
 *     different machines -- but it is DECLARED policy rather than a
 *     number obtained from a machine, which is the distinction the rule
 *     draws.
 *
 * WHAT HAPPENS TO A MASK NAMING A CPU THE MACHINE LACKS IS UNDECIDED,
 * and this comment asserted a refusal for one round. Nothing refuses
 * it: `cpus=63` on a four-CPU machine bakes clean and validates clean,
 * because the bound is the mask's own width and there is no runtime to
 * ask. `claims` ran it. The choice -- refuse, or narrow to what exists
 * -- belongs with the code that applies the block, and
 * tools/HANDOFF-resources.md is where it is put. */
struct nw_res {
    uint64_t cpu_mask;     /* bit i = CPU i; 0 = unset, meaning all */
    uint64_t mem_high;     /* memory.high, bytes: throttle; 0 = unset */
    uint64_t mem_max;      /* memory.max,  bytes: backstop; 0 = unset */
    uint64_t io_rbps;      /* read  bytes/sec; 0 = unset */
    uint64_t io_wbps;      /* write bytes/sec; 0 = unset */
    uint64_t layer_bytes;  /* capacity of the writable layer; 0 = unset */
    uint16_t cpu_weight;   /* cgroup v2 cpu.weight, 1..10000; 0 = unset */
    int8_t   nice;         /* -20..19; 0 = unset, and only under
                            * NW_SCHED_OTHER. This comment omitted the
                            * `0 = unset` half while check(), nw_check()
                            * and empty_res() all treated it that way,
                            * which is how
                            * `nice=0` came to be the one declared zero
                            * the baker accepted. `control`. */
    uint8_t  sched_policy; /* NW_SCHED_*; NW_SCHED_UNSET = unset */
} __attribute__((packed));

struct nw_unit {
    char     name[NW_NAME_LEN];
    /* "if any" is the load-bearing clause and it is not a safety argument.
     * Audited 2026-09-14 against docs/options/07, which nwcheck.c:109
     * already names: "an exec_path or a bind whose name resolves through a
     * link escapes just as cleanly... the fix is to stop carrying free-form
     * paths -- which the brick has now done and these two have not."
     *
     * Two cases where resolution is NOT inside a sealed image:
     *   - No brick. All-zero `brick` shares the machine root, so this is
     *     exactly a TCB path and path_ok_len is the only guard.
     *   - Layer shadow. Even with a brick, resolution happens in the
     *     OVERLAY (brick + layer), not the sealed lower. A symlink planted
     *     in the writable layer over exec_path is followed, and a restart
     *     execs through it again. Content-addressing the brick does not
     *     freeze this path the second time.
     * fexecve on an O_PATH opened after the overlay is up would pin one
     * resolution per spawn; it does not take the name out of the plan, so
     * it is not a format change. path_ok_len is a guard, not the fix. */
    char     exec_path[NW_PATH_LEN];   /* resolved inside the brick, if any */
    uint8_t  brick[NW_BRICK_HASH];     /* all-zero = no brick: shares the machine root */
    char     layer[NW_NAME_LEN];       /* "" = no layer; set iff brick is set */
    uint8_t  kind;       /* NW_KIND_* — was 'critical' until 2026-09-10 */
    uint8_t  budget;     /* deaths for the life of nw-sup; 0 = no restart */
    uint8_t  lids;
    uint8_t  _pad;       /* must stay zero; nwcheck rejects a dirty spare */
    struct nw_res res;   /* every field unset = unlimited; see nw_res */
} __attribute__((packed));

/* A path made visible inside a house's brick before it pivots. Bind mounts of
 * paths, not descriptors handed over: the init provisions nothing and the
 * house opens what it needs itself (invariant 5). The path is the same inside
 * and out, so a house uses the name it would have used anyway.
 *
 * The mount point must already exist inside the brick. nw-sup will not create
 * it: a brick is sealed and content-addressed, and mkdir'ing into one to make
 * room for a mount would break the seal to save a bake-time decision. */
struct nw_bind {
    uint16_t unit;
    char     path[NW_PATH_LEN];
} __attribute__((packed));

struct nw_hdr {
    char     magic[8];
    uint32_t n_units;
    uint32_t n_binds;
    uint32_t crc32;
} __attribute__((packed));

/* The on-disk layout, pinned field by field: offset, extent and type.
 *
 * WHAT THIS CATCHES AND WHAT IT DOES NOT, because every comment that has
 * stood here claimed more than the code did. The first said "NWPLAN05 and
 * sizeof(nw_unit) are one agreement"; the assert it introduced mentions
 * no magic, and a SIZE constant cannot see a REORDER. Swapping `budget`
 * and `lids` was green under -Werror with that size-only assert (check it
 * against 2ed4a45, where it still reproduces); a plan declaring
 * `lids=none` booted under seccomp with its restart budget silently 0,
 * and nw-check said OK.
 *
 * The offsets that replaced it were green for two more mutations, both
 * found by tcb-review at 944e9e7 and both written INTO THIS STRUCT, which
 * is the block the pin sits under:
 *
 *   - shrink `exec_path` by 8 and spend the bytes on a new field. Offsets
 *     and NW_UNIT_SIZE are unchanged, so the build and the suite are
 *     green -- and nwcheck.c validates with path_ok_len(s, NW_PATH_LEN),
 *     a macro rather than a sizeof, so it reads 8 bytes past the array
 *     into the new field and refuses any nonzero value as NW_E_PATH. Bug
 *     12's shape, under a comment saying a field addition is caught.
 *   - `uint8_t budget` -> `int8_t budget`. Same offsets, same size, green
 *     build, green suite; nwspawn.c sign-extends through
 *     snprintf(bbuf, 8, "%u", ...) and a budget of 200 reaches the house
 *     as 4294967. nwcheck.c range-checks `budget` nowhere, which is why
 *     this member and not another.
 *
 * So the extent and type asserts below are not belt-and-braces: each one
 * is a mutation that was green. Offset alone pins where a member STARTS
 * and what the struct TOTALS, and nothing else -- not how far a member
 * reaches, not what it is.
 *
 * Caught now: a field added, removed, resized, retyped or MOVED, and
 * `_pad` reused for anything of a different offset, extent or type.
 *
 * NOT caught, and there is no version field to catch it with: changing
 * what a byte MEANS while leaving it where it is -- redefining `kind`'s
 * values, say. Only the magic can carry that, and nothing forces the
 * magic to move when the layout does, which is why every message below
 * says so at the one moment a reader is guaranteed to be looking. That
 * gap is real and named here rather than papered over; it has no options
 * doc yet (docs/options/09 was the file-ownership question and was
 * deleted at 5422f5b).
 *
 * NOT caught either: __attribute__((packed)) is asserted by nothing. It
 * is inert on any plausible ABI here -- nw_unit is all char and uint8_t,
 * nw_hdr's u32s already sit at 8/12/16 -- but do not read this block as
 * protecting it.
 *
 * The trailing 4 in NW_UNIT_SIZE is hand-written; the other three terms
 * are the same macros the struct uses, so a wrong 4 is the only way that
 * constant can be wrong, and it is a build error rather than a wrong
 * blob. The offsets below are hand-written too, which is the point of
 * them: they are a second, independent statement of the layout. */

/* Offset, extent and type, each with the sentence the reader needs at the
 * moment the build stops. Written as macros so the three cannot be given
 * different messages, or one of them quietly left off a member. */
#define NW_AT(s, m, off) \
    _Static_assert(offsetof(struct s, m) == (off), \
                   #s "." #m " moved: the layout changed, bump NW_MAGIC")
#define NW_EXTENT(s, m, n) \
    _Static_assert(sizeof(((struct s *)0)->m) == (n), \
                   #s "." #m " extent changed: bump NW_MAGIC")
#define NW_TYPE(s, m, t) \
    _Static_assert(_Generic(((struct s *)0)->m, t: 1, default: 0), \
                   #s "." #m " retyped: same bytes, different meaning, " \
                   "bump NW_MAGIC")
/* Arrays need the address-of form. `_Generic` applies lvalue conversion, so
 * a bare `name` decays to `char *` under gcc -- but CBMC's frontend keeps
 * the array type and the assert fires during Type-checking, which took
 * `make proof` down with `CONVERSION ERROR` while `make test` was green.
 * Taking the address sidesteps the decay and both frontends agree. The
 * proofs are part of the toolchain; an assert that only gcc can parse is
 * an assert that removes them. */
#define NW_ARR_TYPE(s, m, t, n) \
    _Static_assert(_Generic(&((struct s *)0)->m, t (*)[n]: 1, default: 0), \
                   #s "." #m " retyped: same bytes, different meaning, " \
                   "bump NW_MAGIC")
/* Hand-written from the field widths, NOT sizeof(struct nw_res) -- that
 * would fold the proof into an identity, which is the shape HISTORY 53
 * records a fix doing to a CBMC harness. Six 64-bit fields, one 16-bit
 * and two 8-bit. */
#define NW_RES_SIZE  (6 * 8 + 2 + 1 + 1)
_Static_assert(sizeof(struct nw_res) == NW_RES_SIZE,
               "resource block size drifted: a field was added, removed "
               "or resized, bump NW_MAGIC");
/* ONE LINE, deliberately: tests/run.py derives the unit size by matching
 * this define as a parenthesised expression and evaluating it, which a
 * backslash continuation defeats -- and it degraded as "blob.h has no
 * NW_UNIT_SIZE as a parenthesised expression", which is at least the
 * error naming its own cause. */
#define NW_UNIT_SIZE (NW_NAME_LEN + NW_PATH_LEN + NW_BRICK_HASH + NW_NAME_LEN + 4 + NW_RES_SIZE)
_Static_assert(sizeof(struct nw_unit) == NW_UNIT_SIZE,
               "unit size drifted: a field was added, removed or resized");
NW_AT(nw_unit, name,      0);    NW_EXTENT(nw_unit, name,      NW_NAME_LEN);
NW_AT(nw_unit, exec_path, 32);   NW_EXTENT(nw_unit, exec_path, NW_PATH_LEN);
NW_AT(nw_unit, brick,     160);  NW_EXTENT(nw_unit, brick,     NW_BRICK_HASH);
NW_AT(nw_unit, layer,     192);  NW_EXTENT(nw_unit, layer,     NW_NAME_LEN);
NW_AT(nw_unit, kind,      224);  NW_TYPE(nw_unit, kind,   uint8_t);
NW_AT(nw_unit, budget,    225);  NW_TYPE(nw_unit, budget, uint8_t);
NW_AT(nw_unit, lids,      226);  NW_TYPE(nw_unit, lids,   uint8_t);
NW_AT(nw_unit, _pad,      227);  NW_TYPE(nw_unit, _pad,   uint8_t);
NW_AT(nw_unit, res,       228);  NW_EXTENT(nw_unit, res,  NW_RES_SIZE);

/* The block's own members, for the same reason the unit's are here: an
 * offset pins where a member STARTS and what the struct TOTALS, and a
 * retype inside a fixed-size block moves neither. Swapping mem_high and
 * mem_max is the mutation this catches and the size assert does not --
 * a plan declaring a throttle would get a backstop and vice versa, which
 * is the difference between a house being slowed and a house being
 * killed. */
NW_AT(nw_res, cpu_mask,     0);  NW_TYPE(nw_res, cpu_mask,     uint64_t);
NW_AT(nw_res, mem_high,     8);  NW_TYPE(nw_res, mem_high,     uint64_t);
NW_AT(nw_res, mem_max,     16);  NW_TYPE(nw_res, mem_max,      uint64_t);
NW_AT(nw_res, io_rbps,     24);  NW_TYPE(nw_res, io_rbps,      uint64_t);
NW_AT(nw_res, io_wbps,     32);  NW_TYPE(nw_res, io_wbps,      uint64_t);
NW_AT(nw_res, layer_bytes, 40);  NW_TYPE(nw_res, layer_bytes,  uint64_t);
NW_AT(nw_res, cpu_weight,  48);  NW_TYPE(nw_res, cpu_weight,   uint16_t);
NW_AT(nw_res, nice,        50);  NW_TYPE(nw_res, nice,         int8_t);
NW_AT(nw_res, sched_policy, 51); NW_TYPE(nw_res, sched_policy, uint8_t);
/* name and exec_path are char: nwcheck.c hands them to path_ok_len and
 * name_ok as char *. `brick` is uint8_t BECAUSE IT IS NO LONGER TEXT -- 32
 * raw bytes, never printed, never parsed, never passed to a string
 * function. The type assert is what stops it quietly becoming a string
 * again, which is the change that would bring the traversal class back. */
NW_ARR_TYPE(nw_unit, name,      char, NW_NAME_LEN);
NW_ARR_TYPE(nw_unit, exec_path, char, NW_PATH_LEN);
NW_ARR_TYPE(nw_unit, layer,     char, NW_NAME_LEN);
NW_ARR_TYPE(nw_unit, brick,     uint8_t, NW_BRICK_HASH);

_Static_assert(sizeof(struct nw_bind) == 130,
               "bind size drifted: a field was added, removed or resized");
NW_AT(nw_bind, unit, 0);  NW_TYPE(nw_bind, unit, uint16_t);
NW_AT(nw_bind, path, 2);  NW_EXTENT(nw_bind, path, NW_PATH_LEN);
NW_ARR_TYPE(nw_bind, path, char, NW_PATH_LEN);

_Static_assert(sizeof(NW_MAGIC) - 1 == 8, "magic must fill nw_hdr.magic");
_Static_assert(sizeof(struct nw_hdr) == 20, "hdr is magic[8] + 3 * u32");
NW_AT(nw_hdr, magic,   0);   NW_EXTENT(nw_hdr, magic, 8);
NW_AT(nw_hdr, n_units, 8);   NW_TYPE(nw_hdr, n_units, uint32_t);
NW_AT(nw_hdr, n_binds, 12);  NW_TYPE(nw_hdr, n_binds, uint32_t);
NW_AT(nw_hdr, crc32,   16);  NW_TYPE(nw_hdr, crc32,   uint32_t);

#define NW_BLOB_SIZE(nu, nb) \
    (sizeof(struct nw_hdr) + (nu) * sizeof(struct nw_unit) \
                           + (nb) * sizeof(struct nw_bind))

/* The largest a legal blob can be. Every reader of a blob sizes its buffer
 * and its size check from this, so there is nothing to keep in sync: raising
 * NW_MAX_UNITS resizes all of them.
 *
 * It was five hand-written numbers in three TCB files until 2026-09-11 --
 * 1<<16 in pid1.c twice and nwspawn.c once, 1<<20 in nwcheck_main.c twice --
 * none of them related to what the format permits, and two of them
 * disagreeing by a factor of sixteen. fd-auditor measured the input where
 * that bites: at NW_MAX_UNITS = 187 with a full bind table, a plan nw-check
 * accepts makes PID 1 print `plan size` and halt. Every failure was loud, so
 * this is the same class as NW_DUP_SLOTS caught earlier rather than a bug
 * that shipped -- and the class is what invariant 3 is about. */
#define NW_BLOB_MAX ((uint32_t)NW_BLOB_SIZE(NW_MAX_UNITS, NW_MAX_BINDS))

/* Buffer size for a blob reader: one byte more than the largest legal blob,
 * so a read that fills the buffer is proof the file is too big. Without the
 * sentinel byte a buffer of exactly NW_BLOB_MAX silently truncates an
 * oversized file to precisely the length a maximal legal blob has, and
 * nw_check's `len != need` then agrees with it -- nw-spawn's recheck, which
 * exists to catch a file that is not the one PID 1 read, started accepting
 * a maximal blob with arbitrary bytes appended the moment its buffer shrank
 * to NW_BLOB_MAX. Found by tcb-review, reproduced. A reader compares the
 * count it got against NW_BLOB_MAX; it never reasons about `sizeof buf`. */
#define NW_BLOB_BUF (NW_BLOB_MAX + 1u)

enum {
    NW_OK = 0,
    NW_E_MAGIC = 1,
    NW_E_UNITS = 2,
    NW_E_SIZE = 3,
    NW_E_CRC = 4,
    NW_E_NAME = 5,
    NW_E_DUPNAME = 6,
    NW_E_PATH = 7,
    NW_E_RSV = 8,
    NW_E_LIDS = 9,
    NW_E_KIND = 10,
    /* NW_E_BRICK was 11 and is RETIRED, not renumbered around: phase 3 made
       `brick` 32 raw bytes of hash, and there is no invalid value of those
       bytes to report. The codes below shifted down by one, which is the
       precedent this enum already set -- never assume a numeric value, read
       the enum. */
    NW_E_BRICKNS = 11,
    NW_E_BINDS = 12,
    NW_E_BINDIDX = 13,
    NW_E_BINDPATH = 14,
    NW_E_LLBRICK = 15,
    NW_E_LAYER = 16,
    NW_E_LAYERPAIR = 17,
    NW_E_LAYERDUP = 18,
    /* The resource block. Range faults and the cross-field pairs, each
     * with its own code: "resource block" as one code would make the
     * refusal name the block and not the field, and an operator reading
     * it would have to guess which of nine numbers was wrong.
     *
     * THAT SENTENCE HAD ONE CODE UNDER IT FOR THE THREE RANGE FAULTS
     * for a round -- a rationale next to code doing its opposite, which
     * is what this project is named after. `tcb-review`. */
    NW_E_RESWEIGHT = 19,  /* cpu-weight outside cgroup v2's range */
    NW_E_RESSCHED = 20,   /* sched policy outside the closed set */
    NW_E_RESNICE = 21,    /* nice outside the kernel's range */
    NW_E_MEMORDER = 22,   /* throttle at or above the backstop */
    NW_E_NICEPOL = 23,    /* nice without a declared sched=other */
    NW_E_CAPNOLAYER = 24, /* layer capacity with no layer to bound */
    /* THERE IS NO CODE FOR A DECLARED ZERO. `cpu-weight=0` and an
     * omitted cpu-weight are the same byte, so this checker cannot tell
     * them apart and a code for it would be unreachable -- the shape
     * CLAUDE.md's characteristic failure is about, in an enum. The baker
     * refuses it, at bake time only, for the same structural reason
     * `lids=` is bake-time only. .claude/rules/plan.md records both. */
    /* Terminator, not a code. nw_errstr's bound and the length of errs[] in
     * nwcheck.c are both derived from it, so the three things that must
     * agree -- last code, array length, bound -- become one number.
     *
     * They were three separate declarations held together by a sentence in
     * .claude/rules/plan.md until 2026-09-11. Adding a code and updating the
     * bound while forgetting the string built clean under -Wall -Wextra
     * -Werror and segfaulted in nw_errstr, which pid1.c calls at boot on the
     * value nw_check returned. Found by fd-auditor.
     *
     * It must be the terminator and not NW_E_LLBRICK + 1: anchoring on the
     * last code makes the anchor move with the thing it is meant to pin, and
     * that version compiles clean against a drifted enum. Also measured. */
    NW_E__COUNT
};

const char *nw_errstr(int e);
/* CRC over two regions, which is the shape nw_check needs: the header with
 * its crc field zeroed, followed by the body. There is no one-region
 * convenience wrapper -- nw_crc32(data, len) existed, exported, called by
 * nothing, for the whole life of this file, and survived the 2026-09-10
 * extraction as a delegating one-liner that still had no caller. Pass NULL
 * and 0 for the second region. */
uint32_t nw_crc32_split(const void *a, uint32_t na, const void *b, uint32_t nb);
int nw_check(const void *blob, uint32_t len);

static inline const struct nw_hdr *nw_hdr(const void *blob)
{
    return (const struct nw_hdr *)blob;
}

static inline const struct nw_unit *nw_units(const void *blob)
{
    return (const struct nw_unit *)((const char *)blob + sizeof(struct nw_hdr));
}

static inline const struct nw_bind *nw_binds(const void *blob)
{
    const struct nw_hdr *h = nw_hdr(blob);
    return (const struct nw_bind *)((const char *)blob + sizeof(struct nw_hdr)
                                    + h->n_units * sizeof(struct nw_unit));
}

/* "DOES THIS UNIT HAVE A BRICK?" -- the only legal interrogation of the
 * field, and it lives here so that there is exactly one of it.
 *
 * All-zero means no brick. A hash has no terminator, so `brick[0]` answers
 * a different question: it reads one image in 256 -- every hash beginning
 * with a zero byte -- as having no brick. That is not a hypothetical. Phase
 * 3 converted the unit loop in nwcheck.c to scan the whole hash and left
 * the BIND loop in the same function reading brick[0], and the caller proof
 * asserting brick[0] too, so the proof pinned the defect in place and would
 * have turned red on the fix. Both found by `tcb-review`; reproduced by
 * baking two plans differing only in the first hex pair, one accepted and
 * one refused as `bind unit index`.
 *
 * The open-coded predicate had FIVE copies at that point. A rule that says
 * "scan every byte" is a rule someone has to remember at each new site,
 * which is the drift class invariant 3 is about, arriving in a predicate
 * instead of a number. So: one function, beside the field it reads, and no
 * site left that could disagree with another.
 *
 * In blob.h rather than nwcheck.c because nwspawn.c needs it too and does
 * not link the checker's non-inline half. It is a loop and a return, and
 * it removes the open-coded copies everywhere except proofs/, which keeps
 * its own -- deliberately, so the proof is an independent oracle rather
 * than an identity, and it says so at each one. Do not "tidy" those into
 * a call to this function: `control` measured that doing so made a
 * mutation of this very helper invisible to the proof. */
static inline int nw_unit_has_brick(const struct nw_unit *u)
{
    int any = 0;
    for (int k = 0; k < NW_BRICK_HASH; k++) any |= u->brick[k];
    /* NORMALISED, because a predicate must return a predicate. This
     * returned the OR itself, which is truthy and served every caller
     * that wrote `!has_brick` or `has_brick &&` -- and then the layer
     * rule compared it for EQUALITY against another flag and got
     * `1 != 171` on a plan that was correct. Caught the first time a
     * legal plan was baked, which is the only reason it did not ship.
     * A function named has_X that returns 171 is an invitation. */
    return any != 0;
}

#endif
