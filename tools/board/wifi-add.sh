#!/bin/sh
# wifi-add.sh SSID (root): one more Wi-Fi network for wlan0 (a phone's hotspot), the ones already there kept; the
# board joins whichever is in range. The passphrase is asked for, never on a command line (shell history, ps).
# wpa_supplicant re-reads its config: the current connection may drop for a few seconds.
# Undo: delete the network={...} block from /etc/wpa_supplicant/wpa_supplicant-wlan0.conf, wpa_cli -i wlan0 reconfigure
set -eu
[ $# -eq 1 ] || { echo "usage: $0 SSID" >&2; exit 2; }
C=${WIFI_CONF:-/etc/wpa_supplicant/wpa_supplicant-wlan0.conf}
printf 'passphrase for %s: ' "$1"
stty -echo
read -r pw
stty echo
echo
# from stdin, not argv; the comment lines (one holds the passphrase in clear) left out
printf '%s\n' "$pw" | wpa_passphrase "$1" | grep -v '^[[:space:]]*#' >> "$C"
[ -n "${WIFI_CONF:-}" ] || wpa_cli -i wlan0 reconfigure
echo "$(grep -c '^network=' "$C") networks in $C"
