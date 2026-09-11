#!/bin/sh
# tools/mkboot.sh — clean checkout -> bootable images for QEMU.
#
# Not TCB. Does not edit any file CLAUDE.md lists as TCB. Compiles
# static copies of the existing C, packs an initramfs, writes two raw
# disks (ext4 root + FAT32 ESP), and prints the qemu-system-x86_64
# command line.
#
# Host tools: gcc, make, python3, qemu-system-x86_64, mkfs.ext4,
# mkfs.vfat, cpio, dd, zstd (only if the chosen kernel ships .ko.zst).
# KERNEL= must be a kernel with virtio-blk, ext4 and vfat built in
# (Ubuntu linux-image-generic 6.8 is one).
#
# NLS stays here, not in dawn. Measured 2026-09-11 on that Ubuntu
# generic: CONFIG_VFAT_FS=y and CONFIG_NLS_ISO8859_1=m. dawn's
# mount(ESP, "vfat", data=NULL) uses the default iocharset
# iso8859-1. Without the module the kernel printed
# "FAT-fs (vdb): IO charset iso8859-1 not found" and dawn returned
# EINVAL 22. finit_module of nls_iso8859_1 + nls_utf8 in the initrd
# wrapper is image-build glue for one distro kernel's Kconfig. Dawn
# is TCB and must not learn which charset a particular kernel
# modularised. The next person who moves this into dawn "for
# convenience" puts a kernel-config workaround in the mount stage.
#
# Usage:
#   sh tools/mkboot.sh              # write images under $OUT
#   sh tools/mkboot.sh --run        # also boot and print the console
#   sh tools/mkboot.sh --check      # boot and fail on panic / no city open
#
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT=${OUT:-$ROOT/boot-out}
KERNEL=${KERNEL:-/boot/vmlinuz}
JOBS=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)}

RUN=0
CHECK=0
for a in "$@"; do
    case "$a" in
        --run) RUN=1 ;;
        --check) RUN=1; CHECK=1 ;;
        *) echo "usage: $0 [--run|--check]" >&2; exit 2 ;;
    esac
done

need() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "missing tool: $1" >&2
        exit 1
    }
}
need gcc
need make
need python3
need qemu-system-x86_64
need mkfs.ext4
need mkfs.vfat
need cpio
need dd

if [ ! -r "$KERNEL" ]; then
    echo "KERNEL=$KERNEL is not readable" >&2
    exit 1
fi

BUILD=$OUT/build
mkdir -p "$BUILD"
for f in dawn.c pid1.c nwcheck.c nwcheck_main.c nwspawn.c nwsup.c lids.c \
         lids.h blob.h rescue.c unit_probe.c Makefile bakery/nw-cc.py \
         houses/boom.c houses/badcall.c houses/term.c houses/brick.c \
         houses/slowdie.c
do
    mkdir -p "$BUILD/$(dirname "$f")"
    cp -f "$ROOT/$f" "$BUILD/$f"
done

# dawn.c in this tree already carries MS_REC|MS_PRIVATE and the
# MS_MOVE fallback. mkboot used to patch a d84da58 copy; that
# patcher is gone because the TCB change landed. A checkout
# without the marker is not this tree.

echo "== static build =="
make -C "$BUILD" -j"$JOBS" \
    CFLAGS="-Wall -Wextra -O2 -std=gnu11 -static -ffile-prefix-map=$BUILD=." \
    STAGE="$BUILD/stage"

echo "== binary linkage (must be static) =="
for b in nw-dawn nw-root nw-spawn nw-sup nw-rescue unit-probe; do
    if command -v file >/dev/null 2>&1; then
        out=$(file "$BUILD/$b")
        echo "  $out"
        echo "$out" | grep -q 'statically linked' || {
            echo "FAIL: $b is not static" >&2
            exit 1
        }
    else
        if ldd "$BUILD/$b" >/dev/null 2>&1; then
            echo "FAIL: $b is dynamic" >&2
            ldd "$BUILD/$b" >&2
            exit 1
        fi
        echo "  $b: statically linked (ldd)"
    fi
done

