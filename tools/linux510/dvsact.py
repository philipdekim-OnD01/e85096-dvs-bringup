"""Person action from the S5KRC1S event stream (11.37): 60 ms count frames -> MobileNetV3-small + GRU -> judge.

Events come from libdvsview's decoded ring (before the HDMI renderer). Every 60 ms of sensor time becomes a
2 x 120 x 160 count frame (ON, OFF; 6 x 6 px cells), log1p, one model step; the judge turns the class
probabilities into a state and writes a JSONL line when the state changes. Binner makes the training frames on
the Mac (frames_of); on the board libdvsview bins the ring by the same rules in C (CBinner, dvs_act_bin, 11.103),
and test_dvsact checks the two give the same frames.

On the board start() runs it in the capture process (ov5647-cap.py --act FILE --act-model PATH), all in C since
11.112 (libdvsview's dvsai.c, onnxruntime's C API): threads "dvs-bin" (frames) and "dvs-act" (the model, the judge)
on CPU4-6 at the top clock. Judge, observe and line here stay the reference (the Mac's evaluate.py, test_dvsai).
"""
import ctypes
import datetime
import json
import os
import sys

import numpy as np

CLASSES = ['none', 'still', 'walk', 'sit_down', 'stand_up', 'pick_up', 'lie_down', 'fall', 'other']
MOVE = {'walk', 'sit_down', 'stand_up', 'pick_up', 'lie_down', 'other'}
STEP_US = 60000         # one frame, one model step: 16.7 Hz
GAP_US = 3000000        # no event for this long (a sensor restart): start over, GRU state too
HOLD = 3                # steps a candidate must hold before the state changes
FALL_P = 0.6            # fall must be at least this sure to be a candidate
CLASSES_FILE = 'classes'    # beside the model, optional: the classes judged (e.g. "none sit_down stand_up")
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
    t is the uint32 display clock: steps are unwrapped from the previous event (the clock never goes back).
    push(t, x, y, pol) counts with numpy (training, any w x h)."""

    def __init__(self, w=960, h=720):
        self.gx = (np.arange(w) * GW // w).astype(np.int32)        # cell of a column, of a row (x GW)
        self.gy = (np.arange(h) * GH // h * GW).astype(np.int32)
        self.acc = np.zeros(2 * GH * GW, np.uint32)                # counts of the current bin
        self.any = False
        self.last = None        # uint32 t of the last event (an int)
        self.tu = 0             # its unwrapped time
        self.start = None       # uint32 start of the current bin, None before the first event and after a gap
        self.t0 = 0             # its unwrapped time

    def _emit(self, out):
        if self.any:
            out.append(('frame', self.t0, np.minimum(self.acc, 65535).astype(np.uint16).reshape(2, GH, GW)))
        self.acc[:] = 0
        self.any = False

    def push(self, t, x, y, pol):
        cell = self.gx[x] + self.gy[y] + np.asarray(pol, np.int32) * (GH * GW)
        return self._push(t, lambda i, j: np.add(self.acc, np.bincount(cell[i:j], minlength=len(self.acc)),
                                                 out=self.acc, casting='unsafe'))

    def _push(self, t, add):
        """bin edges by searchsorted on the sorted clock; add(i, j) counts events i..j-1 into acc"""
        out = []
        if not len(t):
            return out
        t = np.asarray(t, np.uint32)
        gaps = [] if (int(t[-1]) - int(t[0])) & 0xffffffff <= GAP_US else \
            (np.flatnonzero(np.diff(t) > GAP_US) + 1).tolist()         # uint32 differences wrap forward
        first_gap = self.last is not None and (int(t[0]) - self.last) & 0xffffffff > GAP_US
        segs = [0] + gaps + [len(t)]
        for s, (a, b) in enumerate(zip(segs[:-1], segs[1:])):
            ta = int(t[a])
            tua = 0 if self.last is None else self.tu + ((ta - self.last) & 0xffffffff)
            if (s or first_gap) and self.start is not None:
                self.acc[:] = 0
                self.any, self.start = False, None
                out.append(('reset', tua, None))
            if self.start is None:
                self.start, self.t0 = ta, tua
            rel = t[a:b] - np.uint32(self.start)                     # us after the current bin's start
            off, i = 0, 0
            while i < b - a:
                k = max(0, (int(rel[i]) - off) // STEP_US)
                if k:                                                  # past the current bin: the empty ones skipped
                    self._emit(out)
                    off += k * STEP_US
                    self.start, self.t0 = (self.start + k * STEP_US) & 0xffffffff, self.t0 + k * STEP_US
                j = max(i + 1, int(np.searchsorted(rel, np.uint32(off + STEP_US))))   # a Python int key: a cast copy
                add(a + i, a + j)
                self.any = True
                i = j
            self.last, self.tu = int(t[b - 1]), tua + ((int(t[b - 1]) - ta) & 0xffffffff)
        return out


class Bin(ctypes.Structure):
    _fields_ = [('cur', ctypes.c_uint64), ('t0', ctypes.c_int64), ('tu', ctypes.c_int64), ('out', ctypes.c_int64),
                ('start', ctypes.c_uint32), ('last', ctypes.c_uint32), ('flags', ctypes.c_uint32),
                ('step', ctypes.c_uint32), ('gap', ctypes.c_uint32), ('pad', ctypes.c_uint32)]   # struct dvs_binner


class CBinner:
    """Binner's records from libdvsview, 960 x 720: run(state, acc) is dvs_act_bin (the decoder's ring; it starts at
    the newest event) or dvs_bin_run on an array (the test). ctypes drops the GIL for the call. In Python, the
    copy, search and loop per 64 k events took a whole core with fast motion (11.103)."""

    def __init__(self, run):
        self.run, self.s = run, Bin(cur=2**64 - 1, step=STEP_US, gap=GAP_US)
        self.acc = np.zeros(2 * GH * GW, np.uint32)

    def records(self):
        """what the events so far completed; the next call to run() zeroes acc after a frame or a reset"""
        while True:
            r = self.run(ctypes.byref(self.s), self.acc.ctypes.data)
            if not r:
                return
            yield ('frame', self.s.out, np.minimum(self.acc, 65535).astype(np.uint16).reshape(2, GH, GW)) \
                if r == 1 else ('reset', self.s.out, None)


def frames_of(t, x, y, pol, w, h):
    """one clip (t int64 us from 0, as readers.read_events gives it) -> (t0s int64 [N], uint16 [N, 2, GH, GW])"""
    f = [r for r in Binner(w, h).push(np.asarray(t).astype(np.uint32), x, y, pol) if r[0] == 'frame']
    if not f:
        return np.zeros(0, np.int64), np.zeros((0, 2, GH, GW), np.uint16)
    return np.array([r[1] for r in f], np.int64), np.stack([r[2] for r in f])


class Judge:
    """Class probabilities per step -> a state change ({'event', 'state', 'prev', 'p'}) or None. classes: judge
    only these (11.45: none, sit_down, stand_up for the sit / stand test), the others' probability left out."""

    def __init__(self, classes=None):
        self.state, self.cand, self.n = 'none', None, 0
        self.keep = None if classes is None else np.isin(CLASSES, classes)

    def restrict(self, probs):
        """probs over the judged classes only, summing to 1 (all classes: as they are)"""
        if self.keep is None:
            return probs
        p = np.where(self.keep, probs, 0)
        return p / p.sum()

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


