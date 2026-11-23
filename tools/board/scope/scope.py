#!/usr/bin/env python3
"""e850-scope: SOL lensless microscope (CX3 UVC, repo sol-cx3-uvc) on the E850-96 USB host port -> web viewer on :8090

Runs as root under scope-run.sh, which owns USB host mode and restarts this program when it exits (/reload).
Capture: v4l2-ctl streams the camera's largest YUYV size (Hi-846 kit 1632x1224, Hi-5031Q kit 2016x1536) into a pipe;
the Y plane goes through frame averaging and flat-field correction here. The sensor is told by the chip ID in the
firmware's diag (SENSORS), so one viewer serves both kits, including firmware already in a kit's EEPROM. The browser (scope.html) does zoom, display LUT, histogram, measurement and the data bar.
Sensor access uses the firmware's vendor requests (sol-cx3-uvc fw/uvc/app.h) over usbfs while uvcvideo streams.
Every photo is analysed (object count and size, scope_rec.py, OpenCV) and kept in the record (SQLite scope.db);
automatic capture takes one every tl_every seconds with its own LED, exposure, averaging and flat-field steps.

  scope.py [--fake FILE.yuv [--size WxH] [--chip 5031]] [--data DIR] [--port N]
  --fake loops raw YUYV frames (default 1632x1224, chip 4608) from a file instead of the camera (PC without a board).
"""
import argparse, ctypes, fcntl, glob, http.server, json, math, os, queue, re, socketserver, struct, subprocess, sys, threading, time
import urllib.parse, zlib
import numpy as np
import scope_rec
import scope_still

os.environ.setdefault("TZ", "Asia/Seoul")        # the board runs in UTC; photo names and the page clock use lab time
time.tzset()
W, H = 1632, 1224                                 # current frame size, from the camera (largest_yuyv) or --size
# By firmware chip ID: line length clock (MHz), the registers the page explains, default exposure (100 us units),
# output pixel in um (sensor pitch x 2, the firmware averages pixel pairs; Hi-5031Q 0.56 um summed 2 x 2 first).
SENSORS = {
    0x4608: {"name": "Hi-846", "mp": 8, "mhz": 288, "llp": "0x0008", "fll": "0x0006", "expReg": "0x0074", "exp": 150, "um": 2.24,
             "bayer": None, "black": 64,
             "regs": [["0f16", "칩 ID"], ["0006", "프레임 길이(FLL)"], ["0008", "줄 길이(LLP)"], ["0074", "노출(줄 수)"],
                      ["0a00", "스트림 모드"]]},
    0x5031: {"name": "Hi-5031Q", "mp": 50, "mhz": 124, "llp": "0x0206", "fll": "0x0210", "expReg": "0x020C", "exp": 2000, "um": 2.24,
             "bayer": None, "black": 64,      # no colour filter: equal 2x2 site means under LED and room light
             "regs": [["0716", "칩 ID"], ["0210", "프레임 길이(FLL)"], ["0206", "줄 길이(LLP)"], ["020c", "노출(줄 수)"],
                      ["020a", "아날로그 이득(값/16+1 배)"], ["0b00", "스트림 모드"]]},
    0x1634: {"name": "Hi-1634", "mp": 16, "mhz": 80, "llp": "0x0206", "fll": "0x020E", "expReg": "0x020A", "exp": 200, "um": 2.0,
             "bayer": None, "black": 64,
             "regs": [["0716", "칩 ID"], ["020e", "프레임 길이(FLL)"], ["0206", "줄 길이(LLP)"], ["020a", "노출(줄 수)"],
                      ["0b12", "출력 폭"], ["0b00", "스트림 모드"]]},
}
HERE = os.path.dirname(os.path.abspath(__file__))
DIAG_FIELDS = ("magic chipId initStatus usbSpeed frames goodFrames lastFrameBytes buffers commitFail dmaResets "
               "streamStarts lastFrameMs llp fll exposureLines mipiErrors framePeriodMs exposure100us led truncFrames "
               "vBadRows vFirstCol convMs inFrameBytes inFrameMs stillFails").split()
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
cfg = {"exp": 150, "led": 0, "avg": 1, "flat": False, "pixel_um": 2.24, "sample": "", "note": "",
       # automatic capture (tl_), analysis (an_) and record (rec_)
       "tl_on": False, "tl_every": 300, "tl_hours": 24, "tl_start": 0, "tl_run": None,
       "tl_led": True, "tl_ae": True, "tl_flat": True, "tl_avg4": False,
       "an_dark": True, "an_min_um": 5.0, "an_max_um": 500.0, "an_split": True, "an_track": False, "an_sens": 3,
       "rec_png": True, "rec_ov": True, "rec_gb": 30,
       "shot_res": "live", "shot_color": "grey"}      # photo: live frame or high-resolution still (grab_still)
