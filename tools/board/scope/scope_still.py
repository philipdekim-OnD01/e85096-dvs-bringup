#!/usr/bin/env python3
"""High-resolution stills of the microscope kit (UVC format 2, GREY, sol-cx3-uvc still-capture firmware).

The kit sends one still as packed RAW10 rows (5 bytes per 4 pixels), one byte per GREY "pixel", over 3 sensor
frames: frame p carries the rows r with r % 3 == p, and the UVC frame is [rows p = 0][p = 1][p = 2].
reorder -> unpack_raw10 -> develop (black level, demosaic, white balance by channel medians, 8 bit, grey = luma).

  python3 scope_still.py --selftest     packing, row order and live view for the three modules, demosaic and WB
"""
import os, sys, time
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "py"))
try:
    import cv2
except ImportError:
    cv2 = None


def reorder(buf, rows, row_bytes, k=3):
    """One UVC still ([rows r % k == 0][== 1]...) -> uint8 (rows, row_bytes) in sensor row order."""
    if len(buf) != rows * row_bytes:
        raise ValueError(f"still is {len(buf)} B, expected {rows} x {row_bytes}")
    a = np.frombuffer(buf, np.uint8).reshape(rows, row_bytes)
    out = np.empty_like(a)
    o = 0
    for p in range(k):
        n = len(range(p, rows, k))
        out[p::k] = a[o:o + n]
        o += n
    return out


def unpack_raw10(a):
    """Packed CSI-2 RAW10 [M0 M1 M2 M3 L] (M = bits 9..2, pixel j's bits 1..0 at L bits 2j..2j+1) -> uint16."""
    q = a.reshape(a.shape[0], -1, 5)
    p = q[..., :4].astype(np.uint16) << 2
    p |= (q[..., 4:5] >> np.array([0, 2, 4, 6], np.uint8)) & 3
    return p.reshape(a.shape[0], -1)


def live_view(raw):
    """The firmware's live grey Y from a still: odd rows, (M(2j) + M(2j+1)) >> 1 with M = bits 9..2 (BinRowPairs)."""
    m = raw[1::2] >> 2
    return ((m[:, 0::2] + m[:, 1::2]) >> 1).astype(np.uint8)


def develop(raw, bayer, black, color, flat=None):
    """10-bit Bayer (top-left 2x2: "RG" = R G / G B, "GR", "GB", "BG") -> uint8 RGB (color) or grey.
    Channel gains make the R, G and B medians equal; 1023 - black maps to 255; flat (float HxW) multiplies.
    bayer None (no colour filter): grey, every pixel as it is (no demosaic, which would only blur)."""
    if cv2 is None:
        raise RuntimeError("OpenCV 없음: 사진을 현상할 수 없습니다")
    if black:
        raw = np.maximum(raw, black) - np.uint16(black)
    if bayer is None:
        g = raw if flat is None else cv2.multiply(raw, flat, dtype=cv2.CV_32F)
        return cv2.convertScaleAbs(g, alpha=255.0 / (1023 - black))
    code = {"RG": cv2.COLOR_BayerRGGB2RGB, "GR": cv2.COLOR_BayerGRBG2RGB,
            "GB": cv2.COLOR_BayerGBRG2RGB, "BG": cv2.COLOR_BayerBGGR2RGB}[bayer]
    rgb = cv2.demosaicing(np.ascontiguousarray(raw), code)
    med = [max(float(np.median(rgb[::8, ::8, i])), 1.0) for i in range(3)]
    out = []
    for i in range(3):
        alpha = med[1] / med[i] * 255.0 / (1023 - black)
        ch = rgb[..., i] if flat is None else cv2.multiply(rgb[..., i], flat, dtype=cv2.CV_32F)
        out.append(cv2.convertScaleAbs(ch, alpha=alpha))
    rgb = cv2.merge(out)
    return rgb if color else cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def _pack_raw10(p):
    """Test helper: uint16 10-bit (rows, cols) -> packed RAW10 uint8 (rows, cols * 5 // 4)."""
    q = p.reshape(p.shape[0], -1, 4)
    lsb = ((q & 3) << np.array([0, 2, 4, 6], np.uint16)).sum(axis=2).astype(np.uint8)
    return np.concatenate([(q >> 2).astype(np.uint8), lsb[..., None]], axis=2).reshape(p.shape[0], -1)


