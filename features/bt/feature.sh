#!/bin/sh
# about: Bluetooth (WLBT firmware via scsc_bt) as BlueZ hci0 through /dev/vhci
# BD address comes from the chip ID. Needs the WLBT firmware of e850-wifi (bt.hcf is in it).
# Also installs scsc_wlan with the recovery retry (modules/scsc-wlan-recovery, docs 11.78): with the
# stock one a WLBT firmware panic while BT is open leaves Wi-Fi down until reboot. It is used from the
# next boot (reloading scsc_wlan in a running system panics the kernel).
# Audio to Bluetooth speakers: bluez-alsa (A2DP source, ALSA PCM "bluealsa"), see README (11.89); SBC in Dual
# Channel (452 kbps instead of 279) and the speex resampler (11.98).
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd); . "$ROOT/common.sh"; H=$DEST/bt
case $1 in
install)
    install -D -m 644 "$H/bluealsa-no-userns.conf" /etc/systemd/system/bluealsa.service.d/e850-no-userns.conf
    install -m 644 "$H/bluealsa-sbc.conf" /etc/systemd/system/bluealsa.service.d/e850-sbc.conf
    install -D -m 644 "$H/60-e850-rate.conf" /etc/alsa/conf.d/60-e850-rate.conf
    apt_need bluez bluez-alsa-utils libasound2-plugin-bluez libasound2-plugins
    systemctl daemon-reload; systemctl restart bluealsa || true
    # the A2DP sink player (phone -> board) has the same PrivateUsers=true and is not used: off (11.92)
    systemctl disable --now bluealsa-aplay 2>/dev/null || true
    ko_install scsc_wlan
    unit_install "$H/e850-bt.service" enable
    if sh "$H/bt-check.sh" 2>/dev/null; then systemctl restart e850-bt
    else log "bt: reboot once so that the fixed scsc_wlan is loaded (until then BT stays off)"; fi;;
remove)
    unit_remove e850-bt.service; ko_remove scsc_wlan
    rm -rf /etc/systemd/system/bluealsa.service.d /etc/alsa/conf.d/60-e850-rate.conf; systemctl daemon-reload
    log "bt: scsc_bt and hci_vhci stay loaded until reboot";;
status)
    systemctl is-active e850-bt || true
    hciconfig hci0 2>/dev/null | sed -n '1,3p';;
esac
