#!/usr/bin/env python3
"""dvsx11: libdvsview's page in an X window instead of /dev/fb0 (linux510 docs 11.97).

ov5647-cap.py --x11 starts the decoder with dvs_start(NULL): it draws 960x720 XRGB8888 into memory and this thread
puts that page into a window fps times a second (plain XPutImage: the kernel has no SysV IPC, so no MIT-SHM).
Closing the window, q or Esc sends SIGINT to the process: the capture loop then stops the stream as on Ctrl+C.

  dvsx11.py --selftest        draws a test page into a window and reads it back (needs $DISPLAY, e.g. Xvfb)
"""
import ctypes
import ctypes.util
import os
import signal
import sys
import threading
import time

X = ctypes.CDLL(ctypes.util.find_library('X11') or 'libX11.so.6')
P, U, I = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int
for name, res, args in (
        ('XOpenDisplay', P, [ctypes.c_char_p]), ('XDefaultScreen', I, [P]), ('XRootWindow', U, [P, I]),
        ('XDefaultVisual', P, [P, I]), ('XDefaultDepth', I, [P, I]), ('XDefaultGC', P, [P, I]),
        ('XBlackPixel', U, [P, I]), ('XCreateSimpleWindow', U, [P, U, I, I, ctypes.c_uint, ctypes.c_uint,
                                                                 ctypes.c_uint, U, U]),
        ('XStoreName', I, [P, U, ctypes.c_char_p]), ('XSelectInput', I, [P, U, ctypes.c_long]),
        ('XInternAtom', U, [P, ctypes.c_char_p, I]), ('XSetWMProtocols', I, [P, U, ctypes.POINTER(U), I]),
        ('XMapWindow', I, [P, U]), ('XPending', I, [P]), ('XNextEvent', I, [P, P]),
        ('XLookupKeysym', U, [P, I]), ('XFlush', I, [P]), ('XSync', I, [P, I]),
        ('XCreateImage', P, [P, P, ctypes.c_uint, I, I, P, ctypes.c_uint, ctypes.c_uint, I, I]),
        ('XPutImage', I, [P, U, P, P, I, I, I, I, ctypes.c_uint, ctypes.c_uint]),
        ('XGetImage', P, [P, U, I, I, ctypes.c_uint, ctypes.c_uint, U, I]),
        ('XGetPixel', U, [P, I, I]), ('XSetIOErrorHandler', P, [P])):
    getattr(X, name).restype, getattr(X, name).argtypes = res, args

KEY_PRESS, CLIENT_MESSAGE, Z_PIXMAP = 2, 33, 2
EXPOSURE, KEY_PRESS_MASK = 1 << 15, 1 << 0
# a dead X server: Xlib exits the process after the handler returns, so it never returns; the capture loop stops
# the stream on the SIGINT instead (a stream left on hangs the next start, 11.53)
_io_error = ctypes.CFUNCTYPE(I, P)(lambda dpy: (os.kill(os.getpid(), signal.SIGINT), threading.Event().wait())[1])


class Window:
    def __init__(self, frame, w, h, stride, title=b'DVS'):
        """frame: address of w x h 32 bpp pixels (0x00RRGGBB), stride bytes a row; the caller keeps it alive"""
        d = X.XOpenDisplay(None)
        if not d:
            raise OSError('dvsx11: cannot open display %r' % os.environ.get('DISPLAY'))
        X.XSetIOErrorHandler(ctypes.cast(_io_error, P))
        s = X.XDefaultScreen(d)
        if X.XDefaultDepth(d, s) != 24:
            raise OSError('dvsx11: depth %d, want 24' % X.XDefaultDepth(d, s))
        self.d, self.w, self.h = d, w, h
        self.win = X.XCreateSimpleWindow(d, X.XRootWindow(d, s), 0, 0, w, h, 0, 0, X.XBlackPixel(d, s))
        X.XStoreName(d, self.win, title)
        X.XSelectInput(d, self.win, EXPOSURE | KEY_PRESS_MASK)
        self.wm_delete = U(X.XInternAtom(d, b'WM_DELETE_WINDOW', 0))
        X.XSetWMProtocols(d, self.win, ctypes.byref(self.wm_delete), 1)
        self.gc = X.XDefaultGC(d, s)
        self.img = X.XCreateImage(d, X.XDefaultVisual(d, s), 24, Z_PIXMAP, 0, frame, w, h, 32, stride)
        X.XMapWindow(d, self.win)
        X.XSync(d, 0)
        self.ev = ctypes.create_string_buffer(192)      # sizeof(XEvent) on LP64

    def closed(self):
        """handle pending events; True once the window was closed or q / Esc pressed"""
        while X.XPending(self.d):
            X.XNextEvent(self.d, self.ev)
            kind = ctypes.c_int.from_buffer(self.ev).value
            if kind == CLIENT_MESSAGE and ctypes.c_ulong.from_buffer(self.ev, 56).value == self.wm_delete.value:
                return True
            if kind == KEY_PRESS and X.XLookupKeysym(self.ev, 0) in (ord('q'), 0xff1b):
                return True
        return False

    def put(self):
        X.XPutImage(self.d, self.win, self.gc, self.img, 0, 0, 0, 0, self.w, self.h)
        X.XFlush(self.d)


def start(lib):
    """show libdvsview's memory page (after dvs_start(NULL)) from a daemon thread; closing it is a Ctrl+C"""
    w, h, stride = ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
    lib.dvs_frame.restype = P
    lib.dvs_frame.argtypes = [ctypes.POINTER(I)] * 3
    lib.dvs_get.argtypes = [ctypes.c_char_p]
    frame = lib.dvs_frame(ctypes.byref(w), ctypes.byref(h), ctypes.byref(stride))
    win = Window(frame, w.value, h.value, stride.value)       # here: a display error stops the caller

    def loop():
        t = time.monotonic()
        while not win.closed():
            win.put()
            # as often as the decoder draws (its fps setting, also from the panel); more would repeat frames
            t = max(t + 1 / max(lib.dvs_get(b'fps'), 1), time.monotonic() - 0.1)
            time.sleep(max(0, t - time.monotonic()))
        os.kill(os.getpid(), signal.SIGINT)
    threading.Thread(target=loop, daemon=True, name='dvs-x11').start()
    return win


def selftest():
    w, h = 960, 720
    page = (ctypes.c_uint32 * (w * h))()
    for y in range(100, 200):
        for x in range(300, 400):
            page[y * w + x] = 0xff00ff00                 # what dvsview draws: alpha set, green
    win = Window(ctypes.addressof(page), w, h, w * 4)
    for _ in range(5):                                   # let the server map the window
        win.closed()
        win.put()
        time.sleep(0.1)
    got = X.XGetImage(win.d, win.win, 0, 0, w, h, 0xffffffff, Z_PIXMAP)
    inside, outside = X.XGetPixel(got, 350, 150) & 0xffffff, X.XGetPixel(got, 10, 10) & 0xffffff
    assert (inside, outside) == (0x00ff00, 0), (hex(inside), hex(outside))
    print('selftest ok')


if __name__ == '__main__':
    selftest() if sys.argv[1:] == ['--selftest'] else sys.exit(__doc__)
