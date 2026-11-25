// SPDX-License-Identifier: GPL-2.0
/*
 * dvsgl: the DVS view drawn on the GPU (linux510 docs 11.102).
 *
 * libdvsview (dvs_share()) hands over its event ring and, every frame, the frame's window through a memfd. The CPU
 * renderer filtered (CPU3) and drew (CPU2) every event: with fast motion, ~1M events a frame, the filter fell
 * behind, the view held frames waiting for it, and the drawn page went to X with XPutImage (~20 ms a frame) while
 * the next one was being drawn into it. Here GLES 3.1 compute on the Mali-G52 (Mesa panfrost on the kbase shim)
 * filters and draws the frame's events, and a frame is taken only once the render thread published all of it.
 *
 *   dvsgl FD           FD: the memfd; ov5647-cap.py --x11 starts it with the GPU Mesa environment (as kenv)
 *   dvsgl --selftest   synthetic frames through the same passes, read back (needs DISPLAY, makes no window)
 *
 * Passes per frame:
 *   1 latest: per event of [l, b), atomicMax(L[pixel], (time - t0 + 1) << 1 | OFF) in a storage buffer (this
 *             Mesa has no image atomics): each pixel's latest event and its polarity. t0 = the frame's start less
 *             filter_ms: [l, a) only lend neighbours.
 *   2 shade:  per pixel: drawn if its latest event is in the frame and (filter on) a neighbour's latest event is
 *             within filter_ms of it (the CPU filter looks back only, in slots: close, not the same), in the colour
 *             of its polarity; else the last frame x fade/256 (black at fade 0). Clears the other L.
 *   3 show:   the frame and the overlay (boxes, labels, dvsface.py's mark), letterboxed into the window.
 * Keys: q, Esc or closing the window: exit 0 (the capture ends as on Ctrl+C); f or F11: full screen. Exit 1 (no
 * GPU, a bad fd): ov5647-cap.py (dvsx11.py) shows the frames with XPutImage as before.
 *
 * Build (Debian 12 arm64, features/host/bundle.sh): gcc -O2 -Wall -o dvsgl dvsgl.c -lEGL -lGLESv2 -lX11
 */
#define _GNU_SOURCE
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl31.h>
#include <X11/Xlib.h>
#include <X11/Xutil.h>
#include <X11/keysym.h>
#include <errno.h>
#include <linux/futex.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

/* as in dvsview.c (checked: magic, size, xs, ys) */
#define XS 960
#define YS 720
#define SH_MAGIC 0x31475644u
#define CAP (1u << 21)			/* events uploaded a frame at most (16 MB): the newest ones */

struct ev {
	uint32_t t;
	uint16_t x, y;
};

struct dvs_shared {
	uint32_t magic, size, ring, xs, ys, ring_off;
	_Atomic uint32_t seq, attached, seen;
	uint32_t pad;
	_Atomic uint64_t wr, cpu_frames;
	uint64_t l, a, b;
	uint32_t end, window, baf;
	uint32_t fade, flip, pal[2];
	uint32_t ov_seq, ov_lo, ov_hi;
	uint32_t overlay[XS * YS];
};

struct frame {				/* one published frame, copied out under the sequence lock */
	uint64_t l, a, b;
	uint32_t end, window, baf, fade, flip, pal[2], ov_seq, ov_lo, ov_hi;
};

static const char *latest_cs =
	"#version 310 es\n"
	"layout(local_size_x = 256) in;\n"
	"layout(std430, binding = 0) readonly buffer Ev { uvec2 ev[]; };\n"
	"layout(std430, binding = 1) buffer Lat { uint L[]; };\n"
	"uniform uint n, t0, span, flip;\n"
	"void main() {\n"
	"  uint i = gl_GlobalInvocationID.x;\n"
	"  if (i >= n) return;\n"
	"  uvec2 e = ev[i];\n"			/* t; x | (y | EV_SUP | EV_OFF) << 16 */
	"  uint x = e.y & 0xffffu, y = (e.y >> 16) & 0x3ffu, dt = e.x - t0;\n"
	"  if (x >= 960u || y >= 720u || dt >= span) return;\n"
	"  if ((flip & 1u) != 0u) x = 959u - x;\n"
	"  if ((flip & 2u) != 0u) y = 719u - y;\n"
	"  atomicMax(L[y * 960u + x], (dt + 1u) << 1 | e.y >> 31);\n"
	"}\n";

