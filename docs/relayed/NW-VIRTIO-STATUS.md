# The virtio status register is readable from the host

Measured 2026-09-14. Not in the tree when written. Answers the one item
`NW-UNCONSTRAINED-DESIGN` Addenda 5 and 6 both name as **the one thing that
must be checked before it is relied on**, and which both marked as inferred
from mechanism rather than known.

## The question

Addendum 5 proposes that a district's attestation carry one honest signal
beyond *QEMU exec'd*: the virtio device status register. The virtio
specification documents the progression `ACKNOWLEDGE` → `DRIVER` →
`FEATURES_OK` → `DRIVER_OK`, and `DRIVER_OK` on virtio-blk means a running
guest kernel with a working driver. It is admissible where a readiness
protocol is not, because the host observes a register the guest writes in
the course of initialising a device — nothing inside cooperates, nothing is
asked, no protocol is agreed.

What was never checked is whether QEMU exposes that field outside the guest,
and through what. Addendum 5: *"inferred from mechanism and has not been
checked. If it does not, decision one stands alone and the wrapper is all
there is."*

## What was measured

QEMU 8.2.2 (Debian 1:8.2.2+ds-0ubuntu1.18), TCG, guest kernel Ubuntu
6.8.0-139-generic, the ordinary four-house `mkboot.sh` images. QEMU was
started with `-qmp unix:/tmp/qmp.sock,server,nowait` and queried from
outside the guest while it booted.

`x-query-virtio` lists the devices:

```
[ {"name": "virtio-blk", "path": "/machine/peripheral-anon/device[0]/virtio-backend"},
  {"name": "virtio-blk", "path": "/machine/peripheral-anon/device[1]/virtio-backend"} ]
```

`x-query-virtio-status` on device[0], sampled immediately after start,
before the guest kernel had probed:

```
{ "name": "virtio-blk", "device-id": 2, "status": { "statuses": [] } }
```

The same query after `[nw-root] city open houses=4` appeared on the console:

```
VIRTIO_CONFIG_S_ACKNOWLEDGE: Valid virtio device found
VIRTIO_CONFIG_S_DRIVER:      Guest OS compatible with device
VIRTIO_CONFIG_S_FEATURES_OK: Feature negotiation complete
VIRTIO_CONFIG_S_DRIVER_OK:   Driver setup and ready
```

**The transition is the result, not the late reading.** A field that
returned all four statuses at every sample would be a constant and would
prove nothing about the guest. Empty-then-full is what makes it an
observation.

## The panic case, and it refutes the proposed use

Addendum 5's motivating sentence is: *"A guest kernel that panics on its own
initramfs leaves QEMU running perfectly, so the record says the district is
up while the machine inside is at a panic screen."* That is the case
`DRIVER_OK` was proposed to close. It was then measured.

Same disk, same kernel, an initrd whose `/init` exits immediately. The
kernel probes virtio-blk during driver init, long before it execs `/init`,
so the driver reaches `DRIVER_OK` and the guest then dies:

```
console: Kernel panic - not syncing: No working init found.
qemu poll: None            (the process is alive)
QMP query-status: running

virtio-blk status register, guest at the panic screen:
  VIRTIO_CONFIG_S_ACKNOWLEDGE: Valid virtio device found
  VIRTIO_CONFIG_S_DRIVER: Guest OS compatible with device
  VIRTIO_CONFIG_S_FEATURES_OK: Feature negotiation complete
  VIRTIO_CONFIG_S_DRIVER_OK: Driver setup and ready
```

**A panicked guest reports `DRIVER_OK`.** The signal does not distinguish a
working district from a dead one, because a kernel reaches driver init
before it reaches anything that could fail in a way worth attesting.

The fact Addendum 5 states is true and stays true: `DRIVER_OK` means a
running kernel with a working driver, observed by the host, with nothing
inside cooperating. What does not follow is the use. It was proposed to
close the gap between *QEMU exec'd* and *the guest booted*, and it does not
close it — it moves the boundary from `exec` to *early kernel*, which is a
few hundred milliseconds further along and still nowhere near a booted
machine.

That is this project's signature shape arriving in a design document about
avoiding it: a true sentence sitting next to a claim it does not support.

**What the signal is still worth.** It separates three states the record
currently cannot: QEMU exec'd and no guest kernel ran at all (empty), a
guest kernel ran and reached driver init (`DRIVER_OK`), and — with the
healthy boot above — nothing more. That is a real narrowing of the unknown
and it costs one QMP read. It is not attestation of the workload and must
not be recorded as one. Decision one of Addendum 5 — *the record says the
wrapper started and the inside is unknown* — stands, and now stands on a
measurement rather than for want of an alternative.

## What this still does not show

**The command is `x-` prefixed.** `x-query-virtio` and
`x-query-virtio-status` carry QEMU's own marker for an unstable interface:
no compatibility guarantee across releases. A design pinning district
attestation on them inherits a breakage that arrives at a QEMU bump rather
than at a change of ours. That is a cost to write down, not a reason to
refuse — but it should be written down, because the alternative is
discovering it during an upgrade.

**Reading it needs a QMP socket on the district's QEMU.** That is a
launch-time decision, not something addable to a running district. The shape
matches what Addendum 5 already proposed for district status — read-only, no
verbs, granted by a bind to districts the plan names — so the mechanism does
not reopen anything. But it does mean the plan has to say *this district is
observable* before the district starts, and a district launched without the
socket is permanently unattestable.

## Falsified by

The first clause is no longer a falsifier, it is the measured result: a
guest reaching `DRIVER_OK` with its init dead is what happens. What would
still falsify the remainder: a QEMU release where the `x-`
commands are removed or renamed. Or a district whose QEMU was started
without a QMP socket reporting a status at all, which would mean the reading
comes from somewhere other than where this measurement thinks it does.

## Method

`/home/claude/virtio_probe.py` in the session that produced this. Boots the
image with a QMP socket, polls `x-query-virtio-status` at two points, and
uses the serial console's `city open` line rather than a timer to decide
when the guest is up.

One note on the method, because it cost a round: the first run reported
`device-status: null` at both samples, which reads as *the field is not
exposed*. The field names were guessed. The real schema nests under
`status.statuses`. A null from a key you invented is a fact about the key.
