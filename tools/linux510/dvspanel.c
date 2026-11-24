/*
 * dvspanel: the settings page of the DVS (dvsctl.html on :8080) as a window beside the `dvs` view (features/dvs,
 * linux510 docs 11.98). A GTK 3 client of the same HTTP API on 127.0.0.1, with the page's names and texts:
 *   left: the action classifier, web addresses, stats, display presets and settings (/params, at once), sensor
 *         settings (/sensor, the capture restarts and `dvs` starts it again);
 *   right: stats graphs (/history: count tiles and charts, period, CSV), action recording (/rec), sensor register
 *         read (/reg; values only: the register names of the vendor setfile stay out of this source).
 * Left out, the view being beside it: the preview, its crop and rotation, and zoom (no effect in the 960x720 window).
 * The window shows itself once the page answers (nothing on a board without the DVS) and follows capture restarts.
 *
 *   dvspanel [PORT]               PORT of the page, default 8080
 *   dvspanel --selftest PORT      against a stub server (dvspanel-test.sh): loads it all, moves a slider, records
 *
 * Build: gcc -O2 -Wall -o dvspanel dvspanel.c $(pkg-config --cflags --libs gtk+-3.0 libcjson) -lm
 */
#include <arpa/inet.h>
#include <cjson/cJSON.h>
#include <ctype.h>
#include <gtk/gtk.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <time.h>
#include <unistd.h>

static int port = 8080;

/* One HTTP/1.0 request to 127.0.0.1:port (the server closes after the answer). The parsed JSON body (cJSON_Delete
 * it) or NULL; *code: the HTTP status, 0 when nothing answered. Two second timeouts: a restarting capture refuses. */
static cJSON *req(const char *method, const char *path, const char *body, int *code)
{
	struct sockaddr_in a = { .sin_family = AF_INET, .sin_port = htons(port),
				 .sin_addr.s_addr = htonl(INADDR_LOOPBACK) };
	struct timeval tv = { 2, 0 };
	char head[512], *buf = NULL, *b;
	size_t n = 0, cap = 0;
	ssize_t r;
	int fd = socket(AF_INET, SOCK_STREAM, 0), len, st = 0;
	cJSON *j = NULL;

	if (fd < 0)
		goto out;
	setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
	setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
	len = snprintf(head, sizeof(head), "%s %s HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: %zu\r\n\r\n",
		       method, path, body ? strlen(body) : 0);
	if (len >= (int)sizeof(head) || connect(fd, (struct sockaddr *)&a, sizeof(a)) || write(fd, head, len) != len ||
	    (body && write(fd, body, strlen(body)) != (ssize_t)strlen(body)))
		goto out;
	for (;;) {
		if (n + 4097 > cap) {
			char *p = realloc(buf, cap = cap * 2 + 16384);

			if (!p)
				goto out;
			buf = p;
		}
		if ((r = read(fd, buf + n, cap - n - 1)) <= 0)
			break;
		n += r;
	}
	if (!buf)
		goto out;
	buf[n] = 0;
	if (sscanf(buf, "HTTP/1.%*d %d", &st) == 1 && (b = strstr(buf, "\r\n\r\n")))
		j = cJSON_Parse(b + 4);
out:
	if (fd >= 0)
		close(fd);
	free(buf);
	if (code)
		*code = st;
	return j;
}

/* a member's number, NAN if absent or not a number (null) */
static double num(const cJSON *o, const char *k)
{
	const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);

	return cJSON_IsNumber(v) ? v->valuedouble : NAN;
}

static const char *str(const cJSON *o, const char *k)
{
	const cJSON *v = cJSON_GetObjectItemCaseSensitive(o, k);

	return cJSON_IsString(v) ? v->valuestring : NULL;
}

/* the page's fmt(): "–", 1,234 at 100 and above, else 3 significant digits */
static const char *fmt(char *b, size_t n, double x)
{
	char d[32];
	long v;
	size_t i, k = 0, len;

	if (isnan(x)) {
		snprintf(b, n, "–");
		return b;
	}
	if (fabs(x) < 100) {
		snprintf(b, n, "%.3g", x);
		return b;
	}
	v = lround(x);
	len = snprintf(d, sizeof(d), "%ld", labs(v));
	if (v < 0 && k + 1 < n)
		b[k++] = '-';
	for (i = 0; i < len && k + 2 < n; i++) {
		b[k++] = d[i];
		if ((len - i - 1) % 3 == 0 && i + 1 < len)
			b[k++] = ',';
	}
	b[k] = 0;
	return b;
}

static void hhmmss(char *b, size_t n, double t)
{
	time_t s = (time_t)t;
	struct tm tm;

	localtime_r(&s, &tm);
	strftime(b, n, "%H:%M:%S", &tm);
}

/* ---- the page's tables ---- */
static const struct {
	const char *name, *label, *unit, *hint, *const *choices;
} PARAMS[] = {
	{ "fps", "화면 갱신", "fps", "초당 그리는 횟수" },
	{ "window_ms", "누적 시간", "ms", "한 장에 모아 그리는 센서 시간, 0 = 한 프레임" },
	{ "fade", "잔상", "", "0 = 매번 지움, 클수록 이전 장면이 오래 남음" },
	{ "filter_ms", "잡음 필터", "ms", "이웃 픽셀이 이 시간 안에 반응한 이벤트만, 0 = 끔" },
	{ "color", "색", "", NULL,
	  (const char *const[]){ "ON 초록 / OFF 빨강", "ON 흰색 / OFF 파랑", "모두 흰색", "ON만", "OFF만", NULL } },
	{ "flip", "뒤집기", "", NULL, (const char *const[]){ "그대로", "좌우 반전", "상하 반전", "180° 회전", NULL } },
	{ "margin_ms", "재생 여유", "ms", "작을수록 지연이 줄고 늦은 이벤트를 놓침" },
};
#define NP (sizeof(PARAMS) / sizeof(*PARAMS))
/* presets: -1 = keep; "기본값" takes every default */
static const struct {
	const char *label;
	int v[NP];
} PRESETS[] = {
	{ "잡음 제거", { -1, -1, 0, 4, -1, -1, -1 } },
	{ "잔상", { -1, 0, 200, -1, -1, -1, -1 } },
};
/* sensor: scan rate -> CIS margin, fitted to margin 2/17/18/48 = 495/985/1015/1985 us (11.29); 0 = the setfile's 2 */
#define SCAN_BASE 430.0
#define SCAN_LSB 32.4
static const int RATES[] = { 0, 1500, 1000, 750, 500, 250 };
#define NR (sizeof(RATES) / sizeof(*RATES))
static int margin_for(int fps) { return fps ? (int)fmax(1, floor((1e6 / fps - SCAN_BASE) / SCAN_LSB)) : 0; }
static double scan_for(int m) { return SCAN_BASE + SCAN_LSB * (m ? m : 2); }
/* recorder labels in dvsact.CLASSES order; the classifier's states */
static const char *const ACTS[][2] = { { "none", "사람 없음" }, { "still", "가만히" }, { "walk", "걷기" },
	{ "sit_down", "앉기" }, { "stand_up", "일어서기" }, { "pick_up", "줍기" }, { "lie_down", "눕기" },
	{ "fall", "넘어짐" }, { "other", "기타 움직임" } };
#define NA (sizeof(ACTS) / sizeof(*ACTS))
static const int REC_S[] = { 4, 6, 10, 20 };

static const char *act_name(const char *s)
{
	if (!s)
		return "–";
	if (!strcmp(s, "sit_down"))
		return "앉음";
	if (!strcmp(s, "stand_up"))
		return "서 있음";
	for (size_t i = 0; i < NA; i++)
		if (!strcmp(s, ACTS[i][0]))
			return ACTS[i][1];
	return s;
}

static const char *event_name(const char *e)
{
	return !e ? "–" : !strcmp(e, "appear") ? "나타남" : !strcmp(e, "move") ? "움직임" : !strcmp(e, "fall") ? "넘어짐!" : e;
}

