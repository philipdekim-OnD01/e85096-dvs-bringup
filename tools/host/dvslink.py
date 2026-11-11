#!/usr/bin/env python3
"""MIPI link checks on S5KRC1S captures, on the raw packet words with nothing corrected (D-PHY PLL tests,
docs/DVS-PLL-TESTS.md). dvsview.c corrects timestamp bit errors and skips bad stretches; this counts them.

  dvslink.py FILE... [-W 8192] [-H 127] [--json]

FILE is a board recording (dvsrec .gz or .bin: each MIPI line's packet words, then a zero word; dvsview.c
dvs_record) or a VC0 buffer from ov5647-cap.py --raw8 (lines of -W bytes; the file is the whole allocation).
Per file:
  words       by type (dvsdecode.kind); 'unknown' = no defined type: bit errors (11.23, 11.28)
  ts steps    t = ref ms x 1000 + sub us at each sub, step to the next in scans (the median step):
              next (1), miss1 / miss2 (one or two scans missing), gap (> 3), repeat (0), short (< 0.5), back (< 0);
              onebit = a bad step that is 1..3 scans once one bit 1..21 of the ms reference is flipped (11.28)
  sub_range   sub fields >= 1000 us
  frame_end   steps of the 8-bit frame number [18:11]: next (+1), else skips (and the scans they miss)
  lines       buffers only: lines with data in the first -H, lines with data after them (DMA overrun),
              short = a buffer with fewer than -H data lines (a truncated MIPI frame)
"""
import argparse
import gzip
import json
import sys

import numpy as np

MARK = 0x5a5a5a5a   # ov5647-cap.py fills the first 4 kB of a buffer with 0x5a before queueing it


def read_words(path):
    data = open(path, 'rb').read()
    if data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    return np.frombuffer(data[:len(data) & ~3], '>u4').astype(np.uint32)


def kinds(w):
    top = w >> 26
    grp = (w >> 31) == 1
    ts = top == 2
    sub = ts & (((w >> 23) & 1) == 1)
    return {'pad': w == 0, 'group': grp, 'column': top == 1, 'ref_ts': ts & ~sub, 'sub_ts': sub,
            'frame_end': top == 3}


def timestamps(w, k):
    """t (us) at each sub, from the last ref before it"""
    rp, sp = np.flatnonzero(k['ref_ts']), np.flatnonzero(k['sub_ts'])
    j = np.searchsorted(rp, sp) - 1
    sp, j = sp[j >= 0], j[j >= 0]
    ref = (w[rp] & 0x3fffff).astype(np.int64)
    sub = (w[sp] & 0x3ff).astype(np.int64)
    return ref[j] * 1000 + sub, sub


def ts_steps(t):
    dt = np.diff(t)
    ok = (dt > 0) & (dt < 100000)
    if not ok.any():
        return {'n': int(dt.size)}
    scan = float(np.median(dt[ok]))
    r = dt / scan
    good = (r > 0.5) & (r <= 3.5)
    one = np.zeros(dt.size, bool)
    for b in range(1, 22):       # bit 0 (1 ms) is about one scan: not told apart from a real step
        f = 1000 << b
        for s in (dt - f, dt + f):
            one |= ~good & (s / scan > 0.5) & (s / scan <= 3.5)
    return {'n': int(dt.size), 'scan_us': scan,
            'next': int(((r > 0.5) & (r <= 1.5)).sum()), 'miss1': int(((r > 1.5) & (r <= 2.5)).sum()),
            'miss2': int(((r > 2.5) & (r <= 3.5)).sum()), 'gap': int((r > 3.5).sum()),
            'repeat': int((dt == 0).sum()), 'short': int(((dt > 0) & (r <= 0.5)).sum()),
            'back': int((dt < 0).sum()), 'onebit': int(one.sum())}


def fe_steps(w, k):
    f = ((w[k['frame_end']] >> 11) & 0xff).astype(np.int64)
    d = np.diff(f) % 256
    return {'n': int(f.size), 'next': int((d == 1).sum()), 'skips': int((d != 1).sum()),
            'missed': int(np.where((d > 1) & (d <= 16), d - 1, 0).sum())}


