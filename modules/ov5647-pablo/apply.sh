#!/bin/sh
# Add the OV5647 Pablo CIS driver and the E850-96 CN4 overlay to the Linaro exynos-5.10 kernel tree.
#
# usage: apply.sh /path/to/kernel   (run again safely; every step checks before editing)
#
# The board dts can include only one CN4 camera overlay. This script switches the include
# from the S5KRC1S (DVS) overlay to the OV5647 one; switch back by editing the same line in
# arch/arm64/boot/dts/exynos/board/erd/s5e3830-erd3830.dts.
set -eu
K=${1:?usage: apply.sh /path/to/kernel}
HERE=$(cd "$(dirname "$0")" && pwd)
CIS=drivers/media/platform/exynos/camera/sensor/module_framework/cis
BOARD=arch/arm64/boot/dts/exynos/board/erd/s5e3830-erd3830.dts
SENSOR_ID=219          # first free number in the "201~255: Other vendor sensors" range

cp -v "$HERE"/files/$CIS/is-cis-ov5647* "$K/$CIS/"
cp -v "$HERE"/files/arch/arm64/boot/dts/exynos/board/erd/camera/*ov5647* \
      "$K/arch/arm64/boot/dts/exynos/board/erd/camera/"

for h in include/dt-bindings/camera/exynos_is_dt.h \
         drivers/media/platform/exynos/camera/include/exynos_is_dt.h; do
    grep -q SENSOR_NAME_OV5647 "$K/$h" ||
        sed -i "s|^#define SENSOR_NAME_OV32A1Q.*|&\n#define SENSOR_NAME_OV5647\t\t$SENSOR_ID|" "$K/$h"
    grep -n SENSOR_NAME_OV5647 "$K/$h"
done

grep -q CAMERA_CIS_OV5647_OBJ "$K/$CIS/Kconfig" || cat >> "$K/$CIS/Kconfig" <<'EOF'

config CAMERA_CIS_OV5647_OBJ
	tristate "Use OV5647 camera cis"
	depends on CAMERA_CIS_SELECT
	default n
	help
	  OmniVision OV5647 (Raspberry Pi camera v1 style) CIS driver, RAW10 2-lane.
EOF
grep -q is-cis-ov5647.o "$K/$CIS/Makefile" ||
    echo 'obj-$(CONFIG_CAMERA_CIS_OV5647_OBJ) += is-cis-ov5647.o' >> "$K/$CIS/Makefile"

if grep -q 's5e3830-e850-s5krc1s-camera.dtsi' "$K/$BOARD"; then
    sed -i 's|#include "camera/s5e3830-e850-s5krc1s-camera.dtsi"|#include "camera/s5e3830-e850-ov5647-camera.dtsi"|' "$K/$BOARD"
elif ! grep -q 's5e3830-e850-ov5647-camera.dtsi' "$K/$BOARD"; then
    sed -i 's|#include "s5e3830-board_common.dtsi"|&\n#include "camera/s5e3830-e850-ov5647-camera.dtsi"|' "$K/$BOARD"
fi
grep -n 'camera/' "$K/$BOARD"

DEFCONFIG=arch/arm64/configs/erd3830_t_defconfig
grep -q CONFIG_CAMERA_CIS_OV5647_OBJ "$K/$DEFCONFIG" ||
    printf '\n# OV5647\nCONFIG_CAMERA_CIS_OV5647_OBJ=m\n' >> "$K/$DEFCONFIG"
echo "applied to $K"
