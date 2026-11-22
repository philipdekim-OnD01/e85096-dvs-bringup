#!/bin/sh
# Runs inside e850-mesa-build:1. /g/kbase-g52 = Mesa source (patched), /g/build = build dir, /u = kbase uapi.
# Output: /g/out/mesa-kbase (prefix /opt/mesa-kbase), /g/out/gles-test
set -e
gcc -I/g/kbase-g52/src/panfrost/drm-shim -I/u -I/u/jm /t/abi-check.c -o /tmp/abi-check && { /tmp/abi-check > /tmp/abi.txt && tail -1 /tmp/abi.txt; } || { cat /tmp/abi.txt; exit 1; }
[ -d /g/build ] || meson setup /g/build /g/kbase-g52 --prefix=/opt/mesa-kbase --libdir=lib --buildtype=release \
  -Dgallium-drivers=panfrost,kmsro -Dvulkan-drivers= -Dplatforms=x11 -Dglx=dri -Degl=enabled \
  -Dgles1=disabled -Dgles2=enabled -Dopengl=true -Dgbm=enabled -Dllvm=disabled -Dshared-glapi=enabled \
  -Dtools=drm-shim -Dvalgrind=disabled -Dlibunwind=disabled -Dzstd=disabled \
  -Dgallium-vdpau=disabled -Dgallium-va=disabled -Dgallium-xa=disabled -Dgallium-nine=false \
  -Dgallium-opencl=disabled -Dbuild-tests=false -Ddri3=enabled
ninja -C /g/build
rm -rf /g/out/stage /g/out/mesa-kbase && DESTDIR=/g/out/stage ninja -C /g/build install >/dev/null
mv /g/out/stage/opt/mesa-kbase /g/out/mesa-kbase && rm -rf /g/out/stage
gcc -O2 -I/g/out/mesa-kbase/include /t/gles-test.c -L/g/out/mesa-kbase/lib -Wl,-rpath-link,/g/out/mesa-kbase/lib \
  -lEGL -lGLESv2 -o /g/out/gles-test
gcc -O2 -I/g/out/mesa-kbase/include /t/gles-x11.c -L/g/out/mesa-kbase/lib -Wl,-rpath-link,/g/out/mesa-kbase/lib \
  -lEGL -lGLESv2 -lX11 -lm -o /g/out/gles-x11
gcc -O2 -I/g/out/mesa-kbase/include /t/gles-fb.c -L/g/out/mesa-kbase/lib -Wl,-rpath-link,/g/out/mesa-kbase/lib \
  -lEGL -lGLESv2 -lm -o /g/out/gles-fb
gcc -O2 -I/g/out/mesa-kbase/include -I/usr/include/libdrm /t/gles-kms.c -L/g/out/mesa-kbase/lib -Wl,-rpath-link,/g/out/mesa-kbase/lib \
  -lEGL -lGLESv2 -lgbm -ldrm -lm -o /g/out/gles-kms
