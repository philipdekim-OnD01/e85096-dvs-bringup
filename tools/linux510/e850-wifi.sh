#!/bin/sh
# Bring up the on-chip Maxwell (Samsung SCSC) Wi-Fi of the Exynos 850 and join a network.
#
#   e850-wifi.sh                     load driver, wlan0 up, scan
#   e850-wifi.sh SSID PASSWORD       also write /etc/wpa_supplicant/wpa_supplicant-wlan0.conf,
#                                    start wpa_supplicant@wlan0 and DHCP (systemd-networkd)
#   e850-wifi.sh up                  driver + wlan0 up, rejoin the saved network (used at boot by
#                                    e850-wifi.service)
# Firmware: /vendor/etc/wifi (Linaro Android 13 set, tools/linux510/wifi-fw-linaro.py, docs 11.47).
# The driver asks request_firmware() for "../etc/wifi/mx140.bin", i.e. /lib/firmware/../etc/wifi,
# so /lib/firmware must exist and /lib/etc/wifi points at /vendor/etc/wifi.
set -e
mkdir -p /lib/firmware /lib/etc
[ -e /lib/etc/wifi ] || ln -s /vendor/etc/wifi /lib/etc/wifi
# auto-recovery: restart the WLBT firmware after a FW panic (the AOSP default 1 needs a reboot)
modprobe scsc_mx disable_recovery_handling=0
modprobe scsc_wlan
for i in 1 2 3 4 5; do [ -e /sys/class/net/wlan0 ] && break; sleep 1; done
ip link set wlan0 up          # downloads mx140.bin and boots the WLBT core
if [ "$1" = up ]; then
    systemctl restart systemd-networkd wpa_supplicant@wlan0
    exit 0
fi
if [ $# -lt 2 ]; then
    iw dev wlan0 scan | sed -n 's/^\tSSID: //p; s/^\tsignal: /  /p' | paste - - | sort -k2 -n
    exit 0
fi
mkdir -p /etc/wpa_supplicant /etc/systemd/network
# only the PSK file is private; networkd runs as systemd-network and must read its .network
(umask 077
 { echo "ctrl_interface=/run/wpa_supplicant"; wpa_passphrase "$1" "$2" | grep -v '#psk='; } \
     > /etc/wpa_supplicant/wpa_supplicant-wlan0.conf)
printf '[Match]\nName=wlan0\n\n[Network]\nDHCP=yes\n' > /etc/systemd/network/20-wlan0.network
# no systemd-resolved in the minbase rootfs: plain resolv.conf (gateway, then a public resolver)
printf 'nameserver 192.168.0.1\nnameserver 8.8.8.8\n' > /etc/resolv.conf
systemctl enable --now systemd-networkd wpa_supplicant@wlan0
networkctl reload
networkctl reconfigure wlan0
systemctl restart wpa_supplicant@wlan0
for i in $(seq 30); do ip -4 addr show wlan0 | grep -q inet && break; sleep 1; done
ip -4 addr show wlan0
