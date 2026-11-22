#!/bin/sh
# As root on the board: load e850-fbbuf.ko (not at boot: a fault here only lasts until the next
# reboot) and give /dev/e850-fbbuf the owner and mode of /dev/fb0.   sh load.sh [path/to/e850-fbbuf.ko]
set -e
K=${1:-$(dirname "$0")/e850-fbbuf.ko}
grep -q '^e850_fbbuf ' /proc/modules || insmod "$K"
# udev applies its default mode to the new node after insmod returns: let it finish first (11.84)
udevadm settle -t 5 || true
chown --reference=/dev/fb0 /dev/e850-fbbuf
chmod --reference=/dev/fb0 /dev/e850-fbbuf
ls -l /dev/fb0 /dev/e850-fbbuf
