#!/usr/bin/env python3
"""Pack an Android boot image, header version 2 (kernel + ramdisk + dtb).

The E850-96 Android 10 LK loads this format from the 'boot' partition or via
`fastboot boot`. Field values default to the ones in the stock Android 10 boot.img.

usage: mkbootimg_v2.py --kernel Image --ramdisk initramfs.cpio.gz --dtb merged.dtb \
                       --cmdline "..." -o boot.img
"""
import argparse
import hashlib
import struct

PAGE = 2048
BASE = 0x10000000


def pad(data):
    return data + b'\0' * (-len(data) % PAGE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kernel', required=True)
    ap.add_argument('--ramdisk', required=True)
    ap.add_argument('--dtb', required=True)
    ap.add_argument('--cmdline', default='')
    ap.add_argument('--os-version', type=lambda x: int(x, 0), default=0x1400013c)
    ap.add_argument('-o', '--output', required=True)
    a = ap.parse_args()

    kernel = open(a.kernel, 'rb').read()
    ramdisk = open(a.ramdisk, 'rb').read()
    dtb = open(a.dtb, 'rb').read()
    cmd = a.cmdline.encode()
    if len(cmd) > 512 + 1024 - 1:
        raise SystemExit('cmdline too long')
    cmdline, extra = cmd[:512], cmd[512:]

    sha = hashlib.sha1()
    for blob in (kernel, ramdisk, b'', b'', dtb):      # kernel, ramdisk, second, recovery_dtbo, dtb
        sha.update(blob)
        sha.update(struct.pack('<I', len(blob)))
    header_size = 1660                                  # boot_img_hdr_v2

    hdr = struct.pack('<8s10I16s512s32s1024sIQIIQ',
                      b'ANDROID!',
                      len(kernel), BASE + 0x8000,
                      len(ramdisk), BASE,
                      0, BASE + 0xf00000,
                      BASE,                             # tags_addr
                      PAGE, 2, a.os_version,
                      b'', cmdline, sha.digest(), extra,
                      0, 0, header_size,
                      len(dtb), BASE)
    assert len(hdr) == header_size
    with open(a.output, 'wb') as f:
        f.write(pad(hdr))
        f.write(pad(kernel))
        f.write(pad(ramdisk))
        f.write(pad(dtb))
    print(f'{a.output}: kernel {len(kernel)} ramdisk {len(ramdisk)} dtb {len(dtb)} bytes')


if __name__ == '__main__':
    main()
