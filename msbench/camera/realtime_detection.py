#!/usr/bin/env python3
"""realtime_detection.py: live CAMERA-mode YOLO on a USB (UVC) camera, the same code on the E850 and the Jetson
(msbench README, Phase 3).

  python3 camera/realtime_detection.py --sensor camera --runtime ncnn --precision fp16 --model yolo11n-320
        [--threads 4] [--secs 30] [--video /dev/videoN] [--size 640x480]

No OpenCV on the boards: V4L2 mmap capture through ioctl (constants as tools/linux510/ov5647-cap.py), YUYV -> RGB
(BT.601 limited range) and the letterbox in numpy, then benchmark/runtimes.py (the Phase 2 adapters and NMS).
A capture thread keeps only the newest frame; the main loop takes it, so a slow model drops frames instead of falling
behind. Per frame: wait (frame ready -> taken), RGB conversion, letterbox + tensor, inference, NMS, and the end-to-end
latency from the V4L2 buffer timestamp (CLOCK_MONOTONIC; uvcvideo stamps the frame's first USB packet) to the
detections being ready. Counts frames delivered, frames detected, frames dropped. Writes results/realtime/*.json.
--sensor dvs / fusion: Phases 4 and 5, not implemented yet (they stop with the reason).
"""
import argparse
import fcntl
import glob
import json
import mmap
import os
import select
import socket
import struct
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PYDIR = os.path.join(ROOT, 'py', '%d.%d' % sys.version_info[:2])
if os.path.isdir(PYDIR):
    sys.path.insert(0, PYDIR)
sys.path.insert(0, os.path.join(ROOT, 'benchmark'))
import numpy as np                                                         # noqa: E402

VIDIOC_S_FMT, VIDIOC_REQBUFS, VIDIOC_QUERYBUF = 0xc0d05605, 0xc0145608, 0xc0585609
VIDIOC_QBUF, VIDIOC_DQBUF, VIDIOC_STREAMON, VIDIOC_STREAMOFF = 0xc058560f, 0xc0585611, 0x40045612, 0x40045613
CAPTURE, MMAP, YUYV = 1, 1, int.from_bytes(b'YUYV', 'little')


class UVC:
    """V4L2 single-plane mmap capture of YUYV frames"""
    def __init__(self, dev, w, h, nbuf=4):
        self.fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
        f = bytearray(208)
        struct.pack_into('I', f, 0, CAPTURE)
        struct.pack_into('5I', f, 8, w, h, YUYV, 1, 0)                    # field NONE
        fcntl.ioctl(self.fd, VIDIOC_S_FMT, f)
        self.w, self.h, pf = struct.unpack_from('3I', f, 8)
        assert pf == YUYV, 'camera does not give YUYV'
        r = bytearray(20)
        struct.pack_into('3I', r, 0, nbuf, CAPTURE, MMAP)
        fcntl.ioctl(self.fd, VIDIOC_REQBUFS, r)
        self.maps = []
        for i in range(struct.unpack_from('I', r, 0)[0]):
            b = self._buf(i)
            fcntl.ioctl(self.fd, VIDIOC_QUERYBUF, b)
            off, ln = struct.unpack_from('I', b, 64)[0], struct.unpack_from('I', b, 72)[0]
            self.maps.append(mmap.mmap(self.fd, ln, mmap.MAP_SHARED, mmap.PROT_READ, offset=off))
            fcntl.ioctl(self.fd, VIDIOC_QBUF, b)
        fcntl.ioctl(self.fd, VIDIOC_STREAMON, struct.pack('I', CAPTURE))

    @staticmethod
    def _buf(i=0):
        b = bytearray(88)
        struct.pack_into('2I', b, 0, i, CAPTURE)
        struct.pack_into('I', b, 60, MMAP)
        return b

    def read(self, timeout=2.0):
        """(frame bytes copy, V4L2 timestamp s, sequence, dequeue time s)"""
        select.select([self.fd], [], [], timeout)
        b = self._buf()
        fcntl.ioctl(self.fd, VIDIOC_DQBUF, b)
        t_dq = time.clock_gettime(time.CLOCK_MONOTONIC)
        i, used = struct.unpack_from('I', b, 0)[0], struct.unpack_from('I', b, 8)[0]
        sec, usec = struct.unpack_from('qq', b, 24)
        seq = struct.unpack_from('I', b, 56)[0]
        data = bytes(self.maps[i][:used])
        fcntl.ioctl(self.fd, VIDIOC_QBUF, b)
        return data, sec + usec / 1e6, seq, t_dq

    def close(self):
        fcntl.ioctl(self.fd, VIDIOC_STREAMOFF, struct.pack('I', CAPTURE))
        for m in self.maps:
            m.close()
        os.close(self.fd)


