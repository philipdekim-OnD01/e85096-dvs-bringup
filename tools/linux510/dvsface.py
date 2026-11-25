"""dvsface: a face's width on the DVS view (linux510 docs 11.99). A CNN face detector (OpenCV zoo YuNet,
face_detection_yunet_2023mar.onnx, MIT; trained on photos; bundle.sh makes its input size free: the
graph has no fixed shape but the input's) on the latest events as an image, the best face boxed on
the view with its width in cm: the 960 px view shows 17 cm wide on the monitor, and a 19 cm face 1 m away shows 3 cm
there (the user's measurements, 2026-11-24), so the width holds for a face about 1 m from the sensor.
A thread on CPU4-6 (with dvsact), about 5 detections/s; ov5647-cap.py --face MODEL starts it. Since
11.112 the thread is C (libdvsview's dvsai.c, dvs_face_detect); detect() here is its reference (test_dvsai).

  python3 dvsface.py --selftest MODEL [IMAGE]   decode and scale checks (IMAGE: a photo with one face)
  python3 dvsface.py --free-size SRC DST        the model with a free input size (needs the onnx package)
"""
import ctypes
import os
import sys

import numpy as np

VIEW_W, VIEW_H = 960, 720           # dvsview's XS x YS; dvs_surface() is half that (2 x 2 px), the model's input
SCREEN_CM, FACE_CM, FACE_SCREEN_CM = 17.0, 19.0, 3.0
CM_PER_PX = SCREEN_CM / VIEW_W * FACE_CM / FACE_SCREEN_CM      # cm of a face 1 m away per view px
WIN_US = 100000                     # events of the last 100 ms make the image: a still face fires little
THRESH = 0.4                        # edge images of a face scored 0.44..0.53 (a photo 0.95)


def to_input(img):
    """img: H x W (gray) or H x W x 3 uint8 -> 1 x 3 x H' x W' float32, padded to multiples of 32 (the strides)
    at the bottom and right, at its own size: 480 x 360 costs 0.45 of 640 x 640 (YuNet takes BGR 0..255 as is;
    gray is the same in all three)."""
    if img.ndim == 2:
        img = img[:, :, None]
    h, w = img.shape[:2]
    x = np.zeros((1, 3, -(-h // 32) * 32, -(-w // 32) * 32), np.float32)
    x[0, :, :h, :w] = img[:, :, ::-1].transpose(2, 0, 1)
    return x


def decode(outs, names, w, thresh=THRESH):
    """YuNet outputs for an input w px wide -> [(score, x, y, w, h)] in input px, best first (as OpenCV's
    FaceDetectorYN, no NMS: the best one is all this needs)."""
    o = dict(zip(names, outs))
    found = []
    for st in (8, 16, 32):
        n = w // st
        cls = np.clip(o['cls_%d' % st][0, :, 0], 0, 1)
        obj = np.clip(o['obj_%d' % st][0, :, 0], 0, 1)
        score = np.sqrt(cls * obj)
        for i in np.nonzero(score >= thresh)[0]:
            b = o['bbox_%d' % st][0, i]
            bw, bh = np.exp(b[2]) * st, np.exp(b[3]) * st      # until 11.112 named w: the next stride's n was wrong
            cx, cy = (i % n + b[0]) * st, (i // n + b[1]) * st
            found.append((float(score[i]), cx - bw / 2, cy - bh / 2, bw, bh))
    return sorted(found, reverse=True)


def detect(sess, img, thresh=THRESH):
    """The best face in img as (score, x, y, w, h) in img px, or None."""
    x = to_input(img)
    names = [o.name for o in sess.get_outputs()]
    f = decode(sess.run(names, {sess.get_inputs()[0].name: x}), names, x.shape[3], thresh)
    return f[0] if f else None


def label(w_px):
    return 'W %.1f cm' % (w_px * CM_PER_PX)


def start(lib, model, cpus=frozenset({4, 5, 6})):
    """Detect in libdvsview's C thread dvs-face (dvsai.c, 11.112: the loop of this module's Python thread before);
    None (and why on stderr) without onnxruntime or the model."""
    from dvsact import cpu_mask, ort_lib
    ort = ort_lib()
    if not ort:
        return print('[dvsface] off: no onnxruntime', file=sys.stderr, flush=True)
    if not os.path.isfile(model):
        return print('[dvsface] off: no model %s' % model, file=sys.stderr, flush=True)
    err = ctypes.create_string_buffer(512)
    lib.dvs_face_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_int]
    if lib.dvs_face_start(ort.encode(), model.encode(), cpu_mask(cpus), err, len(err)):
        return print('[dvsface] off: %s' % err.value.decode(errors='replace'), file=sys.stderr, flush=True)
    return True


def free_size(src, dst):
    """YuNet 2023mar's input is fixed at 640 x 640 though nothing else in it is (its reshapes take -1, its
    upsamplings scales): name the input's height and width and the outputs' length instead."""
    import onnx
    m = onnx.load(src)
    d = m.graph.input[0].type.tensor_type.shape.dim
    d[2].dim_param, d[3].dim_param = 'h', 'w'
    for o in m.graph.output:
        o.type.tensor_type.shape.dim[1].dim_param = 'n'
    del m.graph.value_info[:]           # the 640 x 640 shapes inside: onnxruntime warns at every other size
    onnx.checker.check_model(m)
    onnx.save(m, dst)


def _selftest(model, image=None):
    import onnxruntime as ort
    assert label(VIEW_W * FACE_SCREEN_CM / SCREEN_CM) == 'W 19.0 cm'   # 3 cm of the 17 cm view: the 19 cm face
    sess = ort.InferenceSession(model, providers=['CPUExecutionProvider'])
    assert detect(sess, np.zeros((VIEW_H // 2, VIEW_W // 2), np.uint8)) is None     # no events, no face
    if image:
        from PIL import Image
        img = np.asarray(Image.open(image).convert('RGB'))
        f = detect(sess, img)
        print('photo %dx%d: %s' % (img.shape[1], img.shape[0], f))
        assert f and f[0] > 0.8, f
    print('selftest ok')


if __name__ == '__main__':
    if sys.argv[1:2] == ['--selftest']:
        _selftest(*sys.argv[2:4])
    elif sys.argv[1:2] == ['--free-size']:
        free_size(*sys.argv[2:4])
