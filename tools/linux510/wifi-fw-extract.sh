#!/bin/sh
# Pull the Maxwell (SCSC) Wi-Fi firmware out of the Android 10 backup:
# (the 2019 S612_E3830_C4_Y19W51A build, which takes Data Aborts every few hours with this driver;
#  use the newer Linaro Android 13 set instead: wifi-fw-linaro.py, docs 11.47)
#   super (mmcblk0p12) -> vendor -> /etc/wifi  ==>  wifi-fw.tgz  (vendor/etc/wifi/...)
#
#   sh tools/linux510/wifi-fw-extract.sh ~/Desktop/E850_Android10_backup_20261031 OUTDIR
# Needs python3 and docker (debugfs from e2fsprogs in an ubuntu container).
set -e
B=$1; O=${2:-.}
T=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$O"; O=$(cd "$O" && pwd)
gzip -dc "$B/mmcblk0p12.img.gz" > "$O/super.img"
(cd "$O" && python3 "$T/lpunpack-min.py" super.img vendor)
rm "$O/super.img"
docker run --rm -v "$O":/w ubuntu:22.04 sh -c '
  apt-get -qq update >/dev/null && apt-get -qq install -y e2fsprogs >/dev/null 2>&1
  rm -rf /w/fw && mkdir -p /w/fw/vendor/etc
  debugfs -R "rdump /etc/wifi /w/fw/vendor/etc" /w/vendor.img'
(cd "$O/fw" && COPYFILE_DISABLE=1 tar czf ../wifi-fw.tgz --no-xattrs vendor/etc 2>/dev/null || tar czf ../wifi-fw.tgz vendor/etc)
rm -f "$O/vendor.img"
ls -l "$O/wifi-fw.tgz"
