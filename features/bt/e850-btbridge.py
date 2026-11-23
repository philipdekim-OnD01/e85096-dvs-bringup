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
import subprocess
import sys
import threading
import time

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


def cmd_status(pkt):
    """(opcode, status) of a Command Complete or Command Status event, else None"""
    if pkt[:2] == b'\x04\x0e' and len(pkt) >= 7:
        return pkt[4] | pkt[5] << 8, pkt[6]
    if pkt[:2] == b'\x04\x0f' and len(pkt) >= 7:
        return pkt[5] | pkt[6] << 8, pkt[3]
    return None


# Stuck discovery (linux510 docs 11.96): when a command of the kernel's stop-discovery request fails, discov_update()
# leaves the state at STOPPING; only a Remote Name Request Complete moves it on (hci_check_pending_name), so Start
# Discovery answers Busy until a power cycle. This fake one (status 0x02, address 0) does that and nothing else.
NUDGE = bytes([0x04, 0x07, 255, 0x02]) + bytes(6 + 248)
STUCK_S = 30            # bluetoothd says discovering but no discovery command for this long; healthy: <= ~11 s
# Inquiry, Inquiry Cancel, Remote Name Request (Cancel), LE (Extended) Scan Parameters and Enable. Only these: blueman
# polls Read RSSI and Link Quality every second while a device is connected.
DISC_OPS = {0x0401, 0x0402, 0x0419, 0x041a, 0x200b, 0x200c, 0x2041, 0x2042}


def disc_cmd(pkt):
    """an HCI command packet of the kernel's discovery"""
    return pkt[0] == 0x01 and len(pkt) >= 3 and pkt[1] | pkt[2] << 8 in DISC_OPS


def watch_action(idle, discovering, nudged):
    """what to do about a discovery that may be stuck: None, 'nudge', then 'power' (adapter off and on)"""
    if idle < STUCK_S or not discovering:
        return None
    return 'power' if nudged else 'nudge'


def discovering(idx):
    try:
        r = subprocess.run(['busctl', 'get-property', 'org.bluez', '/org/bluez/hci%d' % idx, 'org.bluez.Adapter1',
                            'Discovering'], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() == 'b true'
    except (OSError, subprocess.TimeoutExpired):
        return False


def watchdog(vh, idx, last_disc):
    """a stuck discovery: nudge the kernel, then power cycle (own thread: busctl must not hold up the audio)"""
    nudged_at = None
    while True:
        time.sleep(STUCK_S / 2)
        idle = time.monotonic() - last_disc[0]
        nudged = nudged_at is not None and last_disc[0] < nudged_at
        act = watch_action(idle, idle >= STUCK_S and discovering(idx), nudged)
        if act == 'nudge':
            print('discovery stuck (no discovery command for %d s while discovering): fake Remote Name Request '
                  'Complete' % idle, flush=True)
            os.write(vh, NUDGE)              # one packet a write: safe beside the forwarding thread's writes
            nudged_at = time.monotonic()
        elif act == 'power':
            print('discovery still stuck: hci%d off and on' % idx, flush=True)
            subprocess.run(['sh', '-c', 'bluetoothctl power off; sleep 1; bluetoothctl power on'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            nudged_at = None
            last_disc[0] = time.monotonic()


def run():
    vh = os.open(VHCI, os.O_RDWR)
    os.write(vh, b'\xff\x00')                # create the primary controller now, not after vhci's 1 s
    r = os.read(vh, 16)                      # ff 00 <index lo> <index hi>
    assert r[:2] == b'\xff\x00', r
    h4 = os.open(H4_DEV, os.O_RDWR)          # starts the BT service in the WLBT firmware;
    # it may speak first, so hci%d must exist before this
    debug = os.environ.get('E850_BT_DEBUG')
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    idx = r[2] | r[3] << 8
    print('bridge %s <-> %s = hci%d' % (H4_DEV, VHCI, idx), flush=True)
    rest = b''
    p = select.poll()
    p.register(h4, select.POLLIN)
    p.register(vh, select.POLLIN)
    last_disc = [time.monotonic()]          # the time of the kernel's last discovery command
    threading.Thread(target=watchdog, args=(vh, idx, last_disc), daemon=True).start()
    while True:
        for fd, ev in p.poll():
            if ev & (select.POLLERR | select.POLLHUP | select.POLLNVAL):
                sys.exit('%s: poll event 0x%x' % (H4_DEV if fd == h4 else VHCI, ev))
            if fd == vh:
                pkt = os.read(vh, 4096)
                if pkt and pkt[0] != 0xff:   # 0xff: vhci's own vendor reply (index), not for the chip
                    if debug:
                        print('>', pkt[:16].hex(), flush=True)
                    if disc_cmd(pkt):
                        last_disc[0] = time.monotonic()
                    os.write(h4, pkt)
            else:
                data = os.read(h4, 4096)
                if not data:
                    sys.exit(H4_DEV + ': EOF')
                pkts, rest = cut(rest + data)
                for pkt in pkts:
                    if debug:
                        print('<', pkt[:16].hex(), flush=True)
                    st = cmd_status(pkt)
                    if st and st[1]:
                        print('hci command 0x%04x: status 0x%02x' % st, flush=True)
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
    assert cmd_status(ev) == (0x0c03, 0)
    assert cmd_status(bytes([0x04, 0x0f, 0x04, 0x0c, 0x01, 0x1a, 0x04])) == (0x041a, 0x0c)   # Command Status
    assert cmd_status(acl) is None and cmd_status(NUDGE) is None
    assert cut(NUDGE) == ([NUDGE], b'') and NUDGE[1] == 0x07 and NUDGE[3] != 0
    assert disc_cmd(bytes([0x01, 0x01, 0x04, 0x05, 0x33, 0x8b, 0x9e, 0x08, 0x00]))           # Inquiry
    assert disc_cmd(bytes([0x01, 0x42, 0x20, 0x06, 0, 0, 0, 0, 0, 0]))                   # LE ext scan enable
    assert not disc_cmd(bytes([0x01, 0x05, 0x14, 0x02, 0x01, 0x00]))                     # Read RSSI (blueman, 1/s)
    assert not disc_cmd(ev)
    assert watch_action(29, True, False) is None and watch_action(60, False, False) is None
    assert watch_action(30, True, False) == 'nudge' and watch_action(45, True, True) == 'power'
    print('selftest ok')


if __name__ == '__main__':
    selftest() if sys.argv[1:] == ['--selftest'] else run()
