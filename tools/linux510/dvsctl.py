"""Display settings page for the S5KRC1S live view: ov5647-cap.py --live --raw8 --ctl PORT (11.28).

Runs in the capture process (libdvsview's settings live there), one daemon thread and its HTTP/1.1 connection
threads, all on CPU7 at the top clock (pin(), 11.30):
  GET  /           dvsctl.html
  GET  /params     settings with default and range, JSON (dvs_params())
  POST /params     {"name": int, ...}: apply (dvs_set) and save to SAVED for the next start
  GET  /stats      the renderer's last 2 s of stats + the CSIS CRC error count, JSON; the link counters since
                   the start (link) and the CIS D-PHY PLL readback (pll; 11.46); the page's addresses (addrs, 11.50)
  GET  /frame.png  the frame on HDMI now, 960x720 (before zoom)
  GET  /history    ?since=T: the samples after T (epoch s) of the last hour, one per 2 s, for the page's graphs:
                   {"keys": HIST_COLS, "rows": [[t, ...], ...], "frame_bytes": a VC0 frame} (11.50); with the
                   MIPI frame counts of csis-irqstat.ko and the VC0 buffer states of the capture loop (11.51)
  GET  /reg        ?a=30A5&n=3: read n (1-64) sensor registers from a (hex), {"a", "n", "bus", "v": [...]};
                   read only, one I2C_RDWR transfer (register address, then n bytes, auto-increment; 11.50)
  GET  /sensor     sensor settings: saved and in the CIS module now, with ranges (11.29)
  POST /sensor     {"margin": int, "flush_us": int}: save to SENSOR and restart (dvslive.service brings the
                   stream back in ~5 s with them; dvslive.sh writes them to the CIS module before stream on)
  GET  /act        the person action classifier now (dvsact.STATUS, 11.45): on, state, top class and p, newest changes
  GET  /rec        the recorder: {"state": idle|countdown|recording, "left_s", "last"}
  POST /rec        {"label": one of dvsact.CLASSES, "s": 1-30}: after a 3 s countdown, the packets of s seconds to
                   REC_DIR/<UTC>-<label>.gz (dvs_record) and a .json beside it, for the action classifier (11.39)
No login: it only changes how the view is drawn and the sensor's scan timing, on the board's own network.
/reg only reads: no sensor register is written from the page.
HDMI: the page's addresses (Wi-Fi, USB) in Korean at the bottom left of the screen, every 5 s (Banner, 11.50).
"""
import atexit
import base64
import collections
import ctypes
import datetime
import fcntl
import glob
import http.server
import json
import mmap
import os
import shutil
import signal
import socket
import struct
import sys
import threading
import time
import urllib.parse

