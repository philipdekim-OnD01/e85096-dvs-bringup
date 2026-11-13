"""evaluate.py --ckpt CKPT --data DIR --holdout FILE: the held-out clips through the model step by step, as on the
board: a 9 x 9 confusion matrix (rows: the clip's label, columns: the argmax of the softmax mean over its masked
steps) and, with the same probabilities through dvsact.Judge, how many fall records each clip gets."""
import argparse
import json
import os
import sys

import numpy as np
import torch

from model import HID, Step

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../linux510'))
from dvsact import CLASSES, Judge  # noqa: E402


def evaluate(ckpt, data, files):
    """-> (confusion int [9, 9], {file: fall records})"""
    m = Step()
    m.load_state_dict(torch.load(ckpt, map_location='cpu')['model'])
    m.eval()
    cm, falls = np.zeros((len(CLASSES), len(CLASSES)), int), {}
    for f in files:
        z = np.load(os.path.join(data, f))
        x = torch.from_numpy(np.log1p(z['counts'].astype(np.float32)))
        h, probs = torch.zeros(1, HID), []
        with torch.no_grad():
            for t in range(len(x)):
                y, h = m(x[t:t + 1], h)
                probs.append(torch.softmax(y[0], 0).numpy())
        probs, judge = np.array(probs), Judge()
        if z['mask'].any():     # a clip with no step to judge (its peak at the end) is in no row
            cm[int(z['cls']), int(np.argmax(probs[z['mask']].mean(0)))] += 1
        falls[f] = sum((r or {}).get('event') == 'fall' for r in map(judge.step, probs))
    return cm, falls


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--ckpt', required=True)
    ap.add_argument('--data', default=os.path.expanduser('~/e850-act-runs/data'))
    ap.add_argument('--holdout', required=True)
    a = ap.parse_args()
    files = json.load(open(a.holdout))
    cm, falls = evaluate(a.ckpt, a.data, files)
    print('%-9s' % 'label' + ''.join('%9s' % c[:8] for c in CLASSES))
    for i, c in enumerate(CLASSES):
        print('%-9s' % c + ''.join('%9d' % v for v in cm[i]))
    print('clips right: %d of %d' % (np.trace(cm), cm.sum()))
    for f in files:
        print('%-50s fall records %d' % (f, falls[f]))