static const char *shade_cs =
	"#version 310 es\n"
	"layout(local_size_x = 16, local_size_y = 16) in;\n"
	"layout(std430, binding = 1) readonly buffer Lat { uint L[]; };\n"
	"layout(std430, binding = 2) writeonly buffer Lnx { uint Lnext[]; };\n"
	"layout(rgba8, binding = 2) uniform readonly highp image2D prev;\n"
	"layout(rgba8, binding = 3) uniform writeonly highp image2D outp;\n"
	"uniform uint lookback, baf;\n"
	"uniform vec4 pal0, pal1;\n"
	"uniform float fade;\n"
	"void main() {\n"
	"  ivec2 p = ivec2(gl_GlobalInvocationID.xy);\n"
	"  if (p.x >= 960 || p.y >= 720) return;\n"
	"  uint v = L[p.y * 960 + p.x], t = v >> 1;\n"	/* time since t0 + 1, 0: none */
	"  Lnext[p.y * 960 + p.x] = 0u;\n"
	"  bool on = t > lookback;\n"
	"  if (on && baf != 0u) {\n"
	"    bool s = false;\n"
	"    for (int dy = -1; dy <= 1; dy++)\n"
	"      for (int dx = -1; dx <= 1; dx++) {\n"
	"        ivec2 q = p + ivec2(dx, dy);\n"
	"        if ((dx != 0 || dy != 0) && q.x >= 0 && q.y >= 0 && q.x < 960 && q.y < 720) {\n"
	"          uint u = L[q.y * 960 + q.x] >> 1;\n"
	"          s = s || (u != 0u && (u > t ? u - t : t - u) <= baf);\n"
	"        }\n"
	"      }\n"
	"    on = s;\n"
	"  }\n"
	"  vec4 c = (v & 1u) != 0u ? pal1 : pal0;\n"
	"  if (on && c.a > 0.0) imageStore(outp, p, vec4(c.rgb, 1.0));\n"
	"  else imageStore(outp, p, vec4(fade > 0.0 ? imageLoad(prev, p).rgb * fade : vec3(0.0), 1.0));\n"
	"}\n";

static const char *show_vs =
	"#version 300 es\n"
	"out vec2 uv;\n"
	"void main() {\n"
	"  vec2 p = vec2(float(gl_VertexID & 1), float(gl_VertexID >> 1));\n"
	"  uv = vec2(p.x, 1.0 - p.y);\n"		/* image row 0 (sensor row 0) at the top */
	"  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);\n"
	"}\n";

static const char *show_fs =
	"#version 300 es\n"
	"precision mediump float;\n"
	"uniform sampler2D img, ov;\n"
	"in vec2 uv;\n"
	"out vec4 o;\n"
	"void main() {\n"
	"  vec4 m = texture(ov, uv).bgra;\n"	/* XRGB8888 with alpha, little endian: B G R A */
	"  o = vec4(mix(texture(img, uv).rgb, m.rgb, m.a), 1.0);\n"
	"}\n";

static struct {
	GLuint latest, shade, show, ssbo, L[2], acc[2], ov, fbo;
	GLint u_n, u_t0, u_span, u_flip, u_lookback, u_baf, u_pal0, u_pal1, u_fade;
	int k, j;			/* L[k] takes this frame's events; acc[j] holds the last frame */
	unsigned frames;
} G;

static double now(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

static GLuint shader(GLenum type, const char *src)
{
	GLuint s = glCreateShader(type);
	GLint ok;
	char log[2048];

	glShaderSource(s, 1, &src, NULL);
	glCompileShader(s);
	glGetShaderiv(s, GL_COMPILE_STATUS, &ok);
	if (!ok) {
		glGetShaderInfoLog(s, sizeof(log), NULL, log);
		fprintf(stderr, "dvsgl: shader: %s\n", log);
		exit(1);
	}
	return s;
}

static GLuint program(const char *cs, const char *vs, const char *fs)
{
	GLuint p = glCreateProgram();
	GLint ok;
	char log[2048];

	if (cs)
		glAttachShader(p, shader(GL_COMPUTE_SHADER, cs));
	if (vs) {
		glAttachShader(p, shader(GL_VERTEX_SHADER, vs));
		glAttachShader(p, shader(GL_FRAGMENT_SHADER, fs));
	}
	glLinkProgram(p);
	glGetProgramiv(p, GL_LINK_STATUS, &ok);
	if (!ok) {
		glGetProgramInfoLog(p, sizeof(log), NULL, log);
		fprintf(stderr, "dvsgl: link: %s\n", log);
		exit(1);
	}
	return p;
}

static GLuint tex(GLenum fmt, GLenum ext, GLenum type)
{
	static uint32_t zero[XS * YS];
	GLuint t;

	glGenTextures(1, &t);
	glBindTexture(GL_TEXTURE_2D, t);
	glTexStorage2D(GL_TEXTURE_2D, 1, fmt, XS, YS);
	glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, XS, YS, ext, type, zero);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
	glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
	return t;
}

