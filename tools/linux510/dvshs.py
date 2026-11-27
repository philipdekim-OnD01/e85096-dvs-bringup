#!/usr/bin/env python3
"""dvshs: a high-speed camera from the DVS and a USB camera (linux510 docs 11.121). The C core (dvshs.c) fuses;
this records on the board, lines the clocks up, maps the pixels and encodes.

  dvshs.py rec DIR [-s 1]          (board) 640x480 YUYV frames with their CLOCK_MONOTONIC times (v4l2-ctl --verbose),
                                   the DVS packets of s seconds (the live view's POST /rec, 3 s countdown) and clock probes
  dvshs.py prep DIR [--calib J]    the events (dvsview-t --replay), the replay clock's origin, camera times, the map
  dvshs.py make DIR [--c 0.2] [--layout sbs] [--out slowmo.mp4]   1 ms frames -> ffmpeg (30 fps: 33x slower)
  dvshs.py demo                    (board, the desktop icon) rec + prep + make, then plays it in mpv
  dvshs.py compare DIR [--c 0.3]   (Mac) compare.mp4 [camera | DVS 1 ms | fused] and hs1000.mp4 (1000 fps tag)
  dvshs.py eval DIR --c 0.1,0.2    every 2nd frame a key, the frames between are the truth: PSNR per c (and --T)
  dvshs.py calib DIR [--out J]     (Mac, OpenCV) the blinking checker in both views -> homography camera -> DVS
  dvshs.py calib-points OUT u,v,x,y ...   the same from hand-picked camera (u, v) / DVS (x, y) pairs

Clocks. The replay's display clock starts at 0 where the recording began: its first line is the live decoder's
state (rec_state()), so replay 0 is the live display clock (S.t, = /evframe's newest) at that moment. While
recording, rec probes /evframe's clocks and fetches a few time surfaces; prep finds the origin o by matching those
surfaces to the replayed events' (dvs_surface() rules), among the newest values seen around the start. A camera
frame's exposure start (uvcvideo: CLOCK_MONOTONIC, start of exposure) is then at replay mono - d2m - o + delta,
d2m = CLOCK_MONOTONIC - display (X-Dvs-Disp2Mono), delta a camera correction in calib.json (default 0).

The board has no root for this (11.121): the live view (e850-dvs.service) is not changed, the recording comes from
/var/tmp/dvsrec (philip's) and is moved out at once so the action classifier's data stays clean.
"""
import argparse
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zlib

import numpy as np

try:
    import cv2
except ImportError:                     # the board: only calib needs it
    cv2 = None

HERE = os.path.dirname(os.path.abspath(__file__))
EV_DTYPE = np.dtype([('t', '<u4'), ('x', '<u2'), ('y', '<u2')])
CW, CH, DW, DH = 640, 480, 960, 720
SX, SY = DW // 2, DH // 2               # dvs_surface: 2 x 2 px
BASE = 'http://127.0.0.1:8080'
REC_DIR = '/var/tmp/dvsrec'
OUT_DIR = '/var/tmp/dvshs'
CALIB = os.path.expanduser('~/.local/share/dvshs/calib.json')
KEEP = 10                               # demo recordings kept on the board
SNAP_WIN = 10000
PATTERN = (9, 6)                        # dvs3d-checker.html: 10 x 7 squares


# ---- pure parts (tested) ----

def parse_v4l2_log(text):
    """CLOCK_MONOTONIC us of each dequeued frame, from v4l2-ctl --verbose ('... ts: 175987.586238 ...')"""
    out = []
    for line in text.splitlines():
        if line.startswith('cap dqbuf') and ' ts: ' in line:
            s, us = line.split(' ts: ')[1].split()[0].split('.')
            out.append(int(s) * 1000000 + int(us))
    return out


def _ss(t, v, side='right'):
    """searchsorted of a uint32 time: a plain int made numpy convert the whole 17M array each call (0.11 s on the
    board, 40 s of a prep, 11.121)"""
    return np.searchsorted(t, np.uint32(min(max(int(v), 0), 0xffffffff)), side)


