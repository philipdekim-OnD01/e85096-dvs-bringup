"""Known images for two-board DVS fusion (11.61). A tablet page blinks a fixed sequence of black-and-white images
inside dvs3d-checker.html's 10 x 7 frame; the Mac has the same images, so every recorded frame has a ground truth.
  python3 dvsfuse_page.py OUT.html      writes the page and OUT.npz (names, images: the design, truth: as drawn)
The page is CSS only (it must run in a tablet's mail or file preview): slot k shows image k for SLOT_S seconds, the whole
cycle repeats, and the white parts blink at 5 Hz like the checker (a DVS sees changes only). Slot 0 and every
CHECK_EVERY-th slot is the checker, to fit and re-check the screen -> sensor homography.
Images are inline SVG paths, not <img src="data:...">: an iPad's Files preview showed that page black (11.61)."""
import re
import sys

import cv2
import numpy as np

GW, GH = 300, 210                    # 30 px a checker square
SLOT_S, SLOTS, CHECK_EVERY = 3, 160, 16
LETTERS = list('ABCDEFGHJKLMNPRSTUVWXYZ0123456789')


def checker():
    y, x = np.mgrid[0:GH, 0:GW] // (GW // 10)
    return np.uint8(255 * ((x + y) % 2 == 1))           # top-left black