/* graphs: the page's CHARTS and TILES over /history (one row per 2 s, an hour) */
enum { C_VAL, C_RATE, C_MIPIMB, C_DATAMB, C_FILL };
static const struct chart {
	const char *grp, *name, *unit;
	int kind;
	const char *key;
	int bufaxis;
} CHARTS[] = {
	{ "MIPI (CSIS 수신)", "MIPI 프레임 (10초 평균)", "/s", C_RATE, "mipi_fe" },
	{ "MIPI (CSIS 수신)", "MIPI 데이터량 (10초 평균)", "MB/s", C_MIPIMB },
	{ "MIPI (CSIS 수신)", "이벤트 데이터", "MB/s", C_DATAMB },
	{ "MIPI (CSIS 수신)", "프레임 채움", "%", C_FILL },
	{ "DRAM (VC0 버퍼)", "도착 버퍼", "/s", C_VAL, "buffers" },
	{ "DRAM (VC0 버퍼)", "빈 버퍼 (DMA용, 최소)", "개", C_VAL, "vc0_free", 1 },
	{ "DRAM (VC0 버퍼)", "처리 대기 버퍼 (최대)", "개", C_VAL, "vc0_wait", 1 },
	{ "DRAM (VC0 버퍼)", "버퍼 복사", "ms", C_VAL, "ingest_ms" },
	{ "화면", "화면 갱신", "fps", C_VAL, "fps" },
	{ "화면", "그리기", "ms", C_VAL, "draw_ms" },
	{ "화면", "프레임당 이벤트", "", C_VAL, "events" },
	{ "화면", "최신 이벤트보다 늦음", "ms", C_VAL, "behind_ms" },
};
#define NC (sizeof(CHARTS) / sizeof(*CHARTS))
enum { T_SUM, T_ADD, T_DIFF, T_LAST, T_MIN };
static const struct tile {
	const char *name;
	int kind;
	const char *key, *unit;
} TILES[] = {
	{ "버퍼 안 된 MIPI 프레임", T_DIFF }, { "CSIS 링크 오류", T_SUM, "mipi_err" }, { "CRC 오류", T_SUM, "crc" },
	{ "짧은 버퍼", T_SUM, "short_bufs" }, { "scan 누락", T_SUM, "ts_missed" }, { "frame_end 누락", T_SUM, "fe_missed" },
	{ "타임스탬프 오류", T_SUM, "ts_bad" }, { "모르는 워드", T_SUM, "unknown" }, { "어두운 프레임", T_ADD, "dips" },
	{ "센서 scan", T_LAST, "scan_us", "µs" }, { "데이터 있는 시간", T_MIN, "coverage", "%" },
};
#define NT (sizeof(TILES) / sizeof(*TILES))
static const int WINS[] = { 120, 600, 1800, 3600 };
static const char *const WIN_NAMES[] = { "2분", "10분", "30분", "1시간" };
#define MAXK 48
#define MAXROWS 1800

static struct {
	GtkWidget *win, *act_state, *act_top, *nosensor, *addr, *stats, *recent, *msg;
	GtkWidget *p[NP], *pv[NP];
	GtkWidget *s_rate, *s_hint, *s_flush, *s_flushv, *s_msg;
	GtkWidget *h_win, *h_msg, *tile_v[NT], *tile_r[NT], *chart[NC], *chart_v[NC], *chart_r[NC];
	GtkWidget *r_label, *r_s, *r_go, *r_msg;
	GtkWidget *g_a, *g_n, *g_msg, *g_out;
	int loading;			/* setting widgets from the server: no POST back */
	int up;				/* the page answered the last poll */
	double def[NP];
	int pending[NP];		/* -1: nothing to send; the page's 120 ms batching of slider moves */
	guint post_timer;
	int s_margins[NR + 1], s_nm;	/* the margin of each scan rate entry */
	double s_now;
	double apply_t;			/* a sensor apply waits for the capture to go away and come back */
	int apply_down;
	double crc_n, crc_t;
	char keys[MAXK][24];
	int nkeys;
	double rows[MAXROWS][MAXK];	/* NAN: null */
	int nrows;
	double frame_bytes;
	int tick;
} W;

