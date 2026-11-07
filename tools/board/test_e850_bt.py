"""e850-bt.py: H4 packet splitting: python3 -m unittest test_e850_bt (from tools/board)."""
import importlib.util
import os
import socket
import unittest

spec = importlib.util.spec_from_file_location('e850_bt', os.path.join(os.path.dirname(__file__), 'e850-bt.py'))
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

EVT = bytes([0x04, 0x0e, 0x04, 0x01, 0x03, 0x0c, 0x00])            # Command Complete (HCI Reset), 4 bytes
ACL = bytes([0x02, 0x01, 0x20, 0x03, 0x00]) + b'abc'                # handle 0x001, 3 bytes
SCO = bytes([0x03, 0x01, 0x00, 0x02]) + b'xy'
ISO = bytes([0x05, 0x01, 0x00, 0x02, 0x40]) + b'pq'                 # length 0x4002 & 0x3fff = 2


class TestSplit(unittest.TestCase):
    def test_whole_and_partial(self):   # a read can end inside a packet: the rest waits for the next read
        pkts, rest = bt.split(EVT + ACL + SCO + ISO + EVT[:3])
        self.assertEqual((pkts, rest), ([EVT, ACL, SCO, ISO], EVT[:3]))
        self.assertEqual(bt.split(rest + EVT[3:]), ([EVT], b''))

    def test_header_cut(self):          # not even the length byte yet
        self.assertEqual(bt.split(ACL[:2]), ([], ACL[:2]))

    def test_unknown_type(self):        # a lost byte: the stream cannot be resynchronised
        with self.assertRaises(ValueError):
            bt.split(b'\x07\x00\x00')


class TestCreate(unittest.TestCase):
    def test_create(self):              # hci0 before the controller talks: vhci refuses packets until it exists
        a, b = socket.socketpair()
        b.send(bytes([0xff, 0x00, 0x01, 0x00]))     # vhci's answer: created, index 1
        self.assertEqual(bt.create(a.fileno()), 1)
        self.assertEqual(b.recv(16), bytes([0xff, 0x00]))   # the request: a primary controller


if __name__ == '__main__':
    unittest.main()
