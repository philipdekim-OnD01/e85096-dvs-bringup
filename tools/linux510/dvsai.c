// SPDX-License-Identifier: GPL-2.0
/*
 * dvsai: the person action classifier (dvsact.py, 11.37) and the face width (dvsface.py, 11.99) as C threads of the
 * capture process (11.112). dvsview.c includes this file when built with -DDVS_AI (it reads the ring, pins threads
 * and marks the view directly). The models run on onnxruntime's C API from the Python wheel's libonnxruntime.so,
 * opened at start (dlopen: libdvsview.so has no link-time dependency on it; dvsact.ort_lib() finds it), each on its
 * calling thread (one intra-op thread), on CPU4-6 like the Python threads before. In Python the threads also
 * converted every frame with numpy and waited for the GIL behind the page and the capture loop.
 *
 * Classifier: "dvs-bin" bins the ring into 60 ms count frames every 60 ms (dvs_act_bin) into a queue of ACT_QUEUE
 * (full: it waits, and the ring's RING / 2 rule skips what it missed); "dvs-act" runs the model on each frame (log1p
 * of the counts, GRU state h; zeroed at a reset), the softmax, and dvs_act_observe(): dvsact.observe()'s judge, one
 * JSONL line per state change and the state for GET /act (dvs_act_status).
 * Face: "dvs-face" every 200 ms takes dvs_surface() of the last 100 ms, flipped as the screen shows it, fired pixels
 * 255, through YuNet (dvs_face_detect); the best face over FACE_THRESH is marked (dvs_mark) with the median width of
 * the last 5 in cm.
 * test_dvsai.py checks these against the Python code (dvsact.observe, the Python onnxruntime, dvsface.detect).
 */
#include <dlfcn.h>
#include "onnxruntime_c_api.h"

#define DVSAI_ORT_API 10		/* every call here is in onnxruntime 1.10's C API: older wheels than the boards' do */

#define ACT_N 9
#define ACT_FALL 7			/* dvsact.CLASSES index of fall */
#define ACT_FALL_P 0.6f			/* fall must be at least this sure to be a candidate */
#define ACT_HOLD 3			/* steps a candidate must hold before the state changes */
#define ACT_STEP_US 60000		/* dvsact.STEP_US, GAP_US */
#define ACT_GAP_US 3000000
#define ACT_QUEUE 8			/* frames dvs-bin may be ahead of the model */
#define ACT_RECENT 10			/* newest changes kept for GET /act */
#define ACT_HID 128
#define AGW (XS / 6)			/* 160 x 120 cells of 6 x 6 px, ON then OFF */
#define AGH (YS / 6)
#define ACT_CELLS (2 * AGH * AGW)
#define FACE_WIN_US 100000		/* events of the last 100 ms make the image: a still face fires little */
#define FACE_THRESH 0.4f		/* edge images of a face scored 0.44..0.53 (a photo 0.95) */
#define FACE_CM_PER_PX (17.0 / XS * 19.0 / 3.0)	/* the 960 px view is 17 cm wide; a 19 cm face 1 m away is 3 cm */

static const char *const act_class[ACT_N] = {
	"none", "still", "walk", "sit_down", "stand_up", "pick_up", "lie_down", "fall", "other",
};
#define ACT_MOVE (1u << 2 | 1u << 3 | 1u << 4 | 1u << 5 | 1u << 6 | 1u << 8)	/* dvsact.MOVE */

static const OrtApi *ort;
static OrtEnv *ort_env;
static OrtMemoryInfo *ort_cpu;

static int fail(OrtStatus *st, const char *what, char *err, size_t n)
{
	if (!st)
		return 0;
	snprintf(err, n, "%s: %s", what, ort->GetErrorMessage(st));
	ort->ReleaseStatus(st);
	return 1;
}

