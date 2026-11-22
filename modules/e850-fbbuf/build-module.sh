#!/bin/bash
# Build e850-fbbuf.ko out of tree against the vendor 5.10 build in /work/out (see
# modules/ov5647-pablo/build-module.sh for the container mounts; this directory at /m).
set -eu
export PATH=/work/prebuilts/clang/host/linux-x86/clang-r450784e/bin:$PATH
EXT=/work/out/ext-e850-fbbuf
rm -rf $EXT && mkdir -p $EXT && cp /m/Kbuild /m/e850-fbbuf.c $EXT/
make -C /work/kernel O=/work/out ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- \
     TARGET_SOC=s5e3830 KCFLAGS=-D__ANDROID_COMMON_KERNEL__ M=$EXT modules
ls -l $EXT/e850-fbbuf.ko
