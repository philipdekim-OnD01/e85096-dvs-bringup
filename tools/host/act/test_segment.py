"""segment.py: moves found and labelled by the person's height: python -m unittest test_segment (~/.venvs/dvsact)."""
import unittest

import numpy as np

from segment import moves


def clip(seated_first, burst=(40, 50), n=100):
    """noise everywhere; a person in columns 60-100, standing (rows 15-75) or seated (rows 45-105: the cells both
    share are at their median, as on the board), switching in a burst of motion"""
    rng = np.random.default_rng(0)
    c = rng.poisson(2, (n, 2, 120, 160)).astype(np.int64)
    for k in range(n):
        seated = (k < burst[0]) == seated_first
        c[k, :, 45 if seated else 15:105 if seated else 75, 60:100] += 30 if burst[0] <= k < burst[1] else 8
    return c


class TestSegment(unittest.TestCase):
    def test_sit_down(self):
        (s, e, label), = moves(clip(seated_first=False))
        self.assertEqual(label, 'sit_down')
        self.assertTrue(abs(s - 40) <= 3 and abs(e - 50) <= 3, (s, e))

    def test_stand_up(self):
        self.assertEqual([m[2] for m in moves(clip(seated_first=True))], ['stand_up'])

    def test_still(self):           # no burst: no move
        self.assertEqual(moves(clip(seated_first=False, burst=(0, 0))), [])

    def test_at_the_edge(self):     # a move cut by the recording's start: no stillness before it, not kept
        self.assertEqual(moves(clip(seated_first=False, burst=(0, 10))), [])


if __name__ == '__main__':
    unittest.main()
