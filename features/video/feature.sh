#!/bin/sh
# about: MFC hardware video codec (V4L2 /dev/video6 decoder, /dev/video7 encoder)
# Firmware: mfc_fw_v10.21.bin of the Linaro A13 BSP as mfc_fw.bin (host/fetch-fw.py).
# Also installs exynos_mfc with the open race fix (modules/exynos-mfc, docs 11.80): with the stock one an
# open by udev's v4l_id right after the node appears panics the boot. Used from the next boot (never
# rmmod exynos_mfc: an open during the unload panics too).
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd); . "$ROOT/common.sh"; H=$DEST/video
case $1 in
install)
    install -m 644 "$ROOT/fw/video/mfc_fw.bin" /lib/firmware/; users_to_group video
    ko_install exynos_mfc
    unit_install "$H/e850-mfc.service" enable
    if lsmod | grep -q '^exynos_mfc '; then log "video: reboot once so that the fixed exynos_mfc is loaded"
    else systemctl start e850-mfc; fi;;
remove)
    unit_remove e850-mfc.service; ko_remove exynos_mfc; rm -f /lib/firmware/mfc_fw.bin;;
status)
    for d in /sys/class/video4linux/video*; do
        case $(cat $d/name) in s5p-mfc-dec|s5p-mfc-enc) echo "/dev/${d##*/} $(cat $d/name)";; esac
    done;;
esac
