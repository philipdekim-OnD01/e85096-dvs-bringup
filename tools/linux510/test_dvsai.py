"""libdvsview's classifier and face width in C (dvsai.c, 11.112) against their Python references (dvsact.observe, the
Python onnxruntime on dvsact.to_input, dvsface.detect), and its buffer notes: python3 -m unittest test_dvsai.
Needs a libdvsview built with -DDVS_AI, onnxruntime and the models (DVSVIEW_LIB, ACT_MODEL, FACE_MODEL: the board's
by default; on the Mac the Docker arm64 image of 11.112)."""
import ctypes
import json
import os
import unittest

import numpy as np

import dvsface
from dvsact import CLASSES, STATUS, Judge, observe, ort_lib, to_input

LIB = os.environ.get('DVSVIEW_LIB', '/usr/local/lib/libdvsview.so')
ACT = os.environ.get('ACT_MODEL', '/var/lib/dvsact/model.onnx')
FACE = os.environ.get('FACE_MODEL', '/var/lib/dvsview/face.onnx')
NOW = 1794030632123456000

lib = ctypes.CDLL(LIB)
lib.dvs_act_observe.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_char_p, ctypes.c_int]
lib.dvs_act_step.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
lib.dvs_face_detect.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_float, ctypes.c_void_p,
                                ctypes.c_char_p, ctypes.c_int]
ERR = ctypes.create_string_buffer(512)


def session(model):
    import onnxruntime as ort
    o = ort.SessionOptions()
    o.intra_op_num_threads = o.inter_op_num_threads = 1
    return ort.InferenceSession(model, o, providers=['CPUExecutionProvider'])


def c_status():
    buf = ctypes.create_string_buffer(8192)
    assert lib.dvs_act_status(buf, len(buf)) > 0
    return buf.value.decode()


def steps(rng, n):
    """class probabilities that hold a class for 1..6 steps, falls also under FALL_P"""
    out = []
    while len(out) < n:
        k, p = int(rng.integers(len(CLASSES))), float(rng.uniform(0.3, 0.95))
        v = rng.dirichlet(np.ones(len(CLASSES))) * (1 - p)
        v[k] += p
        out += [v.astype(np.float32)] * int(rng.integers(1, 7))
    return out[:n]


class TestDvsai(unittest.TestCase):
    def test_buf_note(self):        # the page's DRAM graph: fewest free, most waiting since the last take
        v = (ctypes.c_int * 4)()
        lib.dvs_buf_take(v)
        for free, wait in ((6, 0), (4, 2), (7, 1)):
            lib.dvs_buf_note(8, free, wait, 1 << 20)
        lib.dvs_buf_take(v)
        self.assertEqual(list(v), [8, 4, 2, 1 << 20])
        lib.dvs_buf_take(v)
        self.assertEqual(list(v), [8, -1, -1, 1 << 20])

    def test_observe(self):         # the judge, the JSONL lines and GET /act's JSON, step by step, as dvsact.observe
        rng, j, line = np.random.default_rng(1), Judge(), ctypes.create_string_buffer(256)
        for phase, classes in enumerate((None, ['none', 'sit_down', 'stand_up'])):
            if classes:
                j.keep = np.isin(CLASSES, classes)
                self.assertEqual(lib.dvs_act_classes(' '.join(classes).encode()), 3)
            else:                       # dvs_act_start (test_act_step) read the classes file beside the model
                self.assertEqual(lib.dvs_act_classes(None), len(CLASSES))
            changes = 0
            for i, p in enumerate(steps(rng, 3000)):
                want = observe(j, p, NOW + i * 60000000)
                got = lib.dvs_act_observe(p.ctypes.data, NOW + i * 60000000, line, len(line))
                self.assertEqual(line.value.decode() if got else None, want, (phase, i))
                self.assertEqual(json.loads(c_status()), dict(STATUS, on=False), (phase, i))
                changes += want is not None
            self.assertGreater(changes, 100)
        self.assertEqual(c_status(), json.dumps(dict(STATUS, on=False)))
        self.assertEqual(lib.dvs_act_classes(b'nonsense'), len(CLASSES))   # none known: all

    def test_act_step(self):        # the model step (log1p, GRU state, softmax) as the Python onnxruntime
        self.assertEqual(lib.dvs_act_start(ort_lib().encode(), ACT.encode(), b'', 0, ERR, len(ERR)), 0, ERR.value)
        sess, rng, h = session(ACT), np.random.default_rng(2), np.zeros((1, 128), np.float32)
        probs, worst = np.zeros(len(CLASSES), np.float32), 0.0
        for i in range(40):
            if i == 25:                 # a reset: h = 0 on both sides
                self.assertEqual(lib.dvs_act_step(None, None, ERR, len(ERR)), 0)
                h[:] = 0
            c = np.minimum(rng.poisson(rng.uniform(0.05, 3), (2, 120, 160)), 65535).astype(np.uint16)
            self.assertEqual(lib.dvs_act_step(c.ctypes.data, probs.ctypes.data, ERR, len(ERR)), 0, ERR.value)
            y, h = sess.run(None, {'x': to_input(c), 'h': h})
            e = np.exp(y[0] - y[0].max())
            worst = max(worst, float(np.abs(probs - e / e.sum()).max()))
        self.assertLess(worst, 1e-5)

    def test_face(self):            # YuNet on event images, the best box over a threshold, as dvsface.detect
        self.assertEqual(lib.dvs_face_start(ort_lib().encode(), FACE.encode(), 0, ERR, len(ERR)), 0, ERR.value)
        sess, rng, box = session(FACE), np.random.default_rng(3), np.zeros(5, np.float32)
        yy, xx = np.mgrid[:360, :480]
        for i in range(12):
            img = (rng.random((360, 480)) < rng.uniform(0, 0.05)).astype(np.uint8) * rng.integers(1, 256)
            cx, cy, a, b = rng.uniform(100, 380), rng.uniform(80, 280), rng.uniform(20, 70), rng.uniform(30, 90)
            r = ((xx - cx) / a) ** 2 + ((yy - cy) / b) ** 2         # an oval outline, eyes, a mouth
            img[abs(r - 1) < 0.08] = 200
            for ex in (-a / 2.5, a / 2.5):
                img[((xx - cx - ex) ** 2 + (yy - cy + b / 4) ** 2) < (a / 8) ** 2] = 90
            img[(abs(yy - cy - b / 2.5) < 2) & (abs(xx - cx) < a / 3)] = 30
            for thresh in (0.0, 0.4):
                want = dvsface.detect(sess, np.where(img > 0, 255, 0).astype(np.uint8), thresh)
                got = lib.dvs_face_detect(img.ctypes.data, 480, 360, ctypes.c_float(thresh), box.ctypes.data,
                                          ERR, len(ERR))
                self.assertEqual(got, int(want is not None), (i, thresh))
                if want:
                    np.testing.assert_allclose(box, np.float32(want), rtol=1e-4, atol=1e-3, err_msg=str((i, thresh)))


if __name__ == '__main__':
    unittest.main()
