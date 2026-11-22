#!/usr/bin/env python3
"""Board desktop wallpaper: aurora over a night sky and mountain ridges, drawn procedurally (no
image files to license). Writes OUT.png (to look at) and OUT.bgrx (raw 1920x1080 BGRX bytes for
xbg on the board). Usage: python3 wallpaper.py OUT [seed]   (needs numpy and Pillow)"""
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
out = sys.argv[1]
rng = np.random.default_rng(int(sys.argv[2]) if len(sys.argv) > 2 else 850)
x = np.arange(W)[None, :].astype(np.float32)
y = np.arange(H)[:, None].astype(np.float32)


def smooth_noise(n, scale):
    """1-D value noise of length n, features about 'scale' pixels wide, range about -1..1"""
    k = rng.standard_normal(n // scale + 3)
    t = np.arange(n) / scale
    i = t.astype(int)
    f = t - i
    f = f * f * (3 - 2 * f)
    return k[i] * (1 - f) + k[i + 1] * f


def ridge(base, amp, octaves):
    h = np.full(W, base, np.float32)
    for o in range(octaves):
        h += amp / 2 ** o * smooth_noise(W, max(4, 420 >> o))
    return h


# sky: near black at the top, deep blue, a little violet at the horizon
v = (y / H) ** 1.4
sky = (np.array([4, 5, 14]) * (1 - v)[..., None] + np.array([34, 22, 70]) * v[..., None]) * np.ones((1, W, 1))

# stars: many faint, a few bright, fewer towards the horizon
img = sky.astype(np.float32)
n = 2600
sx, sy = rng.integers(0, W, n), (H * rng.random(n) ** 1.6 * 0.8).astype(int)
b = 40 + 215 * rng.random(n) ** 6
tint = np.where(rng.random(n)[:, None] < 0.25, [0.8, 0.9, 1.0], [1.0, 0.95, 0.85])
img[sy, sx] += b[:, None] * tint
for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):  # a soft cross on the bright ones
    m = b > 160
    img[np.clip(sy[m] + dy, 0, H - 1), np.clip(sx[m] + dx, 0, W - 1)] += 0.35 * b[m, None] * tint[m]

# aurora: curtains with a sharp lower edge that fade upwards, rays across, green low and violet high
aur = np.zeros((H, W, 3), np.float32)
for base, amp, L, gain in ((420, 120, 150, 1.0), (330, 80, 110, 0.5)):
    yc = base + amp * smooth_noise(W, 520) + 0.35 * amp * smooth_noise(W, 140)
    env = np.clip(0.25 + 0.75 * (0.5 + 0.6 * smooth_noise(W, 360)), 0.05, 1)  # brighter and dimmer stretches
    d = yc[None, :] - y  # > 0 above the edge
    up = np.exp(-np.maximum(d, 0) / L)
    low = np.exp(-np.maximum(-d, 0) ** 2 / (2 * 14 ** 2))
    rays = 0.65 + 0.35 * np.clip(0.5 + 0.5 * smooth_noise(W, 9) + 0.3 * smooth_noise(W, 3), 0, 1)
    a = gain * up * low * (rays * env)[None, :]
    hgt = np.clip(np.maximum(d, 0) / (1.6 * L), 0, 1)[..., None]
    col = np.array([40, 255, 150]) * (1 - hgt) + np.array([150, 60, 230]) * hgt
    aur += a[..., None] * col
img += 0.95 * aur
glow = aur.mean(axis=1, keepdims=True)  # light the air below the curtains a little
img += 0.15 * glow

# mountains: three ridges, the far one lit by the aurora, the near one almost black
for base, amp, oct_, col, lit in ((800, 90, 6, (22, 24, 52), 0.10), (880, 70, 7, (12, 13, 30), 0.05),
                                  (975, 55, 8, (4, 5, 12), 0.0)):
    h = ridge(base, amp, oct_)
    shade = np.array(col, np.float32) + lit * glow[np.clip(h.astype(int), 0, H - 1), 0]  # (W, 3)
    rim = 1 + 0.25 * np.clip(1 - (y - h[None, :]) / 25, 0, 1)  # brighter just below the crest
    img = np.where((y >= h[None, :])[..., None], shade[None] * rim[..., None], img)

img += rng.random(img.shape) - 0.5  # dither: no banding in the dark gradients
pic = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")

try:
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
except OSError:
    font = ImageFont.load_default()
ImageDraw.Draw(pic).text((W - 40, H - 34), "E850-96  ·  Exynos 850  ·  Mali-G52", font=font,
                         fill=(120, 130, 160), anchor="rs")

pic.save(out + ".png")
r, g, bl = (np.asarray(pic)[..., i] for i in range(3))
np.stack([bl, g, r, np.zeros_like(r)], -1).tofile(out + ".bgrx")
print(out + ".png", out + ".bgrx", W, H)