ROOTIMG=$OUT/root.img
ESPIMG=$OUT/esp.img
INITRD=$OUT/initrd.cpio
MNT=$OUT/mnt
mkdir -p "$MNT/root" "$MNT/esp"

echo "== root.img (ext4, 64 MiB) =="
dd if=/dev/zero of="$ROOTIMG" bs=1M count=64 status=none
mkfs.ext4 -q -F -L nw-root "$ROOTIMG"
mount -o loop "$ROOTIMG" "$MNT/root"
mkdir -p "$MNT/root/nw/bin" "$MNT/root/nw/bricks" "$MNT/root/nw/stores" \
         "$MNT/root/efi" "$MNT/root/proc" "$MNT/root/sys" "$MNT/root/dev" \
         "$MNT/root/run" "$MNT/root/tmp" "$MNT/root/sys/fs/cgroup"
cp -f "$BUILD/nw-root" "$BUILD/nw-spawn" "$BUILD/nw-sup" \
      "$BUILD/nw-rescue" "$BUILD/unit-probe" \
      "$BUILD/unit-boom" "$BUILD/unit-badcall" "$BUILD/unit-term" \
      "$BUILD/unit-brick" \
      "$MNT/root/nw/bin/"
chmod 0755 "$MNT/root/nw/bin/"*
sync
umount "$MNT/root"

echo "== esp.img (FAT32, 64 MiB) =="
# 64 MiB so FAT32 is above the mkfs.vfat "suggested minimum clusters"
# warning; the payload is a few kilobytes.
dd if=/dev/zero of="$ESPIMG" bs=1M count=64 status=none
mkfs.vfat -F 32 -n NW-ESP "$ESPIMG"
# Populate it WITHOUT mounting when the host kernel has no FAT driver.
# The guest kernel is what has to mount this image; the build host only
# has to write it. Requiring the host to mount vfat made `make qemu` fail
# on exactly the machines whose missing FAT driver the suite already
# reports as a skip -- `mount: unknown filesystem type 'vfat'`, Error 32,
# before a single byte of the plan was written. mtools writes FAT from
# userspace, so the ESP stays genuine FAT32 rather than being quietly
# substituted with ext4, which would make the boot prove less than it
# claims. Detect the capability the way tests/run.py does, by asking
# /proc/filesystems rather than by trying and reading the error.
esp_stage="$MNT/espstage"
mkdir -p "$esp_stage/slots/A" "$esp_stage/slots/B"
python3 "$ROOT/bakery/nw-cc.py" \
    --probe /nw/bin/unit-probe \
    --out "$esp_stage/slots/A/plan.blob" \
    --lids seccomp
printf 'A\n' > "$esp_stage/slots/current"
if grep -qw vfat /proc/filesystems; then
    mount -o loop "$ESPIMG" "$MNT/esp"
    mkdir -p "$MNT/esp/slots/A" "$MNT/esp/slots/B"
    cp "$esp_stage/slots/A/plan.blob" "$MNT/esp/slots/A/plan.blob"
    cp "$esp_stage/slots/current" "$MNT/esp/slots/current"
    sync
    umount "$MNT/esp"
    echo "  ESP populated by mounting (host kernel has FAT)"
else
    need mcopy
    mmd -i "$ESPIMG" ::/slots ::/slots/A ::/slots/B
    mcopy -i "$ESPIMG" "$esp_stage/slots/A/plan.blob" ::/slots/A/plan.blob
    mcopy -i "$ESPIMG" "$esp_stage/slots/current" ::/slots/current
    echo "  ESP populated with mtools (host kernel has no FAT driver;"
    echo "  the image is still FAT32 and the guest kernel mounts it)"
fi

echo "== initrd.cpio (loader + dawn + nls) =="
IRD=$OUT/ird
rm -rf "$IRD"
mkdir -p "$IRD"

# Boot-glue only. Not TCB. See tools/initrd-init.c.
cp -f "$ROOT/tools/initrd-init.c" "$BUILD/initrd-init.c"
gcc -static -O2 -s -o "$IRD/init" "$BUILD/initrd-init.c"
cp -f "$BUILD/nw-dawn" "$IRD/dawn"
chmod 0755 "$IRD/init" "$IRD/dawn"