def lines(w, width, height):
    wl = width // 4
    n = w.size // wl
    first = w[:n * wl].reshape(n, wl)[:, 0]
    data = (first != 0) & (first != MARK)
    return {'lines': int(min(n, height)), 'data': int(data[:height].sum()), 'after': int(data[height:].sum()),
            'short': int(data[:height].sum() < height)}


def check(w, width=None, height=None):
    k = kinds(w)
    named = np.zeros(w.size, bool)
    for m in k.values():
        named |= m
    unk = ~named
    t, sub = timestamps(w, k)
    out = {'words': {n: int(m.sum()) for n, m in k.items()}, 'unknown': int(unk.sum()),
           'unknown_types': {format(x, '06b'): int(c) for x, c in
                             enumerate(np.bincount(w[unk] >> 26, minlength=64)) if c},
           'events': int(np.unpackbits((w[k['group']] & 0xffff).astype('>u2').view(np.uint8)).sum()),
           'ts': ts_steps(t), 'sub_range': int((sub >= 1000).sum()), 'frame_end': fe_steps(w, k)}
    if width:
        out['lines'] = lines(w, width, height)
    return out


def selftest():
    words, f = [], 0
    for i in range(200):
        if i in (50, 51):        # two scans not sent
            f += 1
            continue
        t = 3685 + 985 * i
        ref = t // 1000 ^ (1 << 5 if i == 120 else 0)     # one bad reference bit
        words += [0x08000000 | ref, 0x08800000 | t % 1000, 0x04000000 | i, 0x80000001, 0x0c000000 | (f & 0xff) << 11, 0]
        f += 1
    words[8] = 0x14000000                                  # an undefined type 000101 for a column
    r = check(np.array(words, np.uint32))
    assert r['unknown'] == 1 and r['unknown_types'] == {'000101': 1}, r
    assert r['ts']['miss2'] == 1 and r['ts']['onebit'] == 2 and r['ts']['back'] + r['ts']['gap'] == 2, r['ts']
    assert r['frame_end'] == {'n': 198, 'next': 196, 'skips': 1, 'missed': 2}, r['frame_end']
    assert r['events'] == 198 and r['sub_range'] == 0, r
    buf = np.zeros(4 * 2048, np.uint32)
    buf[0], buf[2048], buf[3 * 2048] = 0x08000001, 0x08000002, MARK
    assert lines(buf, 8192, 3) == {'lines': 3, 'data': 2, 'after': 0, 'short': 1}


if __name__ == '__main__':
    selftest()
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('-W', type=int, default=8192, help='buffer line length, bytes (buffers only)')
    ap.add_argument('-H', type=int, default=127, help='MIPI lines per frame (buffers only)')
    ap.add_argument('--json', action='store_true', help='one JSON line per file')
    a = ap.parse_args()
    for p in a.files:
        w = read_words(p)
        rec = p.endswith(('.gz', '.bin'))
        r = check(w, None if rec else a.W, a.H)
        r['file'] = p
        if a.json:
            print(json.dumps(r))
            continue
        ts, fe = r['ts'], r['frame_end']
        print('%s: %d words, unknown %d %s, events %d' % (p, w.size, r['unknown'], r['unknown_types'], r['events']))
        print('  ts steps %d (scan %.0f us): next %d, miss1 %d, miss2 %d, gap %d, repeat %d, short %d, back %d, '
              'onebit %d; sub >= 1000: %d' % (ts['n'], ts.get('scan_us', 0), ts.get('next', 0), ts.get('miss1', 0),
                                             ts.get('miss2', 0), ts.get('gap', 0), ts.get('repeat', 0),
                                             ts.get('short', 0), ts.get('back', 0), ts.get('onebit', 0),
                                             r['sub_range']))
        print('  frame_end %d: next %d, skips %d (scans missed %d)' % (fe['n'], fe['next'], fe['skips'], fe['missed']))
        if 'lines' in r:
            ln = r['lines']
            print('  lines with data %d / %d, after %d%s' % (ln['data'], ln['lines'], ln['after'],
                                                           '  SHORT' if ln['short'] else ''))
    sys.exit(0)
