"""dvsctl's recorder with a fake libdvsview: python3 -m unittest test_dvsctl (Mac venv, or board /var/tmp)."""
import glob
import json
import os
import tempfile
import time
import unittest

import dvsctl
from dvsctl import HIST_COLS, History, Recorder, banner_rows, rec_check, reg_args


class FakeLib:
    """dvs_record: a path opens (creates the file, as gzopen does), None closes and returns 1234 events"""

    def __init__(self):
        self.calls = []

    def dvs_record(self, path):
        self.calls.append(path)
        if path:
            open(path, 'wb').close()
            return 0
        return 1234


def only(d, ext):
    f = glob.glob(os.path.join(d, '*' + ext))
    assert len(f) == 1, f
    return f[0]


def wait_state(r, state, s=5):
    end = time.monotonic() + s
    while r.status()['state'] != state:
        assert time.monotonic() < end, r.status()
        time.sleep(0.01)


class TestRecorder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir, self.fake = self.tmp.name, FakeLib()

    def tearDown(self):
        self.tmp.cleanup()

    def test_rec_check(self):
        self.assertEqual(rec_check('fall', 6, 10**9, False), 200)
        self.assertEqual(rec_check('jump', 6, 10**9, False), 400)
        self.assertEqual(rec_check('fall', 0, 10**9, False), 400)
        self.assertEqual(rec_check('fall', '6', 10**9, False), 400)
        self.assertEqual(rec_check('fall', 6, 10**9, True), 409)
        self.assertEqual(rec_check('fall', 6, 299 * 2**20, False), 507)

    def test_recorder_complete(self):
        r = Recorder(self.fake, self.dir, countdown=0)
        self.assertEqual(r.start('walk', 1), 200)
        self.assertEqual(r.start('walk', 1), 409)          # one at a time
        wait_state(r, 'recording')
        wait_state(r, 'idle')
        j = json.load(open(only(self.dir, '.json')))
        self.assertEqual((j['label'], j['s'], j['events'], j['complete']), ('walk', 1, 1234, True))
        self.assertEqual(r.status()['last'], os.path.basename(only(self.dir, '.gz')))

    def test_recorder_abort(self):  # SIGTERM / exit while recording: the file is closed and marked incomplete
        r = Recorder(self.fake, self.dir, countdown=0)
        r.start('fall', 30)
        wait_state(r, 'recording')
        r.close(False)
        self.assertEqual(self.fake.calls[-1], None)
        self.assertFalse(json.load(open(only(self.dir, '.json')))['complete'])
        self.assertEqual(r.status()['state'], 'idle')

    def test_recorder_abort_countdown(self):    # closed before it started: nothing recorded
        r = Recorder(self.fake, self.dir, countdown=5)
        r.start('fall', 6)
        r.close(False)
        wait_state(r, 'idle')
        time.sleep(0.05)
        self.assertEqual((self.fake.calls, glob.glob(os.path.join(self.dir, '*'))), ([], []))


class TestPage(unittest.TestCase):
    def test_reg_args(self):
        self.assertEqual(reg_args('a=30A5&n=3'), (0x30A5, 3))
        self.assertEqual(reg_args('a=3000'), (0x3000, 1))
        self.assertEqual(reg_args('a=FFC0&n=64'), (0xFFC0, 64))
        for q in ('a=30A5&n=0', 'a=30A5&n=65', 'a=FFFF&n=2', 'a=-1', 'a=zz', 'a=1&n=x'):
            self.assertRaises(ValueError, reg_args, q)
        self.assertRaises(KeyError, reg_args, 'n=3')

    def test_banner_rows(self):
        font = {'h': 2, 'glyphs': {'a': (2, bytes([255, 0, 0, 255])), '?': (1, bytes([255, 255]))}}
        rows = banner_rows(font, [('ab', 255), ('a', 128)], 3)
        self.assertEqual(len(rows), 4)                         # two lines of h rows
        self.assertEqual(rows[0], bytearray([255, 0, 255]))   # 'b' unknown -> '?', clipped at w 3
        self.assertEqual(rows[1], bytearray([0, 255, 255]))
        self.assertEqual(rows[2], bytearray([128, 0, 0]))      # level 128 scales the alpha

    def test_buf_note(self):
        dvsctl.BUFS.update(free=None, wait=None)
        for free, wait in ((6, 0), (4, 2), (7, 1)):
            dvsctl.buf_note(8, free, wait, 1 << 20)
        self.assertEqual((dvsctl.BUFS['free'], dvsctl.BUFS['wait'], dvsctl.BUFS['n']), (4, 2, 8))

    def test_history(self):
        h = History(n=3)
        for t in (1, 2, 3, 4):
            h.add(t, {'fps': 30, 'link': {'short_bufs': t}}, 7)
        rows = h.since(2)
        self.assertEqual([r[0] for r in rows], [3, 4])          # oldest dropped, only after t
        r = dict(zip(HIST_COLS, rows[-1]))
        self.assertEqual((r['fps'], r['crc'], r['short_bufs'], r['scan_us'], r['mipi_fe']), (30, 7, 4, None, None))
        h.add(6, {'fps': 30}, 0, {'mipi_fe': 9, 'vc0_free': 6})
        r = dict(zip(HIST_COLS, h.since(5)[0]))
        self.assertEqual((r['mipi_fe'], r['vc0_free'], r['vc0_wait']), (9, 6, None))
        h.add(5, {'fps': 30}, None)                             # no link counters yet
        self.assertIsNone(dict(zip(HIST_COLS, h.since(4)[0]))['ts_bad'])


if __name__ == '__main__':
    unittest.main()
