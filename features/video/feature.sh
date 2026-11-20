#!/bin/sh
# about: MFC hardware video codec (V4L2 /dev/video6 decoder, /dev/video7 encoder)
# Firmware: mfc_fw_v10.21.bin of the Linaro A13 BSP as mfc_fw.bin (host/fetch-fw.py).
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd); . "$ROOT/common.sh"; H=$DEST/video
case $1 in
install)
    install -m 644 "$ROOT/fw/video/mfc_fw.bin" /lib/firmware/; users_to_group video
    unit_install "$H/e850-mfc.service" enable
    systemctl start e850-mfc;;
remove)
    unit_remove e850-mfc.service; rm -f /lib/firmware/mfc_fw.bin;;
status)
    for d in /sys/class/video4linux/video*; do
        case $(cat $d/name) in s5p-mfc-dec|s5p-mfc-enc) echo "/dev/${d##*/} $(cat $d/name)";; esac
    done;;
esac
