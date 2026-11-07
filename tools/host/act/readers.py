"""Event readers for the action classifier's training data (11.37): one Events tuple per clip, upright, t from 0.

  .aedat   PAF (Miao 2019): jAER AER-DAT2.0 from a DAVIS346, '#' header lines, then big-endian int32 address,
           int32 timestamp (us). Decoded as PAF's snn.py/frequency.py show it upright: x = 345 - x, y = 259 - y.
  .aedat4  DailyDVS-200: DVXplorer Lite 320 x 240 through the aedat package.
  .bin     dvsview.c --replay of a board recording: struct ev (u32 display clock us, u16 x, u16 y | sup << 14 |
           off << 15), 960 x 720.
"""
import collections

import numpy as np

Events = collections.namedtuple('Events', 't x y p w h')   # t int64 us from 0, x/y int16, p uint8 0 = ON 1 = OFF


def _from0(t):
    """uint32 display clock -> int64 us from the first event (the clock wraps every 71.6 min, never steps back)"""
    t = np.asarray(t, np.uint32)
    if not len(t):
        return np.zeros(0, np.int64)
    return np.concatenate(([0], np.cumsum(np.diff(t).astype(np.int64))))


def _aedat2(path):
    raw = open(path, 'rb').read()
    p = 0
    while raw.startswith(b'#', p):
        p = raw.index(b'\n', p) + 1
    n = (len(raw) - p) // 8
    w = np.frombuffer(raw, '>u4', 2 * n, p).reshape(n, 2)
    a, t = w[:, 0], w[:, 1]
    dvs = (a & 0x80000000) == 0                                # the rest are APS / IMU / special events
    a, t = a[dvs], t[dvs].astype(np.int64)
    x = 345 - ((a & 0x003FF000) >> 12).astype(np.int64)
    y = 259 - ((a & 0x7FC00000) >> 22).astype(np.int64)
    keep = (x >= 0) & (x < 346) & (y >= 0) & (y < 260)
    t = t[keep]
    return Events(t - t[0], x[keep].astype(np.int16), y[keep].astype(np.int16),
                  ((a[keep] >> 11) & 1).astype(np.uint8), 346, 260)


def _aedat4(path):
    import aedat

    d = aedat.Decoder(path)
    size = next((s['width'], s['height']) for s in d.id_to_stream().values() if s['type'] == 'events')
    ev = [p['events'] for p in d if 'events' in p]
    ev = np.concatenate(ev) if ev else np.zeros(0, [('t', '<u8'), ('x', '<u2'), ('y', '<u2'), ('on', '?')])
    t = ev['t'].astype(np.int64)
    return Events(t - (t[0] if len(t) else 0), ev['x'].astype(np.int16), ev['y'].astype(np.int16),
                  (~ev['on']).astype(np.uint8), *size)


def _replay(path):
    ev = np.fromfile(path, [('t', '<u4'), ('x', '<u2'), ('y', '<u2')])
    return Events(_from0(ev['t']), ev['x'].astype(np.int16), (ev['y'] & 0x3ff).astype(np.int16),
                  (ev['y'] >> 15).astype(np.uint8), 960, 720)


def read_events(path):
    for ext, f in (('.aedat4', _aedat4), ('.aedat', _aedat2), ('.bin', _replay)):
        if path.endswith(ext):
            return f(path)
    raise ValueError('unknown event file: ' + path)


if __name__ == '__main__':
    import sys

    for f in sys.argv[1:]:
        e = read_events(f)
        print('%s: %d events, %.2f s, %dx%d, x %d..%d, y %d..%d, OFF %.0f%%' % (
            f, len(e.t), e.t[-1] / 1e6 if len(e.t) else 0, e.w, e.h, e.x.min(), e.x.max(), e.y.min(), e.y.max(),
            100 * e.p.mean()))
