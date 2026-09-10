# Linux city TCB:
#   PID 1, nw-spawn, nw-check, rescue = C
#   nw-sup = nwsup.c + lids.c
#   baker = Python, offline
# artifacts/ is noexec. `make test` stages to /tmp/nw-init-run.

CC = gcc
CFLAGS = -Wall -Wextra -O2 -g -std=gnu11
STAGE = /tmp/nw-init-run

all: nw-root nw-spawn nw-check nw-sup nw-rescue unit-probe unit-boom unit-badcall unit-term

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

stage: all
	mkdir -p $(STAGE)/slots/A $(STAGE)/slots/B $(STAGE)/slots/rescue
	cp -f nw-root nw-spawn nw-check nw-sup nw-rescue \
	      unit-probe unit-boom unit-badcall unit-term $(STAGE)/
	chmod +x $(STAGE)/*
	python3 bakery/nw-cc.py --probe $(STAGE)/unit-probe --out $(STAGE)/slots/A/plan.blob --lids seccomp
	cp -f $(STAGE)/slots/A/plan.blob $(STAGE)/slots/B/plan.blob
	cp -f $(STAGE)/slots/A/plan.blob.sha256 $(STAGE)/slots/B/plan.blob.sha256 2>/dev/null || true
	cp -f $(STAGE)/nw-rescue $(STAGE)/slots/rescue/nw-rescue
	echo A > $(STAGE)/slots/current
	cp -f $(STAGE)/slots/A/plan.blob $(STAGE)/plan.blob
	cp -f $(STAGE)/slots/A/plan.blob.sha256 $(STAGE)/plan.blob.sha256

test: stage
	$(STAGE)/nw-check $(STAGE)/plan.blob
	python3 tests/run.py

clean:
	rm -f lids.o nw-root nw-spawn nw-check nw-sup nw-rescue \
	      unit-probe unit-boom unit-badcall unit-term
	rm -rf $(STAGE)
