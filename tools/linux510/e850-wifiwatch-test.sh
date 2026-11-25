#!/bin/sh
# e850-wifiwatch-test.sh (Mac or Linux): e850-wifiwatch.sh with stand-in ip, wpa_cli, systemctl, networkctl and
# mx_panic, WAIT 2 s: the steps in order, step 3 once and only while associated, the address coming back.
set -u
D=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
trap 'kill $pid 2>/dev/null; rm -rf "$T"' EXIT
mkdir "$T/bin" "$T/mx"
echo 0 > "$T/mx/mx_recovery_count"
cat > "$T/bin/ip" <<EOF
#!/bin/sh
[ -e $T/addr ] && echo "    inet 192.168.0.9/24 brd 192.168.0.255 scope global wlan0"
exit 0
EOF
cat > "$T/bin/wpa_cli" <<EOF
#!/bin/sh
case "\$*" in *status*) echo "wpa_state=\$(cat $T/state)" ;; *) echo "wpa_cli \$*" >> $T/log ;; esac
EOF
for c in systemctl networkctl; do printf '#!/bin/sh\necho "%s $*" >> %s/log\n' $c "$T" > "$T/bin/$c"; done
chmod +x "$T/bin/"*
: > "$T/log"
fail=0
check() {    # what, want (grep -E on the run's output + actions), file
    if grep -Eq "$2" "$3"; then echo "ok   $1"; else echo "FAIL $1"; fail=1; fi
}
run() {      # seconds
    PATH="$T/bin:$PATH" WAIT=2 TICK=1 MX="$T/mx" sh "$D/e850-wifiwatch.sh" >> "$T/log" 2>&1 & pid=$!
    sleep "$1"
    kill $pid
}

echo COMPLETED > "$T/state"
run 22       # a step every 2-3 s (whole seconds) + 2 s in step 1: 1 2 3 1 2 3
check "step 1 disconnects" "wpa_cli -i wlan0 disconnect" "$T/log"
check "step 1 reconnects" "wpa_cli -i wlan0 reconnect" "$T/log"
check "step 2 restarts wpa_supplicant" "systemctl restart wpa_supplicant@wlan0" "$T/log"
check "step 2 reconfigures networkd" "networkctl reconfigure wlan0" "$T/log"
check "step 3 restarts the firmware" "step 3, WLBT firmware restart" "$T/log"
check "mx_panic written" "1" "$T/mx/mx_panic"
[ "$(grep -c "reconnect$" "$T/log")" -ge 2 ] && echo "ok   ladder starts over" || { echo "FAIL ladder starts over"; fail=1; }
[ "$(grep -c 'WLBT firmware restart' "$T/log")" = 1 ] && grep -q "firmware restart was done" "$T/log" &&
    echo "ok   firmware restart once per outage" || { echo "FAIL firmware restart once per outage"; fail=1; }

: > "$T/log"; rm -f "$T/mx/mx_panic"
echo SCANNING > "$T/state"
run 11
check "not associated: no firmware restart" "step 3 skipped" "$T/log"
[ ! -e "$T/mx/mx_panic" ] && echo "ok   mx_panic untouched" || { echo "FAIL mx_panic untouched"; fail=1; }

: > "$T/log"
echo COMPLETED > "$T/state"
( sleep 3; touch "$T/addr" ) &
run 8
check "address back is logged" "address back after step 1: " "$T/log"
[ "$(grep -c 'step 2' "$T/log")" = 0 ] && echo "ok   no step after the address" || { echo "FAIL no step after the address"; fail=1; }

[ $fail = 0 ] && echo PASS || { echo "--- log"; cat "$T/log"; echo FAIL; }
exit $fail
