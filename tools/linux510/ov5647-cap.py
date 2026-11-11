#!/usr/bin/env python3
"""Capture RAW10 frames from the OV5647 through the Pablo (fimc-is) sensor nodes, no ISP.

Two V4L2 MPLANE nodes are used (sensor instance 1):
  /dev/video102  exynos-is-ss1   group *leader*, OUTPUT queue: image plane + meta plane
                                 (camera2_shot_ext); drives the sensor group frame by frame
  /dev/video214  ss1vc0          VC0 capture node, CAPTURE queue: the RAW10 frames (CSIS WDMA)
The nodes do not implement ENUM_FMT, so v4l2-ctl refuses them; the ioctls are called directly.

Sequence (each step was needed, see docs/E85096-LINUX510-BOOT.md 9.5-9.7):
  leader: S_INPUT(position 1, vindex 2 = SS1, leader) -> S_CTRL(IS_S_SENSOR_SIZE w<<16|h) ->
          S_FMT(SBGGR10) -> REQBUFS(DMABUF)
  vc0:    S_INPUT -> S_FMT(SBGGR10) -> REQBUFS(DMABUF)
  STREAMON vc0, STREAMON leader, then QBUF (QBUF before STREAMON hangs the group thread)
  leader meta: shot.magicNumber + node_group.capture[0] = {vid 114 (SS1VC0), request 1, crop}
  S_CTRL(IS_S_STREAM 1): sensor stream on; DQBUF vc0 frames; S_STREAM 0; STREAMOFF.
Buffers are dma-bufs from /dev/dma_heap/system-uncached (the Pablo QBUF path wants dma-buf fds); VC0 with --live
--raw8 from /dev/dma_heap/system (cached).

    ov5647-cap.py [-W 640] [-H 480] [-n 5] [-o /tmp/ov5647]   -> /tmp/ov5647_<i>.raw
Each file is one VC0 frame: one little-endian u16 per pixel, 10 valid bits (raw10-view.py shows it).

    ov5647-cap.py --live [-n 0]      live video on HDMI (/dev/fb0) instead of files; -n 0 = forever
    ov5647-cap.py --live --raw8 -W 8192 -H 127 -n 0 -b 8 -B 16
        S5KRC1S events on HDMI at --fps (30) frames/s through /usr/local/lib/libdvsview.so (dvsview.c).
        Needs fimc_is.e850_csi_err_stop=0 (tools/board/dvslive.sh sets it and the rest).
    ... --boxes /var/lib/dvsbox/boxes.jsonl --mm-per-px 1.0
        also measure boxes moving through the view (one at a time): one JSON line per box, label on HDMI.
    ... --ctl 8080
        display settings page (dvsctl.py, dvsctl.html in /usr/local/share/dvsview): http://<board>:8080/
    ... --act /var/lib/dvsact/act.jsonl --act-model /var/lib/dvsact/model.onnx
        also classify the person in view (dvsact.py, 11.38): one JSON line per change of state (appear, move, fall)
"""
import argparse
import atexit
import ctypes
import fcntl
import mmap
import os
import select
import signal
import struct
import sys
import time

VIDIOC_S_FMT = 0xc0d05605
VIDIOC_REQBUFS = 0xc0145608
VIDIOC_QUERYBUF = 0xc0585609
VIDIOC_QBUF = 0xc058560f
VIDIOC_DQBUF = 0xc0585611
VIDIOC_STREAMON = 0x40045612
VIDIOC_STREAMOFF = 0x40045613
VIDIOC_S_INPUT = 0xc0045627
VIDIOC_S_CTRL = 0xc008561c
CID_IS_S_STREAM = 0x009a0000 | 0x1000 | 14         # sensor (front) stream on/off: sstream in bits 3:0
CID_IS_S_SENSOR_SIZE = 0x009a0000 | 0x1000 | 69    # sensor mode is looked up from this, not S_FMT
TYPE_CAPTURE, TYPE_OUTPUT = 9, 10                  # V4L2_BUF_TYPE_VIDEO_{CAPTURE,OUTPUT}_MPLANE
MEMORY_DMABUF = 4
PIX_FMT_SBGGR10 = 0x30314742                       # 'BG10'
PIX_FMT_SBGGR8 = 0x31384142                        # 'BA81': S5KRC1S DVS RAW8 transport (8192x127)
INPUT = (1 << 16) | (2 << 8) | 1                   # position 1 (FRONT), vindex 2 (SS1), leader
VID_SS1, VID_SS1VC0 = 2, 114                       # IS_VIDEO_SS1_NUM, IS_VIDEO_SS1VC0_NUM