static double now_s(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

static GtkWidget *label(const char *text, int xalign0)
{
	GtkWidget *l = gtk_label_new(text);

	if (xalign0)
		gtk_label_set_xalign(GTK_LABEL(l), 0);
	gtk_label_set_line_wrap(GTK_LABEL(l), TRUE);
	return l;
}

static GtkWidget *hint(const char *text)
{
	GtkWidget *l = label(NULL, 1);
	char *m = g_markup_printf_escaped("<small>%s</small>", text);

	gtk_label_set_markup(GTK_LABEL(l), m);
	g_free(m);
	gtk_widget_set_opacity(l, 0.7);
	return l;
}

static GtkWidget *heading(GtkWidget *box, const char *text, int big)
{
	GtkWidget *l = label(NULL, 1);
	char *m = g_markup_printf_escaped(big ? "<span size='large' weight='bold'>%s</span>" : "<b>%s</b>", text);

	gtk_label_set_markup(GTK_LABEL(l), m);
	g_free(m);
	gtk_widget_set_margin_top(l, big ? 0 : 10);
	gtk_box_pack_start(GTK_BOX(box), l, FALSE, FALSE, 0);
	return l;
}

/* ---- display settings ---- */
static void show_value(size_t i)
{
	char b[48];

	if (PARAMS[i].choices)
		return;
	snprintf(b, sizeof(b), "%d %s", (int)gtk_range_get_value(GTK_RANGE(W.p[i])), PARAMS[i].unit);
	gtk_label_set_text(GTK_LABEL(W.pv[i]), b);
}

static void set_param(size_t i, int v)
{
	W.loading++;
	if (PARAMS[i].choices)
		gtk_combo_box_set_active(GTK_COMBO_BOX(W.p[i]), v);
	else
		gtk_range_set_value(GTK_RANGE(W.p[i]), v);
	show_value(i);
	W.loading--;
}

static void load_params_json(const cJSON *j)
{
	for (size_t i = 0; i < NP; i++) {
		const cJSON *o = cJSON_GetObjectItemCaseSensitive(j, PARAMS[i].name);
		double v = num(o, "v"), lo = num(o, "min"), hi = num(o, "max");

		if (isnan(v) || isnan(lo) || isnan(hi))
			continue;
		W.def[i] = num(o, "def");
		if (!PARAMS[i].choices) {
			W.loading++;
			gtk_range_set_range(GTK_RANGE(W.p[i]), lo, hi);
			W.loading--;
		}
		set_param(i, (int)v);
	}
}

static gboolean post_params(gpointer unused)
{
	cJSON *b = cJSON_CreateObject(), *j, *rej;
	char *body;
	int code;

	(void)unused;
	W.post_timer = 0;
	for (size_t i = 0; i < NP; i++)
		if (W.pending[i] >= 0) {
			cJSON_AddNumberToObject(b, PARAMS[i].name, W.pending[i]);
			W.pending[i] = -1;
		}
	body = cJSON_PrintUnformatted(b);
	j = req("POST", "/params", body, &code);
	if (!j)
		gtk_label_set_text(GTK_LABEL(W.msg), "보드에 연결할 수 없음");
	else if ((rej = cJSON_GetObjectItemCaseSensitive(j, "rejected")) && cJSON_GetArraySize(rej)) {
		GString *s = g_string_new("적용 안 됨: ");
		const cJSON *e;

		cJSON_ArrayForEach(e, rej) g_string_append_printf(s, "%s%s", e == rej->child ? "" : ", ",
								  cJSON_IsString(e) ? e->valuestring : "?");
		gtk_label_set_text(GTK_LABEL(W.msg), s->str);
		g_string_free(s, TRUE);
	} else
		gtk_label_set_text(GTK_LABEL(W.msg), "적용됨, 다음 실행에도 유지");
	if (j)
		load_params_json(cJSON_GetObjectItemCaseSensitive(j, "params"));
	cJSON_Delete(j);
	cJSON_free(body);
	cJSON_Delete(b);
	return G_SOURCE_REMOVE;
}

static void queue_param(size_t i, int v)
{
	W.pending[i] = v;
	if (W.post_timer)
		g_source_remove(W.post_timer);
	W.post_timer = g_timeout_add(120, post_params, NULL);
}

static void param_changed(GtkWidget *w, gpointer data)
{
	size_t i = GPOINTER_TO_SIZE(data);
	int v = PARAMS[i].choices ? gtk_combo_box_get_active(GTK_COMBO_BOX(w)) : (int)gtk_range_get_value(GTK_RANGE(w));

	show_value(i);
	if (!W.loading && v >= 0)
		queue_param(i, v);
}

static void preset(GtkWidget *b, gpointer data)
{
	long k = GPOINTER_TO_SIZE(data) - 1;	/* -1: 기본값 */

	(void)b;
	for (size_t i = 0; i < NP; i++) {
		int v = k < 0 ? (isnan(W.def[i]) ? -1 : (int)W.def[i]) : PRESETS[k].v[i];

		if (v >= 0) {
			set_param(i, v);
			queue_param(i, v);
		}
	}
}

/* ---- sensor ---- */
static void rate_hint(void)
{
	int a = gtk_combo_box_get_active(GTK_COMBO_BOX(W.s_rate));
	char b[128];
	int n = 0;

	if (a < 0 || a >= W.s_nm)
		return;
	n = snprintf(b, sizeof(b), "예상 scan %ld µs", lround(scan_for(W.s_margins[a])));
	if (!isnan(W.s_now))
		snprintf(b + n, sizeof(b) - n, ", 센서 모듈에 지금 margin %d", (int)W.s_now);
	gtk_label_set_text(GTK_LABEL(W.s_hint), b);
}

static void rate_changed(GtkWidget *w, gpointer unused)
{
	(void)w;
	(void)unused;
	rate_hint();
}

static void flush_changed(GtkWidget *w, gpointer unused)
{
	char b[32];

	(void)unused;
	snprintf(b, sizeof(b), "%dµs", (int)gtk_range_get_value(GTK_RANGE(w)));
	gtk_label_set_text(GTK_LABEL(W.s_flushv), b);
}

static int load_sensor(void)
{
	cJSON *j = req("GET", "/sensor", NULL, NULL);
	const cJSON *mg = cJSON_GetObjectItemCaseSensitive(j, "margin"), *fl = cJSON_GetObjectItemCaseSensitive(j, "flush_us");
	int m = (int)num(mg, "v"), sel = -1;
	char b[64];

	if (!j || isnan(num(mg, "v")) || isnan(num(fl, "v"))) {
		cJSON_Delete(j);
		return 0;
	}
	W.s_now = num(mg, "now");
	gtk_combo_box_text_remove_all(GTK_COMBO_BOX_TEXT(W.s_rate));
	W.s_nm = 0;
	for (size_t i = 0; i < NR; i++) {
		int mm = margin_for(RATES[i]);

		if (RATES[i])
			snprintf(b, sizeof(b), "%d fps (margin %d)", RATES[i], mm);
		else
			snprintf(b, sizeof(b), "약 2000 fps (setfile, margin 2)");
		gtk_combo_box_text_append_text(GTK_COMBO_BOX_TEXT(W.s_rate), b);
		if (mm == m && sel < 0)
			sel = W.s_nm;
		W.s_margins[W.s_nm++] = mm;
	}
	if (sel < 0) {
		snprintf(b, sizeof(b), "margin %d", m);
		gtk_combo_box_text_append_text(GTK_COMBO_BOX_TEXT(W.s_rate), b);
		sel = W.s_nm;
		W.s_margins[W.s_nm++] = m;
	}
	gtk_combo_box_set_active(GTK_COMBO_BOX(W.s_rate), sel);
	gtk_range_set_range(GTK_RANGE(W.s_flush), num(fl, "min"), num(fl, "max"));
	gtk_range_set_value(GTK_RANGE(W.s_flush), num(fl, "v"));
	rate_hint();
	cJSON_Delete(j);
	return 1;
}

static void apply_sensor(GtkWidget *b, gpointer unused)
{
	int a = gtk_combo_box_get_active(GTK_COMBO_BOX(W.s_rate)), code;
	char body[96];
	cJSON *j;

	(void)b;
	(void)unused;
	if (a < 0 || a >= W.s_nm)
		return;
	snprintf(body, sizeof(body), "{\"margin\": %d, \"flush_us\": %d}", W.s_margins[a],
		 (int)gtk_range_get_value(GTK_RANGE(W.s_flush)));
	j = req("POST", "/sensor", body, &code);
	if (code != 200) {
		gtk_label_set_text(GTK_LABEL(W.s_msg), code ? "적용 안 됨" : "보드에 연결할 수 없음");
		cJSON_Delete(j);
		return;
	}
	cJSON_Delete(j);
	gtk_label_set_text(GTK_LABEL(W.s_msg), "스트림 다시 시작 중…");
	W.apply_t = now_s();
	W.apply_down = 0;
}

/* ---- the classifier, stats, recorder ---- */
static void show_act(void)
{
	cJSON *j = req("GET", "/act", NULL, NULL);
	const cJSON *r, *recent = cJSON_GetObjectItemCaseSensitive(j, "recent");
	int on = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(j, "on"));
	const char *state = str(j, "state"), *top = str(j, "top");
	GString *s;
	char b[160], t[16];
	int n = 0;

	if (!j)
		return;
	if (on && state && !strcmp(state, "fall")) {
		char *m = g_markup_printf_escaped("<span size='x-large' weight='bold' foreground='#e03030'>%s</span>",
						  act_name(state));
		gtk_label_set_markup(GTK_LABEL(W.act_state), m);
		g_free(m);
	} else {
		char *m = g_markup_printf_escaped("<span size='x-large' weight='bold'>%s</span>",
						  on ? act_name(state) : "분류기 꺼짐");
		gtk_label_set_markup(GTK_LABEL(W.act_state), m);
		g_free(m);
	}
	if (on && top) {
		snprintf(b, sizeof(b), "지금 후보: %s %.2f", act_name(top), num(j, "p"));
		gtk_label_set_text(GTK_LABEL(W.act_top), b);
	} else
		gtk_label_set_text(GTK_LABEL(W.act_top), "");
	s = g_string_new(NULL);
	cJSON_ArrayForEach(r, recent) {
		if (n++ == 8)
			break;
		hhmmss(t, sizeof(t), num(r, "t_us") / 1e6);
		g_string_append_printf(s, "%s%s  %s → %s (%.2f)", s->len ? "\n" : "", t, event_name(str(r, "event")),
				       act_name(str(r, "state")), num(r, "p"));
	}
	gtk_label_set_text(GTK_LABEL(W.recent), s->len ? s->str : "–");
	g_string_free(s, TRUE);
	cJSON_Delete(j);
}