/* onnxruntime from lib, once for both models; 0, or -1 and why in err */
static int ort_open(const char *lib, char *err, size_t n)
{
	static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
	const OrtApiBase *(*base)(void);
	void *h;
	int r = 0;

	pthread_mutex_lock(&mu);
	if (ort)
		goto out;
	h = dlopen(lib, RTLD_NOW | RTLD_LOCAL);
	base = h ? (const OrtApiBase *(*)(void))dlsym(h, "OrtGetApiBase") : NULL;
	if (!base) {
		snprintf(err, n, "%s", dlerror());
		r = -1;
		goto out;
	}
	ort = base()->GetApi(DVSAI_ORT_API);
	if (!ort) {
		snprintf(err, n, "onnxruntime %s: no C API %d", base()->GetVersionString(), DVSAI_ORT_API);
		r = -1;
	} else if (fail(ort->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "dvsai", &ort_env), "CreateEnv", err, n) ||
		   fail(ort->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &ort_cpu), "MemoryInfo", err, n)) {
		ort = NULL;
		r = -1;
	}
out:
	pthread_mutex_unlock(&mu);
	return r;
}

struct net {
	OrtSession *s;
	size_t nin, nout;
	char *in[4], *out[16];
};

static int net_open(struct net *m, const char *lib, const char *path, char *err, size_t n)
{
	OrtSessionOptions *o = NULL;
	OrtAllocator *a;
	int bad;

	if (ort_open(lib, err, n))
		return -1;
	bad = fail(ort->CreateSessionOptions(&o), "SessionOptions", err, n) ||
	      fail(ort->SetIntraOpNumThreads(o, 1), "IntraOp", err, n) ||
	      fail(ort->SetInterOpNumThreads(o, 1), "InterOp", err, n) ||
	      fail(ort->SetSessionGraphOptimizationLevel(o, ORT_ENABLE_ALL), "GraphOptimization", err, n) ||
	      fail(ort->CreateSession(ort_env, path, o, &m->s), path, err, n) ||
	      fail(ort->GetAllocatorWithDefaultOptions(&a), "Allocator", err, n) ||
	      fail(ort->SessionGetInputCount(m->s, &m->nin), "InputCount", err, n) ||
	      fail(ort->SessionGetOutputCount(m->s, &m->nout), "OutputCount", err, n);
	if (o)
		ort->ReleaseSessionOptions(o);
	if (bad)
		return -1;
	if (m->nin > 4 || m->nout > 16) {
		snprintf(err, n, "%s: %zu inputs, %zu outputs", path, m->nin, m->nout);
		return -1;
	}
	for (size_t i = 0; i < m->nin + m->nout; i++) {
		char *name;

		if (fail(i < m->nin ? ort->SessionGetInputName(m->s, i, a, &name) :
			 ort->SessionGetOutputName(m->s, i - m->nin, a, &name), "Name", err, n))
			return -1;
		*(i < m->nin ? &m->in[i] : &m->out[i - m->nin]) = strdup(name);
		ort->ReleaseStatus(ort->AllocatorFree(a, name));
	}
	return 0;
}

static int net_find(const struct net *m, const char *name, int input)
{
	for (size_t i = 0; i < (input ? m->nin : m->nout); i++)
		if (!strcmp((input ? m->in : m->out)[i], name))
			return i;
	return -1;
}

static OrtValue *tensor(float *p, const int64_t *shape, size_t dims, char *err, size_t n)
{
	OrtValue *v = NULL;
	size_t k = 1;

	for (size_t i = 0; i < dims; i++)
		k *= shape[i];
	return fail(ort->CreateTensorWithDataAsOrtValue(ort_cpu, p, k * sizeof(float), shape, dims,
							ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &v), "Tensor", err, n) ? NULL : v;
}

/* out[i] float data and its element count, or NULL */
static float *out_data(OrtValue *v, size_t *count)
{
	OrtTensorTypeAndShapeInfo *t;
	float *p = NULL;

	if (ort->GetTensorTypeAndShape(v, &t))
		return NULL;
	if (ort->GetTensorShapeElementCount(t, count) || ort->GetTensorMutableData(v, (void **)&p))
		p = NULL;
	ort->ReleaseTensorTypeAndShapeInfo(t);
	return p;
}

static void outs_free(OrtValue **v, size_t n)
{
	for (size_t i = 0; i < n; i++)
		if (v[i])
			ort->ReleaseValue(v[i]);
}

static float median_of(const float *v, int n)
{
	float c[256];

	n = n < 256 ? n : 256;
	memcpy(c, v, n * sizeof(*c));
	return n ? median(c, n) : 0;
}

static int64_t now_ns_real(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_REALTIME, &ts);
	return ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

