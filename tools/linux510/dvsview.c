// SPDX-License-Identifier: GPL-2.0
/*
 * dvsview: S5KRC1S compressed AER packets -> event frames on /dev/fb0 at a fixed frame rate (11.27),
 * and box size measurement on the same frames (11.29).
 *
 * ov5647-cap.py --live --raw8 hands every VC0 buffer to dvs_ingest() through ctypes (the GIL is released
 * for the call): the buffer is copied out of the uncached dma-buf, decoded into a ring of (t, x, y,
 * polarity) events and zeroed. A render thread draws, every 1/fps s, the events of one frame period by
 * *sensor timestamp*, a playout delay behind real time. A VC0 buffer holds ~70 ms of events in a sparse
 * scene and buffers come ~14/s (11.25), so drawing per buffer can not reach 30 fps; drawing by
 * timestamp shows a new 33 ms slice every frame, ~one buffer period late.
 *
 * Boxes (dvs_boxes()): a box moving past fires along its edges across the motion; edges along the
 * motion barely change. Each frame the events of the last mwin us go into 8 x 8 px cells; cells with two
 * or more events and an active neighbour pass the noise filter, and the minimum-area rectangle around
 * their convex hull is the box (the two edge lines across the motion span it without the side edges).
 * Frames where the hull keeps clear of the border are samples; once the box has been gone END_FRAMES
 * frames the median sample becomes one JSON line in the output file (the MySQL uploader's queue) and the
 * label on screen. One box in view at a time; lengths are px x mm_per_px on the belt plane (no height).
 *
 * Packets (32-bit big-endian, manual Fig. 49-52):
 *   [31] = 1        EVENT GROUP, [30:26] offset (0 = SGROUP), [24:18] group address (8 rows each):
 *                   SGROUP [7:0] ON, [15:8] OFF; MGROUP [7:0] group1, [15:8] group1 + offset,
 *                   polarity bit 16 (group1) / 17 (group2), 1 = OFF
 *   [31:26] 000001  COLUMN, [10:0] x
 *   [31:26] 000010  TIMESTAMP, [23] = 0: reference [21:0] ms, [23] = 1: sub [9:0] us
 *   others (frame end, IMU, padding, the undefined 000000/000101 of 11.23) are skipped.
 *
 * Build (Debian 12 arm64, same glibc as the board):
 *   docker run --rm -v "$PWD/tools/linux510:/s" debian:12 sh -c 'apt-get -qq update &&
 *     apt-get -qq install -y gcc zlib1g-dev >/dev/null &&
 *     gcc -O2 -Wall -shared -fPIC -o /s/libdvsview.so /s/dvsview.c -lpthread -lm -lz &&
 *     gcc -O2 -Wall -DDVSVIEW_TEST -o /tmp/t /s/dvsview.c -lpthread -lm -lz && /tmp/t [capture.raw]'
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <time.h>
#include <unistd.h>
#include <zlib.h>

#define XS 960
#define YS 720
#define RING (1u << 22)			/* 4M events x 8 B: > 200 ms of a buffer full of events at 40 buffers/s */
#define STALL 300000			/* data this much off the delay: restart the playout clock */
#define BLACK 0xff000000u		/* XRGB8888, alpha set as in ov5647-cap.py */
#define ON_RGB 0xff00ff00u
#define OFF_RGB 0xffff0000u
#define BOX_RGB 0xffffffffu
#define EV_Y 0x3ff			/* struct ev y: row, */
#define EV_SUP 0x4000			/* a neighbour fired within filter_ms (set only while the filter is on), */
#define EV_OFF 0x8000			/* OFF polarity */
#define BEFORE(a, b) ((int32_t)((a) - (b)) < 0)	/* sensor-time compare on the 32-bit (71 min) clock */
#define SCAN 494			/* us, the scan period of the 2000 fps setfile (11.24) until one is measured */
#define HBIN 10				/* us per bin of the scan period histogram, */
#define HBINS 512			/* up to 5.12 ms (CIS margin <= ~140, 11.29) */
#define LINE 8192			/* bytes per MIPI line, both modes (8192 x 127, 8192 x 32) */
#define REC_Q (64u << 20)		/* bytes of packets queued for the recording writer: ~7 s at 9 MB/s */
#define BROW 104			/* bytes per column of a filter bitmap: 722 rows with the border, + an 8-byte read */

#define CELL 8				/* px, side of a measurement cell */
#define GX (XS / CELL)
#define GY (YS / CELL)
#define CELL_MIN 3			/* pixels fired in a cell in the window, at least; more where the background is */
#define BG_FRAMES 64			/* background time constant, measurements (2 s at 30 fps) */
#define MIN_CELLS 6			/* fewer cells pass the filter: no box in view */
#define MIN_SIDE 24			/* px: a thinner hull is one edge, the other one still out of view */
#define MIN_FRAMES 3			/* a box seen in fewer frames is not reported */
#define END_FRAMES 6			/* frames without a box that end it: 200 ms at 30 fps bridges data gaps */
#define EDGE 8				/* px: a hull this near the border is cut by the field of view */
#define MAX_SAMPLES 512

struct ev {
	uint32_t t;			/* display clock, us */
	uint16_t x;
	uint16_t y;			/* EV_Y | EV_SUP | EV_OFF */
};

static struct {
	struct ev *ring;
	_Atomic uint64_t wr;		/* events written; ring slot = index % RING */
	int col;			/* current column, -1 before the first COLUMN packet */
	uint32_t ref;			/* last reference timestamp, ms */
	int have_ts, bad;
	int64_t raw;			/* last accepted ref * 1000 + sub, us */
	int64_t t;			/* display clock: sum of the accepted forward steps */
	uint8_t *baf[2];		/* filter: pixels fired in slot k (baf[k & 1]) and k - 1, column-major, 1 px border */
	uint32_t slot, slot_us;		/* k = display clock / slot_us; slot_us = filter_ms / 2 */
	/* playout: show sensor time s at real time s + delay */
	_Atomic int64_t delay;
	_Atomic int64_t anchor;		/* >= 0: restart the render clock at this sensor time */
	_Atomic int64_t newest;		/* display clock after the last buffer */
	_Atomic int started;
	int64_t cov, tot;		/* sensor time with scans back to back (since the last buffer), all of it */
	/* the scan period follows the CIS margin (11.29): the most common timestamp step, every stats period */
	uint32_t hist[HBINS];		/* steps taken, HBIN us bins; decode thread counts, render thread clears */
	_Atomic int scan;		/* us; a step up to 3 scans (one or two lost) is back to back, a gap steps one */
	/* stats, written by one thread each, read loosely by the render thread */
	_Atomic uint64_t buffers, ingest_us, ingest_max, bytes, covered;
	/* frame buffer */
	uint8_t *fb;			/* top-left of the visible page */
	size_t stride;
	int xres, yres, zoom;
	uint32_t *back;
	char stats[2][512];		/* the last stats as JSON, double-buffered for dvs_stats() */
	_Atomic int stats_cur;
	/* dvs_record(): each line's packet words and a zero word; the decoder queues them, a writer thread gzips */
	pthread_mutex_t rec_lock;	/* the decoder's queueing against start and stop */
	int rec_on;			/* the decoder queues lines */
	int rec_busy;			/* a recording is open: until dvs_record(NULL) has joined its writer */
	uint64_t rec_events;		/* decoded while recording */
	uint8_t *recq;			/* REC_Q bytes, kept for the next recording */
	_Atomic uint64_t rq_head, rq_tail;	/* bytes queued, bytes written */
	_Atomic int rec_stop, rec_err;	/* drain and end; the queue overflowed or a write failed */
	pthread_t rec_thr;
	gzFile rec;
	_Atomic uint64_t act_lost;	/* events dvs_events() skipped: the decoder was about to overwrite them */
} S = { .col = -1, .anchor = -1, .zoom = -1, .scan = SCAN, .rec_lock = PTHREAD_MUTEX_INITIALIZER };

/* display settings: dvs_set() from the dvsctl page (ov5647-cap.py --ctl, 11.28), read by the render thread */
static struct param {
	const char *name;
	_Atomic int v;
	int def, min, max;
} P[] = {
	{ "fps", 30, 30, 1, 120 },		/* frames drawn per second */
	{ "window_ms", 0, 0, 0, 1000 },		/* sensor time per frame, 0 = one frame period */
	{ "fade", 0, 0, 0, 250 },		/* 0: clear every frame, else keep fade/256 of the last one (trails) */
	{ "filter_ms", 0, 0, 0, 100 },		/* 0: off, else draw an event only if a neighbour fired this recently */
	{ "color", 0, 0, 0, 4 },		/* ON/OFF: 0 green/red, 1 white/blue, 2 white/white, 3 ON only, 4 OFF only */
	{ "zoom", 0, 0, 0, 1 },			/* 0: 960x720, 1: 1440x1080, the full HDMI height */
	{ "flip", 0, 0, 0, 3 },			/* bit 0: mirror left-right, bit 1: upside down */
	{ "margin_ms", 15, 15, 0, 500 },	/* slack on the playout delay: less = lower latency, more late events */
};
enum { P_FPS, P_WINDOW, P_FADE, P_FILTER, P_COLOR, P_ZOOM, P_FLIP, P_MARGIN };
#define PV(i) atomic_load_explicit(&P[i].v, memory_order_relaxed)

