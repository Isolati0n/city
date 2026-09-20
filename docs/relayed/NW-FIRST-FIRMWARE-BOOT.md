# The first boot through firmware

Measured 2026-09-14. Not in the tree when written. `NEXUSWEAVE.md` §11 lists
*never booted on real hardware, QEMU only* among what is known to be
unverified, and `NW-BASE-IMAGE-BUILDER` says what is missing is *a UEFI path,
a whole disk with a partition table, and the second slot placed before first
boot*. This is those three, under OVMF. It is not real hardware.

## What every previous boot did

`mkboot.sh` passes `-kernel` and `-initrd` on the QEMU command line, so QEMU
loads the kernel itself and firmware is never involved. That is the shortcut
the base-image-builder file names: the boot proves the init and proves
nothing about the path a real machine takes to reach it.

## What this boot did

No `-kernel`. No `-initrd`. A single 320 MiB disk image, GPT, two
partitions, given to QEMU as one virtio drive with OVMF as pflash.

```
BdsDxe: loading Boot0001 "UEFI Misc Device" from PciRoot(0x0)/Pci(0x3,0x0)
BdsDxe: starting Boot0001 "UEFI Misc Device" from PciRoot(0x0)/Pci(0x3,0x0)
EFI stub: Loaded initrd from LINUX_EFI_INITRD_MEDIA_GUID device path
[    0.000000] Command line: ... NW_ROOT=/dev/vda2 ... NW_ESP=/dev/vda1 ...
[    2.394073]  vda: vda1 vda2
[dawn] mounted /sysroot
[dawn] mounted /sysroot/efi
[dawn] pivoted MS_MOVE
[dawn] exec /nw/bin/nw-root
[nw-root] live slot /efi/slots/A
[nw-root] plan sealed /efi/slots/A/plan.blob
[nw-root] city open houses=4 slot=/efi/slots/A
```

Four things are established that no previous boot could establish.

**Firmware found and accepted the entry.** `BdsDxe` loaded
`\EFI\BOOT\BOOTX64.EFI` from the fallback path with no NVRAM boot entry
configured — a fresh `OVMF_VARS` file. That is item 1 of the base-image-
builder's hardware-failure ranking, on one permissive implementation.

**The kernel and initrd came from inside the UKI.** `EFI stub: Loaded initrd
from LINUX_EFI_INITRD_MEDIA_GUID device path` is the stub handing over its
own `.initrd` section. Nothing on the QEMU command line supplied either.

**The command line came from the UKI's `.cmdline` section, and carries no
slot.** PID 1 still reported `live slot /efi/slots/A`, read from
`slots/current` after boot. That is the tree fact Decision 1 rests on,
now observed through the path it was reasoning about rather than inferred
from source.

**Partitions, not whole devices.** Every prior boot passed `NW_ROOT=/dev/vda`
— a bare filesystem on a whole virtual disk. This one passed `/dev/vda2` and
`/dev/vda1` off a real partition table, and dawn mounted both. That path had
never run.

## What it does not establish

**Secure Boot is off and the UKI is unsigned.** That is item 2 of the
ranking and it is deleted entirely by this configuration rather than
answered. It remains the unwritten decision the consistency pass named.

**OVMF is one permissive implementation.** Item 1 is *passed on OVMF*, which
is a weaker statement than passed. Real firmware differs on where it looks
for the fallback path and what it does with NVRAM on a new disk.

**Disk geometry was not tested.** QEMU got an image of exactly the size the
builder chose, which is item 4 and is precisely the thing an emulator cannot
falsify.

**No GPU firmware, no second slot in NVRAM, no promote.** Slot B exists on
the disk and holds a copy of the same plan; nothing has ever switched to it.

## Where the prototype knowingly diverges from the design

`/home/claude/mkdisk.sh`, which is a prototype and not the builder.

**The live-slot record stays on the ESP.** Decision 3 moves it off, because
dawn mounts the ESP read-only and promote has no write path to the file it
exists to update. But `pid1.c:339` still reads it from there, so the
prototype matches the code rather than the document. When the record moves,
this script is wrong and should be changed with it.

**No `unpromoted` label.** The consistency pass established that absence is
not an initial state — `slot_from_current` returns −1 and `pid1.c:423` calls
`halt_now("slots/current")`, so a disk without the file halts rather than
coming up untrusted — and therefore the builder must write an initial
pointer. It does. But the label cannot live in that file: `slot_from_current`
rejects any character outside `[A-Za-z0-9_-]`, so `A, not yet promoted` is
refused and `A_unpromoted` parses as a slot name and then fails to find a
directory. The prototype writes a bare `A` and the label question stays open,
which is where the consistency pass left it.

**Both UKIs are one UKI.** The consistency pass asked what two UKIs are for
once the command line carries no slot — two payloads differing in kernel,
initrd or firmware bytes, or one is a recovery copy — and recorded that the
file never says. The prototype writes one, because writing two without
knowing what distinguishes them would be answering that question by accident.

## One thing worth keeping from building it

The first UKI attempt hardcoded section offsets (`0x20000`, `0x2000000`) and
objcopy reported `section below image base` four times. This stub's
`ImageBase` is `0x14df90000`, so a literal small offset lands underneath it.
The offsets are now derived from the stub's own last section and
`SectionAlignment`, read at build time.

That is the project's own rule arriving in a new file: a limit computed from
the thing it must agree with, rather than declared beside it. It is also a
warning for the real builder, since the stub is a distro artifact whose
layout is not ours and can change under us.