SAVED = '/var/lib/dvsview/display.json'
SENSOR = '/var/lib/dvsview/sensor.json'
REC_DIR = '/var/tmp/dvsrec'                 # philip's (dvsact-install.sh); packets ~6 MB/s before gzip (11.38)
REC_FREE = 300 << 20                        # bytes left on /var/tmp to start a recording
HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 960, 720
CIS = '/sys/module/is_cis_s5krc1s/parameters/'
CRC = '/sys/module/fimc_is/parameters/e850_csi_crc_count'
PLL = CIS + 'pll_live'                      # the last 0x30A5-0x30A7 readback (11.46)
# name: (min, max, default) as in dvslive.sh: margin 17 = a 985 us scan (0 = the setfile's 2, 495 us), flush 50 us
SENSOR_RANGE = {'margin': (0, 140, 17), 'flush_us': (1, 1023, 50)}
# /history: one row per renderer stats period (2 s) for an hour; counters are totals, the page takes the rates
HIST_S, HIST_N = 2.0, 1800
HIST_KEYS = ('fps', 'draw_ms', 'events', 'buffers', 'ingest_ms', 'kb', 'behind_ms', 'coverage', 'dips', 'scan_us')
LINK_KEYS = ('bufs', 'short_bufs', 'ts_missed', 'fe_missed', 'ts_bad', 'unknown')    # since the stream start (11.46)
# MIPI level (11.51): csis-irqstat.ko's totals of the CSIS link IRQ for VC0, frame start / end and the decoded
# link error bits (INT_SRC0: CRC, ECC, overflow, lost FS/FE ...; bit 16 VRESOL is not among them, see the module)
IRQSTAT = '/sys/module/csis_irqstat/parameters/'
MIPI_KEYS = ('mipi_fs', 'mipi_fe', 'mipi_err')
# DRAM (11.51): the VC0 buffers, n in all; at each DQBUF the capture loop counts the other buffers QUEUED (the
# driver's, free for the DMA) and DONE (filled, waiting to be dequeued): the fewest free and the most waiting
# since the last sample
BUF_KEYS = ('vc0_n', 'vc0_free', 'vc0_wait')
HIST_COLS = ('t',) + HIST_KEYS + ('crc',) + LINK_KEYS + MIPI_KEYS + BUF_KEYS
BUFS = {'n': None, 'free': None, 'wait': None, 'frame_bytes': None}
I2C_ADDR, I2C_DRIVER = 0x10, 'S5KRC1S'      # 7-0010 on the current DT (i2c-7, HSI2C2)
# the address banner on HDMI (11.50): glyphs from tools/host/hangul-atlas.py (not in git: a Mac system font's)
FONT = os.path.join(HERE, 'dvsfont.json')
BANNER_IFS = (('wlan0', '무선 Wi-Fi'), ('usb0', '유선 USB'))
BANNER_W, BANNER_X = 232, 4                 # inside the 240 px left of the 1.5x view: no zoom covers it
# the page's own core (11.30): no device IRQ (they all land on CPU0), systemd keeps the rest on 0-6 (e850-cpu7.conf)
WEB_CPU = 7


