#!/bin/sh
# wifi-pskeep.sh: keep Wi-Fi power save off (11.47 test: do the FW Data Aborts stop without it?). Root.
# Every 30 s: power save back to off if the driver turned it on (a FW recovery or a reconnect), and each
# change of mx_recovery_count, to /var/tmp/wifi-ps.log. Undo: stop the unit, iw dev wlan0 set power_save on.
P=/proc/driver/mxman_ctrl0
L=/var/tmp/wifi-ps.log
now() { date -u +%FT%TZ; }
c=$(cat $P/mx_recovery_count)
echo "$(now) start: recovery_count $c, $(iw dev wlan0 get power_save 2>&1)" >> $L
while :; do
    n=$(cat $P/mx_recovery_count)
    [ "$n" != "$c" ] && { echo "$(now) recovery_count $n, last panic $(tr -d '\n' < $P/mx_lastpanic)" >> $L; c=$n; }
    case "$(iw dev wlan0 get power_save 2>/dev/null)" in
        *on*) iw dev wlan0 set power_save off 2>>$L && echo "$(now) power_save set off" >> $L ;;
    esac
    sleep 30
done
