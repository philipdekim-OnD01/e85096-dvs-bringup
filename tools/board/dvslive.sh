#!/bin/sh
# S5KRC1S live view on HDMI, settings page on :8080 (CTL_PORT): dvslive.sh [ov5647-cap.py args, e.g. --boxes FILE]
# In the foreground: dvslive.service starts it at boot and again whenever it exits (a sensor setting on the page,
# no VC0 frame for 3 s; 11.29). MIF/INT/CAM devfreq to max (else CSIS overflows, 11.22), fimc-is with the error
# stop off (11.24) and the CRC keep on (CRC_KEEP=0 for the vendor abort, 11.28), CIS scan margin and MIPI flush
# from the page's /var/lib/dvsview/sensor.json (MARGIN, FLUSH_US override): margin 17 = a 985 us scan (1015 fps,
# 11.29), flush 50 us = 4x the buffers of 250 us (11.28).
# Log: /tmp/dvslive.log (DVS_LOG=-: stdout).
set -u
for x in mif int cam; do f=$(ls -d /sys/class/devfreq/*_$x); cat $f/max_freq > $f/min_freq; done
lsmod | grep -q '^fimc_is ' || modprobe fimc-is
lsmod | grep -q '^is_cis_s5krc1s ' || modprobe is-cis-s5krc1s
# MIPI frame and link error counts for the page's graphs (11.51); without it the page leaves them empty
lsmod | grep -q '^csis_irqstat ' || modprobe csis-irqstat || true
echo 0 > /sys/module/fimc_is/parameters/e850_csi_err_stop
echo "${CRC_KEEP:-1}" > /sys/module/fimc_is/parameters/e850_csi_crc_keep
eval "$(python3 -c 'import json
try:
    d = json.load(open("/var/lib/dvsview/sensor.json"))
    m, f = int(d.get("margin", 17)), int(d.get("flush_us", 50))
except Exception:
    m, f = 17, 50
print("m=%d f=%d" % (m, f))')"
echo "${MARGIN:-$m}" > /sys/module/is_cis_s5krc1s/parameters/margin
echo "${FLUSH_US:-$f}" > /sys/module/is_cis_s5krc1s/parameters/flush_us
# a second open of /dev/video102 fails while the old process still holds it
while pkill -TERM -f 'ov5647-cap.py --live'; do sleep 2; done
# the person action classifier (11.38) while this file is there; /var/lib/dvsact is philip's, no root needed
if [ -e /var/lib/dvsact/enable ]; then
    set -- --act /var/lib/dvsact/act.jsonl --act-model /var/lib/dvsact/model.onnx "$@"
fi
# the face box with its width in cm (dvsface.py, 11.99) while the model is there
[ -f /var/lib/dvsview/face.onnx ] && set -- --face /var/lib/dvsview/face.onnx "$@"
# dvs (features/dvs, 11.97): DVS_X11=1 the view in an X window ($DISPLAY), DVS_LOG=- the log on this terminal.
# Without the sensor the demo recording plays over and over, if the board has one.
[ -n "${DVS_X11:-}" ] && set -- --x11 "$@"
# DVS_AUTO=1 (dvs --auto, 11.98): without the sensor exit 3 instead
if [ -n "${DVS_AUTO:-}" ]; then
    set -- --need-sensor "$@"
elif [ -f /var/lib/dvsview/demo.gz ]; then
    set -- --replay /var/lib/dvsview/demo.gz "$@"
fi
set -- --live --raw8 -W 8192 -H 127 -n 0 -b 8 -B 16 -t 3 --ctl "${CTL_PORT:-8080}" "$@"
[ "${DVS_LOG:-}" = - ] && exec ov5647-cap.py "$@"
exec ov5647-cap.py "$@" > /tmp/dvslive.log 2>&1 < /dev/null