static void gl_init(void)
{
	G.latest = program(latest_cs, NULL, NULL);
	G.shade = program(shade_cs, NULL, NULL);
	G.show = program(NULL, show_vs, show_fs);
	G.u_n = glGetUniformLocation(G.latest, "n");
	G.u_t0 = glGetUniformLocation(G.latest, "t0");
	G.u_span = glGetUniformLocation(G.latest, "span");
	G.u_flip = glGetUniformLocation(G.latest, "flip");
	G.u_lookback = glGetUniformLocation(G.shade, "lookback");
	G.u_baf = glGetUniformLocation(G.shade, "baf");
	G.u_pal0 = glGetUniformLocation(G.shade, "pal0");
	G.u_pal1 = glGetUniformLocation(G.shade, "pal1");
	G.u_fade = glGetUniformLocation(G.shade, "fade");
	glUseProgram(G.show);
	glUniform1i(glGetUniformLocation(G.show, "img"), 0);
	glUniform1i(glGetUniformLocation(G.show, "ov"), 1);
	uint32_t *zero = calloc(XS * YS, 4);	/* GLES has no glClearBufferData */

	glGenBuffers(2, G.L);
	for (int i = 0; i < 2; i++) {
		glBindBuffer(GL_SHADER_STORAGE_BUFFER, G.L[i]);
		glBufferData(GL_SHADER_STORAGE_BUFFER, XS * YS * 4, zero, GL_DYNAMIC_COPY);
		G.acc[i] = tex(GL_RGBA8, GL_RGBA, GL_UNSIGNED_BYTE);
	}
	free(zero);
	G.ov = tex(GL_RGBA8, GL_RGBA, GL_UNSIGNED_BYTE);
	glGenBuffers(1, &G.ssbo);
	glBindBuffer(GL_SHADER_STORAGE_BUFFER, G.ssbo);
	glBufferData(GL_SHADER_STORAGE_BUFFER, (GLsizeiptr)CAP * sizeof(struct ev), NULL, GL_STREAM_DRAW);
	glGenFramebuffers(1, &G.fbo);
}

static void color(GLint u, uint32_t c)		/* 0xAARRGGBB */
{
	glUniform4f(u, (c >> 16 & 255) / 255.f, (c >> 8 & 255) / 255.f, (c & 255) / 255.f, (c >> 24) / 255.f);
}

/* Copy events [l, b) of the ring into the storage buffer; returns how many (the newest CAP at most). */
static uint32_t upload(const struct ev *ring, uint32_t size, uint64_t *l, uint64_t b)
{
	uint64_t n = b - *l;
	uint32_t i, k;
	struct ev *dst;

	if (n > CAP)
		*l = b - (n = CAP);
	if (!n)
		return 0;
	glBindBuffer(GL_SHADER_STORAGE_BUFFER, G.ssbo);
	dst = glMapBufferRange(GL_SHADER_STORAGE_BUFFER, 0, n * sizeof(*dst), GL_MAP_WRITE_BIT |
			       GL_MAP_INVALIDATE_BUFFER_BIT);
	if (!dst)
		return 0;
	i = *l % size;
	k = size - i < n ? size - i : n;	/* up to the ring's end, then from its start */
	memcpy(dst, ring + i, k * sizeof(*dst));
	memcpy(dst + k, ring, (n - k) * sizeof(*dst));
	glUnmapBuffer(GL_SHADER_STORAGE_BUFFER);
	return n;
}

