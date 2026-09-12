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

all: nw-dawn nw-root nw-spawn nw-check nw-sup nw-rescue unit-probe unit-boom unit-badcall unit-term unit-brick unit-slowdie unit-dieterm unit-lastwords unit-lastwordsmany unit-orphan unit-orphanslow

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

stage: all
	rm -rf $(STAGE)
	# No $(STAGE)/nw/bricks: phase 3 made nw-sup compose an ABSOLUTE
	# NW_BRICK_DIR path, so make_brick writes the image to the machine
	# root and this directory was created empty and used by nothing.
	mkdir -p $(STAGE)/nw/bin $(STAGE)/nw/stores
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
	mkdir -p /nw/mnt
	mkdir -p $(STAGE)/efi/slots/A $(STAGE)/efi/slots/B $(STAGE)/work
	cp -f nw-dawn nw-root nw-spawn nw-check nw-sup nw-rescue \
	      unit-probe unit-boom unit-badcall unit-term unit-brick unit-slowdie unit-dieterm \
	      unit-lastwords unit-lastwordsmany unit-orphan unit-orphanslow $(STAGE)/nw/bin/
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
	NW_STAGE=$(STAGE) python3 tests/run.py
	NW_STAGE=$(STAGE) sh tools/coverage-tcb.sh

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
