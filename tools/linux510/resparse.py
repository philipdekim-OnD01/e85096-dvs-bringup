"""Split large FILL chunks of an Android sparse image.

The E850-96 Android 10 LK hangs on a FILL chunk of 1 GiB (it stopped at
"CHUNK TYPE FILL (... sct: 2097144 ...)"); 64 MiB and 160 MiB fills worked.
img2simg emits one FILL chunk per run of zero blocks, so a mostly empty 3 GB
ext4 image has a few huge ones. This rewrites the image so that no FILL chunk
covers more than --max-mib (default 32 MiB). RAW and DONT_CARE chunks are copied.

    python3 resparse.py rootfs.simg rootfs-split.simg
    python3 resparse.py --split-mib 700 rootfs.simg part.simg     # part.simg.0, .1, ... for fbmini

--split-mib is for images over the LK's 768 MB download limit: fastboot would split them itself, but it asks
getvar:is-userspace first and this LK hangs on it (11.53). Each piece is a whole-partition sparse image with
DONT_CARE before and after its chunks, flashed in order to the same partition.
"""
import argparse
import struct

HDR = struct.Struct('<IHHHHIIII')      # magic, major, minor, file_hdr_sz, chunk_hdr_sz, blk_sz, blocks, chunks, crc
CHK = struct.Struct('<HHII')           # type, reserved, chunk_sz (blocks), total_sz (bytes incl. header)
RAW, FILL, DONT_CARE, CRC = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4

ap = argparse.ArgumentParser()
ap.add_argument('src')
ap.add_argument('dst')
ap.add_argument('--max-mib', type=int, default=32)
ap.add_argument('--split-mib', type=int, default=0,
                help='write DST.0, DST.1, ... of at most this size each, for fbmini (no fastboot getvar, 11.53)')
a = ap.parse_args()


class Piece:
    """One output sparse file. A piece covers the whole partition: DONT_CARE before and after its own chunks."""
    def __init__(self, path, pos):
        self.f, self.n, self.size = open(path, 'wb'), 0, HDR.size
        self.f.write(bytes(HDR.size))              # header written at the end (chunk count changes)
        if pos:
            self.put(DONT_CARE, pos, b'')

    def put(self, t, n, body):
        self.f.write(CHK.pack(t, 0, n, CHK.size + len(body)) + body)
        self.n += 1
        self.size += CHK.size + len(body)

    def close(self, pos):
        if pos < blocks:
            self.put(DONT_CARE, blocks - pos, b'')
        self.f.seek(0)
        self.f.write(HDR.pack(magic, major, minor, fhs, chs, blk, blocks, self.n, 0))
        self.f.close()


with open(a.src, 'rb') as f:
    magic, major, minor, fhs, chs, blk, blocks, chunks, crc = HDR.unpack(f.read(HDR.size))
    assert magic == 0xED26FF3A and fhs == HDR.size and chs == CHK.size, 'not a v1 sparse image'
    limit = a.max_mib * 1024 * 1024 // blk
    budget = a.split_mib << 20
    paths = [a.dst + '.0' if budget else a.dst]
    o, pos, out, split = Piece(paths[0], 0), 0, 0, 0

    def put(t, n, body):
        global o, pos, out
        if budget and o.n and o.size + 2 * CHK.size + len(body) > budget:
            o.close(pos)
            paths.append('%s.%d' % (a.dst, len(paths)))
            o = Piece(paths[-1], pos)
        o.put(t, n, body)
        pos += n
        out += 1

    for _ in range(chunks):
        t, _r, n, total = CHK.unpack(f.read(CHK.size))
        body = f.read(total - CHK.size)
        if t == FILL and n > limit:
            while n:
                k = min(n, limit)
                put(FILL, k, body)
                n -= k
            split += 1
        elif t == CRC:
            continue                               # CRC chunk no longer valid after rewriting
        else:
            put(t, n, body)
    assert pos == blocks, 'chunks cover %d of %d blocks' % (pos, blocks)
    o.close(pos)
print(f'{chunks} chunks -> {out}; {split} FILL chunks split to <= {a.max_mib} MiB; ' + ' '.join(paths))
