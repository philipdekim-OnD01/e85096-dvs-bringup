#!/usr/bin/env python3
"""Object count and size analysis, tracking and the SQLite record of the microscope viewer (scope.py).

Analysis (classic image processing, the method of ImageJ "Analyze Particles"; no training data):
  background = median of a 1/8 image, window twice the largest object -> blur (sigma = smallest diameter / 4)
  -> contrast (bg - img) / sqrt(bg) (img - bg for bright objects)
  -> threshold median + k MAD (k from the sensitivity) -> edges at half of each object's peak contrast
  -> open/close, fill holes -> optional split of touching objects
  (cores of the distance map above 0.7 of the object's largest distance; each pixel to its nearest core)
  -> connected components, kept when their equivalent diameter is in [min_um, max_um].
OpenCV comes from the opencv-python-headless wheel unpacked in ./py (install.sh); without it the record still
works and analysis is off.

  python3 scope_rec.py --selftest     synthetic discs: count, size, split, size range, bright objects,
                                      empty image, too many objects, tracking
"""
import csv, io, json, os, sqlite3, sys, time
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "py"))
try:
    import cv2
    HAVE_CV, CV_ERR = True, ""
except ImportError as e:
    cv2, HAVE_CV, CV_ERR = None, False, f"OpenCV 없음: 분석이 꺼져 있습니다 ({e})"

MAX_OBJS = 5000            # objects kept per photo (the largest); count still covers all of them
SPLIT_CORE = 0.7           # seed = part of an object whose distance to the edge is above 0.7 of its maximum


