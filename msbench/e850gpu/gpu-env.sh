# . e850gpu/gpu-env.sh: the boards' GLES driver for TFLite GL (11.117), the same as tools/gpu/run-test.sh: Mesa
# panfrost from ~/gpu/mesa-kbase with the drm-shim that turns panfrost ioctls into vendor kbase ioctls (11.82), and
# EGL without a window system (TFLite asks for EGL_DEFAULT_DISPLAY). Source it before Python starts (LD_PRELOAD).
MESA_KBASE=${MESA_KBASE:-$HOME/gpu/mesa-kbase/lib}     # no short names: this is sourced into the caller's shell
export LD_LIBRARY_PATH=$MESA_KBASE${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH} LIBGL_DRIVERS_PATH=$MESA_KBASE/dri \
    LD_PRELOAD=$MESA_KBASE/libpanfrost_kbase_drm_shim.so EGL_PLATFORM=surfaceless
