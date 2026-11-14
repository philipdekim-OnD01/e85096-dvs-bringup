#!/bin/sh
# Rebuild a board's userdata image from its rootfs tar, to put the same system on another E850-96 (docs 11.53).
#   docker run --rm --privileged -v "$PWD/tools/linux510:/t:ro" -v <dir>:/x ubuntu:22.04 sh /t/build-clone.sh
# In : /x/rootfs.tar from tools/board/rootfs-send.py (tools/host/rootfs-recv.py on the Mac).
# Out: /x/rootfs.simg; then on the Mac `python3 tools/linux510/resparse.py --split-mib 700 rootfs.simg part.simg`
#      (FILL chunks <= 32 MiB, 7.12; pieces under the LK's 768 MB download limit) and `fbmini flash userdata
#      part.simg.N` in order. Not `fastboot flash userdata`: its getvar:is-userspace hangs the LK (11.53).
#      /x/manifest.md5: md5 of every file under the static trees, to compare with the new board after it boots.
# Whole procedure: docs/BOARD-CLONE.md.
# Same size, label and UUID as the source filesystem (tune2fs -l).
# Changed: a new machine-id (so the clone gets its own DHCP lease; the Wi-Fi MAC comes from the SoC id anyway)
# and no systemd random seed. Kept: everything else, ssh host keys and the Wi-Fi settings included.
set -eu
BLOCKS=${BLOCKS:-786432}     # 4 KiB blocks = 3 GiB, mmcblk0p24 of the first board
UUID=${UUID:-de77216b-a7d7-4c77-9fec-51c801b1fb77}
I=/tmp/rootfs.ext4 R=/mnt/r

apt-get -qq update >/dev/null
apt-get -qq install -y e2fsprogs android-sdk-libsparse-utils >/dev/null 2>&1
# tar keeps ACLs by user name (journald's user:philip on user journals): resolve names as the board does
tar -x -f /x/rootfs.tar -C / ./etc/passwd ./etc/group

rm -f $I
mke2fs -q -t ext4 -L rootfs -U $UUID -b 4096 $I $BLOCKS
mkdir -p $R
mount -o loop $I $R
tar -x -p --xattrs --xattrs-include='*' --acls --numeric-owner -f /x/rootfs.tar -C $R
od -An -N16 -tx1 /dev/urandom | tr -d ' \n' > $R/etc/machine-id
echo >> $R/etc/machine-id
cp $R/etc/machine-id $R/var/lib/dbus/machine-id
rm -f $R/var/lib/systemd/random-seed
echo "machine-id $(cat $R/etc/machine-id), $(df -h $R | tail -1)"
(cd $R && find usr lib var/tmp root home opt -xdev -type f -print0 | sort -z | xargs -0 md5sum) > /x/manifest.md5
umount $R
e2fsck -fn $I
img2simg $I /x/rootfs.simg
ls -l /x/rootfs.simg