def analyze(img, pixel_um, dark=True, min_um=5.0, max_um=500.0, split=True, k_mad=3.0):
    """Objects in a grey uint8 image. k_mad: threshold in robust noise units (lower = more sensitive).
    Returns count, mean equivalent diameter (um, None without objects), total area (um^2),
    objs [(x, y, area_px)] (the MAX_OBJS largest), truncated, and mask (uint8, kept objects)."""
    min_um, max_um = sorted((float(min_um), float(max_um)))
    h, w = img.shape
    dmax = max_um / pixel_um
    # background: median over a window twice the largest object, on a 1/8 image (a full-size filter takes seconds).
    # A median gives the typical level of a textured sample; a closing gave its bright envelope (195 on a sample
    # whose median is 126) and missed every dark particle.
    k = 8
    small = cv2.resize(img, (max(1, w // k), max(1, h // k)), interpolation=cv2.INTER_AREA)
    ks = max(3, min(int(2 * dmax / k) | 1, min(small.shape) // 2 * 2 - 1))
    p = ks // 2
    bg = cv2.medianBlur(_pad_odd(small, p), ks)[p:-p, p:-p]
    bg = cv2.resize(bg.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    # smooth below the smallest object (sigma = a quarter of its diameter): finer sample texture is not an object
    f = cv2.GaussianBlur(img.astype(np.float32), (0, 0), max(0.5, min_um / pixel_um / 4))
    # contrast in noise units: sensor noise grows as sqrt(signal), so (bg - img) / sqrt(bg) has the same noise in
    # the dim corners as in the middle (a plain ratio has 1.7-3x more there and made false objects in an empty image)
    v = (bg - f) if dark else (f - bg)
    v /= np.sqrt(np.maximum(bg, 1.0))
    # threshold: k_mad robust noise sigmas (MAD) above the median
    sub = v[::4, ::4]
    med = float(np.median(sub))
    mad = float(np.median(np.abs(sub - med))) * 1.4826
    thr = med + max(k_mad * mad, 1e-3)
    mask = (v > thr).astype(np.uint8)
    _half_max(mask, v, thr)
    ker = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(cv2.morphologyEx(mask, cv2.MORPH_OPEN, ker), cv2.MORPH_CLOSE, ker)
    cs, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mask[:] = 0
    cv2.drawContours(mask, cs, -1, 1, cv2.FILLED)          # holes filled: ring-shaped shadows count as one object
    if split:
        _split(mask, max(1.0, min_um / pixel_um / 2))
    n, lab, st, cen = cv2.connectedComponentsWithStats(mask, connectivity=8)
    area = st[1:, cv2.CC_STAT_AREA].astype(np.float64)
    d_um = 2 * np.sqrt(area / np.pi) * pixel_um
    keep = np.nonzero((d_um >= min_um) & (d_um <= max_um))[0]
    order = keep[np.argsort(-area[keep])]
    shown = order[:MAX_OBJS]
    lut = np.zeros(n, np.uint8)
    lut[shown + 1] = 1
    return {"count": int(len(keep)),
            "mean_d_um": round(float(d_um[keep].mean()), 3) if len(keep) else None,
            "total_area_um2": round(float(area[keep].sum()) * pixel_um ** 2, 1),
            "objs": [(round(float(cen[i + 1][0]), 1), round(float(cen[i + 1][1]), 1), int(area[i])) for i in shown],
            "truncated": len(keep) > MAX_OBJS, "mask": lut[lab]}


def _half_max(mask, v, thr):
    """Edges at half of each object's peak contrast, in place. Found at the noise threshold, a blurred object is
    measured at its foot: a 40 um disc came out 57 um at the 20 um smoothing; at half maximum it stays 40."""
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for i in range(1, n):
        x, y, bw, bh, _ = st[i]
        obj = lab[y:y + bh, x:x + bw] == i
        vv = v[y:y + bh, x:x + bw]
        half = 0.5 * float(vv[obj].max())
        if half > thr:
            mask[y:y + bh, x:x + bw][obj & (vv <= half)] = 0


def _pad_odd(a, p):
    """Pad by point reflection about the edge (2 * edge - mirror image): a linear trend continues outwards, so a
    median near the border is not pulled inwards (OpenCV's replicated border lifted the LED-dim corners 14 grey)."""
    a = a.astype(np.float32)
    a = np.vstack([2 * a[:1] - a[p:0:-1], a, 2 * a[-1:] - a[-2:-p - 2:-1]])
    a = np.hstack([2 * a[:, :1] - a[:, p:0:-1], a, 2 * a[:, -1:] - a[:, -2:-p - 2:-1]])
    return np.clip(a + 0.5, 0, 255).astype(np.uint8)


def _split(mask, rmin):
    """Cut touching objects apart in place: inside each object with two or more cores (distance to the edge above
    SPLIT_CORE of its maximum), every pixel goes to its nearest core and a 2 px line is cut where they meet.
    (cv2.watershed floods by neighbour differences and on this flat distance map cut 507 px into 303 + 37.)"""
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for i in range(1, n):
        x, y, bw, bh, a = st[i]
        if a < 2 * np.pi * rmin * rmin:               # too small to hold two objects of the smallest size
            continue
        obj = lab[y:y + bh, x:x + bw] == i
        d = np.where(obj, dist[y:y + bh, x:x + bw], 0)
        dm = float(d.max())
        if dm < rmin:
            continue
        cores = (d > SPLIT_CORE * dm).astype(np.uint8)
        if cv2.connectedComponents(cores, connectivity=8)[0] <= 2:     # one core: nothing to split
            continue
        _, near = cv2.distanceTransformWithLabels(1 - cores, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_CCOMP)
        cut = np.zeros(obj.shape, bool)                # both sides of every label change: no 8-connected leak
        hz, vt = near[:, :-1] != near[:, 1:], near[:-1, :] != near[1:, :]
        cut[:, :-1] |= hz
        cut[:, 1:] |= hz
        cut[:-1, :] |= vt
        cut[1:, :] |= vt
        mask[y:y + bh, x:x + bw][obj & cut] = 0


def overlay_jpg(img, res, quality=85):
    """JPEG of the photo with the outlines of the kept objects and the count."""
    out = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    cs, _ = cv2.findContours(res["mask"], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    t = max(1, img.shape[1] // 1000)
    cv2.drawContours(out, cs, -1, (224, 208, 63), t)
    txt = f"N={res['count']}" + (f"  d={res['mean_d_um']:.1f} um" if res["mean_d_um"] else "")
    cv2.putText(out, txt, (12, 16 + 18 * t), cv2.FONT_HERSHEY_SIMPLEX, 0.6 * t, (0, 0, 0), 4 * t)
    cv2.putText(out, txt, (12, 16 + 18 * t), cv2.FONT_HERSHEY_SIMPLEX, 0.6 * t, (68, 181, 242), t)
    return cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()


def _match(prev, cur):
    """Greedy one-to-one nearest match of centroids: [(i_cur, i_prev)], distance <= max(diameter, 10 px)."""
    if not prev or not cur:
        return []
    p = np.array([(x, y) for x, y, _ in prev])
    order = np.argsort(p[:, 0])
    px = p[order, 0]
    pairs = []
    for i, (x, y, a) in enumerate(cur):
        r = max(2 * np.sqrt(a / np.pi), 10.0)
        lo, hi = np.searchsorted(px, x - r), np.searchsorted(px, x + r, "right")
        for j in order[lo:hi]:
            dd = float(np.hypot(p[j, 0] - x, p[j, 1] - y))
            if dd <= r:
                pairs.append((dd, i, int(j)))
    pairs.sort()
    used_c, used_p, out = set(), set(), []
    for _, i, j in pairs:
        if i not in used_c and j not in used_p:
            used_c.add(i)
            used_p.add(j)
            out.append((i, j))
    return out


# ---------------------------------------------------------------- record (SQLite)
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started REAL, ended REAL, sample TEXT, every_s INTEGER, opts TEXT);
CREATE TABLE IF NOT EXISTS photos(id INTEGER PRIMARY KEY, run_id INTEGER, name TEXT UNIQUE, t REAL, time TEXT,
  exp INTEGER, led INTEGER, pixel_um REAL, mean REAL, count INTEGER, mean_d_um REAL, total_area_um2 REAL,
  truncated INTEGER, ov INTEGER, png INTEGER, opts TEXT);
CREATE INDEX IF NOT EXISTS photos_run ON photos(run_id, t);
CREATE TABLE IF NOT EXISTS objects(photo_id INTEGER, x REAL, y REAL, area_um2 REAL, d_um REAL, track INTEGER);
CREATE INDEX IF NOT EXISTS objects_photo ON objects(photo_id);
"""
PHOTO_COLS = "id run_id name t time exp led pixel_um mean count mean_d_um total_area_um2 truncated ov png".split()


class Rec:
    def __init__(self, path):
        self.path = path
        with self._db() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    def _db(self):                                   # one connection per call: HTTP and capture threads both use it
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    def _q(self, sql, args=()):
        c = self._db()
        try:
            return [dict(r) for r in c.execute(sql, args)]
        finally:
            c.close()

    def new_run(self, sample, every_s, opts):
        c = self._db()
        try:
            with c:
                return c.execute("INSERT INTO runs(started, sample, every_s, opts) VALUES(?,?,?,?)",
                                 (time.time(), sample, every_s, json.dumps(opts, ensure_ascii=False))).lastrowid
        finally:
            c.close()

    def end_run(self, run_id):
        c = self._db()
        try:
            with c:
                c.execute("UPDATE runs SET ended=? WHERE id=? AND ended IS NULL", (time.time(), run_id))
        finally:
            c.close()

    def add_photo(self, run_id, name, meta, res, pixel_um, track=False):
        """One photo and its objects. meta: t, time, exp, led, mean, ov, png, opts. res: analyze() or None."""
        objs = res["objs"] if res else []
        tracks = [None] * len(objs)
        c = self._db()
        try:
            with c:
                if track and run_id is not None and objs:
                    last = c.execute("SELECT id FROM photos WHERE run_id=? ORDER BY t DESC LIMIT 1", (run_id,)).fetchone()
                    prev = c.execute("SELECT x, y, area_um2, track FROM objects WHERE photo_id=?",
                                     (last[0],)).fetchall() if last else []
                    nxt = (c.execute("SELECT MAX(o.track) FROM objects o JOIN photos p ON p.id=o.photo_id WHERE p.run_id=?",
                                     (run_id,)).fetchone()[0] or 0) + 1
                    for i, j in _match([(r[0], r[1], r[2] / pixel_um ** 2) for r in prev], objs):
                        tracks[i] = prev[j][3]
                    for i in range(len(objs)):
                        if tracks[i] is None:
                            tracks[i], nxt = nxt, nxt + 1
                pid = c.execute("INSERT INTO photos(run_id, name, t, time, exp, led, pixel_um, mean, count, mean_d_um, "
                                "total_area_um2, truncated, ov, png, opts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (run_id, name, meta["t"], meta["time"], meta.get("exp"), meta.get("led"), pixel_um,
                                 meta.get("mean"), res["count"] if res else None, res["mean_d_um"] if res else None,
                                 res["total_area_um2"] if res else None, int(bool(res and res["truncated"])),
                                 int(bool(meta.get("ov"))), int(bool(meta.get("png", True))),
                                 json.dumps(meta.get("opts", {}), ensure_ascii=False))).lastrowid
                c.executemany("INSERT INTO objects(photo_id, x, y, area_um2, d_um, track) VALUES(?,?,?,?,?,?)",
                              [(pid, x, y, round(a * pixel_um ** 2, 1), round(2 * np.sqrt(a / np.pi) * pixel_um, 2), tr)
                               for (x, y, a), tr in zip(objs, tracks)])
                return pid
        finally:
            c.close()

    def runs(self):
        return self._q("SELECT r.*, COUNT(p.id) n, MAX(p.t) last FROM runs r LEFT JOIN photos p ON p.run_id=r.id "
                       "GROUP BY r.id ORDER BY r.id DESC") + \
            self._q("SELECT NULL id, MIN(t) started, NULL ended, '수동 촬영' sample, NULL every_s, NULL opts, COUNT(*) n, "
                    "MAX(t) last FROM photos WHERE run_id IS NULL")

    def count(self, run_id):
        return self._q("SELECT COUNT(*) n FROM photos WHERE run_id IS ?", (run_id,))[0]["n"]

    def last_t(self, run_id):
        return self._q("SELECT MAX(t) t FROM photos WHERE run_id IS ?", (run_id,))[0]["t"]

    def photos(self, run_id):
        return self._q(f"SELECT {', '.join(PHOTO_COLS)} FROM photos WHERE run_id IS ? ORDER BY t", (run_id,))

    def objects(self, photo_id):
        return [[r["x"], r["y"], r["d_um"], r["area_um2"], r["track"]]
                for r in self._q("SELECT * FROM objects WHERE photo_id=? ORDER BY d_um DESC", (photo_id,))]

    def tracks(self, run_id, k=8):
        """Diameter over time of the k objects seen in the most photos: [{track, pts: [[t, d_um], ...]}]."""
        top = self._q("SELECT o.track, COUNT(*) c FROM objects o JOIN photos p ON p.id=o.photo_id "
                      "WHERE p.run_id IS ? AND o.track IS NOT NULL GROUP BY o.track HAVING c > 1 "
                      "ORDER BY c DESC, o.track LIMIT ?", (run_id, k))
        out = []
        for r in top:
            pts = self._q("SELECT p.t, o.d_um FROM objects o JOIN photos p ON p.id=o.photo_id "
                          "WHERE p.run_id IS ? AND o.track=? ORDER BY p.t", (run_id, r["track"]))
            out.append({"track": r["track"], "pts": [[p["t"], p["d_um"]] for p in pts]})
        return out

    def csv(self, run_id, what="photos"):
        f = io.StringIO()
        f.write("﻿")                            # Excel reads UTF-8 (Korean sample names) only with a BOM
        w = csv.writer(f)
        if what == "objects":
            w.writerow(["time", "photo", "x_px", "y_px", "d_um", "area_um2", "track"])
            for r in self._q("SELECT p.time, p.name, o.x, o.y, o.d_um, o.area_um2, o.track FROM objects o "
                             "JOIN photos p ON p.id=o.photo_id WHERE p.run_id IS ? ORDER BY p.t, o.d_um DESC", (run_id,)):
                w.writerow(r.values())
        else:
            w.writerow(["time", "count", "mean_d_um", "total_area_um2", "exp_100us", "led", "pixel_um", "mean_grey",
                        "truncated", "photo"])
            for r in self.photos(run_id):
                w.writerow([r["time"], r["count"], r["mean_d_um"], r["total_area_um2"], r["exp"], r["led"],
                            r["pixel_um"], r["mean"], r["truncated"], r["name"]])
        return f.getvalue()


# ---------------------------------------------------------------- self-test
def _synth(discs, w=2016, h=1536, noise=2.0, seed=1):
    """Grey image: LED fall-off (dark corners), dark discs (x, y, r) at 45 % brightness, shot noise (sigma
    grows as sqrt(signal), `noise` grey levels at 180)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = 180 - 60 * (((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
    m = np.ones((h, w), np.float32)
    for x, y, r in discs:
        cv2.circle(m, (int(x), int(y)), int(r), 0.45, -1)
    img = img * m
    img += np.random.default_rng(seed).normal(0, 1, (h, w)).astype(np.float32) * np.sqrt(img / 180) * noise
    return np.clip(img + 0.5, 0, 255).astype(np.uint8)


def selftest():
    import tempfile
    px = 2.24
    small = [(300 + 300 * i, 300, 2) for i in range(5)]         # 10 um discs
    big = [(300 + 300 * i, 800, 9) for i in range(5)]           # 40 um discs
    pair = [(1500, 1200, 9), (1518, 1200, 9)]                    # two 40 um discs touching
    img = _synth(small + big + pair)
    t0 = time.time()
    r = analyze(img, px)
    dt = time.time() - t0
    assert r["count"] == 12, r["count"]
    assert analyze(img, px, split=False)["count"] == 11
    bigd = [2 * np.sqrt(a / np.pi) * px for x, y, a in r["objs"] if y == 800 or abs(y - 800) < 2]
    assert len(bigd) == 5 and all(abs(d - 40) / 40 < 0.15 for d in bigd), bigd
    assert analyze(img, px, min_um=20)["count"] == 7
    assert analyze(255 - img, px, dark=False)["count"] == 12
    e = analyze(_synth([]), px)
    assert e["count"] == 0 and e["mean_d_um"] is None and e["objs"] == [], (e["count"], e["objs"][:5])
    noisy = [analyze(_synth([], noise=6, seed=sd), px)["count"] for sd in (1, 2)]     # three times the noise
    assert max(noisy) <= 1 and analyze(_synth([], noise=6, seed=2), px, k_mad=5)["count"] == 0, noisy
    dots = [(60 + 18 * i, 80 + 18 * j, 2) for i in range(100) for j in range(60)]
    many = analyze(_synth(dots), px, min_um=1, k_mad=5)   # tests the cap, not the sensitivity
    assert many["count"] == 6000 and len(many["objs"]) == MAX_OBJS and many["truncated"], many["count"]
    jpg = overlay_jpg(img, r)
    assert jpg[:2] == b"\xff\xd8"
    with tempfile.TemporaryDirectory() as d:
        rec = Rec(os.path.join(d, "t.db"))
        run = rec.new_run("시험", 60, {})
        meta = {"t": 1.0, "time": "t1", "exp": 1, "led": 255, "mean": 100}
        p1 = rec.add_photo(run, "a", meta, r, px, track=True)
        moved = analyze(_synth([(x + 3, y + 2, rr) for x, y, rr in small + big + pair] + [(1800, 300, 9)], seed=2), px, k_mad=5)
        p2 = rec.add_photo(run, "b", dict(meta, t=2.0, time="t2"), moved, px, track=True)
        t1 = sorted(o[4] for o in rec.objects(p1))
        t2 = sorted(o[4] for o in rec.objects(p2))
        assert t1 == list(range(1, 13)) and t2 == list(range(1, 14)), (t1, t2)
        assert len(rec.tracks(run)) == 8 and rec.count(run) == 2 and rec.last_t(run) == 2.0
        assert rec.csv(run).count("\n") == 3 and rec.csv(run, "objects").count("\n") == 26
        rec.add_photo(None, "m", dict(meta, t=3.0), None, px)
        assert rec.runs()[-1]["n"] == 1 and rec.photos(None)[0]["count"] is None
    print(f"selftest ok (analysis {dt:.2f} s for {img.shape[1]}x{img.shape[0]})")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
