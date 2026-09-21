# Linux city TCB:
#   dawn, PID 1, nw-spawn, nw-check, rescue = C
#   nw-sup = nwsup.c + lids.c
#   baker = Python, offline
# `make test` stages to /tmp/nw-init-run.

CC = gcc
# -ffile-prefix-map makes the build bit-reproducible from any directory.
# Without it two builds of the same commit differ, because the absolute
# source path leaks through debug info -- measured 2026-09-10.
CFLAGS = -Wall -Wextra -O2 -g -std=gnu11 -ffile-prefix-map=$(CURDIR)=.
# Overridable so an isolated run (tools/, the control agent) cannot clobber
# the stage a parallel run is using.
STAGE ?= /tmp/nw-init-run

all: nw-dawn nw-root nw-spawn nw-check nw-sup nw-rescue unit-probe unit-boom unit-badcall unit-term unit-brick unit-slowdie unit-dieterm unit-lastwords unit-lastwordsmany unit-orphan unit-orphanslow unit-layer

# blob.h IS A PREREQUISITE, and leaving it off is not cosmetic. dawn now
# includes it for NW_BRICK_MNT, and the whole justification for that include
# is that the path is declared once instead of twice. Without this line,
# `make` after a change to NW_BRICK_MNT rebuilds nw-sup and leaves nw-dawn
# stale -- dawn creates the old directory, nw-sup mounts on the new one, and
# every brick house dies at `FAIL mount brick image errno=2`. The drift moves
# from the source to the build, which is worse, because grep now says the two
# places agree. `tcb-review`.
nw-dawn: dawn.c blob.h
	$(CC) $(CFLAGS) -o $@ dawn.c

nw-root: pid1.c nwcheck.c blob.h
	$(CC) $(CFLAGS) -o $@ pid1.c nwcheck.c

nw-spawn: nwspawn.c nwcheck.c blob.h
	$(CC) $(CFLAGS) -o $@ nwspawn.c nwcheck.c

nw-check: nwcheck_main.c nwcheck.c blob.h
	$(CC) $(CFLAGS) -o $@ nwcheck_main.c nwcheck.c

lids.o: lids.c lids.h
	$(CC) $(CFLAGS) -c -o $@ lids.c

nw-sup: nwsup.c lids.o blob.h lids.h
	$(CC) $(CFLAGS) -o $@ nwsup.c lids.o

nw-rescue: rescue.c
	$(CC) $(CFLAGS) -o $@ rescue.c

unit-probe: unit_probe.c
	$(CC) $(CFLAGS) -o $@ unit_probe.c

unit-boom: houses/boom.c
	$(CC) $(CFLAGS) -o $@ houses/boom.c

unit-badcall: houses/badcall.c
	$(CC) $(CFLAGS) -o $@ houses/badcall.c

unit-term: houses/term.c
	$(CC) $(CFLAGS) -o $@ houses/term.c

# Static: this one is copied *into* a brick, and a brick carries its own
# libraries. A dynamic build would resolve its loader outside the brick and
# the root test would pass for the wrong reason.
unit-brick: houses/brick.c
	$(CC) $(CFLAGS) -static -o $@ houses/brick.c

unit-slowdie: houses/slowdie.c
	$(CC) $(CFLAGS) -o $@ houses/slowdie.c

unit-dieterm: houses/dieterm.c
	$(CC) $(CFLAGS) -o $@ houses/dieterm.c

unit-lastwords: houses/lastwords.c
	$(CC) $(CFLAGS) -o $@ houses/lastwords.c

unit-orphan: houses/orphan.c
	$(CC) $(CFLAGS) -o $@ houses/orphan.c

# Same source, a child that outlives any hold the suite uses. Two
# binaries rather than one configurable at runtime: a house is exec'd
# with no arguments and a clean environment, so a knob would have to be
# a channel, and the whole point of the slow variant is to make "did
# shutdown WAIT?" separable by a margin no scheduler noise can close.
# The drain at a size the pipe cannot hold in one read. 2500 padded
# lines is ~160 KiB of final output; the unconditional SIGKILL that used
# to end shutdown lost a contiguous tail above ~32 KiB and lost nothing
# at five lines, which is the size the suite pinned. `tcb-review`.
unit-lastwordsmany: houses/lastwords.c
	$(CC) $(CFLAGS) -DLINES=2500 -o $@ houses/lastwords.c