def yuyv_rgb(data, w, h, k=1):
    """YUYV 4:2:2 -> RGB uint8 (H/k) x (W/k) x 3, BT.601 limited range. k=2 averages each 2 x 2 block in YUV first
    (one U, V pair per block), a quarter of the conversion work for a 320 model on a 640 x 480 camera"""
    a = np.frombuffer(data, np.uint8, w * h * 2).reshape(h, w // 2, 4)
    if k == 2:
        a = a.reshape(h // 2, 2, w // 2, 4).sum(1, dtype=np.uint16).astype(np.float32)   # two rows summed
        y = (a[:, :, 0] + a[:, :, 2]) * 0.25 - 16
        u, v = a[:, :, 1] * 0.5 - 128, a[:, :, 3] * 0.5 - 128
    else:
        a = a.astype(np.float32)
        y = a[:, :, [0, 2]].reshape(h, w) - 16
        u = np.repeat(a[:, :, 1] - 128, 2, 1)
        v = np.repeat(a[:, :, 3] - 128, 2, 1)
    y *= 1.164
    rgb = np.stack([y + 1.596 * v, y - 0.392 * u - 0.813 * v, y + 2.017 * u], -1)
    return np.clip(rgb + 0.5, 0, 255).astype(np.uint8)                   # rounded


def letterbox(rgb, s):
    """as Ultralytics' LetterBox (centre, pad 114); resize by area mean for an integer factor, else nearest"""
    h, w = rgb.shape[:2]
    g = min(s / h, s / w)
    nw, nh = round(w * g), round(h * g)
    k = round(1 / g)
    if g < 1 and abs(1 / g - k) < 1e-6 and h % k == 0 and w % k == 0:
        im = ((rgb.reshape(nh, k, nw, k, 3).sum((1, 3), dtype=np.uint32) + k * k // 2) // (k * k)).astype(np.uint8)
    elif (nw, nh) != (w, h):
        im = rgb[(np.arange(nh) / g).astype(int)][:, (np.arange(nw) / g).astype(int)]
    else:
        im = rgb
    out = np.full((s, s, 3), 114, np.uint8)
    l, t = round((s - nw) / 2 - 0.1), round((s - nh) / 2 - 0.1)
    out[t:t + nh, l:l + nw] = im
    return out, g, (l, t)


def find_uvc():
    for d in sorted(glob.glob('/sys/class/video4linux/video*')):
        drv = os.path.realpath(d + '/device/driver')
        if drv.endswith('uvcvideo') and open(d + '/index').read().strip() == '0':
            return '/dev/' + os.path.basename(d)
    sys.exit('no UVC camera')


def summary(v):
    v = sorted(v)
    q = lambda f: round(v[min(len(v) - 1, int(f * len(v)))], 2)
    return {'n': len(v), 'median': q(.5), 'p95': q(.95), 'mean': round(sum(v) / len(v), 2), 'max': round(v[-1], 2)} if v else None


def main():
    ap = argparse.ArgumentParser(description='live CAMERA-mode YOLO (msbench Phase 3)')
    ap.add_argument('--sensor', default='camera', choices=['camera', 'dvs', 'fusion'])
    ap.add_argument('--fusion-method', default='all')
    ap.add_argument('--runtime', default='ncnn')
    ap.add_argument('--precision', default='fp16')
    ap.add_argument('--model', default='yolo11n-320')
    ap.add_argument('--threads', type=int, default=4)
    ap.add_argument('--cpus', default='', help='comma list; default the run_benchmark.py set for --threads')
    ap.add_argument('--secs', type=float, default=30)
    ap.add_argument('--video', default='')
    ap.add_argument('--size', default='640x480')
    ap.add_argument('--conf', type=float, default=0.25)
    ap.add_argument('--models-dir', default=os.path.join(ROOT, 'models', 'files'))
    ap.add_argument('--tag', default='')
    a = ap.parse_args()
    if a.sensor != 'camera':
        sys.exit('--sensor %s: Phase %s, not implemented yet (no DVS-trained YOLO11n; see README)' %
                 (a.sensor, 4 if a.sensor == 'dvs' else 5))
    import run_benchmark as rb
    from runtimes import RUNTIMES, nms
    dev = rb.device()
    cpus = [int(c) for c in a.cpus.split(',')] if a.cpus else rb.CPUSETS.get(dev, {}).get(a.threads, list(range(a.threads)))
    os.sched_setaffinity(0, cpus)
    man = json.load(open(os.path.join(a.models_dir, 'manifest.json')))[a.model]
    cls, key = RUNTIMES[(a.runtime, a.precision)]
    rt = cls(os.path.join(a.models_dir, a.model), man['files'][key], a.threads)
    s = man['input']['size']
    norm = a.runtime.startswith('tflite') and man['tflite_coords_normalized']
    w, h = map(int, a.size.split('x'))
    cam = UVC(a.video or find_uvc(), w, h)
    latest, lock, stop = [None], threading.Lock(), threading.Event()
    got = {'frames': 0, 'overwritten': 0}

    def grab():
        while not stop.is_set():
            try:
                fr = cam.read()
            except (BlockingIOError, OSError):
                continue
            with lock:
                got['frames'] += 1
                got['overwritten'] += latest[0] is not None
                latest[0] = fr
    th = threading.Thread(target=grab, daemon=True)
    th.start()
    for _ in range(5):                                                       # warm-up on real frames
        while latest[0] is None:
            time.sleep(0.005)
        with lock:
            fr, latest[0] = latest[0], None
        rgb = yuyv_rgb(fr[0], cam.w, cam.h)
        rt.infer(rt.prepare(letterbox(rgb, s)[0]))
    with lock:
        got['frames'] = got['overwritten'] = 0
    k2 = 2 if min(s / cam.h, s / cam.w) == 0.5 and cam.w % 4 == 0 and cam.h % 2 == 0 else 1
    T = {k: [] for k in ('wait', 'rgb', 'prep', 'infer', 'nms', 'e2e', 'age_at_take')}
    seqs, dets, t0 = [], [], time.monotonic()
    c0 = os.times()
    while time.monotonic() - t0 < a.secs:
        tw = time.clock_gettime(time.CLOCK_MONOTONIC)
        while True:
            with lock:
                fr, latest[0] = latest[0], None
            if fr is not None:
                break
            time.sleep(0.001)
        t1 = time.clock_gettime(time.CLOCK_MONOTONIC)
        data, ts, seq, tdq = fr
        rgb = yuyv_rgb(data, cam.w, cam.h, k2)
        t2 = time.clock_gettime(time.CLOCK_MONOTONIC)
        x = rt.prepare(letterbox(rgb, s)[0])
        t3 = time.clock_gettime(time.CLOCK_MONOTONIC)
        out = rt.infer(x)
        t4 = time.clock_gettime(time.CLOCK_MONOTONIC)
        b, sc, cl = nms(out, s, norm, conf=a.conf)
        t5 = time.clock_gettime(time.CLOCK_MONOTONIC)
        for k, v in (('wait', t1 - tw), ('rgb', t2 - t1), ('prep', t3 - t2), ('infer', t4 - t3), ('nms', t5 - t4),
                     ('e2e', t5 - ts), ('age_at_take', t1 - ts)):
            T[k].append(v * 1e3)
        seqs.append(seq)
        dets.append(len(sc))
    wall = time.monotonic() - t0
    c1 = os.times()
    stop.set()
    th.join(3)
    cam.close()
    st = dict(ln.split(':', 1) for ln in open('/proc/self/status').read().splitlines() if ':' in ln)
    r = {'device': dev, 'host': socket.gethostname(), 'sensor': 'camera', 'data': 'live', 'model': a.model,
         'runtime': a.runtime, 'precision': a.precision, 'threads': a.threads, 'cpus': cpus, 'camera': a.size,
         'secs': round(wall, 1), 'yuv_downscale': k2, 'frames_delivered': got['frames'], 'frames_detected': len(seqs),
         'frames_dropped': got['frames'] - len(seqs), 'camera_fps': round(got['frames'] / wall, 2),
         'detect_fps': round(len(seqs) / wall, 2), 'seq_gaps': int(sum(max(0, b - a - 1) for a, b in zip(seqs, seqs[1:]))),
         'mean_detections': round(sum(dets) / max(1, len(dets)), 2), 'process_cpu_cores':
         round((c1.user + c1.system - c0.user - c0.system) / wall, 2), 'rss_peak_mb':
         round(int(st.get('VmHWM', '0 kB').split()[0]) / 1024, 1), 'ms': {k: summary(v) for k, v in T.items()},
         'runtime_info': rt.info, 'env': {k: v for k, v in os.environ.items() if k.startswith('OMP_')},
         'time': time.strftime('%Y-%m-%dT%H:%M:%S')}
    os.makedirs(os.path.join(ROOT, 'results', 'realtime'), exist_ok=True)
    f = os.path.join(ROOT, 'results', 'realtime', '%s-%s-%s-%s-t%d%s.json' % (dev, a.model, a.runtime, a.precision,
                                                                             a.threads, '-' + a.tag if a.tag else ''))
    json.dump(r, open(f, 'w'), indent=1)
    print(json.dumps({k: r[k] for k in ('camera_fps', 'detect_fps', 'frames_dropped', 'mean_detections',
                                         'process_cpu_cores')}))
    for k, v in r['ms'].items():
        print('  %-12s %s' % (k, v))
    print('wrote', f)


def _selftest():
    w, h = 4, 2
    yuyv = bytes([235, 128, 16, 128] * (w * h // 2))                     # white, black pixels
    rgb = yuyv_rgb(yuyv, w, h)
    assert rgb[0, 0].tolist() == [255, 255, 255] and rgb[0, 1].tolist() == [0, 0, 0], rgb[0, :2]
    im = np.zeros((480, 640, 3), np.uint8)
    im[:, :, 0] = 200
    out, g, (l, t) = letterbox(im, 320)
    assert g == 0.5 and (l, t) == (0, 40) and out[39, 0, 0] == 114 and out[40, 0, 0] == 200 and out[280, 0, 0] == 114
    out, g, (l, t) = letterbox(im, 640)
    assert g == 1 and (l, t) == (0, 80) and out[80:560, :, 0].min() == 200
    yy, xx = np.mgrid[0:480, 0:320]                                        # smooth, in-gamut picture
    big = np.stack([60 + xx * 0.3 + yy * 0.1, 110 + yy * 0.07, 61 + xx * 0.3 + yy * 0.1, 140 - xx * 0.05], -1)
    big = big.astype(np.uint8).tobytes()
    full, half = yuyv_rgb(big, 640, 480).astype(int), yuyv_rgb(big, 640, 480, 2).astype(int)
    ref = full.reshape(240, 2, 320, 2, 3).mean((1, 3))
    assert np.abs(half - ref).max() < 2, np.abs(half - ref).max()            # the same picture
    print('realtime_detection selftest ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--selftest']:
        _selftest()
    else:
        main()
