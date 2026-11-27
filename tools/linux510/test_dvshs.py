"""Tests for dvshs.py (the high-speed fusion's recording, clocks and maps) and the dvshs C CLI (11.121).
  python3 -m unittest tools/linux510/test_dvshs.py      (numpy; cv2 only for the checker test; cc for the CLI tests)"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dvshs  # noqa: E402

EV = dvshs.EV_DTYPE


def events(t, x, y, off=None):
    e = np.zeros(len(t), EV)
    e['t'], e['x'], e['y'] = t, x, y
    if off is not None:
        e['y'] |= np.where(off, 0x8000, 0).astype(np.uint16)
    return e


class Pure(unittest.TestCase):
    def test_parse_v4l2_log(self):
        log = ('cap dqbuf: 1 seq:      2 bytesused: 614400 ts: 175987.586238 (ts-monotonic, ts-src-soe)\n'
               'something else\n'
               'cap dqbuf: 2 seq:      3 bytesused: 614400 ts: 175987.654258 delta: 68.020 ms (ts-monotonic, ts-src-soe)\n')
        self.assertEqual(dvshs.parse_v4l2_log(log), [175987586238, 175987654258])

    def test_surface_latest_wins(self):
        e = events([1000, 6000], [10, 11], [20, 21])      # both land in surface pixel (5, 10)
        img = dvshs.surface(e, 10500, 10000)                # window (500, 10500]: both, the later one shows
        self.assertEqual(img.shape, (360, 480))
        self.assertEqual(img[10, 5], 1 + 254 * (10000 - 4500) // 10000)
        self.assertEqual(int((img > 0).sum()), 1)
        self.assertEqual(dvshs.surface(e, 6000, 10000)[10, 5], 255)   # age 0

    def _scene(self, seed):
        rng = np.random.default_rng(seed)
        n = 200000
        t = np.sort(rng.integers(0, 1000000, n))
        return events(t, rng.integers(0, 960, n), rng.integers(0, 720, n))

    def test_find_origin_exact(self):
        e = self._scene(1)
        o_true = 806940210 + 3217
        snaps = [(o_true + te, 10000, dvshs.surface(e, te, 10000)) for te in (300000, 700000)]
        o, score = dvshs.find_origin(e, snaps, [o_true - 1600, o_true + 14000])
        self.assertLessEqual(abs(o - o_true), 100)
        self.assertGreater(score, 0.9)

    def test_find_origin_periodic_far_candidates(self):
        """a fan repeats every blade (10 ms here); the polls can miss the start by a buffer or two (11.121: 15 ms a
        poll, buffers every ~15 ms): the true origin is 25 ms from the nearest candidate"""
        rng = np.random.default_rng(3)
        k = 3000                                        # events of one 10 ms period, in scan steps of 500 us
        pt, px, py = np.sort(rng.integers(0, 20, k)) * 500, rng.integers(0, 960, k), rng.integers(0, 720, k)
        t = np.concatenate([pt + 10000 * i for i in range(100)])
        x, y = np.tile(px, 100), np.tile(py, 100)
        n = 30000                                       # and a little that does not repeat
        t = np.concatenate([t, rng.integers(0, 1000000, n) // 500 * 500])
        x, y = np.concatenate([x, rng.integers(0, 960, n)]), np.concatenate([y, rng.integers(0, 720, n)])
        o_ = np.argsort(t, kind='stable')
        e = events(t[o_], x[o_], y[o_])
        o_true = 1668093244
        snaps = [(o_true + te, 10000, dvshs.surface(e, te, 10000)) for te in (250250, 550250, 850250)]
        o, score = dvshs.find_origin(e, snaps, [o_true - 25000, o_true + 21000])
        self.assertLessEqual(abs(o - o_true), 100)
        self.assertGreater(score, 0.9)

    def test_find_origin_bad_snap(self):
        e, other = self._scene(1), self._scene(2)
        snaps = [(5000000 + 300000, 10000, dvshs.surface(other, 300000, 10000))]
        o, score = dvshs.find_origin(e, snaps, [5000000])
        self.assertLess(score, 0.5)

    def test_unwrap(self):
        self.assertEqual(dvshs.unwrap(5, (1 << 32) + 3), (1 << 32) + 5)
        self.assertEqual(dvshs.unwrap((1 << 32) - 2, (1 << 32) + 3), (1 << 32) - 2)

    def test_cam_times(self):
        t = dvshs.cam_times([1000000, 1068000], d2m=500, origin=200000, delta=-30)
        self.assertEqual(list(t), [1000000 - 500 - 200000 - 30, 1068000 - 500 - 200000 - 30])

    def test_homography_map_scale(self):
        H = np.diag([1.6, 1.6, 1.0])                           # camera px -> DVS px, 1.6x
        m = dvshs.homography_map(H)
        self.assertEqual(m.dtype, np.int32)
        self.assertEqual(m.shape, (480 * 640,))
        self.assertEqual(m[10 * 640 + 10], 16 * 960 + 16)
        self.assertEqual(m[479 * 640 + 639], -1)               # (1022.4, 766.4): outside 960 x 720

    def test_homography_map_roi(self):
        m = dvshs.homography_map(np.eye(3), roi=(320, 240, 100, 50))  # ellipse in camera px
        self.assertEqual(m[240 * 640 + 320], 240 * 960 + 320)
        self.assertEqual(m[240 * 640 + 419], 240 * 960 + 419)
        self.assertEqual(m[240 * 640 + 421], -1)
        self.assertEqual(m[291 * 640 + 320], -1)

    def test_dvs_panel(self):
        e = events([400, 900, 1500], [300, 600, 30], [150, 450, 30], off=np.array([False, True, False]))
        img = dvshs.dvs_panel(e, 1000)                          # (0, 1000]: the first two
        self.assertEqual(img.shape, (480, 640, 3))
        self.assertEqual(tuple(img[100, 200]), (0, 255, 0))     # ON green, 960 x 720 -> 640 x 480
        self.assertEqual(tuple(img[300, 400]), (255, 0, 0))     # OFF red (RGB)
        self.assertEqual(int(img.sum()), 2 * 255)

    def test_onoff_ratio(self):
        e = events(np.arange(500), np.zeros(500, int), np.zeros(500, int), off=np.arange(500) >= 300)
        self.assertAlmostEqual(dvshs.onoff_ratio(e, min_count=1), 1.5)

    def test_onoff_ratio_map(self):
        on = [(5, 5)] * 300 + [(7, 7)] * 30 + [(9, 9)] * 4
        off = [(5, 5)] * 200 + [(9, 9)] * 6
        xy = np.array(on + off)
        e = events(np.arange(len(xy)), xy[:, 0], xy[:, 1], off=np.arange(len(xy)) >= len(on))
        r = dvshs.onoff_ratio_map(e, min_count=20)
        self.assertEqual((r.shape, r.dtype), ((720, 960), np.float32))
        self.assertAlmostEqual(float(r[5, 5]), 1.5, places=5)   # its own n_on / n_off
        self.assertAlmostEqual(float(r[7, 7]), 4.0, places=5)   # no OFF: capped
        self.assertAlmostEqual(float(r[9, 9]), dvshs.onoff_ratio(e, min_count=20), places=5)  # too few: global
        self.assertAlmostEqual(float(r[0, 0]), dvshs.onoff_ratio(e, min_count=20), places=5)

    def test_span_inside_events_and_keys(self):
        f, t = dvshs.span(cam_t=[-50000, 18000, 86000], T=60000, ev_first=0, ev_last=120000, step=1000)
        self.assertEqual((f, t), (0, 116000))                  # keys' middles -20000..116000, events 0..120000

    def test_origin_candidates(self):
        polls = [{'state': 'countdown', 'newest': 10}, {'state': 'countdown', 'newest': 20},
                 {'state': 'countdown', 'newest': 30}, {'state': 'recording', 'newest': 40},
                 {'state': 'recording', 'newest': 50}]
        self.assertEqual(dvshs.origin_candidates(polls), [20, 30, 40, 50])

    def test_calib_points_roundtrip(self):
        H = np.array([[1.2, 0.05, 130.0], [-0.03, 1.25, 80.0], [1e-5, 2e-5, 1.0]])
        cam = np.array([[50, 40], [600, 30], [620, 450], [30, 470], [320, 240], [200, 300]], float)
        p = np.c_[cam, np.ones(6)] @ H.T
        dvs = p[:, :2] / p[:, 2:]
        H2 = dvshs.fit_homography(cam, dvs)
        q = np.c_[cam, np.ones(6)] @ H2.T
        self.assertLess(np.abs(q[:, :2] / q[:, 2:] - dvs).max(), 0.5)


@unittest.skipUnless(shutil.which('cc'), 'no C compiler')
class Cli(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.bin = os.path.join(cls.tmp, 'dvshs')
        subprocess.run(['cc', '-O2', '-Wall', '-o', cls.bin, os.path.join(HERE, 'dvshs.c'), '-lm'], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def files(self, ys, times, mapping):
        d = tempfile.mkdtemp(dir=self.tmp)
        cam = np.zeros((len(ys), 480, 640, 2), np.uint8)
        cam[..., 1] = 128
        for k, y in enumerate(ys):
            cam[k, ..., 0] = y
        cam.tofile(os.path.join(d, 'cam.yuyv'))
        np.zeros(0, EV).tofile(os.path.join(d, 'dvs.bin'))
        with open(os.path.join(d, 'cam.t'), 'w') as f:
            f.write(''.join('%d\n' % t for t in times))
        mapping.astype(np.int32).tofile(os.path.join(d, 'map.bin'))
        return [os.path.join(d, n) for n in ('dvs.bin', 'cam.yuyv', 'cam.t', 'map.bin')]

    def test_no_map_is_linear_blend(self):
        a = np.tile(np.linspace(16, 235, 640).astype(np.uint8), (480, 1))
        args = self.files([a, np.full((480, 640), 100, np.uint8)], [0, 66000], np.full(480 * 640, -1))
        out = subprocess.run([self.bin, 'fuse', *args, '--T', '50000'], capture_output=True, check=True).stdout
        fr = np.frombuffer(out, np.uint8).reshape(-1, 640 * 480 * 3 // 2)
        self.assertEqual(len(fr), 66)                           # middles 25000 .. 91000, 1 ms apart
        y = fr[:, :640 * 480].reshape(-1, 480, 640).astype(int)
        self.assertTrue(np.array_equal(y[0], a))
        mid = np.rint(a + 33 / 66 * (100 - a.astype(float)))
        self.assertLessEqual(np.abs(y[33] - mid).max(), 1)
        self.assertTrue(np.all(fr[:, 640 * 480:] == 128))

    def test_sbs_width(self):
        args = self.files([np.full((480, 640), 50, np.uint8)] * 2, [0, 66000], np.arange(480 * 640) % (960 * 720))
        out = subprocess.run([self.bin, 'fuse', *args, '--T', '50000', '--layout', 'sbs', '--to', '30000'],
                             capture_output=True, check=True).stdout
        self.assertEqual(len(out), 5 * 1280 * 480 * 3 // 2)    # 25000 .. 29000

    def test_eval_json(self):
        y = np.full((480, 640), 120, np.uint8)
        args = self.files([y, y, y], [0, 66000, 132000], np.arange(480 * 640))
        out = subprocess.run([self.bin, 'fuse', *args, '--T', '50000', '--eval'], capture_output=True, check=True)
        r = json.loads(out.stdout)
        self.assertEqual(r['frames'], 1)
        self.assertEqual(r['px'], 480 * 640)
        self.assertGreater(r['psnr_fused'], 40)

    def test_ratio_map_same_as_scalar(self):
        rng = np.random.default_rng(4)
        y = rng.integers(40, 200, (480, 640)).astype(np.uint8)
        args = self.files([y, y[::-1]], [0, 66000], np.arange(480 * 640))
        n = 20000
        e = events(np.sort(rng.integers(0, 100000, n)), rng.integers(0, 640, n), rng.integers(0, 480, n),
                   off=rng.random(n) < 0.6)
        e.tofile(args[0])
        rm = os.path.join(self.tmp, 'ratio.bin')
        run = lambda *a: subprocess.run([self.bin, 'fuse', *args, '--T', '50000', '--to', '40000', *a],  # noqa: E731
                                        capture_output=True, check=True).stdout
        np.full((720, 960), 2.0, np.float32).tofile(rm)
        self.assertEqual(run('--ratio', '2'), run('--ratio-map', rm))
        self.assertNotEqual(run('--ratio', '1'), run('--ratio-map', rm))

    def test_blur_spreads_one_event(self):
        y = np.full((480, 640), 120, np.uint8)
        v, u = np.mgrid[0:480, 0:640]
        args = self.files([y, y], [0, 66000], (v * 960 + u).ravel())    # camera (u, v) reads DVS (u, v)
        events([60000], [100], [100]).tofile(args[0])                     # ON, after key a's exposure
        def frame(*a):
            out = subprocess.run([self.bin, 'fuse', *args, '--T', '50000', '--from', '70000', '--to', '71000',
                                  '--c', '2', *a],                       # a ninth of c=0.2 rounds away
                                 capture_output=True, check=True).stdout
            return np.frombuffer(out, np.uint8)[:640 * 480].reshape(480, 640).astype(int)
        f0, f1 = frame(), frame('--blur', '1')
        self.assertGreater(f0[100, 100], f0[100, 101])
        self.assertEqual(f0[100, 101], f0[100, 103])
        self.assertGreater(f1[100, 101], f1[100, 103])                   # the neighbour gets a ninth of it
        self.assertLess(f1[100, 100], f0[100, 100])

    def test_from_skips_keys_same_frames(self):
        rng = np.random.default_rng(5)
        ys = [rng.integers(30, 220, (480, 640)).astype(np.uint8) for _ in range(4)]
        args = self.files(ys, [0, 66000, 132000, 198000], np.arange(480 * 640))
        n = 50000
        events(np.sort(rng.integers(0, 250000, n)), rng.integers(0, 960, n), rng.integers(0, 720, n),
               off=rng.random(n) < 0.5).tofile(args[0])
        fr = 640 * 480 * 3 // 2
        full = subprocess.run([self.bin, 'fuse', *args, '--T', '50000', '--blur', '1'], capture_output=True,
                              check=True).stdout
        tail = subprocess.run([self.bin, 'fuse', *args, '--T', '50000', '--blur', '1', '--from', '170000'],
                              capture_output=True, check=True).stdout
        self.assertEqual(len(full), 198 * fr)                   # 25000 .. 222000
        self.assertEqual(tail, full[(170 - 25) * fr:])

    def test_selftest(self):
        self.assertEqual(subprocess.run([self.bin, '--selftest'], capture_output=True).returncode, 0)


@unittest.skipUnless(dvshs.cv2 is not None, 'no OpenCV')
class Checker(unittest.TestCase):
    def test_calib_checker_synthetic(self):
        cv2 = dvshs.cv2
        sq = 40
        board = np.uint8(255 * ((np.indices((7 * sq, 10 * sq)) // sq).sum(0) % 2))    # 10 x 7 squares: 9 x 6 inner
        board = cv2.copyMakeBorder(board, sq, sq, sq, sq, cv2.BORDER_CONSTANT, value=255)
        Hc = np.array([[1.3, 0.1, 40.0], [0.05, 1.2, 30.0], [0, 0, 1.0]])               # board -> camera
        cam = cv2.warpPerspective(board, Hc, (640, 480), borderValue=255)
        Hd = np.array([[1.6, 0.0, 120.0], [0.0, 1.6, 60.0], [0, 0, 1.0]])               # board -> DVS
        dvs = cv2.warpPerspective(board, Hd, (960, 720), borderValue=255)
        fired = np.where(dvs > 128, 40.0, 0.0).astype(np.float32)                      # only white squares blink
        H = dvshs.checker_homography(cam, fired)
        truth = Hd @ np.linalg.inv(Hc)
        pts = np.array([[200, 150, 1.0], [400, 300, 1.0], [300, 250, 1.0]])
        a, b = pts @ H.T, pts @ truth.T
        self.assertLess(np.abs(a[:, :2] / a[:, 2:] - b[:, :2] / b[:, 2:]).max(), 2.0)


class Demo(unittest.TestCase):
    def test_demo_failure_tells_user(self):
        """started from the desktop icon nobody sees stderr: a failure after the checks shows a message, after the
        'recording' note is gone, and still fails"""
        said, notes, tmp = [], [], tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)

        class Note:
            def terminate(self):
                notes.append('closed')

        def say(title, text, wait=True):
            said.append((text, list(notes)))
            return None if wait else Note()

        def rec(d, s=1):
            raise RuntimeError('no events recorded')
        saved = {k: getattr(dvshs, k) for k in ('clocks', 'find_camera', 'say', 'rec', 'CALIB', 'OUT_DIR')}
        self.addCleanup(lambda: [setattr(dvshs, k, v) for k, v in saved.items()])
        open(os.path.join(tmp, 'calib.json'), 'w').write('{}')
        dvshs.clocks, dvshs.find_camera, dvshs.say, dvshs.rec = lambda: None, lambda: '/dev/video9', say, rec
        dvshs.CALIB, dvshs.OUT_DIR = os.path.join(tmp, 'calib.json'), os.path.join(tmp, 'out')
        with self.assertRaises(RuntimeError):
            dvshs.demo()
        self.assertIn('no events recorded', said[-1][0])
        self.assertEqual(said[-1][1], ['closed'])


if __name__ == '__main__':
    unittest.main()
