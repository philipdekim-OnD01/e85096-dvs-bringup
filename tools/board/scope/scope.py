#!/usr/bin/env python3
"""e850-scope: SOL lensless microscope (CX3 UVC, repo sol-cx3-uvc) on the E850-96 USB host port -> web viewer on :8090

Runs as root under scope-run.sh, which owns USB host mode and restarts this program when it exits (/reload).
Capture: v4l2-ctl streams the camera's largest YUYV size (Hi-846 kit 1632x1224, Hi-5031Q kit 2016x1536) into a pipe;
the Y plane goes through frame averaging and flat-field correction here. The sensor is told by the chip ID in the
firmware's diag (SENSORS), so one viewer serves both kits, including firmware already in a kit's EEPROM. The browser (scope.html) does zoom, display LUT, histogram, measurement and the data bar.
Sensor access uses the firmware's vendor requests (sol-cx3-uvc fw/uvc/app.h) over usbfs while uvcvideo streams.

  scope.py [--fake FILE.yuv [--size WxH] [--chip 5031]] [--data DIR] [--port N]
  --fake loops raw YUYV frames (default 1632x1224, chip 4608) from a file instead of the camera (PC without a board).
"""
import argparse, ctypes, fcntl, glob, http.server, json, os, re, socketserver, struct, subprocess, sys, threading, time
import urllib.parse, zlib
import numpy as np

os.environ.setdefault("TZ", "Asia/Seoul")        # the board runs in UTC; photo names and the page clock use lab time
time.tzset()
W, H = 1632, 1224                                 # current frame size, from the camera (largest_yuyv) or --size
# By firmware chip ID: line length clock (MHz), the registers the page explains, default exposure (100 us units).
SENSORS = {
    0x4608: {"name": "Hi-846", "mhz": 288, "llp": "0x0008", "fll": "0x0006", "expReg": "0x0074", "exp": 150,
             "regs": [["0f16", "칩 ID"], ["0006", "프레임 길이(FLL)"], ["0008", "줄 길이(LLP)"], ["0074", "노출(줄 수)"],
                      ["0a00", "스트림 모드"]]},
    0x5031: {"name": "Hi-5031Q", "mhz": 124, "llp": "0x0206", "fll": "0x0210", "expReg": "0x020C", "exp": 2000,
             "regs": [["0716", "칩 ID"], ["0210", "프레임 길이(FLL)"], ["0206", "줄 길이(LLP)"], ["020c", "노출(줄 수)"],
                      ["020a", "아날로그 이득(값/16+1 배)"], ["0b00", "스트림 모드"]]},
}
HERE = os.path.dirname(os.path.abspath(__file__))
DIAG_FIELDS = ("magic chipId initStatus usbSpeed frames goodFrames lastFrameBytes buffers commitFail dmaResets "
               "streamStarts lastFrameMs llp fll exposureLines mipiErrors framePeriodMs exposure100us led truncFrames "
               "vBadRows vFirstCol convMs").split()
USBDEVFS_CONTROL = 0xC0185500                     # _IOWR('U', 0, struct usbdevfs_ctrltransfer), 24 B on arm64
FLAT_FRAMES = 16

ap = argparse.ArgumentParser()
ap.add_argument("--fake")
ap.add_argument("--size", default="1632x1224")             # --fake only
ap.add_argument("--chip", default="4608")                  # --fake only
ap.add_argument("--data", default="/var/lib/e850-scope")
ap.add_argument("--port", type=int, default=8090)
ap.add_argument("--ctl", default="/var/tmp/e850-scope")   # stop / reload flag files (scope-run.sh)
A = ap.parse_args()
PHOTOS = os.path.join(A.data, "photos")
os.makedirs(PHOTOS, exist_ok=True)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- settings (persist across restarts)
CFG_FILE = os.path.join(A.data, "settings.json")
cfg = {"exp": 150, "avg": 1, "flat": False, "pixel_um": 2.24, "sample": "", "note": ""}
try:
    cfg.update(json.load(open(CFG_FILE)))
except (OSError, ValueError):
    pass
cfg_lock = threading.Lock()


def save_cfg():
    tmp = CFG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, ensure_ascii=False)
    os.replace(tmp, CFG_FILE)