/* Python's repr(round(p, 2)) */
static const char *round2(char *s, double p)
{
	size_t k = snprintf(s, 16, "%.2f", p);

	while (k > 1 && s[k - 1] == '0' && s[k - 2] != '.')
		s[--k] = 0;
	return s;
}

/* ---- the classifier ---- */

static struct {
	struct net m;
	int ix, ih, oy, oh;		/* the model's x, h inputs; y (logits), h2 outputs */
	OrtValue *vx, *vh;		/* over act_x, act_h */
	FILE *out;
	uint32_t cpus, keep;		/* keep: the classes judged (bits): the "classes" file beside the model */
	pthread_mutex_t mu;		/* the queue; act_got below */
	pthread_cond_t put;
	int qh, qn;			/* act_q: the first, how many */
	pthread_mutex_t st;		/* the judge and the status */
	int on, state, cand, held, top;	/* cand -1: none (a fall under ACT_FALL_P); top -1 before the first step */
	float p;
	const char *ev;			/* the last change's event */
	char recent[ACT_RECENT][192];	/* JSONL lines, newest first */
	int nrecent;
} A = { .mu = PTHREAD_MUTEX_INITIALIZER, .st = PTHREAD_MUTEX_INITIALIZER, .put = PTHREAD_COND_INITIALIZER,
	.cand = -1, .top = -1, .keep = (1u << ACT_N) - 1 };
static pthread_cond_t act_got;			/* on CLOCK_MONOTONIC: dvs-act waits on it up to 2 s */
static float act_x[ACT_CELLS], act_h[ACT_HID];	/* outside A: its initializer would put them in the file */
static struct { int kind; uint16_t c[ACT_CELLS]; } act_q[ACT_QUEUE];	/* kind 1 frame, 2 reset (dvs_bin_run) */

/* The classes judged: the words of s among dvsact.CLASSES (dvsact.CLASSES_FILE); none known or s NULL: all */
int dvs_act_classes(const char *s)
{
	uint32_t keep = 0;

	for (int i = 0; s && i < ACT_N; i++) {
		size_t k = strlen(act_class[i]);

		for (const char *w = s; (w = strstr(w, act_class[i])); w += k)
			if ((w == s || strchr(" \t\n", w[-1])) && strchr(" \t\n", w[k]))
				keep |= 1u << i;
	}
	pthread_mutex_lock(&A.st);
	A.keep = keep ? keep : (1u << ACT_N) - 1;
	pthread_mutex_unlock(&A.st);
	return __builtin_popcount(A.keep);
}

static int argmax(const float *p, int n)
{
	int k = 0;

	for (int i = 1; i < n; i++)
		if (p[i] > p[k])
			k = i;
	return k;
}

/*
 * One step's class probabilities (softmax, all classes) through dvsact.observe(): only the judged classes, summing to
 * 1; the judge (a candidate held ACT_HOLD steps becomes the state; fall only at ACT_FALL_P); the status. A change
 * gives its JSONL line (dvsact.line, with now_ns) in line and returns 1, else 0.
 */
int dvs_act_observe(const float *probs, int64_t now_ns, char *line, int size)
{
	float p[ACT_N], sum = 0;
	char t[32], ps[16];
	struct tm tm;
	time_t sec;
	int k, c, prev, all, r = 0;
	const char *ev;

	pthread_mutex_lock(&A.st);
	all = A.keep == (1u << ACT_N) - 1;
	for (int i = 0; i < ACT_N; i++)
		sum += p[i] = all || A.keep >> i & 1 ? probs[i] : 0;
	for (int i = 0; i < ACT_N && !all; i++)
		p[i] /= sum;
	k = argmax(p, ACT_N);
	c = k == ACT_FALL && p[k] < ACT_FALL_P ? -1 : k;
	A.held = c == A.cand ? A.held + 1 : 1;
	A.cand = c;
	A.top = k;
	A.p = p[k];
	if (c >= 0 && A.held >= ACT_HOLD && c != A.state) {
		prev = A.state;
		A.state = c;
		A.ev = ev = c == ACT_FALL ? "fall" : prev == 0 ? "appear" : ACT_MOVE >> c & 1 ? "move" : act_class[c];
		sec = now_ns / 1000000000;
		gmtime_r(&sec, &tm);
		strftime(t, sizeof(t), "%Y-%m-%dT%H:%M:%S", &tm);
		snprintf(line, size, "{\"t_us\": %lld, \"time\": \"%s.%06lldZ\", \"event\": \"%s\", \"state\": \"%s\", "
			 "\"prev\": \"%s\", \"p\": %s}", (long long)(now_ns / 1000), t, (long long)(now_ns / 1000 % 1000000),
			 ev, act_class[c], act_class[prev], round2(ps, p[k]));
		memmove(A.recent[1], A.recent[0], sizeof(A.recent[0]) * (ACT_RECENT - 1));
		snprintf(A.recent[0], sizeof(A.recent[0]), "%s", line);
		A.nrecent += A.nrecent < ACT_RECENT;
		r = 1;
	}
	pthread_mutex_unlock(&A.st);
	return r;
}

