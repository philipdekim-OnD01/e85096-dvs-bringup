"""read_events() on the three formats: python -m unittest test_readers (from tools/host/act, ~/.venvs/dvsact)."""
import glob
import os
import tempfile
import unittest

import numpy as np

from readers import read_events

DATA = os.path.expanduser('~/e850-act-data')


class TestReaders(unittest.TestCase):
    def test_paf_toy(self):
        e = read_events(os.path.join(DATA, 'paf/toy.aedat'))
        self.assertEqual((e.w, e.h), (346, 260))
        self.assertTrue(len(e.t) > 1000 and (np.diff(e.t) >= 0).all() and e.t[0] == 0)
        self.assertTrue(0 <= e.x.min() and e.x.max() < 346 and 0 <= e.y.min() and e.y.max() < 260)
        self.assertTrue(set(np.unique(e.p)) <= {0, 1})

    def test_replay_bin(self):
        raw = np.array([(1000, 5, 16), (1494, 5, 23 | 1 << 15 | 1 << 14), (2000, 959, 719)],
                       dtype=[('t', '<u4'), ('x', '<u2'), ('y', '<u2')])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'a.bin')
            raw.tofile(path)
            e = read_events(path)
        self.assertEqual(e.t.tolist(), [0, 494, 1000])
        self.assertEqual(e.y.tolist(), [16, 23, 719])
        self.assertEqual(e.p.tolist(), [0, 1, 0])
        self.assertEqual((e.w, e.h), (960, 720))

    def test_replay_bin_wrap(self):     # the display clock is uint32 us: a clip may cross its wrap
        raw = np.array([(2**32 - 10, 1, 1), (5, 2, 2)], dtype=[('t', '<u4'), ('x', '<u2'), ('y', '<u2')])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'a.bin')
            raw.tofile(path)
            self.assertEqual(read_events(path).t.tolist(), [0, 15])

    def test_aedat4_one(self):
        files = sorted(glob.glob(os.path.join(DATA, 'dailydvs/**/*.aedat4'), recursive=True))
        if not files:
            self.skipTest('no DailyDVS-200 file in ~/e850-act-data/dailydvs')
        e = read_events(files[0])
        self.assertEqual((e.w, e.h), (320, 240))
        self.assertTrue(e.x.max() < 320 and e.y.max() < 240 and e.t[0] == 0)
        self.assertTrue(set(np.unique(e.p)) <= {0, 1})


if __name__ == '__main__':
    unittest.main()
