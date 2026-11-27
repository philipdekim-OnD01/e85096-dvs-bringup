// SPDX-License-Identifier: GPL-2.0
/*
 * dvshs: DVS events + a slow camera's blurred frames -> 1 ms frames, a high-speed camera look (linux510 docs 11.121).
 *
 * EDI (Pan et al., CVPR 2019): a camera frame is its exposure's mean of the latent image L(t), and the events say how
 * log L moved: R(t) = sum of c (ON) or -c*ratio (OFF) since the exposure start. So per pixel
 *   L(f) = B / mean_exposure exp(R(t) - R(f))     (the sharp frame at f, the exposure's middle)
 * and between two such key frames a, b each 1 ms frame is log L(t) = R'(t) + (1-w) log L_a + w (log L_b - R'(f_b)),
 * R' counted from f_a, w = (t - f_a) / (f_b - f_a): forward from a and back from b, weighted, so errors do not pile up
 * past the next key. The events are summed on the DVS grid; a camera pixel reads its DVS pixel through MAP (-1: none,
 * that pixel is the keys' linear blend). Colour (U, V) is the keys' blend.
 *
 *   dvshs fuse EVENTS CAM TIMES MAP [--c 0.2] [--ratio 1 | --ratio-map F] [--T 60000] [--step 1000] [--from 0] [--to inf]
 *              [--keys 1] [--blur 0] [--layout fused|sbs] [--eval]  > frames.i420
 *     EVENTS  struct ev (dvsview-t --replay), times in replay us; CAM  640x480 YUYV frames; TIMES  a line per frame,
 *     its exposure start in replay us; MAP  int32 x 640x480, y*960+x or -1. sbs: [keys' linear blend | fused].
 *     --ratio-map: float32 x 960x720, OFF takes c * its value at the pixel (dvshs.py onoff_ratio_map()).
 *     --eval: keys are every 2nd frame; the frames between are the truth: the fused frames over such a frame's
 *     exposure, averaged, against it (PSNR of Y where MAP >= 0), next to the nearest key and the keys' blend: one
 *     JSON line.
 *   dvshs --selftest   a synthetic spinning fan, exit 0 if the fused frames beat both baselines
 *
 * Build: cc -O2 -Wall -o dvshs dvshs.c -lm   (Mac); the board: gcc -O2 -Wall -fopenmp (Debian 12 arm64). dvshs.py
 * runs it with 4 threads beside the live DVS: 1 s recorded, 88 keys, 994 frames: deblur 9.3 s, all 39-45 s with
 * ffmpeg (11.121)
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define CW 640				/* camera */
#define CH 480
#define DW 960				/* DVS */
#define DH 720
#define EV_Y 0x3ff
#define EV_SUP 0x4000			/* noise mark: none in a replay, skipped anyway */
#define EV_OFF 0x8000
#define RCLAMP 20.0f			/* |R| in exp(): e^20, 66 steps still fit a float */
#define LMIN (-6.2f)			/* log of the darkest linear level (0.002) */
#define LUTN 4096
#ifdef _OPENMP				/* gcc -fopenmp (the board): the per-pixel loops split, each pixel's sums unchanged */
#define PAR _Pragma("omp parallel for schedule(static)")
#else
#define PAR
#endif

struct ev {
	uint32_t t;			/* replay display clock, us */
	uint16_t x;
	uint16_t y;			/* EV_Y | EV_SUP | EV_OFF */
};

struct fz {
	int w, h, dw, dh;
	const int32_t *map;		/* w*h -> DVS pixel or -1 */
	float c, ratio;			/* ON adds c, OFF takes c*ratio (log units) */
	const float *rmap;		/* dw*dh: a ratio per DVS pixel instead (--ratio-map), or NULL */
	int64_t T;			/* exposure, us */
	const struct ev *ev;
	size_t nev;
	float *R;			/* dw*dh: the running sum */
	float *a, *b;			/* w*h scratch */
	int blur;			/* read R as the mean of (2 blur + 1)^2 DVS pixels: ~2.25 of them per camera pixel */
	int *roi, nroi;			/* the camera pixels with a DVS pixel: the only ones fused (the rest is the keys' blend) */
};

