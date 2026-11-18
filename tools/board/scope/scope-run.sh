#!/bin/sh
# e850-scope supervisor (root): USB host mode for the SOL microscope, then scope.py, restarted on GET /reload.
# At boot: e850-scope.service (install.sh) runs it from root-owned /opt/e850-scope. Stop: GET /quit or touch $R/stop.
# Dev: systemd-run --unit=e850-scope-dev sh /var/tmp/e850-scope/scope-run.sh (stop the boot service first).
D=${SCOPE_DIR:-/var/tmp/e850-scope}           # programs
R=${SCOPE_RUN:-$D}                             # log and the stop / reload flag files
G=/sys/devices/platform/13600000.usb
exec >>$R/scope.log 2>&1
echo "== $(date '+%F %T') start, state $(cat $G/state)"
[ -d /sys/module/e850_hubpwr ] || insmod $D/e850-hubpwr.ko || { echo "e850-hubpwr failed"; exit 1; }
rm -f $R/stop $R/reload
python3 $D/gpiohold.py 3 5 0 & GP=$!            # gpa3-5 low = USB-A port power on
echo 0 > $G/b_sess; sleep 1; echo 0 > $G/id; sleep 1
[ "$(cat $G/state)" = a_host ] || { echo 0 > $G/id; sleep 1; }
echo "state $(cat $G/state)"
trap 'touch $R/stop; kill $PY 2>/dev/null' TERM INT
while [ ! -e $R/stop ]; do
    python3 $D/scope.py --ctl $R & PY=$!
    wait $PY
    [ -e $R/stop ] || sleep 1
done
kill $GP
echo 1 > $G/id; sleep 2; echo 1 > $G/b_sess
echo "== $(date '+%F %T') stop, state $(cat $G/state)"
