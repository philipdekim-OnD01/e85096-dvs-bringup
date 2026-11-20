#!/bin/sh
# e850-bt ExecCondition (fails = unit skipped, not restarted): start BT only when the scsc_wlan with the recovery retry is the loaded one
# (docs 11.78). e850-wifi loads it at boot, so: extra/scsc_wlan.ko installed before this boot.
K=/lib/modules/$(uname -r)/extra/scsc_wlan.ko
boot=$(( $(date +%s) - $(cut -d. -f1 /proc/uptime) ))
[ -f "$K" ] && [ "$(stat -c %Y "$K")" -lt "$boot" ] && exit 0
echo "e850-bt: the fixed scsc_wlan is not loaded yet (reboot once after installing); BT stays off" >&2
exit 1
