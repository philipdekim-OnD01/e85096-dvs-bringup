#!/bin/sh
# bundle.sh [OUT.tgz]  (Mac): build the e850-features bundle -- modules in Docker (CLAUDE.md volumes),
# firmware from Linaro (fetch-fw.py, cached in out/fw-cache, checked with fw.sha256), the feature scripts.
# Default OUT: out/e850-features.tgz. Then: deploy.sh HOST feature...   (docs 11.76)
set -e
R=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${1:-$R/out/e850-features.tgz}
S=$R/out/e850-features-stage; B=$S/e850-features
MODS="smsc95xx exynos3830-sound e850-hubpwr scsc-wlan-recovery"   # modules/<dir>, each with build-module.sh

rm -rf "$S"; mkdir -p "$B/ko"
for m in $MODS; do
    docker run --rm -v ~/e850-a13-build:/work -v e850-kernel:/work/kernel -v e850-out:/work/out \
        -v "$R/modules/$m:/m:ro" e850-build-env:1 bash /m/build-module.sh > "$S/build-$m.log" 2>&1 ||
        { tail -20 "$S/build-$m.log"; exit 1; }
done
docker run --rm -v ~/e850-a13-build:/work -v e850-out:/work/out -v "$B/ko:/x" e850-build-env:1 sh -c '
    S=/work/prebuilts/clang/host/linux-x86/clang-r450784e/bin/llvm-strip
    for k in /work/out/ext-smsc95xx/smsc95xx.ko /work/out/ext-exynos3830-sound/exynos3830_sound.ko \
             /work/out/ext-e850-hubpwr/e850-hubpwr.ko /work/out/ext-scsc-wlan/scsc_wlan.ko; do $S --strip-debug -o /x/${k##*/} $k; done
    cat /work/out/include/config/kernel.release' > "$B/KVER"

FW=$R/out/fw-cache
( cd "$FW" 2>/dev/null && shasum -a 256 -c -s "$R/features/host/fw.sha256" ) ||
    { rm -rf "$FW"; python3 "$R/features/host/fetch-fw.py" "$FW"; }
cp -R "$FW" "$B/fw"

cd "$R/features"
cp README.md e850-feature common.sh "$B/"
for f in */feature.sh; do d=${f%/feature.sh}; [ "$d" = host ] || cp -R "$d" "$B/"; done
cp "$R/tools/board/scope/gpiohold.py" "$B/usbhost/"
find "$B" -name __pycache__ -prune -exec rm -rf {} +
git -C "$R" describe --always --dirty > "$B/VERSION"
COPYFILE_DISABLE=1 tar --no-xattrs --no-mac-metadata -czf "$OUT" -C "$S" e850-features
echo "$OUT: $(du -h "$OUT" | cut -f1), kernel $(cat "$B/KVER"), $(cat "$B/VERSION")"