/* Passes 1 and 2 for a frame whose n events are in the storage buffer: the result in acc[G.j] after. */
static void draw(const struct frame *f, uint32_t n)
{
	uint32_t t0 = f->end - f->window - f->baf;

	if (n) {
		glUseProgram(G.latest);
		glUniform1ui(G.u_n, n);
		glUniform1ui(G.u_t0, t0);
		glUniform1ui(G.u_span, f->window + f->baf);
		glUniform1ui(G.u_flip, f->flip);
		glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 0, G.ssbo);
		glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 1, G.L[G.k]);
		glDispatchCompute((n + 255) / 256, 1, 1);
		glMemoryBarrier(GL_SHADER_STORAGE_BARRIER_BIT);
	}
	glUseProgram(G.shade);
	glUniform1ui(G.u_lookback, f->baf);
	glUniform1ui(G.u_baf, f->baf);
	color(G.u_pal0, f->pal[0]);
	color(G.u_pal1, f->pal[1]);
	glUniform1f(G.u_fade, f->fade / 256.f);
	glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 1, G.L[G.k]);
	glBindBufferBase(GL_SHADER_STORAGE_BUFFER, 2, G.L[!G.k]);
	glBindImageTexture(2, G.acc[G.j], 0, GL_FALSE, 0, GL_READ_ONLY, GL_RGBA8);
	glBindImageTexture(3, G.acc[!G.j], 0, GL_FALSE, 0, GL_WRITE_ONLY, GL_RGBA8);
	glDispatchCompute(XS / 16, YS / 16, 1);
	glMemoryBarrier(GL_SHADER_STORAGE_BARRIER_BIT | GL_SHADER_IMAGE_ACCESS_BARRIER_BIT | GL_TEXTURE_FETCH_BARRIER_BIT);
	G.j = !G.j;
	G.k = !G.k;		/* pass 2 cleared it */
	G.frames++;
}

/* Pass 3: acc[G.j] and the overlay, letterboxed into a w x h window. */
static void show(int w, int h)
{
	int vw = w, vh = h;

	if ((long)w * YS > (long)h * XS)
		vw = h * XS / YS;
	else
		vh = w * YS / XS;
	glBindFramebuffer(GL_FRAMEBUFFER, 0);
	glViewport(0, 0, w, h);
	glClearColor(0, 0, 0, 1);
	glClear(GL_COLOR_BUFFER_BIT);
	glViewport((w - vw) / 2, (h - vh) / 2, vw, vh);
	glUseProgram(G.show);
	glActiveTexture(GL_TEXTURE1);
	glBindTexture(GL_TEXTURE_2D, G.ov);
	glActiveTexture(GL_TEXTURE0);
	glBindTexture(GL_TEXTURE_2D, G.acc[G.j]);
	glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
}

/* Read the published frame under the sequence lock; 0 when seq moved meanwhile */
static int take(struct dvs_shared *h, uint32_t s, struct frame *f)
{
	atomic_thread_fence(memory_order_acquire);
	*f = (struct frame){ h->l, h->a, h->b, h->end, h->window, h->baf, h->fade, h->flip, { h->pal[0], h->pal[1] },
			     h->ov_seq, h->ov_lo, h->ov_hi };
	atomic_thread_fence(memory_order_acquire);
	return atomic_load_explicit(&h->seq, memory_order_relaxed) == s;
}

static EGLDisplay egl_display(Display *x)
{
	PFNEGLGETPLATFORMDISPLAYEXTPROC gpd = (void *)eglGetProcAddress("eglGetPlatformDisplayEXT");
	EGLDisplay d = gpd ? gpd(EGL_PLATFORM_X11_KHR, x, NULL) : EGL_NO_DISPLAY;

	if (d == EGL_NO_DISPLAY || !eglInitialize(d, NULL, NULL) || !eglBindAPI(EGL_OPENGL_ES_API)) {
		fprintf(stderr, "dvsgl: no EGL on the X display\n");
		exit(1);
	}
	return d;
}

static EGLContext egl_context(EGLDisplay d, EGLConfig cfg)
{
	EGLint ca[] = { EGL_CONTEXT_MAJOR_VERSION, 3, EGL_CONTEXT_MINOR_VERSION, 1, EGL_NONE };
	EGLContext c = eglCreateContext(d, cfg, EGL_NO_CONTEXT, ca);

	if (c == EGL_NO_CONTEXT) {
		fprintf(stderr, "dvsgl: no GLES 3.1 context (0x%x)\n", eglGetError());
		exit(1);
	}
	return c;
}