static const uint32_t colors[5][2] = {
	{ ON_RGB, OFF_RGB }, { 0xffffffffu, 0xff3070ffu }, { 0xffffffffu, 0xffffffffu }, { ON_RGB, 0 }, { 0, OFF_RGB },
};

struct pt {
	float x, y;
};

struct rect {
	float l, w, ang;		/* px, l >= w; ang: direction of the l side, degrees in [0, 180) */
	struct pt c[4];			/* corners in order, for drawing */
};

static struct {
	FILE *out;
	float mm;			/* mm per px on the belt plane */
	int64_t win;			/* us of events per measurement, <= 100 ms (cnt) */
	uint16_t cnt[GY][GX], thr[GY][GX];
	float bg[GY][GX], bg2[GY][GX];	/* per cell, mean and mean square of the fired pixels per measurement */
	int warm;
	uint8_t ok[GY][GX];		/* cell passes the noise filter */
	uint8_t map[YS][XS];		/* pixel fired in the window */
	int16_t lo[XS], hi[XS];		/* lowest and highest y of a kept event per column */
	struct pt pts[2 * XS], hull[4 * XS];
	float l[MAX_SAMPLES], w[MAX_SAMPLES], ang[MAX_SAMPLES], cx[MAX_SAMPLES], cy[MAX_SAMPLES], ct[MAX_SAMPLES];
	int n, frames, miss, shown;	/* samples, frames with the box, frames without it, r is this frame's */
	float pl, pw;			/* largest box while cut by the border, for a box that never fits */
	struct rect r;
	char label[48];
} B;

static int64_t now_us(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1000000LL + ts.tv_nsec / 1000;
}

/* Timestamps carry bit errors (11.24: sub bit 8 dropped, lost ref packets; 11.28: bit 8 of the ms reference reads
 * at random, for a dozen packets in a row at times, bits 7 and 8 together too, and some buffers bring data from
 * ~133 ms back). A step of 0..100 ms forward is taken as it is, also after undoing one flipped bit of the
 * reference; one up to 1 ms back is skipped (a bad sub, or the reference not yet stepped). Any other is bad and
 * decode() drops the events until a good one; so is a corrected one inside a bad stretch, or a reference that is
 * off would keep the stretch going. 4 in a row forward are a real gap (> 100 ms), 64 others (~2 buffers) a counter
 * reset. The display clock steps one scan over a gap: a lost buffer (30-60 ms, ~0.6/s in 11.28) is then a repeated
 * frame instead of a black one. */
static void timestamp(int64_t raw)
{
	int64_t dt = raw - S.raw;
	int fixed = 0, scan;

	if (!S.have_ts) {
		S.raw = raw;
		S.have_ts = 1;
		return;
	}
	for (int b = 1; (dt <= -1000 || dt >= 100000) && b < 22; b++) {
		int64_t f = 1000LL << b;

		if (dt - f >= 0 && dt - f < 100000)
			dt -= f, fixed = 1;
		else if (dt + f >= 0 && dt + f < 100000)
			dt += f, fixed = 1;
	}
	if (dt < 0 && dt > -1000)
		return;
	if (dt < 0 || dt >= 100000 || (fixed && S.bad)) {
		if (++S.bad >= (dt >= 100000 ? 4 : 64)) {
			S.tot += dt > 0 && dt < 60000000 ? dt : 0;	/* a gap is sensor time without data */
			S.raw = raw;
			S.bad = 0;
		}
		return;
	}
	S.bad = 0;
	scan = atomic_load_explicit(&S.scan, memory_order_relaxed);
	S.t += dt <= 3 * scan ? dt : scan;
	S.tot += dt;
	S.cov += dt <= 3 * scan ? dt : 0;
	S.raw += dt;
	S.hist[dt < HBIN * HBINS ? dt / HBIN : 0]++;
}

/* bits of rows y - 1 .. y + 1 in a column of a filter bitmap (bit y + 1 is row y) */
static inline uint32_t rows3(const uint8_t *col, uint32_t y)
{
	uint64_t v;

	memcpy(&v, col + (y >> 3), 8);
	return v >> (y & 7) & 7;
}

/* A new display clock t for the filter: past a slot, clear the bitmap of the slot before last (both after a gap) */
static void slot(uint32_t t, uint32_t baf)
{
	uint32_t us = baf / 2, k = t / us;

	if (us != S.slot_us || k - S.slot > 1) {
		memset(S.baf[0], 0, (XS + 2) * BROW);
		memset(S.baf[1], 0, (XS + 2) * BROW);
	} else if (k != S.slot) {
		memset(S.baf[k & 1], 0, (XS + 2) * BROW);
	}
	S.slot = k;
	S.slot_us = us;
}

/*
 * baf > 0: background activity filter, EV_SUP if one of the 8 neighbours fired in this slot of baf/2 us or the
 * last one, baf/2 to baf before. One bit per pixel keeps the maps (200 kB) in cache: a 32-bit time per pixel
 * (2.8 MB) cost ~285 ns an event on the A55 (three DRAM misses in a row), 8x the decoding (11.28).
 */
static uint64_t put(uint64_t wr, uint32_t t, uint32_t addr, uint32_t bits, uint32_t off, uint32_t baf)
{
	while (bits) {
		uint32_t y = addr * 8 + __builtin_ctz(bits), sup = 0;
		struct ev *e;

		if (y >= YS)
			break;			/* bits climb: the rest of the group is off the array too */
		if (baf) {
			uint8_t *c = S.baf[S.slot & 1] + (S.col + 1) * BROW, *o = S.baf[~S.slot & 1] + (S.col + 1) * BROW;

			sup = (rows3(c - BROW, y) | rows3(c + BROW, y) | (rows3(c, y) & 5) |	/* & 5: not itself */
			       rows3(o - BROW, y) | rows3(o + BROW, y) | (rows3(o, y) & 5)) != 0;
			c[(y + 1) >> 3] |= 1 << ((y + 1) & 7);
		}
		e = &S.ring[wr++ % RING];
		e->t = t;
		e->x = S.col;
		e->y = y | off << 15 | sup << 14;
		bits &= bits - 1;
	}
	return wr;
}

/* Decode up to n words, to the first zero word (the line padding); returns the words read. */
static size_t decode(const uint32_t *p, size_t n)
{
	uint64_t wr = atomic_load_explicit(&S.wr, memory_order_relaxed);
	uint32_t t = S.t, baf = PV(P_FILTER) * 1000;
	size_t i;

	if (baf)
		slot(t, baf);
	for (i = 0; i < n; i++) {
		uint32_t w = __builtin_bswap32(p[i]);

		if (!w)
			break;
		if (w >> 31) {
			uint32_t off = w >> 26 & 0x1f, a1 = w >> 18 & 0x7f;

			if (S.col < 0 || S.col >= XS || S.bad)
				continue;
			if (!off) {
				wr = put(wr, t, a1, w & 0xff, 0, baf);
				wr = put(wr, t, a1, w >> 8 & 0xff, 1, baf);
			} else {
				wr = put(wr, t, a1, w & 0xff, w >> 16 & 1, baf);
				wr = put(wr, t, a1 + off, w >> 8 & 0xff, w >> 17 & 1, baf);
			}
			continue;
		}
		if ((w & 0xfffffc00) == 0x00800000)
			w |= 2u << 26;		/* a SUB whose type bits the link cleared: all of them for 256 ms at times (11.28) */
		switch (w >> 26) {
		case 1:
			S.col = w & 0x7ff;
			break;
		case 2:
			if (w >> 23 & 1) {
				timestamp((int64_t)S.ref * 1000 + (w & 0x3ff));
				t = S.t;
				if (baf)
					slot(t, baf);
			} else {
				S.ref = w & 0x3fffff;
			}
			break;
		}
	}
	atomic_store_explicit(&S.wr, wr, memory_order_release);
	return i;
}

/* Clean and invalidate [p, p + n) to DRAM (EL0 may: SCTLR_EL1.UCI); dsb sy after the last one. */
static void flush(const uint8_t *p, size_t n)
{
	static uintptr_t line;

	if (!line) {
		uint64_t ctr;

		asm volatile("mrs %0, ctr_el0" : "=r"(ctr));
		line = 4 << (ctr >> 16 & 15);	/* DminLine: log2 of the line in words */
	}
	for (uintptr_t a = (uintptr_t)p & ~(line - 1); a < (uintptr_t)p + n; a += line)
		asm volatile("dc civac, %0" : : "r"(a) : "memory");
}

/* Queue n bytes for the recording writer (under rec_lock); when the writer is REC_Q behind (or failed) the
 * recording loses its data, not the decoder its time: dvs_record(NULL) then says -EIO. */
static void rec_put(const void *p, size_t n)
{
	uint64_t h = atomic_load_explicit(&S.rq_head, memory_order_relaxed);
	size_t i = h % REC_Q, k = n < REC_Q - i ? n : REC_Q - i;

	if (atomic_load_explicit(&S.rec_err, memory_order_relaxed) ||
	    h + n - atomic_load_explicit(&S.rq_tail, memory_order_acquire) > REC_Q) {
		atomic_store(&S.rec_err, 1);
		return;
	}
	memcpy(S.recq + i, p, k);
	memcpy(S.recq, (const uint8_t *)p + k, n - k);
	atomic_store_explicit(&S.rq_head, h + n, memory_order_release);
}

