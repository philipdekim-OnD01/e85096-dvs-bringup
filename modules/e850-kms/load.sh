#!/bin/sh
# As root on the board: load e850-kms.ko (not at boot: a fault here only lasts until the next reboot).
#   sh load.sh [path/to/e850-kms.ko]     Unload: stop X first, then rmmod e850_kms
set -e
K=${1:-$(dirname "$0")/e850-kms.ko}
grep -q '^e850_kms ' /proc/modules || insmod "$K"
udevadm settle -t 5 || true
ls -l /dev/dri/