KBASE=$(basename "$KERNEL")
KREL=${KBASE#vmlinuz-}
if [ -d "/lib/modules/$KREL" ]; then
    MODDIR=/lib/modules/$KREL
elif [ -d "/lib/modules/$(uname -r)" ]; then
    MODDIR=/lib/modules/$(uname -r)
else
    MODDIR=
fi
if [ -n "$MODDIR" ]; then
    for pair in "nls_iso8859-1:nls_iso8859_1.ko" "nls_utf8:nls_utf8.ko"; do
        src=${pair%%:*}
        dst=${pair##*:}
        for cand in \
            "$MODDIR/kernel/fs/nls/${src}.ko" \
            "$MODDIR/kernel/fs/nls/${src}.ko.zst" \
            "$MODDIR/kernel/fs/nls/${src}.ko.xz"
        do
            [ -f "$cand" ] || continue
            case "$cand" in
                *.zst) zstd -d -c "$cand" > "$IRD/$dst" ;;
                *.xz)  xz   -d -c "$cand" > "$IRD/$dst" ;;
                *)     cp -f "$cand" "$IRD/$dst" ;;
            esac
            break
        done
    done
fi
( cd "$IRD" && find . -print0 | cpio --null -o -H newc --quiet ) > "$INITRD"

APPEND="console=ttyS0,115200n8 ignore_loglevel NW_ROOT=/dev/vda NW_ROOT_FSTYPE=ext4 NW_ESP=/dev/vdb NW_ESP_FSTYPE=vfat"

# TCG in this lab: KVM on the shared host has powered the sandbox
# off before. Production machines with a real /dev/kvm can export
# ACCEL=kvm.
ACCEL=${ACCEL:-tcg}

# One copy-pasteable line. virtio-blk order is load-bearing: first
# drive is /dev/vda (root), second is /dev/vdb (ESP).
QEMU_LINE="qemu-system-x86_64 -machine q35,accel=$ACCEL -cpu max -m 512M -nographic -no-reboot -display none -serial mon:stdio -monitor none -kernel $KERNEL -initrd $INITRD -append \"$APPEND\" -drive file=$ROOTIMG,if=virtio,format=raw,cache=writeback -drive file=$ESPIMG,if=virtio,format=raw,cache=writeback"

{
    echo
    echo "== images =="
    echo "  kernel : $KERNEL"
    echo "  initrd : $INITRD"
    echo "  root   : $ROOTIMG  (ext4, /nw/bin + empty bricks/stores)"
    echo "  esp    : $ESPIMG   (FAT32, slots/A/plan.blob + slots/current=A)"
    echo "  accel  : $ACCEL"
    echo
    echo "== qemu-system-x86_64 command line =="
    echo "$QEMU_LINE"
} | tee "$OUT/qemu.cmd"

if [ "$RUN" -eq 0 ]; then
    exit 0
fi

echo
echo "== boot =="
LOG=$OUT/console.log
rm -f "$LOG"
set +e
timeout --signal=KILL 50s qemu-system-x86_64 \
  -machine q35,accel=$ACCEL \
  -cpu max \
  -m 512M \
  -nographic \
  -no-reboot \
  -display none \
  -serial file:"$LOG" \
  -monitor none \
  -kernel "$KERNEL" \
  -initrd "$INITRD" \
  -append "$APPEND" \
  -drive "file=$ROOTIMG,if=virtio,format=raw,cache=writeback" \
  -drive "file=$ESPIMG,if=virtio,format=raw,cache=writeback"
rc=$?
set -e
echo "== qemu rc=$rc (124/137 = observer timeout; 0 = guest powered off) =="
echo "== console ($LOG) =="
cat "$LOG"
if [ "$CHECK" -eq 1 ]; then
    if ! grep -q '\[nw-root\] city open' "$LOG"; then
        echo "FAIL: no city open" >&2
        exit 1
    fi
    if grep -q 'Attempted to kill init' "$LOG"; then
        echo "FAIL: PID 1 returned" >&2
        exit 1
    fi
    if grep -q '\[nw-root\] HALT:' "$LOG"; then
        echo "FAIL: HALT" >&2
        exit 1
    fi
    echo "== check: city open, no panic, no HALT =="
fi
exit 0
