#!/bin/sh
# make-install-sd.sh CARD BOOT_IMG USERDATA_SIMG: an SD card for the installer boot image (docs 11.120,
# BOARD-CLONE.md 13). CARD is the mounted card (exFAT: FAT32 cannot hold a file over 4 GB).
#   sh tools/host/make-install-sd.sh "/Volumes/NO NAME" ~/e850-logs/boot-nocpu/boot.img <dir>/userdata.simg
# MD5SUMS is taken from the sources, so the board checks the card's copy against them.
# The card then holds the image's secrets (password hashes, ssh keys, Wi-Fi password): keep it with you.
set -eu
D="$1/e850-install"
! LC_ALL=C grep -aq 'e850\.install' "$2" || { echo "$2 is an installer image: give the system's boot.img"; exit 1; }
mkdir -p "$D"
b=$(md5 -q "$2") u=$(md5 -q "$3")
cp "$2" "$D/boot.img"
[ "$3" -ef "$D/userdata.simg" ] || cp "$3" "$D/userdata.simg"
printf '%s  boot.img\n%s  userdata.simg\n' "$b" "$u" > "$D/MD5SUMS"
cat "$D/MD5SUMS"
