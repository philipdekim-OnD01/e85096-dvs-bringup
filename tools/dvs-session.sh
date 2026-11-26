#!/usr/bin/env bash
# Run ON THE E850-96 (Linux, root): load both modules, start the CSIS WDMA
# capture, then apply the NRV sensor recipe (which ends with stream on).
#
#   tools/dvs-session.sh SETTINGS.txt OUT.dvsc [FRAMES]
#
# SETTINGS.txt: an NRV DELTA SDK file, e.g. Delta_01_1000FPS.txt from
# https://github.com/nrvcorp/DELTA_SDK. Start with a low-FPS recipe: CN4 is
# 15-pin (2 lanes) and the dvs driver rewrites 0x3915 to 2-lane mode.
set -euo pipefail
settings=${1:?usage: dvs-session.sh SETTINGS.txt OUT.dvsc [FRAMES]}
out=${2:?usage: dvs-session.sh SETTINGS.txt OUT.dvsc [FRAMES]}
frames=${3:-1000}
root=$(cd "$(dirname "$0")/.." && pwd)
capture=${CAPTURE:-$root/tools/dvs-decoder/build/dvs-capture}
dev=${CSIS_DEV:-/dev/e850-csis2}

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -x $capture ]] || { echo "build $capture first (make -C tools/dvs-decoder)" >&2; exit 1; }

lsmod | grep -q '^dvs ' || insmod "${DVS_KO:-$root/modules/dvs/build/dvs.ko}"
lsmod | grep -q '^e850_csis_dma ' || insmod "${CSIS_KO:-$root/modules/csis/build/e850-csis-dma.ko}"

sensor=$(ls -d /sys/bus/i2c/drivers/dvs/*-0010 2>/dev/null | head -1 || true)
[[ -n $sensor ]] || { echo "DVS I2C device not bound; check dmesg" >&2; exit 1; }

for _ in $(seq 60); do [[ $(cat "$sensor/ready") == 1 ]] && break; sleep 1; done
[[ $(cat "$sensor/ready") == 1 ]] || { echo "sensor not ready (I2C/RESET); see dmesg" >&2; exit 1; }
# The CSIS0 fallback DT node creates /dev/e850-csis0 instead.
[[ -e $dev || -n ${CSIS_DEV:-} ]] || dev=/dev/e850-csis0
[[ -e $dev ]] || { echo "$dev missing; is the CSIS DT node enabled?" >&2; exit 1; }

mkdir -p /lib/firmware/nrv
fw=nrv/$(basename "$settings")
cp "$settings" "/lib/firmware/$fw"

"$capture" -d "$dev" -n "$frames" -t 30 -o "$out" &
cap=$!
sleep 1
echo "$fw" > "$sensor/load_settings"
echo "sensor: recipe applied, MODE_SELECT=$(cat "$sensor/stream")"
wait "$cap" || true
echo 0 > "$sensor/stream" || true

echo "--- dmesg (csis/dvs) ---"
dmesg | grep -E 'e850-csis|csis@|dvs ' | tail -n 15
echo "--- decode: raw payload survey ---"
"$root/tools/dvs-decoder/build/dvs-decode" -f raw -n 5 "$out" || true