/* GET /act (dvsctl): json.dumps(dvsact.STATUS) as the Python classifier gave it; returns the length */
int dvs_act_status(char *buf, int size)
{
	char ps[16];
	int k;

	pthread_mutex_lock(&A.st);
	k = snprintf(buf, size, "{\"on\": %s, \"state\": \"%s\", \"top\": %s%s%s, \"p\": %s, \"recent\": [",
		     A.on ? "true" : "false", act_class[A.state], A.top < 0 ? "" : "\"",
		     A.top < 0 ? "null" : act_class[A.top], A.top < 0 ? "" : "\"", round2(ps, A.p));
	for (int i = 0; i < A.nrecent && k < size; i++)
		k += snprintf(buf + k, size - k, "%s%s", i ? ", " : "", A.recent[i]);
	if (k < size)
		k += snprintf(buf + k, size - k, "]}");
	pthread_mutex_unlock(&A.st);
	return k < size ? k : -ENOSPC;
}

/* One model step on a frame's counts: probs = the softmax of the logits, h the GRU state after it. counts NULL: a
 * reset (h = 0). 0, or -1 and why in err. */
static int act_step(const uint16_t *counts, float *probs, char *err, size_t n)
{
	OrtValue *in[2], *out[16] = { 0 };
	size_t ny, nh;
	float *y, *h2, mx, sum = 0;

	if (!counts) {
		memset(act_h, 0, sizeof(act_h));
		return 0;
	}
	for (int i = 0; i < ACT_CELLS; i++)
		act_x[i] = log1pf(counts[i]);
	in[A.ix] = A.vx;
	in[A.ih] = A.vh;
	if (fail(ort->Run(A.m.s, NULL, (const char *const *)A.m.in, (const OrtValue *const *)in, 2,
			  (const char *const *)A.m.out, A.m.nout, out), "Run", err, n))
		return outs_free(out, A.m.nout), -1;
	y = out_data(out[A.oy], &ny);
	h2 = out_data(out[A.oh], &nh);
	if (!y || !h2 || ny != ACT_N || nh != ACT_HID) {
		snprintf(err, n, "outputs: %zu logits, %zu state", ny, nh);
		return outs_free(out, A.m.nout), -1;
	}
	memcpy(act_h, h2, sizeof(act_h));
	mx = y[argmax(y, ACT_N)];
	for (int i = 0; i < ACT_N; i++)
		sum += probs[i] = expf(y[i] - mx);
	for (int i = 0; i < ACT_N; i++)
		probs[i] /= sum;
	outs_free(out, A.m.nout);
	return 0;
}

/* the test's door to act_step (dvs_act_start with cpus 0 first) */
int dvs_act_step(const uint16_t *counts, float *probs, char *err, int n)
{
	return A.vx ? act_step(counts, probs, err, n) : -1;
}

static void act_put(int kind, const uint32_t *acc)
{
	uint16_t *c;

	pthread_mutex_lock(&A.mu);
	while (A.qn == ACT_QUEUE)
		pthread_cond_wait(&A.put, &A.mu);
	act_q[(A.qh + A.qn) % ACT_QUEUE].kind = kind;
	c = act_q[(A.qh + A.qn) % ACT_QUEUE].c;
	for (int i = 0; kind == 1 && i < ACT_CELLS; i++)
		c[i] = acc[i] < 65535 ? acc[i] : 65535;
	A.qn++;
	pthread_cond_signal(&act_got);
	pthread_mutex_unlock(&A.mu);
}

