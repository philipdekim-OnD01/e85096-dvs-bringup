#!/bin/sh
# wifi-fwswap.sh a13|a10: put the Linaro Android 13 WLBT firmware set (/var/tmp/wifi-a13.tgz: nacho_s612
# S612_E3830_R4-1_Y23W13A, regulatory_db_2_7.bin; tools/linux510/wifi-fw-linaro.py, 11.47) or the saved Android 10
# one (/var/tmp/wifi-a10-vendor) into /vendor/etc/wifi, then
# force one WLBT panic: the recovery loads mx140.bin and the .hcf files again, no reboot. Root.
set -e
W=/vendor/etc/wifi
B=/var/tmp/wifi-a10-vendor
P=/proc/driver/mxman_ctrl0
[ -d $B ] || cp -a $W $B                        # the Android 10 set, once
cd $W
rm -rf mx140 mx140_t mx140.bin mx140_t.bin
case "$1" in
    a13) tar xzf /var/tmp/wifi-a13.tgz -C $W ;;
    a10) cp -a $B/mx140 $B/mx140_t $B/mx140.bin $B/mx140_t.bin $B/slsi_reg_database.bin $W/ ;;
    *) echo "usage: $0 a13|a10"; exit 1 ;;
esac
chown -R root:root $W
md5sum $W/mx140.bin $W/slsi_reg_database.bin
c=$(cat $P/mx_recovery_count)
t0=$(date +%s)
echo 1 > $P/mx_panic
while [ $(( $(date +%s) - t0 )) -lt 60 ]; do
    [ "$(cat $P/mx_recovery_count)" -gt "$c" ] && wpa_cli -i wlan0 status 2>/dev/null | grep -qx wpa_state=COMPLETED &&
        { echo "back after $(( $(date +%s) - t0 )) s: $(wpa_cli -i wlan0 status | grep -E '^(bssid|freq|ip_address)=' | tr '\n' ' ')"; exit 0; }
    sleep 1
done
echo "NOT BACK in 60 s (recovery_count $(cat $P/mx_recovery_count))"; exit 1
