#!/usr/bin/env python3
"""evaluate.py GT.json DET.json... [--out results/accuracy.json]: detection accuracy of run_benchmark.py --accuracy files.

COCO mAP50-95 and mAP50 with pycocotools (the 200 images of data/prepare.py), and at confidence 0.25, IoU 0.5:
precision, recall, F1 and the miss rate (ground-truth boxes not found / all; crowd boxes ignored), matching each class
greedily by score. Runs where pycocotools is (the export container). Adds to --out, one entry per DET file name.
"""
import argparse
import contextlib
import io
import json
import os
from collections import defaultdict

import numpy as np
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval


def iou(a, b):
    """a: 4, b: N x 4, xywh"""
    x1, y1 = np.maximum(a[0], b[:, 0]), np.maximum(a[1], b[:, 1])
    x2, y2 = np.minimum(a[0] + a[2], b[:, 0] + b[:, 2]), np.minimum(a[1] + a[3], b[:, 1] + b[:, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    return inter / (a[2] * a[3] + b[:, 2] * b[:, 3] - inter + 1e-9)


def prf(gt, det, conf=0.25, thr=0.5):
    g = defaultdict(list)
    for a in gt.dataset['annotations']:
        if not a['iscrowd']:
            g[a['image_id'], a['category_id']].append(a['bbox'])
    d = defaultdict(list)
    for x in det:
        if x['score'] >= conf:
            d[x['image_id'], x['category_id']].append((x['score'], x['bbox']))
    tp = fp = 0
    for k in set(g) | set(d):
        gb = np.array(g.get(k, []), np.float64).reshape(-1, 4)
        used = np.zeros(len(gb), bool)
        for _, b in sorted(d.get(k, []), key=lambda v: -v[0]):
            if len(gb):
                o = iou(np.array(b), gb)
                o[used] = 0
                j = o.argmax()
                if o[j] >= thr:
                    used[j] = True
                    tp += 1
                    continue
            fp += 1
    n = sum(len(v) for v in g.values())
    p, r = tp / max(1, tp + fp), tp / max(1, n)
    return {'precision': round(p, 4), 'recall': round(r, 4), 'f1': round(2 * p * r / max(1e-9, p + r), 4),
            'miss_rate': round(1 - r, 4), 'tp': tp, 'fp': fp, 'gt': n, 'conf': conf, 'iou': thr}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('gt')
    ap.add_argument('det', nargs='+')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                  'results', 'accuracy.json'))
    a = ap.parse_args()
    with contextlib.redirect_stdout(io.StringIO()):
        gt = COCO(a.gt)
    res = json.load(open(a.out)) if os.path.exists(a.out) else {}
    for f in a.det:
        det = json.load(open(f))
        r = {'images': len(gt.getImgIds()), 'detections': len(det)}
        if det:
            with contextlib.redirect_stdout(io.StringIO()):
                e = COCOeval(gt, gt.loadRes(f), 'bbox')
                e.params.imgIds = sorted(gt.getImgIds())
                e.evaluate()
                e.accumulate()
                e.summarize()
            r.update(map50_95=round(float(e.stats[0]), 4), map50=round(float(e.stats[1]), 4), **prf(gt, det))
        res[os.path.basename(f)] = r
        print(os.path.basename(f), r)
    json.dump(res, open(a.out, 'w'), indent=1)


if __name__ == '__main__':
    main()