# camera2_shot_ext layout (fimc-is.ko DWARF, llvm-dwarfdump --name=camera2_shot_ext ...)
SHOT_MAGIC = 0x92345678                            # vendor/default/v8_20_0/is-metadata-vendor.h
SHOT_MAGIC_OFFSET = 0xb38 + 0xbc98                 # shot_ext.shot @0xb38, shot.magicNumber @0xbc98
NODE_GROUP = 0x58                                  # shot_ext.node_group: leader @+0, capture[] @+0x30
NODE_SIZE = 0x30                                   # camera2_node: vid, request, in crop[4], out crop[4], pixfmt, pixsize

DMA_HEAP_IOCTL_ALLOC = 0xc0184800


def log(msg):
    up = open('/proc/uptime').read().split()[0]     # same clock as dmesg, roughly
    print('[%8s] %s' % (up, msg), flush=True)


ap = argparse.ArgumentParser()
ap.add_argument('-W', type=int, default=640)
ap.add_argument('-H', type=int, default=480)
ap.add_argument('-n', type=int, default=5, help='frames to save')
ap.add_argument('-b', type=int, default=4, help='VC0 buffers')
ap.add_argument('-B', type=int, default=16,
                help='leader buffers: in bypass they are never completed (no 3AA), so one leader\n'
                     'buffer = one VC0 frame; queue as many as frames wanted')
ap.add_argument('-o', default='/tmp/ov5647')
ap.add_argument('-t', type=float, default=3.0, help='timeout per frame, s')
ap.add_argument('-v', action='store_true', help='log every ioctl in the loop')
ap.add_argument('--live', action='store_true', help='draw every frame on /dev/fb0, save nothing')
ap.add_argument('--loud', action='store_true', help='keep console printk on (to see a hang on UART)')
ap.add_argument('--raw8', action='store_true', help='RAW8 (S5KRC1S DVS: -W 8192 -H 127)')
ap.add_argument('--fps', type=int, default=30, help='--live --raw8: frames drawn per second')
ap.add_argument('--boxes', help='--live --raw8: append measured boxes to this file (JSON lines)')
ap.add_argument('--mm-per-px', type=float, default=1.0, help='--boxes: belt-plane scale, calibrate with a known box')
ap.add_argument('--box-window', type=int, default=5000, help='--boxes: us of events per measurement (<= 100000)')
ap.add_argument('--ctl', type=int, default=0, help='--live --raw8: serve the display settings page on this port')
ap.add_argument('--act', help='--live --raw8: append person action changes to this file (JSON lines)')
ap.add_argument('--act-model', help='--act: the ONNX model (tools/host/act/export.py)')
a = ap.parse_args()
PIX_FMT, PIX_BITS = (PIX_FMT_SBGGR8, 8) if a.raw8 else (PIX_FMT_SBGGR10, 10)

# The boot command line has loglevel=8 ignore_loglevel: the driver's per-frame messages then go
# out on the 115200 baud UART from the interrupt handler, CSIS loses interrupts and a 60 fps
# stream drops to ~5 fps. Keep them in dmesg only while streaming.
QUIET = (('/sys/module/printk/parameters/ignore_loglevel', 'N'), ('/proc/sys/kernel/printk', '1'))
saved_printk = [open(f).read().split()[0] for f, _ in QUIET]
for f, v in QUIET * (not a.loud):
    open(f, 'w').write(v)
atexit.register(lambda: [open(f, 'w').write(v) for (f, _), v in zip(QUIET, saved_printk)])
if a.live and a.n == 5:
    a.n = 0

heap = os.open('/dev/dma_heap/system-uncached', os.O_RDWR)
# --live --raw8 reads each 1 MB VC0 buffer: uncached that took 8-14 ms. Cached reads are fresh because Pablo unmaps
# the buffer at DQBUF (an invalidate); libdvsview writes its zeroes back itself before QBUF (11.28)
cached = os.open('/dev/dma_heap/system', os.O_RDWR) if a.live and a.raw8 else heap


def heap_alloc(size, h=heap):
    d = bytearray(24)
    struct.pack_into('QII', d, 0, (size + 4095) & ~4095, 0, os.O_RDWR | os.O_CLOEXEC)
    fcntl.ioctl(h, DMA_HEAP_IOCTL_ALLOC, d)
    return struct.unpack_from('I', d, 8)[0]


