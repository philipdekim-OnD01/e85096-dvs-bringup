#!/bin/bash
# exynos_mfc.ko (MFC video codec) with the open race fix (exynos-mfc-open.patch, docs 11.80): an open
# between video_register_device() and video_set_drvdata() returned success and the close panicked.
# Built from the kernel tree's own source plus the patch. Mounts as in CLAUDE.md (this directory at /m).
# Result: /work/out/ext-exynos-mfc/exynos_mfc.ko
set -eu
export PATH=/work/prebuilts/clang/host/linux-x86/clang-r450784e/bin:$PATH
K=/work/kernel; EXT=/work/out/ext-exynos-mfc
rm -rf $EXT && cp -r $K/drivers/media/platform/exynos/mfc $EXT
patch -d $EXT -p1 < /m/exynos-mfc-open.patch
make -C $K O=/work/out ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- \
     TARGET_SOC=s5e3830 KCFLAGS=-D__ANDROID_COMMON_KERNEL__ M=$EXT modules
llvm-size $EXT/exynos_mfc.ko /work/out/drivers/media/platform/exynos/mfc/exynos_mfc.ko
