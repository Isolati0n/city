#!/bin/sh
# mkdisk.sh -- prototype of the base image builder, per NW-BASE-IMAGE-BUILDER.
#
# NOT the builder and not in the tree. This exists to answer one question
# mkboot.sh cannot: does this system boot through firmware, rather than
# through QEMU's -kernel/-initrd shortcut? Items 1, 2 and 4 of that file's
# hardware-failure ranking are all invisible to the current boots.
#
# What it follows from the design:
#   - UKI, not a bootloader. One PE, kernel + initrd + cmdline.
#   - The command line carries NW_ROOT and NW_ESP and NO slot, because the
#     slot is post-PID-1 state read from slots/current.
#   - A whole disk: GPT, ESP, root. Both slots present before first boot.
#   - The builder writes the initial live-slot pointer. Per the consistency
#     pass, absence is not an initial state: slot_from_current returns -1
#     and PID 1 calls halt_now("slots/current"), so a disk without it does
#     not come up untrusted, it halts.
#
# Where it knowingly diverges from the design, and why:
#   - The live-slot record stays on the ESP. The design moves it off,
#     because dawn mounts the ESP read-only and promote has no write path.
#     pid1.c:339 still reads it from there, so the prototype matches the
#     code rather than the document. Promote is out of scope here.
#   - No "unpromoted" label. slot_from_current rejects any character
#     outside [A-Za-z0-9_-], so the label cannot live in that file. That is
#     the open item, not something to invent here.
#   - Secure Boot off. Unsigned UKI. That is the unwritten decision.
set -eu

OUT=${OUT:-/tmp/disk}
SRC=${SRC:-/tmp/bo}                       # mkboot output: root.img, initrd.cpio
KERNEL=${KERNEL:-/tmp/kx/boot/vmlinuz-6.8.0-139-generic}
STUB=/usr/lib/systemd/boot/efi/linuxx64.efi.stub
DISK=$OUT/city.img

rm -rf "$OUT"; mkdir -p "$OUT"

# The command line. NW_ROOT and NW_ESP now name PARTITIONS, not whole
# devices -- that is the first thing a partition table changes and the
# current boots have never exercised it.
CMDLINE="console=ttyS0,115200n8 ignore_loglevel NW_ROOT=/dev/vda2 NW_ROOT_FSTYPE=ext4 NW_ESP=/dev/vda1 NW_ESP_FSTYPE=vfat"
printf '%s' "$CMDLINE" > "$OUT/cmdline.txt"
printf 'nexusweave\n' > "$OUT/osrel.txt"

echo "== UKI =="
# Section VMAs are DERIVED from the stub, not written here. Hardcoded
# offsets produced "section below image base" from objcopy on this stub --
# its ImageBase is 0x14df90000, so a literal 0x20000 lands underneath it.
# Every offset below is computed from the stub's own last section.
align=$(objdump -p "$STUB" | sed -n "s/.*SectionAlignment[^0-9a-fA-F]*\([0-9a-fA-F]*\).*/\1/p" | head -1)
align=$((0x$align))
base=$(objdump -p "$STUB" | sed -n "s/.*ImageBase[^0-9a-fA-F]*\([0-9a-fA-F]*\).*/\1/p" | head -1)
base=$((0x$base))
last=$(objdump -h "$STUB" | awk "/^ *[0-9]+ [.]/ {print \$3, \$4}" | while read sz vma; do echo $((0x$vma + 0x$sz)); done | sort -n | tail -1)
next_vma() {  # round the running cursor up to SectionAlignment
    cur=$1
    echo $(( (cur + align - 1) / align * align ))
}
o_osrel=$(next_vma "$last")
o_cmdline=$(next_vma $(( o_osrel   + $(stat -c%s "$OUT/osrel.txt") )))
o_linux=$(next_vma   $(( o_cmdline + $(stat -c%s "$OUT/cmdline.txt") )))
o_initrd=$(next_vma  $(( o_linux   + $(stat -c%s "$KERNEL") )))
printf '  base=%#x align=%#x  osrel=%#x cmdline=%#x linux=%#x initrd=%#x\n' \
    "$base" "$align" "$o_osrel" "$o_cmdline" "$o_linux" "$o_initrd"

objcopy \
    --add-section .osrel="$OUT/osrel.txt"     --change-section-vma .osrel="$o_osrel" \
    --add-section .cmdline="$OUT/cmdline.txt" --change-section-vma .cmdline="$o_cmdline" \
    --add-section .linux="$KERNEL"            --change-section-vma .linux="$o_linux" \
    --add-section .initrd="$SRC/initrd.cpio"  --change-section-vma .initrd="$o_initrd" \
    "$STUB" "$OUT/city.efi"
ls -la "$OUT/city.efi" | awk '{print "  city.efi", $5, "bytes"}'

echo "== whole disk: GPT, ESP, root =="
# 64 MiB ESP + 64 MiB root + slack. Sizes are machine properties in the
# real builder; here they are whatever fits the fixtures.
dd if=/dev/zero of="$DISK" bs=1M count=320 status=none
sgdisk --clear \
       --new=1:2048:+96M  --typecode=1:ef00 --change-name=1:ESP \
       --new=2:0:+128M    --typecode=2:8300 --change-name=2:nw-root \
       "$DISK" > /dev/null
sgdisk --print "$DISK" | tail -4

# Partition offsets, read back rather than assumed.
ESP_OFF=$(sgdisk --info=1 "$DISK" | awk '/First sector/{print $3}')
ROOT_OFF=$(sgdisk --info=2 "$DISK" | awk '/First sector/{print $3}')
ESP_SZ=$(sgdisk --info=1 "$DISK" | awk '/Partition size/{print $3}')
echo "  esp  lba $ESP_OFF  root lba $ROOT_OFF"

echo "== ESP contents =="
dd if=/dev/zero of="$OUT/esp.part" bs=512 count="$ESP_SZ" status=none
mkfs.vfat -F 32 -n NW-ESP "$OUT/esp.part" > /dev/null
mmd -i "$OUT/esp.part" ::/EFI ::/EFI/BOOT ::/slots ::/slots/A ::/slots/B
# Fallback path. Real firmware differs on whether it honours this; that is
# ranking item 1 and it is the whole reason for booting through OVMF.
mcopy -i "$OUT/esp.part" "$OUT/city.efi" ::/EFI/BOOT/BOOTX64.EFI
# Both slots present before first boot. Slot B carries the same plan here;
# a real stager would put a candidate there.
mcopy -i "$OUT/esp.part" /tmp/bo/mnt/espstage/slots/A/plan.blob ::/slots/A/plan.blob
mcopy -i "$OUT/esp.part" /tmp/bo/mnt/espstage/slots/A/plan.blob ::/slots/B/plan.blob
printf 'A\n' > "$OUT/current"
mcopy -i "$OUT/esp.part" "$OUT/current" ::/slots/current
mdir -i "$OUT/esp.part" ::/slots | sed 's/^/  /'

echo "== assemble =="
dd if="$OUT/esp.part" of="$DISK" bs=512 seek="$ESP_OFF" conv=notrunc status=none
dd if="$SRC/root.img" of="$DISK" bs=512 seek="$ROOT_OFF" conv=notrunc status=none
echo "  $DISK $(stat -c%s "$DISK") bytes"

cp /usr/share/OVMF/OVMF_VARS_4M.fd "$OUT/vars.fd"
echo "== ready: boot with OVMF, no -kernel and no -initrd =="