BOOL_KEYS = ("tl_led", "tl_ae", "tl_flat", "tl_avg4", "an_dark", "an_split", "an_track", "rec_png", "rec_ov")
AN_KEYS = ("an_dark", "an_min_um", "an_max_um", "an_split", "an_track", "an_sens")
SENS_K = {1: 5.0, 2: 4.0, 3: 3.0, 4: 2.5, 5: 2.0}      # sensitivity -> threshold in noise units (scope_rec.analyze)
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


# ---------------------------------------------------------------- PNG (grey: zlib, 75 ms a live frame vs OpenCV 100 ms)
def png(gray):
    """PNG of a grey (HxW) or RGB (HxWx3, only made when OpenCV is there) uint8 image."""
    if gray.ndim == 3:
        cv2 = scope_rec.cv2
        bgr = cv2.cvtColor(np.ascontiguousarray(gray), cv2.COLOR_RGB2BGR)
        return cv2.imencode(".png", bgr, [cv2.IMWRITE_PNG_COMPRESSION, 1])[1].tobytes()
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

    def led(self, level):
        self.ctl(0x40, 0xD5, level, 0, 0)          # LED PWM 0..255 (fw LedSet, LOG 11), 0 = off


sol = Sol()


# ---------------------------------------------------------------- capture and processing
def camera_sizes(dev):
    """Largest YUYV frame size the camera offers (frame 1 of the SOL firmware), and the GREY still format of the
    still-capture firmware as (row bytes, rows), None with older firmware."""
    out = subprocess.run(["v4l2-ctl", "-d", dev, "--list-formats-ext"], capture_output=True, text=True).stdout
    sizes, grey, fmt = [], None, ""
    for line in out.splitlines():
        if "'" in line:
            fmt = line.split("'")[1]
        m = re.search(r"Size: Discrete (\d+)x(\d+)", line)
        if m and fmt == "YUYV":
            sizes.append((int(m[1]), int(m[2])))
        elif m and fmt == "GREY":
            grey = (int(m[1]), int(m[2]))
    return (max(sizes, key=lambda s: s[0] * s[1]) if sizes else (1632, 1224)), grey


