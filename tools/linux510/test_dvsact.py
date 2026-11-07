"""dvsact's frames and judge, numpy only: python3 -m unittest test_dvsact (Mac venv, or board /var/tmp)."""
import contextlib
import ctypes
import io
import os
import tempfile
import unittest

import numpy as np

from dvsact import CLASSES, EV, Binner, Judge, counts, line, split, start, to_input

LIB = os.environ.get('DVSVIEW_LIB', '/usr/local/lib/libdvsview.so')


def push_ms(b, start, n):
    """an ON event at (10, 10) every 1 ms from start (us) on the uint32 clock, pushed 7 at a time"""
    t = ((start + 1000 * np.arange(n)) % 2**32).astype(np.uint32)
    one = np.full(n, 10, np.uint16)
    out = []
    for i in range(0, n, 7):
        out += b.push(t[i:i + 7], one[i:i + 7], one[i:i + 7], np.zeros(len(t[i:i + 7]), np.uint16))
    return out


def random_ev(seed, n=20000):
    """struct ev records over 400 ms across the uint32 wrap, rows with the OFF and SUP bits"""
    rng = np.random.default_rng(seed)
    ev = np.zeros(n, EV)
    ev['t'] = (2**32 - 150000 + np.sort(rng.integers(0, 400000, n))) % 2**32
    ev['x'] = rng.integers(0, 960, n)
    ev['y'] = rng.integers(0, 720, n) | rng.integers(0, 2, n) << 15 | rng.integers(0, 2, n) << 14
    return ev


def push_chunks(b, ev, ev_path=False, seed=0):
    rng, out, i = np.random.default_rng(seed), [], 0
    while i < len(ev):
        j = i + int(rng.integers(1, 3000))
        out += b.push_ev(ev[i:j]) if ev_path else b.push(*split(ev[i:j]))
        i = j
    return out


def onehot(k, p):
    v = np.full(len(CLASSES), (1 - p) / (len(CLASSES) - 1), np.float32)
    v[k] = p
    return v


class TestDvsact(unittest.TestCase):
    def test_counts_cells(self):
        c = counts(np.array([0, 5, 959]), np.array([0, 5, 719]), np.array([0, 0, 1]))
        self.assertEqual(c.shape, (2, 120, 160))
        self.assertEqual(c[0, 0, 0], 2)
        self.assertEqual(c[1, 119, 159], 1)
        self.assertEqual(counts(np.array([345]), np.array([259]), np.array([0]), 346, 260)[0, 119, 159], 1)

    def test_to_input(self):
        x = to_input(np.full((2, 120, 160), 3, np.uint16))
        self.assertEqual((x.shape, x.dtype), ((1, 2, 120, 160), np.float32))
        self.assertAlmostEqual(float(x[0, 0, 0, 0]), np.log1p(3), 6)

    def test_binner_bins(self):
        out = push_ms(Binner(), start=0, n=200)
        self.assertEqual([k for k, *_ in out], ['frame'] * 3)
        self.assertEqual([int(f[2].sum()) for f in out], [60, 60, 60])

    def test_binner_wrap(self):     # the display clock is uint32 us and wraps every 71.6 min
        out = push_ms(Binner(), start=2**32 - 90000, n=200)
        self.assertEqual([k for k, *_ in out], ['frame'] * 3)
        self.assertEqual([int(f[2].sum()) for f in out], [60, 60, 60])

    def test_binner_gap(self):      # a sensor restart: no data for > GAP_US
        b = Binner()
        out = push_ms(b, 0, 100) + push_ms(b, 2100000, 100)
        self.assertNotIn('reset', [k for k, *_ in out])
        b = Binner()
        out = push_ms(b, 0, 100) + push_ms(b, 5100000, 100)
        self.assertEqual([k for k, *_ in out].count('reset'), 1)
        self.assertTrue(all(f[2].sum() > 0 for f in out if f[0] == 'frame'))

    def test_binner_counts(self):    # each frame = counts() of its 60 ms, whatever the chunks, across the wrap
        rng = np.random.default_rng(1)
        n = 20000
        rel = np.sort(rng.integers(0, 400000, n))
        t = ((2**32 - 150000 + rel) % 2**32).astype(np.uint32)
        x, y, p = rng.integers(0, 960, n), rng.integers(0, 720, n), rng.integers(0, 2, n)
        b, out, i = Binner(), [], 0
        while i < n:
            j = i + int(rng.integers(1, 3000))
            out += b.push(t[i:j], x[i:j], y[i:j], p[i:j])
            i = j
        self.assertEqual([f[1] for f in out], [k * 60000 for k in range(6)])
        for _, t0, c in out:
            m = (rel - rel[0] >= t0) & (rel - rel[0] < t0 + 60000)
            self.assertTrue((c == counts(x[m], y[m], p[m])).all())

    def test_push_ev(self):         # the board's path (struct ev records, cells counted in C or numpy) = push()
        ev, frames = random_ev(3), lambda out: [(k, t0, c.tolist()) for k, t0, c in out]
        self.assertEqual(frames(push_chunks(Binner(), ev, ev_path=True)), frames(push_chunks(Binner(), ev)))

    @unittest.skipUnless(os.path.exists(LIB), 'no libdvsview.so (DVSVIEW_LIB)')
    def test_push_ev_c(self):       # dvs_cells() in libdvsview counts as numpy does
        lib = ctypes.CDLL(LIB)
        ev, frames = random_ev(4), lambda out: [(k, t0, c.tolist()) for k, t0, c in out]
        out = push_chunks(Binner(cells=lib.dvs_cells), ev, ev_path=True)
        self.assertTrue(out)
        self.assertEqual(frames(out), frames(push_chunks(Binner(), ev)))

    def test_judge(self):
        j, P = Judge(), lambda k, p=0.9: onehot(CLASSES.index(k), p)
        self.assertIsNone([j.step(P('none')) for _ in range(3)][-1])
        r = [j.step(P('walk')) for _ in range(3)]
        self.assertEqual((r[1], r[2]['event'], r[2]['prev']), (None, 'appear', 'none'))
        self.assertTrue(all(j.step(P('fall', 0.55)) is None for _ in range(5)))
        self.assertEqual([j.step(P('fall', 0.7)) for _ in range(3)][-1]['event'], 'fall')
        self.assertEqual([j.step(P('still')) for _ in range(3)][-1]['event'], 'still')
        self.assertEqual([j.step(P('none')) for _ in range(3)][-1]['event'], 'none')
        self.assertEqual([j.step(P('fall')) for _ in range(3)][-1]['event'], 'fall')   # fall first, even after none

    def test_line(self):
        s = line({'event': 'fall', 'state': 'fall', 'prev': 'walk', 'p': 0.8734}, 1794030632123456000)
        self.assertEqual(s, '{"t_us": 1794030632123456, "time": "2026-11-07T05:50:32.123456Z", '
                            '"event": "fall", "state": "fall", "prev": "walk", "p": 0.87}')

    def test_start_missing_model(self):     # no model yet: the live view goes on without the classifier
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stderr(err):
            self.assertIsNone(start(object(), d + '/a.jsonl', '/nonexistent.onnx'))
        self.assertIn('[dvsact] off', err.getvalue())


if __name__ == '__main__':
    unittest.main()