/* dvs-bin: the ring's frames and resets into the queue, all that came since its last wake, every ACT_STEP_US */
static void *act_bin(void *arg)
{
	static uint32_t acc[ACT_CELLS];
	struct dvs_binner s = { .cur = UINT64_MAX, .step = ACT_STEP_US, .gap = ACT_GAP_US };
	struct timespec wake;
	int r;

	dvs_pin(A.cpus, "dvs-bin");
	clock_gettime(CLOCK_MONOTONIC, &wake);
	for (;;) {
		struct timespec now;

		while ((r = dvs_act_bin(&s, acc)))
			act_put(r, acc);
		wake.tv_nsec += ACT_STEP_US * 1000L;
		if (wake.tv_nsec >= 1000000000L) {
			wake.tv_sec++;
			wake.tv_nsec -= 1000000000L;
		}
		clock_gettime(CLOCK_MONOTONIC, &now);
		if (now.tv_sec > wake.tv_sec || (now.tv_sec == wake.tv_sec && now.tv_nsec > wake.tv_nsec))
			wake = now;
		clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &wake, NULL);
	}
	return arg;
}

static void act_off(const char *why)
{
	pthread_mutex_lock(&A.st);
	A.on = 0;
	pthread_mutex_unlock(&A.st);
	fprintf(stderr, "[dvsact] off: %s\n", why);
}

/* dvs-act: the model, the judge and the JSONL file; its timing on stderr every 2 s, as dvsact.py did */
static void *act_run(void *arg)
{
	static uint16_t c[ACT_CELLS];
	float probs[ACT_N], ms[256], mx = 0;
	char err[512], line[256];
	int64_t t_log = now_us();
	int nms = 0;

	dvs_pin(A.cpus, "dvs-act");
	for (;;) {
		struct timespec to;
		int kind = 0;

		clock_gettime(CLOCK_MONOTONIC, &to);
		to.tv_sec += 2;
		pthread_mutex_lock(&A.mu);
		while (!A.qn && pthread_cond_timedwait(&act_got, &A.mu, &to) != ETIMEDOUT)
			;
		if (A.qn) {
			kind = act_q[A.qh].kind;
			if (kind == 1)
				memcpy(c, act_q[A.qh].c, sizeof(c));
			A.qh = (A.qh + 1) % ACT_QUEUE;
			A.qn--;
			pthread_cond_signal(&A.put);
		}
		pthread_mutex_unlock(&A.mu);
		if (kind) {
			int64_t t0 = now_us();

			if (act_step(kind == 1 ? c : NULL, probs, err, sizeof(err))) {
				act_off(err);
				return arg;
			}
			if (kind == 1) {
				if (dvs_act_observe(probs, now_ns_real(), line, sizeof(line))) {
					char ps[16];

					fprintf(A.out, "%s\n", line);
					fflush(A.out);
					fprintf(stderr, "[dvsact] %s %s p %.2f\n", A.ev, act_class[A.state],
						atof(round2(ps, A.p)));
				}
				ms[nms < 256 ? nms++ : 255] = (now_us() - t0) / 1000.0f;
				mx = fmaxf(mx, ms[nms - 1]);
			}
		}
		if (now_us() - t_log >= 2000000) {
			fprintf(stderr, "[dvsact] steps %d, step median %.1f ms max %.1f ms, queued %d, lost %llu\n", nms,
				median_of(ms, nms), mx, A.qn, (unsigned long long)atomic_load(&S.act_lost));
			nms = 0;
			mx = 0;
			t_log = now_us();
		}
	}
	return arg;
}

/*
 * The classifier: onnxruntime from ort, the model (inputs x [1, 2, 120, 160] and h [1, 128]; outputs the logits,
 * then h2), the judged classes from the "classes" file beside it, JSONL lines appended to out. cpus: the threads'
 * CPUs (bits); 0: the model only, no threads (test_dvsai: dvs_act_step). 0, or -1 and why in err.
 */