def pin(cpus, name='dvs-web'):
    """This thread and the threads it starts (they inherit all three): the CPUs cpus only, at the cluster's top
    clock while one runs (an idle cluster sits at 546 MHz and a 25 ms request is over before schedutil raises it:
    pinned without this a frame took 80 ms, with it 27; 11.30), named name (dvsact uses it too, 11.38)."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl(15, name.encode(), 0, 0, 0)                                  # PR_SET_NAME
    # struct sched_attr: size 56, SCHED_FLAG_KEEP_POLICY | KEEP_PARAMS | UTIL_CLAMP_MIN, util_min 1024
    attr = struct.pack('IIQiIQQQII', 56, 0, 0x08 | 0x10 | 0x20, 0, 0, 0, 0, 0, 1024, 1024)
    if libc.syscall(ctypes.c_long(274), ctypes.c_long(0), attr, ctypes.c_long(0)):    # __NR_sched_setattr (arm64)
        print('dvsctl: uclamp: %s' % os.strerror(ctypes.get_errno()), file=sys.stderr)
    try:
        os.sched_setaffinity(0, cpus)
    except OSError as e:                    # CPU offline (hotplug): stay on the shared cores
        print('dvsctl: CPU%s: %s' % (sorted(cpus), e), file=sys.stderr)


def read_int(path):
    try:
        return int(open(path).read())
    except (OSError, ValueError):
        return None


def buf_note(n, free, wait, frame_bytes):
    """ov5647-cap.py at each VC0 DQBUF: free and wait among the other n - 1 buffers (see BUF_KEYS)"""
    b = BUFS
    b['n'], b['frame_bytes'] = n, frame_bytes
    b['free'] = free if b['free'] is None else min(b['free'], free)
    b['wait'] = wait if b['wait'] is None else max(b['wait'], wait)


def mipi():
    """{'mipi_fs', 'mipi_fe', 'mipi_err'} totals, None without csis-irqstat.ko"""
    try:
        bits = open(IRQSTAT + 'err_bits').read().split(',')
        return {'mipi_fs': read_int(IRQSTAT + 'fs'), 'mipi_fe': read_int(IRQSTAT + 'fe'),
                'mipi_err': sum(int(b) for b in bits)}
    except (OSError, ValueError):
        return {}


class History:
    """The last n rows (HIST_COLS), None where the stats lack one."""

    def __init__(self, n=HIST_N):
        self.rows, self.lock = collections.deque(maxlen=n), threading.Lock()

    def add(self, t, st, crc, extra=None):
        link, extra = st.get('link') or {}, extra or {}
        row = ([round(t, 1)] + [st.get(k) for k in HIST_KEYS] + [crc] + [link.get(k) for k in LINK_KEYS] +
               [extra.get(k) for k in MIPI_KEYS + BUF_KEYS])
        with self.lock:
            self.rows.append(row)

    def since(self, t):
        with self.lock:
            return [r for r in self.rows if r[0] > t]


def reg_args(query):
    """?a=HEX&n=N -> (a, n); ValueError unless 0 <= a, 1 <= n <= 64 and a + n <= 0x10000"""
    q = urllib.parse.parse_qs(query)
    a, n = int(q['a'][0], 16), int(q.get('n', ['1'])[0])
    if not (0 <= a and 1 <= n <= 64 and a + n <= 0x10000):
        raise ValueError('range')
    return a, n


class I2cMsg(ctypes.Structure):
    _fields_ = [('addr', ctypes.c_uint16), ('flags', ctypes.c_uint16), ('len', ctypes.c_uint16),
                ('buf', ctypes.c_void_p)]


class I2cData(ctypes.Structure):
    _fields_ = [('msgs', ctypes.c_void_p), ('n', ctypes.c_uint32)]


def reg_read(a, n):
    """n sensor registers from a. I2C_RDWR passes the bound driver; the adapter lock keeps the CIS module's own
    transfers apart. Burst reads auto-increment (0x30A5 x 3 = the three single reads, 11.50)."""
    bus = next((int(os.path.basename(d).split('-')[0]) for d in glob.glob('/sys/bus/i2c/devices/*-%04x' % I2C_ADDR)
                if os.path.basename(os.path.realpath(d + '/driver')) == I2C_DRIVER), None)
    if bus is None:
        raise OSError('no %s on I2C' % I2C_DRIVER)
    w, r = ctypes.create_string_buffer(bytes([a >> 8, a & 0xff]), 2), ctypes.create_string_buffer(n)
    msgs = (I2cMsg * 2)(I2cMsg(I2C_ADDR, 0, 2, ctypes.addressof(w)), I2cMsg(I2C_ADDR, 1, n, ctypes.addressof(r)))
    fd = os.open('/dev/i2c-%d' % bus, os.O_RDWR)
    try:
        fcntl.ioctl(fd, 0x0707, I2cData(ctypes.addressof(msgs), 2))     # I2C_RDWR
    finally:
        os.close(fd)
    return bus, list(r.raw)


def ipv4(ifname):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        return socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack('256s', ifname.encode()))[20:24])
    except OSError:                         # SIOCGIFADDR: down or no address
        return None
    finally:
        s.close()


def addrs(port):
    """[(label, 'ip:port' or None)] for the page's interfaces"""
    return [(name, '%s:%d' % (ip, port) if ip else None) for ifn, name in BANNER_IFS for ip in [ipv4(ifn)]]