/* the stats text of the page; 0 when nothing answered */
static int show_stats(void)
{
	cJSON *j = req("GET", "/stats", NULL, NULL);
	const cJSON *link, *addrs, *a;
	const char *ns, *pll;
	GString *s;
	char crc[64] = "", f1[16], f2[16], f3[16];
	double t = now_s();

	if (!j) {
		gtk_label_set_text(GTK_LABEL(W.stats), "보드에 연결할 수 없음 (캡처가 다시 시작하는 중일 수 있음)");
		return 0;
	}
	if (!isnan(num(j, "crc"))) {
		if (!isnan(W.crc_n) && num(j, "crc") >= W.crc_n)
			snprintf(crc, sizeof(crc), ", CRC 오류 %ld/s", lround((num(j, "crc") - W.crc_n) / (t - W.crc_t)));
		W.crc_n = num(j, "crc");
		W.crc_t = t;
	}
	if ((addrs = cJSON_GetObjectItemCaseSensitive(j, "addrs"))) {
		s = g_string_new("웹 접속 주소: ");
		cJSON_ArrayForEach(a, addrs) {
			const cJSON *nm = cJSON_GetArrayItem(a, 0), *url = cJSON_GetArrayItem(a, 1);

			g_string_append_printf(s, "%s %s  ", cJSON_IsString(nm) ? nm->valuestring : "",
					       cJSON_IsString(url) ? url->valuestring : "연결 안 됨");
		}
		gtk_label_set_text(GTK_LABEL(W.addr), s->str);
		g_string_free(s, TRUE);
	}
	ns = str(j, "no_sensor");
	gtk_widget_set_visible(W.nosensor, ns != NULL);
	if (ns) {
		char *m = g_strdup_printf("센서 스트림 시작 실패: %s", ns);

		gtk_label_set_text(GTK_LABEL(W.stats), m);
		g_free(m);
	}
	if (isnan(num(j, "fps"))) {
		if (!ns)
			gtk_label_set_text(GTK_LABEL(W.stats), "통계 기다리는 중…");
		cJSON_Delete(j);
		return 1;
	}
	s = g_string_new(NULL);
	g_string_append_printf(s, "%g fps, 그리기 %g ms, 프레임당 이벤트 %g\n", num(j, "fps"), num(j, "draw_ms"),
			       num(j, "events"));
	g_string_append_printf(s, "버퍼 %g/s, 복사 %g ms, %g kB/버퍼\n", num(j, "buffers"), num(j, "ingest_ms"), num(j, "kb"));
	g_string_append_printf(s, "최신 이벤트보다 %g ms 늦음, 시간의 %g%%에 데이터, 어두운 프레임 %g장/2초\n",
			       num(j, "behind_ms"), num(j, "coverage"), num(j, "dips"));
	if (num(j, "scan_us") > 0)
		g_string_append_printf(s, "센서 scan %g µs (%ld fps)%s", num(j, "scan_us"), lround(1e6 / num(j, "scan_us")),
				       crc);
	if ((pll = str(j, "pll"))) {
		const char *r = strstr(pll, "rate=");

		g_string_append_printf(s, "\nPLL %.8s", pll);
		if (r && isdigit((unsigned char)r[5]))
			g_string_append_printf(s, " (공식으로 %d Mbps, 잰 값 아님)", atoi(r + 5));
	}
	if ((link = cJSON_GetObjectItemCaseSensitive(j, "link")))
		g_string_append_printf(s, "\n스트림 시작부터: 짧은 버퍼 %s, scan 누락 %s, frame_end 누락 %s, 타임스탬프 오류 %g",
				       fmt(f1, sizeof(f1), num(link, "short_bufs")), fmt(f2, sizeof(f2), num(link, "ts_missed")),
				       fmt(f3, sizeof(f3), num(link, "fe_missed")), num(link, "ts_bad"));
	gtk_label_set_text(GTK_LABEL(W.stats), s->str);
	g_string_free(s, TRUE);
	cJSON_Delete(j);
	return 1;
}

static void show_rec(const cJSON *j)
{
	const char *st = str(j, "state"), *last = str(j, "last");
	char b[160];

	if (!st)
		return;
	gtk_widget_set_sensitive(W.r_go, !strcmp(st, "idle"));
	if (!strcmp(st, "countdown"))
		snprintf(b, sizeof(b), "%d초 뒤 시작", (int)ceil(num(j, "left_s")));
	else if (!strcmp(st, "recording"))
		snprintf(b, sizeof(b), "녹화 중, %d초 남음", (int)ceil(num(j, "left_s")));
	else if (last)
		snprintf(b, sizeof(b), "마지막: %s", last);
	else
		b[0] = 0;
	gtk_label_set_text(GTK_LABEL(W.r_msg), b);
}

static void rec_go(GtkWidget *b, gpointer unused)
{
	int a = gtk_combo_box_get_active(GTK_COMBO_BOX(W.r_label)), s = gtk_combo_box_get_active(GTK_COMBO_BOX(W.r_s));
	char body[96], m[160];
	int code;
	cJSON *j;

	(void)b;
	(void)unused;
	if (a < 0 || s < 0)
		return;
	snprintf(body, sizeof(body), "{\"label\": \"%s\", \"s\": %d}", ACTS[a][0], REC_S[s]);
	j = req("POST", "/rec", body, &code);
	if (code == 200)
		show_rec(j);
	else {
		snprintf(m, sizeof(m), "시작 안 됨: %s", code == 400 ? "동작이나 길이가 잘못됨" : code == 409 ? "이미 녹화 중" :
			 code == 507 ? "보드 저장 공간 부족(300 MB 미만)" : code ? "오류" : "보드에 연결할 수 없음");
		gtk_label_set_text(GTK_LABEL(W.r_msg), m);
	}
	cJSON_Delete(j);
}

/* ---- sensor registers: values only ---- */
static void read_regs(GtkWidget *w, gpointer unused)
{
	const char *t = gtk_entry_get_text(GTK_ENTRY(W.g_a));
	char *e, path[64], b[64], tm[16];
	long a = strtol(t, &e, 16);
	int n = gtk_spin_button_get_value_as_int(GTK_SPIN_BUTTON(W.g_n)), code, i = 0;
	cJSON *j;
	const cJSON *v, *x;
	GString *s;

	(void)w;
	(void)unused;
	if (!*t || *e || a < 0 || n < 1 || n > 64 || a + n > 0x10000) {
		gtk_label_set_text(GTK_LABEL(W.g_msg), "주소는 0000~FFFF, 개수는 1~64");
		return;
	}
	snprintf(path, sizeof(path), "/reg?a=%04lX&n=%d", a, n);
	j = req("GET", path, NULL, &code);
	if (code != 200 || !(v = cJSON_GetObjectItemCaseSensitive(j, "v"))) {
		snprintf(b, sizeof(b), "읽지 못함: %s", str(j, "error") ? str(j, "error") : code ? "오류" : "연결 안 됨");
		gtk_label_set_text(GTK_LABEL(W.g_msg), b);
		cJSON_Delete(j);
		return;
	}
	s = g_string_new("주소   16진  10진  2진");
	cJSON_ArrayForEach(x, v) {
		int d = (int)x->valuedouble;

		g_string_append_printf(s, "\n%04lX   %02X    %4d  ", a + i++, d, d);
		for (int k = 7; k >= 0; k--)
			g_string_append_c(s, '0' + (d >> k & 1));
	}
	gtk_label_set_text(GTK_LABEL(W.g_out), s->str);
	g_string_free(s, TRUE);
	hhmmss(tm, sizeof(tm), time(NULL));
	snprintf(b, sizeof(b), "i2c-%d 0x10, %s", (int)num(j, "bus"), tm);
	gtk_label_set_text(GTK_LABEL(W.g_msg), b);
	cJSON_Delete(j);
}

/* ---- graphs ---- */
static int K(const char *k)
{
	for (int i = 0; k && i < W.nkeys; i++)
		if (!strcmp(W.keys[i], k))
			return i;
	return -1;
}

static double cell(int n, const char *k)
{
	int i = K(k);

	return i < 0 || n < 0 || n >= W.nrows ? NAN : W.rows[n][i];
}

/* a counter's rate over the last 10 s (5 rows) */
static double rate(int n, const char *k)
{
	double a = cell(n - 5, k), b = cell(n, k), dt = cell(n, "t") - cell(n - 5, "t");

	return isnan(a) || isnan(b) || b < a || dt > 12 || dt <= 0 ? NAN : (b - a) / dt;
}

