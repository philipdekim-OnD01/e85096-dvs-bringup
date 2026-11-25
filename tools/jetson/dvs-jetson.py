#!/usr/bin/env python3
"""dvs-jetson: the E850's S5KRC1S decoder and settings (libdvsview.so, dvsctl.py, dvsx11.py, dvsgl, dvspanel) on a
Jetson Orin Nano with the NRV DVS camera (docs 11.108). This file only replaces ov5647-cap.py's Exynos capture:

  /dev/video0 (tegra VI, driver nrv_dvs): the register list goes in the V4L2 array control nrv_register_writes
  (words (addr << 8) | value; the driver writes it at STREAMON and itself starts the stream, so 0x0100 is left out),
  S_FMT 8192 x 128 RGGB8 picks the DT's AER mode0, USERPTR buffers in cached memory as the NRV SDK (stream.cpp)
  does, each DQBUF goes to dvs_ingest() whole: 0x391A/B = 127 packets and the sensor sends one more (11.22).

Register list: the setfile (default: the Jetson's own AER setfile, whose link part, 4 lanes and D-PHY PLL, matches
this receiver's DT), then what the E850 driver writes after its setfile: 0x391A/B packets, 0x3912/13 flush_us and
0x321D/E margin from the settings page (sensor.json, as dvslive.sh), then --regs.

  dvs-jetson.py [--x11] [--ctl 8080] [--fps 30]     q / Esc in the window or Ctrl+C: exit 130
  dvs-jetson.py --print-regs                         the register list, one "AAAA=VV" a line
  dvs-jetson.py --selftest
A sensor setting from the page exits 0 (SIGTERM), no buffer for -t s exits 1: the launcher (dvs-jetson) starts it
again, as dvslive.service on the E850.

The page's MIPI and DRAM graphs (E850: csis-irqstat.ko and the VC0 buffer states, 11.51), from what VI tells:
  MIPI frames   every buffer VI hands back, all decoded (vi5_fops.c: one MIPI frame each; v4l2 sequence counts these
                buffers, so a frame VI had no buffer for shows nowhere here: the decoder's missed scans still see it)
  link errors   buffers with V4L2_BUF_FLAG_ERROR: VI's corr_err, CRC/ECC and the like (err_data in kern.log)
  buffers       at each DQBUF, QUERYBUF of the others: queued (free for VI) and done (waiting)
"""
import argparse
import ctypes
import json
import mmap
import os
import re
import select
import signal
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.expanduser('~/.config/dvs-jetson')       # dvsctl's /var/lib/dvsview on the E850
RUN = os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/tmp'), 'dvs-jetson')
SETFILE = os.path.expanduser('~/DVS.JS.ORIN.01/app/dvs_streamer/settings/Delta_10_2000FPS.txt')
W, H = 8192, 128                    # the DT's mode0 (cam1.dts): RAW8 lines of 8192 bytes

VIDIOC_S_FMT = 0xc0d05605
VIDIOC_REQBUFS = 0xc0145608
VIDIOC_QUERYBUF = 0xc0585609
VIDIOC_QBUF = 0xc058560f
VIDIOC_DQBUF = 0xc0585611
VIDIOC_STREAMON = 0x40045612
VIDIOC_STREAMOFF = 0x40045613
VIDIOC_S_EXT_CTRLS = 0xc0205648
VIDIOC_QUERY_EXT_CTRL = 0xc0e85667
CAPTURE, USERPTR = 1, 2
FMT_RGGB8 = 0x42474752              # V4L2_PIX_FMT_SRGGB8
BUF_ERROR = 0x40
BUF = struct.Struct('IIIII4xqq16sIIQIIi4x')     # struct v4l2_buffer on arm64, 88 bytes
QCTRL = struct.Struct('II32sqqQqIIII4I32I')     # struct v4l2_query_ext_ctrl, 232 bytes
REG_CTRL = b'nrv_register_writes'


def log(msg):
    print('dvs-jetson: ' + msg, file=sys.stderr, flush=True)