def banner_rows(font, lines, w):
    """[(text, level)] -> rows of w gray bytes: each glyph's alpha x level / 255, clipped at w"""
    h, rows = font['h'], []
    for text, lvl in lines:
        tab = bytes(a * lvl // 255 for a in range(256))
        line, x = [bytearray(w) for _ in range(h)], 0
        for ch in text:
            gw, a = font['glyphs'].get(ch) or font['glyphs']['?']
            n = min(gw, w - x)
            if n <= 0:
                break
            for r in range(h):
                line[r][x:x + n] = a[r * gw:r * gw + n].translate(tab)
            x += gw
        rows += line
    return rows


def fb_put(f, rows, x0, bottom):
    """gray rows -> f = open /dev/fb0 (32 bpp) at x0, bottom px above the visible page's last row. mmap as dvsview
    does: read() and write() on this fb return 0 bytes (11.50). The caller keeps f open: every close prints
    decon_release to the kernel log (and UART)."""
    xres, yres, _, yv, _, yoff, bpp = struct.unpack('7I', fcntl.ioctl(f, 0x4600, bytes(160))[:28])  # FBIOGET_VSCREENINFO
    stride = int(open('/sys/class/graphics/fb0/stride').read())
    if bpp != 32 or not rows or x0 + len(rows[0]) > xres:
        return
    y0 = yoff + yres - bottom - len(rows)
    with mmap.mmap(f.fileno(), stride * yv) as m:
        for i, g in enumerate(rows):
            px = bytearray(len(g) * 4)
            px[0::4] = px[1::4] = px[2::4] = g
            px[3::4] = b'\xff' * len(g)
            o = (y0 + i) * stride + x0 * 4
            m[o:o + len(px)] = px


def banner_loop(port):
    pin({WEB_CPU}, 'dvs-banner')
    try:
        font = json.load(open(FONT))
    except (OSError, ValueError):
        print('dvsctl: no %s, no address banner on HDMI' % FONT, file=sys.stderr)
        return
    font['glyphs'] = {c: (w, base64.b64decode(a)) for c, (w, a) in font['glyphs'].items()}
    try:
        fb = open('/dev/fb0', 'r+b', buffering=0)
    except OSError as e:
        print('dvsctl: banner: %s' % e, file=sys.stderr)
        return
    while True:
        lines = [('웹 접속 주소', 255)]
        for name, url in addrs(port):
            lines += [(name, 150), (url or '연결 안 됨', 255)]
        try:
            fb_put(fb, banner_rows(font, lines, BANNER_W), BANNER_X, 8)
        except OSError as e:
            print('dvsctl: banner: %s' % e, file=sys.stderr)
            return
        time.sleep(5)               # a zoom change clears the screen; a Wi-Fi address can change


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'w') as f:
        json.dump(obj, f)
    os.replace(path + '.tmp', path)


def sensor():
    try:
        saved = json.load(open(SENSOR))
    except (OSError, ValueError):
        saved = {}
    out = {}
    for k, (lo, hi, d) in SENSOR_RANGE.items():
        try:
            now = int(open(CIS + k).read())
        except (OSError, ValueError):
            now = None
        out[k] = {'v': saved.get(k, d), 'now': now, 'min': lo, 'max': hi, 'def': d}
    return out


def rec_check(label, s, free_bytes, busy):
    """HTTP code for POST /rec: 400 bad label or length, 409 one is running, 507 disk nearly full"""
    from dvsact import CLASSES          # numpy: only once someone records

    if label not in CLASSES or type(s) is not int or not 1 <= s <= 30:
        return 400
    if busy:
        return 409
    return 507 if free_bytes < REC_FREE else 200