static void set_roi(struct fz *z)
{
	z->roi = malloc(sizeof(int) * z->w * z->h);
	z->nroi = 0;
	for (int p = 0; p < z->w * z->h; p++)
		if (z->map[p] >= 0)
			z->roi[z->nroi++] = p;
}

typedef void (*frame_cb)(void *arg, int64_t t, float w, const float *logL);

static float lin_lut[256];		/* Y (limited range) -> linear */
static uint8_t y_lut[LUTN];		/* log L in [LMIN, 0] -> Y */

static void luts(void)
{
	for (int i = 0; i < 256; i++) {
		float v = (i - 16) / 219.0f;

		lin_lut[i] = powf(v < 0.002f ? 0.002f : v > 1 ? 1 : v, 2.2f);
	}
	for (int i = 0; i < LUTN; i++)
		y_lut[i] = (uint8_t)lrintf(16 + 219 * powf(expf(LMIN + (0 - LMIN) * i / (LUTN - 1)), 1 / 2.2f));
}

static inline uint8_t y_of_log(float l)
{
	if (!(l > LMIN))		/* NaN too */
		return y_lut[0];
	if (l >= 0)
		return y_lut[LUTN - 1];
	return y_lut[(int)((l - LMIN) * (LUTN - 1) / (0 - LMIN) + 0.5f)];
}

static inline uint8_t yuyv_y(const uint8_t *f, int p)
{
	return f[2 * p];
}