def content(rng):
    kind = rng.choice(['shapes', 'letters', 'bars', 'dots', 'blobs'])
    img = np.zeros((GH, GW), np.uint8)
    if kind == 'shapes':
        for _ in range(rng.integers(1, 5)):
            c = (int(rng.integers(0, GW)), int(rng.integers(0, GH)))
            r = int(rng.integers(12, 80))
            s = rng.integers(0, 3)
            if s == 0:
                cv2.circle(img, c, r, 255, -1)
            elif s == 1:
                cv2.rectangle(img, (c[0] - r, c[1] - r // 2), (c[0] + r, c[1] + r // 2), 255, -1)
            else:
                a = rng.uniform(0, 2 * np.pi) + np.arange(int(rng.integers(3, 7))) * 2 * np.pi / 6
                cv2.fillPoly(img, [np.int32(np.c_[c[0] + r * np.cos(a), c[1] + r * np.sin(a)])], 255)
    elif kind == 'letters':
        text = ''.join(rng.choice(LETTERS, int(rng.integers(1, 5))))
        scale, th = rng.uniform(1.5, 5.0), int(rng.integers(3, 14))
        (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, th)
        scale *= min(1.0, 0.95 * GW / w, 0.9 * GH / h)
        (w, h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, th)
        org = (int(rng.integers(0, max(1, GW - w))), int(rng.integers(h, max(h + 1, GH - th))))
        cv2.putText(img, text, org, cv2.FONT_HERSHEY_DUPLEX, scale, 255, th, cv2.LINE_8)
    elif kind == 'bars':
        y, x = np.mgrid[0:GH, 0:GW]
        p, a = rng.uniform(6, 60), rng.uniform(0, np.pi)
        img[:] = 255 * ((x * np.cos(a) + y * np.sin(a)) % p < p / 2)
    elif kind == 'dots':
        for _ in range(rng.integers(5, 60)):
            cv2.circle(img, (int(rng.integers(0, GW)), int(rng.integers(0, GH))), int(rng.integers(3, 20)), 255, -1)
    else:
        n = cv2.GaussianBlur(rng.standard_normal((GH, GW)).astype(np.float32), (0, 0), rng.uniform(5, 25))
        img[:] = 255 * (n > np.quantile(n, rng.uniform(0.3, 0.7)))
    if rng.random() < 0.3:
        return kind + '-inv', 255 - img
    return str(kind), img


UP = 4                               # trace the outlines on a 4x grid, so the path keeps the design's widths
UNIT = 2 * UP                        # path coordinates are integers in 1/UNIT design px (a short page)


def path_d(img):
    """an SVG path (even-odd fill) of the white parts of a 0/255 design, in 1/UNIT design px"""
    big = cv2.resize(img, None, fx=UP, fy=UP, interpolation=cv2.INTER_NEAREST)
    cs, _ = cv2.findContours(big, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in cs:
        c = cv2.approxPolyDP(c, UP * 0.5, True)[:, 0]      # within half a design px: fewer points on diagonals
        if len(c) >= 3:
            out.append('M' + ' '.join('%d,%d' % (2 * x + 1, 2 * y + 1) for x, y in c) + 'Z')
    return ''.join(out)


def raster(d, k=4):
    """the truth: path_d's polygons filled (even-odd) on a k x grid, averaged down to GH x GW (0..1)"""
    polys = [np.float32(re.findall(r'(\d+),(\d+)', m)) / UNIT for m in d.split('M') if m]
    big = np.zeros((GH * k, GW * k), np.uint8)
    cv2.fillPoly(big, [np.int32(np.round(q * k * 8)) for q in polys], 255, cv2.LINE_8, shift=3)
    return cv2.resize(big, (GW, GH), interpolation=cv2.INTER_AREA) / 255.0


def images(seed=20261115):
    """[(name, GH x GW uint8 0/255)] for the SLOTS slots, the same every time"""
    rng, out = np.random.default_rng(seed), []
    for k in range(SLOTS):
        if k % CHECK_EVERY == 0:
            out.append(('checker', checker()))
            continue
        while True:
            name, img = content(rng)
            if 0.03 < (img > 0).mean() < 0.97:
                break
        out.append((name, img))
    return out


PAGE = '''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DVS 정답 그림</title>
<style>
  /* dvsfuse_page.py (11.61): %(slots)d black-and-white images in the checker's 10 x 7 frame, %(slot)gs each, then again.
     Only white blinks (white <-> black every 100 ms). CSS only, so it also runs in a mail preview. */
  :root { --s: min(calc(100vw / 11), calc(100vh / 8)); }
  html, body { margin: 0; height: 100%%; background: #000; color: #888; font: 14px system-ui, sans-serif; overflow: hidden; }
  body { display: grid; place-items: center; }
  .stage { position: relative; width: calc(10 * var(--s)); height: calc(7 * var(--s));
    animation: blink .2s steps(1, end) infinite; }
  .stage svg { position: absolute; top: 0; left: 0; width: 100%%; height: 100%%; opacity: 0;
    animation: slot %(cycle)gs steps(1, end) infinite; }
  .stage svg:first-child { opacity: 1; }   /* no effect while animations run; if a viewer stops them: a still checker */
  @keyframes slot { from { opacity: 1 } %(frac)g%%, to { opacity: 0 } }
  @keyframes blink { from { opacity: 1 } 50%%, to { opacity: 0 } }
  #info { position: fixed; left: 8px; bottom: 8px; }
  button { font: inherit; padding: 6px 12px; }
</style>
</head>
<body>
<div class="stage" role="img" aria-label="깜빡이는 정답 그림">
%(imgs)s
</div>
<div id="info"><button id="fs" hidden>전체 화면</button> 그림 %(slots)d장, 장마다 %(slot)g초, %(minutes)g분마다 처음부터. 녹화는 페이지를 연 직후부터</div>
<script>
// Optional when scripts run: full screen (webkit prefix for iPad) and keep the screen on.
const fs = document.getElementById('fs'), el = document.documentElement;
const full = el.requestFullscreen || el.webkitRequestFullscreen;
if (full) { fs.hidden = false; fs.onclick = () => { full.call(el); navigator.wakeLock?.request('screen').catch(() => {}); }; }
</script>
</body>
</html>
'''


def page(imgs):
    tags = []
    for k, (name, img) in enumerate(imgs):
        delay = ' style="animation-delay: %gs"' % (k * SLOT_S) if k else ''
        tags.append('<svg viewBox="0 0 %d %d" preserveAspectRatio="none"%s><title>%d %s</title>'
                    '<path fill="#fff" fill-rule="evenodd" d="%s"/></svg>' % (GW * UNIT, GH * UNIT, delay, k, name, path_d(img)))
    cycle = len(imgs) * SLOT_S
    return PAGE % {'slots': len(imgs), 'slot': SLOT_S, 'cycle': cycle, 'frac': 100 / len(imgs),
                   'minutes': cycle / 60, 'imgs': '\n'.join(tags)}


def main():
    out = sys.argv[1]
    imgs = images()
    open(out, 'w').write(page(imgs))
    np.savez_compressed(out.rsplit('.', 1)[0] + '.npz', names=np.array([n for n, _ in imgs]),
                        images=np.array([x for _, x in imgs]), slot_s=SLOT_S,
                        truth=np.array([np.uint8(np.round(255 * raster(path_d(x)))) for _, x in imgs]))
    print('%s: %d images, %d s each, %d s cycle' % (out, len(imgs), SLOT_S, len(imgs) * SLOT_S))


if __name__ == '__main__':
    main()
