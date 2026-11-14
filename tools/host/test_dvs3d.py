"""dvs3d (11.56): python3 -m unittest discover -s tools/host -p test_dvs3d.py (Mac: numpy + OpenCV)."""
import http.server
import socket
import struct
import json
import threading
import types
import unittest
import urllib.error
import urllib.request

import cv2
import numpy as np

import dvs3d
from dvs3d import Board, BoardClock, Pipeline, pack_points, thin

F, B = 600.0, 0.15                         # synthetic rig: two equal cameras 15 cm apart along x


def synth_calib():
    K = np.array([[F, 0, 240], [0, F, 180], [0, 0, 1]])
    D = np.zeros(5)
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K, D, K, D, (480, 360), np.eye(3), np.array([-B, 0, 0]))
    return {'K1': K, 'D1': D, 'K2': K, 'D2': D, 'R1': R1, 'R2': R2, 'P1': P1, 'P2': P2, 'Q': Q}


class TestClock(unittest.TestCase):
    def test_offset_from_the_shortest_round_trip(self):
        c = BoardClock()
        c.add(0, 10000, 1_000_000, 500)
        c.add(100000, 104000, 1_100_000, 500)
        self.assertEqual((c.offset(), c.rtt(), c.uncertain()), (1_100_000 - 102000, 4000, False))
        self.assertEqual(c.to_disp(1000), (1000 + 998000 - 500) & 0xffffffff)

    def test_old_samples_dropped(self):
        c = BoardClock()
        c.add(0, 1000, 5000, 0)
        c.add(40_000_000, 40_005_000, 7000, 0)
        self.assertEqual(c.rtt(), 5000)

    def test_uncertain(self):
        c = BoardClock()
        self.assertTrue(c.uncertain())
        c.add(0, 25000, 0, 0)
        self.assertTrue(c.uncertain())

    def test_to_disp_wraps(self):   # a new display clock (dvslive restarted) and a negative offset fold into 32 bits
        c = BoardClock()
        c.add(10000, 10000, 5000, 0)
        self.assertEqual(c.to_disp(1000), (1000 - 5000) & 0xffffffff)
        c.add(20000, 20000, 15000, 3_000_000)        # the latest disp2mono counts
        self.assertEqual(c.to_disp(1000), (1000 - 5000 - 3_000_000) & 0xffffffff)


class TestPipeline(unittest.TestCase):
    def test_empty_window(self):     # fan off: nothing fired, no points, all stages timed
        z = np.zeros((360, 480), np.uint8)
        xyz, val, t = Pipeline(None).run(z, z)
        self.assertEqual(len(xyz), 0)
        self.assertEqual(set(t), {'rectify', 'sgbm', 'mask', 'reproject'})
        xyz, val, t = Pipeline(synth_calib()).run(z, z)
        self.assertEqual(len(xyz), 0)

    def test_depth_of_a_shifted_pattern(self):
        rng = np.random.default_rng(0)
        pat = rng.integers(1, 256, (60, 60), dtype=np.uint8)
        left, right = np.zeros((360, 480), np.uint8), np.zeros((360, 480), np.uint8)
        left[150:210, 200:260] = pat
        right[150:210, 180:240] = pat                 # 20 px to the left: z = F * B / 20 = 4.5 m
        xyz, val, t = Pipeline(synth_calib()).run(left, right)
        self.assertGreater(len(xyz), 500)
        self.assertAlmostEqual(float(np.median(xyz[:, 2])), F * B / 20, delta=0.45)
        self.assertEqual(len(val), len(xyz))

    def test_no_calibration_no_points(self):         # without calib.npz: views and times only
        left = np.full((360, 480), 9, np.uint8)
        self.assertEqual(len(Pipeline(None).run(left, left)[0]), 0)


class TestPacking(unittest.TestCase):
    def test_pack_points(self):
        xyz, val = np.ones((3, 3), np.float32), np.array([1, 2, 3], np.uint8)
        b = pack_points(xyz, val)
        self.assertEqual((len(b), struct.unpack_from('<I', b)[0]), (4 + 13 * 3, 3))

    def test_thin(self):
        a, b = np.arange(10), np.arange(3)
        self.assertEqual(len(thin(5, a)[0]), 5)
        self.assertEqual(len(thin(5, b)[0]), 3)
        x, y = thin(4, a, a * 2)
        self.assertTrue((y == x * 2).all())             # the same rows of every array


class Reply(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    codes = {}

    def do_GET(self):
        code = Reply.codes.get(self.path.split('?')[0], 404)
        self.send_response(code)
        self.send_header('Content-Length', '0')
        self.send_header('X-Dvs-Mono', '1000')
        self.send_header('X-Dvs-Disp2Mono', '0')
        if code >= 400:
            self.send_header('Connection', 'close')
        self.end_headers()

    def log_message(self, *a):
        pass


class TestBoard(unittest.TestCase):
    def test_refused_frames_are_counted(self):
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Reply)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        b = Board('127.0.0.1', srv.server_address[1])
        Reply.codes = {'/evframe': 425}
        self.assertIsNone(b.frame(5, 20000))
        Reply.codes = {'/evframe': 410}
        self.assertIsNone(b.frame(5, 20000))
        self.assertEqual((b.errors[425], b.errors[410]), (1, 1))
        self.assertIsNotNone(b.clock.offset())        # the refused replies still carry the clock
        srv.shutdown()

    def test_no_board(self):
        s = socket.socket()
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
        s.close()                                    # nothing listens there
        b = Board('127.0.0.1', port, timeout=0.5)
        self.assertIsNone(b.frame(5, 20000))
        self.assertIsNone(b.png())
        self.assertEqual(b.errors['net'], 2)

    def test_png_stand_in(self):     # the HDMI frame: max of RGB, 2 x 2 max-pooled to 480 x 360
        rgb = np.zeros((720, 960, 3), np.uint8)
        rgb[11, 21] = (0, 200, 0)
        img = dvs3d.from_png(cv2.imencode('.png', rgb[:, :, ::-1])[1].tobytes())
        self.assertEqual((img.shape, int(img[5, 10]), int(img.sum())), ((360, 480), 200, 200))


class TestServe(unittest.TestCase):
    def setUp(self):
        xyz = np.ones((2, 3), np.float32)
        self.live = types.SimpleNamespace(lock=threading.Lock(), state={
            'points': pack_points(xyz, [5, 6]), 'views': b'', 'stats': {'pairs': 3, 'calib': False}})
        self.srv = dvs3d.serve(self.live, 0)
        self.url = 'http://127.0.0.1:%d' % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def get(self, path):
        return urllib.request.urlopen(self.url + path, timeout=5)

    def test_page(self):
        r = self.get('/')
        self.assertEqual(r.status, 200)
        self.assertIn(b'<canvas', r.read())

    def test_points_and_stats(self):
        self.assertEqual(len(self.get('/points').read()), 4 + 13 * 2)
        self.assertEqual(json.load(self.get('/stats')), {'pairs': 3, 'calib': False})

    def test_views_before_the_first_pair(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get('/views.png')
        self.assertEqual(e.exception.code, 503)
        self.live.state['views'] = b'\x89PNG'
        self.assertEqual(self.get('/views.png').headers['Content-Type'], 'image/png')


if __name__ == '__main__':
    unittest.main()