static double chart_value(const struct chart *c, int n)
{
	double v;

	switch (c->kind) {
	case C_RATE:
		return rate(n, c->key);
	case C_MIPIMB:
		v = rate(n, "mipi_fe");
		return isnan(v) || !W.frame_bytes ? NAN : v * W.frame_bytes / 1e6;
	case C_DATAMB:
		return cell(n, "kb") * 1024 * cell(n, "buffers") / 1e6;
	case C_FILL:
		return !W.frame_bytes ? NAN : cell(n, "kb") * 1024 * 100 / W.frame_bytes;
	default:
		return cell(n, c->key);
	}
}

static int window_start(void)
{
	int w = WINS[gtk_combo_box_get_active(GTK_COMBO_BOX(W.h_win))], n = 0;
	double t1 = W.nrows ? W.rows[W.nrows - 1][0] : 0;

	while (n < W.nrows && W.rows[n][0] < t1 - w)
		n++;
	return n;
}

/* a counter's increase over rows n0.., across gaps and restarts (a drop = a new count); NAN: no pair */
static double growth(const char *k, int n0)
{
	double g = 0;
	int seen = 0;

	for (int n = n0 + 1; n < W.nrows; n++) {
		double a = cell(n - 1, k), b = cell(n, k);

		if (isnan(a) || isnan(b))
			continue;
		seen = 1;
		g += b >= a ? b - a : b;
	}
	return seen ? g : NAN;
}

static void draw_tiles(int n0)
{
	char b[64], f[32], lo[32], hi[32];

	for (size_t c = 0; c < NT; c++) {
		const struct tile *t = &TILES[c];
		double v = NAN, mn = INFINITY, mx = -INFINITY, sum = 0;
		int cnt = 0, count = t->kind == T_SUM || t->kind == T_ADD || t->kind == T_DIFF, bad;

		for (int n = n0; t->key && n < W.nrows; n++) {
			double x = cell(n, t->key);

			if (isnan(x))
				continue;
			cnt++;
			sum += x;
			mn = fmin(mn, x);
			mx = fmax(mx, x);
			if (t->kind == T_LAST)
				v = x;
		}
		if (t->kind == T_SUM)
			v = growth(t->key, n0);
		else if (t->kind == T_ADD)
			v = cnt ? sum : NAN;
		else if (t->kind == T_DIFF)
			v = growth("mipi_fe", n0) - growth("bufs", n0);
		else if (t->kind == T_MIN)
			v = cnt ? mn : NAN;
		bad = !isnan(v) && t->kind == T_SUM && v > 0;	/* errors only */
		snprintf(b, sizeof(b), "%s%s%s%s", bad ? "⚠ " : "", fmt(f, sizeof(f), v), isnan(v) ? "" : count ? "개" : " ",
			 isnan(v) || count ? "" : t->unit);
		if (bad) {
			char *m = g_markup_printf_escaped("<b><span foreground='#e03030'>%s</span></b>", b);

			gtk_label_set_markup(GTK_LABEL(W.tile_v[c]), m);
			g_free(m);
		} else {
			char *m = g_markup_printf_escaped("<b>%s</b>", b);

			gtk_label_set_markup(GTK_LABEL(W.tile_v[c]), m);
			g_free(m);
		}
		if (count)
			snprintf(b, sizeof(b), isnan(v) ? "기록 없음" : "최근 %s",
				 WIN_NAMES[gtk_combo_box_get_active(GTK_COMBO_BOX(W.h_win))]);
		else if (cnt)
			snprintf(b, sizeof(b), "범위 %s ~ %s", fmt(lo, sizeof(lo), mn), fmt(hi, sizeof(hi), mx));
		else
			b[0] = 0;
		gtk_label_set_text(GTK_LABEL(W.tile_r[c]), b);
	}
}

static gboolean draw_chart(GtkWidget *w, cairo_t *cr, gpointer data)
{
	const struct chart *c = &CHARTS[GPOINTER_TO_SIZE(data)];
	size_t ci = GPOINTER_TO_SIZE(data);
	int cw = gtk_widget_get_allocated_width(w), ch = gtk_widget_get_allocated_height(w), n0 = window_start(), pen = 0;
	double lo = INFINITY, hi = -INFINITY, last = NAN, nb = cell(W.nrows - 1, "vc0_n"), pad = 4, prev = NAN;
	double t1 = W.nrows ? W.rows[W.nrows - 1][0] : 0, t0 = t1 - WINS[gtk_combo_box_get_active(GTK_COMBO_BOX(W.h_win))];
	char b[96], f[32], g[32];

	for (int n = n0; n < W.nrows; n++) {
		double v = chart_value(c, n);

		if (!isnan(v)) {
			lo = fmin(lo, v);
			hi = fmax(hi, v);
			last = v;
		}
	}
	cairo_set_source_rgb(cr, 0x1b / 255.0, 0x1b / 255.0, 0x1b / 255.0);
	cairo_paint(cr);
	snprintf(b, sizeof(b), "%s%s%s", fmt(f, sizeof(f), last), *c->unit && !isnan(last) ? " " : "",
		 isnan(last) ? "" : c->unit);
	gtk_label_set_text(GTK_LABEL(W.chart_v[ci]), b);
	if (isinf(lo)) {
		gtk_label_set_text(GTK_LABEL(W.chart_r[ci]), "기록 없음");
		return FALSE;
	}
	if (c->bufaxis && !isnan(nb))
		snprintf(b, sizeof(b), "범위 %s ~ %s (전체 %d개 중 나머지 %d개)", fmt(f, sizeof(f), lo), fmt(g, sizeof(g), hi),
			 (int)nb, (int)nb - 1);
	else
		snprintf(b, sizeof(b), "범위 %s ~ %s", fmt(f, sizeof(f), lo), fmt(g, sizeof(g), hi));
	gtk_label_set_text(GTK_LABEL(W.chart_r[ci]), b);
	if (lo >= 0 && lo < (hi - lo) * 0.5)
		lo = 0;				/* near-zero series from a zero baseline */
	if (c->bufaxis && !isnan(nb)) {		/* the other buffers: 0 .. n - 1 */
		lo = 0;
		hi = nb - 1;
	}
	if (hi - lo < 1e-9)
		hi = lo + fmax(1, fabs(lo) * 0.1);
#define X(t) (((t) - t0) / (t1 - t0 ? t1 - t0 : 1) * cw)
#define Y(v) (ch - pad - ((v) - lo) / (hi - lo) * (ch - 2 * pad))
	cairo_set_source_rgb(cr, 0x2c / 255.0, 0x2c / 255.0, 0x2c / 255.0);
	cairo_set_line_width(cr, 1);
	cairo_move_to(cr, 0, round(Y(lo)) + 0.5);
	cairo_line_to(cr, cw, round(Y(lo)) + 0.5);
	cairo_move_to(cr, 0, round(Y(hi)) + 0.5);
	cairo_line_to(cr, cw, round(Y(hi)) + 0.5);
	cairo_stroke(cr);
	cairo_set_source_rgb(cr, 0x39 / 255.0, 0x87 / 255.0, 0xe5 / 255.0);
	cairo_set_line_width(cr, 2);
	cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
	cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
	for (int n = n0; n < W.nrows; n++) {
		double t = W.rows[n][0], v = chart_value(c, n);

		if (isnan(v) || (!isnan(prev) && t - prev > 6))
			pen = 0;			/* a gap over 6 s breaks the line */
		if (!isnan(v)) {
			if (pen)
				cairo_line_to(cr, X(t), Y(v));
			else
				cairo_move_to(cr, X(t), Y(v));
			pen = 1;
		}
		prev = t;
	}
	cairo_stroke(cr);
#undef X
#undef Y
	return FALSE;
}

static void redraw_graphs(void)
{
	char b[160];
	int n0 = window_start();

	draw_tiles(n0);
	for (size_t c = 0; c < NC; c++)
		gtk_widget_queue_draw(W.chart[c]);
	if (W.nrows)
		snprintf(b, sizeof(b), "왼쪽 끝 %s 전, 오른쪽 끝 지금 · 기록 %ld분치",
			 WIN_NAMES[gtk_combo_box_get_active(GTK_COMBO_BOX(W.h_win))],
			 lround((W.rows[W.nrows - 1][0] - W.rows[0][0]) / 60));
	else
		snprintf(b, sizeof(b), "기록 기다리는 중…");
	gtk_label_set_text(GTK_LABEL(W.h_msg), b);
}