/*
 * Decode a buffer of MIPI lines in place and zero the packets (bytesused is 0, so a zero is how the next frame
 * tells where its data ends); returns the bytes of packets. Every line of the 11.28 captures is one run of
 * packets from its start, then zeros: the padding (0-82% of a buffer) is never read.
 * The zeros go to DRAM here: Pablo unmaps a VC0 buffer at DQBUF (is_queue_buffer_finish, so DMA_BUF_IOCTL_SYNC
 * does nothing) and maps it at QBUF with an invalidate that drops dirty lines. A frame the DMA then filled in
 * part kept the rest of its last turn, events 8 buffers (~133 ms) old (11.28).
 */
static size_t decode_lines(uint8_t *buf, size_t len)
{
	size_t bytes = 0;
	uint64_t w0 = atomic_load_explicit(&S.wr, memory_order_relaxed);

	pthread_mutex_lock(&S.rec_lock);
	for (size_t l = 0; l + 4 <= len; l += LINE) {
		uint32_t *p = (uint32_t *)(buf + l);
		size_t n = decode(p, (len - l < LINE ? len - l : LINE) / 4);

		if (S.rec_on && n) {
			static const uint32_t zero;

			rec_put(p, n * 4);
			rec_put(&zero, 4);
		}
		memset(p, 0, n * 4);
		flush((uint8_t *)p, n * 4);
		bytes += n * 4;
	}
	if (S.rec_on)
		S.rec_events += atomic_load_explicit(&S.wr, memory_order_relaxed) - w0;
	pthread_mutex_unlock(&S.rec_lock);
	asm volatile("dsb sy" : : : "memory");
	return bytes;
}

/* One VC0 buffer, in the cached dma-buf mmap between DMA_BUF_IOCTL_SYNC start and end (ov5647-cap.py). */
int dvs_ingest(uint8_t *buf, size_t len)
{
	int64_t t0 = now_us(), s_first = S.t, d, delay;
	size_t n = decode_lines(buf, len);

	atomic_store(&S.newest, S.t);
	atomic_fetch_add(&S.covered, S.cov);
	S.cov = 0;

	/* the oldest event of this buffer became visible now: the delay has to cover it */
	d = now_us() - s_first;
	delay = atomic_load(&S.delay);
	if (!atomic_load(&S.started) || d > delay + STALL || d < delay - STALL) {	/* a stall, or the clock jumped */
		delay = d;
		atomic_store(&S.anchor, s_first);
		atomic_store(&S.started, 1);
	} else if (d > delay) {
		delay = d;
	} else {
		delay -= (delay - d) >> 8;	/* creep down, ~20 s time constant at 14 buffers/s */
	}
	atomic_store(&S.delay, delay);

	d = now_us() - t0;
	atomic_fetch_add(&S.buffers, 1);
	atomic_fetch_add(&S.bytes, n);
	atomic_fetch_add(&S.ingest_us, d);
	if ((uint64_t)d > atomic_load(&S.ingest_max))
		atomic_store(&S.ingest_max, d);
	return 0;
}

/* ---- box measurement ---- */

static int busy(int cx, int cy)
{
	return cx >= 0 && cx < GX && cy >= 0 && cy < GY && B.cnt[cy][cx] >= B.thr[cy][cx];
}

static int lit(int x, int y)
{
	return x >= 0 && x < XS && y >= 0 && y < YS && B.map[y][x];
}

/*
 * Noise filter for events [from, to): keep an event when its cell and a neighbouring cell have more fired pixels
 * than that cell's own background (mean + 4 sigma over the last ~2 s: hot pixels, the noisy bottom rows of 11.28)
 * and two neighbouring pixels fired too (edges are lines; the 1.9 Hz per pixel background is single pixels).
 * B.pts gets the lowest and highest kept y of each column in x order: their hull is the hull of the kept events.
 * Returns the cells that pass.
 */
static int collect(uint64_t from, uint64_t to, int *np)
{
	int ncell = 0, n = 0;

	memset(B.cnt, 0, sizeof(B.cnt));
	memset(B.map, 0, sizeof(B.map));
	for (uint64_t i = from; i < to; i++) {
		const struct ev *e = &S.ring[i % RING];
		int y = e->y & EV_Y;

		if (B.map[y][e->x])
			continue;		/* a hot pixel counts once */
		B.map[y][e->x] = 1;
		B.cnt[y / CELL][e->x / CELL]++;
	}
	for (int cy = 0; cy < GY; cy++)
		for (int cx = 0; cx < GX; cx++) {
			float c = B.cnt[cy][cx], m, s2;

			if (!B.warm) {
				B.bg[cy][cx] = c;
				B.bg2[cy][cx] = c * c + c;
			}
			m = B.bg[cy][cx];
			s2 = fmaxf(B.bg2[cy][cx] - m * m, m);	/* Poisson at least */
			B.thr[cy][cx] = fmaxf(CELL_MIN, ceilf(m + 4 * sqrtf(s2) + 1));
			c = fminf(c, B.thr[cy][cx]);	/* an edge going by barely moves it, lasting activity is learnt */
			B.bg[cy][cx] += (c - m) / BG_FRAMES;
			B.bg2[cy][cx] += (c * c - B.bg2[cy][cx]) / BG_FRAMES;
		}
	B.warm = 1;
	for (int cy = 0; cy < GY; cy++)
		for (int cx = 0; cx < GX; cx++) {
			int nb = 0;

			if (busy(cx, cy))
				for (int dy = -1; dy <= 1; dy++)
					for (int dx = -1; dx <= 1; dx++)
						nb += (dx || dy) && busy(cx + dx, cy + dy);
			B.ok[cy][cx] = nb > 0;
			ncell += nb > 0;
		}
	for (int x = 0; x < XS; x++) {
		B.lo[x] = YS;
		B.hi[x] = -1;
	}
	for (uint64_t i = from; i < to; i++) {
		const struct ev *e = &S.ring[i % RING];
		int x = e->x, y = e->y & EV_Y;

		if (!B.ok[y / CELL][x / CELL] || lit(x - 1, y - 1) + lit(x, y - 1) + lit(x + 1, y - 1) + lit(x - 1, y) +
		    lit(x + 1, y) + lit(x - 1, y + 1) + lit(x, y + 1) + lit(x + 1, y + 1) < 2)
			continue;
		if (y < B.lo[x])
			B.lo[x] = y;
		if (y > B.hi[x])
			B.hi[x] = y;
	}
	for (int x = 0; x < XS; x++) {
		if (B.hi[x] < 0)
			continue;
		B.pts[n++] = (struct pt){ x, B.lo[x] };
		if (B.hi[x] > B.lo[x])
			B.pts[n++] = (struct pt){ x, B.hi[x] };
	}
	*np = n;
	return ncell;
}

static float cross(struct pt o, struct pt a, struct pt b)
{
	return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
}

/* Andrew's monotone chain over p sorted by x, then y; h holds 2n points */
static int hull(const struct pt *p, int n, struct pt *h)
{
	int k = 0;

	for (int i = 0; i < n; i++) {
		while (k >= 2 && cross(h[k - 2], h[k - 1], p[i]) <= 0)
			k--;
		h[k++] = p[i];
	}
	for (int i = n - 2, lo = k + 1; i >= 0; i--) {
		while (k >= lo && cross(h[k - 2], h[k - 1], p[i]) <= 0)
			k--;
		h[k++] = p[i];
	}
	return k > 1 ? k - 1 : k;
}

/* area of the rectangle around h with sides at angle a (radians); fills r if given */
static float extent(const struct pt *h, int n, float a, struct rect *r)
{
	float c = cosf(a), s = sinf(a), u0 = 1e9f, u1 = -1e9f, v0 = 1e9f, v1 = -1e9f;

	for (int i = 0; i < n; i++) {
		float u = h[i].x * c + h[i].y * s, v = h[i].y * c - h[i].x * s;

		u0 = fminf(u0, u);
		u1 = fmaxf(u1, u);
		v0 = fminf(v0, v);
		v1 = fmaxf(v1, v);
	}
	if (r) {
		float uv[4][2] = { { u0, v0 }, { u1, v0 }, { u1, v1 }, { u0, v1 } }, deg = a * 180 / (float)M_PI;

		for (int i = 0; i < 4; i++)
			r->c[i] = (struct pt){ uv[i][0] * c - uv[i][1] * s, uv[i][0] * s + uv[i][1] * c };
		r->l = fmaxf(u1 - u0, v1 - v0);
		r->w = fminf(u1 - u0, v1 - v0);
		r->ang = fmodf((u1 - u0 >= v1 - v0 ? deg : deg + 90) + 360, 180);
	}
	return (u1 - u0) * (v1 - v0);
}

/* minimum-area rectangle: 1 degree search over 0..89, then 0.1 degree around the best */
static void minrect(const struct pt *h, int n, struct rect *r)
{
	float best = 1e30f, ba = 0, a0, a, area;

	for (int d = 0; d < 90; d++) {
		a = d * (float)M_PI / 180;
		area = extent(h, n, a, NULL);
		if (area < best) {
			best = area;
			ba = a;
		}
	}
	a0 = ba;
	for (int d = -10; d <= 10; d++) {
		a = a0 + d * (float)M_PI / 1800;
		area = extent(h, n, a, NULL);
		if (area < best) {
			best = area;
			ba = a;
		}
	}
	extent(h, n, ba, r);
}