static double now_ms(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

/* the first event after t (the decoder wrote them in time order) */
static size_t seek(const struct fz *z, int64_t t)
{
	size_t lo = 0, hi = z->nev;

	while (lo < hi) {
		size_t mid = lo + (hi - lo) / 2;

		if ((int64_t)z->ev[mid].t <= t)
			lo = mid + 1;
		else
			hi = mid;
	}
	return lo;
}

/* add the events of (.., t] from i into R; returns the next */
static size_t accum(struct fz *z, size_t i, int64_t t)
{
	float on = z->c, off = -z->c * z->ratio;

	for (; i < z->nev && (int64_t)z->ev[i].t <= t; i++) {
		const struct ev *e = &z->ev[i];
		int y = e->y & EV_Y;

		if (!(e->y & EV_SUP) && e->x < z->dw && y < z->dh) {
			int k = y * z->dw + e->x;

			z->R[k] += !(e->y & EV_OFF) ? on : z->rmap ? -z->c * z->rmap[k] : off;
		}
	}
	return i;
}

static inline float rd(const struct fz *z, int p)
{
	int k = z->map[p], b = z->blur, x, y, n = 0;
	float s = 0;

	if (k < 0 || !b)
		return k < 0 ? 0 : z->R[k];
	x = k % z->dw;
	y = k / z->dw;
	for (int j = y - b; j <= y + b; j++)
		for (int i = x - b; i <= x + b; i++)
			if (i >= 0 && i < z->dw && j >= 0 && j < z->dh)
				s += z->R[j * z->dw + i], n++;
	return s / n;
}

/* key frame (YUYV) exposed over [s, s + T] -> log L at its middle f: log B + R(f) - log mean exp(R), R from s */
static void deblur_key(struct fz *z, const uint8_t *yuyv, int64_t s, float *logL)
{
	int n = z->nroi, M = z->T >= 2000 ? (int)(z->T / 1000) : 1, have_f = 0;
	int64_t f = s + z->T / 2;
	float *S = z->a, *rf = z->b;
	size_t i = seek(z, s);

	memset(z->R, 0, sizeof(float) * z->dw * z->dh);
	memset(S, 0, sizeof(float) * z->w * z->h);
	for (int m = 0; m < M; m++) {
		int64_t tm = s + (2 * m + 1) * z->T / (2 * M);

		if (!have_f && f <= tm) {
			i = accum(z, i, f);
			PAR
			for (int q = 0; q < n; q++)
				rf[z->roi[q]] = rd(z, z->roi[q]);
			have_f = 1;
		}
		i = accum(z, i, tm);
		PAR
		for (int q = 0; q < n; q++) {
			int p = z->roi[q];
			float r = rd(z, p);

			S[p] += expf(r > RCLAMP ? RCLAMP : r < -RCLAMP ? -RCLAMP : r);
		}
	}
	PAR
	for (int q = 0; q < n; q++) {
		int p = z->roi[q];

		logL[p] = logf(lin_lut[yuyv_y(yuyv, p)]) + rf[p] - logf(S[p] / M);
	}
}

/* frames at the grid t0 + j*step inside [fa, fb) and [t0, t1), between keys log L_a at fa and log L_b at fb */
static void fuse_interval(struct fz *z, const float *la, const float *lb, int64_t fa, int64_t fb, int64_t t0,
			  int64_t t1, int64_t step, frame_cb cb, void *arg)
{
	int n = z->nroi;
	float *tot = z->a, *out = z->b;
	int64_t t = fa > t0 ? t0 + (fa - t0 + step - 1) / step * step : t0;
	size_t i;

	if (t >= fb || t >= t1)
		return;
	memset(z->R, 0, sizeof(float) * z->dw * z->dh);
	accum(z, seek(z, fa), fb);
	PAR
	for (int q = 0; q < n; q++)
		tot[z->roi[q]] = rd(z, z->roi[q]);
	memset(z->R, 0, sizeof(float) * z->dw * z->dh);
	for (i = seek(z, fa); t < fb && t < t1; t += step) {
		float w = (float)(t - fa) / (float)(fb - fa);

		i = accum(z, i, t);
		PAR
		for (int q = 0; q < n; q++) {
			int p = z->roi[q];

			out[p] = rd(z, p) + (1 - w) * la[p] + w * (lb[p] - tot[p]);
		}
		cb(arg, t, w, out);
	}
}

/* Y plane: fused where the map has a DVS pixel, else the keys' linear blend */
static void fused_y(const struct fz *z, const float *logL, float w, const uint8_t *ka, const uint8_t *kb,
		    uint8_t *y, int stride)
{
	PAR
	for (int p = 0; p < z->w * z->h; p++) {
		uint8_t *d = &y[p / z->w * stride + p % z->w];

		if (z->map[p] < 0)
			*d = (uint8_t)lrintf(yuyv_y(ka, p) + w * (yuyv_y(kb, p) - yuyv_y(ka, p)));
		else
			*d = y_of_log(logL[p]);
	}
}

/* U, V planes (w/2 x h/2): the keys' blend; rows of the YUYV pairs averaged two by two */
static void blend_uv(int w, int h, float wt, const uint8_t *ka, const uint8_t *kb, uint8_t *u, uint8_t *v, int stride)
{
	PAR
	for (int cy = 0; cy < h / 2; cy++)
		for (int cx = 0; cx < w / 2; cx++) {
			const uint8_t *a0 = ka + (2 * cy * w + 2 * cx) * 2, *a1 = a0 + 2 * w;
			const uint8_t *b0 = kb + (2 * cy * w + 2 * cx) * 2, *b1 = b0 + 2 * w;
			float ua = (a0[1] + a1[1]) * 0.5f, va = (a0[3] + a1[3]) * 0.5f;
			float ub = (b0[1] + b1[1]) * 0.5f, vb = (b0[3] + b1[3]) * 0.5f;

			u[cy * stride + cx] = (uint8_t)lrintf(ua + wt * (ub - ua));
			v[cy * stride + cx] = (uint8_t)lrintf(va + wt * (vb - va));
		}
}

static void lin_y(int w, int h, float wt, const uint8_t *ka, const uint8_t *kb, uint8_t *y, int stride)
{
	PAR
	for (int p = 0; p < w * h; p++)
		y[p / w * stride + p % w] = (uint8_t)lrintf(yuyv_y(ka, p) + wt * (yuyv_y(kb, p) - yuyv_y(ka, p)));
}

/* ---- selftest: a fan of 4 blades turning 10 rev/s, events from the true log image at 0.1 ms ---- */

#define SW 64
#define SH 48
#define SKEYS 4
#define SC 0.25f

static float scene(int x, int y, int64_t t)
{
	float dx = x - SW / 2, dy = y - SH / 2, r = hypotf(dx, dy);
	double a;

	if (r > 20)
		return 0.5f;
	if (r < 4)
		return 0.3f;
	a = fmod(atan2(dy, dx) - 2 * M_PI * t / 100000.0 + 8 * M_PI, M_PI / 2);
	return a < M_PI / 4 ? 0.1f : 0.8f;
}

struct st {
	const struct fz *z;
	const uint8_t *ka, *kb;
	double sse[3];			/* fused, nearest key, linear blend */
	long n;
	int bad;			/* NaN or Y out of [16, 235] */
};

static void st_frame(void *arg, int64_t t, float w, const float *logL)
{
	struct st *s = arg;
	uint8_t y[SW * SH], l[SW * SH];

	fused_y(s->z, logL, w, s->ka, s->kb, y, SW);
	lin_y(SW, SH, w, s->ka, s->kb, l, SW);
	for (int p = 0; p < SW * SH; p++) {
		float tr = 16 + 219 * powf(scene(p % SW, p / SW, t), 1 / 2.2f);
		float nr = yuyv_y(w < 0.5f ? s->ka : s->kb, p);

		if (y[p] < 16 || y[p] > 235 || isnan(logL[p]))
			s->bad++;
		s->sse[0] += (y[p] - tr) * (y[p] - tr);
		s->sse[1] += (nr - tr) * (nr - tr);
		s->sse[2] += (l[p] - tr) * (l[p] - tr);
	}
	s->n += SW * SH;
}

static double psnr(double sse, long n)
{
	return n ? 10 * log10(255.0 * 255.0 / (sse / n + 1e-12)) : 0;
}

/* run the synthetic fan through deblur + fuse with c and the map; returns the stats */
static struct st st_run(const struct ev *ev, size_t nev, const int32_t *map, float c, uint8_t keys[SKEYS][SW * SH * 2],
			const int64_t *s)
{
	static float R[SW * SH], A[SW * SH], B[SW * SH], la[SW * SH], lb[SW * SH];
	struct fz z = { SW, SH, SW, SH, map, c, 1.0f, NULL, 50000, ev, nev, R, A, B };
	struct st st = { &z };

	set_roi(&z);
	deblur_key(&z, keys[0], s[0], la);
	for (int k = 0; k + 1 < SKEYS; k++) {
		deblur_key(&z, keys[k + 1], s[k + 1], lb);
		st.ka = keys[k];
		st.kb = keys[k + 1];
		fuse_interval(&z, la, lb, s[k] + z.T / 2, s[k + 1] + z.T / 2, 0, INT64_MAX, 1000, st_frame, &st);
		memcpy(la, lb, sizeof(la));
	}
	free(z.roi);
	return st;
}

static int selftest(void)
{
	static float ref[SW * SH];
	static uint8_t keys[SKEYS][SW * SH * 2];
	static int32_t map[SW * SH], none[SW * SH];
	size_t nev = 0, cap = 1 << 20;
	struct ev *ev = malloc(cap * sizeof(*ev));
	int64_t s[SKEYS];
	struct st a, b, c5;
	int fail = 0;

	for (int p = 0; p < SW * SH; p++) {
		ref[p] = logf(scene(p % SW, p / SW, 0));
		map[p] = p;
		none[p] = -1;
	}
	for (int64_t t = 100; t <= 300000; t += 100)	/* a DVS that fires at each c step of log L */
		for (int p = 0; p < SW * SH; p++) {
			float l = logf(scene(p % SW, p / SW, t));

			for (; l - ref[p] >= SC || ref[p] - l >= SC; ref[p] += l > ref[p] ? SC : -SC) {
				if (nev == cap)
					ev = realloc(ev, (cap *= 2) * sizeof(*ev));
				ev[nev++] = (struct ev){ (uint32_t)t, (uint16_t)(p % SW),
							 (uint16_t)(p / SW | (l > ref[p] ? 0 : EV_OFF)) };
			}
		}
	for (int k = 0; k < SKEYS; k++) {		/* 50 ms exposures, 66 ms apart (a 15 fps camera) */
		s[k] = 20000 + 66000LL * k;
		for (int p = 0; p < SW * SH; p++) {
			double m = 0;

			for (int64_t t = s[k] + 50; t < s[k] + 50000; t += 100)
				m += scene(p % SW, p / SW, t);
			keys[k][2 * p] = (uint8_t)lrint(16 + 219 * pow(m / 500, 1 / 2.2));
			keys[k][2 * p + 1] = 128;
		}
	}
	a = st_run(ev, nev, map, SC, keys, s);
	b = st_run(ev, nev, none, SC, keys, s);
	c5 = st_run(ev, nev, map, 5 * SC, keys, s);
	printf("selftest: %zu events, %ld px; PSNR fused %.1f dB, nearest key %.1f, linear blend %.1f\n", nev, a.n,
	       psnr(a.sse[0], a.n), psnr(a.sse[1], a.n), psnr(a.sse[2], a.n));
	if (!a.n || psnr(a.sse[0], a.n) < psnr(a.sse[1], a.n) + 3 || psnr(a.sse[0], a.n) < psnr(a.sse[2], a.n) + 3)
		fail = printf("FAIL: fused does not beat both baselines by 3 dB\n");
	if (!b.n || b.sse[0] > b.sse[2] + b.n * 0.25)	/* <= 1 LSB off per pixel, rounding only */
		fail = printf("FAIL: no map: fused %.1f dB is not the linear blend %.1f dB\n", psnr(b.sse[0], b.n),
			      psnr(b.sse[2], b.n));
	if (!c5.n || c5.bad)
		fail = printf("FAIL: c x5: %d NaN or out-of-range pixels\n", c5.bad);
	free(ev);
	if (!fail)
		printf("selftest ok\n");
	return fail ? 1 : 0;
}

/* ---- the CLI ---- */

struct out {
	const struct fz *z;
	const uint8_t *ka, *kb;
	int sbs;
	uint8_t *buf;
	long frames;
	double ms;
	double *acc;			/* --eval: linear L summed over a held-out frame's exposure */
	int nacc;
};

static void out_frame(void *arg, int64_t t, float w, const float *logL)
{
	struct out *o = arg;
	int cw = o->z->w, ch = o->z->h, W = o->sbs ? 2 * cw : cw;
	uint8_t *u = o->buf + W * ch, *v = u + W / 2 * (ch / 2);
	double t0 = now_ms();

	(void)t;
	if (o->sbs) {
		lin_y(cw, ch, w, o->ka, o->kb, o->buf, W);
		blend_uv(cw, ch, w, o->ka, o->kb, u, v, W / 2);
	}
	fused_y(o->z, logL, w, o->ka, o->kb, o->buf + W - cw, W);
	blend_uv(cw, ch, w, o->ka, o->kb, u + W / 2 - cw / 2, v + W / 2 - cw / 2, W / 2);
	if (fwrite(o->buf, W * ch * 3 / 2, 1, stdout) != 1) {
		perror("dvshs: stdout");
		exit(1);
	}
	o->frames++;
	o->ms += now_ms() - t0;
}

static void acc_frame(void *arg, int64_t t, float w, const float *logL)
{
	struct out *o = arg;

	(void)t; (void)w;
	for (int q = 0; q < o->z->nroi; q++) {
		int p = o->z->roi[q];

		o->acc[p] += expf(logL[p] > 0 ? 0 : logL[p] < LMIN ? LMIN : logL[p]);
	}
	o->nacc++;
}

static void *load(const char *path, size_t *size)
{
	int fd = open(path, O_RDONLY);
	struct stat st;
	void *p;

	if (fd < 0 || fstat(fd, &st)) {
		perror(path);
		exit(1);
	}
	*size = st.st_size;
	p = st.st_size ? mmap(NULL, st.st_size, PROT_READ, MAP_PRIVATE, fd, 0) : NULL;
	if (p == MAP_FAILED) {
		perror(path);
		exit(1);
	}
	close(fd);
	return p;
}

static int usage(void)
{
	fprintf(stderr, "usage: dvshs fuse EVENTS CAM TIMES MAP [--c X] [--ratio X] [--T us] [--step us] [--from us] "
		"[--to us] [--keys K] [--blur 0-3] [--layout fused|sbs] [--eval] [--ratio-map F] | dvshs --selftest\n");
	return 2;
}

static int fuse_main(int argc, char **argv)
{
	struct fz z = { CW, CH, DW, DH, NULL, 0.2f, 1.0f, NULL, 60000 };
	int64_t step = 1000, from = INT64_MIN, to = INT64_MAX, *s = NULL;
	int keys = 1, sbs = 0, eval = 0, nfr = 0, cap = 0;
	size_t evsize, camsize, mapsize, rsize;
	const uint8_t *cam;
	float *la, *lb;
	double t0 = now_ms(), dms = 0, fms;
	struct out o = { &z };
	FILE *fp;
	long long v;

	if (argc < 6)
		return usage();
	for (int i = 6; i < argc; i++) {
		const char *a = argv[i], *n = i + 1 < argc ? argv[i + 1] : NULL;

		if (!strcmp(a, "--eval"))
			eval = 1;
		else if (!n)
			return usage();
		else if (!strcmp(a, "--c"))
			z.c = strtof(n, NULL), i++;
		else if (!strcmp(a, "--ratio"))
			z.ratio = strtof(n, NULL), i++;
		else if (!strcmp(a, "--ratio-map")) {
			z.rmap = load(n, &rsize), i++;
			if (rsize != sizeof(float) * DW * DH)
				return fprintf(stderr, "dvshs: %s: %zu bytes, want %d\n", n, rsize,
					       (int)sizeof(float) * DW * DH), 1;
		}
		else if (!strcmp(a, "--T"))
			z.T = strtoll(n, NULL, 0), i++;
		else if (!strcmp(a, "--step"))
			step = strtoll(n, NULL, 0), i++;
		else if (!strcmp(a, "--from"))
			from = strtoll(n, NULL, 0), i++;
		else if (!strcmp(a, "--to"))
			to = strtoll(n, NULL, 0), i++;
		else if (!strcmp(a, "--blur"))
			z.blur = atoi(n), i++;
		else if (!strcmp(a, "--keys"))
			keys = atoi(n), i++;
		else if (!strcmp(a, "--layout"))
			sbs = !strcmp(n, "sbs"), i++;
		else
			return usage();
	}
	if (z.T < 1000 || step < 1 || keys < 1 || !(z.c > 0) || !(z.ratio > 0) || z.blur < 0 || z.blur > 3)
		return usage();
	z.ev = load(argv[2], &evsize);
	z.nev = evsize / sizeof(struct ev);
	cam = load(argv[3], &camsize);
	z.map = load(argv[5], &mapsize);
	if (mapsize != sizeof(int32_t) * CW * CH) {
		fprintf(stderr, "dvshs: %s: %zu bytes, want %d\n", argv[5], mapsize, (int)sizeof(int32_t) * CW * CH);
		return 1;
	}
	if (!(fp = fopen(argv[4], "r")))
		return perror(argv[4]), 1;
	while (fscanf(fp, "%lld", &v) == 1) {
		if (nfr == cap)
			s = realloc(s, (cap = cap ? 2 * cap : 256) * sizeof(*s));
		s[nfr++] = v;
	}
	fclose(fp);
	if ((size_t)nfr > camsize / (CW * CH * 2))
		nfr = camsize / (CW * CH * 2);
	if (eval)
		keys = 2;
	if (nfr < keys + 1) {
		fprintf(stderr, "dvshs: %d camera frames, need two keys\n", nfr);
		return 1;
	}
	set_roi(&z);
	z.R = malloc(sizeof(float) * DW * DH);
	z.a = malloc(sizeof(float) * CW * CH);
	z.b = malloc(sizeof(float) * CW * CH);
	la = malloc(sizeof(float) * CW * CH);
	lb = malloc(sizeof(float) * CW * CH);
	o.sbs = sbs;
	o.buf = malloc((size_t)(sbs ? 2 : 1) * CW * CH * 3 / 2);
	o.acc = calloc(CW * CH, sizeof(double));
	if (from == INT64_MIN)
		from = s[0] + z.T / 2;

	double sse[3] = { 0 };
	long px = 0;
	int held = 0;

	for (int k = 0, have = -1; k + keys < nfr; k += keys) {	/* have: the key in la */
		const uint8_t *ka = cam + (size_t)k * CW * CH * 2, *kb = ka + (size_t)keys * CW * CH * 2;
		int64_t fa = s[k] + z.T / 2, fb = s[k + keys] + z.T / 2;

		if (fb <= from || fa >= to)	/* the countdown's frames: no output, no deblur */
			continue;
		fms = now_ms();
		if (have != k)
			deblur_key(&z, ka, s[k], la);
		deblur_key(&z, kb, s[k + keys], lb);
		have = k + keys;
		dms += now_ms() - fms;
		o.ka = ka;
		o.kb = kb;
		if (!eval) {
			fuse_interval(&z, la, lb, fa, fb, from, to, step, out_frame, &o);
		} else {
			const uint8_t *kj = ka + (size_t)CW * CH * 2;	/* the held-out frame */

			memset(o.acc, 0, sizeof(double) * CW * CH);
			o.nacc = 0;
			fuse_interval(&z, la, lb, fa, fb, s[k + 1] + 500, s[k + 1] + z.T, 1000, acc_frame, &o);
			for (int p = 0; o.nacc && p < CW * CH; p++) {
				float tr = yuyv_y(kj, p), f = y_of_log(logf(o.acc[p] / o.nacc));
				float nr = yuyv_y(ka, p), li = lrintf((yuyv_y(ka, p) + yuyv_y(kb, p)) * 0.5f);

				if (z.map[p] < 0)
					continue;
				sse[0] += (f - tr) * (f - tr);
				sse[1] += (nr - tr) * (nr - tr);
				sse[2] += (li - tr) * (li - tr);
				px++;
			}
			held += o.nacc > 0;
		}
		memcpy(la, lb, sizeof(float) * CW * CH);
	}
	if (eval)
		printf("{\"frames\": %d, \"px\": %ld, \"psnr_fused\": %.2f, \"psnr_near\": %.2f, \"psnr_lin\": %.2f, "
		       "\"c\": %g, \"ratio\": %g, \"T\": %lld}\n", held, px, psnr(sse[0], px), psnr(sse[1], px),
		       psnr(sse[2], px), z.c, z.ratio, (long long)z.T);
	fprintf(stderr, "dvshs: %zu events, %d camera frames, keys every %d; %ld frames from %lld step %lld; "
		"deblur %.0f ms, write %.0f ms, total %.0f ms\n", z.nev, nfr, keys, o.frames, (long long)from,
		(long long)step, dms, o.ms, now_ms() - t0);
	fflush(stdout);
	return 0;
}

int main(int argc, char **argv)
{
	luts();
	if (argc == 2 && !strcmp(argv[1], "--selftest"))
		return selftest();
	if (argc >= 2 && !strcmp(argv[1], "fuse"))
		return fuse_main(argc, argv);
	return usage();
}