int dvs_act_start(const char *ort_lib, const char *model, const char *out, uint32_t cpus, char *err, int n)
{
	static const int64_t sx[] = { 1, 2, AGH, AGW }, sh[] = { 1, ACT_HID };
	pthread_condattr_t ca;
	pthread_t th;
	char cls[256], *d = strdup(model), *slash = strrchr(d, '/');
	FILE *f;

	snprintf(cls, sizeof(cls), "%s/classes", slash ? (*slash = 0, d) : ".");
	free(d);
	if ((f = fopen(cls, "r"))) {
		size_t k = fread(cls, 1, sizeof(cls) - 2, f);

		fclose(f);
		cls[k] = '\n';
		cls[k + 1] = 0;
		dvs_act_classes(cls);
	}
	if (net_open(&A.m, ort_lib, model, err, n))
		return -1;
	A.ix = net_find(&A.m, "x", 1);
	A.ih = net_find(&A.m, "h", 1);
	if (A.m.nin != 2 || A.ix < 0 || A.ih < 0 || A.m.nout < 2) {
		snprintf(err, n, "%s: not x, h -> y, h2", model);
		return -1;
	}
	A.oy = 0;
	A.oh = 1;
	if (!(A.vx = tensor(act_x, sx, 4, err, n)) || !(A.vh = tensor(act_h, sh, 2, err, n)))
		return -1;
	if (!cpus)
		return 0;
	if (!(A.out = fopen(out, "a"))) {
		snprintf(err, n, "%s: %s", out, strerror(errno));
		return -1;
	}
	pthread_condattr_init(&ca);
	pthread_condattr_setclock(&ca, CLOCK_MONOTONIC);
	pthread_cond_init(&act_got, &ca);
	A.cpus = cpus;
	A.on = 1;
	if (pthread_create(&th, NULL, act_run, NULL) || pthread_create(&th, NULL, act_bin, NULL)) {
		snprintf(err, n, "pthread_create");
		return -1;
	}
	fprintf(stderr, "[dvsact] judging");
	for (int i = 0; i < ACT_N; i++)
		if (A.keep >> i & 1)
			fprintf(stderr, " %s", act_class[i]);
	fprintf(stderr, "\n");
	return 0;
}

/* ---- the face width ---- */

static struct {
	struct net m;
	const char *model;
	const char *want[9];		/* the outputs dvsface.decode reads */
	float *in;
	size_t cap;
	uint32_t cpus;
} F;

/*
 * The best face in img (w x h, 0 = no event, else fired: dvsface.detect on np.where(img > 0, 255, 0)), padded to
 * multiples of 32 at the bottom and right, gray in all three channels. Returns 1 and box = score, x, y, w, h in img
 * px (dvsface.decode's best over thresh), 0 for none, -1 and why in err.
 */
int dvs_face_detect(const uint8_t *img, int w, int h, float thresh, float *box, char *err, int n)
{
	static const int st[3] = { 8, 16, 32 };
	int64_t shape[4] = { 1, 3, (h + 31) / 32 * 32, (w + 31) / 32 * 32 };
	size_t plane = shape[2] * shape[3], cnt[3];
	OrtValue *in, *o9[9] = { 0 };
	int found = 0;

	if (!F.m.s)
		return snprintf(err, n, "no model"), -1;
	if (3 * plane > F.cap) {
		free(F.in);
		if (!(F.in = malloc(3 * plane * sizeof(float))))
			return snprintf(err, n, "no memory"), -1;
		F.cap = 3 * plane;
	}
	memset(F.in, 0, plane * sizeof(float));
	for (int y = 0; y < h; y++)
		for (int x = 0; x < w; x++)
			F.in[y * shape[3] + x] = img[y * w + x] ? 255.0f : 0.0f;
	memcpy(F.in + plane, F.in, plane * sizeof(float));
	memcpy(F.in + 2 * plane, F.in, plane * sizeof(float));
	if (!(in = tensor(F.in, shape, 4, err, n)))
		return -1;
	if (fail(ort->Run(F.m.s, NULL, (const char *const *)F.m.in, (const OrtValue *const *)&in, 1, F.want, 9, o9),
		 "Run", err, n)) {
		ort->ReleaseValue(in);
		outs_free(o9, 9);
		return -1;
	}
	ort->ReleaseValue(in);
	for (int k = 0; k < 3; k++) {
		const float *c = out_data(o9[k], &cnt[0]), *o = out_data(o9[3 + k], &cnt[1]), *b = out_data(o9[6 + k], &cnt[2]);
		int g = shape[3] / st[k];

		if (!c || !o || !b || cnt[1] != cnt[0] || cnt[2] != 4 * cnt[0]) {
			snprintf(err, n, "outputs of stride %d", st[k]);
			outs_free(o9, 9);
			return -1;
		}
		for (size_t i = 0; i < cnt[0]; i++) {
			float s = sqrtf(fminf(fmaxf(c[i], 0), 1) * fminf(fmaxf(o[i], 0), 1)), bw, bh;
			const float *v = b + 4 * i;

			if (s < thresh || (found && s <= box[0]))
				continue;
			bw = expf(v[2]) * st[k];
			bh = expf(v[3]) * st[k];
			box[0] = s;
			box[1] = (i % g + v[0]) * st[k] - bw / 2;
			box[2] = (i / g + v[1]) * st[k] - bh / 2;
			box[3] = bw;
			box[4] = bh;
			found = 1;
		}
	}
	outs_free(o9, 9);
	return found;
}