static int cmp_f(const void *a, const void *b)
{
	float x = *(const float *)a, y = *(const float *)b;

	return (x > y) - (x < y);
}

static float median(float *v, int n)
{
	qsort(v, n, sizeof(*v), cmp_f);
	return n % 2 ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}

/* the box has gone: one JSON line, the screen label */
static void box_end(void)
{
	float l = B.pl, w = B.pw, ang = 0, v = 0, a0 = B.ang[0];
	struct timespec ts;
	struct tm tm;
	char when[32];

	if (B.n) {
		l = median(B.l, B.n);
		w = median(B.w, B.n);
		for (int i = 0; i < B.n; i++)		/* angles wrap at 180: take them around the first one */
			B.ang[i] = fmodf(B.ang[i] - a0 + 270, 180) - 90;
		ang = fmodf(a0 + median(B.ang, B.n) + 180, 180);
	}
	if (B.n >= 2) {
		/* velocity: median over sample pairs half the run apart, px per window */
		int h = B.n / 2, k = B.n - h;
		float dx, dy, c = cosf(ang * (float)M_PI / 180), s = sinf(ang * (float)M_PI / 180);

		for (int i = 0; i < k; i++) {
			float dt = (B.ct[i + h] - B.ct[i]) / B.win;

			B.cx[i] = (B.cx[i + h] - B.cx[i]) / dt;
			B.cy[i] = (B.cy[i + h] - B.cy[i]) / dt;
		}
		dx = median(B.cx, k);
		dy = median(B.cy, k);
		/* the hull spans the box swept over the window: take the sweep off each side */

		l -= fabsf(dx * c + dy * s);
		w -= fabsf(dy * c - dx * s);
		v = hypotf(dx, dy) * B.mm * 1e6f / B.win;
		if (l < w) {
			float t = l;

			l = w;
			w = t;
			ang = fmodf(ang + 90, 180);
		}
	}
	if (B.frames >= MIN_FRAMES) {
		clock_gettime(CLOCK_REALTIME, &ts);
		gmtime_r(&ts.tv_sec, &tm);
		strftime(when, sizeof(when), "%Y-%m-%dT%H:%M:%S", &tm);
		fprintf(B.out, "{\"t_us\":%lld,\"time\":\"%s.%06ldZ\",\"length_mm\":%.1f,\"width_mm\":%.1f,"
			"\"angle_deg\":%.1f,\"speed_mm_s\":%.0f,\"samples\":%d,\"frames\":%d,\"partial\":%s}\n",
			ts.tv_sec * 1000000LL + ts.tv_nsec / 1000, when, ts.tv_nsec / 1000, l * B.mm, w * B.mm,
			ang, v, B.n, B.frames, B.n ? "false" : "true");
		fflush(B.out);
		snprintf(B.label, sizeof(B.label), "L %.0f W %.0f mm", l * B.mm, w * B.mm);
		fprintf(stderr, "[dvsbox] L %.1f W %.1f mm, %.1f deg, %.0f mm/s, %d samples / %d frames%s\n",
			l * B.mm, w * B.mm, ang, v, B.n, B.frames, B.n ? "" : " (partial: never clear of the border)");
	}
	B.n = B.frames = B.miss = 0;
	B.pl = B.pw = 0;
}

/* one frame: events [from, to) are the last B.win us before end */
static void box_frame(uint64_t from, uint64_t to, int64_t end)
{
	int n, k, cut = 0;
	struct pt c = { 0, 0 };

	if (collect(from, to, &n) < MIN_CELLS || (k = hull(B.pts, n, B.hull)) < 3) {
		B.shown = 0;
		if (B.frames && ++B.miss >= END_FRAMES)
			box_end();
		return;
	}
	minrect(B.hull, k, &B.r);
	B.shown = 1;
	B.frames++;
	B.miss = 0;
	for (int i = 0; i < k; i++)
		cut |= B.hull[i].x < EDGE || B.hull[i].x > XS - EDGE || B.hull[i].y < EDGE || B.hull[i].y > YS - EDGE;
	if (cut || B.r.w < MIN_SIDE) {		/* cut by the view, or one edge with the other out of view */
		B.pl = fmaxf(B.pl, B.r.l);
		B.pw = fmaxf(B.pw, B.r.w);
		return;
	}
	if (B.n == MAX_SAMPLES)
		return;
	for (int i = 0; i < 4; i++) {
		c.x += B.r.c[i].x / 4;
		c.y += B.r.c[i].y / 4;
	}
	B.cx[B.n] = c.x;
	B.cy[B.n] = c.y;
	B.ct[B.n] = end;
	B.l[B.n] = B.r.l;
	B.w[B.n] = B.r.w;
	B.ang[B.n] = B.r.ang;
	B.n++;
}

/* 5 x 7 glyphs for the label, bit 4 = left column */
static const char glyphs[] = "0123456789LWm .-";
static const uint8_t font[][7] = {
	{ 0x0e, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0e }, { 0x04, 0x0c, 0x04, 0x04, 0x04, 0x04, 0x0e },
	{ 0x0e, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1f }, { 0x1f, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0e },
	{ 0x02, 0x06, 0x0a, 0x12, 0x1f, 0x02, 0x02 }, { 0x1f, 0x10, 0x1e, 0x01, 0x01, 0x11, 0x0e },
	{ 0x06, 0x08, 0x10, 0x1e, 0x11, 0x11, 0x0e }, { 0x1f, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08 },
	{ 0x0e, 0x11, 0x11, 0x0e, 0x11, 0x11, 0x0e }, { 0x0e, 0x11, 0x11, 0x0f, 0x01, 0x02, 0x0c },
	{ 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1f }, { 0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0a },
	{ 0x00, 0x00, 0x1a, 0x15, 0x15, 0x11, 0x11 }, { 0 },
	{ 0x00, 0x00, 0x00, 0x00, 0x00, 0x0c, 0x0c }, { 0x00, 0x00, 0x00, 0x1f, 0x00, 0x00, 0x00 },
};

static void dot(int x, int y, uint32_t c)
{
	if (x >= 0 && x < XS && y >= 0 && y < YS)
		S.back[y * XS + x] = c;
}

static void line(struct pt a, struct pt b, uint32_t c)
{
	int n = fmaxf(fabsf(b.x - a.x), fabsf(b.y - a.y)) + 1;

	for (int i = 0; i <= n; i++)
		dot(a.x + (b.x - a.x) * i / n, a.y + (b.y - a.y) * i / n, c);
}

static void text(int x, int y, const char *s, int scale, uint32_t c)
{
	for (; *s; s++, x += 6 * scale) {
		const char *g = strchr(glyphs, *s);

		if (!g)
			continue;
		for (int r = 0; r < 7 * scale; r++)
			for (int b = 0; b < 5 * scale; b++)
				if (font[g - glyphs][r / scale] >> (4 - b / scale) & 1)
					dot(x + b, y + r, c);
	}
}

/* back buffer -> the visible page, centred, at 1x or 1.5x (nearest neighbour; 1440x1080 fills the HDMI height) */
static void present(int zoom)
{
	static uint32_t row[XS * 3 / 2];
	int ow = zoom ? XS * 3 / 2 : XS, oh = zoom ? YS * 3 / 2 : YS;
	uint8_t *dst = S.fb + (size_t)((S.yres - oh) / 2) * S.stride + (S.xres - ow) / 2 * 4;

	if (zoom != S.zoom) {			/* the other size left its frame around this one */
		for (int x = 0; x < ow; x++)
			row[x] = BLACK;
		for (int y = 0; y < S.yres; y++)
			for (int x = 0; x < S.xres; x += ow)
				memcpy(S.fb + (size_t)y * S.stride + x * 4, row, (S.xres - x < ow ? S.xres - x : ow) * 4);
		S.zoom = zoom;
	}
	if (!zoom) {
		for (int y = 0; y < YS; y++)
			memcpy(dst + (size_t)y * S.stride, S.back + y * XS, XS * 4);
		return;
	}
	for (int sy = 0; sy < YS; sy++) {
		const uint32_t *src = S.back + sy * XS;

		for (int x = 0; x < ow; x++)
			row[x] = src[x * 2 / 3];
		for (int y = (sy * 3 + 1) / 2; y < ((sy + 1) * 3 + 1) / 2; y++)	/* rows ceil(1.5 sy) .. */
			memcpy(dst + (size_t)y * S.stride, row, ow * 4);
	}
}

/* The most common timestamp step since the last call becomes the scan period; render thread, every stats period. */
static int scan_update(void)
{
	int best = 1;

	for (int i = 2; i < HBINS; i++)		/* bin 0: steps under HBIN us and over the histogram */
		if (S.hist[i] > S.hist[best])
			best = i;
	if (S.hist[best] >= 16)
		atomic_store(&S.scan, best * HBIN + HBIN / 2);
	memset(S.hist, 0, sizeof(S.hist));
	return atomic_load(&S.scan);
}