static void fetch_history(void)
{
	char path[64];
	cJSON *j;
	const cJSON *keys, *rows, *r, *k;

	snprintf(path, sizeof(path), "/history?since=%.1f", W.nrows ? W.rows[W.nrows - 1][0] : 0);
	if (!(j = req("GET", path, NULL, NULL)))
		return;
	if ((keys = cJSON_GetObjectItemCaseSensitive(j, "keys")) && cJSON_GetArraySize(keys) <= MAXK) {
		W.nkeys = 0;
		cJSON_ArrayForEach(k, keys) snprintf(W.keys[W.nkeys++], sizeof(*W.keys), "%s",
						     cJSON_IsString(k) ? k->valuestring : "");
	}
	if (!isnan(num(j, "frame_bytes")))
		W.frame_bytes = num(j, "frame_bytes");
	rows = cJSON_GetObjectItemCaseSensitive(j, "rows");
	cJSON_ArrayForEach(r, rows) {
		int i = 0;

		if (W.nrows == MAXROWS)
			memmove(W.rows[0], W.rows[1], sizeof(W.rows[0]) * --W.nrows);
		cJSON_ArrayForEach(k, r) if (i < MAXK) W.rows[W.nrows][i++] = cJSON_IsNumber(k) ? k->valuedouble : NAN;
		while (i < MAXK)
			W.rows[W.nrows][i++] = NAN;
		W.nrows++;
	}
	cJSON_Delete(j);
	redraw_graphs();
}

static void win_changed(GtkWidget *w, gpointer unused)
{
	(void)w;
	(void)unused;
	redraw_graphs();
}

static void save_csv(GtkWidget *w, gpointer unused)
{
	char path[96], ts[32], b[160];
	time_t t = time(NULL);
	struct tm tm;
	FILE *f;

	(void)w;
	(void)unused;
	gmtime_r(&t, &tm);
	strftime(ts, sizeof(ts), "%Y%m%dT%H%M%S", &tm);
	snprintf(path, sizeof(path), "/var/tmp/dvs-stats-%s.csv", ts);
	if (!(f = fopen(path, "w"))) {
		gtk_label_set_text(GTK_LABEL(W.h_msg), "CSV를 쓸 수 없음");
		return;
	}
	for (int i = 0; i < W.nkeys; i++)
		fprintf(f, "%s%s", i ? "," : "", W.keys[i]);
	fputc('\n', f);
	for (int n = 0; n < W.nrows; n++) {
		time_t s = (time_t)W.rows[n][0];

		gmtime_r(&s, &tm);
		strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", &tm);
		fputs(ts, f);
		for (int i = 1; i < W.nkeys; i++)
			isnan(W.rows[n][i]) ? fputs(",", f) : fprintf(f, ",%g", W.rows[n][i]);
		fputc('\n', f);
	}
	fclose(f);
	snprintf(b, sizeof(b), "저장함: %s", path);
	gtk_label_set_text(GTK_LABEL(W.h_msg), b);
}

/* ---- polling: once a second, stats and graphs every other time ---- */
static int load_all(void)
{
	cJSON *p = req("GET", "/params", NULL, NULL);
	int ok = p != NULL;

	load_params_json(p);
	cJSON_Delete(p);
	return ok && load_sensor();
}

static gboolean poll(gpointer unused)
{
	cJSON *r;

	(void)unused;
	if (W.tick++ % 2 == 0) {
		if (!show_stats()) {
			W.up = 0;	/* a capture restart: everything is read again when it answers */
			W.apply_down = 1;
			goto pending;
		}
		if (!W.up) {		/* (back) after a restart: values may be new, the history starts over */
			W.up = load_all();
			W.nrows = 0;
			W.crc_n = NAN;
		}
		fetch_history();
	}
	show_act();
	if ((r = req("GET", "/rec", NULL, NULL))) {
		show_rec(r);
		cJSON_Delete(r);
	}
	if (W.up && !gtk_widget_get_visible(W.win)) {	/* hidden until the capture runs: `dvs --auto` without a DVS */
		gtk_widget_show_all(W.win);	/* (not the no-sensor line: no_show_all, show_stats sets it) */
	}
pending:
	if (W.apply_t) {
		if (W.up && W.apply_down) {
			gtk_label_set_text(GTK_LABEL(W.s_msg), "적용됨 (측정한 scan은 위 통계, 2초 뒤 갱신)");
			W.apply_t = 0;
		} else if (now_s() - W.apply_t > 30) {
			gtk_label_set_text(GTK_LABEL(W.s_msg), "30초 안에 돌아오지 않음: 보드를 확인하세요");
			W.apply_t = 0;
		}
	}
	return G_SOURCE_CONTINUE;
}

/* ---- the window ---- */
static GtkWidget *column(GtkWidget *parent, int width)
{
	GtkWidget *sw = gtk_scrolled_window_new(NULL, NULL), *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);

	gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(sw), GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC);
	gtk_widget_set_size_request(sw, width, -1);
	gtk_container_set_border_width(GTK_CONTAINER(box), 8);
	gtk_container_add(GTK_CONTAINER(sw), box);
	gtk_box_pack_start(GTK_BOX(parent), sw, TRUE, TRUE, 0);
	return box;
}

static GtkWidget *row(GtkWidget *box)
{
	GtkWidget *r = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);

	gtk_box_pack_start(GTK_BOX(box), r, FALSE, FALSE, 0);
	return r;
}

