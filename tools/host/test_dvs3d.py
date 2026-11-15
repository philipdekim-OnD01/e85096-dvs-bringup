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
from dvs3d import Board, BoardClock, Pipeline, calibrate, find_corners, pack_points, thin

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
        xyz, val, t = Pipeline(synth_calib(), zrange=(1.0, 10.0)).run(left, right)
        self.assertGreater(len(xyz), 500)
        self.assertAlmostEqual(float(np.median(xyz[:, 2])), F * B / 20, delta=0.45)
        self.assertEqual(len(val), len(xyz))

    def test_disparity_range_from_the_rig(self):    # 15 cm at f 600: 0.3-3 m is 30-300 px, not 0-64
        p = Pipeline(synth_calib())
        self.assertEqual((p.min_disp, p.num_disp), (30, 272))
        self.assertEqual((Pipeline(None).min_disp, Pipeline(None).num_disp), (0, 64))

    def test_depth_of_a_near_pattern(self):          # 200 px: 0.45 m, the fan's range
        rng = np.random.default_rng(0)
        pat = rng.integers(1, 256, (60, 60), dtype=np.uint8)
        left, right = np.zeros((360, 480), np.uint8), np.zeros((360, 480), np.uint8)
        left[150:210, 300:360] = pat
        right[150:210, 100:160] = pat
        xyz = Pipeline(synth_calib()).run(left, right)[0]
        self.assertGreater(len(xyz), 500)
        self.assertAlmostEqual(float(np.median(xyz[:, 2])), F * B / 200, delta=0.045)

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
        Reply.codes = {}                              # a board without /evframe (not installed yet): 404, counted
        b.probe()
        self.assertEqual(b.errors[404], 1)
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
        self.assertIn(b'class="board"', self.get('/checker').read())

    def test_points_and_stats(self):
        self.assertEqual(len(self.get('/points').read()), 4 + 13 * 2)
        self.assertEqual(json.load(self.get('/stats')), {'pairs': 3, 'calib': False})

    def test_views_before_the_first_pair(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get('/views.png')
        self.assertEqual(e.exception.code, 503)
        self.live.state['views'] = b'\x89PNG'
        self.assertEqual(self.get('/views.png').headers['Content-Type'], 'image/png')


class TestRough(unittest.TestCase):      # no corners (blurry lenses): a parallel rig, rows lined up from the images
    def pair(self, dx, dy):
        rng = np.random.default_rng(0)
        pat = rng.integers(1, 256, (60, 60), dtype=np.uint8)
        left, right = np.zeros((360, 480), np.uint8), np.zeros((360, 480), np.uint8)
        left[150:210, 200:260] = pat
        right[150 + dy:210 + dy, 200 - dx:260 - dx] = pat     # dx: disparity, dy: the right sensor sits higher
        return left, right

    def test_shift(self):
        self.assertEqual(dvs3d.shift([self.pair(20, 12)]), (20, 12))

    def test_shift_blurry_left_sharp_right(self):   # 11-15: board 1 a wide blur, board 2 the squares (11.56)
        sq = np.kron((np.indices((7, 10)).sum(0) % 2).astype(np.uint8) * 200, np.ones((27, 27), np.uint8))
        right = np.zeros((360, 480), np.uint8)
        right[150:150 + 189, 30:300] = sq
        left = np.zeros((360, 480), np.uint8)
        left[83:83 + 189, 129:399] = sq
        left = np.where(cv2.GaussianBlur(left, (0, 0), 25) > 20, 150, 0).astype(np.uint8)
        dx, dy = dvs3d.shift([(left, right)] * 5)
        self.assertLess(abs(dx - 99), 6)
        self.assertLess(abs(dy - 67), 6)

    def test_depth_after_lining_up_the_rows(self):
        left, right = self.pair(20, 12)
        c = dvs3d.rough_calib(F, B, 12)
        self.assertTrue(c['rough'])
        xyz = Pipeline(c, zrange=(1.0, 10.0)).run(left, right)[0]
        self.assertGreater(len(xyz), 500)
        self.assertAlmostEqual(float(np.median(xyz[:, 2])), F * B / 20, delta=0.45)
        unaligned = Pipeline(dvs3d.rough_calib(F, B, 0), zrange=(1.0, 10.0)).run(left, right)[0]
        self.assertLess(len(unaligned), len(xyz) / 2)      # 12 rows apart: 1100 of 3600 points, 40 % of them wrong


PATTERN, SQUARE = (9, 6), 0.03


def board_points():
    g = np.mgrid[0:PATTERN[0], 0:PATTERN[1]].T.reshape(-1, 2)
    return np.hstack([g * SQUARE, np.zeros((len(g), 1))]).astype(np.float32)


class TestCalib(unittest.TestCase):
    def test_find_corners_in_a_blinking_board(self):   # only the white squares fire: they are the pattern
        img = np.zeros((360, 480), np.uint8)
        for r in range(PATTERN[1] + 1):
            for c in range(PATTERN[0] + 1):
                if (r + c) % 2:
                    img[60 + 30 * r:90 + 30 * r, 90 + 30 * c:120 + 30 * c] = 200
        found = find_corners(img, PATTERN)
        self.assertIsNotNone(found)
        self.assertEqual(found.shape, (54, 1, 2))
        self.assertIsNone(find_corners(np.zeros((360, 480), np.uint8), PATTERN))

    def test_calibrate_a_synthetic_rig(self):
        K = np.array([[F, 0, 240], [0, F, 180], [0, 0, 1]])
        obj, rng, views = board_points(), np.random.default_rng(1), []
        for _ in range(15):
            rvec = rng.uniform(-0.4, 0.4, 3)
            tvec = np.array([rng.uniform(-0.15, 0.05), rng.uniform(-0.1, 0.05), rng.uniform(0.6, 1.0)])
            l = cv2.projectPoints(obj, rvec, tvec, K, None)[0]
            r = cv2.projectPoints(obj, rvec, tvec - np.array([B, 0, 0]), K, None)[0]
            views.append((l.astype(np.float32), r.astype(np.float32)))
        c = calibrate(views, SQUARE, PATTERN)
        self.assertLess(c['rms'], 0.5)
        self.assertAlmostEqual(float(np.linalg.norm(c['T'])), B, delta=0.01)
        self.assertAlmostEqual(float(c['f_ratio']), 1.0, delta=0.05)
        self.assertEqual({'K1', 'D1', 'K2', 'D2', 'R1', 'R2', 'P1', 'P2', 'Q'} - set(c), set())


if __name__ == '__main__':
    unittest.main()