class Node:
    def __init__(self, path, btype, name):
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        self.type, self.name, self.bufs = btype, name, []

    def s_input(self, value):
        fcntl.ioctl(self.fd, VIDIOC_S_INPUT, struct.pack('I', value))

    def s_fmt(self, w, h):
        # struct v4l2_format: type, union @8 = v4l2_pix_format_mplane (w h pixfmt field colorspace ...)
        fmt = bytearray(208)
        struct.pack_into('I', fmt, 0, self.type)
        struct.pack_into('5I', fmt, 8, w, h, PIX_FMT, 1, 0)
        fcntl.ioctl(self.fd, VIDIOC_S_FMT, fmt)

    def buffer(self, index, planes, n):
        # struct v4l2_buffer (arm64): index type bytesused flags field | timestamp @24 | timecode @40 |
        # sequence @56 memory @60 | m.planes @64 | length @72 | ... 88 bytes
        b = bytearray(88)
        struct.pack_into('2I', b, 0, index, self.type)
        struct.pack_into('I', b, 60, MEMORY_DMABUF)
        struct.pack_into('Q', b, 64, ctypes.addressof(ctypes.c_char.from_buffer(planes)))
        struct.pack_into('I', b, 72, n)
        return b

    def reqbufs(self, count, h=heap):
        req = bytearray(20)
        struct.pack_into('3I', req, 0, count, self.type, MEMORY_DMABUF)
        fcntl.ioctl(self.fd, VIDIOC_REQBUFS, req)
        n = struct.unpack_from('I', req, 0)[0]
        probe = bytearray(64 * 8)               # struct v4l2_plane is 64 bytes
        qb = self.buffer(0, probe, 8)
        fcntl.ioctl(self.fd, VIDIOC_QUERYBUF, qb)
        self.nplanes = struct.unpack_from('I', qb, 72)[0]
        self.lengths = [struct.unpack_from('I', probe, p * 64 + 4)[0] for p in range(self.nplanes)]
        for i in range(n):
            planes = []
            for length in self.lengths:
                # 2x headroom: the CSIS WDMA wrote ~1 KiB past a 640x480 RAW10 plane
                # ("SysMMU WRITE PAGE FAULT" just after the buffer); the IOMMU maps the whole dma-buf
                size = length * 2
                dfd = heap_alloc(size, h)
                planes.append((dfd, mmap.mmap(dfd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)))
            self.bufs.append(planes)
        log('%s: %d buffers, %d plane(s) %s' % (self.name, n, self.nplanes, self.lengths))

    def qbuf(self, i):
        pl = bytearray(64 * self.nplanes)       # must stay alive during the ioctl (holds its address)
        for p, (dfd, _) in enumerate(self.bufs[i]):
            struct.pack_into('IIi', pl, p * 64, self.lengths[p], self.lengths[p], dfd)  # bytesused length m.fd
        fcntl.ioctl(self.fd, VIDIOC_QBUF, self.buffer(i, pl, self.nplanes))

    def dqbuf(self):
        pl = bytearray(64 * self.nplanes)
        b = self.buffer(0, pl, self.nplanes)
        fcntl.ioctl(self.fd, VIDIOC_DQBUF, b)
        return struct.unpack_from('I', b, 0)[0], struct.unpack_from('I', b, 12)[0], struct.unpack_from('I', pl, 0)[0]

    def stream(self, on):
        fcntl.ioctl(self.fd, VIDIOC_STREAMON if on else VIDIOC_STREAMOFF, struct.pack('I', self.type))


def node_entry(meta, off, vid, w, h):
    struct.pack_into('2I4I4I2I', meta, off, vid, 1, 0, 0, w, h, 0, 0, w, h, PIX_FMT, PIX_BITS)


ldr = Node('/dev/video102', TYPE_OUTPUT, 'ss1 (leader)')
ldr.s_input(INPUT)
fcntl.ioctl(ldr.fd, VIDIOC_S_CTRL, struct.pack('Ii', CID_IS_S_SENSOR_SIZE, (a.W << 16) | a.H))
ldr.s_fmt(a.W, a.H)
vc0 = Node('/dev/video214', TYPE_CAPTURE, 'ss1vc0')
vc0.s_input(0)
vc0.s_fmt(a.W, a.H)
log('S_INPUT 0x%x, sensor size %dx%d, S_FMT %d-bit on both nodes' % (INPUT, a.W, a.H, PIX_BITS))
ldr.reqbufs(a.B)
vc0.reqbufs(a.b, cached)