static void *render(void *arg)
{
	uint64_t a = 0, b = 0, m = 0;		/* frame window [a, b), measurement window [m, b) */
	int64_t prev = INT64_MIN, frame_max = 0, frame_sum = 0, shown = 0, lag = 0, ema = 0, t_log = now_us();
	unsigned frames = 0, dips = 0;
	struct timespec next;

	clock_gettime(CLOCK_MONOTONIC, &next);
	for (;;) {
		int64_t period = 1000000 / PV(P_FPS), window = PV(P_WINDOW) ? PV(P_WINDOW) * 1000LL : period;
		int fade = PV(P_FADE), filter = PV(P_FILTER), color = PV(P_COLOR), flip = PV(P_FLIP);
		int zoom = PV(P_ZOOM) && S.xres >= XS * 3 / 2 && S.yres >= YS * 3 / 2;
		int64_t t0, end, anchor, newest;
		uint64_t wr;

		next.tv_nsec += period * 1000;
		while (next.tv_nsec >= 1000000000) {
			next.tv_nsec -= 1000000000;
			next.tv_sec++;
		}
		clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
		if (!atomic_load(&S.started))
			continue;

		t0 = now_us();
		end = t0 - atomic_load(&S.delay) - PV(P_MARGIN) * 1000LL;
		newest = atomic_load(&S.newest);
		if (end > newest)
			end = newest;			/* not past the data: a hole holds the last frame instead of a dark one */
		anchor = atomic_exchange(&S.anchor, -1);
		if (anchor >= 0)
			prev = anchor - window;		/* first buffer or after a stall: start from its data */
		if (end < prev)
			end = prev;			/* the delay grew: hold, never step back in sensor time */
		wr = atomic_load_explicit(&S.wr, memory_order_acquire);
		if (wr - a > RING)
			a = wr - RING;			/* the decoder lapped us: those events are gone */
		if (wr - m > RING)
			m = wr - RING;
		if (b < a)
			b = a;
		while (b < wr && BEFORE(S.ring[b % RING].t, (uint32_t)end))
			b++;
		while (a < b && BEFORE(S.ring[a % RING].t, (uint32_t)(end - window)))
			a++;
		while (m < b && BEFORE(S.ring[m % RING].t, (uint32_t)(end - B.win)))
			m++;
		if (B.out && end != prev)
			box_frame(m, b, end);		/* not while holding: the same events again */

		if (fade)
			for (int i = 0; i < XS * YS; i++) {
				uint32_t c = S.back[i];

				S.back[i] = BLACK | ((c & 0xff00ff) * fade >> 8 & 0xff00ff) | ((c & 0xff00) * fade >> 8 & 0xff00);
			}
		else
			for (int i = 0; i < XS * YS; i++)
				S.back[i] = BLACK;
		for (uint64_t i = a; i < b; i++) {
			const struct ev *e = &S.ring[i % RING];
			uint32_t c = colors[color][e->y >> 15];
			int x = e->x, y = e->y & EV_Y;

			if (!c || (filter && !(e->y & EV_SUP)))
				continue;
			if (flip & 1)
				x = XS - 1 - x;
			if (flip & 2)
				y = YS - 1 - y;
			S.back[y * XS + x] = c;
		}
		if (B.out && B.shown)
			for (int i = 0; i < 4; i++)
				line(B.r.c[i], B.r.c[(i + 1) % 4], BOX_RGB);
		if (B.label[0])
			text(12, 12, B.label, 4, BOX_RGB);
		present(zoom);
		prev = end;

		t0 = now_us() - t0;
		frame_sum += t0;
		if (t0 > frame_max)
			frame_max = t0;
		shown += b - a;
		/* a frame with under half the recent events (ema: 16 x their average) flashes dark with fade 0 */
		dips += ema > 200 * 16 && (int64_t)(b - a) * 32 < ema;
		ema += (int64_t)(b - a) - ema / 16;
		lag += newest - end;
		if (++frames >= 2 * 1000000 / period) {
			int64_t now = now_us(), dt = now - t_log;
			uint64_t nb = atomic_exchange(&S.buffers, 0), cov = atomic_exchange(&S.covered, 0);
			uint64_t us = atomic_exchange(&S.ingest_us, 0);
			uint64_t kb = nb ? atomic_exchange(&S.bytes, 0) / nb >> 10 : 0;
			double fps = frames * 1e6 / dt, draw = frame_sum / 1e3 / frames, events = (double)shown / frames;
			double bps = nb * 1e6 / dt, ingest = nb ? us / 1e3 / nb : 0, imax = atomic_exchange(&S.ingest_max, 0) / 1e3;
			double behind = lag / 1e3 / frames, cover = fmin(100, 100.0 * cov / dt);	/* of the real time */
			int next_stats = !atomic_load(&S.stats_cur), scan = scan_update();

			fprintf(stderr, "[dvsview] %.1f fps, draw %.1f ms (max %.1f), %.0f events/frame | "
				"%.1f buffers/s, ingest %.1f ms (max %.1f), %llu kB/buffer | %.0f ms behind the newest "
				"event | data for %.0f%% of the time | %u dim frames | scan %d us\n", fps, draw, frame_max / 1e3,
				events, bps, ingest, imax, (unsigned long long)kb, behind, cover, dips, scan);
			snprintf(S.stats[next_stats], sizeof(S.stats[0]), "{\"fps\":%.1f,\"draw_ms\":%.1f,\"events\":%.0f,"
				 "\"buffers\":%.1f,\"ingest_ms\":%.1f,\"kb\":%llu,\"behind_ms\":%.0f,\"coverage\":%.0f,"
				 "\"dips\":%u,\"scan_us\":%d,\"act_lost\":%llu}", fps, draw, events, bps, ingest,
				 (unsigned long long)kb, behind, cover, dips, scan,
				 (unsigned long long)atomic_load(&S.act_lost));
			atomic_store(&S.stats_cur, next_stats);
			frames = dips = 0;
			frame_sum = frame_max = shown = lag = 0;
			t_log = now;
		}
	}
	return arg;
}

static int alloc_ring(void)
{
	if (!S.ring)
		S.ring = malloc(sizeof(*S.ring) * RING);
	for (int i = 0; i < 2; i++)
		if (!S.baf[i])
			S.baf[i] = calloc(XS + 2, BROW);
	return S.ring && S.baf[0] && S.baf[1] ? 0 : -ENOMEM;
}

/* Measure boxes, one JSON line per box appended to path; call before dvs_start(). */
int dvs_boxes(const char *path, double mm_per_px, int window_us)
{
	B.out = fopen(path, "a");
	if (!B.out)
		return -errno;
	B.mm = mm_per_px > 0 ? mm_per_px : 1;
	B.win = window_us > 0 && window_us <= 100000 ? window_us : 5000;
	return 0;
}

/* Display setting name = value (the table P); -ENOENT, -ERANGE. */
int dvs_set(const char *name, int value)
{
	for (size_t i = 0; i < sizeof(P) / sizeof(*P); i++) {
		if (strcmp(name, P[i].name))
			continue;
		if (value < P[i].min || value > P[i].max)
			return -ERANGE;
		atomic_store(&P[i].v, value);
		return 0;
	}
	return -ENOENT;
}

/* All settings as JSON, {"name":{"v":..,"def":..,"min":..,"max":..},...}; not thread-safe (one caller). */
const char *dvs_params(void)
{
	static char buf[1024];
	size_t n = 0;

	for (size_t i = 0; i < sizeof(P) / sizeof(*P) && n < sizeof(buf); i++)
		n += snprintf(buf + n, sizeof(buf) - n, "%s\"%s\":{\"v\":%d,\"def\":%d,\"min\":%d,\"max\":%d}",
			      i ? "," : "{", P[i].name, PV(i), P[i].def, P[i].min, P[i].max);
	if (n < sizeof(buf) - 1)
		strcpy(buf + n, "}");
	return buf;
}

/* The renderer's last 2 s of stats as JSON ("{}" before the first). */
const char *dvs_stats(void)
{
	const char *s = S.stats[atomic_load(&S.stats_cur)];

	return *s ? s : "{}";
}

/*
 * The action classifier's reader (11.37): copy up to max events after *cursor into dst and move the cursor;
 * *cursor == UINT64_MAX starts at the newest event. A reader more than RING / 2 behind jumps to there (the
 * decoder may be overwriting the slots behind it) and the events it skipped go to act_lost.
 */
int dvs_events(struct ev *dst, int max, uint64_t *cursor)
{
	uint64_t wr = atomic_load_explicit(&S.wr, memory_order_acquire), c = *cursor, n, i;

	if (c == UINT64_MAX) {
		*cursor = wr;
		return 0;
	}
	if (wr - c > RING / 2) {
		atomic_fetch_add(&S.act_lost, wr - RING / 2 - c);
		c = wr - RING / 2;
	}
	n = wr - c < (uint64_t)max ? wr - c : (uint64_t)max;
	i = c % RING;
	if (i + n > RING) {
		memcpy(dst, S.ring + i, (RING - i) * sizeof(*dst));
		memcpy(dst + (RING - i), S.ring, (n - (RING - i)) * sizeof(*dst));
	} else {
		memcpy(dst, S.ring + i, n * sizeof(*dst));
	}
	*cursor = c + n;
	return n;
}

/* Add events e[0..n) into cells[2][120][160] (OFF after ON, 6 x 6 px each): the classifier's count frame,
 * dvsact.Binner.push_ev. numpy took 25-50 ms for the 150-300 k events of a busy 60 ms (11.38). */
