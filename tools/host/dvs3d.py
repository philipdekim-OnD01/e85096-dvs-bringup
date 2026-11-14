#!/usr/bin/env python3
"""Stereo 3D from the DVS of two E850-96 boards, stage 1 on the Mac (11.56, spec
docs/superpowers/specs/2026-11-14-dvs-stereo-3d-design.md).

Each board's /evframe gives the time surface (480 x 360) of a display-clock window. This takes, every 100 ms, the
window ending at one Mac time T (now - 80 ms) from both boards (left = board 1), rectifies them with calib.npz
(dvs3d.py calib, a blinking board from dvs3d-checker.html), runs OpenCV StereoSGBM, keeps the pixels that fired on the left and turns them into 3D points
for the page (dvs3d.html, http://localhost:8090/). Every stage is timed: the bench replays recorded pairs through
the same code to measure the compute on the Mac and, later, on the board (the lightening plan of stage 2).

  dvs3d.py live --left 192.168.0.236 --right 192.168.0.168 [--rec DIR] [--calib F] [--png] [--port 8090]
  dvs3d.py bench DIR [--threads N] [--calib F]
  dvs3d.py calib [--left H --right H] [--square M] [--n 20] [--png] [--from DIR]   (writes calib.npz)

--png: a board without /evframe (no root to install it) serves its HDMI frame instead (/frame.png: the fade of
the renderer for a time surface, no timestamp, paired by request time). Enough for timing and a look, not for
3D of moving things.
"""
import argparse
import collections
import glob
import http.client
import http.server
import json
import os
import struct
import sys
import threading
import time
import zlib

import cv2
import numpy as np

W, H = 480, 360
T_DELAY_US = 80_000                 # T = now - this: both boards have the window by then (~40 ms buffers + Wi-Fi)
CLOCK_KEEP_US = 30_000_000          # clock samples kept; the shortest round trip of these sets the offset
RTT_MAX_US = 20_000                 # a shortest round trip above this: the pairing time is uncertain
MAX_POINTS = 20_000
CALIB = os.path.expanduser('~/e850-logs/dvs3d/calib.npz')


def now_us():
    return time.monotonic_ns() // 1000


