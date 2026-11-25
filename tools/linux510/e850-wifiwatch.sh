#!/bin/sh
# e850-wifiwatch.sh (root, e850-wifiwatch.service): wlan0 with no IPv4 address for WAIT s (180) gets one step,
# then WAIT s more for each next one, until an address comes back:
#   1 wpa_cli disconnect, reconnect (a fresh connect: not reassociate, see below)
#   2 restart wpa_supplicant@wlan0, networkd reconfigure
#   3 only while associated (wpa_state COMPLETED), once per outage: a WLBT firmware restart (forced panic, the
#     driver's recovery: back in about 6 s, docs 11.31/11.33; BT drops with it)
#   then 1, 2 again
# Why (docs 11.98, 11.100): connecting again to the same AP while connected (wpa_supplicant did it one night;
# wpa_cli reassociate does it too: slsi_connect vifStatus=2, same BSSID) leaves the SCSC driver without a queue
# for group-addressed frames ("no fcq for 33:..."), so DHCP's broadcasts never leave: no address for 9 hours.
# A disconnect first makes it a fresh connect. Each step goes to the journal (journalctl -u e850-wifiwatch).
WAIT=${WAIT:-180}
TICK=${TICK:-10}
MX=${MX:-/proc/driver/mxman_ctrl0}
since=$(date +%s) step=0 fw=0
while :; do
    if ip -4 addr show dev wlan0 | grep -q 'inet '; then
        [ $step -gt 0 ] && echo "address back after step $step: $(ip -4 -br addr show dev wlan0)"
        since=$(date +%s) step=0 fw=0
    elif [ $(( $(date +%s) - since )) -ge "$WAIT" ]; then
        since=$(date +%s) step=$(( step % 3 + 1 ))
        st=$(wpa_cli -i wlan0 status 2>/dev/null | sed -n 's/^wpa_state=//p')
        case $step in
        1)  echo "no IPv4 on wlan0 ($st): step 1, disconnect and reconnect"
            wpa_cli -i wlan0 disconnect
            sleep 2
            wpa_cli -i wlan0 reconnect ;;
        2)  echo "still none ($st): step 2, restart wpa_supplicant@wlan0"
            systemctl restart wpa_supplicant@wlan0
            networkctl reconfigure wlan0 ;;
        3)  if [ "$st" = COMPLETED ] && [ $fw = 0 ]; then
                echo "associated, still none: step 3, WLBT firmware restart (count $(cat $MX/mx_recovery_count))"
                fw=1
                echo 1 > $MX/mx_panic
            else
                echo "still none ($st): step 3 skipped (not associated, or the firmware restart was done)"
            fi ;;
        esac
    fi
    sleep "$TICK"
done
