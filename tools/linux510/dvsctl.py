"""Display settings page for the S5KRC1S live view: ov5647-cap.py --live --raw8 --ctl PORT (11.28).

Runs in the capture process (libdvsview's settings live there), one daemon thread and its HTTP/1.1 connection
threads, all on CPU7 at the top clock (pin(), 11.30):
  GET  /           dvsctl.html
  GET  /params     settings with default and range, JSON (dvs_params())
  POST /params     {"name": int, ...}: apply (dvs_set) and save to SAVED for the next start
  GET  /stats      the renderer's last 2 s of stats + the CSIS CRC error count, JSON
  GET  /frame.png  the frame on HDMI now, 960x720 (before zoom)
  GET  /sensor     sensor settings: saved and in the CIS module now, with ranges (11.29)
  POST /sensor     {"margin": int, "flush_us": int}: save to SENSOR and restart (dvslive.service brings the
                   stream back in ~5 s with them; dvslive.sh writes them to the CIS module before stream on)
No login: it only changes how the view is drawn and the sensor's scan timing, on the board's own network.
"""
import ctypes
import http.server
import json
import os
import signal
import struct
import sys
import threading

SAVED = '/var/lib/dvsview/display.json'
SENSOR = '/var/lib/dvsview/sensor.json'
HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 960, 720
CIS = '/sys/module/is_cis_s5krc1s/parameters/'
CRC = '/sys/module/fimc_is/parameters/e850_csi_crc_count'
# name: (min, max, default) as in dvslive.sh: margin 17 = a 985 us scan (0 = the setfile's 2, 495 us), flush 50 us
SENSOR_RANGE = {'margin': (0, 140, 17), 'flush_us': (1, 1023, 50)}
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


def start(lib, port):
    lib.dvs_set.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.dvs_params.restype = lib.dvs_stats.restype = ctypes.c_char_p
    lib.dvs_png.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
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
                try:
                    st['crc'] = int(open(CRC).read())
                except (OSError, ValueError):
                    pass
                self.send(json.dumps(st).encode())
            elif path == '/sensor':
                self.send(json.dumps(sensor()).encode())
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
            if path not in ('/params', '/sensor'):
                return self.send(b'not found', 'text/plain', 404)
            try:
                req = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
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

    srv = http.server.ThreadingHTTPServer(('', port), Handler)
    srv.daemon_threads = True
    threading.Thread(target=serve, daemon=True).start()
    return srv