class BoardClock:
    """A board's CLOCK_MONOTONIC against the Mac's, NTP style from the replies' X-Dvs-Mono, and its display clock"""

    def __init__(self):
        self.samples = collections.deque()          # (t1, round trip, offset, disp2mono)

    def add(self, t0_us, t1_us, mono_us, disp2mono_us):
        self.samples.append((t1_us, t1_us - t0_us, mono_us - (t0_us + t1_us) // 2, disp2mono_us))
        while t1_us - self.samples[0][0] > CLOCK_KEEP_US:
            self.samples.popleft()

    def _best(self):
        return min(self.samples, key=lambda s: s[1]) if self.samples else None

    def offset(self):
        b = self._best()
        return None if b is None else b[2]

    def rtt(self):
        b = self._best()
        return None if b is None else b[1]

    def to_disp(self, mac_us):
        """the board's display clock (uint32 us) at Mac time mac_us; the newest disp2mono (it moves on a gap)"""
        return (mac_us + self.offset() - self.samples[-1][3]) & 0xffffffff

    def uncertain(self):
        r = self.rtt()
        return r is None or r > RTT_MAX_US


def from_png(png):
    """the stand-in source: the HDMI frame (960 x 720 RGB) as one channel, 2 x 2 max-pooled to W x H"""
    rgb = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    return rgb.max(2).reshape(H, 2, W, 2).max(3).max(1)


class Board:
    """One keep-alive connection to a board's page; frame() and png() return None on any failure (counted)"""

    def __init__(self, host, port=8080, timeout=1.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.clock, self.errors, self.conn, self.bytes = BoardClock(), collections.Counter(), None, 0

    def _get(self, path):
        t0 = now_us()
        try:
            if self.conn is None:
                self.conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
            self.conn.request('GET', path)
            r = self.conn.getresponse()
            body = r.read()
        except (OSError, http.client.HTTPException):
            if self.conn:
                self.conn.close()
            self.conn = None
            self.errors['net'] += 1
            return None
        t1 = now_us()
        if r.getheader('Connection', '').lower() == 'close':
            self.conn.close()
            self.conn = None
        if r.getheader('X-Dvs-Mono'):
            self.clock.add(t0, t1, int(r.getheader('X-Dvs-Mono')), int(r.getheader('X-Dvs-Disp2Mono')))
        self.bytes += len(body)
        return r, body

    def probe(self):
        got = self._get('/evframe')
        if got and got[0].status != 200:            # e.g. 404: this board's dvsctl has no /evframe yet
            self.errors[got[0].status] += 1

    def frame(self, t_end, win):
        got = self._get('/evframe?t_end=%d&win=%d' % (t_end, win))
        if got is None:
            return None
        r, body = got
        if r.status != 200:
            self.errors[r.status] += 1
            return None
        img = np.frombuffer(zlib.decompress(body), np.uint8).reshape(H, W)
        return img, {'t_end': t_end, 'events': int(r.getheader('X-Dvs-Events')),
                     'cost_us': int(r.getheader('X-Dvs-Cost-Us'))}

    def png(self):
        got = self._get('/frame.png')
        if got is None:
            return None
        if got[0].status != 200:
            self.errors[got[0].status] += 1
            return None
        return from_png(got[1])


def thin(n_max, *arrays):
    """the same random n_max rows of each array (all of them when fewer); a fixed seed keeps runs comparable"""
    n = len(arrays[0])
    if n <= n_max:
        return list(arrays)
    idx = np.sort(np.random.default_rng(0).choice(n, n_max, replace=False))
    return [a[idx] for a in arrays]


def pack_points(xyz, val):
    """/points: uint32 N, then N x 3 float32 (m), then N uint8 (the left surface value)"""
    return struct.pack('<I', len(xyz)) + np.asarray(xyz, '<f4').tobytes() + np.asarray(val, np.uint8).tobytes()


def load_calib(path):
    try:
        z = np.load(path)
    except OSError:
        return None
    return {k: z[k] for k in z.files}


class Pipeline:
    """left, right surfaces (H x W uint8) -> 3D points of the left pixels that fired; stage times in ms.
    The disparity search covers zrange (m): d = f * baseline / z from the rig's Q (15 cm at f 600: 0.3-3 m is
    30-300 px; a fixed 0-64 would see nothing nearer than 1.4 m). Uncalibrated: 0-64."""

    def __init__(self, calib=None, block=5, zrange=(0.3, 3.0)):
        self.calib = calib
        if calib:
            fb = calib['Q'][2, 3] / abs(calib['Q'][3, 2])          # f (px) * baseline (m); Q[3,2] = -1 / Tx
            self.min_disp = int(fb / zrange[1])
            self.num_disp = -(-(int(np.ceil(fb / zrange[0])) - self.min_disp) // 16) * 16
        else:
            self.min_disp, self.num_disp = 0, 64
        if calib:
            self.maps = [cv2.initUndistortRectifyMap(calib['K%d' % i], calib['D%d' % i], calib['R%d' % i],
                                                     calib['P%d' % i], (W, H), cv2.CV_16SC2) for i in (1, 2)]
        self.sgbm = cv2.StereoSGBM_create(minDisparity=self.min_disp, numDisparities=self.num_disp, blockSize=block,
                                          P1=8 * block * block, P2=32 * block * block, disp12MaxDiff=1,
                                          uniquenessRatio=10, speckleWindowSize=50, speckleRange=2)
        self.last = None

    def run(self, left, right):
        t, c = {}, time.perf_counter
        a = c()
        if self.calib:          # nearest: a surface value is a time, not a brightness to blend
            left = cv2.remap(left, *self.maps[0], cv2.INTER_NEAREST)
            right = cv2.remap(right, *self.maps[1], cv2.INTER_NEAREST)
        b = c()
        disp = self.sgbm.compute(left, right)           # int16, 1/16 px
        a, t['rectify'] = c(), (b - a) * 1e3
        t['sgbm'] = (a - b) * 1e3
        m = (left > 0) & (disp >= max(16 * self.min_disp, 1))     # invalid: (min_disp - 1) * 16
        b = c()
        t['mask'] = (b - a) * 1e3
        if self.calib:
            xyz = cv2.reprojectImageTo3D(disp.astype(np.float32) / 16, self.calib['Q'])[m]
            xyz, val = thin(MAX_POINTS, xyz.astype(np.float32), left[m])
        else:
            xyz, val = np.zeros((0, 3), np.float32), np.zeros(0, np.uint8)
        t['reproject'] = (c() - b) * 1e3
        self.last = (left, right, disp)
        return xyz, val, t


def grab(boards, win, png=False):
    """both surfaces of one Mac time T (or both HDMI frames with png), each board in its own thread; None if either
    failed"""
    out = [None, None]
    if png:
        def get(i):
            out[i] = boards[i].png()
    else:
        for b in boards:
            if b.clock.offset() is None:
                b.probe()
        if any(b.clock.offset() is None for b in boards):
            return None
        T = now_us() - T_DELAY_US

        def get(i):
            f = boards[i].frame(boards[i].clock.to_disp(T), win)
            out[i] = f and f[0]
    th = [threading.Thread(target=get, args=(i,)) for i in (0, 1)]
    for x in th:
        x.start()
    for x in th:
        x.join()
    return None if out[0] is None or out[1] is None else out


PATTERN = (9, 6)                    # inner corners of dvs3d-checker.html's 10 x 7 squares


def find_corners(img, pattern=PATTERN):
    """the corners of the blinking board in one surface: only its white squares fire, so what fired is white;
    a median drops the scattered noise events. (n, 1, 2) float32 or None"""
    b = cv2.medianBlur(np.where(img > 0, 255, 0).astype(np.uint8), 5)
    ok, c = cv2.findChessboardCornersSB(b, pattern)
    return c.astype(np.float32) if ok else None


def calibrate(views, square_m, pattern=PATTERN, size=(W, H)):
    """[(left corners, right corners), ...] of the same board poses -> both lenses, the rig, the rectification
    (the keys Pipeline reads) + rms (stereo, px) and f_ratio (the longer focal length / the shorter)"""
    g = np.mgrid[0:pattern[0], 0:pattern[1]].T.reshape(-1, 2) * square_m
    obj = [np.hstack([g, np.zeros((len(g), 1))]).astype(np.float32)] * len(views)
    left, right = [v[0] for v in views], [v[1] for v in views]
    _, K1, D1, _, _ = cv2.calibrateCamera(obj, left, size, None, None)
    _, K2, D2, _, _ = cv2.calibrateCamera(obj, right, size, None, None)
    rms, K1, D1, K2, D2, R, T, _, _ = cv2.stereoCalibrate(obj, left, right, K1, D1, K2, D2, size,
                                                          flags=cv2.CALIB_FIX_INTRINSIC)
    R1, R2, P1, P2, Q, _, _ = cv2.stereoRectify(K1, D1, K2, D2, size, R, T)
    return {'K1': K1, 'D1': D1, 'K2': K2, 'D2': D2, 'R': R, 'T': T, 'R1': R1, 'R2': R2, 'P1': P1, 'P2': P2,
            'Q': Q, 'rms': rms, 'f_ratio': max(K1[0, 0] / K2[0, 0], K2[0, 0] / K1[0, 0])}


def calib_cmd(a):
    """capture board poses from both boards (or reuse a capture with --from), then solve and save calib.npz"""
    d = a.from_dir or os.path.expanduser(time.strftime('~/e850-logs/dvs3d/calib-%Y%m%d-%H%M%S'))
    if not a.from_dir:
        os.makedirs(d)
        boards = (Board(a.left), Board(a.right))
        print('dvs3d-checker.html (/checker)를 전체 화면으로 띄우고, 두 센서가 함께 보는 곳에서 화면을 움직이세요.')
        n = 0
        while n < a.n:
            pair = grab(boards, a.win, a.png)
            cs = pair and [find_corners(x) for x in pair]
            if cs and cs[0] is not None and cs[1] is not None:
                np.savez(os.path.join(d, '%02d.npz' % n), left=pair[0], right=pair[1], cl=cs[0], cr=cs[1])
                n += 1
                print('%d/%d 저장. 화면을 다른 위치·기울기로 옮기세요.' % (n, a.n), flush=True)
                time.sleep(1.5)
            else:
                time.sleep(0.3)
    views = [(z['cl'], z['cr']) for z in map(np.load, sorted(glob.glob(os.path.join(d, '*.npz'))))]
    c = calibrate(views, a.square)
    np.savez(a.out, **c)
    ok = c['rms'] < 1 and c['f_ratio'] <= 2
    print('%d poses, rms %.3f px, baseline %.1f cm, f %.0f / %.0f px (ratio %.2f): %s -> %s' % (
        len(views), c['rms'], 100 * np.linalg.norm(c['T']), c['K1'][0, 0], c['K2'][0, 0], c['f_ratio'],
        'pass' if ok else 'FAIL (rms < 1 px, ratio <= 2)', a.out))


class Live:
    """the 10 Hz loop: one Mac time T for both boards, the pipeline, the page's state, the recording"""

    def __init__(self, left, right, calib, rec=None, png=False, win=20000, zrange=(0.3, 3.0)):
        self.boards, self.pipe, self.rec, self.png, self.win = (left, right), Pipeline(calib, zrange=zrange), rec, png, win
        self.lock, self.n, self.dropped = threading.Lock(), 0, 0
        self.state = {'points': pack_points(np.zeros((0, 3)), []), 'views': b'', 'stats': {}}
        self.timing = collections.deque(maxlen=50)
        if rec:
            os.makedirs(rec, exist_ok=True)

    def step(self):
        pair = grab(self.boards, self.win, self.png)
        if pair is None:
            self.dropped += 1
            return
        xyz, val, t = self.pipe.run(*pair)
        self.timing.append(t)
        if self.rec:
            np.savez_compressed(os.path.join(self.rec, '%06d.npz' % self.n), left=pair[0], right=pair[1],
                                mac_us=now_us())
        self.n += 1
        left, right, _ = self.pipe.last
        views = cv2.imencode('.png', np.hstack([left, np.full((H, 4), 255, np.uint8), right]))[1].tobytes()
        stats = {'pairs': self.n, 'dropped': self.dropped, 'points': len(xyz), 'calib': self.pipe.calib is not None,
                 'source': 'frame.png' if self.png else 'evframe',
                 'timing_ms': {k: round(float(np.median([x[k] for x in self.timing])), 2) for k in t},
                 'boards': [{'host': b.host, 'rtt_ms': None if b.clock.rtt() is None else b.clock.rtt() / 1e3,
                             'uncertain': b.clock.uncertain(), 'errors': dict(b.errors), 'bytes': b.bytes}
                            for b in self.boards]}
        with self.lock:
            self.state = {'points': pack_points(xyz, val), 'views': views, 'stats': stats}


def bench(rec, threads, calib, zrange=(0.3, 3.0)):
    """the recorded pairs through the pipeline: median and p90 ms per stage, the points"""
    cv2.setNumThreads(threads)
    files = sorted(glob.glob(os.path.join(rec, '*.npz')))
    if not files:
        sys.exit('bench: no pairs in %s' % rec)
    pipe, rows, pts = Pipeline(calib, zrange=zrange), [], []
    for f in files:
        z = np.load(f)
        xyz, _, t = pipe.run(z['left'], z['right'])
        t['total'] = sum(t.values())
        rows.append(t)
        pts.append(len(xyz))
    print('%d pairs, %s threads, calib %s, disparity %d+%d, OpenCV %s, %s' % (
        len(rows), threads, bool(calib), pipe.min_disp, pipe.num_disp, cv2.__version__, os.uname().machine))
    for k in rows[0]:
        v = np.array([r[k] for r in rows])
        print('  %-9s median %7.2f ms  p90 %7.2f ms' % (k, np.median(v), np.percentile(v, 90)))
    print('  points    median %d' % np.median(pts))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    lv = sub.add_parser('live')
    lv.add_argument('--left', default='192.168.0.236')
    lv.add_argument('--right', default='192.168.0.168')
    lv.add_argument('--rec')
    lv.add_argument('--calib', default=CALIB)
    lv.add_argument('--png', action='store_true')
    lv.add_argument('--win', type=int, default=20000)
    lv.add_argument('--port', type=int, default=8090)
    zr = dict(type=lambda v: tuple(float(x) for x in v.split(',')), default=(0.3, 3.0),
              help='nearest,farthest depth to search (m), sets the disparity range')
    lv.add_argument('--zrange', **zr)
    lv.add_argument('--seconds', type=float, default=0, help='stop after this long (0: run until killed)')
    cb = sub.add_parser('calib')
    cb.add_argument('--left', default='192.168.0.236')
    cb.add_argument('--right', default='192.168.0.168')
    cb.add_argument('--png', action='store_true')
    cb.add_argument('--n', type=int, default=20, help='board poses')
    cb.add_argument('--square', type=float, default=0.03, help='a square on the screen, measured with a ruler (m)')
    cb.add_argument('--win', type=int, default=150000, help='longer than the 100 ms blink')
    cb.add_argument('--out', default=CALIB)
    cb.add_argument('--from', dest='from_dir', help='solve again from a capture directory')
    bn = sub.add_parser('bench')
    bn.add_argument('rec')
    bn.add_argument('--threads', type=int, default=1)
    bn.add_argument('--calib', default=CALIB)
    bn.add_argument('--zrange', **zr)
    a = ap.parse_args()
    if a.cmd == 'calib':
        return calib_cmd(a)
    calib = load_calib(a.calib)
    if a.cmd == 'bench':
        return bench(a.rec, a.threads, calib, a.zrange)
    live = Live(Board(a.left), Board(a.right), calib, a.rec, a.png, a.win, a.zrange)
    if a.port:
        serve(live, a.port)
    end = time.monotonic() + a.seconds
    while not a.seconds or time.monotonic() < end:
        t0 = time.monotonic()
        live.step()
        time.sleep(max(0.0, 0.1 - (time.monotonic() - t0)))
    print(live.state['stats'])


def serve(live, port):
    """the page on localhost:port in a thread (port 0: any free one); returns the server.
    GET / dvs3d.html (read per request), /checker (the calibration board), /points (pack_points), /views.png (both surfaces), /stats (JSON)"""
    here = os.path.dirname(os.path.abspath(__file__))
    page, checker = os.path.join(here, 'dvs3d.html'), os.path.join(here, 'dvs3d-checker.html')

    class Handler(http.server.BaseHTTPRequestHandler):
        def send(self, body, ctype, code=200):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            with live.lock:
                st = live.state
            path = self.path.split('?')[0]
            if path == '/':
                self.send(open(page, 'rb').read(), 'text/html; charset=utf-8')
            elif path == '/checker':
                self.send(open(checker, 'rb').read(), 'text/html; charset=utf-8')
            elif path == '/points':
                self.send(st['points'], 'application/octet-stream')
            elif path == '/views.png':
                self.send(st['views'], 'image/png') if st['views'] else self.send(b'no pair yet', 'text/plain', 503)
            elif path == '/stats':
                self.send(json.dumps(st['stats']).encode(), 'application/json')
            else:
                self.send(b'not found', 'text/plain', 404)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == '__main__':
    main()