# ---------------------------------------------------------------- PNG (grey, no extra libraries on the board)
def png(gray):
    h, w = gray.shape
    raw = np.empty((h, w + 1), np.uint8)
    raw[:, 0] = 0
    raw[:, 1:] = gray
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw.tobytes(), 1)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- firmware vendor requests over usbfs
class Sol:
    def __init__(self):
        self.path = None

    def find(self, video):
        """usbfs node of the USB device behind /dev/videoN."""
        d = os.path.realpath(f"/sys/class/video4linux/{os.path.basename(video)}/device")  # .../2-1.3:1.0
        d = os.path.dirname(d)
        bus, dev = (int(open(os.path.join(d, f)).read()) for f in ("busnum", "devnum"))
        self.path = f"/dev/bus/usb/{bus:03d}/{dev:03d}"

    def ctl(self, rtype, req, val, idx, n_or_data):
        if A.fake:
            return bytes(n_or_data) if isinstance(n_or_data, int) else b""
        if not self.path:
            raise OSError("현미경 없음")
        data = bytes(n_or_data) if not isinstance(n_or_data, int) else b""
        n = n_or_data if isinstance(n_or_data, int) else len(data)
        buf = ctypes.create_string_buffer(data, max(n, 1))
        arg = bytearray(struct.pack("<BBHHHI4xQ", rtype, req, val, idx, n, 2000, ctypes.addressof(buf)))
        fd = os.open(self.path, os.O_RDWR)
        try:
            r = fcntl.ioctl(fd, USBDEVFS_CONTROL, arg)
        finally:
            os.close(fd)
        return buf.raw[:r]

    def diag(self):
        raw = self.ctl(0xC0, 0xD0, 0, 0, 4 * len(DIAG_FIELDS))
        return dict(zip(DIAG_FIELDS, struct.unpack(f"<{len(raw) // 4}I", raw))) if len(raw) >= 4 else {}

    def reg_read(self, reg):
        r = self.ctl(0xC0, 0xD3, 0, reg, 2)
        return (r[0] << 8 | r[1]) if len(r) == 2 else 0

    def reg_write(self, reg, val):
        self.ctl(0x40, 0xD2, val, reg, 0)

    def exposure(self, e100us):
        self.ctl(0x40, 0xD6, e100us, 0, 0)


sol = Sol()


# ---------------------------------------------------------------- capture and processing
def largest_yuyv(dev):
    """Largest YUYV frame size the camera offers (frame 1 of the SOL firmware)."""
    out = subprocess.run(["v4l2-ctl", "-d", dev, "--list-formats-ext"], capture_output=True, text=True).stdout
    sizes, yuyv = [], False
    for line in out.splitlines():
        if "'" in line:
            yuyv = "'YUYV'" in line
        m = re.search(r"Size: Discrete (\d+)x(\d+)", line)
        if m and yuyv:
            sizes.append((int(m[1]), int(m[2])))
    return max(sizes, key=lambda s: s[0] * s[1]) if sizes else (1632, 1224)