void dvs_cells(const struct ev *e, int n, uint32_t *cells)
{
	for (int i = 0; i < n; i++) {
		uint32_t x = e[i].x, y = e[i].y & EV_Y;

		if (x < XS && y < YS)
			cells[(e[i].y >> 15) * (YS / 6 * XS / 6) + y / 6 * (XS / 6) + x / 6]++;
	}
}

/* A recording starts mid-stream: first a line that puts a fresh decoder where this one is (the last accepted
 * timestamp, the reference after it, the column), so the replay keeps the first events and their times. */
static void rec_state(void)
{
	uint32_t w[5], n = 0;

	if (S.have_ts) {
		w[n++] = 0x08000000 | (uint32_t)(S.raw / 1000 & 0x3fffff);
		w[n++] = 0x08800000 | (uint32_t)(S.raw % 1000);
		w[n++] = 0x08000000 | S.ref;
	}
	if (S.col >= 0)
		w[n++] = 0x04000000 | S.col;
	for (uint32_t i = 0; i < n; i++)
		w[i] = __builtin_bswap32(w[i]);
	w[n++] = 0;
	if (n > 1)
		rec_put(w, n * 4);
}

/* gzip the queue to the file until dvs_record(NULL) stops it and it is drained; on the CPUs of the thread that
 * started the recording (dvsctl's, CPU7), off the decoder's. */
static void *rec_writer(void *arg)
{
	prctl(PR_SET_NAME, "dvs-rec");
	for (;;) {
		uint64_t h = atomic_load_explicit(&S.rq_head, memory_order_acquire), t = atomic_load(&S.rq_tail);
		size_t i = t % REC_Q, n = h - t < REC_Q - i ? h - t : REC_Q - i;

		if (h == t) {
			if (atomic_load(&S.rec_stop) && atomic_load_explicit(&S.rq_head, memory_order_acquire) == t)
				break;
			nanosleep(&(struct timespec){ 0, 2000000 }, NULL);
			continue;
		}
		if (n > 1u << 20)
			n = 1u << 20;		/* 1 MB at a time: the decoder gets the room back sooner */
		if (!atomic_load(&S.rec_err) && gzwrite(S.rec, S.recq + i, n) != (int)n)
			atomic_store(&S.rec_err, 1);	/* drain on: the decoder must not find the queue full */
		atomic_store_explicit(&S.rq_tail, t + n, memory_order_release);
	}
	if (gzclose(S.rec) != Z_OK)
		atomic_store(&S.rec_err, 1);
	return arg;
}

/*
 * Record the packets to path (gzip) until dvs_record(NULL), which returns the events decoded meanwhile once the
 * writer has drained the queue (-EINVAL: not recording, -EIO: the queue overflowed or a write failed).
 * -EBUSY: already recording. Replay: t --replay (11.37).
 */
int dvs_record(const char *path)
{
	int r = 0;
	uint64_t n;

	pthread_mutex_lock(&S.rec_lock);
	if (path) {
		errno = 0;
		if (S.rec_busy)
			r = -EBUSY;
		else if (!S.recq && !(S.recq = malloc(REC_Q)))
			r = -ENOMEM;
		else if (!(S.rec = gzopen(path, "wb1")))
			r = errno ? -errno : -ENOMEM;
		else {
			atomic_store(&S.rq_head, 0);
			atomic_store(&S.rq_tail, 0);
			atomic_store(&S.rec_stop, 0);
			atomic_store(&S.rec_err, 0);
			S.rec_events = 0;
			rec_state();
			if ((r = -pthread_create(&S.rec_thr, NULL, rec_writer, NULL)))
				gzclose(S.rec);
			else
				S.rec_on = S.rec_busy = 1;
		}
		pthread_mutex_unlock(&S.rec_lock);
		return r;
	}
	if (!S.rec_on) {			/* not recording, or another call is closing it */
		pthread_mutex_unlock(&S.rec_lock);
		return -EINVAL;
	}
	S.rec_on = 0;
	n = S.rec_events;
	pthread_mutex_unlock(&S.rec_lock);
	atomic_store(&S.rec_stop, 1);		/* the decoder queues no more: drain, close */
	pthread_join(S.rec_thr, NULL);
	r = atomic_load(&S.rec_err) ? -EIO : (int)n;
	pthread_mutex_lock(&S.rec_lock);
	S.rec_busy = 0;
	pthread_mutex_unlock(&S.rec_lock);
	return r;
}

/* Length and type in front of the n data bytes at p + 8, CRC after them; returns the end of the chunk. */
static uint8_t *png_chunk(uint8_t *p, const char *type, uint32_t n)
{
	uint32_t crc;

	memcpy(p + 4, type, 4);
	crc = crc32(0, p + 4, n + 4);
	for (int i = 0; i < 4; i++) {
		p[i] = n >> (24 - 8 * i);
		p[8 + n + i] = crc >> (24 - 8 * i);
	}
	return p + 12 + n;
}

/* The frame being shown as an RGB PNG, XS x YS (before zoom), into dst; returns its length. For dvsctl's
 * /frame.png, which serialises the calls (one static buffer): ~25 ms here, numpy + Python zlib took ~45 (11.29). */
int dvs_png(uint8_t *dst, size_t len)
{
	static const uint8_t ihdr[13] = { 0, 0, XS >> 8, XS & 0xff, 0, 0, YS >> 8, YS & 0xff, 8, 2 };	/* RGB8 */
	static uint8_t raw[YS * (1 + XS * 3)];
	uint8_t *r = raw, *p;
	uLongf n;

	if (!S.back || len < 8 + 25 + 24 + 1024)
		return -EINVAL;
	for (int y = 0; y < YS; y++) {
		const uint32_t *row = S.back + y * XS;

		*r++ = 0;				/* filter: none */
		for (int x = 0; x < XS; x++, r += 3)
			r[0] = row[x] >> 16, r[1] = row[x] >> 8, r[2] = row[x];
	}
	memcpy(dst, "\x89PNG\r\n\x1a\n", 8);
	memcpy(dst + 16, ihdr, sizeof(ihdr));
	p = png_chunk(dst + 8, "IHDR", sizeof(ihdr));
	n = len - (p - dst) - 24;			/* room left for the IDAT data, then its CRC and IEND */
	if (compress2(p + 8, &n, raw, sizeof(raw), Z_BEST_SPEED) != Z_OK)
		return -ENOSPC;
	p = png_chunk(p, "IDAT", n);
	return png_chunk(p, "IEND", 0) - dst;
}