class Cam:
    def __init__(self):
        self.lock = threading.Lock()
        self.img = None            # processed frame, uint8 HxW
        self.raw = None            # last camera frame before averaging and flat-field
        self.n = 0
        self.fps = 0.0
        self.dev = None
        self.sensor = {}           # SENSORS entry + chip
        self.msg = "시작하는 중"
        self.ring, self.sum = [], None
        self.gain = None           # flat-field gain, float32 HxW
        self.flat_acc, self.flat_left = None, 0
        self.still = None          # GREY still format (row bytes, rows), None = this firmware has none
        self.busy = ""             # what a high-resolution still is doing, for the page
        self.pause = threading.Event()     # set by grab_still: live capture lets go of the camera
        self.paused = threading.Event()    # set by frames() once it has
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
            self.img, self.raw, self.n = img, y, self.n + 1

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
            while self.pause.is_set():          # a high-resolution still has the camera (grab_still)
                self.paused.set()
                time.sleep(0.05)
            dev = next((("/dev/" + os.path.basename(v)) for v in sorted(glob.glob("/sys/class/video4linux/video*"))
                        if "/usb" in os.path.realpath(v + "/device")), None)
            if not dev:
                self.dev, self.still, self.msg = None, None, "현미경을 기다리는 중 · micro-USB 케이블을 빼야 USB 허브가 붙습니다"
                time.sleep(1)
                continue
            sol.find(dev)
            (W, H), self.still = camera_sizes(dev)
            size = W * H * 2
            try:
                chip = sol.diag().get("chipId", 0)
                self.sensor = dict(SENSORS.get(chip, {}), chip=chip)
                with cfg_lock:
                    if chip in SENSORS and cfg.get("chip") != chip:    # another module: its exposure and pixel size
                        cfg.update(chip=chip, exp=SENSORS[chip]["exp"], pixel_um=SENSORS[chip]["um"])
                        save_cfg()
                    sol.exposure(cfg["exp"])
                    sol.led(cfg["led"])                 # the firmware starts with the LED off
            except OSError as e:
                log("sensor setup:", e)
            self.dev, self.msg = dev, "streaming"
            log("streaming from", dev, sol.path, f"{W}x{H}", self.sensor.get("name", "unknown sensor"))
            p = subprocess.Popen(["v4l2-ctl", "-d", dev, f"--set-fmt-video=width={W},height={H},pixelformat=YUYV",
                                  "--stream-mmap=4", "--stream-to=-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            last = [time.time()]

            def watch():            # an unplugged kit (module swap) leaves v4l2-ctl waiting on the gone node forever
                while p.poll() is None:
                    if self.pause.is_set() or not os.path.exists(dev) or time.time() - last[0] > 20:   # 20 s: frames every <= ~1.5 s
                        p.kill()
                    time.sleep(1)
            threading.Thread(target=watch, daemon=True).start()
            try:
                while not self.pause.is_set():
                    buf = p.stdout.read(size)
                    if len(buf) < size:
                        break
                    last[0] = time.time()
                    yield np.frombuffer(buf, np.uint8)[0::2].reshape(H, W)
            finally:
                p.terminate()
                p.wait()
            if self.pause.is_set():
                continue
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


rec = scope_rec.Rec(os.path.join(A.data, "scope.db"))


def save_photo(img, run_id=None, over=None, wait=True):
    """PNG + thumbnail + JSON in photos/, objects into the record. over: settings of an automatic shot at the moment
    it was taken (exp, led, avg, flat). wait=False: the JSON says pending and the analysis runs in an_loop (a still
    takes ~20 s to analyse). Returns the photo's JSON with its name."""
    with cfg_lock:
        c = dict(cfg)
    meta = {k: c[k] for k in ("exp", "led", "avg", "flat", "pixel_um", "sample", "note")}
    meta.update(res="live")
    meta.update(over or {})
    if meta["res"] == "still":                       # stills keep every row and pixel: half the live pixel size
        meta["pixel_um"] = round(c["pixel_um"] / 2, 4)
    grey = img if img.ndim == 2 else scope_rec.cv2.cvtColor(img, scope_rec.cv2.COLOR_RGB2GRAY)
    t = time.time()
    lt = time.localtime(t)
    meta.update(time=time.strftime("%Y-%m-%d %H:%M:%S", lt), width=img.shape[1], height=img.shape[0],
                color=img.ndim == 3, sensor=cam.sensor.get("name", ""), run=run_id,
                mean=round(float(grey.mean()), 1), board=open("/etc/hostname").read().strip() if os.path.exists("/etc/hostname") else "")
    base = time.strftime("%Y%m%d-%H%M%S", lt) + ("_" + safe_name(meta["sample"]) if meta["sample"] else "")
    name, i = base, 2
    while os.path.exists(os.path.join(PHOTOS, name + ".json")):     # a manual and an automatic shot in one second
        name, i = f"{base}-{i}", i + 1
    keep_png = run_id is None or c["rec_png"]                         # a manual shot always keeps its PNG
    if keep_png:
        with open(os.path.join(PHOTOS, name + ".png"), "wb") as f:
            f.write(png(img))
    with open(os.path.join(PHOTOS, name + ".thumb.png"), "wb") as f:
        f.write(png(img[::8, ::8]))
    meta.update(png=keep_png)
    if wait:
        return finish_photo(name, grey, meta, c, run_id, t)
    meta.update(pending=True, ov=False, count=None, mean_d_um=None, total_area_um2=None)
    write_json(name, meta)
    an_q.put((name, grey, dict(meta), c, run_id, t))       # blocks while 4 wait: the next capture answers later
    log("photo", name, "saved, analysis queued")
    return dict(meta, name=name)


def write_json(name, meta):
    tmp = os.path.join(PHOTOS, name + ".json.tmp")
    with open(tmp, "w") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(PHOTOS, name + ".json"))     # /photos never reads half a file


def finish_photo(name, grey, meta, c, run_id, t):
    """Analysis, contour JPEG, final JSON and record of a saved photo."""
    res, ta = None, time.time()
    if scope_rec.HAVE_CV:
        try:
            res = scope_rec.analyze(grey, meta["pixel_um"], c["an_dark"], c["an_min_um"], c["an_max_um"], c["an_split"],
                                    SENS_K.get(c["an_sens"], 3.0))
            if c["rec_ov"]:
                with open(os.path.join(PHOTOS, name + ".ov.jpg"), "wb") as f:
                    f.write(scope_rec.overlay_jpg(grey, res))
        except Exception as e:                                      # an analysis fault must not lose the photo
            log("analysis:", e)
            res = None
    meta.pop("pending", None)
    meta.update(ov=bool(res and c["rec_ov"]), count=res["count"] if res else None,
                mean_d_um=res["mean_d_um"] if res else None, total_area_um2=res["total_area_um2"] if res else None)
    write_json(name, meta)
    try:
        rec.add_photo(run_id, name, dict(meta, t=t, opts={k: c[k] for k in AN_KEYS}), res, meta["pixel_um"],
                      track=bool(run_id and c["an_track"]))
    except Exception as e:                                          # the files are written; keep going
        log("record:", e)
    log("photo", name, "objects", meta["count"], f"analysis {time.time() - ta:.1f} s")
    return dict(meta, name=name)


an_q = queue.Queue(maxsize=4)


def an_loop():
    """Analyses of saved photos, in order. Photos still pending from before a restart first: from their PNG, with
    the analysis settings of now; one without a PNG cannot be analysed and is left without a count."""
    for j in sorted(glob.glob(os.path.join(PHOTOS, "*.json"))):
        try:
            meta = json.load(open(j))
            if not meta.get("pending"):
                continue
            name = os.path.basename(j)[:-5]
            grey = scope_rec.cv2.imread(os.path.join(PHOTOS, name + ".png"), 0) if scope_rec.HAVE_CV else None
            with cfg_lock:
                c = dict(cfg)
            if grey is None:
                meta.pop("pending")
                write_json(name, meta)
            else:
                finish_photo(name, grey, meta, c, meta.get("run"), time.mktime(time.strptime(meta["time"], "%Y-%m-%d %H:%M:%S")))
        except Exception as e:
            log("pending analysis", j, e)
    while True:
        job = an_q.get()
        try:
            finish_photo(*job)
        except Exception as e:                   # finish_photo keeps the photo; a fault here must not stop the loop
            log("analysis", job[0], e)


def wait_frames(k, timeout=8.0):
    n0, t_end = cam.n, time.time() + timeout
    while cam.n < n0 + k:
        if time.time() > t_end:
            return False
        time.sleep(0.05)
    return True


def auto_exposure():
    """Exposure for a mean grey of ~118 on the raw frames, the page's rule (scope.html bAE)."""
    for _ in range(8):
        if not wait_frames(3):
            raise OSError("영상 없음")
        with cam.lock:
            m = float(cam.raw[::4, ::4].mean())
        if abs(m - 118) < 10:
            return
        with cfg_lock:
            cfg["exp"] = max(1, min(10000, round(cfg["exp"] * (0.4 if m >= 250 else 118 / max(m, 4)))))
            sol.exposure(cfg["exp"])


def grab(n, flat):
    """Mean of the next n raw frames (not the page's running average), flat-field corrected when asked and a
    reference of this size exists. Returns (image, flat applied)."""
    acc, got, last, t_end = None, 0, cam.n, time.time() + 10 + 3 * n
    while got < n:
        if time.time() > t_end:
            raise OSError("영상 없음")
        if cam.n != last:
            with cam.lock:
                y, last = cam.raw, cam.n
            if acc is not None and acc.shape != y.shape:               # the camera changed mid-way
                acc, got = None, 0
            acc = y.astype(np.float32) if acc is None else acc + y
            got += 1
        time.sleep(0.02)
    img = acc / n
    flat = bool(flat and cam.gain is not None and cam.gain.shape == img.shape)
    if flat:
        img *= cam.gain
    return np.clip(img + 0.5, 0, 255).astype(np.uint8), flat


still_lock = threading.RLock()      # one shot at a time: a manual still and an automatic shot wait for each other


def grab_still(color, n=1, flat=False):
    """Mean of n high-resolution stills (UVC format 2, scope_still), developed; live capture stops meanwhile.
    Colour only when the sensor has a colour filter (SENSORS bayer). Returns (image, flat applied)."""
    with still_lock:
        if not scope_rec.HAVE_CV:
            raise OSError(scope_rec.CV_ERR)
        if not cam.dev or not cam.still:
            raise OSError("현미경 없음" if not cam.dev else "이 키트 펌웨어는 고해상도 사진을 주지 않습니다")
        dev, (rb, rows), s = cam.dev, cam.still, cam.sensor
        cam.busy = "고해상도 촬영 중"
        cam.paused.clear()                    # set again only once live capture has let go (frames)
        cam.pause.set()
        try:
            if not cam.paused.wait(10):
                raise OSError("실시간 영상을 멈추지 못했습니다")
            f, t0 = rb * rows, time.time()
            try:
                out = subprocess.run(["v4l2-ctl", "-d", dev, f"--set-fmt-video=width={rb},height={rows},pixelformat=GREY",
                                      "--stream-mmap=2", f"--stream-count={n}", "--stream-to=-"],
                                     capture_output=True, timeout=15 + 3 * n).stdout
            except subprocess.TimeoutExpired:
                out = b""
            log(f"still {n} x {rb}x{rows}: {len(out)} B in {time.time() - t0:.1f} s")
            if len(out) != n * f:
                raise OSError("고해상도 사진을 받지 못했습니다")
            raw = None
            for i in range(n):                        # 10-bit sums of up to 64 stills fit in uint16
                r = scope_still.unpack_raw10(scope_still.reorder(out[i * f:(i + 1) * f], rows, rb))
                raw = r if raw is None else raw + r
            if n > 1:
                raw = ((raw + n // 2) // n).astype(np.uint16)
            flat = bool(flat and cam.gain is not None and cam.gain.shape == (H, W))
            gain = scope_rec.cv2.resize(cam.gain, (raw.shape[1], raw.shape[0])) if flat else None
            return scope_still.develop(raw, s.get("bayer"), s.get("black", 64), color, gain), flat
        finally:
            cam.pause.clear()
            cam.busy = ""


# ---------------------------------------------------------------- automatic capture
tl = {"next": 0, "last": 0, "n": 0, "msg": ""}


def tl_stop(msg):
    with cfg_lock:
        run, cfg["tl_on"] = cfg["tl_run"], False
        save_cfg()
    if run:
        rec.end_run(run)
    tl["msg"] = msg
    log("auto capture stopped:", msg)


def photos_bytes():
    return sum(e.stat().st_size for e in os.scandir(PHOTOS) if e.is_file())


def disk_full(limit_gb):
    """Why automatic capture must stop for space, or None. Nothing is ever deleted automatically."""
    used = photos_bytes()
    st = os.statvfs(PHOTOS)
    if used > limit_gb * 1e9:
        return f"사진이 보관 한도 {limit_gb} GB를 넘어 자동 촬영을 멈췄습니다"
    if st.f_bavail * st.f_frsize < 2e9:
        return "남은 저장 공간이 2 GB 아래라 자동 촬영을 멈췄습니다"
    return None


def shoot_auto(c):
    """One automatic photo: LED on, auto exposure, average, flat-field as chosen; LED and exposure back after."""
    with still_lock:
        with cfg_lock:
            exp0, led0 = cfg["exp"], cfg["led"]
        try:
            if c["tl_led"] and led0 < 255:
                with cfg_lock:
                    cfg["led"] = 255
                    sol.led(255)
                wait_frames(3)
            if c["tl_ae"]:
                auto_exposure()
            n = 4 if c["tl_avg4"] else 1
            if c["shot_res"] == "still":
                img, flat = grab_still(c["shot_color"] == "color", n, c["tl_flat"])
            else:
                img, flat = grab(n, c["tl_flat"])
            with cfg_lock:
                over = {"exp": cfg["exp"], "led": cfg["led"], "avg": n, "flat": flat, "res": c["shot_res"]}
        finally:
            with cfg_lock:
                changed = (cfg["exp"], cfg["led"]) != (exp0, led0)
                cfg["exp"], cfg["led"] = exp0, led0
                if changed:
                    try:
                        sol.exposure(exp0)
                        sol.led(led0)
                    except OSError:
                        pass
    return save_photo(img, c["tl_run"], over)


def tl_loop():
    """Shots on the slots tl_start + k * tl_every; a slot missed (busy, camera away) is skipped, not made up."""
    while True:
        time.sleep(0.5)
        with cfg_lock:
            c = dict(cfg)
        if not c["tl_on"]:
            tl["next"] = 0
            continue
        now = time.time()
        if c["tl_hours"] and now >= c["tl_start"] + c["tl_hours"] * 3600:
            tl_stop("정한 기간이 끝나 자동 촬영을 마쳤습니다")
            continue
        if not tl["last"]:                         # just started, or the service restarted in the middle of a run
            tl["last"] = rec.last_t(c["tl_run"]) or 0
            tl["n"] = rec.count(c["tl_run"])
        every = c["tl_every"]
        slot = math.floor((now - c["tl_start"]) / every)
        done = math.floor((tl["last"] - c["tl_start"]) / every) if tl["last"] >= c["tl_start"] else -1
        if slot <= done:
            tl["next"] = c["tl_start"] + (slot + 1) * every
            continue
        tl["last"] = now
        why = disk_full(c["rec_gb"])
        if why:
            tl_stop(why)
            continue
        try:
            shoot_auto(c)
            tl["msg"] = ""
        except Exception as e:                    # camera away, sensor error: try again on the next slot
            tl["msg"] = f"{time.strftime('%H:%M:%S')} 촬영 실패: {e}"
            log("auto capture:", e)
        tl["n"] = rec.count(c["tl_run"])


def photos():
    out = []
    for j in sorted(glob.glob(os.path.join(PHOTOS, "*.json")), reverse=True)[:60]:
        try:
            out.append({"name": os.path.basename(j)[:-5], **json.load(open(j))})
        except (OSError, ValueError):
            pass
    return out


def an_opts(q):
    """Analysis settings: cfg, with the page's draft values from the query on top (cfg itself is not changed)."""
    with cfg_lock:
        c = dict(cfg)
    g = lambda k: q.get(k, [None])[0]
    if g("dark") in ("0", "1"):
        c["an_dark"] = g("dark") == "1"
    if g("split") in ("0", "1"):
        c["an_split"] = g("split") == "1"
    if g("sens") in ("1", "2", "3", "4", "5"):
        c["an_sens"] = int(g("sens"))
    for k, key in (("min", "an_min_um"), ("max", "an_max_um")):
        if g(k):
            c[key] = max(0.5, min(5000.0, float(g(k))))
    return c


def analyze_c(img, c):
    return scope_rec.analyze(img, c["pixel_um"], c["an_dark"], c["an_min_um"], c["an_max_um"], c["an_split"],
                             SENS_K.get(c["an_sens"], 3.0))


def an_json(res, pixel_um, cap=3000):
    """Objects as circles for the page: [x, y, equivalent diameter um], the largest first."""
    objs = sorted(res["objs"], key=lambda o: -o[2])[:cap]
    return {"count": res["count"], "mean_d_um": res["mean_d_um"], "total_area_um2": res["total_area_um2"],
            "truncated": res["truncated"] or len(res["objs"]) > cap,
            "objs": [[int(x), int(y), round(2 * math.sqrt(a / math.pi) * pixel_um, 1)] for x, y, a in objs]}


disk_cache = {"t": 0, "d": None}


def disk_info():
    if time.time() - disk_cache["t"] > 30:
        st = os.statvfs(PHOTOS)
        disk_cache.update(t=time.time(), d={"free_gb": round(st.f_bavail * st.f_frsize / 1e9, 2),
                                            "photos_gb": round(photos_bytes() / 1e9, 3),
                                            "n": sum(1 for e in os.scandir(PHOTOS) if e.name.endswith(".json"))})
    return disk_cache["d"]


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
                           "still": [cam.still[0] * 4 // 5, cam.still[1]] if cam.still else None,
                           "still_color": bool(cam.sensor.get("bayer")), "busy": cam.busy,
                           "tl": {k: tl[k] for k in ("next", "n", "msg")}, "cv": {"have": scope_rec.HAVE_CV, "err": scope_rec.CV_ERR},
                           "disk": disk_info(),
                           "time": time.strftime("%Y-%m-%d %H:%M:%S"), "now": time.time()})
            elif p == "/set":
                with cfg_lock:      # blank values kept: an emptied sample name or note must clear it
                    for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items():
                        v = v[0]
                        if k == "exp" and v.isdigit():
                            cfg["exp"] = max(1, min(10000, int(v)))
                            sol.exposure(cfg["exp"])
                        elif k == "led" and v.isdigit():
                            cfg["led"] = min(255, int(v))
                            sol.led(cfg["led"])
                        elif k == "avg" and v in ("1", "2", "4", "8", "16"):
                            cfg["avg"] = int(v)
                        elif k == "flat":
                            cfg["flat"] = v == "1"
                        elif k == "pixel_um":
                            cfg["pixel_um"] = max(0.1, min(50.0, float(v)))
                        elif k in ("sample", "note"):
                            cfg[k] = v[:80]
                        elif k == "tl_every" and v.isdigit():
                            cfg[k] = max(10, min(86400, int(v)))
                        elif k == "tl_hours":
                            cfg[k] = max(0.0, min(24 * 30.0, float(v)))      # 0 = until stopped
                        elif k in ("an_min_um", "an_max_um"):
                            cfg[k] = max(0.5, min(5000.0, float(v)))
                        elif k == "an_sens" and v in ("1", "2", "3", "4", "5"):
                            cfg[k] = int(v)
                        elif k == "shot_res" and v in ("live", "still"):
                            cfg[k] = v
                        elif k == "shot_color" and v in ("grey", "color"):
                            cfg[k] = v
                        elif k == "rec_gb" and v.isdigit():
                            cfg[k] = max(1, min(45, int(v)))
                        elif k in BOOL_KEYS:
                            cfg[k] = v == "1"
                        elif k == "tl_on":
                            on = v == "1"
                            if on and not cfg["tl_on"]:          # a new run: its settings go with it
                                cfg["tl_run"] = rec.new_run(cfg["sample"], cfg["tl_every"],
                                                            {kk: cfg[kk] for kk in cfg if kk.startswith(("tl_", "an_", "rec_")) or kk == "pixel_um"})
                                cfg["tl_start"] = time.time()
                                tl.update(last=0, n=0, msg="")
                            elif not on and cfg["tl_on"]:
                                rec.end_run(cfg["tl_run"])
                                tl["msg"] = "멈춤"
                            cfg["tl_on"] = on
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
                with cfg_lock:
                    c = dict(cfg)
                if c["shot_res"] == "still":
                    img, flat = grab_still(c["shot_color"] == "color", 1, c["flat"])
                    j = save_photo(img, over={"avg": 1, "flat": flat, "res": "still"}, wait=False)
                else:
                    with cam.lock:
                        img = cam.img
                    if img is None:
                        raise OSError("영상 없음")
                    j = save_photo(img, wait=False)
                self.send(j)
            elif p == "/analyze/preview":
                if not scope_rec.HAVE_CV:
                    return self.send({"error": scope_rec.CV_ERR}, code=503)
                with cam.lock:
                    img = cam.img
                if img is None:
                    raise OSError("영상 없음")
                c = an_opts(q)
                if "fmt" not in q:                                # contour JPEG, as before
                    res = analyze_c(img, c)
                    return self.send(scope_rec.overlay_jpg(img, res), "image/jpeg",
                                     extra=[("X-Count", str(res["count"])), ("X-Mean-D", str(res["mean_d_um"] or ""))])
                self.send(an_json(analyze_c(img, c), c["pixel_um"]))
            elif p.startswith("/rec/"):
                r = q.get("run", [""])[0]
                run = int(r) if r.isdigit() else None             # none = manual photos
                what = p[5:]
                if what == "runs":
                    self.send(rec.runs())
                elif what == "photos":
                    self.send(rec.photos(run))
                elif what == "objects":
                    self.send(rec.objects(int(q["photo"][0])))
                elif what == "tracks":
                    self.send(rec.tracks(run))
                elif what == "csv":
                    kind = "objects" if q.get("what", [""])[0] == "objects" else "photos"
                    self.send(rec.csv(run, kind), "text/csv; charset=utf-8",
                              extra=[("Content-Disposition", f'attachment; filename="scope-{run or "manual"}-{kind}.csv"')])
                else:
                    self.send({"error": "없음"}, code=404)
            elif p == "/photos":
                self.send(photos())
            elif p.startswith("/photos/"):
                f = urllib.parse.unquote(p[8:])
                path = os.path.join(PHOTOS, f)
                if "/" in f or not os.path.isfile(path):
                    self.send({"error": "없음"}, code=404)
                else:
                    self.send(open(path, "rb").read(), "image/png" if f.endswith(".png") else
                              "image/jpeg" if f.endswith(".jpg") else "application/json")
            elif p == "/reload":
                self.send({"reload": True})
                open(os.path.join(A.ctl, "reload"), "w").close()
            elif p == "/quit":
                self.send({"quit": True})
                open(os.path.join(A.ctl, "stop"), "w").close()
            else:
                self.send({"error": "없음"}, code=404)
        except (ConnectionResetError, BrokenPipeError):
            raise
        except Exception as e:                       # OpenCV and SQLite errors too: an answer, not a dropped request
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
    threading.Thread(target=tl_loop, daemon=True).start()
    threading.Thread(target=an_loop, daemon=True).start()
    log("http on", A.port, "fake" if A.fake else "")
    while not any(os.path.exists(os.path.join(A.ctl, f)) for f in ("stop", "reload")):
        time.sleep(0.5)
    log("exit for", [f for f in ("stop", "reload") if os.path.exists(os.path.join(A.ctl, f))])
    srv.shutdown()
    srv.server_close()
    os._exit(0)                     # the capture thread may sit in a pipe read; v4l2-ctl dies with the pipe


if __name__ == "__main__":
    main()