static void fullscreen(Display *x, Window win)
{
	XEvent e = { .xclient = { .type = ClientMessage, .window = win, .format = 32,
				  .message_type = XInternAtom(x, "_NET_WM_STATE", False) } };

	e.xclient.data.l[0] = 2;		/* toggle */
	e.xclient.data.l[1] = XInternAtom(x, "_NET_WM_STATE_FULLSCREEN", False);
	e.xclient.data.l[3] = 1;
	XSendEvent(x, DefaultRootWindow(x), False, SubstructureRedirectMask | SubstructureNotifyMask, &e);
	XFlush(x);
}

static int view(int fd)
{
	struct stat st;
	struct dvs_shared *h;
	const struct ev *ring;
	Display *x;
	Window win;
	Atom del;
	EGLDisplay d;
	EGLConfig cfg;
	EGLSurface surf;
	EGLint n = 0, ca[] = { EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_SURFACE_TYPE, EGL_WINDOW_BIT,
			       EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT, EGL_NONE };
	uint32_t last = 0, ov_seen = 0;
	int w = XS, hgt = YS, drawn = 0, lapped = 0;
	double t_log = now(), busy = 0;
	uint64_t events = 0;

	prctl(PR_SET_PDEATHSIG, SIGTERM);	/* the capture went: so does its view */
	if (fstat(fd, &st) || st.st_size < (off_t)sizeof(*h) ||
	    (h = mmap(NULL, st.st_size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0)) == MAP_FAILED)
		return fprintf(stderr, "dvsgl: fd %d: %s\n", fd, strerror(errno)), 1;
	if (h->magic != SH_MAGIC || h->size != sizeof(*h) || h->xs != XS || h->ys != YS ||
	    (off_t)h->ring_off + (off_t)h->ring * (off_t)sizeof(struct ev) > st.st_size)
		return fprintf(stderr, "dvsgl: not a dvsview %ux%u block of this build (size %u)\n", h->xs, h->ys, h->size), 1;
	ring = (const struct ev *)((const uint8_t *)h + h->ring_off);

	x = XOpenDisplay(NULL);
	if (!x)
		return fprintf(stderr, "dvsgl: cannot open display %s\n", getenv("DISPLAY") ?: "(unset)"), 1;
	d = egl_display(x);
	if (!eglChooseConfig(d, ca, &cfg, 1, &n) || n < 1)
		return fprintf(stderr, "dvsgl: no EGL window config\n"), 1;
	win = XCreateSimpleWindow(x, DefaultRootWindow(x), 0, 0, XS, YS, 0, 0, BlackPixel(x, DefaultScreen(x)));
	XStoreName(x, win, "DVS");
	{	/* at the left edge, where dvspanel leaves room: else Openbox centred it on an empty screen and the
		 * panel covered it (11.104) */
		XSizeHints hint = { .flags = USPosition, .x = 0, .y = 0 };

		XSetWMNormalHints(x, win, &hint);
	}
	XSelectInput(x, win, KeyPressMask | StructureNotifyMask);
	del = XInternAtom(x, "WM_DELETE_WINDOW", False);
	XSetWMProtocols(x, win, &del, 1);
	XMapWindow(x, win);
	surf = eglCreateWindowSurface(d, cfg, (EGLNativeWindowType)win, NULL);
	if (surf == EGL_NO_SURFACE || !eglMakeCurrent(d, surf, surf, egl_context(d, cfg)))
		return fprintf(stderr, "dvsgl: EGL window surface (0x%x)\n", eglGetError()), 1;
	gl_init();
	fprintf(stderr, "dvsgl: %s, %s\n", glGetString(GL_RENDERER), glGetString(GL_VERSION));
	atomic_store(&h->attached, 1);

	for (;;) {
		uint32_t s = atomic_load_explicit(&h->seq, memory_order_acquire), cnt;
		struct frame f;
		double t;

		while (XPending(x)) {
			XEvent e;

			XNextEvent(x, &e);
			if (e.type == ConfigureNotify) {
				w = e.xconfigure.width;
				hgt = e.xconfigure.height;
			} else if (e.type == ClientMessage && (Atom)e.xclient.data.l[0] == del) {
				return 0;
			} else if (e.type == KeyPress) {
				KeySym k = XLookupKeysym(&e.xkey, 0);

				if (k == XK_q || k == XK_Escape)
					return 0;
				if (k == XK_f || k == XK_F11)
					fullscreen(x, win);
			}
		}
		if (s == last || s & 1 || !take(h, s, &f)) {
			syscall(SYS_futex, &h->seq, FUTEX_WAIT, s, &(struct timespec){ 0, 50000000 }, NULL, 0);
			continue;
		}
		last = s;
		t = now();
		cnt = upload(ring, h->ring, &f.l, f.b);
		if (atomic_load_explicit(&h->wr, memory_order_acquire) - f.l > h->ring) {
			lapped++;		/* the decoder overwrote some while they were copied: keep the last frame */
			continue;
		}
		if (f.ov_seq != ov_seen) {	/* the rows that changed; all of them after a missed change */
			uint32_t lo = f.ov_seq - ov_seen == 1 ? f.ov_lo : 0, hi = f.ov_seq - ov_seen == 1 ? f.ov_hi : YS;

			if (hi > YS)
				hi = YS;
			glBindTexture(GL_TEXTURE_2D, G.ov);
			if (lo < hi)
				glTexSubImage2D(GL_TEXTURE_2D, 0, 0, lo, XS, hi - lo, GL_RGBA, GL_UNSIGNED_BYTE,
						h->overlay + (size_t)lo * XS);
			ov_seen = f.ov_seq;
		}
		draw(&f, cnt);
		show(w, hgt);
		eglSwapBuffers(d, surf);
		atomic_store_explicit(&h->seen, s, memory_order_release);
		busy += now() - t;
		events += cnt;
		drawn++;
		if (now() - t_log >= 10) {
			fprintf(stderr, "dvsgl: %.1f frames/s, %.0f events/frame, %.1f ms CPU+GPU a frame, %d lapped\n",
				drawn / (now() - t_log), drawn ? (double)events / drawn : 0, drawn ? busy * 1e3 / drawn : 0,
				lapped);
			t_log = now();
			busy = 0;
			events = drawn = lapped = 0;
		}
	}
}

