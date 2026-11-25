#!/bin/sh
# bundle.sh [OUT.tgz]  (Mac): build the e850-features bundle -- modules in Docker (CLAUDE.md volumes),
# firmware from Linaro (fetch-fw.py, cached in out/fw-cache, checked with fw.sha256), the feature scripts.
# Default OUT: out/e850-features.tgz. Then: deploy.sh HOST feature...   (docs 11.76)
set -e
R=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${1:-$R/out/e850-features.tgz}
S=$R/out/e850-features-stage; B=$S/e850-features
MODS="smsc95xx exynos3830-sound e850-hubpwr scsc-wlan-recovery exynos-mfc"   # modules/<dir>, each with build-module.sh

rm -rf "$S"; mkdir -p "$B/ko"
for m in $MODS; do
    docker run --rm -v ~/e850-a13-build:/work -v e850-kernel:/work/kernel -v e850-out:/work/out \
        -v "$R/modules/$m:/m:ro" e850-build-env:1 bash /m/build-module.sh > "$S/build-$m.log" 2>&1 ||
        { tail -20 "$S/build-$m.log"; exit 1; }
done
docker run --rm -v ~/e850-a13-build:/work -v e850-out:/work/out -v "$B/ko:/x" e850-build-env:1 sh -c '
    S=/work/prebuilts/clang/host/linux-x86/clang-r450784e/bin/llvm-strip
    for k in /work/out/ext-smsc95xx/smsc95xx.ko /work/out/ext-exynos3830-sound/exynos3830_sound.ko \
             /work/out/ext-e850-hubpwr/e850-hubpwr.ko /work/out/ext-scsc-wlan/scsc_wlan.ko \
             /work/out/ext-exynos-mfc/exynos_mfc.ko \
             /work/out/drivers/media/platform/exynos/camera/fimc-is.ko /work/out/ext-s5krc1s/is-cis-s5krc1s.ko \
             /work/out/ext-csis-irqstat/csis-irqstat.ko; do $S --strip-debug -o /x/${k##*/} $k; done
    cat /work/out/include/config/kernel.release' > "$B/KVER"

FW=$R/out/fw-cache
( cd "$FW" 2>/dev/null && shasum -a 256 -c -s "$R/features/host/fw.sha256" ) ||
    { rm -rf "$FW"; python3 "$R/features/host/fetch-fw.py" "$FW"; }
cp -R "$FW" "$B/fw"

cd "$R/features"
cp README.md e850-feature common.sh "$B/"
for f in */feature.sh; do d=${f%/feature.sh}; [ "$d" = host ] || cp -R "$d" "$B/"; done
cp "$R/tools/board/scope/gpiohold.py" "$B/usbhost/"
# dvs (11.97, 11.98): the decoder, the settings panel and the GPU view (11.102) built for the boards' Debian 12, its scripts from tools/,
# the optional banner glyphs and demo recording from out/ (not in git: a system font's glyphs; a recording)
docker run --rm --platform linux/arm64 -v "$R/tools/linux510:/s:ro" -v "$B/dvs:/x" debian:12 sh -c '
    apt-get -qq update && apt-get -qq install -y gcc libc6-dev zlib1g-dev pkg-config libgtk-3-dev libcjson-dev libegl-dev libgles-dev libx11-dev >/dev/null &&
    gcc -O2 -Wall -shared -fPIC -o /x/libdvsview.so /s/dvsview.c -lpthread -lm -lz &&
    gcc -O2 -Wall -o /x/dvspanel /s/dvspanel.c $(pkg-config --cflags --libs gtk+-3.0 libcjson) -lm &&
    gcc -O2 -Wall -o /x/dvsgl /s/dvsgl.c -lEGL -lGLESv2 -lX11' > "$S/build-dvsview.log" 2>&1 ||
    { tail -20 "$S/build-dvsview.log"; exit 1; }
cp "$R/tools/linux510/ov5647-cap.py" "$R/tools/linux510/dvsctl.py" "$R/tools/linux510/dvsctl.html" \
    "$R/tools/linux510/dvsact.py" "$R/tools/linux510/dvsx11.py" "$R/tools/linux510/dvsface.py" "$R/tools/board/dvslive.sh" \
    "$R/tools/board/dvslive.service" "$B/dvs/"
# the face detector (11.99): OpenCV zoo YuNet 2023mar (MIT), fetched once into out/ and checked
YUNET=$R/out/face/face_detection_yunet_2023mar.onnx
[ -f "$YUNET" ] || { mkdir -p "${YUNET%/*}" && curl -sSfL -o "$YUNET" \
    https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx; }
echo "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4  $YUNET" | shasum -a 256 -c -s ||
    { echo "bundle: $YUNET: wrong checksum"; exit 1; }
FACE=$R/out/face/yunet-free.onnx      # its input size made free (dvsface.py --free-size)
[ -f "$FACE" ] || docker run --rm --platform linux/arm64 -v "$R/tools/linux510:/s:ro" -v "${YUNET%/*}:/f" \
    python:3.11-slim sh -c 'pip install -q "onnx<1.17" "numpy<2" &&
    python -I /s/dvsface.py --free-size /f/face_detection_yunet_2023mar.onnx /f/yunet-free.onnx' > "$S/yunet.log" 2>&1 ||
    { tail -5 "$S/yunet.log"; exit 1; }
cp "$FACE" "$B/dvs/face.onnx"
for f in dvsfont.json:dvsfont.json dvs-demo.gz:demo.gz; do [ ! -f "$R/out/${f%:*}" ] || cp "$R/out/${f%:*}" "$B/dvs/${f#*:}"; done
find "$B" -name __pycache__ -prune -exec rm -rf {} +
git -C "$R" describe --always --dirty > "$B/VERSION"
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -czf "$OUT" -C "$S" e850-features
echo "$OUT: $(du -h "$OUT" | cut -f1), kernel $(cat "$B/KVER"), $(cat "$B/VERSION")"
