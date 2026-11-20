#!/bin/sh
# about: LAN9514 Ethernet (smsc95xx) as eth0, DHCP, fixed MAC from machine-id
# needs: usbhost
# The LAN9514 sits behind the USB hub, so eth0 exists only while USB host mode is on.
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd); . "$ROOT/common.sh"; H=$DEST/ethernet
case $1 in
install)
    ko_install smsc95xx
    install -m 644 "$H/10-e850-eth.link" "$H/30-e850-eth.network" /etc/systemd/network/
    networkctl reload 2>/dev/null || true
    if [ -d /sys/module/smsc95xx ]; then           # re-add the device so the .link (name, MAC) applies
        ip link set eth0 down 2>/dev/null || true
        rmmod smsc95xx; modprobe smsc95xx
    fi;;
remove)
    rm -f /etc/systemd/network/10-e850-eth.link /etc/systemd/network/30-e850-eth.network
    networkctl reload 2>/dev/null || true; ko_remove smsc95xx;;
status)
    ip -br addr show eth0 2>/dev/null || echo "no eth0 (USB host mode off?)"
    [ -e /sys/class/net/eth0/carrier ] && echo "carrier $(cat /sys/class/net/eth0/carrier 2>/dev/null)";;
esac
