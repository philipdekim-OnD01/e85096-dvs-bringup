#!/bin/sh
# wifiwatch-trial.sh OUT (root, detached: ssh over Wi-Fi drops for about 2 minutes):
#   sudo systemd-run --unit=wifiwatch-trial sh /var/tmp/wifiwatch-trial.sh /var/tmp/wifiwatch-trial
# Makes "associated, no address" (docs 11.98): DHCP dropped on wlan0 both ways (tc), the IPv4 address flushed.
# e850-wifiwatch runs with WAIT 60 (the service is stopped meanwhile); the block goes at 80 s, after step 1.
# OUT.state: every 2 s "<s> <wpa_state> <ipv4>"; OUT.watch: the watch's lines. The service starts again at the end.
O=$1
undo() {
    tc qdisc del dev wlan0 clsact 2>/dev/null
    [ -n "$w" ] && kill "$w" 2>/dev/null
    systemctl start e850-wifiwatch
}
trap undo EXIT
w=
systemctl stop e850-wifiwatch
WAIT=60 TICK=5 /usr/local/bin/e850-wifiwatch.sh > "$O.watch" 2>&1 & w=$!
tc qdisc add dev wlan0 clsact
tc filter add dev wlan0 egress protocol ip u32 match ip protocol 17 0xff match ip dport 67 0xffff action drop
tc filter add dev wlan0 ingress protocol ip u32 match ip protocol 17 0xff match ip sport 67 0xffff action drop
ip -4 addr flush dev wlan0
t0=$(date +%s) back=
while [ $(( $(date +%s) - t0 )) -lt 300 ]; do
    s=$(( $(date +%s) - t0 ))
    a=$(ip -4 -br addr show dev wlan0 | awk '{print $3}')
    echo "$s $(wpa_cli -i wlan0 status | sed -n 's/^wpa_state=//p') ${a:--}" >> "$O.state"
    if [ $s -ge 80 ] && tc qdisc show dev wlan0 | grep -q clsact; then
        tc qdisc del dev wlan0 clsact && echo "$s DHCP unblocked" >> "$O.state"
    fi
    [ -n "$a" ] && [ $s -ge 80 ] && { back=$s; break; }
    sleep 2
done
sleep 12        # the watch's "address back" line (TICK 5)
if [ -n "$back" ]; then echo "address back at $back s"; else echo "address NOT back in 300 s"; fi >> "$O.state"
