#!/usr/bin/env python3
"""e850-bt.py: BlueZ on the WLBT Bluetooth core (docs 11.42).

scsc_bt.ko gives the H4 byte stream Android's BT HAL used (/dev/scsc_h4_0; opening it starts the BT service in the
WLBT firmware, which loads bt.hcf itself). Its BlueZ glue (scsc_bluez.c) does not build on this 5.10 tree, so this
bridges the stream to BlueZ's virtual HCI (/dev/vhci, modules/hci-vhci): hci0 exists while this runs.
/dev/vhci takes one whole packet per write and gives one per read; the scsc stream is split here. It refuses
packets (ENODEV) until hci0 exists, so hci0 is created before the controller is started.
Run by e850-bt.service (modprobe scsc_bt hci_vhci first).
"""
import os
import select

SCSC, VHCI = '/dev/scsc_h4_0', '/dev/vhci'
# H4 type -> (header bytes after the type, payload length from the header)
HDR = {0x02: (4, lambda h: h[2] | h[3] << 8),               # ACL
       0x03: (3, lambda h: h[2]),                           # SCO
       0x04: (2, lambda h: h[1]),                           # event
       0x05: (4, lambda h: (h[2] | h[3] << 8) & 0x3fff)}    # ISO
VENDOR = 0xff           # vhci's own packet: create a controller / created


def split(buf):
    """bytes -> (whole H4 packets, the incomplete rest); ValueError on an unknown packet type"""
    pkts, i = [], 0
    while i < len(buf):
        if buf[i] not in HDR:
            raise ValueError('H4 type 0x%02x at byte %d' % (buf[i], i))
        n, length = HDR[buf[i]]
        if len(buf) - i - 1 < n:
            break
        end = i + 1 + n + length(buf[i + 1:i + 1 + n])
        if end > len(buf):
            break
        pkts.append(bytes(buf[i:end]))
        i = end
    return pkts, bytes(buf[i:])


def write_all(fd, b):
    while b:
        b = b[os.write(fd, b):]


def create(vhci):
    """ask vhci for a primary controller now (not after its 1 s open timeout) -> its hci index"""
    os.write(vhci, bytes([VENDOR, 0x00]))
    r = os.read(vhci, 16)
    if len(r) < 4 or r[0] != VENDOR:
        raise SystemExit('vhci: no controller (%r)' % r)
    return r[2] | r[3] << 8


def main():
    vhci = os.open(VHCI, os.O_RDWR)
    print('hci%d' % create(vhci), flush=True)  # BlueZ's HCI Reset waits in vhci until the loop reads it
    scsc = os.open(SCSC, os.O_RDWR)     # starts the BT service in the WLBT firmware
    rest = b''
    while True:
        for fd in select.select([scsc, vhci], [], [])[0]:
            b = os.read(fd, 4096)
            if not b:
                raise SystemExit('%s closed' % (SCSC if fd == scsc else VHCI))
            if fd == vhci:
                write_all(scsc, b)
            else:
                pkts, rest = split(rest + b)
                for p in pkts:
                    write_all(vhci, p)


if __name__ == '__main__':
    main()
