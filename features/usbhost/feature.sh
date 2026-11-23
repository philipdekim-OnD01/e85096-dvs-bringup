#!/bin/sh
# about: USB-A host ports (keyboard, mouse, LAN9514 Ethernet): e850-usbhost on|off
# The micro-USB port is the same OTG controller: while host mode is on there is no ACM/ECM gadget
# (docs 11.64). So it is not on at boot unless you `systemctl enable e850-usbhost`.
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd); . "$ROOT/common.sh"; H=$DEST/usbhost
case $1 in
install)
    ko_install e850-hubpwr
    unit_install "$H/e850-usbhost.service"
    ln -sf "$H/usbhost.sh" /usr/local/sbin/e850-usbhost
    install -D -m 644 "$H/wait-online-any.conf" /etc/systemd/system/systemd-networkd-wait-online.service.d/e850-any.conf
    systemctl daemon-reload
    systemctl is-enabled -q e850-scope 2>/dev/null && log "e850-scope is enabled: it already turns host mode on at boot"
    log "usbhost: 'e850-usbhost on' (until reboot) or 'systemctl enable --now e850-usbhost' (every boot)";;
remove)
    unit_remove e850-usbhost.service; rm -f /usr/local/sbin/e850-usbhost; ko_remove e850-hubpwr
    rm -f /etc/systemd/system/systemd-networkd-wait-online.service.d/e850-any.conf; systemctl daemon-reload;;
status)
    sh "$H/usbhost.sh" status;;
esac
