import re
import unittest

import numpy as np

import dvsfuse_page as P


class TestImages(unittest.TestCase):
    def test_same_every_time(self):
        a, b = P.images(), P.images()
        self.assertEqual([n for n, _ in a], [n for n, _ in b])
        for (_, x), (_, y) in zip(a, b):
            np.testing.assert_array_equal(x, y)

    def test_sequence(self):
        im = P.images()
        self.assertEqual(len(im), P.SLOTS)
        for k, (name, x) in enumerate(im):
            self.assertEqual(x.shape, (P.GH, P.GW))
            self.assertEqual(set(np.unique(x)) - {0, 255}, set())
            if k % P.CHECK_EVERY == 0:               # the checker comes back to check the alignment
                self.assertEqual(name, 'checker')
                self.assertEqual(x[0, 0], 0)         # top-left square black, as dvs3d-checker.html
                self.assertEqual(x[0, P.GW // 10], 255)
            else:
                self.assertNotEqual(name, 'checker')
                self.assertTrue(0.03 < (x > 0).mean() < 0.97, name)
        self.assertEqual(len({x.tobytes() for _, x in im}), P.SLOTS - P.SLOTS // P.CHECK_EVERY + 1)


class TestDrawn(unittest.TestCase):
    def test_vector_matches_the_design(self):
        """an iPad's file preview showed a black page with <img src="data:..."> (11.61): the images are drawn as
        inline SVG paths, and the truth is those paths rasterized"""
        for name, img in P.images()[:12]:
            d = P.path_d(img)
            self.assertNotIn('data:', d)
            truth = P.raster(d)
            self.assertEqual(truth.shape, (P.GH, P.GW))
            self.assertLess(abs((truth - img / 255.0).mean()), 0.01, name)       # no thinning or thickening
            self.assertLess(np.abs(truth - img / 255.0).mean(), 0.1, name)     # finest bars: the design's stairs

    def test_holes(self):
        img = np.zeros((P.GH, P.GW), np.uint8)
        img[50:150, 50:250] = 255
        img[80:120, 100:200] = 0
        self.assertLess(np.abs(P.raster(P.path_d(img)) - img / 255.0).mean(), 0.01)


class TestPage(unittest.TestCase):
    def test_one_image_per_slot(self):
        html = P.page(P.images())
        self.assertEqual(html.count('<svg '), P.SLOTS)
        self.assertNotIn('data:image', html)
        self.assertIn('%gs' % (P.SLOTS * P.SLOT_S), html)          # the whole cycle
        delays = [float(d) for d in re.findall(r'animation-delay: *([\d.]+)s', html)]
        self.assertEqual(delays, [k * P.SLOT_S for k in range(1, P.SLOTS)])   # slot 0 starts at once


if __name__ == '__main__':
    unittest.main()