def write_shot(i):
    """Shot meta of leader buffer i. Rewritten before every QBUF: the driver clears
    capture[].request when it drops a VC0 frame, and a stale 0 stops VC0 for good."""
    meta = ldr.bufs[i][-1][1]
    meta[SHOT_MAGIC_OFFSET:SHOT_MAGIC_OFFSET + 4] = struct.pack('<I', SHOT_MAGIC)
    node_entry(meta, NODE_GROUP, VID_SS1, a.W, a.H)                     # leader
    node_entry(meta, NODE_GROUP + NODE_SIZE, VID_SS1VC0, a.W, a.H)      # capture[0] = VC0


for i in range(len(ldr.bufs)):
    write_shot(i)
for planes in vc0.bufs:
    planes[0][1][:4096] = b'\x5a' * 4096       # marker: still 0x5a after DQBUF = DMA never wrote

vc0.stream(True)
ldr.stream(True)
log('STREAMON vc0 + leader')
for i in range(len(vc0.bufs)):
    vc0.qbuf(i)
for i in range(len(ldr.bufs)):
    ldr.qbuf(i)
fcntl.ioctl(ldr.fd, VIDIOC_S_CTRL, struct.pack('Ii', CID_IS_S_STREAM, 1))
log('queued, sensor stream on')

