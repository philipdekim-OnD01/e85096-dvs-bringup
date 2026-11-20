#!/usr/bin/env python3
"""e850-btbridge: Samsung SCSC Bluetooth (/dev/scsc_h4_0) -> BlueZ hci0 via /dev/vhci (linux510 docs 11.78).

The vendor scsc_bt.ko speaks H4 on a char device (Android's HAL opens it); the in-tree BlueZ glue
(scsc_bluez.c) does not build any more. hci_vhci makes a BlueZ controller whose HCI traffic goes to
this process, so we only copy packets: vhci gives and takes one H4 packet per read/write, the scsc
device is a byte stream, so packets from it are cut by their H4 headers.

  e850-btbridge.py            run (root; modprobe scsc_bt hci_vhci first)
  e850-btbridge.py --selftest
"""
import os
import select
import signal
import sys

H4_DEV = '/dev/scsc_h4_0'
VHCI = '/dev/vhci'
# H4 type -> (header length, offset of length field, size of length field)
HDR = {0x01: (3, 2, 1), 0x02: (4, 2, 2), 0x03: (3, 2, 1), 0x04: (2, 1, 1), 0x05: (4, 2, 2)}


def cut(buf):
    """split a byte stream into whole H4 packets; returns (packets, rest)"""
    out = []
    while buf:
        t = buf[0]
        if t not in HDR:
            raise ValueError('bad H4 type 0x%02x' % t)
        hl, lo, ls = HDR[t]
        if len(buf) < 1 + hl:
            break
        n = int.from_bytes(buf[1 + lo:1 + lo + ls], 'little')
        if t == 0x05:
            n &= 0x3fff
        end = 1 + hl + n
        if len(buf) < end:
            break
        out.append(bytes(buf[:end]))
        buf = buf[end:]
    return out, buf


def run():
    vh = os.open(VHCI, os.O_RDWR)
    os.write(vh, b'\xff\x00')                # create the primary controller now, not after vhci's 1 s
    r = os.read(vh, 16)                      # ff 00 <index lo> <index hi>
    assert r[:2] == b'\xff\x00', r
    h4 = os.open(H4_DEV, os.O_RDWR)          # starts the BT service in the WLBT firmware;
    # it may speak first, so hci%d must exist before this
    debug = os.environ.get('E850_BT_DEBUG')
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    print('bridge %s <-> %s = hci%d' % (H4_DEV, VHCI, r[2] | r[3] << 8), flush=True)
    rest = b''
    p = select.poll()
    p.register(h4, select.POLLIN)
    p.register(vh, select.POLLIN)
    while True:
        for fd, ev in p.poll():
            if ev & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                sys.exit('%s: poll event 0x%x' % (H4_DEV if fd == h4 else VHCI, ev))
            if fd == vh:
                pkt = os.read(vh, 4096)
                if pkt and pkt[0] != 0xff:   # 0xff: vhci's own vendor reply (index), not for the chip
                    if debug:
                        print('>', pkt[:16].hex(), flush=True)
                    os.write(h4, pkt)
            else:
                data = os.read(h4, 4096)
                if not data:
                    sys.exit(H4_DEV + ': EOF')
                pkts, rest = cut(rest + data)
                for pkt in pkts:
                    if debug:
                        print('<', pkt[:16].hex(), flush=True)
                    os.write(vh, pkt)


def selftest():
    ev = bytes([0x04, 0x0e, 0x04, 0x01, 0x03, 0x0c, 0x00])          # Command Complete (Reset)
    acl = bytes([0x02, 0x01, 0x20, 0x03, 0x00, 0xaa, 0xbb, 0xcc])
    pk, rest = cut(ev + acl[:5])
    assert pk == [ev] and rest == acl[:5], (pk, rest)
    pk, rest = cut(rest + acl[5:])
    assert pk == [acl] and rest == b''
    try:
        cut(b'\x09\x00')
        assert False
    except ValueError:
        pass
    print('selftest ok')


if __name__ == '__main__':
    selftest() if sys.argv[1:] == ['--selftest'] else run()