def setfile_regs(text):
    """[(addr, value)] of the '20:AAAA=VV' lines, in order; 0x0100 (stream on/off) is the driver's own"""
    out = []
    for line in text.splitlines():
        m = re.match(r'\s*20:([0-9a-fA-F]{4})=([0-9a-fA-F]{1,2})\b', line.split('//')[0])
        if m and int(m.group(1), 16) != 0x0100:
            out.append((int(m.group(1), 16), int(m.group(2), 16)))
    return out


def reg_list(setfile, packets, flush_us, margin, extra=''):
    """setfile, then the E850 driver's writes after it (is-cis-s5krc1s.c mode_change); margin 0 keeps the setfile's"""
    regs = list(setfile) + [(0x391A, packets & 0xff), (0x391B, packets >> 8)]
    if 1 <= flush_us <= 1023:
        regs += [(0x3912, flush_us & 0xff), (0x3913, flush_us >> 8)]
    if 1 <= margin <= 0xffff:
        regs += [(0x321D, margin >> 8), (0x321E, margin & 0xff)]
    for a, v in re.findall(r'([0-9a-fA-F]{1,4})=([0-9a-fA-F]{1,2})', extra or ''):
        regs.append((int(a, 16), int(v, 16)))
    if any(a == 0x0100 for a, _ in regs):
        raise ValueError('0x0100 is the driver\'s')
    return regs


def sensor_settings(dvsctl):
    """margin and flush_us saved by the page (dvsctl.SENSOR), else dvsctl's defaults, clamped to its ranges"""
    try:
        saved = json.load(open(dvsctl.SENSOR))
    except (OSError, ValueError):
        saved = {}
    out = {}
    for k, (lo, hi, d) in dvsctl.SENSOR_RANGE.items():
        try:
            out[k] = min(max(int(saved.get(k, d)), lo), hi)
        except (TypeError, ValueError):
            out[k] = d
    return out


def ioctl(fd, req, buf):
    import fcntl
    while True:
        try:
            return fcntl.ioctl(fd, req, buf)
        except InterruptedError:
            continue


def find_ctrl(fd, name):
    q = bytearray(QCTRL.size)
    cid = 0
    while True:
        struct.pack_into('I', q, 0, cid | 0x80000000 | 0x40000000)     # NEXT_CTRL | NEXT_COMPOUND
        try:
            ioctl(fd, VIDIOC_QUERY_EXT_CTRL, q)
        except OSError:
            raise OSError('%s: no control %s (not the nrv_dvs driver?)' % (a.dev, name.decode()))
        f = QCTRL.unpack(q)
        cid = f[0]
        if f[2].rstrip(b'\0') == name:
            return cid, f[11]                    # id, dims[0]: the most words


def set_regs(fd, regs):
    cid, most = find_ctrl(fd, REG_CTRL)
    if len(regs) > most:
        raise OSError('%d registers, the driver takes %d' % (len(regs), most))
    words = (ctypes.c_uint32 * len(regs))(*[(r << 8) | v for r, v in regs])
    ctrl = struct.pack('=IIIQ', cid, ctypes.sizeof(words), 0, ctypes.addressof(words))      # packed, 20 bytes
    cbuf = ctypes.create_string_buffer(ctrl)
    ioctl(fd, VIDIOC_S_EXT_CTRLS, bytearray(struct.pack('IIIiI4xQ', 0, 1, 0, 0, 0, ctypes.addressof(cbuf))))


