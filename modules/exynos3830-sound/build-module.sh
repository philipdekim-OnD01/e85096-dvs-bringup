#!/bin/bash
# exynos3830_sound.ko (the ABOX sound card) with the DT child order fix (exynos3830-sound-order.patch, docs
# 11.76): the vendor driver matches its link table to the sound node's children by position, and our
# fdtoverlay-merged DTB has them in reverse. Built from the kernel tree's own source plus the patch.
# Mounts as in CLAUDE.md (this directory at /m). Result: /work/out/ext-exynos3830-sound/exynos3830_sound.ko
set -eu
export PATH=/work/prebuilts/clang/host/linux-x86/clang-r450784e/bin:$PATH
K=/work/kernel; EXT=/work/out/ext-exynos3830-sound
rm -rf $EXT && mkdir -p $EXT && cp $K/sound/soc/samsung/exynos3830_sound.c $EXT/
patch -d $EXT -p4 < /m/exynos3830-sound-order.patch
sed -i "s#\"../codecs/madera.h\"#\"$K/sound/soc/codecs/madera.h\"#" $EXT/exynos3830_sound.c
echo 'obj-m := exynos3830_sound.o' > $EXT/Kbuild
make -C $K O=/work/out ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- \
     TARGET_SOC=s5e3830 KCFLAGS=-D__ANDROID_COMMON_KERNEL__ M=$EXT modules
ls -l $EXT/exynos3830_sound.ko
