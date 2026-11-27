#!/bin/sh
# Build boot.img (header v2) for the E850-96 Android 10 LK from the vendor 5.10 kernel build.
#
# Runs inside a native arm64 Ubuntu container (Apple Silicon Docker runs arm64 natively):
#   docker run --rm -v e850-out:/o:ro -v "$PWD/tools/linux510:/t:ro" -v "$PWD/out:/out" \
#       -v ~/Desktop/E850_Android10_backup_20261031:/b:ro \
#       ubuntu:22.04 sh /t/build-bootimg.sh [root=PARTLABEL=userdata]
#   installer boot image (docs 11.120): sh /t/build-bootimg.sh "root=PARTLABEL=userdata e850.install"
#
# Inputs : /o   = kernel build output (Image, dtb, dtbo, *.ko, modules.order)
#          /t   = this directory (init, helper scripts, mkbootimg_v2.py, autoload.deny,
#                 vendor_boot_module_order_s5e3830.cfg, modules.stage1)
#          /b   = optional: the board backup folder; its mmcblk0p15.img.gz (stock Android 10
#                 boot image) is embedded so `restore-android` works from the rescue shell
# Outputs: /out = boot.img, initramfs.cpio.gz, merged.dtb, cmdline.txt
set -eu
ROOTARG=${1:-}

apt-get -qq update >/dev/null
apt-get -qq install -y busybox-static kmod device-tree-compiler cpio binutils python3 gcc libc6-dev >/dev/null 2>&1

KVER=$(cat /o/include/config/kernel.release)
IRD=/tmp/initramfs
MODDIR=$IRD/lib/modules/$KVER
rm -rf $IRD && mkdir -p $IRD/bin $IRD/dev $MODDIR /out

# 1. busybox + init + rescue helpers; /dev/console so the kernel can open the initial console
cp /bin/busybox $IRD/bin/busybox
cp /t/init $IRD/init && chmod 755 $IRD/init
for h in part-dev restore-android to-fastboot e850-install; do cp /t/$h $IRD/bin/$h && chmod 755 $IRD/bin/$h; done
gcc -O2 -static -o $IRD/bin/simg-write /t/simg-write.c     # for e850-install
cp /t/rebootarg/rebootarg $IRD/bin/rebootarg && chmod 755 $IRD/bin/rebootarg
mknod -m 600 $IRD/dev/console c 5 1
if [ -f /b/mmcblk0p15.img.gz ]; then
    mkdir -p $IRD/restore && cp /b/mmcblk0p15.img.gz $IRD/restore/boot-android10.img.gz
else
    echo "no /b/mmcblk0p15.img.gz: restore-android will not work" >&2
fi

# 2. modules: all of them, without debug info.
#    Autoload order = the BSP's vendor_boot order (vendor_boot_module_order_s5e3830.cfg: chipid,
#    reboot, clocks, MCT timer, UART, pinctrl, I2C first), then the rest of modules.order,
#    minus the deny patterns.
while read -r m; do
    f=/o/$m
    [ -f "$f" ] || { echo "missing $m" >&2; continue; }
    strip --strip-debug -o $MODDIR/$(basename "$m") "$f"
done < /o/modules.order
# out-of-tree modules (e.g. modules/ov5647-pablo -> /o/ext-ov5647): copied, never autoloaded
for f in /o/ext-*/*.ko; do
    [ -f "$f" ] && strip --strip-debug -o $MODDIR/$(basename "$f") "$f" || true
done
{ grep -v '^#' /t/vendor_boot_module_order_s5e3830.cfg; sed 's|.*/||' /o/modules.order; } |
    awk 'NF && !seen[$0]++' | grep -vEf /t/autoload.deny > $MODDIR/modules.load
grep -v '^#' /t/modules.stage1 > $MODDIR/modules.stage1
cp /o/modules.builtin $MODDIR/ 2>/dev/null || true
cp /o/modules.order $MODDIR/
depmod -b $IRD "$KVER"
echo "modules: $(ls $MODDIR/*.ko | wc -l) copied, $(wc -l < $MODDIR/modules.load) autoload"

(cd $IRD && find . | cpio -o -H newc --quiet | gzip -9) > /out/initramfs.cpio.gz

# 3. DTB: SoC dtb + board overlay merged here, so LK sees one complete tree
DTS=/o/arch/arm64/boot/dts/exynos
fdtoverlay -i $DTS/s5e3830.dtb -o /out/merged.dtb $DTS/board/erd/s5e3830-erd3830.dtbo
# CPU ids as the board's (Android 10 era) BL31 reads them for PSCI CPU_ON: cluster<<8 | core
for c in 0 1 2 3 4 5 6 7; do fdtput -t x /out/merged.dtb /cpus/cpu@000$c reg 0 $(printf %x $(( (c / 4) << 8 | c % 4 ))); done
# Kernel messages go out through earlycon only (keep_bootcon keeps it after boot); there is no
# tty console (console=ram, as in the BSP), because opening ttySAC0 from user space hung the
# board (docs 7.15). The login is the USB ACM gadget instead.
# cpuidle.off=1: without Android's exynos-cpupm setup, the first CPU idle entry hung the SoC
# (docs 7.17: 6 s with stage-1 modules, right after the last module with all of them).
# panic=0: stay in the panic instead of a normal reboot (which LK boots straight into Linux
# again); the hardware watchdog then resets the SoC and LK stops in fastboot (docs 7.18).
# HDMI text console: 16x32 font (2x the 8x16 default) and white text (VT palette entry 7, the
# default foreground, from light grey 170,170,170 to 255,255,255)
VTPAL="vt.default_red=0,170,0,170,0,170,0,255,85,255,85,255,85,255,85,255 vt.default_grn=0,0,170,85,0,0,170,255,85,85,255,255,85,85,255,255 vt.default_blu=0,0,0,0,170,170,170,255,85,85,85,85,255,255,255,255"
# cgroup_disable=cpu: the vendor scheduler (ems.ko) indexes 9-entry arrays with the cpu cgroup id
# (css->id - 1). Once systemd creates more cpu cgroups (logind sessions, user@.service delegates
# cpu), moving a runnable task panics in freqboost_can_attach (docs 11.80).
CMDLINE="console=ram keep_bootcon earlycon=exynos4210,mmio32,0x13820000 cpuidle.off=1 panic=0 fbcon=font:TER16x32 $VTPAL clocksource=arch_sys_counter clk_ignore_unused printk.devkmsg=on loglevel=8 ignore_loglevel firmware_class.path=/lib/firmware cgroup_disable=cpu $ROOTARG"
# The stock bootargs also start the watchdog at boot (s3c2410_wdt.tmr_atboot=1); without
# Android's watchdogd nobody pings it, so it is left out here.
fdtput -t s /out/merged.dtb /chosen bootargs "$CMDLINE"
echo "$CMDLINE" > /out/cmdline.txt

# 4. boot.img
python3 /t/mkbootimg_v2.py --kernel /o/arch/arm64/boot/Image --ramdisk /out/initramfs.cpio.gz \
    --dtb /out/merged.dtb -o /out/boot.img      # LK takes bootargs from the DTB /chosen
ls -l /out
