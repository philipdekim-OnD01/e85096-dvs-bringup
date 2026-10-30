#!/bin/sh
# Build a Debian 12 (bookworm) arm64 rootfs for E850-96 as an Android sparse ext4 image.
#
# Runs in a native arm64 Ubuntu container; debootstrap needs --privileged:
#   docker run --rm --privileged -v e850-out:/o:ro -v "$PWD/tools/linux510:/t:ro" -v "$PWD/out:/out" \
#       ubuntu:22.04 sh /t/build-rootfs.sh
#
# Output: /out/rootfs.simg  -> fastboot flash userdata rootfs.simg   (userdata = mmcblk0p24)
# Login : serial console ttySAC0 logs in as root automatically; user "e850", password "e850".
set -eu
SIZE=${SIZE:-3G}
R=/tmp/rootfs

apt-get -qq update >/dev/null
apt-get -qq install -y debootstrap e2fsprogs android-sdk-libsparse-utils binutils >/dev/null 2>&1

rm -rf $R
debootstrap --arch=arm64 --variant=minbase \
    --include=systemd-sysv,udev,kmod,iproute2,iputils-ping,procps,less,nano,sudo,openssh-server,i2c-tools,v4l-utils,python3,python3-numpy,ca-certificates,dbus \
    bookworm $R http://deb.debian.org/debian

echo e850-96 > $R/etc/hostname
printf '127.0.0.1\tlocalhost\n127.0.1.1\te850-96\n' > $R/etc/hosts
printf '/dev/mmcblk0p24\t/\text4\tdefaults,noatime\t0\t1\n' > $R/etc/fstab

# Serial console: autologin as root on the UART
mkdir -p $R/etc/systemd/system/serial-getty@ttySAC0.service.d
cat > $R/etc/systemd/system/serial-getty@ttySAC0.service.d/autologin.conf <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin root --keep-baud 115200 %I $TERM
EOF
chroot $R systemctl enable serial-getty@ttySAC0.service

# Student account
chroot $R useradd -m -s /bin/bash -G sudo,video,i2c e850 2>/dev/null || chroot $R useradd -m -s /bin/bash -G sudo,video e850
echo 'e850:e850' | chroot $R chpasswd

# Kernel modules (stripped) so modprobe works after switch_root
KVER=$(cat /o/include/config/kernel.release)
mkdir -p $R/lib/modules/$KVER
while read -r m; do
    strip --strip-debug -o $R/lib/modules/$KVER/$(basename "$m") /o/$m
done < /o/modules.order
for extra in /o/ext-*/*.ko; do
    [ -f "$extra" ] && strip --strip-debug -o $R/lib/modules/$KVER/$(basename "$extra") "$extra" || true
done
chroot $R depmod "$KVER"

rm -rf $R/var/cache/apt/archives/*.deb $R/var/lib/apt/lists/*
mkdir -p /out
rm -f /out/rootfs.ext4 /out/rootfs.simg
mke2fs -q -t ext4 -L rootfs -d $R /out/rootfs.ext4 $SIZE
img2simg /out/rootfs.ext4 /out/rootfs.simg
rm /out/rootfs.ext4
ls -l /out/rootfs.simg
du -sh $R