static void build(void)
{
	GtkWidget *top = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0), *L, *R, *r, *g, *b;

	W.win = gtk_window_new(GTK_WINDOW_TOPLEVEL);
	gtk_window_set_title(GTK_WINDOW(W.win), "DVS 설정");
	gtk_window_set_default_size(GTK_WINDOW(W.win), 940, 1000);
	gtk_window_move(GTK_WINDOW(W.win), 968, 0);	/* right of the 960 px wide DVS window at the left edge */
	gtk_container_add(GTK_CONTAINER(W.win), top);
	g_signal_connect(W.win, "destroy", G_CALLBACK(gtk_main_quit), NULL);
	L = column(top, 440);
	R = column(top, 480);

	heading(L, "DVS 화면 조정", 1);
	W.act_state = label("분류기 기다리는 중…", 1);
	gtk_box_pack_start(GTK_BOX(L), W.act_state, FALSE, FALSE, 0);
	W.act_top = label("", 1);
	gtk_box_pack_start(GTK_BOX(L), W.act_top, FALSE, FALSE, 0);
	W.nosensor = label(NULL, 1);
	gtk_label_set_markup(GTK_LABEL(W.nosensor), "<span foreground='#e03030' weight='bold'>DVS 센서가 연결되어 있지 "
				 "않습니다. 보드를 끄고 CN4에 센서를 꽂은 뒤 다시 켜 주세요.</span>");
	gtk_widget_set_no_show_all(W.nosensor, TRUE);
	gtk_box_pack_start(GTK_BOX(L), W.nosensor, FALSE, FALSE, 0);
	W.addr = hint("");
	gtk_box_pack_start(GTK_BOX(L), W.addr, FALSE, FALSE, 0);
	W.stats = label("통계 기다리는 중…", 1);
	gtk_box_pack_start(GTK_BOX(L), W.stats, FALSE, FALSE, 0);
	heading(L, "최근 판정 변화", 0);
	W.recent = label("–", 1);
	gtk_box_pack_start(GTK_BOX(L), W.recent, FALSE, FALSE, 0);

	heading(L, "표시 (바로 적용)", 0);
	r = row(L);
	b = gtk_button_new_with_label("기본값");
	g_signal_connect(b, "clicked", G_CALLBACK(preset), GSIZE_TO_POINTER(0));
	gtk_box_pack_start(GTK_BOX(r), b, FALSE, FALSE, 0);
	for (size_t k = 0; k < sizeof(PRESETS) / sizeof(*PRESETS); k++) {
		b = gtk_button_new_with_label(PRESETS[k].label);
		g_signal_connect(b, "clicked", G_CALLBACK(preset), GSIZE_TO_POINTER(k + 1));
		gtk_box_pack_start(GTK_BOX(r), b, FALSE, FALSE, 0);
	}
	g = gtk_grid_new();
	gtk_grid_set_column_spacing(GTK_GRID(g), 8);
	gtk_box_pack_start(GTK_BOX(L), g, FALSE, FALSE, 0);
	for (size_t i = 0, y = 0; i < NP; i++) {
		gtk_grid_attach(GTK_GRID(g), label(PARAMS[i].label, 1), 0, y, 1, 1);
		if (PARAMS[i].choices) {
			W.p[i] = gtk_combo_box_text_new();
			for (const char *const *c = PARAMS[i].choices; *c; c++)
				gtk_combo_box_text_append_text(GTK_COMBO_BOX_TEXT(W.p[i]), *c);
			gtk_grid_attach(GTK_GRID(g), W.p[i], 1, y, 2, 1);
		} else {
			W.p[i] = gtk_scale_new_with_range(GTK_ORIENTATION_HORIZONTAL, 0, 1000, 1);
			gtk_scale_set_draw_value(GTK_SCALE(W.p[i]), FALSE);
			W.pv[i] = label("", 1);
			gtk_label_set_width_chars(GTK_LABEL(W.pv[i]), 7);
			gtk_grid_attach(GTK_GRID(g), W.p[i], 1, y, 1, 1);
			gtk_grid_attach(GTK_GRID(g), W.pv[i], 2, y, 1, 1);
		}
		gtk_widget_set_hexpand(W.p[i], TRUE);
		W.def[i] = NAN;
		W.pending[i] = -1;
		g_signal_connect(W.p[i], PARAMS[i].choices ? "changed" : "value-changed", G_CALLBACK(param_changed),
				 GSIZE_TO_POINTER(i));
		y++;
		if (PARAMS[i].hint)
			gtk_grid_attach(GTK_GRID(g), hint(PARAMS[i].hint), 0, y++, 3, 1);
	}
	W.msg = hint("");
	gtk_box_pack_start(GTK_BOX(L), W.msg, FALSE, FALSE, 0);

	heading(L, "센서 (적용하면 스트림을 다시 시작, 약 5초)", 0);
	r = row(L);
	gtk_box_pack_start(GTK_BOX(r), label("scan 속도", 1), FALSE, FALSE, 0);
	W.s_rate = gtk_combo_box_text_new();
	g_signal_connect(W.s_rate, "changed", G_CALLBACK(rate_changed), NULL);
	gtk_box_pack_start(GTK_BOX(r), W.s_rate, TRUE, TRUE, 0);
	W.s_hint = hint("");
	gtk_box_pack_start(GTK_BOX(L), W.s_hint, FALSE, FALSE, 0);
	r = row(L);
	gtk_box_pack_start(GTK_BOX(r), label("MIPI flush", 1), FALSE, FALSE, 0);
	W.s_flush = gtk_scale_new_with_range(GTK_ORIENTATION_HORIZONTAL, 1, 1023, 1);
	gtk_scale_set_draw_value(GTK_SCALE(W.s_flush), FALSE);
	W.s_flushv = label("", 1);
	gtk_label_set_width_chars(GTK_LABEL(W.s_flushv), 7);
	g_signal_connect(W.s_flush, "value-changed", G_CALLBACK(flush_changed), NULL);
	gtk_box_pack_start(GTK_BOX(r), W.s_flush, TRUE, TRUE, 0);
	gtk_box_pack_start(GTK_BOX(r), W.s_flushv, FALSE, FALSE, 0);
	gtk_box_pack_start(GTK_BOX(L), hint("센서 FIFO를 이 시간(µs)마다 내보냄: 짧을수록 버퍼가 잦고 지연이 적음, 기본 50"),
			   FALSE, FALSE, 0);
	b = gtk_button_new_with_label("센서 설정 적용");
	g_signal_connect(b, "clicked", G_CALLBACK(apply_sensor), NULL);
	gtk_box_pack_start(GTK_BOX(L), b, FALSE, FALSE, 0);
	W.s_msg = hint("");
	gtk_box_pack_start(GTK_BOX(L), W.s_msg, FALSE, FALSE, 0);

	heading(R, "통계 그래프", 1);
	r = row(R);
	gtk_box_pack_start(GTK_BOX(r), label("기간", 1), FALSE, FALSE, 0);
	W.h_win = gtk_combo_box_text_new();
	for (size_t i = 0; i < sizeof(WINS) / sizeof(*WINS); i++)
		gtk_combo_box_text_append_text(GTK_COMBO_BOX_TEXT(W.h_win), WIN_NAMES[i]);
	gtk_combo_box_set_active(GTK_COMBO_BOX(W.h_win), 1);
	g_signal_connect(W.h_win, "changed", G_CALLBACK(win_changed), NULL);
	gtk_box_pack_start(GTK_BOX(r), W.h_win, FALSE, FALSE, 0);
	b = gtk_button_new_with_label("CSV 저장");
	g_signal_connect(b, "clicked", G_CALLBACK(save_csv), NULL);
	gtk_box_pack_start(GTK_BOX(r), b, FALSE, FALSE, 0);
	W.h_msg = hint("기록 기다리는 중…");
	gtk_box_pack_start(GTK_BOX(R), W.h_msg, FALSE, FALSE, 0);
	g = gtk_grid_new();
	gtk_grid_set_column_spacing(GTK_GRID(g), 10);
	gtk_grid_set_row_spacing(GTK_GRID(g), 4);
	gtk_grid_set_column_homogeneous(GTK_GRID(g), TRUE);
	gtk_box_pack_start(GTK_BOX(R), g, FALSE, FALSE, 0);
	for (size_t c = 0; c < NT; c++) {
		GtkWidget *t = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);

		gtk_box_pack_start(GTK_BOX(t), hint(TILES[c].name), FALSE, FALSE, 0);
		gtk_box_pack_start(GTK_BOX(t), W.tile_v[c] = label("–", 1), FALSE, FALSE, 0);
		gtk_box_pack_start(GTK_BOX(t), W.tile_r[c] = hint(""), FALSE, FALSE, 0);
		gtk_grid_attach(GTK_GRID(g), t, c % 3, c / 3, 1, 1);
	}
	for (size_t c = 0; c < NC; c++) {
		if (!c || strcmp(CHARTS[c].grp, CHARTS[c - 1].grp))
			heading(R, CHARTS[c].grp, 0);
		r = row(R);
		gtk_box_pack_start(GTK_BOX(r), label(CHARTS[c].name, 1), TRUE, TRUE, 0);
		gtk_box_pack_start(GTK_BOX(r), W.chart_v[c] = label("–", 1), FALSE, FALSE, 0);
		W.chart[c] = gtk_drawing_area_new();
		gtk_widget_set_size_request(W.chart[c], -1, 48);
		g_signal_connect(W.chart[c], "draw", G_CALLBACK(draw_chart), GSIZE_TO_POINTER(c));
		gtk_box_pack_start(GTK_BOX(R), W.chart[c], FALSE, FALSE, 0);
		gtk_box_pack_start(GTK_BOX(R), W.chart_r[c] = hint(""), FALSE, FALSE, 0);
	}

	heading(R, "행동 녹화 (학습용, 3초 뒤 시작)", 0);
	r = row(R);
	gtk_box_pack_start(GTK_BOX(r), label("동작", 1), FALSE, FALSE, 0);
	W.r_label = gtk_combo_box_text_new();
	for (size_t i = 0; i < NA; i++) {
		char *t = g_strdup_printf("%s (%s)", ACTS[i][1], ACTS[i][0]);

		gtk_combo_box_text_append_text(GTK_COMBO_BOX_TEXT(W.r_label), t);
		g_free(t);
	}
	gtk_combo_box_set_active(GTK_COMBO_BOX(W.r_label), 0);
	gtk_box_pack_start(GTK_BOX(r), W.r_label, TRUE, TRUE, 0);
	gtk_box_pack_start(GTK_BOX(r), label("길이", 1), FALSE, FALSE, 0);
	W.r_s = gtk_combo_box_text_new();
	for (size_t i = 0; i < sizeof(REC_S) / sizeof(*REC_S); i++) {
		char t[16];

		snprintf(t, sizeof(t), "%d초", REC_S[i]);
		gtk_combo_box_text_append_text(GTK_COMBO_BOX_TEXT(W.r_s), t);
	}
	gtk_combo_box_set_active(GTK_COMBO_BOX(W.r_s), 1);
	gtk_box_pack_start(GTK_BOX(r), W.r_s, FALSE, FALSE, 0);
	W.r_go = gtk_button_new_with_label("녹화");
	g_signal_connect(W.r_go, "clicked", G_CALLBACK(rec_go), NULL);
	gtk_box_pack_start(GTK_BOX(r), W.r_go, FALSE, FALSE, 0);
	W.r_msg = hint("");
	gtk_box_pack_start(GTK_BOX(R), W.r_msg, FALSE, FALSE, 0);

	heading(R, "센서 레지스터 읽기 (읽기만, 스트림은 그대로)", 0);
	r = row(R);
	gtk_box_pack_start(GTK_BOX(r), label("주소 (16진)", 1), FALSE, FALSE, 0);
	W.g_a = gtk_entry_new();
	gtk_entry_set_max_length(GTK_ENTRY(W.g_a), 4);
	gtk_entry_set_width_chars(GTK_ENTRY(W.g_a), 6);
	g_signal_connect(W.g_a, "activate", G_CALLBACK(read_regs), NULL);
	gtk_box_pack_start(GTK_BOX(r), W.g_a, FALSE, FALSE, 0);
	gtk_box_pack_start(GTK_BOX(r), label("개수", 1), FALSE, FALSE, 0);
	W.g_n = gtk_spin_button_new_with_range(1, 64, 1);
	gtk_spin_button_set_value(GTK_SPIN_BUTTON(W.g_n), 1);
	gtk_box_pack_start(GTK_BOX(r), W.g_n, FALSE, FALSE, 0);
	b = gtk_button_new_with_label("읽기");
	g_signal_connect(b, "clicked", G_CALLBACK(read_regs), NULL);
	gtk_box_pack_start(GTK_BOX(r), b, FALSE, FALSE, 0);
	W.g_msg = hint("");
	gtk_box_pack_start(GTK_BOX(R), W.g_msg, FALSE, FALSE, 0);
	W.g_out = label("", 1);
	{
		PangoAttrList *al = pango_attr_list_new();

		pango_attr_list_insert(al, pango_attr_family_new("monospace"));
		gtk_label_set_attributes(GTK_LABEL(W.g_out), al);
		pango_attr_list_unref(al);
	}
	gtk_label_set_selectable(GTK_LABEL(W.g_out), TRUE);
	gtk_box_pack_start(GTK_BOX(R), W.g_out, FALSE, FALSE, 0);
	W.s_now = W.crc_n = NAN;
}