class Recorder:
    """One recording at a time: countdown, dvs_record(path) for s seconds, dvs_record(None), then a .json with the
    label and the sensor settings. close(False) (exit, SIGTERM) ends one early and marks it incomplete."""

    def __init__(self, lib, dirpath=REC_DIR, countdown=3.0):
        self.lib, self.dir, self.countdown = lib, dirpath, countdown
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.state, self.until, self.last, self.cur = 'idle', 0.0, None, None

    def status(self):
        with self.lock:
            return {'state': self.state, 'left_s': round(max(0.0, self.until - time.monotonic()), 1),
                    'last': self.last}

    def start(self, label, s):
        with self.lock:
            try:
                free = shutil.disk_usage(self.dir).free
            except OSError:
                free = 0
            code = rec_check(label, s, free, self.state != 'idle')
            if code == 200:
                self.state, self.until = 'countdown', time.monotonic() + self.countdown
                self.stop.clear()
                threading.Thread(target=self._run, args=(label, s), daemon=True).start()
            return code

    def _run(self, label, s):
        if self.stop.wait(self.countdown):
            with self.lock:
                self.state = 'idle'
            return
        with self.lock:
            if self.stop.is_set():
                self.state = 'idle'
                return
            now = datetime.datetime.now(datetime.timezone.utc)
            path = os.path.join(self.dir, now.strftime('%Y%m%dT%H%M%SZ') + '-' + label)
            r = self.lib.dvs_record((path + '.gz').encode())
            if r < 0:
                self.state, self.last = 'idle', 'error: ' + os.strerror(-r)
                return
            self.state, self.until = 'recording', time.monotonic() + s
            self.cur = {'path': path, 'label': label, 'start': now.isoformat().replace('+00:00', 'Z'), 's': s}
        if not self.stop.wait(s):
            self.close(True)

    def close(self, complete):
        with self.lock:
            self.stop.set()
            cur, self.cur = self.cur, None
            if cur is None:
                return
            n = self.lib.dvs_record(None)
            now = {k: v['now'] for k, v in sensor().items()}
            meta = {'label': cur['label'], 'start': cur['start'], 's': cur['s'], 'events': max(n, 0),
                    'margin': now['margin'], 'flush_us': now['flush_us'], 'sensor': 's5krc1s', 'complete': complete}
            self.state, self.last = 'idle', os.path.basename(cur['path']) + '.gz'
            try:
                with open(cur['path'] + '.json', 'w') as f:
                    json.dump(meta, f)
            except OSError as e:                # the .gz is there; a full disk must not wedge the recorder
                self.last = 'error: %s.json: %s' % (os.path.basename(cur['path']), e)