/* Map fbdev, start drawing fps frames/s on its visible page. */
int dvs_start(const char *fbdev, int fps)
{
	struct fb_var_screeninfo v;
	struct fb_fix_screeninfo f;
	pthread_t th;
	uint8_t *fb;
	int fd;

	if (dvs_set("fps", fps) || alloc_ring())
		return -EINVAL;
	fd = open(fbdev, O_RDWR);
	if (fd < 0)
		return -errno;
	if (ioctl(fd, FBIOGET_VSCREENINFO, &v) || ioctl(fd, FBIOGET_FSCREENINFO, &f) ||
	    v.bits_per_pixel != 32 || v.xres < XS || v.yres < YS)
		return -EINVAL;
	fb = mmap(NULL, (size_t)f.line_length * v.yres_virtual, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
	if (fb == MAP_FAILED)
		return -errno;
	S.stride = f.line_length;
	S.fb = fb + (size_t)v.yoffset * S.stride;
	S.xres = v.xres;
	S.yres = v.yres;
	S.back = malloc(XS * YS * 4);
	if (!S.back)
		return -ENOMEM;
	for (int i = 0; i < XS * YS; i++)
		S.back[i] = BLACK;
	return -pthread_create(&th, NULL, render, NULL);
}

#ifdef DVSVIEW_TEST
static int nev(void)
{
	return atomic_load(&S.wr);
}

static void reset(void)
{
	S.col = -1;
	S.have_ts = S.bad = 0;
	S.raw = S.t = 0;
	S.ref = 0;
	atomic_store(&S.wr, 0);
	atomic_store(&S.scan, SCAN);
	memset(S.hist, 0, sizeof(S.hist));
}

static void feed(const uint32_t *w, size_t n)
{
	uint32_t be[16];

	for (size_t i = 0; i < n; i++)
		be[i] = __builtin_bswap32(w[i]);
	decode(be, n);
}

static void ev_add(uint32_t t, float x, float y)
{
	uint64_t w = atomic_load(&S.wr);

	if (x < 0 || x >= XS || y < 0 || y >= YS)
		return;
	S.ring[w % RING] = (struct ev){ t, x, y };
	atomic_store(&S.wr, w + 1);
}

/* --replay in.gz out.bin: a dvs_record() file, one zero-ended line at a time, back through decode() with the
 * filter off; the events go to out.bin as struct ev (tools/host/act/readers.py reads it). */
static int replay(const char *in, const char *out)
{
	static uint32_t line[LINE / 4];
	gzFile g = gzopen(in, "rb");
	FILE *fp = g ? fopen(out, "wb") : NULL;
	uint64_t total = 0, w0;
	size_t n = 0;
	uint32_t w;
	int r;

	if (!g || !fp)
		return perror(g ? out : in), 1;
	dvs_set("filter_ms", 0);
	while ((r = gzread(g, &w, 4)) == 4 || n) {
		if (r == 4 && w) {
			line[n++] = w;
			if (n < LINE / 4)
				continue;
		}
		w0 = nev();			/* a zero word, the end of the file, or a full line */
		decode(line, n);
		for (uint64_t i = w0; i < (uint64_t)nev(); i++)
			fwrite(&S.ring[i % RING], sizeof(struct ev), 1, fp);
		total += nev() - w0;
		n = 0;
	}
	gzclose(g);
	if (fclose(fp))
		return perror(out), 1;
	printf("replay: %llu events\n", (unsigned long long)total);
	return 0;
}

/*
 * A len x wid box at angle deg (of its length) moving +x at v px/s through the view, 30 fps, scans every
 * 500 us; edges == 2: only the two edges across the motion fire (an axis-aligned box), else all four.
 * Returns 0 if the reported box is within 6 px and 2 degrees.
 */
static int synth(float len, float wid, float deg, float v, int edges)
{
	char line_[256] = "";
	float l, w, ang, a = deg * (float)M_PI / 180, ca = cosf(a), sa = sinf(a);
	int64_t period = 33333;

	B.out = tmpfile();
	B.mm = 1;
	B.win = 5000;
	B.label[0] = 0;
	reset();
	srand(1);
	for (int64_t end = period; end < 4000000; end += period) {
		uint64_t from = atomic_load(&S.wr);

		for (int64_t t = end - B.win; t < end; t += 500) {
			float cx = -len + v * t / 1e6f, cy = YS / 2;

			for (int e = 0; e < 4; e++) {
				/* edge e of the box: 0, 2 across the length direction (the leading / trailing edges) */
				float s = e < 2 ? 1 : -1, hl = len / 2, hw = wid / 2, n = e % 2 ? len : wid;

				if (edges == 2 && e % 2)
					continue;
				for (float u = -n / 2; u < n / 2; u += 1) {
					float px = e % 2 ? u : s * hl, py = e % 2 ? s * hw : u;

					ev_add(t, cx + px * ca - py * sa, cy + px * sa + py * ca);
				}
			}
			for (int i = 0; i < 657; i++)	/* background, 1.9 Hz per pixel as measured (11.28) */
				ev_add(t, rand() % XS, rand() % YS);
		}
		box_frame(from, atomic_load(&S.wr), end);
	}
	rewind(B.out);
	if (!fgets(line_, sizeof(line_), B.out) ||
	    sscanf(line_, "{\"t_us\":%*d,\"time\":\"%*[^\"]\",\"length_mm\":%f,\"width_mm\":%f,\"angle_deg\":%f",
		   &l, &w, &ang) != 3)
		return fprintf(stderr, "no box record: %s\n", line_), 1;
	fprintf(stderr, "synth %.0fx%.0f at %.0f deg, %d edges: %s", len, wid, deg, edges, line_);
	if (fabsf(l - len) > 6 || fabsf(w - wid) > 6 || fabsf(fmodf(ang - deg + 270, 180) - 90) > 2)
		return 1;
	fclose(B.out);
	B.out = NULL;
	return 0;
}

int main(int argc, char **argv)
{
	/* column 5; SGROUP addr 2: ON bit 0, OFF bit 7; MGROUP offset 1 addr 3: group1 ON bit 1, group2 OFF bit 2 */
	static const uint32_t groups[] = { 0x04000005, 0x80080001 | 0x8000, 0x84000000 | 3 << 18 | 1 << 17 | 0x0402 };
	static const struct { uint16_t x, y; } want[] = { { 5, 16 }, { 5, 23 | 1 << 15 }, { 5, 25 }, { 5, 34 | 1 << 15 } };
	/* ref 3 ms + sub 717 us, then ref 4 + sub 17 (+300 us), a sub that goes back (rejected), sub 511 (+494) */
	static const uint32_t ts[] = { 0x08000003, 0x088002cd, 0x08000004, 0x08800011, 0x08800005, 0x088001ff };

	if (alloc_ring())
		return 1;
	if (argc == 4 && !strcmp(argv[1], "--replay"))
		return replay(argv[2], argv[3]);
	feed(groups, 3);
	if (nev() != 4)
		return fprintf(stderr, "events %d, want 4\n", nev()), 1;
	for (int i = 0; i < 4; i++)
		if (S.ring[i].x != want[i].x || S.ring[i].y != want[i].y)
			return fprintf(stderr, "event %d: %u,%#x\n", i, S.ring[i].x, S.ring[i].y), 1;
	reset();
	feed(ts, 4);
	if (S.t != 300)
		return fprintf(stderr, "t %lld after +300 us\n", (long long)S.t), 1;
	feed(ts + 4, 2);
	if (S.t != 794)
		return fprintf(stderr, "t %lld after a bad and a +494 us timestamp\n", (long long)S.t), 1;
	feed((const uint32_t[]){ 0x08000105, 0x08800005 }, 2);	/* ref 5 with bit 8 set + sub 5: +494 us */
	if (S.t != 1288)
		return fprintf(stderr, "t %lld after a reference with a flipped bit\n", (long long)S.t), 1;
	/* ref 200: events kept; a chunk from 133 ms back (ref 67): its event dropped; back at 200 ms +494 us: kept */
	reset();
	feed((const uint32_t[]){ 0x080000c8, 0x08800005, 0x088001f3, 0x04000005, 0x80080001, 0x08000043, 0x08800005,
				 0x80080001, 0x080000c8, 0x088003e1, 0x80080001 }, 11);
	if (nev() != 2 || S.t != 988)
		return fprintf(stderr, "stale chunk: %d events, t %lld\n", nev(), (long long)S.t), 1;
	feed((const uint32_t[]){ 0x080000d8, 0x08800005 }, 2);	/* ref 216: a 15 ms gap, one scan on the display */
	for (int i = 0; i < 64 && S.t == 1482; i++)		/* 64 packets 133 ms back: a counter reset */
		feed((const uint32_t[]){ 0x08000053, 0x08800000 | i * 7 }, 2);
	feed((const uint32_t[]){ 0x08000053, 0x088003a7 }, 2);	/* ref 83 + 935 us: 494 us after the last */
	if (S.t != 1976)
		return fprintf(stderr, "gap or counter reset: t %lld\n", (long long)S.t), 1;
	feed((const uint32_t[]){ 0x08000054, 0x008001ad }, 2);	/* ref 84 + sub 429 us with its type bits cleared */
	if (S.t != 2470)
		return fprintf(stderr, "sub without its type bits: t %lld\n", (long long)S.t), 1;
	/* 20 scans of 1 ms, then 2 ms: a gap (one 494 us scan) at the 2000 fps period, which then follows the steps */
	for (int i = 1; i <= 21; i++)
		feed((const uint32_t[]){ 0x08000054 + i + (i == 21), 0x088001ad }, 2);
	if (scan_update() != 1005 || S.t != 22964)
		return fprintf(stderr, "scan period %d us, t %lld\n", atomic_load(&S.scan), (long long)S.t), 1;
	feed((const uint32_t[]){ 0x0800006f, 0x088001ad }, 2);	/* ref 111: a 5 ms gap, one 1 ms scan on the display */
	if (S.t != 23969)
		return fprintf(stderr, "gap at a 1 ms scan: t %lld\n", (long long)S.t), 1;
	{					/* a black frame with one ON pixel at (1, 2) as a PNG, inflated back */
		static uint8_t png[XS * YS * 3 + YS + 65536], raw[YS * (1 + XS * 3)];
		int n, lit = 0;
		uint32_t zn;
		uLongf rn = sizeof(raw);

		S.back = calloc(XS * YS, 4);
		S.back[2 * XS + 1] = ON_RGB;
		n = dvs_png(png, sizeof(png));
		zn = png[33] << 24 | png[34] << 16 | png[35] << 8 | png[36];
		if (n < 0 || memcmp(png, "\x89PNG\r\n\x1a\n", 8) || memcmp(png + 37, "IDAT", 4) || n != 57 + (int)zn ||
		    memcmp(png + n - 8, "IEND", 4) || uncompress(raw, &rn, png + 41, zn) != Z_OK || rn != sizeof(raw))
			return fprintf(stderr, "png: %d B, IDAT %u B\n", n, zn), 1;
		for (size_t i = 0; i < sizeof(raw); i++)
			lit += raw[i] != 0;
		if (lit != 1 || raw[2 * (1 + XS * 3) + 1 + 3 + 1] != 0xff)
			return fprintf(stderr, "png: %d bytes lit\n", lit), 1;
		free(S.back);
		S.back = NULL;
	}
	if (dvs_set("fade", 128) || PV(P_FADE) != 128 || dvs_set("fade", 999) != -ERANGE || dvs_set("nope", 1) != -ENOENT ||
	    !strstr(dvs_params(), "\"fade\":{\"v\":128,\"def\":0,\"min\":0,\"max\":250}") || dvs_set("fade", 0))
		return fprintf(stderr, "settings: %s\n", dvs_params()), 1;
	/* filter: column 5, ON at rows 16 and 17 (the second has a neighbour), then column 300 row 400 alone */
	dvs_set("filter_ms", 5);
	reset();
	feed((const uint32_t[]){ 0x04000005, 0x80080003, 0x0400012c, 0x80000000 | 50 << 18 | 1 }, 4);
	dvs_set("filter_ms", 0);
	if (nev() != 3 || S.ring[0].y & EV_SUP || !(S.ring[1].y & EV_SUP) || S.ring[2].y & EV_SUP)
		return fprintf(stderr, "filter: %d events, flags %#x %#x %#x\n", nev(), S.ring[0].y, S.ring[1].y,
			       S.ring[2].y), 1;
	if (synth(300, 150, 0, 600, 2) || synth(250, 120, 30, 600, 4) || synth(200, 160, 15, 900, 4))
		return fprintf(stderr, "box measurement off\n"), 1;
	{					/* the action classifier's reader: from the newest event, then what came after */
		struct ev dst[16];
		uint64_t c = UINT64_MAX;

		reset();
		if (dvs_events(dst, 16, &c) || c != (uint64_t)nev())
			return fprintf(stderr, "dvs_events start: cursor %llu\n", (unsigned long long)c), 1;
		feed(groups, 3);
		if (dvs_events(dst, 16, &c) != 4)
			return fprintf(stderr, "dvs_events: not the 4 new events\n"), 1;
		for (int i = 0; i < 4; i++)
			if (dst[i].x != want[i].x || dst[i].y != want[i].y)
				return fprintf(stderr, "dvs_events %d: %u,%#x\n", i, dst[i].x, dst[i].y), 1;
	}
	{					/* a recording of two lines (timestamps to 300 us, then groups) replays the same */
		static uint32_t lines[2 * LINE / 4];
		const char *gz = "/tmp/dvsview-rec.gz", *bin = "/tmp/dvsview-rec.bin";
		struct ev rec[8], back[8];
		FILE *fp;
		int n, w0;

		reset();
		w0 = nev();
		for (int i = 0; i < 4; i++)
			lines[i] = __builtin_bswap32(ts[i]);
		for (int i = 0; i < 3; i++)
			lines[LINE / 4 + i] = __builtin_bswap32(groups[i]);
		if (dvs_record(gz) || dvs_record(gz) != -EBUSY)
			return fprintf(stderr, "dvs_record: start, or a second start not busy\n"), 1;
		decode_lines((uint8_t *)lines, sizeof(lines));
		n = dvs_record(NULL);
		if (n != 4 || nev() - w0 != 4 || dvs_record(NULL) != -EINVAL)
			return fprintf(stderr, "dvs_record: %d events, %d decoded\n", n, nev() - w0), 1;
		memcpy(rec, S.ring + w0, n * sizeof(*rec));
		reset();
		if (replay(gz, bin) || !(fp = fopen(bin, "rb")))
			return fprintf(stderr, "replay failed\n"), 1;
		n = fread(back, sizeof(*back), 8, fp);
		fclose(fp);
		if (n != 4 || rec[0].t != 300 || memcmp(rec, back, sizeof(*rec) * 4))
			return fprintf(stderr, "replay: %d events, t %u (want 4, 300)\n", n, n ? back[0].t : 0), 1;
	}
	{					/* started mid-stream: a column and a clock from before the recording */
		static uint32_t lines[LINE / 4];
		static const uint32_t body[] = { 0x80080001 | 0x8000, 0x84000000 | 3 << 18 | 1 << 17 | 0x0402,
						 0x088001ff, 0x80080001 | 0x8000 };	/* 4 events, +494 us, 2 */
		const char *gz = "/tmp/dvsview-rec2.gz", *bin = "/tmp/dvsview-rec2.bin";
		struct ev rec[8], back[8];
		FILE *fp;
		int n, w0;

		reset();
		feed(ts, 4);			/* t 300 */
		feed((const uint32_t[]){ 0x04000005 }, 1);
		w0 = nev();
		for (int i = 0; i < 4; i++)
			lines[i] = __builtin_bswap32(body[i]);
		dvs_record(gz);
		decode_lines((uint8_t *)lines, sizeof(lines));
		n = dvs_record(NULL);
		memcpy(rec, S.ring + w0, n * sizeof(*rec));
		reset();
		if (n != 6 || replay(gz, bin) || !(fp = fopen(bin, "rb")))
			return fprintf(stderr, "mid-stream recording: %d events\n", n), 1;
		n = fread(back, sizeof(*back), 8, fp);
		fclose(fp);
		for (int i = 0; i < 6; i++)
			if (n != 6 || back[i].t != rec[i].t - rec[0].t || back[i].x != rec[i].x || back[i].y != rec[i].y)
				return fprintf(stderr, "mid-stream replay: %d events, event %d t %u x %u (live %u, %u)\n", n,
					       i, back[i].t, back[i].x, rec[i].t - rec[0].t, rec[i].x), 1;
	}
	{					/* recording must not slow the decoder: gzip in the ingest thread took 6 -> 40 ms a
						 * buffer at 1.7 M events/s and lost buffers (11.39) */
		static uint32_t big[127 * LINE / 4];
		int64_t us[2] = { 0, 0 };
		uint32_t r = 1;

		for (int on = 0; on < 2; on++) {
			reset();
			if (on && dvs_record("/tmp/dvsview-rec3.gz"))
				return fprintf(stderr, "dvs_record busy\n"), 1;
			for (int b = 0; b < 16; b++) {
				int64_t t0;

				for (int l = 0; l < 127; l++) {	/* a column, then groups to the end of the line */
					uint32_t *p = big + l * LINE / 4;

					p[0] = __builtin_bswap32(0x04000000 | (r >> 8) % XS);
					for (int i = 1; i < LINE / 4; i++) {
						r = r * 1103515245 + 12345;
						p[i] = __builtin_bswap32(0x80000000 | (r >> 9) % 90 << 18 | (r & 0xffff));
					}
				}
				t0 = now_us();
				decode_lines((uint8_t *)big, sizeof(big));
				us[on] += now_us() - t0;
			}
			if (on && dvs_record(NULL) <= 0)
				return fprintf(stderr, "dvs_record of 16 buffers failed\n"), 1;
		}
		if (us[1] > 2 * us[0])
			return fprintf(stderr, "decode while recording %.1f ms a buffer, %.1f without\n", us[1] / 16e3,
				       us[0] / 16e3), 1;
	}
	{					/* a reader RING/2 + 100 behind skips the 100 that are about to be overwritten */
		struct ev dst[16];
		uint64_t c = nev(), lost = atomic_load(&S.act_lost);

		for (uint32_t i = 0; i < RING / 2 + 100; i++)
			ev_add(i, i % XS, i / XS % YS);
		if (dvs_events(dst, 16, &c) != 16 || atomic_load(&S.act_lost) - lost != 100 ||
		    c != (uint64_t)nev() - RING / 2 + 16)
			return fprintf(stderr, "lapped reader: lost %llu, cursor %llu\n",
				       (unsigned long long)(atomic_load(&S.act_lost) - lost), (unsigned long long)c), 1;
	}
	puts("selftest ok");

	/* capture files, consecutive VC0 buffers: decode speed, timestamp coverage, the box filter on that scene */
	reset();
	B.warm = 0;				/* learn this scene's background, not the synthetic one */
	for (int f = 1; f < argc; f++) {
		FILE *fp = fopen(argv[f], "rb");
		static uint8_t buf[8192 * 127];
		size_t len = fp ? fread(buf, 1, sizeof(buf), fp) : 0;
		uint64_t w0 = nev();
		int64_t t0;
		size_t n;

		if (!len)
			return perror(argv[f]), 1;
		fclose(fp);
		t0 = now_us();
		n = decode_lines(buf, len);
		printf("%s: %zu B, %zu B of packets, %llu events, decode %.2f ms\n", argv[f], len, n,
		       (unsigned long long)(nev() - w0), (now_us() - t0) / 1e3);
	}
	if (argc > 1) {
		int windows = 0, boxes = 0, most = 0;

		printf("sensor time %.1f ms, %.1f%% of it with scans back to back\n", S.tot / 1e3,
		       S.tot ? 100.0 * S.cov / S.tot : 0.0);
		for (uint64_t i = 0, j = 0; j < (uint64_t)nev(); i = j) {
			int n, c;

			while (j < (uint64_t)nev() && S.ring[j].t - S.ring[i].t < 5000)
				j++;
			c = collect(i, j, &n);
			windows++;
			boxes += c >= MIN_CELLS;
			most = c > most ? c : most;
		}
		printf("box filter, 5 ms windows: %d, with >= %d cells passing (a box): %d, most cells %d\n", windows,
		       MIN_CELLS, boxes, most);
	}
	for (int ms = 2; argc > 1 && ms <= 8; ms *= 2) {	/* the display filter on the same data */
		uint64_t kept = 0;
		int64_t t0, t;

		dvs_set("filter_ms", ms);
		reset();
		S.slot_us = 0;			/* clear the bitmaps */
		t = 0;
		for (int f = 1; f < argc; f++) {
			FILE *fp = fopen(argv[f], "rb");
			static uint8_t buf[8192 * 127];
			size_t len = fread(buf, 1, sizeof(buf), fp);

			fclose(fp);
			t0 = now_us();
			decode_lines(buf, len);
			t += now_us() - t0;
		}
		for (uint64_t i = 0; i < (uint64_t)nev(); i++)
			kept += !!(S.ring[i].y & EV_SUP);
		printf("filter_ms %d: %.1f%% of %d events kept, decode %.2f ms per buffer\n", ms, 100.0 * kept / nev(),
		       nev(), t / 1e3 / (argc - 1));
		dvs_set("filter_ms", 0);
	}
	return 0;
}
#endif
