"""train.py: augmentation and a tiny overfit: python -m unittest test_train (from tools/host/act, ~/.venvs/dvsact)."""
import json
import os
import sys
import tempfile
import unittest

import numpy as np
import torch

from evaluate import evaluate
from model import Step
from train import OURS_REPEAT, augment, batches, collate, load, seq_loss

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../linux510'))
from dvsact import CLASSES  # noqa: E402


def run_tiny(steps):
    """two classes, motion at the left or the right, 4 clips of 10 steps: the loss at the first and last step"""
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    clips = []
    for k in range(4):
        c = rng.poisson(2, (10, 2, 120, 160)).astype(np.uint8)
        c[3:, :, 40:80, (10, 110)[k % 2]:(10, 110)[k % 2] + 40] += 20
        clips.append({'counts': c, 'mask': np.arange(10) >= 3, 'cls': k % 2})
    m = Step()
    opt = torch.optim.AdamW(m.parameters(), lr=1e-3)
    w = torch.zeros(9)
    w[:2] = 0.5
    losses = []
    for _ in range(steps):
        x, present, mask, y = collate(clips, rng, public=False, augmenting=False)
        loss = seq_loss(m, x, present, mask, y, w)
        opt.zero_grad()
        loss.backward()
        opt.step()
        losses.append(loss.item())
    return losses[0], losses[-1]


class TestTrain(unittest.TestCase):
    def test_augment(self):
        c = np.random.default_rng(0).integers(0, 5, (10, 2, 120, 160)).astype(np.uint8)
        a = augment(c, np.random.default_rng(1), public=True)
        self.assertEqual((a.shape, a.dtype), (c.shape, np.uint16))
        self.assertTrue(np.array_equal(augment(c, np.random.default_rng(1), True), a))   # same seed, same result
        self.assertFalse(np.array_equal(augment(c, np.random.default_rng(2), True), a))

    def test_augment_board_background(self):    # the board's own empty scene under a public clip, not moved with it
        c = np.zeros((10, 2, 120, 160), np.uint8)
        bg = np.zeros((30, 2, 120, 160), np.uint8)
        bg[:, 0, 5, 7] = np.arange(30)          # a hot pixel counting the steps: the window is 10 steps in a row
        a = augment(c, np.random.default_rng(1), public=True, bg=[bg])
        self.assertEqual(np.diff(a[:, 0, 5, 7].astype(int)).tolist(), [1] * 9)
        a[:, 0, 5, 7] = 0
        self.assertEqual(int(a.sum()), 0)       # nothing else: no synthetic noise on top

    def test_augment_no_empty_border(self):     # zoom / shift left 20% of a board clip at 0: the model learned 'none'
        c = np.full((4, 2, 120, 160), 3, np.uint8)  # from the empty strip, which the board's input never has (11.43)
        rng = np.random.default_rng(0)
        self.assertTrue(all((augment(c, rng, public=False) > 0).all() for _ in range(20)))

    def test_load_mix(self):        # public train clips and the board clips, the latest board clip of a class held out
        with tempfile.TemporaryDirectory() as d:
            man = [('paf/0.npz', 'paf', None, 'fall', 'a.aedat'), ('daily/1.npz', 'daily', 'val', 'fall', 'b.aedat4'),
                   ('ours/2.npz', 'ours', None, 'none', '20261108T140000Z-none.bin'),
                   ('ours/3.npz', 'ours', None, 'none', '20261108T140100Z-none.bin')]
            with open(d + '/manifest.jsonl', 'w') as f:
                for file, src, split, cls, path in man:
                    os.makedirs(os.path.dirname(os.path.join(d, file)), exist_ok=True)
                    np.savez(os.path.join(d, file), counts=np.zeros((3, 2, 120, 160), np.uint8), mask=np.ones(3, bool),
                             cls=CLASSES.index(cls))
                    f.write(json.dumps({'file': file, 'src': src, 'split': split, 'cls': cls, 'path': path}) + '\n')
            clips = load(d, 'mix', 1, d + '/m.pt')
            held = json.load(open(d + '/m.pt.holdout.json'))
        self.assertGreater(OURS_REPEAT, 1)  # 16 board sit/stand clips against 270 public ones: each one several times
        self.assertEqual(sorted(c['src'] for c in clips), ['ours'] * OURS_REPEAT + ['paf'])
        self.assertEqual(held, ['ours/3.npz'])

    def test_batches(self):         # MPS memory grows with the frames in a step (~9 MB each): a frame budget
        lengths = [100, 100, 100, 300, 50, 20, 20, 20, 20, 20, 20, 20, 20, 20]
        b = list(batches(lengths, range(len(lengths)), frames=250, clips=8))
        self.assertEqual(sorted(i for x in b for i in x), list(range(len(lengths))))   # every clip once
        self.assertIn([3], b)                       # a clip over the budget goes alone
        self.assertTrue(all(len(x) <= 8 and (len(x) == 1 or sum(lengths[i] for i in x) <= 250) for x in b))

    def test_collate_pads(self):    # clips of 3 and 5 steps: padding is neither input nor loss
        rng = np.random.default_rng(0)
        clips = [{'counts': np.ones((n, 2, 120, 160), np.uint8), 'mask': np.ones(n, bool), 'cls': 1} for n in (3, 5)]
        x, present, mask, y = collate(clips, rng, public=False, augmenting=False)
        self.assertEqual(tuple(x.shape), (2, 5, 2, 120, 160))
        self.assertEqual(present.tolist(), [[True] * 3 + [False] * 2, [True] * 5])
        self.assertEqual(mask.tolist(), present.tolist())
        self.assertEqual(float(x[0, 3:].abs().sum()), 0.0)

    def test_evaluate(self):        # every clip lands in one row of the confusion matrix; falls are counted per clip
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(d + '/ours')
            files = []
            for k, cls in enumerate((7, 2, 2)):
                f = 'ours/%d.npz' % k
                np.savez(os.path.join(d, f), t0s=np.arange(20) * 60000, mask=np.arange(20) >= 5, cls=cls,
                         counts=np.random.default_rng(k).poisson(2, (20, 2, 120, 160)).astype(np.uint8))
                files.append(f)
            torch.save({'model': Step().state_dict()}, d + '/m.pt')
            cm, falls = evaluate(d + '/m.pt', d, files)
        self.assertEqual((cm.shape, int(cm.sum()), int(cm[7].sum()), int(cm[2].sum())), ((9, 9), 3, 1, 2))
        self.assertEqual(sorted(falls), sorted(files))

    def test_evaluate_empty_mask(self):     # no step with a loss (its peak at the clip's end): not a row, falls counted
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(d + '/ours')
            np.savez(d + '/ours/0.npz', t0s=np.arange(20) * 60000, mask=np.zeros(20, bool), cls=4,
                     counts=np.random.default_rng(0).poisson(2, (20, 2, 120, 160)).astype(np.uint8))
            torch.save({'model': Step().state_dict()}, d + '/m.pt')
            cm, falls = evaluate(d + '/m.pt', d, ['ours/0.npz'])
        self.assertEqual((int(cm.sum()), list(falls)), (0, ['ours/0.npz']))

    def test_overfit_tiny(self):
        first, last = run_tiny(steps=30)
        self.assertLess(last, 0.5 * first)


if __name__ == '__main__':
    unittest.main()
