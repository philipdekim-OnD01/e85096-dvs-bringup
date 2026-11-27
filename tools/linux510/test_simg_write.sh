#!/bin/sh
# test_simg_write.sh: simg-write against simg2img, and --grow on a loop device (docs 11.120).
#   docker run --rm --privileged -v "$PWD/tools/linux510:/t:ro" ubuntu:22.04 sh /t/test_simg_write.sh
set -eu
apt-get -qq update >/dev/null
apt-get -qq install -y gcc libc6-dev e2fsprogs android-sdk-libsparse-utils python3 >/dev/null 2>&1
W=/tmp/w; rm -rf $W; mkdir -p $W/src; cd $W
gcc -O2 -static -Wall -o sw /t/simg-write.c
# a small ext4 (4 KiB blocks, as userdata) with random data, a long non-zero run and empty space
head -c 3000000 /dev/urandom > src/rand; yes e850 | head -c 5000000 > src/text
truncate -s 64M fs.img; mke2fs -q -t ext4 -b 4096 -d src fs.img
img2simg fs.img fs.simg
simg2img fs.simg ref.img

# 1. the whole image, from a file and from a pipe
./sw fs.simg out1.img 2>/dev/null; cmp out1.img ref.img
cat fs.simg | ./sw - out2.img 2>/dev/null; cmp out2.img ref.img
# 2. resparse pieces (DONT_CARE before and after, FILL split) written in order onto one file
python3 /t/resparse.py --max-mib 1 --split-mib 2 fs.simg part.simg >/dev/null
for p in $(ls part.simg.* | sort -t. -k3 -n); do ./sw $p out3.img 2>/dev/null; done
cmp out3.img ref.img
# 3. a device smaller than the image: refused before anything is written
truncate -s 32M small.img; L=$(losetup -f --show small.img)
if ./sw fs.simg $L 2>err; then echo "FAIL: wrote to a smaller device"; exit 1; fi
grep -q " only " err
losetup -d $L
cmp -n $(stat -c %s small.img) small.img /dev/zero
# 4. onto a bigger block device, then --grow while mounted
truncate -s 128M big.img; L=$(losetup -f --show big.img)
./sw fs.simg $L 2>/dev/null
mkdir -p m; mount $L m; ./sw --grow m $L; cmp m/rand src/rand; umount m
e2fsck -fn $L >/dev/null
n=$(dumpe2fs -h $L 2>/dev/null | sed -n 's/^Block count: *//p')
losetup -d $L
[ "$n" = $((128 * 1024 * 1024 / 4096)) ] || { echo "FAIL: grown to $n blocks"; exit 1; }
# 5. not a sparse image, and a truncated one
if head -c 100 /dev/urandom | ./sw - bad.img 2>/dev/null; then echo "FAIL: took garbage"; exit 1; fi
if head -c 200000 fs.simg | ./sw - cut.img 2>/dev/null; then echo "FAIL: took a cut image"; exit 1; fi
echo "test_simg_write: all passed"
