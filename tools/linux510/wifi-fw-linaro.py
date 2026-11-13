#!/usr/bin/env python3
"""wifi-fw-linaro.py OUT.tgz: the E850 (nacho_s612) WLBT firmware of Linaro's Android 13 E850-96 BSP, laid out
as /vendor/etc/wifi wants it (11.47): mx140.bin, mx140_t.bin, mx140/ (of mx140/conf/nacho_s612_*/ only the ERD
board's), mx140_t/, and slsi_reg_database.bin = reg_domain/regulatory_db_2_7.bin, as wlbt_firmware.mk does.
A new rootfs gets it the same way (build-rootfs.sh has no Wi-Fi firmware; wifi-fw-extract.sh is the 2019 set).

Source: gitlab.com/Linaro/96boards/e850-96/platform/vendor/samsung_slsi, pinned commit. Install on the board:
tools/board/wifi-fwswap.sh a13 (reads /var/tmp/wifi-a13.tgz).
"""
import hashlib
import io
import json
import re
import sys
import tarfile
import urllib.parse
import urllib.request

API = 'https://gitlab.com/api/v4/projects/33954066/repository'
REF = '11e9c19afbbe6c39da0a7bf32a2d17a5a77087fb'     # android13-e850-96, 2025-08-15
BASE = 'mx140/firmware/nacho_s612/'
REGDB = 'mx140/firmware/reg_domain/regulatory_db_2_7.bin'
MX140_SHA256 = '3d94b5b5c97921860bd6afc0f58de9cf9a454cadc06de1e7972524c23fb5bd7c'   # S612_E3830_R4-1_Y23W13A


def get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def tree():
    paths, page = [], 1
    while True:
        q = urllib.parse.urlencode({'ref': REF, 'path': BASE.rstrip('/'), 'recursive': 'true', 'per_page': 100,
                                    'page': page})
        batch = json.loads(get('%s/tree?%s' % (API, q)))
        paths += [e['path'] for e in batch if e['type'] == 'blob']
        if len(batch) < 100:
            return paths
        page += 1


def wanted(p):
    """mx140/conf/<product>/ only for the ERD board (= the generic conf); everything else as it is"""
    m = re.match(BASE + r'mx140/conf/(nacho_s612_[^/]+)/', p)
    return not m or m.group(1) == 'nacho_s612_erd'


def raw(p):
    return get('%s/files/%s/raw?ref=%s' % (API, urllib.parse.quote(p, safe=''), REF))


def main():
    files = {p[len(BASE):]: p for p in tree() if wanted(p)}
    files['slsi_reg_database.bin'] = REGDB
    with tarfile.open(sys.argv[1], 'w:gz') as t:
        for name, p in sorted(files.items()):
            data = raw(p)
            if name == 'mx140.bin':
                assert hashlib.sha256(data).hexdigest() == MX140_SHA256, 'mx140.bin is not the pinned build'
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode = len(data), 0o644
            t.addfile(ti, io.BytesIO(data))
    print('%s: %d files' % (sys.argv[1], len(files)))


if __name__ == '__main__':
    main()