unit-orphanslow: houses/orphan.c
	$(CC) $(CFLAGS) -DORPHAN_SLEEP_MS=3000 -o $@ houses/orphan.c

# -static, like unit-brick and for the same reason: this one runs INSIDE
# a brick, which contains the binary and nothing else -- no loader, no
# libc. A dynamically linked fixture there fails as ENOENT on execve,
# which reads as a missing binary and is a missing interpreter.
# harness.md records that trap; this is it, met head-on.
unit-layer: houses/layer.c
	$(CC) $(CFLAGS) -static -o $@ houses/layer.c

stage: all
	rm -rf $(STAGE)
	# No $(STAGE)/nw/bricks: phase 3 made nw-sup compose an ABSOLUTE
	# NW_BRICK_DIR path, so make_brick writes the image to the machine
	# root and this directory was created empty and used by nothing.
	mkdir -p $(STAGE)/nw/bin
	# NW_BRICK_MNT ON THE HOST ROOT, and it is deliberate. nw-sup mounts a
	# brick image on an ABSOLUTE path -- /nw/mnt -- because in production
	# dawn has pivoted and the machine root IS the staged tree. The suite
	# execs nw-root with no pivot, so that absolute path resolves against
	# the host root, where nothing created it: every brick test failed at
	# `mount brick image errno=2` until this line existed, which reads
	# exactly like a code defect and is not one.
	#
	# This is the harness being LESS capable than the machine, the inverse
	# of harness.md's usual case. (It is NOT "the first project-absolute
	# path the TCB uses", which this said until `tcb-review` ran the grep:
	# dawn alone has /sysroot, /efi, /efi/slots, /nw/bin/nw-root and /run.
	# The true and narrower claim is that it is the first absolute path the
	# TCB requires to ALREADY EXIST on the post-pivot root, which the lab
	# does not otherwise provide.) One idempotent mkdir;
	# concurrent suites cannot collide on it because each house's mount is
	# in its own namespace (verified, docs/plans/01).
	#
	# The alternative was an env override for the mountpoint, which is the
	# NW_HOLD_MS mistake exactly: a production control surface added for a
	# lab need. Refused.
	# /nw/layers beside it, and for the same reason: nw-sup composes an
	# ABSOLUTE layer path because in production dawn has pivoted and the
	# machine root IS the staged tree. The suite execs nw-root with no
	# pivot, so both resolve against the host root. Dawn creates these two
	# on a real boot; this line is the lab standing in for dawn, not a
	# second creator of the per-house children -- those come from
	# tools/stage-layers.py, which is the only thing that makes them.
	mkdir -p /nw/mnt /nw/layers
	mkdir -p $(STAGE)/efi/slots/A $(STAGE)/efi/slots/B $(STAGE)/work
	cp -f nw-dawn nw-root nw-spawn nw-check nw-sup nw-rescue \
	      unit-probe unit-boom unit-badcall unit-term unit-brick unit-slowdie unit-dieterm \
	      unit-lastwords unit-lastwordsmany unit-orphan unit-orphanslow \
      unit-layer $(STAGE)/nw/bin/
	chmod +x $(STAGE)/nw/bin/*
	# The sources the staged binaries were built from, staged with them.
	# tests/run.py's hash probe compiles nwcheck.c to ask which slot a name
	# lands in, and it must ask the code under test, not the tree. Compiling
	# from ROOT against a stale stage made the collision case degrade to a
	# plain duplicate while still printing "a real collision" -- the staging
	# trap inside a test, found by fd-auditor. Staged together, they cannot
	# disagree.
	mkdir -p $(STAGE)/src
	cp -f nwcheck.c blob.h $(STAGE)/src/
	# The specs' limits, derived from blob.h. Generated at stage time and
	# not only inside tests/run.py, because a fresh clone has no specs/
	# (it is a build product, gitignored) and install-agents --check reads
	# drift.md, which names specs/limits.als. `make test` on a clean clone
	# failed there before this line existed -- found by actually cloning,
	# which nothing else in the suite does.
	python3 tools/gen-spec-limits.py >/dev/null
	python3 bakery/nw-cc.py --probe $(STAGE)/nw/bin/unit-probe \
	    --out $(STAGE)/efi/slots/A/plan.blob --lids seccomp
	printf 'house solo %s/nw/bin/unit-probe kind=oneshot lids=seccomp\n' $(STAGE) >  $(STAGE)/work/slot-b.city
	printf 'house duo %s/nw/bin/unit-probe kind=oneshot lids=seccomp\n'  $(STAGE) >> $(STAGE)/work/slot-b.city
	python3 bakery/nw-cc.py --city $(STAGE)/work/slot-b.city \
	    --out $(STAGE)/efi/slots/B/plan.blob
	echo A > $(STAGE)/efi/slots/current

test: stage
	sh install-agents.sh --check
	$(STAGE)/nw/bin/nw-check $(STAGE)/efi/slots/A/plan.blob
# THE BOOT GLUE IS COMPILED BY NOTHING ELSE. tools/initrd-init.c is built
# only inside tools/mkboot.sh, which needs root to mount its images, so no
# gate anyone runs had ever compiled it -- a syntax error there ships and
# surfaces as a QEMU boot that dies before dawn. Found 2026-09-20, after a
# patch added three finit_module calls to that file and `make test` could
# not have caught a typo in them.
#
# -c -O2 -o /dev/null, NOT -fsyntax-only, and the difference is the whole
# gate: -fsyntax-only skips the analysis that produces warn_unused_result,
# so it reported 0 warnings on a file where -c -O2 reports 2. The first
# version of this line used it and would have passed by not looking --
# adding a gate of exactly the class it was written to catch. No link:
# mkboot builds this -static for the initrd and this target has no
# business producing that artifact. -Werror because a
# gate that prints two warnings every run is one people stop reading; the
# file was made clean in the same change so the flag can bite. What this
# buys is that the file still compiles; what it does NOT buy is that the
# initrd works, which needs a boot and is the operator's.
	gcc -c -O2 -std=gnu11 -Wall -Wextra -Werror -o /dev/null tools/initrd-init.c
# THE SUITE MUST PROVE IT RAN, the same rule as the fold suite below and
# for a sharper reason since --only landed: tests/run.py now has a code
# path that runs a NAMED SUBSET, and a defect in the flag's no-argument
# branch makes a bare invocation run ZERO tests and exit 0 under a line
# reading `SUBSET RUN PASSED`. Measured: `return []` in place of `return
# None` does exactly that. The target then failed further down, in
# coverage-tcb.sh, with a message about the CORPUS -- a true message
# naming the wrong cause, which is this project's characteristic shape
# arriving through a gate. `control` found it.
#
# Grepping the suite's own terminal line subsumes the exit status: main()
# raises out of expect() on a failure and never reaches the print, so the
# line cannot appear on a red run. The `ok` floor is what a terminal line
# alone does not give -- SUBSET RUN PASSED is a different string, but a
# subset containing a real test would print a terminal line and an ok.
# Same
# shape as --check's file floor, and like it, it is a number a run prints
# beside itself.
	NW_STAGE=$(STAGE) python3 tests/run.py 2>&1 | tee $(STAGE)/suite.log; \
	  grep -qE '^(ALL TESTS PASSED|PASSED, WITH SKIPS)' $(STAGE)/suite.log && \
	  [ "$$(grep -c '^ok ' $(STAGE)/suite.log)" -ge 40 ] || \
	  { echo "make: the suite did not prove it ran -- no terminal line, or" \
	         "fewer than 40 ok lines. A zero-test run exits 0." >&2; exit 1; }
# The fold's own suite. It runs HERE rather than beside the tarball it
# arrived in, because a suite outside the tree is a suite nobody runs --
# the same rule proofs/ exists to enforce. Exit 2 is "some check skipped
# and none failed", which a machine without root, erofs or overlay will
# produce, so it is accepted; the run still prints which properties went
# untested, and a skip is not a pass. Exit 1 is a real failure and stops
# the target.
# THE RUN MUST PROVE IT RAN. python3 exits 2 when it cannot OPEN the script
# and the guard reads 2 as "skipped, none failed", so a deleted file left
# `make test` green with the suite never running. `test -f` was the first
# answer and it closed deletion only: truncate the file to zero bytes, or
# replace it with a comment, and python3 exits 0 having run nothing --
# green again, two echoed command lines with nothing between them, which is
# exactly the output the failure it was meant to close produces. Existence
# is not execution. This greps the run's own summary for a nonzero pass
# count, so the evidence is something the suite printed rather than
# something about the file. `control`, twice.
#
# ONE RUN, AND tee GOES TO A FILE. `tee /dev/stderr` stood here for a
# round: /dev/stderr is /proc/self/fd/2, so when make's stderr is a
# regular file tee opens it with O_TRUNC and DESTROYS everything the
# earlier steps wrote -- the log restarts at this line. Measured with 40
# lines of prior output: none survived. It also ran the suite twice, once
# for the grep and once for the exit code. Grepping the summary for
# `0 fail` subsumes the exit code, so one run does both.
#
# STILL NOT CLOSED, written down rather than left silent: this proves at
# least one check ran, not that the SUITE ran. Delete all but one check
# and the gate passes. Closing that needs a minimum count, which is the
# hostage this project's own rules forbid.
	python3 bakery/test_fold.py 2>&1 | tee $(STAGE)/fold-suite.log; \
	  grep -qE '^[0-9]+ checks: [1-9][0-9]* pass, 0 fail' $(STAGE)/fold-suite.log
	NW_STAGE=$(STAGE) sh tools/coverage-tcb.sh

# The pre-report shape checker, over the diff you are about to report on.
# BASE= selects what to diff against; it defaults to the upstream branch,
# the same default tools/review-pack.sh uses, so the two see one change.
#
# Not part of `test`, and not because it is slow -- it is instant. It
# EXITS 0 ALWAYS, deliberately: a heuristic wired into a build gets routed
# around within a week, and then the signal is gone rather than merely
# ignored. There IS a target so that it is discoverable and so the answer
# to "when did it last fire" is not "never" -- which is the finding
# `tcb-review` raised against it arriving wired to nothing at all.
PREREPORT_BASE ?= $(shell git rev-parse --verify --quiet '@{u}'                     || git rev-parse --verify --quiet origin/main                     || echo HEAD)
prereport:
	@git diff $(PREREPORT_BASE) | python3 tools/prereport.py --diff -

# The brief's numbered invariants, against the tree. Reads only items under
# a heading whose first word is `Invariants`, so the refusals and the
# waiting-on-a-prerequisite entries are excluded BY SECTION rather than by
# anyone's judgement -- they are never candidates and cannot be counted as
# gaps.
#
# --exclude is not optional: the checker's own source contains every
# annotation form it looks for, so a tree containing it answers its own
# grep-absent checks. prereport.py hit the identical thing in the same
# session it arrived.
#
# NOT in `test`, and the reason is the honest one: today it reports one
# invariant verified out of eight, so wiring it into the suite would add a
# target that passes while checking almost nothing. It is a tool you run
# when you edit the brief. Exit 1 means an annotation is CONTRADICTED --
# the brief says something the tree does not.
checkbrief:
	python3 tools/checkbrief.py --brief CLAUDE.md --tree . 	    --exclude tools/checkbrief.py

# The CBMC proofs of the validator, and the controls that must fail. Not in
# `test`: it is minutes rather than seconds, and it needs cbmc, which is not
# a build dependency of anything here. `run.sh` exits 3 and says SKIP rather
# than passing when cbmc is absent -- a proof that could not run is not a
# proof that passed, same rule as the suite's skips.
proof:
	sh proofs/run.sh

# Real bootloader-style boot. Needs qemu-system-x86_64, a kernel with
# virtio-blk/ext4/vfat, mkfs.vfat, and root for loop mounts. Fails if
# the console has HALT or Attempted to kill init, or lacks city open.
# KERNEL= selects the image. This is not `make test`.
qemu:
	KERNEL=$(KERNEL) OUT=$(or $(QEMU_OUT),/tmp/nw-boot) \
	    sh tools/mkboot.sh --run --check

KERNEL ?= /boot/vmlinuz

clean:
	rm -f lids.o nw-dawn nw-root nw-spawn nw-check nw-sup nw-rescue \
	      unit-probe unit-boom unit-badcall unit-term unit-brick unit-slowdie unit-dieterm
	rm -rf $(STAGE)
