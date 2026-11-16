#!/bin/sh
# Spike (run as root, from systemd-run so it outlives the ACM console):
# DWC3 gadget -> host, USB-A VBUS on (gpa3-5 low, mainline exynos850-e850-96.dts), wait for the SOL microscope,
# grab frames with v4l2-ctl, then back to gadget. Log: /var/tmp/scope-spike.log
# It first waits for /var/tmp/scope-go (made by any user over ssh), so the Mac cable can be pulled after it starts:
# with that cable in, the USB lines seem to stay on the device connector (first run: no hub on the root port).
L=/var/tmp/scope-spike.log
exec >"$L" 2>&1
G=/sys/devices/platform/13600000.usb
t() { echo "== $(cut -d' ' -f1 /proc/uptime) s: $*"; }
rm -f /var/tmp/scope-go
t "waiting for /var/tmp/scope-go"; cat $G/state
i=0; while [ $i -lt 1800 ] && [ ! -e /var/tmp/scope-go ]; do sleep 1; i=$((i+1)); done
t start
dmesg -c >/var/tmp/scope-spike-dmesg-before.txt
python3 /var/tmp/gpiohold.py 3 5 0 & GP=$!
sleep 1
t "b_sess 0"; echo 0 > $G/b_sess; sleep 1; cat $G/state
t "id 0"; echo 0 > $G/id; sleep 1; cat $G/state
[ "$(cat $G/state)" = a_host ] || { echo 0 > $G/id; sleep 1; cat $G/state; }
uvc() { for v in /sys/class/video4linux/video*; do readlink -f $v/device | grep -q /usb && echo /dev/${v##*/}; done | head -1; }
i=0
while [ $i -lt 120 ] && [ -z "$(uvc)" ]; do sleep 1; i=$((i+1)); done
V=$(uvc)
t "after ${i} s: uvc node '$V'"
for d in /sys/bus/usb/devices/*; do [ -f $d/idVendor ] && echo "$(basename $d) $(cat $d/idVendor):$(cat $d/idProduct) $(cat $d/speed 2>/dev/null)M $(cat $d/product 2>/dev/null) $(cat $d/bMaxPower 2>/dev/null)"; done
if [ -n "$V" ]; then
    v4l2-ctl -d $V --info --list-formats-ext
    for s in 1632x1224 816x612; do
        w=${s%x*}; h=${s#*x}
        t "capture $s"
        v4l2-ctl -d $V --set-fmt-video=width=$w,height=$h,pixelformat=YUYV --stream-mmap=4 --stream-skip=30 --stream-count=3 \
            --stream-to=/var/tmp/scope-$s.yuv 2>&1 | tr '\r' '\n' | tail -4
        ls -l /var/tmp/scope-$s.yuv
    done
fi
t "dmesg"; dmesg | grep -vE "s2m_rtc" | tail -80
t "back to gadget"; echo 1 > $G/id; sleep 2; cat $G/state; echo 1 > $G/b_sess; sleep 2; cat $G/state
kill $GP
t end
