"""model.Step shapes: python -m unittest test_model (from tools/host/act, ~/.venvs/dvsact)."""
import unittest

import torch

from model import Step


class TestModel(unittest.TestCase):
    def test_step_shapes(self):
        y, h = Step()(torch.zeros(2, 2, 120, 160), torch.zeros(2, 128))
        self.assertEqual((tuple(y.shape), tuple(h.shape)), ((2, 9), (2, 128)))

    def test_imagenet_first_conv(self):
        self.assertEqual(tuple(Step.imagenet().enc[0][0].weight.shape), (16, 2, 3, 3))


if __name__ == '__main__':
    unittest.main()
