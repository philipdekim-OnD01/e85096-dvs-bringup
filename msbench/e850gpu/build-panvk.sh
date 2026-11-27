#!/bin/sh
# build-panvk.sh: Mesa's Vulkan driver for the Mali-G52 (panvk, Bifrost v7) from the same Mesa tree as the boards' GLES
# driver (tools/gpu/build.sh, 11.82), so NCNN's Vulkan path can run on the E850 GPU (11.118). panvk talks to the same
# panfrost ioctls as the GLES driver, so the same kbase drm-shim (LD_PRELOAD) carries it. Mesa marks panvk as
# unfinished in this version: it only starts with PAN_I_WANT_A_BROKEN_VULKAN_DRIVER=1.
# Runs on the Mac after tools/gpu/build.sh has prepared ~/e850-gpu. Own build dir and prefix, so the GLES build is
# untouched. Output: ~/e850-gpu/out/mesa-vk.tgz (mesa-vk/lib/libvulkan_panfrost.so + the ICD json).
set -e
W=${W:-$HOME/e850-gpu}
docker run --rm -v "$W:/g" -v "$W/uapi:/u:ro" e850-mesa-build:1 sh -c '
set -e
[ -d /g/build-vk ] || meson setup /g/build-vk /g/kbase-g52 --prefix=/opt/mesa-vk --libdir=lib --buildtype=release \
  -Dgallium-drivers= -Dvulkan-drivers=panfrost -Dplatforms= -Dglx=disabled -Degl=disabled -Dgles1=disabled \
  -Dgles2=disabled -Dopengl=false -Dgbm=disabled -Dllvm=disabled -Dtools=drm-shim -Dvalgrind=disabled \
  -Dlibunwind=disabled -Dzstd=disabled -Dbuild-tests=false
ninja -C /g/build-vk
rm -rf /g/out/stage-vk /g/out/mesa-vk && DESTDIR=/g/out/stage-vk ninja -C /g/build-vk install >/dev/null
mv /g/out/stage-vk/opt/mesa-vk /g/out/mesa-vk && rm -rf /g/out/stage-vk
cp /g/build-vk/src/panfrost/drm-shim/libpanfrost_kbase_drm_shim.so /g/out/mesa-vk/lib/ 2>/dev/null || true
'
cd "$W/out" && COPYFILE_DISABLE=1 tar --no-xattrs -czf mesa-vk.tgz mesa-vk && ls -l mesa-vk.tgz
