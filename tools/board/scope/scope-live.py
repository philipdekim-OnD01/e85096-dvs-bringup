#!/usr/bin/env python3
"""scope-live.py (root; spike, throwaway): SOL microscope on the E850 USB host port -> live view on http://<board>:8090/

While it runs it holds the DWC3 in host mode and the USB-A port power switch on (gpa3-5 low). Needs e850-hubpwr.ko
loaded (PMIC LDO24 = LAN9514 hub supply) and the micro-USB cable out (the board's USB mux follows its VBUS).
Stops on SIGTERM, on GET /quit, or when /var/tmp/scope-live-stop appears; then it puts the USB gadget back.
Pages: /  live view;  /frame.png  816x612 preview;  /full.png  1632x1224 still;  /exp?v=N  exposure (100 us);  /stat
"""
import fcntl, glob, http.server, json, os, signal, socketserver, struct, subprocess, threading, time, urllib.parse, zlib
import numpy as np

G = "/sys/devices/platform/13600000.usb/"
W, H, PORT = 1632, 1224, 8090
STOP = "/var/tmp/scope-live-stop"
st = {"y": None, "n": 0, "fps": 0.0, "dev": None, "msg": "starting"}
cache = {}
quit_ev = threading.Event()


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def gpio_hold(chip, line, value):
    """Request one GPIO line as output (chardev v1 ioctl); it stays driven while the returned fd is open."""
    req = bytearray(364)
    struct.pack_into("<I", req, 0, line)
    struct.pack_into("<I", req, 256, 1 << 1)        # GPIOHANDLE_REQUEST_OUTPUT
    req[260] = value
    req[324:334] = b"e850-scope"
    struct.pack_into("<I", req, 356, 1)
    with open(f"/dev/gpiochip{chip}", "rb") as f:
        fcntl.ioctl(f, 0xC16CB403, req)             # GPIO_GET_LINEHANDLE_IOCTL
    return struct.unpack_from("<i", req, 360)[0]


def sysw(name, v):
    with open(G + name, "w") as f:
        f.write(v)


def otg_state():
    return open(G + "state").read().strip()


def png(gray):
    h, w = gray.shape
    raw = np.empty((h, w + 1), np.uint8)
    raw[:, 0] = 0
    raw[:, 1:] = gray
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw.tobytes(), 1)) + chunk(b"IEND", b""))


def uvc_node():
    for v in sorted(glob.glob("/sys/class/video4linux/video*")):
        if "/usb" in os.path.realpath(v + "/device"):
            return "/dev/" + os.path.basename(v)


def capture():
    size = W * H * 2
    while not quit_ev.is_set():
        dev = uvc_node()
        if not dev:
            st["msg"] = "현미경을 기다리는 중 (micro-USB 케이블을 빼야 허브가 붙습니다)"
            time.sleep(1)
            continue
        st["dev"], st["msg"] = dev, "streaming"
        log("streaming from", dev)
        p = subprocess.Popen(["v4l2-ctl", "-d", dev, f"--set-fmt-video=width={W},height={H},pixelformat=YUYV",
                              "--stream-mmap=4", "--stream-to=-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        t0, n0 = time.time(), st["n"]
        while not quit_ev.is_set():
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            st["y"] = np.frombuffer(buf, np.uint8)[0::2].reshape(H, W)
            st["n"] += 1
            if time.time() - t0 >= 3:
                st["fps"], t0, n0 = (st["n"] - n0) / (time.time() - t0), time.time(), st["n"]
        p.terminate()
        p.wait()
        log("stream ended")
        time.sleep(1)


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>SOL 현미경</title><style>body{margin:0;background:#111;color:#ddd;font:15px sans-serif}
#bar{padding:8px 12px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}img{display:block;width:100%%;max-width:1632px}
a,button{color:#fff;background:#335;border:0;padding:6px 10px;border-radius:4px;text-decoration:none}</style>
<div id=bar><b>SOL 현미경 (E850 보드, 임시 뷰어)</b><span id=s>…</span>
<label>노출 <input id=e type=range min=5 max=1000 value=150> <span id=ev>15.0 ms</span></label>
<a href=/full.png download>사진 저장 (1632×1224 PNG)</a></div><img id=v src=/frame.png>
<script>const v=document.getElementById('v'),s=document.getElementById('s'),e=document.getElementById('e'),ev=document.getElementById('ev');
v.onload=v.onerror=()=>setTimeout(()=>v.src='/frame.png?'+Date.now(),v.naturalWidth?0:1000);
setInterval(()=>fetch('/stat').then(r=>r.json()).then(j=>s.textContent=j.msg+' · '+j.fps.toFixed(1)+' fps · 프레임 '+j.n),1000);
e.oninput=()=>ev.textContent=(e.value/10).toFixed(1)+' ms';e.onchange=()=>fetch('/exp?v='+e.value);</script>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype, extra=()):
        self.send_response(200)
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
        y, n = st["y"], st["n"]
        if u.path == "/":
            self.send(PAGE.replace("%%", "%").encode(), "text/html; charset=utf-8")
        elif u.path in ("/frame.png", "/full.png"):
            if y is None:
                self.send_error(503)
                return
            key = (u.path, n)
            if key not in cache:
                cache.clear()
                cache[key] = png(y if u.path == "/full.png" else y[0::2, 0::2])
            extra = [("Content-Disposition", time.strftime('attachment; filename="scope-%Y%m%d-%H%M%S.png"'))] \
                if u.path == "/full.png" else []
            self.send(cache[key], "image/png", extra)
        elif u.path == "/exp" and q.get("v", [""])[0].isdigit() and st["dev"]:
            r = subprocess.run(["v4l2-ctl", "-d", st["dev"], "-c", f"exposure_time_absolute={int(q['v'][0])}"],
                               capture_output=True, text=True)
            self.send((r.stdout + r.stderr or "ok").encode(), "text/plain; charset=utf-8")
        elif u.path == "/stat":
            self.send(json.dumps({k: st[k] for k in ("n", "fps", "dev", "msg")}).encode(), "application/json")
        elif u.path == "/quit":
            self.send(b"bye", "text/plain")
            quit_ev.set()
        else:
            self.send_error(404)


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *a: quit_ev.set())
    if os.path.exists(STOP):
        os.remove(STOP)
    vbus_fd = gpio_hold(3, 5, 0)                     # USB_PWR_CTL low: U14 may power the USB-A ports
    sysw("b_sess", "0"); time.sleep(1); sysw("id", "0"); time.sleep(1)
    if otg_state() != "a_host":
        sysw("id", "0"); time.sleep(1)
    log("otg", otg_state())
    srv = Server(("", PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=capture, daemon=True).start()
    log("http on", PORT)
    while not quit_ev.wait(1):
        if os.path.exists(STOP):
            quit_ev.set()
    log("stopping")
    srv.shutdown()
    time.sleep(1)
    sysw("id", "1"); time.sleep(2); sysw("b_sess", "1")
    os.close(vbus_fd)
    log("otg", otg_state())


if __name__ == "__main__":
    main()
