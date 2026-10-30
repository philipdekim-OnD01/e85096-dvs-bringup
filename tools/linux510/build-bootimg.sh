#!/bin/sh
# Build boot.img (header v2) for the E850-96 Android 10 LK from the vendor 5.10 kernel build.
#
# Runs inside a native arm64 Ubuntu container (Apple Silicon Docker runs arm64 natively):
#   docker run --rm -v e850-out:/o:ro -v "$PWD/tools/linux510:/t:ro" -v "$PWD/out:/out" \
#       ubuntu:22.04 sh /t/build-bootimg.sh [root=/dev/mmcblk0pN]
#
# Inputs : /o   = kernel build output (Image, dtb, dtbo, *.ko, modules.order)
#          /t   = this directory (init, mkbootimg_v2.py, autoload.deny)
# Outputs: /out = boot.img, initramfs.cpio.gz, merged.dtb, cmdline.txt
set -eu
ROOTARG=${1:-}

apt-get -qq update >/dev/null
apt-get -qq install -y busybox-static kmod device-tree-compiler cpio binutils python3 >/dev/null 2>&1

KVER=$(cat /o/include/config/kernel.release)
IRD=/tmp/initramfs
MODDIR=$IRD/lib/modules/$KVER
rm -rf $IRD && mkdir -p $IRD/bin $MODDIR /out

# 1. busybox + init
cp /bin/busybox $IRD/bin/busybox
cp /t/init $IRD/init && chmod 755 $IRD/init

# 2. modules: all of them, without debug info; autoload list = modules.order minus deny patterns
: > $MODDIR/modules.load
while read -r m; do
    f=/o/$m
    [ -f "$f" ] || { echo "missing $m" >&2; continue; }
    strip --strip-debug -o $MODDIR/$(basename "$m") "$f"
    if ! echo "$(basename "$m")" | grep -qEf /t/autoload.deny; then
        basename "$m" >> $MODDIR/modules.load
    fi
done < /o/modules.order
cp /o/modules.builtin $MODDIR/ 2>/dev/null || true
cp /o/modules.order $MODDIR/
depmod -b $IRD "$KVER"
echo "modules: $(ls $MODDIR/*.ko | wc -l) copied, $(wc -l < $MODDIR/modules.load) autoload"

(cd $IRD && find . | cpio -o -H newc --quiet | gzip -9) > /out/initramfs.cpio.gz

# 3. DTB: SoC dtb + board overlay merged here, so LK sees one complete tree
DTS=/o/arch/arm64/boot/dts/exynos
fdtoverlay -i $DTS/s5e3830.dtb -o /out/merged.dtb $DTS/board/erd/s5e3830-erd3830.dtbo
CMDLINE="console=ttySAC0,115200n8 earlycon=exynos4210,mmio32,0x13820000 maxcpus=1 clocksource=arch_sys_counter clk_ignore_unused printk.devkmsg=on loglevel=8 ignore_loglevel firmware_class.path=/lib/firmware $ROOTARG"
# The stock bootargs also start the watchdog at boot (s3c2410_wdt.tmr_atboot=1); without
# Android's watchdogd nobody pings it, so it is left out here.
fdtput -t s /out/merged.dtb /chosen bootargs "$CMDLINE"
echo "$CMDLINE" > /out/cmdline.txt

# 4. boot.img
python3 /t/mkbootimg_v2.py --kernel /o/arch/arm64/boot/Image --ramdisk /out/initramfs.cpio.gz \
    --dtb /out/merged.dtb -o /out/boot.img      # LK takes bootargs from the DTB /chosen
ls -l /out