class Cam:
    def __init__(self):
        self.lock = threading.Lock()
        self.img = None            # processed frame, uint8 HxW
        self.n = 0
        self.fps = 0.0
        self.dev = None
        self.sensor = {}           # SENSORS entry + chip
        self.msg = "시작하는 중"
        self.ring, self.sum = [], None
        self.gain = None           # flat-field gain, float32 HxW
        self.flat_acc, self.flat_left = None, 0
        try:
            self.gain = np.load(os.path.join(A.data, "flat.npy"))
        except (OSError, ValueError):
            pass

    def feed(self, y):
        with cfg_lock:
            navg, flat = cfg["avg"], cfg["flat"]
        if self.flat_left:
            self.flat_acc += y
            self.flat_left -= 1
            if not self.flat_left:
                acc = np.maximum(self.flat_acc, 1.0)
                self.gain = (acc.mean() / acc).astype(np.float32)
                np.save(os.path.join(A.data, "flat.npy"), self.gain)
                log("flat-field reference taken, mean", round(float(acc.mean() / FLAT_FRAMES), 1))
        if self.sum is not None and self.sum.shape != y.shape:     # another camera size: start the average again
            self.ring, self.sum = [], None
        self.ring.append(y)
        self.sum = y.astype(np.uint16) if self.sum is None else self.sum + y
        while len(self.ring) > navg:
            self.sum -= self.ring.pop(0)
        img = (self.sum // len(self.ring)).astype(np.uint8) if len(self.ring) > 1 else y
        if flat and self.gain is not None and self.gain.shape == img.shape:
            img = np.minimum(img * self.gain, 255).astype(np.uint8)
        with self.lock:
            self.img, self.n = img, self.n + 1

    def take_flat(self):
        self.flat_acc, self.flat_left = np.zeros((H, W), np.float32), FLAT_FRAMES

    def frames(self):
        """Yield Y planes (uint8 HxW) forever, from the camera or the --fake file."""
        global W, H
        if A.fake:
            W, H = map(int, A.size.split("x"))
            size, chip = W * H * 2, int(A.chip, 16)
            self.sensor = dict(SENSORS.get(chip, {}), chip=chip)
            data = open(A.fake, "rb").read()
            k = len(data) // size
            self.dev, self.msg = "fake", "streaming"
            i = 0
            while True:
                time.sleep(1 / 7.2)
                buf = data[(i % k) * size:(i % k + 1) * size]
                noise = np.random.randint(0, 3, (H, W), np.uint8)
                yield np.minimum(np.frombuffer(buf, np.uint8)[0::2].reshape(H, W).astype(np.uint16) + noise, 255).astype(np.uint8)
                i += 1
        while True:
            dev = next((("/dev/" + os.path.basename(v)) for v in sorted(glob.glob("/sys/class/video4linux/video*"))
                        if "/usb" in os.path.realpath(v + "/device")), None)
            if not dev:
                self.dev, self.msg = None, "현미경을 기다리는 중 · micro-USB 케이블을 빼야 USB 허브가 붙습니다"
                time.sleep(1)
                continue
            sol.find(dev)
            W, H = largest_yuyv(dev)
            size = W * H * 2
            try:
                chip = sol.diag().get("chipId", 0)
                self.sensor = dict(SENSORS.get(chip, {}), chip=chip)
                with cfg_lock:
                    if chip in SENSORS and cfg.get("chip") != chip:    # another kit: its sensor's exposure default
                        cfg.update(chip=chip, exp=SENSORS[chip]["exp"])
                        save_cfg()
                    sol.exposure(cfg["exp"])
            except OSError as e:
                log("sensor setup:", e)
            self.dev, self.msg = dev, "streaming"
            log("streaming from", dev, sol.path, f"{W}x{H}", self.sensor.get("name", "unknown sensor"))
            p = subprocess.Popen(["v4l2-ctl", "-d", dev, f"--set-fmt-video=width={W},height={H},pixelformat=YUYV",
                                  "--stream-mmap=4", "--stream-to=-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            try:
                while True:
                    buf = p.stdout.read(size)
                    if len(buf) < size:
                        break
                    yield np.frombuffer(buf, np.uint8)[0::2].reshape(H, W)
            finally:
                p.terminate()
                p.wait()
            self.msg = "스트림이 끊겼습니다 · 다시 연결하는 중"
            log("stream ended")
            time.sleep(1)

    def run(self):
        t0, n0 = time.time(), 0
        for y in self.frames():
            self.feed(y)
            if time.time() - t0 >= 2:
                self.fps, t0, n0 = (self.n - n0) / (time.time() - t0), time.time(), self.n


cam = Cam()


# ---------------------------------------------------------------- photos
def safe_name(s):
    return re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", s).strip("_")[:40]


def capture():
    with cam.lock:
        img = cam.img
    if img is None:
        raise OSError("영상 없음")
    with cfg_lock:
        meta = {k: cfg[k] for k in ("exp", "avg", "flat", "pixel_um", "sample", "note")}
    meta.update(time=time.strftime("%Y-%m-%d %H:%M:%S"), width=img.shape[1], height=img.shape[0],
                sensor=cam.sensor.get("name", ""),
                mean=round(float(img.mean()), 1), board=open("/etc/hostname").read().strip() if os.path.exists("/etc/hostname") else "")
    name = time.strftime("%Y%m%d-%H%M%S") + ("_" + safe_name(meta["sample"]) if meta["sample"] else "")
    with open(os.path.join(PHOTOS, name + ".png"), "wb") as f:
        f.write(png(img))
    with open(os.path.join(PHOTOS, name + ".thumb.png"), "wb") as f:
        f.write(png(img[::8, ::8]))
    with open(os.path.join(PHOTOS, name + ".json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False)
    log("photo", name)
    return name


def photos():
    out = []
    for j in sorted(glob.glob(os.path.join(PHOTOS, "*.json")), reverse=True)[:60]:
        try:
            out.append({"name": os.path.basename(j)[:-5], **json.load(open(j))})
        except (OSError, ValueError):
            pass
    return out


# ---------------------------------------------------------------- HTTP
frame_cache = {}
diag_cache = {"t": 0, "d": {}}


def frame_png(q):
    """PNG of the current frame: region x,y,w,h in full-size pixels, every step-th pixel (step 2 = 2x2 mean)."""
    with cam.lock:
        img, n = cam.img, cam.n
    if img is None:
        return None
    g = lambda k, d: int(q.get(k, [d])[0])
    step = 2 if g("step", 2) == 2 else 1
    ih, iw = img.shape
    x, y = max(0, min(iw - 2, g("x", 0))) & ~1, max(0, min(ih - 2, g("y", 0))) & ~1
    w, h = max(2, min(iw - x, g("w", iw))) & ~1, max(2, min(ih - y, g("h", ih))) & ~1
    key = (n, x, y, w, h, step)
    if key not in frame_cache:
        r = img[y:y + h, x:x + w]
        if step == 2:
            r = ((r[0::2, 0::2].astype(np.uint16) + r[1::2, 0::2] + r[0::2, 1::2] + r[1::2, 1::2] + 2) >> 2).astype(np.uint8)
        if len(frame_cache) > 8:
            frame_cache.clear()
        frame_cache[key] = png(r)
    return frame_cache[key], (x, y, w, h, step, n)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200, extra=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path
        try:
            if p in ("/", "/index.html"):
                self.send(open(os.path.join(HERE, "scope.html"), "rb").read(), "text/html; charset=utf-8")
            elif p == "/frame.png":
                after, t_end = int(q.get("after", [-1])[0]), time.time() + 1
                while cam.n <= after and time.time() < t_end:     # long poll: one PNG per new frame, not per request
                    time.sleep(0.02)
                r = frame_png(q)
                if r is None:
                    self.send({"error": cam.msg}, code=503)
                else:
                    self.send(r[0], "image/png", extra=[("X-Region", ",".join(map(str, r[1])))])
            elif p == "/stat":
                if time.time() - diag_cache["t"] > 1 and cam.dev:
                    try:
                        diag_cache["d"] = sol.diag()
                    except OSError:
                        diag_cache["d"] = {}
                    diag_cache["t"] = time.time()
                with cfg_lock:
                    c = dict(cfg)
                self.send({"n": cam.n, "fps": round(cam.fps, 2), "dev": cam.dev, "msg": cam.msg, "cfg": c,
                           "flat_ready": cam.gain is not None and cam.gain.shape == (H, W), "flat_left": cam.flat_left,
                           "diag": diag_cache["d"], "sensor": cam.sensor, "size": [W, H],
                           "time": time.strftime("%Y-%m-%d %H:%M:%S")})
            elif p == "/set":
                with cfg_lock:
                    for k, v in q.items():
                        v = v[0]
                        if k == "exp" and v.isdigit():
                            cfg["exp"] = max(1, min(10000, int(v)))
                            sol.exposure(cfg["exp"])
                        elif k == "avg" and v in ("1", "2", "4", "8", "16"):
                            cfg["avg"] = int(v)
                        elif k == "flat":
                            cfg["flat"] = v == "1"
                        elif k == "pixel_um":
                            cfg["pixel_um"] = max(0.1, min(50.0, float(v)))
                        elif k in ("sample", "note"):
                            cfg[k] = v[:80]
                    save_cfg()
                    self.send(cfg)
            elif p == "/flat/take":
                cam.take_flat()
                self.send({"frames": FLAT_FRAMES})
            elif p == "/reg":
                reg = int(q["r"][0], 16)
                if "v" in q:
                    sol.reg_write(reg, int(q["v"][0], 16))
                self.send({"reg": f"0x{reg:04x}", "value": f"0x{sol.reg_read(reg):04x}"})
            elif p == "/capture":
                self.send({"name": capture()})
            elif p == "/photos":
                self.send(photos())
            elif p.startswith("/photos/"):
                f = urllib.parse.unquote(p[8:])
                path = os.path.join(PHOTOS, f)
                if "/" in f or not os.path.isfile(path):
                    self.send({"error": "없음"}, code=404)
                else:
                    self.send(open(path, "rb").read(), "image/png" if f.endswith(".png") else "application/json")
            elif p == "/reload":
                self.send({"reload": True})
                open(os.path.join(A.ctl, "reload"), "w").close()
            elif p == "/quit":
                self.send({"quit": True})
                open(os.path.join(A.ctl, "stop"), "w").close()
            else:
                self.send({"error": "없음"}, code=404)
        except (OSError, ValueError, KeyError) as e:
            self.send({"error": str(e)}, code=400)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, addr):          # browsers closing a long-poll are not errors
        if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, addr)


def main():
    os.makedirs(A.ctl, exist_ok=True)
    for f in ("reload",):
        if os.path.exists(os.path.join(A.ctl, f)):
            os.remove(os.path.join(A.ctl, f))
    srv = Server(("", A.port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=cam.run, daemon=True).start()
    log("http on", A.port, "fake" if A.fake else "")
    while not any(os.path.exists(os.path.join(A.ctl, f)) for f in ("stop", "reload")):
        time.sleep(0.5)
    log("exit for", [f for f in ("stop", "reload") if os.path.exists(os.path.join(A.ctl, f))])
    srv.shutdown()
    srv.server_close()
    os._exit(0)                     # the capture thread may sit in a pipe read; v4l2-ctl dies with the pipe


if __name__ == "__main__":
    main()