/* ---- selftest against dvspanel-test.sh's stub ---- */
static int selftest_rc = 1;

static gboolean selftest_end(gpointer unused)
{
	(void)unused;
	gtk_main_quit();
	return G_SOURCE_REMOVE;
}

static gboolean selftest_step(gpointer unused)
{
	const char *st, *tile, *recent;

	(void)unused;
	poll(NULL);		/* stats, params, sensor, history, act, rec */
	st = gtk_label_get_text(GTK_LABEL(W.stats));
	tile = gtk_label_get_text(GTK_LABEL(W.tile_v[2]));	/* CRC 오류 over the stub's rows */
	recent = gtk_label_get_text(GTK_LABEL(W.recent));
	if (!W.up || !gtk_widget_get_visible(W.win))
		fprintf(stderr, "selftest: no answer on port %d\n", port);
	else if (gtk_range_get_value(GTK_RANGE(W.p[2])) != 0 || gtk_combo_box_get_active(GTK_COMBO_BOX(W.p[4])) != 1 ||
		 strcmp(gtk_label_get_text(GTK_LABEL(W.pv[0])), "60 fps"))
		fprintf(stderr, "selftest: display settings not loaded\n");
	else if (gtk_combo_box_get_active(GTK_COMBO_BOX(W.s_rate)) != 2 ||
		 !strstr(gtk_label_get_text(GTK_LABEL(W.s_hint)), "예상 scan 981 µs, 센서 모듈에 지금 margin 17"))
		fprintf(stderr, "selftest: sensor: %s\n", gtk_label_get_text(GTK_LABEL(W.s_hint)));
	else if (!strstr(st, "57 fps, 그리기 2.2 ms") || !strstr(st, "PLL 00 8b b0 (공식으로 2244 Mbps") ||
		 !strstr(st, "frame_end 누락 3, 타임스탬프 오류 4"))
		fprintf(stderr, "selftest: stats: %s\n", st);
	else if (!strstr(tile, "⚠ 10개") || !strstr(gtk_label_get_text(GTK_LABEL(W.addr)), "무선 Wi-Fi 10.0.0.2:8099"))
		fprintf(stderr, "selftest: tile %s / addr\n", tile);
	else if (!strstr(recent, "움직임 → 앉음 (0.55)") || !strstr(gtk_label_get_text(GTK_LABEL(W.act_state)), "서 있음"))
		fprintf(stderr, "selftest: act: %s\n", recent);
	else {
		gtk_range_set_value(GTK_RANGE(W.p[2]), 100);	/* fade, as a drag: POST after the 120 ms batching */
		gtk_combo_box_set_active(GTK_COMBO_BOX(W.r_label), 3);
		rec_go(NULL, NULL);				/* POST /rec sit_down 6 s */
		gtk_entry_set_text(GTK_ENTRY(W.g_a), "321d");
		gtk_spin_button_set_value(GTK_SPIN_BUTTON(W.g_n), 2);
		read_regs(NULL, NULL);
		if (!strstr(gtk_label_get_text(GTK_LABEL(W.g_out)), "321E   11      17  00010001"))
			fprintf(stderr, "selftest: reg: %s\n", gtk_label_get_text(GTK_LABEL(W.g_out)));
		else
			selftest_rc = 0;
	}
	g_timeout_add(400, selftest_end, NULL);
	return G_SOURCE_REMOVE;
}

static int fmt_checks(void)
{
	char b[32];

	if (strcmp(fmt(b, sizeof(b), NAN), "–") || strcmp(fmt(b, sizeof(b), 1234567), "1,234,567") ||
	    strcmp(fmt(b, sizeof(b), 100), "100") || strcmp(fmt(b, sizeof(b), 2.25), "2.25") ||
	    strcmp(fmt(b, sizeof(b), -1500), "-1,500") || margin_for(1000) != 17 || margin_for(0) != 0 ||
	    lround(scan_for(0)) != 495)
		return fprintf(stderr, "selftest: fmt / scan (%s)\n", b), 1;
	return 0;
}

int main(int argc, char **argv)
{
	int test = argc > 1 && !strcmp(argv[1], "--selftest");

	if (argc > 1 + test)
		port = atoi(argv[1 + test]);
	if (test && fmt_checks())
		return 1;
	gtk_init(&argc, &argv);
	build();
	if (test) {
		g_idle_add(selftest_step, NULL);
		gtk_main();
		if (!selftest_rc)
			puts("selftest ok");
		return selftest_rc;
	}
	poll(NULL);
	g_timeout_add(1000, poll, NULL);
	gtk_main();
	return 0;
}
