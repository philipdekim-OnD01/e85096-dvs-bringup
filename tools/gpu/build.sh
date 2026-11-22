#!/bin/sh
# Mesa panfrost on the vendor Mali driver (kbase r38p1) for the board's Debian 12 arm64 (11.82).
# Source: Icecream95's kbase-g52 branch (drm-shim that turns panfrost ioctls into kbase ioctls) + our patch.
# Output: $W/out/mesa-kbase.tgz (mesa-kbase/ and the gles-* test programs). Usage on the Mac: sh tools/gpu/build.sh
set -e
H=$(cd "$(dirname "$0")" && pwd)
W=${W:-$HOME/e850-gpu}
REV=87d63f238f7eee7efe3bf3e02cf78f9bd5b58058
mkdir -p "$W/out" "$W/uapi"
if [ ! -d "$W/kbase-g52" ]; then
    git clone --depth 1 -b kbase-g52 https://gitlab.freedesktop.org/icecream95/mesa.git "$W/kbase-g52"
    [ "$(git -C "$W/kbase-g52" rev-parse HEAD)" = $REV ] || { echo "kbase-g52 moved off $REV"; exit 1; }
    git -C "$W/kbase-g52" apply "$H/mesa-kbase-g52-e850.patch"
fi
docker run --rm -v e850-kernel:/k:ro -v "$W/uapi:/u" ubuntu:22.04 sh -c 'cp -r /k/include/uapi/gpu/arm/b_r38p1/* /u/'
docker build -q -t e850-mesa-build:1 "$H" >/dev/null
docker run --rm -v "$W:/g" -v "$W/uapi:/u:ro" -v "$H:/t:ro" e850-mesa-build:1 sh /t/build-mesa.sh
cd "$W/out" && COPYFILE_DISABLE=1 tar --no-xattrs -czf mesa-kbase.tgz mesa-kbase gles-test gles-x11 gles-fb gles-kms && ls -l mesa-kbase.tgz
