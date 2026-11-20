#!/bin/sh
# about: Bluetooth (WLBT firmware via scsc_bt) as BlueZ hci0 through /dev/vhci
# BD address comes from the chip ID. Needs the WLBT firmware of e850-wifi (bt.hcf is in it).
# Also installs scsc_wlan with the recovery retry (modules/scsc-wlan-recovery, docs 11.78): with the
# stock one a WLBT firmware panic while BT is open leaves Wi-Fi down until reboot. It is used from the
# next boot (reloading scsc_wlan in a running system panics the kernel).
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd); . "$ROOT/common.sh"; H=$DEST/bt
case $1 in
install)
    apt_need bluez
    ko_install scsc_wlan
    unit_install "$H/e850-bt.service" enable
    if sh "$H/bt-check.sh" 2>/dev/null; then systemctl restart e850-bt
    else log "bt: reboot once so that the fixed scsc_wlan is loaded (until then BT stays off)"; fi;;
remove)
    unit_remove e850-bt.service; ko_remove scsc_wlan
    log "bt: scsc_bt and hci_vhci stay loaded until reboot";;
status)
    systemctl is-active e850-bt || true
    hciconfig hci0 2>/dev/null | sed -n '1,3p';;
esac
