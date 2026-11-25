#!/usr/bin/env python3
"""viewstat.py SECONDS [MPV_SOCKET]: how often the DVS window and the USB camera window ("USB 카메라") change on the
screen, counted the same way on the E850 and the Jetson (docs 11.115): X Damage events on each window (level
NonEmpty, subtracted at once: one event per drawn frame). With MPV_SOCKET (mpv --input-ipc-server=MPV_SOCKET
--rebase-start-time=no) also the camera frame's age when mpv takes it for the screen: CLOCK_MONOTONIC now - time-pos,
time-pos being the V4L2 buffer timestamp (uvcvideo: the frame's first USB packet); sampled at mpv's property updates
(~15/s, not every frame). Also mpv's drop counters over the run. Needs DISPLAY (and XAUTHORITY); no root, no python3-xlib.
"""
import ctypes, json, os, re, select, socket, subprocess, sys, time

secs = float(sys.argv[1])
names = ('DVS', 'USB 카메라')
x11, xd = ctypes.CDLL('libX11.so.6'), ctypes.CDLL('libXdamage.so.1')
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
for f in (x11.XConnectionNumber, x11.XPending, x11.XFlush):
    f.argtypes = [ctypes.c_void_p]
x11.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
xd.XDamageQueryExtension.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
xd.XDamageCreate.restype = ctypes.c_ulong
xd.XDamageCreate.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
xd.XDamageSubtract.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]

# the client window of each name (GNOME's frame window carries the same name: skip mutter-x11-frames)
wins = {}
for ln in subprocess.run(['xwininfo', '-root', '-tree'], capture_output=True, text=True, check=True).stdout.splitlines():
    m = re.match(r'\s*(0x[0-9a-f]+) "(.*)": \((.*?)\)\s+(\d+x\d+)', ln)
    if m and m[2] in names and 'mutter-x11-frames' not in m[3]:
        wins[m[2]] = (int(m[1], 16), m[4])
dpy = x11.XOpenDisplay(None)
assert dpy, 'no X display (DISPLAY, XAUTHORITY)'
base, err = ctypes.c_int(), ctypes.c_int()
assert xd.XDamageQueryExtension(dpy, ctypes.byref(base), ctypes.byref(err)), 'no DAMAGE extension'
dmg = {xd.XDamageCreate(dpy, w, 3): n for n, (w, _) in wins.items()}     # 3: XDamageReportNonEmpty
count = dict.fromkeys(dmg, 0)
xfd = x11.XConnectionNumber(dpy)
ev = ctypes.create_string_buffer(192)                                    # sizeof(XEvent) on LP64

mpv = None
if len(sys.argv) > 2:
    sk = socket.socket(socket.AF_UNIX)
    sk.connect(sys.argv[2])
    mpv = sk.makefile('rwb', 0)
    keys = ('frame-drop-count', 'decoder-frame-drop-count', 'vo-delayed-frame-count')

    def get(p):
        mpv.write(json.dumps({'command': ['get_property', p], 'request_id': 9}).encode() + b'\n')
        while True:
            r = json.loads(mpv.readline())
            if r.get('request_id') == 9:
                return r.get('data')
    drops0 = [get(k) for k in keys]
    mpv.write(b'{"command":["observe_property",1,"time-pos"]}\n')
ages, last = [], None

t0 = time.monotonic()
while time.monotonic() - t0 < secs:
    x11.XFlush(dpy)
    rd = select.select([xfd] + ([sk] if mpv else []), [], [], 0.2)[0]
    while x11.XPending(dpy):
        x11.XNextEvent(dpy, ev)
        if int.from_bytes(ev.raw[:4], 'little') == base.value:           # XDamageNotify: damage at offset 40
            d = int.from_bytes(ev.raw[40:48], 'little')
            if d in count:
                count[d] += 1
                xd.XDamageSubtract(dpy, d, 0, 0)
    if mpv and sk in rd:
        m = json.loads(mpv.readline())
        if m.get('event') == 'property-change' and m.get('data') is not None and m['data'] != last:
            last = m['data']
            ages.append(time.clock_gettime(time.CLOCK_MONOTONIC) - last)
dt = time.monotonic() - t0
for d, n in dmg.items():
    print('%s %s: %.1f updates/s' % (n, wins[n][1], count[d] / dt))
if mpv:
    ages.sort()
    assert ages and 0 < ages[0] < 2, 'time-pos is not CLOCK_MONOTONIC (--rebase-start-time=no?): %r' % ages[:3]
    q = lambda f: ages[min(len(ages) - 1, int(f * len(ages)))] * 1e3
    print('camera frame age at mpv: median %.1f, p95 %.1f, min %.1f, max %.1f ms (%d samples)' %
          (q(.5), q(.95), ages[0] * 1e3, ages[-1] * 1e3, len(ages)))
    print('mpv: estimated-vf-fps %s, drops %s' % (get('estimated-vf-fps'),
          {k: get(k) - a for k, a in zip(keys, drops0)}))
