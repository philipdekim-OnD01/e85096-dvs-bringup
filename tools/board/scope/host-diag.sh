#!/bin/sh
# Spike 2 (root, via systemd-run): why no hub on the root port. Waits for /var/tmp/scope-go (Mac cable out), then host
# mode, and every 5 s logs gpa0-0 / gpa3-5 registers, xHCI PORTSC and USB devices; VBUS enable low for 30 s, then high
# for 30 s if nothing showed up. Captures frames if a UVC node appears. Back to gadget at the end.
L=/var/tmp/scope-diag.log
exec >"$L" 2>&1
G=/sys/devices/platform/13600000.usb
t() { echo "== $(cut -d' ' -f1 /proc/uptime) s: $*"; }
snap() {
    python3 /var/tmp/gpiodump.py 2>&1
    for p in /sys/kernel/debug/usb/xhci/*/ports/*/portsc; do [ -f "$p" ] && echo "$p: $(cat $p)"; done
    echo "usb: $(ls /sys/bus/usb/devices | tr '\n' ' ')"
}
found() { ls /sys/bus/usb/devices | grep -q '^2-'; }
uvc() { for v in /sys/class/video4linux/video*; do readlink -f $v/device | grep -q /usb && echo /dev/${v##*/}; done | head -1; }
t "before (gadget)"; cat $G/state; snap
grep -l "i2c5" /proc/device-tree/aliases/* 2>/dev/null; tr '\0' ' ' < /proc/device-tree/aliases/i2c5 2>/dev/null; echo
rm -f /var/tmp/scope-go
t "waiting for /var/tmp/scope-go"
i=0; while [ $i -lt 1800 ] && [ ! -e /var/tmp/scope-go ]; do sleep 1; i=$((i+1)); done
t "go"; snap
dmesg -c > /var/tmp/scope-diag-dmesg-before.txt
echo 0 > $G/b_sess; sleep 1; echo 0 > $G/id; sleep 1
[ "$(cat $G/state)" = a_host ] || { echo 0 > $G/id; sleep 1; }
t "state $(cat $G/state)"; snap
for v in 0 1; do
    python3 /var/tmp/gpiohold.py 3 5 $v & GP=$!
    k=0; while [ $k -lt 6 ]; do sleep 5; k=$((k+1)); t "gpa3-5 driven $v, ${k}x5 s"; snap; found && break; done
    found && break
    kill $GP; GP=
done
sleep 5; V=$(uvc); t "uvc node '$V'"
for d in /sys/bus/usb/devices/*; do [ -f $d/idVendor ] && echo "$(basename $d) $(cat $d/idVendor):$(cat $d/idProduct) $(cat $d/speed)M $(cat $d/product 2>/dev/null)"; done
if [ -n "$V" ]; then
    v4l2-ctl -d $V --list-formats-ext
    for s in 816x612 1632x1224; do
        t "capture $s"
        v4l2-ctl -d $V --set-fmt-video=width=${s%x*},height=${s#*x},pixelformat=YUYV --stream-mmap=4 --stream-skip=30 \
            --stream-count=3 --stream-to=/var/tmp/scope-$s.yuv 2>&1 | tr '\r' '\n' | tail -3
        ls -l /var/tmp/scope-$s.yuv
    done
fi
t "dmesg"; dmesg | grep -vE "s2m_rtc|TPMON|RPM|runtime_|utmi|soft_reset|ldo|LDO|get_struct|GUCTL|GFLADJ|sclk|clock|call_device_notify|root hub|bus_suspend|bus_resume|CSIS" | tail -60
t "back to gadget"; echo 1 > $G/id; sleep 2; echo 1 > $G/b_sess; sleep 2; cat $G/state
[ -n "$GP" ] && kill $GP
t end
