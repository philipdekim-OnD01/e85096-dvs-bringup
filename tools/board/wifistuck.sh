#!/bin/sh
# wifistuck.sh ROUNDS [PROFILES]: per round and profile, a 4 min plltest.py run with one forced WLBT FW panic at
# 140 s (2 min of samples before it). Does a Wi-Fi firmware panic put the capture path in the stuck state (11.46)?
# Root, needs disable_recovery_handling=0 (e850-wifi.sh). Out: /var/tmp/plltest/8-wifi-<profile>-<round>/ and a
# .panic file beside it with the time of the panic.
P=/proc/driver/mxman_ctrl0
for i in $(seq 1 "$1"); do
    for p in ${2:-recommended_00_8b_b0 setfile}; do
        o=/var/tmp/plltest/8-wifi-$p-$i
        ( sleep 140; date +%s.%N > $o.panic; echo 1 > $P/mx_panic ) &
        python3 /var/tmp/plltest.py "$p" 0 4 --out "$o"
        wait
    done
done
echo "wifistuck done: mx_recovery_count $(cat $P/mx_recovery_count)"
