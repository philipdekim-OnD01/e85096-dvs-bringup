#!/bin/sh
# On the board, as any user: unpack mesa-kbase.tgz into ~/gpu, then  sh run-test.sh [frames]
# Offscreen GLES2 check (7 draws read back) and a 1080p full-screen shader timing.
L=$HOME/gpu/mesa-kbase/lib
export LD_LIBRARY_PATH=$L LIBGL_DRIVERS_PATH=$L/dri LD_PRELOAD=$L/libpanfrost_kbase_drm_shim.so
timeout 60 $HOME/gpu/gles-test "${1:-30}"; echo "rc=$?"
