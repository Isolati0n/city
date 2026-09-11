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
# The fixture houses are globbed, not listed. A hand-written list here is a
# second declaration of what houses/ contains, and it drifted the day a
# fixture was added: `make` inside this build dir died with "No rule to make
# target 'houses/dieterm.c'" while the Makefile's own `all:` had it. One
# place names the fixtures, and it is the directory they live in.
for f in dawn.c pid1.c nwcheck.c nwcheck_main.c nwspawn.c nwsup.c lids.c \
         lids.h blob.h rescue.c unit_probe.c Makefile bakery/nw-cc.py \
         $(cd "$ROOT" && ls houses/*.c)
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

# Resolve the symlink first: KERNEL defaults to /boot/vmlinuz, whose
# basename is "vmlinuz", so stripping the "vmlinuz-" prefix left "vmlinuz"
# and /lib/modules/vmlinuz never exists. The fallback then picked the HOST
# kernel's modules -- which by construction are not the guest's -- staged
# no NLS module, and dawn failed to mount the FAT ESP with errno 22. That
# surfaced as `Attempted to kill init`, so `make qemu` with the default
# KERNEL panicked every time on the machine it was written on and the
# failure read as a dawn bug. tcb-review.
KREAL=$(readlink -f "$KERNEL" 2>/dev/null || echo "$KERNEL")
KBASE=$(basename "$KREAL")
KREL=${KBASE#vmlinuz-}
if [ -d "/lib/modules/$KREL" ]; then
    MODDIR=/lib/modules/$KREL
else
    # No guess. uname -r is the host kernel and can only ever be wrong
    # here; staging its modules would load the wrong NLS into the guest
    # or none at all, and the symptom appears in dawn.
    echo "mkboot: no /lib/modules/$KREL for $KREAL." >&2
    echo "mkboot: KERNEL= must name a kernel whose modules are installed," >&2
    echo "mkboot: or one with CONFIG_NLS_ISO8859_1=y. Not guessing with" >&2
    echo "mkboot: the host's $(uname -r) -- that is not the guest kernel." >&2
    exit 1
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
    # ASSERT THE ARTIFACT, NOT THE DIRECTORY. The guard above checks that
    # /lib/modules/$KREL exists; the loop above checks nothing at all. A
    # headers-only install has the directory and no kernel/ subtree, and a
    # distro that ships NLS elsewhere has both and neither module -- in
    # either case this exits 0 having copied nothing, and the failure
    # arrives much later as
    #     FAT-fs (vdb): IO charset iso8859-1 not found
    #     [dawn] FAIL mount /sysroot/efi errno=22
    #     Kernel panic - not syncing: Attempted to kill init!
    # which is a console that reads as a dawn bug. That misdiagnosis is the
    # entire reason the guard above exists, and covering one arm of it left
    # the other reproducing it verbatim. tcb-review, 2026-09-11.
    #
    # A kernel with CONFIG_NLS_ISO8859_1=y needs no module and is legitimate
    # -- and is indistinguishable from a broken tree by looking at the
    # filesystem, so it must be said rather than detected.
    if [ ! -f "$IRD/nls_iso8859_1.ko" ]; then
        echo "mkboot: $MODDIR has no nls_iso8859-1 module." >&2
        echo "mkboot: vfat needs it to mount the ESP, and without it the" >&2
        echo "mkboot: guest panics in dawn with errno=22 -- which reads as" >&2
        echo "mkboot: a dawn bug and is not one." >&2
        echo "mkboot: If this kernel has CONFIG_NLS_ISO8859_1=y, set" >&2
        echo "mkboot: NW_NLS_BUILTIN=1 to say so; nothing here can tell a" >&2
        echo "mkboot: built-in from a missing one by looking." >&2
        [ -n "${NW_NLS_BUILTIN:-}" ] || exit 1
        echo "mkboot: NW_NLS_BUILTIN=1 set; continuing without the module." >&2
    fi
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
    # An unattended production boot NEVER shuts down. Seeing it is a
    # failure. Until 2026-09-11 NW_HOLD_MS on the kernel command line
    # -- the same channel that carries NW_ROOT -- reached dawn via
    # getenv and forwarded --hold-ms. The city closed 800ms after
    # opening and powered off; every grep above still passed. dawn
    # no longer reads that variable. The check stays: a regression
    # that puts a timer back on the production argv must not print
    # green.
    if grep -q '\[nw-root\] closed' "$LOG"; then
        echo "FAIL: the city closed. PID 1 is not supposed to return on an" >&2
        echo "      unattended boot -- check whether NW_HOLD_MS reached the" >&2
        echo "      kernel command line." >&2
        exit 1
    fi
    if grep -q 'reboot: Power down\|reboot: System halted' "$LOG"; then
        echo "FAIL: the machine powered itself off" >&2
        exit 1
    fi
    if grep -q 'Kernel panic' "$LOG"; then
        echo "FAIL: kernel panic" >&2
        exit 1
    fi
    echo "== check: city open, stayed up, no panic, no HALT =="
fi
exit 0