def start(lib, port):
    lib.dvs_set.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.dvs_params.restype = lib.dvs_stats.restype = ctypes.c_char_p
    lib.dvs_png.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    lib.dvs_record.argtypes = [ctypes.c_char_p]
    rec = Recorder(lib)
    hist, reg_lock = History(), threading.Lock()
    atexit.register(rec.close, False)          # SIGTERM is a KeyboardInterrupt in ov5647-cap.py, then sys.exit
    pngbuf = bytearray(W * H * 3 + H + 65536)   # level-1 deflate of the raw rows can come out a little larger
    lock = threading.Lock()                     # dvs_params() and dvs_png() are not reentrant
    try:
        for k, v in json.load(open(SAVED)).items():
            lib.dvs_set(k.encode(), int(v))
    except (OSError, ValueError, AttributeError):
        pass

    def params():
        with lock:
            return json.loads(lib.dvs_params())

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'       # keep-alive: no TCP connect per frame over Wi-Fi (11.30)
        disable_nagle_algorithm = True      # the body goes out right after the headers, not after their ACK
        timeout = 30                        # an idle connection (a browser's preconnect) holds a thread

        def send(self, body, ctype='application/json', code=200):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            if code >= 400:                 # a refused request's body may be unread: don't parse it as the next one
                self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split('?')[0]
            if path == '/':
                self.send(open(os.path.join(HERE, 'dvsctl.html'), 'rb').read(), 'text/html; charset=utf-8')
            elif path == '/params':
                self.send(json.dumps(params()).encode())
            elif path == '/stats':
                st = json.loads(lib.dvs_stats())
                crc = read_int(CRC)
                if crc is not None:
                    st['crc'] = crc
                st['addrs'] = addrs(port)
                try:
                    st['pll'] = open(PLL).read().strip()
                except OSError:                 # a CIS module from before 11.46
                    pass
                self.send(json.dumps(st).encode())
            elif path == '/sensor':
                self.send(json.dumps(sensor()).encode())
            elif path == '/act':
                import dvsact               # the classifier's module in this process; 'on' false without it
                self.send(json.dumps(dvsact.STATUS).encode())
            elif path == '/rec':
                self.send(json.dumps(rec.status()).encode())
            elif path == '/history':
                try:
                    t = float(urllib.parse.parse_qs(self.path.partition('?')[2]).get('since', ['0'])[0])
                except ValueError:
                    return self.send(b'{"error": "since: epoch seconds"}', code=400)
                self.send(json.dumps({'keys': HIST_COLS, 'rows': hist.since(t),
                                      'frame_bytes': BUFS['frame_bytes']}).encode())
            elif path == '/reg':
                try:
                    a, n = reg_args(self.path.partition('?')[2])
                except (KeyError, ValueError):
                    return self.send(b'{"error": "want ?a=HEX&n=1..64 inside 0000-FFFF"}', code=400)
                try:
                    with reg_lock:
                        bus, v = reg_read(a, n)
                except OSError as e:            # sensor powered off, no device
                    return self.send(json.dumps({'error': str(e)}).encode(), code=502)
                self.send(json.dumps({'a': a, 'n': n, 'bus': bus, 'v': v}).encode())
            elif path == '/frame.png':
                with lock:
                    n = lib.dvs_png(ctypes.addressof(ctypes.c_char.from_buffer(pngbuf)), len(pngbuf))
                    body = bytes(pngbuf[:n]) if n > 0 else b''
                if not body:
                    return self.send(b'no frame yet', 'text/plain', 503)
                self.send(body, 'image/png')
            else:
                self.send(b'not found', 'text/plain', 404)

        def do_POST(self):
            path = self.path.split('?')[0]
            if path not in ('/params', '/sensor', '/rec'):
                return self.send(b'not found', 'text/plain', 404)
            try:
                req = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                if path == '/rec':
                    code = rec.start(req.get('label'), req.get('s', 6))
                    return self.send(json.dumps(dict(rec.status(), code=code)).encode(), code=code)
                if path == '/sensor':
                    cur = {k: p['v'] for k, p in sensor().items()}
                    for k, v in req.items():
                        lo, hi, _ = SENSOR_RANGE[k]
                        if not lo <= int(v) <= hi:
                            raise ValueError(k)
                        cur[k] = int(v)
                else:
                    rejected = [k for k, v in req.items() if lib.dvs_set(str(k).encode(), int(v)) < 0]
            except (ValueError, TypeError, AttributeError, KeyError):
                return self.send(b'{"error": "want {\\"name\\": integer in range, ...}"}', code=400)
            if path == '/sensor':
                save(SENSOR, cur)
                self.send(json.dumps({'restart': True, 'sensor': cur}).encode())
                # SIGTERM = the pkill path: stream off in ov5647-cap.py, then systemd starts dvslive.sh again
                threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM)).start()
                return
            cur = {k: p['v'] for k, p in params().items()}
            save(SAVED, cur)
            self.send(json.dumps({'rejected': rejected, 'params': cur}).encode())

        def log_message(self, *args):
            pass

    def serve():
        pin({WEB_CPU})
        srv.serve_forever()

    def sample():
        pin({WEB_CPU}, 'dvs-hist')
        while True:
            st = json.loads(lib.dvs_stats())
            if 'fps' in st:                     # "{}" before the first stats period
                b = {'vc0_n': BUFS['n'], 'vc0_free': BUFS['free'], 'vc0_wait': BUFS['wait']}
                BUFS['free'] = BUFS['wait'] = None      # a race with buf_note loses one DQBUF's count at most
                hist.add(time.time(), st, read_int(CRC), dict(mipi(), **b))
            time.sleep(HIST_S)

    srv = http.server.ThreadingHTTPServer(('', port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=serve, daemon=True).start()
    threading.Thread(target=sample, daemon=True).start()
    threading.Thread(target=banner_loop, args=(port,), daemon=True).start()
    return srv
