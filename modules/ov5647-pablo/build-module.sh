#!/bin/bash
# Build is-cis-ov5647.ko out of tree against the vendor 5.10 build in /work/out.
# Runs inside e850-build-env:1 with ~/e850-a13-build at /work, the e850-kernel volume at
# /work/kernel, the e850-out volume at /work/out, and this directory at /m:
#
#   docker run --rm -v ~/e850-a13-build:/work -v e850-kernel:/work/kernel -v e850-out:/work/out \
#       -v "$PWD/modules/ov5647-pablo:/m:ro" e850-build-env:1 bash /m/build-module.sh
#
# Output: /work/out/ext-ov5647/is-cis-ov5647.ko
set -eu
export PATH=/work/prebuilts/clang/host/linux-x86/clang-r450784e/bin:$PATH
EXT=/work/out/ext-ov5647
CIS=/m/files/drivers/media/platform/exynos/camera/sensor/module_framework/cis

rm -rf $EXT && mkdir -p $EXT
cp /m/Kbuild $CIS/is-cis-ov5647.c $CIS/is-cis-ov5647.h $CIS/is-cis-ov5647-setA.h $EXT/

# For external modules modpost reads all of /work/out/Module.symvers, which already lists the
# fimc-is.ko exports; passing them again via KBUILD_EXTRA_SYMBOLS fails with "exported twice".

make -C /work/kernel O=/work/out ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-gnu- \
     M=$EXT modules
ls -l $EXT/is-cis-ov5647.ko