def selftest():
    sf = setfile_regs('20:0100=00 // stop\n20:3912=E7 // to_scnt1\n// 20:3913=03\n20:391a=7F\nx 20:0000=1\n')
    assert sf == [(0x3912, 0xE7), (0x391A, 0x7F)], sf
    r = reg_list(sf, 127, 50, 17, '30a7=b0')
    assert r[2:] == [(0x391A, 127), (0x391B, 0), (0x3912, 50), (0x3913, 0), (0x321D, 0), (0x321E, 17),
                     (0x30A7, 0xB0)], r
    assert reg_list(sf, 127, 0, 0) == sf + [(0x391A, 127), (0x391B, 0)]     # 0: the setfile's
    try:
        reg_list(sf, 127, 50, 17, '100=01')
        raise AssertionError('0x0100 let through')
    except ValueError:
        pass
    assert BUF.size == 88 and QCTRL.size == 232
    print('dvs-jetson selftest ok')


ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('--dev', default='/dev/video0')
ap.add_argument('--setfile', default=SETFILE)
ap.add_argument('--regs', default='', help='extra writes after the rest, "AAAA=VV,..." (hex)')
ap.add_argument('--bufs', type=int, default=16, help='VI buffers (1 MiB each)')
ap.add_argument('--fps', type=int, default=30, help='frames drawn per second')
ap.add_argument('--ctl', type=int, default=8080, help='settings page port, 0: none')
ap.add_argument('--x11', action='store_true', help='the view in an X window ($DISPLAY), else no view')
ap.add_argument('-t', type=float, default=3.0, help='no buffer for this many s: exit 1')
ap.add_argument('--print-regs', action='store_true')
ap.add_argument('--selftest', action='store_true')
a = ap.parse_args()
if a.selftest:
    selftest()
    sys.exit(0)

sys.path.insert(0, HERE)
import dvsctl                                       # noqa: E402  (beside this file, as on the E850)
dvsctl.SAVED, dvsctl.SENSOR = STATE + '/display.json', STATE + '/sensor.json'
dvsctl.REC_DIR = os.path.expanduser('~/dvsrec')
os.makedirs(dvsctl.REC_DIR, exist_ok=True)         # the page's recorder needs it for its free-space check
dvsctl.CIS = RUN + '/'                              # the page's "now": what this process wrote to the sensor
dvsctl.I2C_DRIVER = 'nrv_dvs'
dvsctl.WEB_CPU = 5                                  # 6 cores; dvsview.c keeps decode 1, render 2, filter 3
dvsctl.BANNER_IFS = (('wlP1p1s0', '무선 Wi-Fi'), ('enP8p1s0', '유선 LAN'))     # the page's addresses

s = sensor_settings(dvsctl)
regs = reg_list(setfile_regs(open(a.setfile).read()), H - 1, s['flush_us'], s['margin'], a.regs)
if a.print_regs:
    print('\n'.join('%04X=%02X' % r for r in regs))
    sys.exit(0)

fd = os.open(a.dev, os.O_RDWR | os.O_NONBLOCK)
set_regs(fd, regs)
fmt = bytearray(208)
struct.pack_into('IIIIIIII', fmt, 0, CAPTURE, 0, W, H, FMT_RGGB8, 1, W, W * H)    # field NONE
ioctl(fd, VIDIOC_S_FMT, fmt)
w, h, pf, _, bpl, size = struct.unpack_from('IIIIII', fmt, 8)
if (w, h, pf, bpl) != (W, H, FMT_RGGB8, W) or size < W * H:
    sys.exit('dvs-jetson: S_FMT gave %dx%d %08x bpl %d size %d' % (w, h, pf, bpl, size))
req = bytearray(struct.pack('IIII4x', a.bufs, CAPTURE, USERPTR, 0))
ioctl(fd, VIDIOC_REQBUFS, req)
n = struct.unpack_from('I', req)[0]
bufs = [mmap.mmap(-1, size) for _ in range(n)]          # page aligned (VI wants 64)
addrs = [ctypes.addressof(ctypes.c_char.from_buffer(b)) for b in bufs]


def qbuf(i):
    ioctl(fd, VIDIOC_QBUF, bytearray(BUF.pack(i, CAPTURE, 0, 0, 0, 0, 0, b'', 0, USERPTR, addrs[i], size, 0, 0)))


def buf_flags(i):
    b = bytearray(BUF.pack(i, CAPTURE, 0, 0, 0, 0, 0, b'', 0, USERPTR, 0, 0, 0, 0))
    ioctl(fd, VIDIOC_QUERYBUF, b)
    return struct.unpack_from('I', b, 12)[0]


def note_bufs(idx):
    """dvsctl's DRAM graph: the other buffers QUEUED (free for VI) and DONE (waiting), as ov5647-cap.py"""
    f = [buf_flags(i) for i in range(n) if i != idx]
    dvsctl.buf_note(n, sum(x & 2 != 0 for x in f), sum(x & 4 != 0 for x in f), size)


