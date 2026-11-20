#!/bin/bash
# Build scsc_wlan.ko out of tree with the recovery retry (docs 11.78), against the vendor 5.10 build in
# /work/out. Mounts as in CLAUDE.md, this directory at /m. NOPATCH=1 builds the vendor source as it is
# (to compare with the in-tree build). Result: /work/out/ext-scsc-wlan/scsc_wlan.ko
set -eu
export PATH=/work/prebuilts/clang/host/linux-x86/clang-r450784e/bin:$PATH
K=/work/kernel; EXT=/work/out/ext-scsc-wlan
rm -rf $EXT && cp -r $K/drivers/net/wireless/scsc $EXT
# the in-tree Makefile only adds this include relative to the tree; also build only scsc_wlan
sed -i "s|-Idrivers/misc/samsung/scsc|-I$K/drivers/misc/samsung/scsc|; /scsc_wifilogger/d" $EXT/Makefile
[ "${NOPATCH:-}" = 1 ] || patch -d $EXT -p5 < /m/scsc-wlan-recovery.patch
make -C $K O=/work/out ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- \
     TARGET_SOC=s5e3830 KCFLAGS=-D__ANDROID_COMMON_KERNEL__ M=$EXT modules
llvm-size $EXT/scsc_wlan.ko /work/out/drivers/net/wireless/scsc/scsc_wlan.ko
