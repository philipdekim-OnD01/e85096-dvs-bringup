#!/usr/bin/env python3
"""fetch-fw.py OUTDIR: firmware and config the E850-96 features need, from Linaro's Android 13 E850-96 device
tree (gitlab.com/Linaro/96boards/e850-96/device/samsung/universal3830), pinned commit. Not kept in git: the files
are Samsung binaries. Each file is checked against the sha256 below.

  audio: ABOX DSP firmware (two Calliope cores, names from the DT abox-core nodes), the ASoC topology
         abox_tplg.bin, and Android's mixer_paths.xml (codec / ABOX routes) for reference
  video: MFC v10.21 firmware; the driver asks for mfc_fw.bin (MFC_FW_NAME)
"""
import hashlib
import os
import sys
import urllib.parse
import urllib.request

API = 'https://gitlab.com/api/v4/projects/51360264/repository'
REF = '8bfd9659e2bed8956900a5e404c33e724652e59d'     # android13-e850-96, 2025-08-15
FILES = {   # repo path -> name under OUTDIR
    'firmware/audio/calliope_sram.bin': 'audio/calliope_sram.bin',
    'firmware/audio/calliope_dram.bin': 'audio/calliope_dram.bin',
    'firmware/audio/calliope_sram_2.bin': 'audio/calliope_sram_2.bin',
    'firmware/audio/calliope_dram_2.bin': 'audio/calliope_dram_2.bin',
    'firmware/audio/abox_tplg.bin': 'audio/abox_tplg.bin',
    'firmware/audio/abox_tplg.conf': 'audio/abox_tplg.conf',
    'audio/config/mixer_paths.xml': 'audio/mixer_paths.xml',
    'audio/config/audio_board_info.xml': 'audio/audio_board_info.xml',
    'firmware/mfc_fw_v10.21.bin': 'video/mfc_fw.bin',
}
SHA = {}    # filled from fw.sha256 next to this script when present
here = os.path.dirname(os.path.abspath(__file__))
if os.path.exists(os.path.join(here, 'fw.sha256')):
    for line in open(os.path.join(here, 'fw.sha256')):
        h, name = line.split()
        SHA[name] = h


def main():
    out = sys.argv[1]
    sums = []
    for path, name in FILES.items():
        url = '%s/files/%s/raw?ref=%s' % (API, urllib.parse.quote(path, safe=''), REF)
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        h = hashlib.sha256(data).hexdigest()
        if name in SHA and SHA[name] != h:
            sys.exit('%s: sha256 %s, expected %s' % (name, h, SHA[name]))
        dst = os.path.join(out, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'wb') as f:
            f.write(data)
        sums.append('%s  %s' % (h, name))
        print('%8d  %s' % (len(data), name))
    if not SHA:     # first fetch: record the sums for later checks
        with open(os.path.join(here, 'fw.sha256'), 'w') as f:
            f.write('\n'.join(sums) + '\n')
        print('wrote fw.sha256')


if __name__ == '__main__':
    main()
