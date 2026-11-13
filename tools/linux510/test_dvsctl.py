"""dvsctl's recorder with a fake libdvsview: python3 -m unittest test_dvsctl (Mac venv, or board /var/tmp)."""
import glob
import json
import os
import tempfile
import time
import unittest

from dvsctl import Recorder, rec_check


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


if __name__ == '__main__':
    unittest.main()