RECENT = 10
# the classifier now, for the page (dvsctl GET /act): on, the judge's state, this step's top class and p, the
# newest RECENT records (newest first)
STATUS = {'on': False, 'state': 'none', 'top': None, 'p': 0.0, 'recent': []}


def observe(judge, probs, now_ns):
    """one step's probabilities -> the judge and STATUS; the JSONL line of a state change, else None"""
    probs = judge.restrict(probs)
    rec = judge.step(probs)
    k = int(np.argmax(probs))
    STATUS.update(state=judge.state, top=CLASSES[k], p=round(float(probs[k]), 2))
    if rec is None:
        return None
    s = line(rec, now_ns)
    STATUS['recent'] = [json.loads(s)] + STATUS['recent'][:RECENT - 1]
    return s


def _off(why):
    STATUS['on'] = False
    print('[dvsact] off: %s' % why, file=sys.stderr, flush=True)


def ort_lib():
    """the onnxruntime wheel's libonnxruntime.so (its C API runs the models in libdvsview's dvsai.c, 11.112), found
    without importing the package; None without the wheel"""
    import glob
    import importlib.util
    spec = importlib.util.find_spec('onnxruntime')
    f = spec and sorted(glob.glob(os.path.join(os.path.dirname(spec.origin), 'capi', 'libonnxruntime.so*')))
    return f[0] if f else None


def cpu_mask(cpus):
    return sum(1 << c for c in cpus)


def start(lib, out_path, model_path, cpus=frozenset({4, 5, 6})):
    """Classify in libdvsview's C threads dvs-bin and dvs-act (dvsai.c, 11.112; until then the same in Python threads
    here), one JSON line per state change appended to out_path, the state at GET /act; None (and why on stderr)
    without onnxruntime or the model: the live view goes on without the classifier."""
    ort = ort_lib()
    if not ort:
        return _off('no onnxruntime')
    if not os.path.isfile(model_path):
        return _off('no model %s' % model_path)
    err = ctypes.create_string_buffer(512)
    lib.dvs_act_start.argtypes = [ctypes.c_char_p] * 3 + [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_int]
    if lib.dvs_act_start(ort.encode(), model_path.encode(), out_path.encode(), cpu_mask(cpus), err, len(err)):
        return _off(err.value.decode(errors='replace'))
    return True
