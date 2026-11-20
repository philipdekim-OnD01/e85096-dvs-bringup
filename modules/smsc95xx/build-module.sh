#!/bin/bash
# smsc95xx.ko (LAN9514 Ethernet on the E850-96 USB hub, 0424:ec00) out of tree. The vendor defconfig leaves
# CONFIG_USB_NET_SMSC95XX unset; usbnet and mii are built in, so the stock driver source builds as is.
# Mounts as in CLAUDE.md (this directory at /m). Result: /work/out/ext-smsc95xx/smsc95xx.ko
set -eu
export PATH=/work/prebuilts/clang/host/linux-x86/clang-r450784e/bin:$PATH
EXT=/work/out/ext-smsc95xx
rm -rf $EXT && mkdir -p $EXT
cp /work/kernel/drivers/net/usb/smsc95xx.c /work/kernel/drivers/net/usb/smsc95xx.h $EXT/
echo 'obj-m := smsc95xx.o' > $EXT/Kbuild
make -C /work/kernel O=/work/out ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- \
     TARGET_SOC=s5e3830 KCFLAGS=-D__ANDROID_COMMON_KERNEL__ M=$EXT modules
ls -l $EXT/smsc95xx.ko
