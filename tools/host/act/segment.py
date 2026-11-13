"""segment.py SRC_DIR OUT_DIR: board recordings of a person sitting down and standing up -> one clip per move (11.45)

The person sat and stood at their own pace, not on the recorder's cue: most 7 s recordings hold two moves, so
a recording's label does not say which move is which. Each burst of motion (dataset.activity over 1.6 x its
median for >= 0.3 s) is labelled by the person's centroid row before and after it, measured on the counts above
each cell's median in the person's columns less the other columns' noise: standing ~45, seated ~65 of 120 rows
(the 20 recordings of 2026-11-09, checked by eye on three). Kept: down >= 10 rows to >= 58 (sit_down), up >= 10
rows to <= 50 (stand_up), with 0.36 s of the recording on both sides. Each kept move ->
OUT_DIR/<recording>+<start s>-<label>.bin (replay.sh's format, the events from 1 s before it to 1.5 s after,
not into a neighbouring move) and a .json as the recorder writes it, for dataset.py --ours.
"""
import glob
import json
import os
import sys

import numpy as np

from dataset import activity
from readers import _from0, read_events

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../linux510'))
from dvsact import frames_of  # noqa: E402

SIDE = 6                # steps of stillness looked at on each side of a burst (0.36 s), 12 measured
BEFORE, AFTER = 17, 25  # steps cut before and after a move (1 s, 1.5 s)


def bursts(act):
    """[start, end) step ranges of activity over 1.6 x its median, gaps under 0.3 s joined, >= 0.3 s long"""
    on = np.flatnonzero(act > 1.6 * np.median(act))
    out = []
    for i in on:
        if out and i - out[-1][1] < 5:
            out[-1][1] = i + 1
        elif not out or i >= out[-1][1]:
            out.append([i, i + 1])
    return [(s, e) for s, e in out if e - s >= 5]


def centre_row(a, band):
    """a [rows, cols] counts above each cell's median: the person's centroid row (columns band, less the noise
    of the other columns), None without one"""
    r = np.maximum(a[:, band].sum(1) - a[:, ~band].mean(1) * band.sum(), 0)
    return float((r * np.arange(len(r))).sum() / r.sum()) if r.sum() > 0 else None


def moves(counts):
    """counts [N, 2, GH, GW] -> [(start, end, 'sit_down' | 'stand_up')] of the bursts kept"""
    c = counts.astype(np.float32)
    a = np.maximum(c - np.median(c, 0), 0).sum(1)
    col = a.sum((0, 1))
    band = col > 2 * np.median(col)                     # the person's columns
    out = []
    for s, e in bursts(activity(counts)):
        if s < SIDE or e > len(a) - SIDE:
            continue
        pre, post = centre_row(a[max(s - 12, 0):s].sum(0), band), centre_row(a[e:e + 12].sum(0), band)
        if pre is None or post is None:
            continue
        if post - pre >= 10 and post >= 58:
            out.append((s, e, 'sit_down'))
        elif pre - post >= 10 and post <= 50:
            out.append((s, e, 'stand_up'))
    return out


def main(src, dst):
    os.makedirs(dst, exist_ok=True)
    for b in sorted(glob.glob(os.path.join(src, '*.bin'))):
        meta = json.load(open(b[:-4] + '.json'))
        if meta['label'] not in ('sit_down', 'stand_up') or not meta.get('complete'):
            continue
        e = read_events(b)
        t0s, c = frames_of(e.t, e.x, e.y, e.p, e.w, e.h)
        found = moves(c)
        raw = np.fromfile(b, [('t', '<u4'), ('x', '<u2'), ('y', '<u2')])
        t = _from0(raw['t'])
        for k, (s, e_, label) in enumerate(found):
            s0 = max(s - BEFORE, found[k - 1][1] if k else 0)
            s1 = min(e_ + AFTER, found[k + 1][0] if k + 1 < len(found) else len(t0s))
            keep = (t >= t0s[s0]) & (t < (t0s[s1] if s1 < len(t0s) else t[-1] + 1))
            name = '%s+%.1f-%s' % (os.path.basename(b)[:16], t0s[s0] / 1e6, label)
            raw[keep].tofile(os.path.join(dst, name + '.bin'))
            json.dump(dict(meta, label=label, events=int(keep.sum()), segment_of=os.path.basename(b),
                           segment_s=[round(t0s[s0] / 1e6, 2), round((t0s[s1 - 1] + 60000) / 1e6, 2)]),
                      open(os.path.join(dst, name + '.json'), 'w'))
            print('%-16s %5.1f-%4.1fs %s' % (os.path.basename(b)[:16], s * 0.06, e_ * 0.06, label))


if __name__ == '__main__':
    main(*sys.argv[1:3])
