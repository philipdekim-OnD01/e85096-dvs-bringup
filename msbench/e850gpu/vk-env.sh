# . e850gpu/vk-env.sh: Mesa's Vulkan driver for the Mali-G52 (panvk, build-panvk.sh, 11.118) on the vendor kbase,
# through the same drm-shim as gpu-env.sh. NCNN's Vulkan path then sees the GPU. Mesa marks panvk unfinished here.
MESA_VK=${MESA_VK:-$HOME/gpu/mesa-vk}     # no short names: this is sourced into the caller's shell
[ -f "$MESA_VK/icd.json" ] || printf '{"file_format_version": "1.0.0", "ICD": {"library_path": "%s", "api_version": "1.1.211"}}\n' \
    "$MESA_VK/lib/libvulkan_panfrost.so" > "$MESA_VK/icd.json"
# PAN_KBASE_UNCACHED: the shim's CPU-cached BOs (>= 1 MB, for GL readback, 11.82) were cleaned whole on every submit,
# ~16 MB and 2.1 ms per NCNN extract; panvk reports them host-coherent and never invalidates (11.118)
export VK_ICD_FILENAMES=$MESA_VK/icd.json LD_PRELOAD=$MESA_VK/lib/libpanfrost_kbase_drm_shim.so \
    PAN_I_WANT_A_BROKEN_VULKAN_DRIVER=1 PAN_KBASE_UNCACHED=1