def stream_off():
    try:
        ioctl(fd, VIDIOC_STREAMOFF, struct.pack('I', CAPTURE))
    except OSError as e:
        log('STREAMOFF: %s' % e)


for i in range(n):
    qbuf(i)
lib = ctypes.CDLL(os.path.join(HERE, 'libdvsview.so'))
lib.dvs_start.argtypes = [ctypes.c_char_p, ctypes.c_int]
lib.dvs_ingest.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
lib.dvs_stats.restype = ctypes.c_char_p
import dvsx11                                       # noqa: E402
dvsx11.GL, dvsx11.GL_LIB = os.path.join(HERE, 'dvsgl'), None     # the system's EGL (NVIDIA)
gl = lib.dvs_share() if a.x11 and dvsx11.gl_ready() else -1
r = lib.dvs_start(None, a.fps)
if r < 0:
    sys.exit('dvs-jetson: dvs_start: ' + os.strerror(-r))
if a.x11:
    dvsx11.start_gl(lib, gl) if gl >= 0 else dvsx11.start(lib)
if a.ctl:
    try:
        dvsctl.start(lib, a.ctl, banner=False)
    except OSError as e:
        log('settings page off: port %d: %s' % (a.ctl, e))

os.makedirs(RUN, exist_ok=True)
for k, v in s.items():                              # the page's "now"
    with open(os.path.join(RUN, k), 'w') as f:
        f.write('%d\n' % v)
signal.signal(signal.SIGTERM, signal.default_int_handler)
user_quit = []


def _sigint(*_):
    user_quit.append(1)
    raise KeyboardInterrupt


signal.signal(signal.SIGINT, _sigint)
ioctl(fd, VIDIOC_STREAMON, struct.pack('I', CAPTURE))
log('%s: %d registers from %s, flush %d us, margin %d; %d buffers of %d bytes' % (
    a.dev, len(regs), a.setfile, s['flush_us'], s['margin'], n, size))
got, errors, lost, last_seq = 0, 0, 0, None


def mipi():
    """the page's MIPI graphs: each buffer VI hands back goes to the decoder, so its count, from the same 2 s stats
    as the rest of the row (a live count ran up to 2 s ahead of them)"""
    b = json.loads(lib.dvs_stats()).get('link', {}).get('bufs')
    return {'mipi_fs': b, 'mipi_fe': b, 'mipi_err': errors}


dvsctl.mipi = mipi
t0 = last = report = time.monotonic()
code = 1
try:
    while True:
        if not select.select([fd], [], [], 0.2)[0]:
            if time.monotonic() - last > a.t:
                log('no buffer for %.1f s' % a.t)
                break
            continue
        b = bytearray(BUF.size)
        struct.pack_into('II', b, 0, 0, CAPTURE)
        struct.pack_into('I', b, 60, USERPTR)
        try:
            ioctl(fd, VIDIOC_DQBUF, b)
        except BlockingIOError:
            continue
        i, _, used, flags = struct.unpack_from('IIII', b)
        seq = struct.unpack_from('I', b, 56)[0]
        errors += flags & BUF_ERROR != 0
        if last_seq is not None and seq > last_seq + 1:
            lost += seq - last_seq - 1                  # buffers the driver never handed back
        last_seq = seq
        lib.dvs_ingest(addrs[i], size)
        if a.ctl:
            note_bufs(i)
        qbuf(i)
        got += 1
        last = time.monotonic()
        if last - report >= 10:
            report = last
            lk = json.loads(lib.dvs_stats()).get('link', {})
            log('%d MIPI frames (%.0f/s), %d with the error flag, %d skipped in sequence; decoder: short %s, '
                'ts_missed %s, fe_missed %s' % (got, got / (last - t0), errors, lost, lk.get('short_bufs'),
                                                lk.get('ts_missed'), lk.get('fe_missed')))
    code = 1
except KeyboardInterrupt:
    code = 130 if user_quit else 0
finally:
    stream_off()
    log('stopped after %.1f s, %d buffers' % (time.monotonic() - t0, got))
import atexit                   # noqa: E402
atexit._run_exitfuncs()         # dvsctl's recorder; then as ov5647-cap.py leave(): no interpreter teardown
sys.stdout.flush()
os._exit(code)
