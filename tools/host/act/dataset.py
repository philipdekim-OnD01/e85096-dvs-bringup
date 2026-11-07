"""Training clips for the action model (spec 7): event files -> 60 ms count frames (dvsact.frames_of, the board's
definition) and the steps a clip's one label applies to.

    dataset.py --paf DIR --daily DIR --ours DIR --out ~/e850-act-runs/data

<out>/<source>/<n>-<name>.npz: t0s, counts (uint8, cut at 255), mask (steps with a loss), cls (CLASSES index);
<out>/manifest.jsonl: one line per clip. Board recordings (--ours: replay.sh's .bin beside the .json) are used
only when complete.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../linux510'))
from dvsact import CLASSES, frames_of  # noqa: E402
from readers import read_events  # noqa: E402

PAF_MAP = {'falling': 'fall', 'walking': 'walk', 'sitting-down': 'sit_down', 'slumping-down': 'sit_down',
           'getting-up': 'stand_up', 'bending': 'pick_up', 'picking-up': 'pick_up', 'tying-shoes': 'pick_up',
           'arm-crossing': 'other', 'kicking': 'other', 'jumping': 'other', 'throwing': 'other',
           'turning-around': 'other', 'waving': 'other'}
DAILY_MAP = {109: 'fall', 107: 'walk', 148: 'sit_down', 93: 'sit_down', 165: 'stand_up', 100: 'pick_up',
             99: 'pick_up', 97: 'pick_up', 80: 'pick_up'}
OTHER_PER_CLASS = 5     # DailyDVS clips of each class not in DAILY_MAP, as 'other'
SMOOTH = 5              # steps, 0.3 s
STILL_FROM = 17         # none / still: a loss from the first step past 1 s (17 x 60 ms)


def daily_class(path):
    """DailyDVS-200 class id from the file name (C<id>P<person>M..S.._<time>.aedat4)"""
    return int(re.match(r'C(\d+)P', os.path.basename(path)).group(1))


def paf_class(path):
    """PAF class from a folder or file name containing it ('sitting-down', 'sitting_down', 'sitting down')"""
    p = path.lower().replace('_', '-').replace(' ', '-')
    for k in sorted(PAF_MAP, key=len, reverse=True):
        if k in p:
            return PAF_MAP[k]
    return None


def noise(c):
    """per polarity, the median cell count: the background (sensor noise)"""
    return np.median(c.reshape(len(c), 2, -1).transpose(1, 0, 2).reshape(2, -1), axis=1)


def activity(c):
    """per step, the counts above the background, 0.3 s centred moving average"""
    a = np.maximum(c.astype(np.float32) - noise(c)[None, :, None, None], 0).sum((1, 2, 3))
    return np.convolve(a, np.ones(SMOOTH) / SMOOTH, 'same')


def label_mask(cls, act):
    """steps the clip's label applies to: none / still from 1 s, an action from its peak + 0.3 s"""
    m = np.zeros(len(act), bool)
    m[STILL_FROM if cls in ('none', 'still') else int(np.argmax(act)) + SMOOTH:] = True
    return m


def build(items, out_dir):
    """items: (path, class, source[, split]); writes the .npz files and manifest.jsonl (made anew)"""
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'manifest.jsonl'), 'w') as man:
        for i, (path, cls, src, *split) in enumerate(items):
            if src == 'ours' and not json.load(open(path[:-4] + '.json')).get('complete'):
                continue
            e = read_events(path)
            t0s, c = frames_of(e.t, e.x, e.y, e.p, e.w, e.h)
            if not len(t0s):
                continue
            mask = label_mask(cls, activity(c))
            name = '%05d-%s.npz' % (i, os.path.splitext(os.path.basename(path))[0])
            os.makedirs(os.path.join(out_dir, src), exist_ok=True)
            np.savez_compressed(os.path.join(out_dir, src, name), t0s=t0s, counts=np.minimum(c, 255).astype(np.uint8),
                                mask=mask, cls=CLASSES.index(cls))
            man.write(json.dumps({'file': src + '/' + name, 'cls': cls, 'src': src, 'split': split[0] if split else None,
                                  'steps': len(t0s), 'masked': int(mask.sum()), 'path': path}) + '\n')
            man.flush()


def items_of(paf, daily, ours):
    items, skipped = [], collections.Counter()
    for p in sorted(glob.glob(os.path.join(paf, '**/*.aedat'), recursive=True)) if paf else []:
        c = paf_class(os.path.relpath(p, paf))
        if c:
            items.append((p, c, 'paf'))
        else:
            skipped['paf: no class in the name'] += 1
    if daily:
        split = {}
        for s in ('train', 'val', 'test'):
            for line in open(os.path.join(daily, s + '.txt')):
                split[os.path.basename(line.split()[0])] = s
        others = collections.Counter()
        for p in sorted(glob.glob(os.path.join(daily, '**/*.aedat4'), recursive=True)):
            k = daily_class(p)
            if k not in DAILY_MAP:
                others[k] += 1
                if others[k] > OTHER_PER_CLASS:
                    continue
            items.append((p, DAILY_MAP.get(k, 'other'), 'daily', split.get(os.path.basename(p))))
    for p in sorted(glob.glob(os.path.join(ours, '*.bin'))) if ours else []:
        items.append((p, json.load(open(p[:-4] + '.json'))['label'], 'ours'))
    return items, skipped


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--paf')
    ap.add_argument('--daily')
    ap.add_argument('--ours')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    items, skipped = items_of(a.paf, a.daily, a.ours)
    build(items, a.out)
    n = collections.Counter((m['src'], m['cls']) for m in map(json.loads, open(os.path.join(a.out, 'manifest.jsonl'))))
    for cls in CLASSES:
        print('%-9s' % cls, '  '.join('%s %d' % (s, n[s, cls]) for s in ('paf', 'daily', 'ours')))
    for k, v in skipped.items():
        print('skipped, %s: %d' % (k, v))
