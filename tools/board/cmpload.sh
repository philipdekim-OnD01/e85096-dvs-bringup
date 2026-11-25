#!/bin/sh
# cmpload.sh SECONDS [MPV_SOCKET]: one condition of the E850 / Jetson comparison (docs 11.115), board or Jetson, no root.
# First SECONDS: thrcpu.sh on the DVS capture process, dvsgl, mpv, the X server and gnome-shell at once, and every CPU
# from /proc/stat. Then SECONDS of viewstat.py (window updates/s, camera frame age; needs DISPLAY), apart from the
# first part so that its own CPU is not in it.
D=$(dirname "$(readlink -f "$0")"); s=$1; shift; T=$(mktemp -d); trap 'rm -rf $T' EXIT
for p in $(pgrep -f 'python3 .*(ov5647-cap.py --live|dvs-jetson.py)') $(pgrep -x dvsgl) $(pgrep -x mpv) \
         $(pgrep -x Xorg) $(pgrep -x gnome-shell); do
    sh "$D/thrcpu.sh" $p $s > $T/$p 2>&1 &
done
grep '^cpu' /proc/stat > $T/stat.a; sleep $s; grep '^cpu' /proc/stat > $T/stat.b; wait
for f in $T/[0-9]*; do echo "== $(tr '\0' ' ' < /proc/${f##*/}/cmdline | cut -c1-60) (pid ${f##*/})"; cat $f; done
# busy = user..steal but idle and iowait (guest is already in user); "cpu" is all of them together, in % of one core
paste -d' ' $T/stat.a $T/stat.b | awk '{n = (NF - 2) / 2; t = 0; i = 0
    for (k = 2; k <= 9; k++) t += $(k + n + 1) - $k
    i = $(5 + n + 1) - $5 + $(6 + n + 1) - $6
    printf "%s %.1f%%%s", $1, (t - i) * 100 / (t / ($1 == "cpu" ? '"$(nproc)"' : 1)), $1 == "cpu" ? "\n" : " "} END {print ""}'
python3 "$D/viewstat.py" $s "$@"