/* --selftest: frames of known events through passes 1 and 2, the result read back. */
static uint32_t px(int x, int y)
{
	static uint8_t rgba[XS * YS * 4];
	static unsigned read = -1;

	if (read != G.frames) {
		glMemoryBarrier(GL_FRAMEBUFFER_BARRIER_BIT);
		glBindFramebuffer(GL_FRAMEBUFFER, G.fbo);
		glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, G.acc[G.j], 0);
		glReadPixels(0, 0, XS, YS, GL_RGBA, GL_UNSIGNED_BYTE, rgba);
		read = G.frames;
	}
	return rgba[(y * XS + x) * 4] << 16 | rgba[(y * XS + x) * 4 + 1] << 8 | rgba[(y * XS + x) * 4 + 2];
}

static int selftest(void)
{
	static struct ev ring[1024];
	/* frame ends at 100 ms, 10 ms long, filter 2 ms: t0 = 88 ms, the frame from 90 ms */
	struct frame f = { .end = 100000, .window = 10000, .baf = 2000, .pal = { 0xff00ff00u, 0xffff0000u } };
	static const struct { uint32_t t; uint16_t x, y; uint32_t want; } c[] = {
		{ 95000, 100, 100, 0 },				/* alone: filtered out */
		{ 95000, 200, 200 | 0x8000, 0xff0000 },	/* a pair 0.5 ms apart: both, OFF red, */
		{ 95500, 201, 200, 0x00ff00 },			/* ON green */
		{ 89000, 300, 300, 0 },				/* before the frame: lends its neighbour support, */
		{ 90500, 301, 300, 0x00ff00 },			/* drawn */
		{ 91000, 400, 400, 0 },				/* 8 ms apart: neither */
		{ 99000, 401, 400, 0 },
		{ 80000, 500, 500, 0 },				/* before t0 (a stale slot): ignored, */
		{ 95000, 501, 500, 0 },				/* so this one stands alone */
		{ 96000, 600, 600, 0 }, { 96100, 600, 600 | 0x8000, 0 },	/* the same pixel twice, alone */
		{ 97000, 700, 700, 0 }, { 97200, 700, 701, 0 }, { 97100, 700, 700 | 0x8000, 0 },
	};
	int n = sizeof(c) / sizeof(c[0]), fails = 0, base = 1020;	/* across the ring's end */
	uint64_t l = base;
	Display *x = XOpenDisplay(NULL);
	EGLDisplay d;
	EGLConfig cfg;
	EGLint k = 0, ca[] = { EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT, EGL_NONE };

	if (!x)
		return fprintf(stderr, "dvsgl --selftest: needs DISPLAY\n"), 1;
	d = egl_display(x);
	if (!eglChooseConfig(d, ca, &cfg, 1, &k) || k < 1 ||
	    !eglMakeCurrent(d, EGL_NO_SURFACE, EGL_NO_SURFACE, egl_context(d, cfg)))
		return fprintf(stderr, "dvsgl --selftest: no surfaceless GLES 3.1 context (0x%x)\n", eglGetError()), 1;
	gl_init();
	printf("dvsgl: %s, %s\n", glGetString(GL_RENDERER), glGetString(GL_VERSION));
	for (int i = 0; i < n; i++)
		ring[(base + i) % 1024] = (struct ev){ c[i].t, c[i].x, c[i].y };
#define CHECK(what, xx, yy, want)                                                                         \
	do {                                                                                              \
		uint32_t got = px(xx, yy), w_ = (want);                                                   \
		int near_ = 1;                                                                            \
		for (int sh_ = 0; sh_ < 24; sh_ += 8)	/* GPU rounding: 255 x 0.5 is 127 or 128 */        \
			near_ &= abs((int)(got >> sh_ & 255) - (int)(w_ >> sh_ & 255)) <= 1;              \
		if (!near_) {                                                                             \
			printf("FAIL %s: (%d, %d) %06x, want %06x\n", what, xx, yy, got, (uint32_t)(want)); \
			fails++;                                                                          \
		}                                                                                         \
	} while (0)
	draw(&f, upload(ring, 1024, &l, base + n));
	CHECK("alone", 100, 100, 0);
	CHECK("pair OFF", 200, 200, 0xff0000);
	CHECK("pair ON", 201, 200, 0x00ff00);
	CHECK("lookback not drawn", 300, 300, 0);
	CHECK("lookback lends", 301, 300, 0x00ff00);
	CHECK("8 ms apart", 400, 400, 0);
	CHECK("8 ms apart", 401, 400, 0);
	CHECK("before t0", 500, 500, 0);
	CHECK("before t0 lends nothing", 501, 500, 0);
	CHECK("same pixel alone", 600, 600, 0);
	CHECK("latest polarity", 700, 700, 0xff0000);	/* 97.1 ms OFF after 97.0 ON, 97.2 below supports it */
	CHECK("its neighbour", 700, 701, 0x00ff00);
	CHECK("elsewhere black", 10, 10, 0);
	/* the same events again mirrored (flip bit 0), the filter off: every one in the frame drawn */
	f.flip = 1;
	f.baf = 0;
	l = base;
	draw(&f, upload(ring, 1024, &l, base + n));
	CHECK("mirrored", 959 - 100, 100, 0x00ff00);
	CHECK("mirrored, not there", 100, 100, 0);
	CHECK("filter off: lookback is out of the frame", 959 - 300, 300, 0);
	CHECK("filter off: 90.5 ms is in the frame", 959 - 301, 300, 0x00ff00);
	/* no events, fade 128: the last frame at half */
	f.fade = 128;
	draw(&f, 0);
	CHECK("fade", 959 - 100, 100, 0x008000);
	CHECK("fade OFF", 959 - 200, 200, 0x800000);
	f.fade = 0;
	draw(&f, 0);
	CHECK("fade 0 clears", 959 - 100, 100, 0);
	if (glGetError() != GL_NO_ERROR)
		printf("FAIL GL error\n"), fails++;
	puts(fails ? "dvsgl selftest FAILED" : "dvsgl selftest ok");
	return fails != 0;
}

int main(int argc, char **argv)
{
	if (argc == 2 && !strcmp(argv[1], "--selftest"))
		return selftest();
	if (argc != 2 || atoi(argv[1]) < 3)
		return fprintf(stderr, "usage: dvsgl FD (from ov5647-cap.py --x11) | dvsgl --selftest\n"), 1;
	return view(atoi(argv[1]));
}