def surface(ev, t_end, win):
    """dvs_surface() of events (t_end - win, t_end]: 360 x 480, the latest event of a pixel 1 + 254 (win - age) / win"""
    e = ev[_ss(ev['t'], t_end - win):_ss(ev['t'], t_end)]
    val = (1 + 254 * (win - (t_end - e['t'].astype(np.int64))) // win).astype(np.uint8)
    idx = (e['y'] & 0x3ff).astype(np.int64) // 2 * SX + e['x'] // 2
    o = np.argsort(idx, kind='stable')          # time order kept within a pixel: its last is its latest
    s = idx[o]
    last = np.r_[s[1:] != s[:-1], True] if len(s) else np.zeros(0, bool)
    img = np.zeros(SY * SX, np.uint8)
    img[s[last]] = val[o][last]                 # np.maximum.at was ~40 s of a board prep (numpy 1.24)
    return img.reshape(SY, SX)


def _match(a, b):
    """(mean |a - b| over the pixels either fired, fraction of those within 2 levels)"""
    u = (a > 0) | (b > 0)
    n = int(u.sum())
    if not n:
        return 255.0, 0.0
    d = np.abs(a[u].astype(np.int16) - b[u].astype(np.int16))
    return float(d.mean()), float((d <= 2).mean())


def vote_origin(ev, snaps, lo, hi, bin_us=50):
    """every replayed event at a pixel the live surface shows votes for the origin that puts it at that pixel's age:
    o = t_end - (t + age(value)). Each pixel's latest event votes for the true o; a fan's earlier passes vote one
    blade period off, but only for the pixels it covers and smeared by its speed. O(events in [lo, hi]), once.
    shortcut: o in [lo, hi] must not cross the 32-bit display clock's wrap (71.6 min), upgrade if a live view runs that long"""
    t = ev['t'].astype(np.int64)
    votes = np.zeros((hi - lo) // bin_us + 1)
    for te, win, img in snaps:
        a, b = np.searchsorted(t, te - hi - win, 'right'), np.searchsorted(t, te - lo, 'right')
        e = ev[a:b]
        v = img.ravel()[(e['y'] & 0x3ff).astype(np.int64) // 2 * SX + e['x'] // 2].astype(np.int64)
        ok = v > 0
        age = win - (2 * v[ok] - 1) * win // 508          # the middle of value v's age range
        k = (te - (t[a:b][ok] + age) - lo) // bin_us
        k = k[(k >= 0) & (k < len(votes))]
        votes += np.bincount(k, minlength=len(votes))
    return lo + int(np.argmax(np.convolve(votes, np.ones(3), 'same'))) * bin_us


def find_origin(ev, snaps, cands, margin_us=50000, fine_us=200, step_us=25):
    """the live display time o of replay 0, near the candidates (the newest values seen as the recording began):
    vote_origin() over them +- margin, then the o whose replayed surfaces differ least from the live ones within
    +- fine. snaps [(t_end live, win, img)]. Returns (o, score): score the fraction of fired pixels that match, ~1
    when found. fine 200: the vote was 50 and 75 us off on board 6 (11.121), each step costs 3 surfaces"""
    c = vote_origin(ev, snaps, min(cands) - margin_us, max(cands) + margin_us)
    best = (np.inf, 0.0, c)
    for o in range(c - fine_us, c + fine_us + 1, step_us):
        mads, scores = zip(*(_match(surface(ev, (te - o) % (1 << 32), w), img) for te, w, img in snaps))
        if np.mean(mads) < best[0]:
            best = (float(np.mean(mads)), float(np.mean(scores)), o)
    return best[2], best[1]


def origin_candidates(polls):
    """the newest values seen from the poll before the last 'countdown' to the one after the first 'recording':
    the recording began on one of them (dvs_record() runs between buffers, then the state turns)"""
    st = [p['state'] for p in polls]
    j = st.index('recording') if 'recording' in st else len(st) - 1
    i = max(k for k in range(j + 1) if st[k] != 'recording') if any(s != 'recording' for s in st[:j + 1]) else 0
    out = []
    for p in polls[max(0, i - 1):j + 2]:
        if p['newest'] not in out:
            out.append(p['newest'])
    return out


def unwrap(v32, near):
    """the 64-bit value = v32 (mod 2^32) closest to near"""
    v = (near & ~0xffffffff) | (v32 & 0xffffffff)
    return min((v - (1 << 32), v, v + (1 << 32)), key=lambda x: abs(x - near))


def cam_times(mono, d2m, origin, delta=0):
    """camera exposure starts in replay us"""
    return np.asarray(mono, np.int64) - d2m - origin + delta


def homography_map(H, cam=(CW, CH), dvs=(DW, DH), roi=None):
    """int32 per camera pixel (row-major): the DVS pixel y*DW+x under H (camera px -> DVS px), -1 outside it or
    outside roi (cx, cy, rx, ry), an ellipse in camera px: where both see the same moving thing. Elsewhere the
    two sensors shake apart (11.121: the fan shakes the desk) and the DVS's edges only add error"""
    v, u = np.mgrid[0:cam[1], 0:cam[0]]
    p = np.stack([u.ravel(), v.ravel(), np.ones(u.size)]) .astype(np.float64)
    q = np.asarray(H, np.float64) @ p
    x, y = np.rint(q[0] / q[2]), np.rint(q[1] / q[2])
    ok = (q[2] > 0) & (x >= 0) & (x < dvs[0]) & (y >= 0) & (y < dvs[1])
    if roi is not None:
        cx, cy, rx, ry = roi
        ok &= ((u.ravel() - cx) / rx) ** 2 + ((v.ravel() - cy) / ry) ** 2 <= 1
    return np.where(ok, y * dvs[0] + x, -1).astype(np.int32)


def onoff_counts(ev):
    """(n_on, n_off) per DVS pixel, float64 DW * DH each: one bincount (17M events, ~1 s on the board)"""
    k = ((ev['y'] & 0x3ff).astype(np.int64) * DW + ev['x']) * 2 + (ev['y'] >> 15)
    n = np.bincount(k, minlength=2 * DW * DH).astype(np.float64).reshape(-1, 2)
    return n[:, 0], n[:, 1]


def onoff_ratio(ev, min_count=20, counts=None):
    """c_off / c_on = n_on / n_off over the pixels with min_count events or more: where something keeps moving
    (the fan), log L comes back to where it was, so c_on n_on = c_off n_off"""
    n_on, n_off = counts or onoff_counts(ev)
    keep = n_on + n_off >= min_count
    return float(n_on[keep].sum() / max(1, n_off[keep].sum()))


def onoff_ratio_map(ev, min_count=20, lo=0.25, hi=4.0):
    """onoff_ratio() per DVS pixel, float32 720 x 960: the fan's pixels differ (11.121: 0.44 to 1.16 n_on/n_off,
    a blade passing gives ~2 ON in 1 ms, ~5 OFF over 15 ms); a pixel with fewer events gets the global one"""
    n_on, n_off = onoff_counts(ev)
    with np.errstate(divide='ignore', invalid='ignore'):
        r = np.clip(n_on / n_off, lo, hi)
    r = np.where(n_on + n_off >= min_count, r, onoff_ratio(ev, min_count, (n_on, n_off)))
    return r.reshape(DH, DW).astype(np.float32)


def dvs_panel(ev, t, win=1000):
    """the events of (t - win, t] as RGB 480 x 640: ON green, OFF red (the DVS 960 x 720 scaled 2/3)"""
    e = ev[_ss(ev['t'], t - win):_ss(ev['t'], t)]
    img = np.zeros((CH, CW, 3), np.uint8)
    off = (e['y'] & 0x8000) > 0
    ys, xs = (e['y'] & 0x3ff).astype(np.int64) * 2 // 3, e['x'].astype(np.int64) * 2 // 3
    img[ys[~off], xs[~off], 1] = 255
    img[ys[off], xs[off], 0] = 255
    return img


def span(cam_t, T, ev_first, ev_last, step):
    """[from, to) of the 1 ms frames: inside both the keys' exposure middles and the events, from on the step grid"""
    f = max(int(cam_t[0]) + T // 2, int(ev_first))
    return -(-f // step) * step, min(int(cam_t[-1]) + T // 2, int(ev_last))


def fit_homography(src, dst):
    """DLT (normalised) of N >= 4 point pairs, src -> dst"""
    def norm(p):
        m, s = p.mean(0), np.sqrt(2) / np.mean(np.linalg.norm(p - p.mean(0), axis=1))
        return np.array([[s, 0, -s * m[0]], [0, s, -s * m[1]], [0, 0, 1]])
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    Ts, Td = norm(src), norm(dst)
    a = (np.c_[src, np.ones(len(src))] @ Ts.T)[:, :2]
    b = (np.c_[dst, np.ones(len(dst))] @ Td.T)[:, :2]
    rows = []
    for (x, y), (u, v) in zip(a, b):
        rows += [[-x, -y, -1, 0, 0, 0, u * x, u * y, u], [0, 0, 0, -x, -y, -1, v * x, v * y, v]]
    H = np.linalg.svd(np.array(rows))[2][-1].reshape(3, 3)
    H = np.linalg.inv(Td) @ H @ Ts
    return H / H[2, 2]


def checker_homography(cam_gray, dvs_count):
    """camera -> DVS homography from the blinking board seen by both: the DVS fires on the white squares alone
    (dvs3d.find_corners: what fired is white, a median drops the noise)"""
    ok, a = cv2.findChessboardCornersSB(cam_gray, PATTERN)
    b = cv2.medianBlur(np.where(dvs_count > 0, 255, 0).astype(np.uint8), 5)
    ok2, c = cv2.findChessboardCornersSB(b, PATTERN)
    if not (ok and ok2):
        raise RuntimeError('board not found: camera %s, DVS %s' % (ok, ok2))
    H, _ = cv2.findHomography(a.reshape(-1, 2), c.reshape(-1, 2), cv2.RANSAC, 3.0)
    return H


# ---- the board ----

def http(path, data=None, timeout=5):
    req = urllib.request.Request(BASE + path, data=data, method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def clocks():
    _, h, _ = http('/evframe')
    return int(h['X-Dvs-Newest']), int(h['X-Dvs-Disp2Mono']), int(h['X-Dvs-Mono'])


def find_camera():
    """the capture node of a UVC camera that is not the microscope (04b4:00c3, e850-open)"""
    for v in sorted(glob.glob('/sys/class/video4linux/video*'), key=lambda p: int(p.rsplit('video', 1)[1])):
        try:
            drv = os.path.basename(os.path.realpath(v + '/device/driver'))
            idx = open(v + '/index').read().strip()
            usb = (open(v + '/device/../idVendor').read().strip() + ':' +
                   open(v + '/device/../idProduct').read().strip())
        except OSError:
            continue
        if drv == 'uvcvideo' and idx == '0' and usb != '04b4:00c3':
            return '/dev/' + os.path.basename(v)
    return None


def stop_viewer(dev):
    """close an mpv showing dev (e850-open cam): one process at a time streams a UVC camera"""
    out = subprocess.run(['pgrep', '-a', '-x', 'mpv'], capture_output=True, text=True).stdout
    pids = [int(line.split()[0]) for line in out.splitlines() if dev in line]
    for p in pids:
        os.kill(p, 15)
    for _ in range(50):
        if not any(os.path.exists('/proc/%d' % p) for p in pids):
            break
        time.sleep(0.1)
    if pids:
        time.sleep(1.0)                 # uvcvideo lets go a little after (11.114: a restart at once failed)
    return pids


def rec(d, s=1, dev=None):
    """record s seconds into d: cam.yuyv + cam.log, dvs.gz + dvs.json, rec.json (polls), snaps.npz"""
    dev = dev or find_camera()
    if not dev:
        raise RuntimeError('USB 카메라가 연결되어 있지 않습니다')
    newest, _, _ = clocks()             # the live view answers, else this raises
    os.makedirs(d)
    stop_viewer(dev)
    n = int((3 + s + 1.5) * 16)          # countdown + recording + slack at ~15 fps
    log = open(os.path.join(d, 'cam.log'), 'w')
    cam = subprocess.Popen(['v4l2-ctl', '-d', dev, '--set-fmt-video=width=%d,height=%d,pixelformat=YUYV' % (CW, CH),
                            '--set-parm=30', '--stream-mmap=4', '--stream-count=%d' % n,
                            '--stream-to=' + os.path.join(d, 'cam.yuyv'), '--verbose'], stdout=log,
                           stderr=subprocess.STDOUT)
    time.sleep(0.5)
    _, _, last0 = http('/rec')
    last0 = json.loads(last0).get('last')
    http('/rec', json.dumps({'label': 'other', 's': s}).encode())
    polls, snaps, t_rec = [], [], None
    while True:
        st = json.loads(http('/rec')[2])
        nw, d2m, mono = clocks()
        polls.append({'state': st['state'], 'newest': nw, 'd2m': d2m, 'mono': mono, 'last': st.get('last')})
        if st['state'] == 'recording' and t_rec is None:
            t_rec = time.monotonic()
        if t_rec is not None and len(snaps) < 3 and time.monotonic() - t_rec > 0.2 + 0.3 * len(snaps) * s:
            te = (nw - 5000) % (1 << 32)
            code, h, body = http('/evframe?t_end=%d&win=%d' % (te, SNAP_WIN))
            if code == 200:
                snaps.append((te, SNAP_WIN, np.frombuffer(zlib.decompress(body), np.uint8).reshape(SY, SX)))
        if st['state'] == 'idle' and st.get('last') != last0 and t_rec is not None:
            break
        if len(polls) > 20000:
            raise RuntimeError('the recorder did not finish')
        time.sleep(0.004)
    cam.wait(timeout=30)
    log.close()
    last = polls[-1]['last']
    if not last or last.startswith('error'):
        raise RuntimeError('DVS recording: %s' % last)
    base = os.path.join(REC_DIR, last[:-3])
    shutil.move(base + '.gz', os.path.join(d, 'dvs.gz'))
    shutil.move(base + '.json', os.path.join(d, 'dvs.json'))
    np.savez_compressed(os.path.join(d, 'snaps.npz'), t_end=np.array([x[0] for x in snaps], np.int64),
                        win=np.array([x[1] for x in snaps]), img=np.array([x[2] for x in snaps]))
    with open(os.path.join(d, 'rec.json'), 'w') as f:
        json.dump({'s': s, 'dev': dev, 'polls': polls}, f)
    meta = json.load(open(os.path.join(d, 'dvs.json')))
    if not meta.get('events'):
        raise RuntimeError('DVS recording lost its data (queue overflow?): %s' % meta)
    return d


# ---- prep / make ----

def replay(d):
    """dvs.gz -> dvs.bin with dvsview-t (the board: next to this script or on PATH; the Mac: in Docker)"""
    out = os.path.join(d, 'dvs.bin')
    if os.path.exists(out):
        return out
    exe = shutil.which('dvsview-t') or os.path.join(HERE, 'dvsview-t')
    if os.uname().sysname == 'Linux' and os.path.exists(exe):
        subprocess.run([exe, '--replay', os.path.join(d, 'dvs.gz'), out], check=True, stdout=subprocess.DEVNULL)
    else:
        b = os.path.expanduser('~/e850-logs/dvsfuse/bin')
        subprocess.run(['docker', 'run', '--rm', '-v', b + ':/b:ro', '-v', os.path.abspath(d) + ':/d', 'debian:12',
                        '/b/dvsview-t', '--replay', '/d/dvs.gz', '/d/dvs.bin'], check=True, stdout=subprocess.DEVNULL)
    return out


def load_calib(path):
    if path and os.path.exists(path):
        return json.load(open(path))
    return {}


def prep(d, calib=None, log=print):
    cal = load_calib(calib)
    ev = np.fromfile(replay(d), EV_DTYPE)
    r = json.load(open(os.path.join(d, 'rec.json')))
    z = np.load(os.path.join(d, 'snaps.npz'))
    snaps = list(zip(z['t_end'].tolist(), z['win'].tolist(), list(z['img'])))
    cands = origin_candidates(r['polls'])
    t0 = time.monotonic()
    o, score = find_origin(ev, snaps, cands) if snaps else (cands[0], 0.0)
    log('dvshs: origin %d, score %.3f in %.1f s' % (o, score, time.monotonic() - t0))
    if score < 0.9:
        log('dvshs: warning: the clock origin matched only %.2f (%d snapshots)' % (score, len(snaps)))
    rec_polls = [p for p in r['polls'] if p['state'] == 'recording'] or r['polls']
    d2m = int(np.median([p['d2m'] for p in rec_polls]))
    near = rec_polls[0]['mono'] - d2m   # the live display clock, 64 bits, about now
    o64 = unwrap(o, near)
    mono = parse_v4l2_log(open(os.path.join(d, 'cam.log')).read())
    nfr = os.path.getsize(os.path.join(d, 'cam.yuyv')) // (CW * CH * 2)
    mono = mono[:nfr]
    delta = int(cal.get('delta_us', 0))
    ct = cam_times(mono, d2m, o64, delta)
    np.savetxt(os.path.join(d, 'cam.t'), ct, fmt='%d')
    H = np.array(cal['H']) if 'H' in cal else np.diag([DW / CW, DH / CH, 1.0])
    homography_map(H, roi=cal.get('roi')).tofile(os.path.join(d, 'map.bin'))
    period = int(np.median(np.diff(mono))) if len(mono) > 1 else 66667
    T = int(cal.get('T_us', min(66000, 0.95 * period)))
    ratio = float(cal['ratio'] if 'ratio' in cal else onoff_ratio(ev))
    onoff_ratio_map(ev).tofile(os.path.join(d, 'ratio.bin'))
    f, t = span(ct, T, ev['t'][0], ev['t'][-1], 1000)
    p = {'origin': o64, 'origin32': o, 'score': score, 'cands': cands, 'd2m': d2m, 'delta': delta, 'T': T,
         'ratio': ratio, 'from': f, 'to': t, 'frames': len(mono), 'period': period, 'events': len(ev),
         'calib': cal.get('source', 'none (DVS scaled to the camera)'), 'c': float(cal.get('c', 0.2)),
         'blur': int(cal.get('blur', 1))}
    json.dump(p, open(os.path.join(d, 'prep.json'), 'w'), indent=1)
    log('dvshs: prep: %d events, %d frames (%.1f fps), origin score %.2f, T %d us, ratio %.2f, %d..%d us' %
        (len(ev), len(mono), 1e6 / period, score, T, ratio, f, t))
    return p


def dvshs_bin():
    return os.environ.get('DVSHS') or shutil.which('dvshs') or os.path.join(HERE, 'dvshs')


def fuse_cmd(d, p, c, extra=()):
    j = lambda n: os.path.join(d, n)   # noqa: E731
    r = ['--ratio-map', j('ratio.bin')] if os.path.exists(j('ratio.bin')) else ['--ratio', str(p['ratio'])]
    return [dvshs_bin(), 'fuse', j('dvs.bin'), j('cam.yuyv'), j('cam.t'), j('map.bin'), '--c', str(c)] + r + [
            '--T', str(p['T']), '--from', str(p['from']), '--to', str(p['to']), '--blur', str(p.get('blur', 0))] + list(extra)


def make(d, c=None, layout='sbs', out=None, label=True, log=print, blur=None):
    p = json.load(open(os.path.join(d, 'prep.json')))
    if blur is not None:
        p['blur'] = blur
    c = p['c'] if c is None else c
    out = out or os.path.join(d, 'slowmo.mp4')
    w = 2 * CW if layout == 'sbs' else CW
    vf = []
    font = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    has_text = b'drawtext' in subprocess.run(['ffmpeg', '-hide_banner', '-filters'], capture_output=True).stdout
    if label and has_text and os.path.exists(font):
        t0 = p['from'] // 1000
        vf = ['-vf', "drawtext=fontfile=%s:fontsize=22:fontcolor=white:box=1:boxcolor=black@0.5:x=8:y=8:"
                     "text='t = %%{eif\\:n+%d\\:d} ms   (1000 fps, 33x)'" % (font, t0)]
    t1 = time.monotonic()
    # the live DVS keeps CPU1-6 busy; spinning OpenMP waits starve ffmpeg (make 59 -> 45 s passive, 11.121)
    env = dict(os.environ, OMP_NUM_THREADS=os.environ.get('OMP_NUM_THREADS', '4'),
               OMP_WAIT_POLICY=os.environ.get('OMP_WAIT_POLICY', 'passive'))
    fz = subprocess.Popen(['nice', '-n', '10'] + fuse_cmd(d, p, c, ['--layout', layout]), stdout=subprocess.PIPE,
                          env=env)
    enc = subprocess.run(['nice', '-n', '10', 'ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'yuv420p', '-s',
                          '%dx%d' % (w, CH), '-r', '30', '-i', '-'] + vf +
                         ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '20', '-pix_fmt', 'yuv420p', out],
                         stdin=fz.stdout)
    fz.stdout.close()
    if fz.wait() or enc.returncode:
        raise RuntimeError('dvshs/ffmpeg failed (%s, %s)' % (fz.returncode, enc.returncode))
    log('dvshs: make: %s in %.1f s (c %.2f)' % (out, time.monotonic() - t1, c))
    return out


def compare(d, c=None, blur=None, log=print):
    """(Mac) compare.mp4 [camera keys' blend | DVS events of the last 1 ms | fused] at 30 fps, and hs1000.mp4: the
    fused frames tagged 1000 fps (players may not keep up)"""
    p = json.load(open(os.path.join(d, 'prep.json')))
    sbs = make(d, c, 'sbs', os.path.join(d, 'slowmo.mp4'), label=False, log=log, blur=blur)
    ev = np.fromfile(os.path.join(d, 'dvs.bin'), EV_DTYPE)
    ts = range(p['from'], p['to'], 1000)
    enc = subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s',
                            '%dx%d' % (CW, CH), '-r', '30', '-i', '-', '-i', sbs, '-filter_complex',
                            '[1:v]crop=%d:%d:0:0[l];[1:v]crop=%d:%d:%d:0[r];[l][0:v][r]hstack=3' % (CW, CH, CW, CH, CW),
                            '-c:v', 'libx264', '-crf', '20', '-pix_fmt', 'yuv420p', os.path.join(d, 'compare.mp4')],
                           stdin=subprocess.PIPE)
    for t in ts:
        enc.stdin.write(dvs_panel(ev, t).tobytes())
    enc.stdin.close()
    if enc.wait():
        raise RuntimeError('ffmpeg compare failed')
    make(d, c, 'fused', os.path.join(d, 'fused.mp4'), label=False, log=log, blur=blur)
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-itsscale', '0.03', '-i', os.path.join(d, 'fused.mp4'),
                    '-c', 'copy', os.path.join(d, 'hs1000.mp4')], check=True)
    log('dvshs: compare: %s, hs1000.mp4 (%d frames)' % (os.path.join(d, 'compare.mp4'), len(ts)))


def evaluate(d, cs, Ts=None, blurs=None):
    p = json.load(open(os.path.join(d, 'prep.json')))
    rows = []
    for b in blurs or [p.get('blur', 0)]:
        for T in Ts or [p['T']]:
            for c in cs:
                q = dict(p, T=T, blur=b)
                r = subprocess.run(fuse_cmd(d, q, c, ['--eval']), capture_output=True, text=True, check=True)
                rows.append(dict(json.loads(r.stdout), blur=b))
                print(json.dumps(rows[-1]))
    json.dump(rows, open(os.path.join(d, 'eval.json'), 'w'), indent=1)
    return rows


def say(title, text, wait=True):
    exe = shutil.which('e850-say')
    if not exe or not os.environ.get('DISPLAY'):
        print('%s: %s' % (title, text), file=sys.stderr)
        return None
    pr = subprocess.Popen([exe, title, text])
    if wait:
        pr.wait()
    return pr


def demo():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    d = os.path.join(OUT_DIR, stamp)
    os.makedirs(OUT_DIR, exist_ok=True)
    for old in sorted(glob.glob(os.path.join(OUT_DIR, '2*')))[:-(KEEP - 1)]:
        shutil.rmtree(old, ignore_errors=True)
    t0 = time.monotonic()
    try:
        clocks()
    except OSError:
        return say('고속 촬영', 'DVS 화면이 켜져 있지 않습니다. 바탕화면의 DVS 아이콘을 먼저 눌러 주세요.') and 1
    if not find_camera():
        return say('고속 촬영', 'USB 카메라가 연결되어 있지 않습니다. USB-A 포트에 꽂아 주세요.') and 1
    if not os.path.exists(CALIB):
        return say('고속 촬영', '두 카메라의 보정 파일이 없습니다 (%s).\nMac에서 dvshs.py calib으로 만들어 넣어 주세요.'
                   % CALIB) and 1
    note = say('고속 촬영', '3초 뒤 1초 동안 녹화합니다. 끝나면 합성해서 슬로모션으로 보여 줍니다 (약 1분).',
               wait=False)
    times, err = {}, None
    try:
        rec(d, 1)
        times['rec'] = time.monotonic() - t0
        prep(d, CALIB)
        times['prep'] = time.monotonic() - t0
        make(d, layout='sbs')
        times['make'] = time.monotonic() - t0
    except Exception as e:              # from the icon nobody sees stderr
        err = e
    if note:
        note.terminate()
    if err:
        say('고속 촬영', '촬영 또는 합성에 실패했습니다.\n%s\n(%s)' % (err, d))
        raise err
    json.dump(times, open(os.path.join(d, 'times.json'), 'w'))
    print('dvshs: demo %s: %s' % (d, times), file=sys.stderr)
    subprocess.Popen(['mpv', '--loop', '--no-terminal', '--title=고속 촬영 (왼쪽 카메라 15 fps, 오른쪽 합성 1000 fps)',
                      os.path.join(d, 'slowmo.mp4')], start_new_session=True)
    return 0


def calib(d, out):
    ev = np.fromfile(replay(d), EV_DTYPE)
    cnt = np.bincount((ev['y'] & 0x3ff).astype(np.int64) * DW + ev['x'], minlength=DW * DH).reshape(DH, DW)
    fr = np.fromfile(os.path.join(d, 'cam.yuyv'), np.uint8).reshape(-1, CH, CW, 2)[..., 0]
    H = checker_homography(fr.mean(0).astype(np.uint8), cnt.astype(np.float32))
    save_calib(out, H, 'checker %s' % os.path.basename(os.path.abspath(d)))
    return H


def save_calib(out, H, source, **kw):
    cal = load_calib(out)
    cal.update(kw, H=np.asarray(H).tolist(), source=source)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json.dump(cal, open(out, 'w'), indent=1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('rec')
    a.add_argument('dir')
    a.add_argument('-s', type=int, default=1)
    a = sub.add_parser('prep')
    a.add_argument('dir')
    a.add_argument('--calib', default=CALIB)
    a = sub.add_parser('make')
    a.add_argument('dir')
    a.add_argument('--c', type=float)
    a.add_argument('--layout', default='sbs', choices=['sbs', 'fused'])
    a.add_argument('--out')
    a.add_argument('--blur', type=int)
    sub.add_parser('demo')
    a = sub.add_parser('eval')
    a.add_argument('dir')
    a.add_argument('--c', default='0.1,0.15,0.2,0.3,0.4')
    a.add_argument('--T')
    a.add_argument('--blur')
    a = sub.add_parser('compare')
    a.add_argument('dir')
    a.add_argument('--c', type=float)
    a.add_argument('--blur', type=int)
    a = sub.add_parser('calib')
    a.add_argument('dir')
    a.add_argument('--out', default=CALIB)
    a = sub.add_parser('calib-points')
    a.add_argument('out')
    a.add_argument('pairs', nargs='+', help='u,v,x,y: camera (u, v) is DVS (x, y)')
    a.add_argument('--roi', help='cx,cy,rx,ry: fuse only inside this ellipse (camera px)')
    x = ap.parse_args(argv)
    if x.cmd == 'rec':
        rec(x.dir, x.s)
    elif x.cmd == 'prep':
        prep(x.dir, x.calib)
    elif x.cmd == 'make':
        make(x.dir, x.c, x.layout, x.out, blur=x.blur)
    elif x.cmd == 'demo':
        return demo()
    elif x.cmd == 'eval':
        evaluate(x.dir, [float(c) for c in x.c.split(',')], [int(t) for t in x.T.split(',')] if x.T else None,
                 [int(b) for b in x.blur.split(',')] if x.blur else None)
    elif x.cmd == 'compare':
        compare(x.dir, x.c, x.blur)
    elif x.cmd == 'calib':
        calib(x.dir, x.out)
    elif x.cmd == 'calib-points':
        pts = np.array([[float(v) for v in p.split(',')] for p in x.pairs])
        kw = {'roi': [float(v) for v in x.roi.split(',')]} if x.roi else {}
        save_calib(x.out, fit_homography(pts[:, :2], pts[:, 2:]), 'manual', points=pts.tolist(), **kw)
    return 0


if __name__ == '__main__':
    sys.exit(main())