/* dvs-face: every 200 ms, as dvsface.py did */
static void *face_run(void *arg)
{
	static uint8_t surf[SY * SX], img[SY * SX];
	float box[5], widths[5], ms[64];
	char err[512], label[32] = "-";
	uint32_t info[4];
	int64_t t_log = now_us();
	int nw = 0, nms = 0, faces = 0;

	dvs_pin(F.cpus, "dvs-face");
	fprintf(stderr, "[dvsface] on: %s, %.4f cm/px (17 cm view, 19 cm face = 3 cm at 1 m)\n", F.model, FACE_CM_PER_PX);
	for (;;) {
		int flip, r;
		int64_t t0;

		usleep(200000);
		dvs_surface(0, 0, NULL, info);			/* the newest event's time */
		if (dvs_surface(info[0], FACE_WIN_US, surf, info) >= 0) {
			flip = dvs_get("flip");			/* the face as on the screen (upright) */
			for (int y = 0; y < SY; y++)
				for (int x = 0; x < SX; x++)
					img[y * SX + x] = surf[(flip & 2 ? SY - 1 - y : y) * SX + (flip & 1 ? SX - 1 - x : x)];
			t0 = now_us();
			r = dvs_face_detect(img, SX, SY, FACE_THRESH, box, err, sizeof(err));
			if (r < 0) {
				fprintf(stderr, "[dvsface] off: %s\n", err);
				return arg;
			}
			ms[nms < 64 ? nms++ : 63] = (now_us() - t0) / 1000.0f;
			if (r) {
				faces++;
				if (nw == 5)
					memmove(widths, widths + 1, 4 * sizeof(*widths));
				widths[nw < 5 ? nw++ : 4] = box[3] * 2;	/* surface px -> view px */
				snprintf(label, sizeof(label), "W %.1f cm", median_of(widths, nw) * FACE_CM_PER_PX);
				dvs_mark((int)(box[1] * 2), (int)(box[2] * 2), (int)(box[3] * 2), (int)(box[4] * 2), label);
			}
		}
		if (now_us() - t_log >= 2000000) {
			fprintf(stderr, "[dvsface] %d runs, %.0f ms median, %d faces, last %s\n", nms, median_of(ms, nms),
				faces, label);
			nms = faces = 0;
			t_log = now_us();
		}
	}
	return arg;
}

/* The face width: onnxruntime from ort, YuNet with a free input size (dvsface.py --free-size), its thread on cpus
 * (bits; 0: the model only, test_dvsai: dvs_face_detect). 0, or -1 and why in err. */
int dvs_face_start(const char *ort_lib, const char *model, uint32_t cpus, char *err, int n)
{
	static const char *const names[9] = { "cls_8", "cls_16", "cls_32", "obj_8", "obj_16", "obj_32",
					      "bbox_8", "bbox_16", "bbox_32" };
	pthread_t th;

	if (net_open(&F.m, ort_lib, model, err, n))
		return -1;
	for (int i = 0; i < 9; i++)
		if (net_find(&F.m, names[i], 0) < 0)
			return snprintf(err, n, "%s: no output %s", model, names[i]), -1;
	memcpy(F.want, names, sizeof(names));
	if (F.m.nin != 1)
		return snprintf(err, n, "%s: %zu inputs", model, F.m.nin), -1;
	F.model = strdup(model);
	F.cpus = cpus;
	if (cpus && pthread_create(&th, NULL, face_run, NULL))
		return snprintf(err, n, "pthread_create"), -1;
	return 0;
}
