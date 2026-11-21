#!/bin/sh
# e850-usbhost on|off|status (root); `run` is the unit's main process (same steps as scope-run.sh, 11.64).
G=/sys/devices/platform/13600000.usb
case $1 in
on)  exec systemctl start e850-usbhost;;
off) exec systemctl stop e850-usbhost;;
status)
    echo "otg state $(cat $G/state 2>/dev/null || echo '? (root only)'), unit $(systemctl is-active e850-usbhost)"
    ls /sys/bus/usb/devices/ 2>/dev/null | grep -E '^[0-9]-[0-9.]+$' | while read d; do
        echo "  $d $(cat /sys/bus/usb/devices/$d/idVendor):$(cat /sys/bus/usb/devices/$d/idProduct) $(cat /sys/bus/usb/devices/$d/product 2>/dev/null)"
    done;;
run)
    if systemctl is-active -q e850-scope; then       # the microscope owns the port and already did all this
        echo "e850-scope is running: host mode is its job"; exec sleep infinity
    fi
    modprobe e850-hubpwr || exit 1                     # LDO24 = hub (LAN9514) power
    python3 /opt/e850-features/usbhost/gpiohold.py 3 5 0 & GP=$!    # gpa3-5 low = USB-A port power
    trap 'kill $GP; echo 1 > $G/id; sleep 2; echo 1 > $G/b_sess; echo "gadget mode again, state $(cat $G/state)"; exit 0' TERM INT
    echo 0 > $G/b_sess; sleep 1; echo 0 > $G/id; sleep 1
    [ "$(cat $G/state)" = a_host ] || { echo 0 > $G/id; sleep 1; }
    echo "state $(cat $G/state)"
    while :; do sleep 3600 & wait $!; done;;
*) sed -n 2p "$0"; exit 1;;
esac
