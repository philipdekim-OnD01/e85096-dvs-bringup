#!/bin/sh
# Spike 3 (root, via systemd-run; transient, gone at reboot): run USB host tests on request so the Mac cable can stay out.
# Request = file /var/tmp/scope-go holding "VBUS SECS" (VBUS 0, 1 or n = leave gpa3-5 alone; SECS 5..300) or "quit".
# Each test: gadget -> host, gpa3-5, poll USB devices and the xHCI port every 2 s, capture if a UVC node shows up,
# back to gadget. Log: /var/tmp/scope-loop.log
L=/var/tmp/scope-loop.log
exec >>"$L" 2>&1
G=/sys/devices/platform/13600000.usb
t() { echo "== $(cut -d' ' -f1 /proc/uptime) s: $*"; }
devs() { ls /sys/bus/usb/devices | grep -E '^[0-9]+-[0-9.]+$' | tr '\n' ' '; }
port() { cat /sys/kernel/debug/usb/xhci/*/ports/port01/portsc 2>/dev/null; }
uvc() { for v in /sys/class/video4linux/video*; do readlink -f $v/device | grep -q /usb && echo /dev/${v##*/}; done | head -1; }
t "loop up, state $(cat $G/state)"
n=0
while :; do
    rm -f /var/tmp/scope-go
    while [ ! -e /var/tmp/scope-go ]; do sleep 1; done
    sleep 0.2; set -- $(head -c 32 /var/tmp/scope-go); n=$((n+1))
    [ "$1" = quit ] && { t quit; break; }
    case "$1" in 0|1|n) ;; *) t "bad request '$*'"; continue;; esac
    case "$2" in [0-9]|[0-9][0-9]|[0-9][0-9][0-9]) s=$2;; *) s=30;; esac
    [ $s -lt 5 ] && s=5; [ $s -gt 300 ] && s=300
    t "test $n: vbus $1, $s s"
    dmesg -c >/dev/null
    echo 0 > $G/b_sess; sleep 1; echo 0 > $G/id; sleep 1
    [ "$(cat $G/state)" = a_host ] || { echo 0 > $G/id; sleep 1; }
    t "state $(cat $G/state); port: $(port)"
    GP=; [ "$1" != n ] && { python3 /var/tmp/gpiohold.py 3 5 $1 & GP=$!; }
    k=0; last=
    while [ $k -lt $s ]; do
        sleep 2; k=$((k+2)); now="$(devs)| $(port)"
        [ "$now" != "$last" ] && { t "${k} s: devices: $now"; last=$now; }
    done
    grep -E "gpa3:|gpio-29" -A0 /sys/kernel/debug/gpio
    V=$(uvc); t "uvc node '$V'"
    for d in /sys/bus/usb/devices/*; do [ -f $d/idVendor ] && echo "  $(basename $d) $(cat $d/idVendor):$(cat $d/idProduct) $(cat $d/speed)M $(cat $d/product 2>/dev/null)"; done
    if [ -n "$V" ]; then
        for sz in 816x612 1632x1224; do
            t "capture $sz"
            v4l2-ctl -d $V --set-fmt-video=width=${sz%x*},height=${sz#*x},pixelformat=YUYV --stream-mmap=4 --stream-skip=30 \
                --stream-count=3 --stream-to=/var/tmp/scope-$sz.yuv 2>&1 | tr '\r' '\n' | tail -2
        done
    fi
    dmesg | grep -E "usb [0-9]|hub|xhci|over-current|ovc|vbus|VBUS" | grep -vE "root hub|bus_suspend|bus_resume" | tail -25
    [ -n "$GP" ] && kill $GP
    echo 1 > $G/id; sleep 2; echo 1 > $G/b_sess; sleep 2
    t "test $n done, state $(cat $G/state)"
done