def _bayer(h, w, pattern, vals, rng, noise=2.0):
    """Test helper: 10-bit Bayer image whose top-left 2x2 is pattern ("RG" = R G / G B), vals = {"R":, "G":, "B":}."""
    site = {"RG": "RGGB", "GR": "GRBG", "GB": "GBRG", "BG": "BGGR"}[pattern]
    img = np.empty((h, w), np.float64)
    for i, (dy, dx) in enumerate(((0, 0), (0, 1), (1, 0), (1, 1))):
        v = vals[site[i]]
        img[dy::2, dx::2] = v(np.arange(dx, w, 2)[None, :] / w) if callable(v) else v
    return np.clip(img + rng.normal(0, noise, img.shape), 0, 1023).astype(np.uint16)


def selftest():
    rng = np.random.default_rng(1)
    for w, h in ((4032, 3072), (3264, 2448), (4608, 3504)):
        raw = rng.integers(0, 1024, (h, w), dtype=np.uint16)
        packed = _pack_raw10(raw)
        rb = w * 5 // 4
        assert packed.shape == (h, rb)
        phased = np.concatenate([packed[p::3] for p in range(3)]).tobytes()
        back = unpack_raw10(reorder(phased, h, rb))
        assert back.dtype == np.uint16 and np.array_equal(back, raw), (w, h)
        lv = live_view(raw)
        assert lv.shape == (h // 2, w // 2) and lv.dtype == np.uint8
        assert lv[0, 0] == ((int(raw[1, 0]) >> 2) + (int(raw[1, 1]) >> 2)) >> 1, (w, h)   # firmware BinRowPairs
    try:
        reorder(b"\0" * 10, 3, 5)
        raise AssertionError("short buffer accepted")
    except ValueError:
        pass
    h, w, black, s = 3072, 4032, 64, 500.0
    grey = _bayer(h, w, "RG", {"R": black + 0.5 * s, "G": black + s, "B": black + 0.7 * s}, rng)
    t0 = time.time()
    c = develop(grey, "RG", black, True)
    dt = time.time() - t0
    assert c.shape == (h, w, 3) and c.dtype == np.uint8
    m = c.reshape(-1, 3).mean(axis=0)
    assert m.max() - m.min() <= 2, m
    g = develop(grey, "RG", black, False)
    assert g.shape == (h, w) and g.dtype == np.uint8
    sm = [g[dy::2, dx::2].mean() for dy in (0, 1) for dx in (0, 1)]
    assert max(sm) - min(sm) <= 1, sm
    # R falls and B rises from left to right: the colour must follow for every pattern (a wrong demosaic code
    # swaps R and B or reads G sites as R, which white balance cannot undo)
    for pat in ("RG", "GR", "GB", "BG"):
        img = _bayer(512, 512, pat, {"R": lambda x: 100 + 700 * (1 - x), "G": 450.0, "B": lambda x: 100 + 700 * x}, rng)
        c = develop(img, pat, 0, True).astype(int)
        left, right = c[:, :128].reshape(-1, 3).mean(axis=0), c[:, -128:].reshape(-1, 3).mean(axis=0)
        assert left[0] > left[2] + 40 and right[2] > right[0] + 40, (pat, left, right)
    # no colour filter (bayer None): every pixel kept, (v - black) * 255 / (1023 - black), colour asked or not
    raw = rng.integers(0, 1024, (64, 96), dtype=np.uint16)
    want = np.clip(np.rint((raw.astype(float) - black).clip(0) * 255 / (1023 - black)), 0, 255)
    for col in (False, True):
        m = develop(raw, None, black, col)
        assert m.shape == raw.shape and m.dtype == np.uint8 and np.abs(m - want).max() <= 1, col
    flat = np.full(raw.shape, 0.5, np.float32)
    assert np.abs(develop(raw, None, black, False, flat).astype(int) - want * 0.5).max() <= 1
    print(f"selftest ok (develop {dt:.2f} s for {w}x{h})")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
