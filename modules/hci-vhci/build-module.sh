#!/bin/bash
# Build hci_vhci.ko (BlueZ's virtual HCI, /dev/vhci) out of tree from the kernel's own
# drivers/bluetooth/hci_vhci.c: the vendor config has CONFIG_BT=y but CONFIG_BT_HCIVHCI off.
# tools/board/e850-bt.py bridges /dev/vhci to the WLBT BT driver (/dev/scsc_h4_0). Container
# mounts as in modules/ov5647-pablo/build-module.sh, this directory at /m.
set -eu
export PATH=/work/prebuilts/clang/host/linux-x86/clang-r450784e/bin:$PATH
EXT=/work/out/ext-hci-vhci
rm -rf $EXT && mkdir -p $EXT && cp /m/Kbuild /work/kernel/drivers/bluetooth/hci_vhci.c $EXT/
make -C /work/kernel O=/work/out ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- \
     TARGET_SOC=s5e3830 KCFLAGS=-D__ANDROID_COMMON_KERNEL__ M=$EXT modules
ls -l $EXT/hci_vhci.ko