class Live:
    """Half-resolution demosaic (one RGB pixel per BGGR cell) through per-channel LUTs that hold
    black level, grey-world white balance, auto gain and gamma; drawn centred on /dev/fb0."""

    def __init__(self, w, h):
        import numpy as np
        self.np = np
        self.w, self.h = w, h
        fd = os.open('/dev/fb0', os.O_RDWR)
        var = bytearray(160)
        fcntl.ioctl(fd, 0x4600, var)                 # FBIOGET_VSCREENINFO
        xres, yres, _, yv = struct.unpack_from('4I', var)
        yoff = struct.unpack_from('I', var, 20)[0]
        stride = int(open('/sys/class/graphics/fb0/stride').read())
        self.fbmap = mmap.mmap(fd, stride * yv, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
        fb = np.frombuffer(self.fbmap, np.uint8).reshape(yv, stride)
        self.scale = max(1, min(xres // (w // 2), yres // (h // 2)))
        iw, ih = w // 2 * self.scale, h // 2 * self.scale
        x0, y0 = (xres - iw) // 2, yoff + (yres - ih) // 2
        # one XRGB8888 word per screen pixel; viewed as (row, s, col, s) so a broadcast write scales
        self.win = fb[y0:y0 + ih, x0 * 4:(x0 + iw) * 4].view('<u4').reshape(ih // self.scale, self.scale,
                                                                          iw // self.scale, self.scale)
        self.luts = None
        self.n = 0
        self.t = time.time()

    def update_luts(self, r, g, b):
        np = self.np
        means = [max(float(c.mean()) - 16, 1.0) for c in (r, g, b)]
        top = max(float(np.percentile(g, 99.5)) - 16, 8.0)
        v = np.clip(np.arange(1024, dtype=np.float32) - 16, 0, None)
        r8, g8, b8 = [(np.clip(v * (sum(means) / 3 / m) / top, 0, 1) ** (1 / 2.2) * 255).astype(np.uint32)
                      for m in means]
        # pre-shifted to their XRGB8888 byte, alpha folded into blue: a pixel is three lookups ORed
        self.luts = (b8 | 0xff000000, g8 << 8, r8 << 16)

    def show(self, buf):
        np = self.np
        bayer = np.frombuffer(buf, '<u2', self.w * self.h).reshape(self.h, self.w) & 0x3ff
        b = bayer[0::2, 0::2]
        g = (bayer[0::2, 1::2] + bayer[1::2, 0::2]) >> 1
        r = bayer[1::2, 1::2]
        if self.luts is None or self.n % 30 == 0:  # white balance + gain every 30 frames
            self.update_luts(r, g, b)
        lb, lg, lr = self.luts
        self.win[:] = (lb[b] | lg[g] | lr[r])[:, None, :, None]   # 10 ms at 4x vs 21 ms for repeat()
        self.n += 1
        if self.n % 30 == 0:
            now = time.time()
            log('live: %d frames, %.1f fps' % (self.n, 30 / (now - self.t)))
            self.t = now


class DvsLive:
    """S5KRC1S events on /dev/fb0 at --fps frames/s: libdvsview.so (dvsview.c, 11.27) decodes each VC0 buffer
    with the GIL released and draws, from its own thread, one frame period of events by sensor timestamp."""

    def __init__(self):
        self.lib = ctypes.CDLL('/usr/local/lib/libdvsview.so')
        self.lib.dvs_start.argtypes = [ctypes.c_char_p, ctypes.c_int]
        self.lib.dvs_ingest.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        self.lib.dvs_boxes.argtypes = [ctypes.c_char_p, ctypes.c_double, ctypes.c_int]
        if a.boxes:
            os.makedirs(os.path.dirname(os.path.abspath(a.boxes)), exist_ok=True)
            r = self.lib.dvs_boxes(a.boxes.encode(), a.mm_per_px, a.box_window)
            if r < 0:
                raise OSError(-r, 'dvs_boxes: ' + os.strerror(-r))
        r = self.lib.dvs_start(b'/dev/fb0', a.fps)
        if r < 0:
            raise OSError(-r, 'dvs_start: ' + os.strerror(-r))
        if a.ctl:
            sys.path.append('/usr/local/share/dvsview')
            import dvsctl
            try:
                dvsctl.start(self.lib, a.ctl)
                log('display settings: http://<board>:%d/' % a.ctl)
            except OSError as e:                # port taken: keep the live view without the page
                log('display settings off: port %d: %s' % (a.ctl, e))
        if a.act and a.act_model:               # without onnxruntime or the model it says why and stays off
            sys.path.append('/usr/local/share/dvsview')
            import dvsact
            dvsact.start(self.lib, a.act, a.act_model)

    def show(self, buf):
        p = ctypes.c_char.from_buffer(buf)      # the dma-buf mmap itself: C decodes and zeroes it in place
        # the sensor sends H + 1 packets a frame (DT H, VRESOL mismatch let through, 11.22); the last one lands
        # after line H; it held a sub timestamp in 3 of 14 raw buffers, a scan lost each time unread (11.46)
        self.lib.dvs_ingest(ctypes.addressof(p), a.W * (a.H + 1))
        del p                                   # drop the buffer export so the mmap can close at exit


live = (DvsLive() if a.raw8 else Live(a.W, a.H)) if a.live else None
signal.signal(signal.SIGTERM, signal.default_int_handler)   # pkill: stream off cleanly (& ignores SIGINT)
saved = 0
t0 = last = time.time()
try:
    while a.n == 0 or saved < a.n:
        # the OUTPUT (leader) fd can poll writable without a finished buffer: time out on the clock
        if time.time() - last > a.t:
            log('timeout: no VC0 frame in %.1f s' % a.t)
            break
        r, w, _x = select.select([vc0.fd], [ldr.fd], [], 0.2)
        if not (r or w):
            time.sleep(0.01)
        if ldr.fd in w:                          # leader (meta) buffer back: requeue it
            try:
                a.v and log('ldr dqbuf')
                idx, flags, _ = ldr.dqbuf()
                a.v and log('ldr qbuf %d (flags 0x%x)' % (idx, flags))
                write_shot(idx)
                ldr.qbuf(idx)
            except BlockingIOError:
                time.sleep(0.005)
        if vc0.fd in r:
            try:
                idx, flags, used = vc0.dqbuf()
            except BlockingIOError:
                continue
            if live:
                live.show(vc0.bufs[idx][0][1])
                saved += 1
                last = time.time()
                vc0.qbuf(idx)
                continue
            data = vc0.bufs[idx][0][1][:]           # whole allocation (2x the plane) to see overruns
            name = '%s_%d.raw' % (a.o, saved)
            open(name, 'wb').write(data)
            marker = data[:4096] == b'\x5a' * 4096
            log('vc0 frame %d: buf %d flags 0x%x bytesused %d -> %s%s' % (
                saved, idx, flags, used, name, '  (marker intact: no data)' if marker else ''))
            saved += 1
            last = time.time()
            vc0.bufs[idx][0][1][:4096] = b'\x5a' * 4096
            a.v and log('vc0 qbuf %d' % idx)
            vc0.qbuf(idx)
            a.v and log('vc0 qbuf done')
except KeyboardInterrupt:
    log('interrupted')
finally:
    try:
        fcntl.ioctl(ldr.fd, VIDIOC_S_CTRL, struct.pack('Ii', CID_IS_S_STREAM, 0))
    except OSError as e:
        log('S_STREAM off: %s' % e)
    for n in (ldr, vc0):
        try:
            n.stream(False)
        except OSError as e:
            log('%s STREAMOFF: %s' % (n.name, e))
    log('stopped after %.1f s, %d VC0 frame(s)' % (time.time() - t0, saved))
sys.exit(0 if saved else 1)
