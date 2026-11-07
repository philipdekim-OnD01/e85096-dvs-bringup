"""Person action from the S5KRC1S event stream (11.37): 60 ms count frames -> MobileNetV3-small + GRU -> judge.

Events come from libdvsview (dvs_events(), the decoded ring before the HDMI renderer). Every 60 ms of sensor
time becomes a 2 x 120 x 160 count frame (ON, OFF; 6 x 6 px cells), log1p, one model step; the judge turns
the class probabilities into a state and writes a JSONL line when the state changes. The same Binner makes
the training frames on the Mac (frames_of), so training and the board see one definition of a frame.
"""
import datetime
import json

import numpy as np

CLASSES = ['none', 'still', 'walk', 'sit_down', 'stand_up', 'pick_up', 'lie_down', 'fall', 'other']
MOVE = {'walk', 'sit_down', 'stand_up', 'pick_up', 'lie_down', 'other'}
STEP_US = 60000         # one frame, one model step: 16.7 Hz
GAP_US = 3000000        # no event for this long (a sensor restart): start over, GRU state too
HOLD = 3                # steps a candidate must hold before the state changes
FALL_P = 0.6            # fall must be at least this sure to be a candidate
GW, GH = 160, 120
EV = np.dtype([('t', '<u4'), ('x', '<u2'), ('y', '<u2')])     # libdvsview struct ev


def split(ev):
    """struct ev records -> (t uint32 us, x, row, pol 1 = OFF); the filter's EV_SUP bit is dropped"""
    return ev['t'], ev['x'], ev['y'] & 0x3ff, ev['y'] >> 15


def counts(x, y, pol, w=960, h=720):
    """events of one frame -> uint16 [2, GH, GW]"""
    gx = np.asarray(x, np.int64) * GW // w
    gy = np.asarray(y, np.int64) * GH // h
    c = np.bincount((np.asarray(pol, np.int64) * GH + gy) * GW + gx, minlength=2 * GH * GW)
    return np.minimum(c, 65535).astype(np.uint16).reshape(2, GH, GW)


def to_input(c):
    return np.log1p(c.astype(np.float32))[None]


class Binner:
    """Events in time order -> one ('frame', t0_us, counts) per STEP_US bin that has events, on a grid set by
    the first event; ('reset', t_us, None) when the stream had a gap over GAP_US (the bin then is dropped).
    t is the uint32 display clock: steps are unwrapped from the previous event (the clock never goes back)."""

    def __init__(self, w=960, h=720):
        self.w, self.h = w, h
        self.last = None        # uint32 t of the last event
        self.t = 0              # its unwrapped time
        self.origin = 0         # grid start
        self.k = None           # current bin index, None before the first event
        self.cur = []           # (x, y, pol) chunks of the current bin

    def _emit(self, out):
        if self.k is not None and self.cur:
            x, y, p = (np.concatenate(a) for a in zip(*self.cur))
            out.append(('frame', int(self.origin + self.k * STEP_US), counts(x, y, p, self.w, self.h)))
        self.cur = []

    def push(self, t, x, y, pol):
        out = []
        if not len(t):
            return out
        t = np.asarray(t, np.uint32)
        prev = np.array([t[0] if self.last is None else self.last], np.uint32)
        d = (t - np.concatenate((prev, t[:-1]))).astype(np.int64)     # uint32 subtraction: wraps forward
        t64 = self.t + np.cumsum(d)
        cuts = [0] + [int(i) for i in np.flatnonzero(d > GAP_US)] + [len(t)]
        for a, b in zip(cuts[:-1], cuts[1:]):
            if a == b:
                continue
            if self.k is not None and d[a] > GAP_US:
                self.cur, self.k = [], None
                out.append(('reset', int(t64[a]), None))
            if self.k is None:
                self.origin, self.k = int(t64[a]), 0
            ks = (t64[a:b] - self.origin) // STEP_US
            runs = np.concatenate(([0], np.flatnonzero(np.diff(ks)) + 1, [b - a]))
            for i, j in zip(runs[:-1], runs[1:]):
                if ks[i] != self.k:
                    self._emit(out)
                    self.k = int(ks[i])
                self.cur.append((x[a + i:a + j], y[a + i:a + j], pol[a + i:a + j]))
        self.last, self.t = t[-1], int(t64[-1])
        return out


def frames_of(t, x, y, pol, w, h):
    """one clip (t int64 us from 0, as readers.read_events gives it) -> (t0s int64 [N], uint16 [N, 2, GH, GW])"""
    f = [r for r in Binner(w, h).push(np.asarray(t).astype(np.uint32), x, y, pol) if r[0] == 'frame']
    if not f:
        return np.zeros(0, np.int64), np.zeros((0, 2, GH, GW), np.uint16)
    return np.array([r[1] for r in f], np.int64), np.stack([r[2] for r in f])


class Judge:
    """Class probabilities per step -> a state change ({'event', 'state', 'prev', 'p'}) or None."""

    def __init__(self):
        self.state, self.cand, self.n = 'none', None, 0

    def step(self, probs):
        k = int(np.argmax(probs))
        c, p = CLASSES[k], float(probs[k])
        if c == 'fall' and p < FALL_P:
            c = None
        self.n = self.n + 1 if c == self.cand else 1
        self.cand = c
        if c is None or self.n < HOLD or c == self.state:
            return None
        prev, self.state = self.state, c
        event = 'fall' if c == 'fall' else 'appear' if prev == 'none' else 'move' if c in MOVE else c
        return {'event': event, 'state': c, 'prev': prev, 'p': p}


def line(rec, now_ns):
    """one JSONL record: t_us and time (UTC) of now_ns, then the judge's change"""
    us = now_ns // 1000
    time = datetime.datetime.fromtimestamp(us // 1000000, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    return json.dumps({'t_us': us, 'time': '%s.%06dZ' % (time, us % 1000000), 'event': rec['event'],
                       'state': rec['state'], 'prev': rec['prev'], 'p': round(rec['p'], 2)})
