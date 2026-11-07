"""dataset.py: class maps, step labels, build: python -m unittest test_dataset (from tools/host/act, ~/.venvs/dvsact)."""
import glob
import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../linux510'))
from dataset import DAILY_MAP, PAF_MAP, activity, build, daily_class, label_mask, paf_class  # noqa: E402
from dvsact import CLASSES, EV  # noqa: E402


def only(d, ext):
    f = glob.glob(os.path.join(d, '*' + ext))
    assert len(f) == 1, f
    return f[0]


def clip(path, complete):
    """a 6 s board clip as --replay writes it: an ON event at (100, 100) every 1 ms, and its .json"""
    ev = np.zeros(6000, EV)
    ev['t'], ev['x'], ev['y'] = 1000 * np.arange(6000), 100, 100
    ev.tofile(path)
    json.dump({'label': 'walk', 'events': 6000, 'complete': complete}, open(path[:-4] + '.json', 'w'))


class TestDataset(unittest.TestCase):
    def test_maps(self):
        self.assertEqual((PAF_MAP['falling'], PAF_MAP['slumping-down'], PAF_MAP['getting-up']),
                         ('fall', 'sit_down', 'stand_up'))
        self.assertEqual((DAILY_MAP[109], DAILY_MAP[148], DAILY_MAP[100]), ('fall', 'sit_down', 'pick_up'))
        self.assertTrue(set(PAF_MAP.values()) | set(DAILY_MAP.values()) <= set(CLASSES))
        self.assertEqual((daily_class('action_110/C109P3M0S1_20231111.aedat4'), daily_class('x/C13P1M0S2_1.aedat4')),
                         (109, 13))
        # the folder names of the PAF download (Tongji SharePoint, 2026-11-07)
        self.assertEqual([paf_class(p) for p in ('fall/sit-down/f_kaibo_1.1.aedat', 'action/get-up/a.aedat',
                                                 'fall/falling down/b.aedat', 'action/arm crossing/c.aedat',
                                                 'fall/tying shoes/d.aedat', 'toy.aedat')],
                         ['sit_down', 'stand_up', 'fall', 'other', 'pick_up', None])

    def test_label_mask_peak(self):     # the movement peaks at step 40
        c = np.full((100, 2, 120, 160), 2, np.uint16)
        c[38:43, :, 50:60, 70:80] += 30
        m = label_mask('fall', activity(c))
        self.assertFalse(m[:45].any())
        self.assertTrue(m[45:].all())

    def test_label_mask_onset(self):    # motion from step 30 to the end, its peak at step 82
        c = np.full((100, 2, 120, 160), 2, np.uint16)
        c[30:, :, 50:60, 70:80] += 20
        c[80:85, :, 50:60, 70:80] += 10
        for cls in ('walk', 'other'):   # motion all through: from its onset (step 30) + 0.3 s
            m = label_mask(cls, activity(c))
            self.assertFalse(m[:35].any())
            self.assertTrue(m[35:].all())
        m = label_mask('fall', activity(c))     # an action that happens once: from its peak + 0.3 s
        self.assertFalse(m[:87].any())
        self.assertTrue(m[87:].all())

    def test_label_mask_still(self):
        m = label_mask('still', activity(np.full((100, 2, 120, 160), 2, np.uint16)))
        self.assertFalse(m[:17].any())
        self.assertTrue(m[17:].all())

    def test_build_skips_incomplete(self):  # a recording cut short (complete: false) is not training data
        with tempfile.TemporaryDirectory() as d:
            a, b, out = d + '/a.bin', d + '/b.bin', d + '/out'
            clip(a, True)
            clip(b, False)
            build([(a, 'walk', 'ours'), (b, 'walk', 'ours')], out)
            lines = open(out + '/manifest.jsonl').readlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])['cls'], 'walk')
            z = np.load(only(out + '/ours', '.npz'))
            self.assertEqual((z['counts'].dtype, z['counts'].shape[1:]), (np.uint8, (2, 120, 160)))
            self.assertEqual((len(z['t0s']), len(z['mask']), int(z['cls'])), (99, 99, CLASSES.index('walk')))


if __name__ == '__main__':
    unittest.main()
