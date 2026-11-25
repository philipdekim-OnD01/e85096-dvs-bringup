// SPDX-License-Identifier: GPL-2.0
/*
 * archbench: CPU and memory micro-benchmarks behind the E850 (Cortex-A55) / Jetson Orin Nano (Cortex-A78AE) DVS
 * comparison (linux510 docs 11.115). One static binary, run unchanged on both, no root:
 *
 *   archbench CPU            the single-core tests on CPU (pinned), each the best of 3 runs (the boards keep their
 *                            live load meanwhile: the best run has the least interference)
 *   archbench -m CPU,CPU,..  read bandwidth with one thread on each CPU listed, all at once
 *   archbench CPU isa        only the clock, ilp, fma and isa tests
 *   archbench -m CPUS SECS   the same, reading for SECS seconds (a memory load beside another test): average GB/s
 *
 * clock  a chain of dependent adds: one a cycle on both cores, so 1 / (ns an add) is the clock the core ran at
 * ilp    the same 32 adds as 1, 2, 4, 8 independent chains: adds a cycle (A55: 2 integer ALUs, A78: 4)
 * branch a branch on random bits against the same branch always taken: the extra time of a mispredict (half miss)
 * lat    pointer chasing over a random cycle of 64-byte lines in 4 KB .. 256 MB: load-to-use latency of each level
 * mlp    K independent chases at once (K = 1, 2, 4, 8): how many misses the core overlaps; at 192 KB (the DVS
 *        filter's two bitmaps, ~175 KB, dvsview.c filter()) and 64 MB (DRAM)
 * fma    FP32 fmla.4s: its latency (1 chain) and throughput (8 chains): the onnxruntime convolutions' arithmetic
 * isa    what each Arm version added, on this core: v8.1 LSE atomic add (ldadd) against the v8.0 exclusive pair
 *        (ldxr/stxr), v8.2 FP16 fmla.8h and v8.2 dot product sdot (int8, SIGILL caught: a core may lack it)
 * bw     one core: reading (sum), memset, memcpy
 * Pages: 4 KB (MADV_NOHUGEPAGE), the same on both, so the TLB reach is each core's own.
 *
 * Build (Mac Docker, Debian 12 arm64): gcc -O2 -static -pthread -o archbench archbench.c
 * Self-check: the clock test's ns per add times the cpufreq clock (scaling_cur_freq) is printed: ~1.0 if one add a
 * cycle holds; the chase checks it visited every line of the cycle.
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <sched.h>
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>

static double now(void)
{
	struct timespec t;

	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec + t.tv_nsec * 1e-9;
}

static void pin(int cpu)
{
	cpu_set_t s;

	CPU_ZERO(&s);
	CPU_SET(cpu, &s);
	if (sched_setaffinity(0, sizeof(s), &s))
		perror("sched_setaffinity"), exit(1);
}

static long khz(int cpu)
{
	char p[96];
	long k = 0;
	FILE *f;

	snprintf(p, sizeof(p), "/sys/devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq", cpu);
	if ((f = fopen(p, "r"))) {
		if (fscanf(f, "%ld", &k) != 1)
			k = 0;
		fclose(f);
	}
	return k;
}

static void *big(size_t n)
{
	void *p = mmap(NULL, n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);

	if (p == MAP_FAILED)
		perror("mmap"), exit(1);
	madvise(p, n, MADV_NOHUGEPAGE);
	memset(p, 1, n);
	return p;
}

static uint64_t rng = 88172645463325252ull;
static uint64_t xs(void)
{
	rng ^= rng << 13;
	rng ^= rng >> 7;
	rng ^= rng << 17;
	return rng;
}

static volatile uint64_t sink;

/* ---- clock, ilp: 32 adds an iteration ---- */
#define X2(s) s s
#define X4(s) X2(s) X2(s)
#define X8(s) X4(s) X4(s)
#define X16(s) X8(s) X8(s)
#define X32(s) X16(s) X16(s)

static double alu(int chains)
{
	uint64_t a = 1, b = 2, c = 3, d = 4, e = 5, f = 6, g = 7, h = 8, n = 1 << 22;
	double t = now();

	switch (chains) {
	case 1:
		asm volatile("1:\n" X32("add %0, %0, #1\n") "subs %1, %1, #1\nb.ne 1b\n" : "+r"(a), "+r"(n));
		break;
	case 2:
		asm volatile("1:\n" X16("add %0, %0, #1\nadd %1, %1, #1\n") "subs %2, %2, #1\nb.ne 1b\n"
			     : "+r"(a), "+r"(b), "+r"(n));
		break;
	case 4:
		asm volatile("1:\n" X8("add %0, %0, #1\nadd %1, %1, #1\nadd %2, %2, #1\nadd %3, %3, #1\n")
			     "subs %4, %4, #1\nb.ne 1b\n" : "+r"(a), "+r"(b), "+r"(c), "+r"(d), "+r"(n));
		break;
	case 8:
		asm volatile("1:\n" X4("add %0, %0, #1\nadd %1, %1, #1\nadd %2, %2, #1\nadd %3, %3, #1\n"
				       "add %4, %4, #1\nadd %5, %5, #1\nadd %6, %6, #1\nadd %7, %7, #1\n")
			     "subs %8, %8, #1\nb.ne 1b\n"
			     : "+r"(a), "+r"(b), "+r"(c), "+r"(d), "+r"(e), "+r"(f), "+r"(g), "+r"(h), "+r"(n));
		break;
	}
	t = now() - t;
	sink = a + b + c + d + e + f + g + h;
	return t * 1e9 / (32.0 * (1 << 22));		/* ns an add */
}

/* ---- fma: 32 FP32 fmla.4s (8 flops each) an iteration, as 1 chain (latency) or 8 (throughput) ---- */
static double fma_ns(int chains)
{
	uint64_t n = 1 << 20;
	double t = now();

	if (chains == 1)
		asm volatile("movi v0.4s, #0\nmovi v1.4s, #0\nmovi v2.4s, #0\n1:\n" X32("fmla v0.4s, v1.4s, v2.4s\n")
			     "subs %0, %0, #1\nb.ne 1b\n" : "+r"(n) : : "v0", "v1", "v2", "cc");
	else
		asm volatile("movi v1.4s, #0\nmovi v2.4s, #0\n1:\n"
			     X4("fmla v0.4s, v1.4s, v2.4s\nfmla v3.4s, v1.4s, v2.4s\nfmla v4.4s, v1.4s, v2.4s\n"
				"fmla v5.4s, v1.4s, v2.4s\nfmla v6.4s, v1.4s, v2.4s\nfmla v7.4s, v1.4s, v2.4s\n"
				"fmla v16.4s, v1.4s, v2.4s\nfmla v17.4s, v1.4s, v2.4s\n")
			     "subs %0, %0, #1\nb.ne 1b\n" : "+r"(n) : : "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
			     "v16", "v17", "cc");
	t = now() - t;
	return t * 1e9 / (32.0 * (1 << 20));		/* ns an fmla */
}

static double t_fma(double *a) { return fma_ns((int)*a); }

/* ---- isa: per-version instructions; ns an instruction ---- */
static sigjmp_buf ill_jb;
static void on_ill(int s) { (void)s; siglongjmp(ill_jb, 1); }

static double isa_ns(int which)
{
	static volatile uint64_t word;
	uint64_t n = 1 << 20, k = 1, w;
	double t = now();

	switch (which) {
	case 0:		/* v8.1 LSE: dependent atomic adds on one word */
		asm volatile(".arch_extension lse\n1:\n" X32("ldadd %1, %1, [%2]\n") "subs %0, %0, #1\nb.ne 1b\n"
			     : "+r"(n), "+r"(k) : "r"(&word) : "memory", "cc");
		break;
	case 1:		/* v8.0: the same with an exclusive pair */
		asm volatile("1:\n" X32("2: ldxr %1, [%3]\nadd %1, %1, #1\nstxr %w2, %1, [%3]\ncbnz %w2, 2b\n")
			     "subs %0, %0, #1\nb.ne 1b\n" : "+r"(n), "+r"(k), "=&r"(w) : "r"(&word) : "memory", "cc");
		break;
	case 2:		/* v8.2 FP16: 8 independent fmla.8h (16 flops each) */
		asm volatile(".arch_extension fp16\nmovi v1.8h, #0\nmovi v2.8h, #0\n1:\n"
			     X4("fmla v0.8h, v1.8h, v2.8h\nfmla v3.8h, v1.8h, v2.8h\nfmla v4.8h, v1.8h, v2.8h\n"
				"fmla v5.8h, v1.8h, v2.8h\nfmla v6.8h, v1.8h, v2.8h\nfmla v7.8h, v1.8h, v2.8h\n"
				"fmla v16.8h, v1.8h, v2.8h\nfmla v17.8h, v1.8h, v2.8h\n")
			     "subs %0, %0, #1\nb.ne 1b\n" : "+r"(n) : : "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
			     "v16", "v17", "cc");
		break;
	case 3:		/* v8.2 DotProd: 8 independent sdot (16 int8 multiply-adds each) */
		asm volatile(".arch armv8.2-a+dotprod\nmovi v1.16b, #1\nmovi v2.16b, #1\n1:\n"
			     X4("sdot v0.4s, v1.16b, v2.16b\nsdot v3.4s, v1.16b, v2.16b\nsdot v4.4s, v1.16b, v2.16b\n"
				"sdot v5.4s, v1.16b, v2.16b\nsdot v6.4s, v1.16b, v2.16b\nsdot v7.4s, v1.16b, v2.16b\n"
				"sdot v16.4s, v1.16b, v2.16b\nsdot v17.4s, v1.16b, v2.16b\n")
			     "subs %0, %0, #1\nb.ne 1b\n" : "+r"(n) : : "v0", "v1", "v2", "v3", "v4", "v5", "v6", "v7",
			     "v16", "v17", "cc");
		break;
	}
	t = now() - t;
	sink = k + word;
	return t * 1e9 / (32.0 * (1 << 20));
}

static double t_isa(double *a) { return isa_ns((int)*a); }

/* ---- branch: ns an element, bit 0 of each byte decides ---- */
static double branch(const uint8_t *p, size_t n, int reps)
{
	uint64_t acc = 0;
	double t = now();

	for (int r = 0; r < reps; r++) {
		const uint8_t *q = p;
		uint64_t k = n, w;

		asm volatile("1:\nldrb %w2, [%1], #1\ntbz %w2, #0, 2f\nadd %0, %0, #3\n2:\nsubs %3, %3, #1\nb.ne 1b\n"
			     : "+r"(acc), "+r"(q), "=&r"(w), "+r"(k) : : "memory", "cc");
	}
	t = now() - t;
	sink = acc;
	return t * 1e9 / ((double)n * reps);
}

/* ---- lat, mlp: a random cycle through the lines of size bytes ---- */
static uint64_t **chain(char *buf, size_t size)
{
	size_t n = size / 64;
	uint32_t *o = malloc(n * sizeof(*o));

	for (size_t i = 0; i < n; i++)
		o[i] = i;
	for (size_t i = n - 1; i > 0; i--) {
		size_t j = xs() % (i + 1);
		uint32_t x = o[i];

		o[i] = o[j];
		o[j] = x;
	}
	for (size_t i = 0; i < n; i++)
		*(char **)(buf + (size_t)o[i] * 64) = buf + (size_t)o[(i + 1) % n] * 64;
	free(o);
	/* self-check: the walk comes back to its start after exactly n steps */
	char *p = buf;
	size_t k = 0;

	do
		p = *(char **)p, k++;
	while (p != buf && k <= n);
	if (k != n)
		fprintf(stderr, "chain of %zu lines: cycle of %zu\n", n, k), exit(1);
	return (uint64_t **)buf;
}

/* ns a load with K chases at once: starts spread along the cycle */
static double chase(char *buf, size_t size, int K, double secs)
{
	char *p[8];
	size_t n = size / 64, loads = 0;
	double t;

	p[0] = buf;
	for (int k = 1; k < K; k++) {		/* n / K steps apart */
		p[k] = p[k - 1];
		for (size_t s = 0; s < n / K; s++)
			p[k] = *(char **)p[k];
	}
	t = now();
	do {
		if (K == 1)		/* the latency: nothing but the chase in the loop (the in-order A55 feels any) */
			for (int i = 0; i < (1 << 16); i += 4) {
				p[0] = *(char **)p[0];
				p[0] = *(char **)p[0];
				p[0] = *(char **)p[0];
				p[0] = *(char **)p[0];
			}
		else
		for (int i = 0; i < (1 << 16); i++) {
			switch (K) {
			case 8: p[7] = *(char **)p[7]; p[6] = *(char **)p[6]; p[5] = *(char **)p[5];
				p[4] = *(char **)p[4]; /* fall through */
			case 4: p[3] = *(char **)p[3]; p[2] = *(char **)p[2]; /* fall through */
			case 2: p[1] = *(char **)p[1]; /* fall through */
			case 1: p[0] = *(char **)p[0];
			}
		}
		loads += (size_t)K << 16;
	} while (now() - t < secs);
	t = now() - t;
	for (int k = 0; k < K; k++)
		sink += (uintptr_t)p[k];
	return t * 1e9 / loads;
}

/* ---- bw ---- */
static double rd(const uint64_t *p, size_t n)
{
	uint64_t a = 0, b = 0, c = 0, d = 0;
	double t = now();

	for (size_t i = 0; i < n / 8; i += 4)
		a += p[i], b += p[i + 1], c += p[i + 2], d += p[i + 3];
	t = now() - t;
	sink = a + b + c + d;
	return n / t / 1e9;
}

static double best3(double (*f)(double *), double *arg, int lower)
{
	double b = f(arg);

	for (int i = 0; i < 2; i++) {
		double v = f(arg);

		if (lower ? v < b : v > b)
			b = v;
	}
	return b;
}

struct mt { int cpu; double gbs; pthread_barrier_t *bar; };
static double hog_secs;
static int isa_only;

static void *mt_read(void *arg)
{
	struct mt *m = arg;
	size_t n = 64 << 20;
	uint64_t *p;

	pin(m->cpu);
	p = big(n);
	rd(p, n);
	pthread_barrier_wait(m->bar);
	m->gbs = 0;
	if (hog_secs) {
		double t = now();
		int k = 0;

		while (now() - t < hog_secs)
			rd(p, n), k++;
		m->gbs = (double)k * n / (now() - t) / 1e9;
	} else
		for (int i = 0; i < 3; i++) {
			double g = rd(p, n);

			if (g > m->gbs)
				m->gbs = g;
		}
	munmap(p, n);
	return NULL;
}

static int multi(char *list)
{
	struct mt m[16];
	pthread_t th[16];
	pthread_barrier_t bar;
	int n = 0;
	double sum = 0;

	for (char *s = strtok(list, ","); s && n < 16; s = strtok(NULL, ","))
		m[n++].cpu = atoi(s);
	pthread_barrier_init(&bar, NULL, n);
	for (int i = 0; i < n; i++) {
		m[i].bar = &bar;
		pthread_create(&th[i], NULL, mt_read, &m[i]);
	}
	for (int i = 0; i < n; i++) {
		pthread_join(th[i], NULL);
		printf("cpu%d %.2f GB/s  ", m[i].cpu, m[i].gbs);
		sum += m[i].gbs;
	}
	printf("\nread, %d threads: %.2f GB/s together (%s)\n", n, sum,
	       hog_secs ? "each its average" : "each its best of 3 passes over 64 MB");
	return 0;
}

static double t_alu1(double *a) { (void)a; return alu(1); }
static double t_aluK(double *a) { return alu((int)*a); }

int main(int argc, char **argv)
{
	static const size_t sizes[] = { 4 << 10, 8 << 10, 16 << 10, 24 << 10, 32 << 10, 48 << 10, 64 << 10, 96 << 10,
		128 << 10, 192 << 10, 256 << 10, 384 << 10, 512 << 10, 768 << 10, 1 << 20, 3 << 19, 2 << 20, 3 << 20,
		4 << 20, 6 << 20, 8 << 20, 16 << 20, 32 << 20, 64 << 20, 128 << 20, 256 << 20 };
	int cpu;
	double ns1, ghz;

	if ((argc == 3 || argc == 4) && !strcmp(argv[1], "-m")) {
		hog_secs = argc == 4 ? atof(argv[3]) : 0;
		return multi(argv[2]);
	}
	if (argc == 3 && !strcmp(argv[2], "isa"))	/* only the clock and the isa tests */
		isa_only = 1;
	else if (argc != 2)
		return fprintf(stderr, "usage: archbench CPU [isa] | archbench -m CPU,CPU,... [SECONDS]\n"), 2;
	cpu = atoi(argv[1]);
	pin(cpu);

	/* clock: warm up (the governor ramps the clock under load), then the best of 3 */
	for (double t = now(); now() - t < 0.3;)
		alu(1);
	ns1 = best3(t_alu1, NULL, 1);
	ghz = 1 / ns1;
	printf("clock: %.3f ns a dependent add = %.3f GHz (cpufreq says %.3f GHz)\n", ns1, ghz, khz(cpu) / 1e6);

	printf("ilp: adds a cycle with 1/2/4/8 chains:");
	for (double k = 1; k <= 8; k *= 2)
		printf(" %.2f", ns1 / best3(t_aluK, &k, 1));
	printf("\n");
	{
		double one = 1, eight = 8, l = best3(t_fma, &one, 1), r = best3(t_fma, &eight, 1);

		printf("fma: FP32 fmla.4s latency %.1f cycles, %.2f a cycle = %.1f flops a cycle = %.1f GFLOPS\n",
		       l * ghz, 1 / (r * ghz), 8 / (r * ghz), 8 / r);
	}

	{
		static const char *what[] = { "v8.1 LSE ldadd (dependent)", "v8.0 ldxr/stxr add (dependent)",
					      "v8.2 FP16 fmla.8h", "v8.2 DotProd sdot" };

		signal(SIGILL, on_ill);
		for (int i = 0; i < 4; i++) {
			double a = i, v;

			if (sigsetjmp(ill_jb, 1)) {
				printf("isa: %s: SIGILL (this core does not have it)\n", what[i]);
				continue;
			}
			v = best3(t_isa, &a, 1);
			if (i < 2)
				printf("isa: %s: %.1f cycles an add\n", what[i], v * ghz);
			else
				printf("isa: %s: %.2f a cycle = %.0f %s a cycle\n", what[i], 1 / (v * ghz),
				       (i == 2 ? 16 : 32) / (v * ghz), i == 2 ? "flops" : "int8 ops");
		}
		signal(SIGILL, SIG_DFL);
	}
	if (isa_only)
		return 0;

	{
		size_t n = 256 << 10;
		uint8_t *r = malloc(n), *z = calloc(n, 1);
		double br = 1e9, bz = 1e9;

		for (size_t i = 0; i < n; i++)
			r[i] = xs() >> 32;
		for (int i = 0; i < 3; i++) {
			double a = branch(r, n, 16), b = branch(z, n, 16);

			br = a < br ? a : br;
			bz = b < bz ? b : bz;
		}
		printf("branch: %.2f ns an element random, %.2f predictable: a mispredict %.1f ns = %.1f cycles\n",
		       br, bz, 2 * (br - bz), 2 * (br - bz) * ghz);
		free(r);
		free(z);
	}

	{
		char *buf = big(256 << 20);

		printf("lat (ns, cycles a load):\n");
		for (size_t i = 0; i < sizeof(sizes) / sizeof(sizes[0]); i++) {
			double best = 1e9;

			chain(buf, sizes[i]);
			for (int r = 0; r < 3; r++) {
				double v = chase(buf, sizes[i], 1, 0.1);

				best = v < best ? v : best;
			}
			printf("  %7zu KB %7.2f ns %6.1f cyc\n", sizes[i] >> 10, best, best * ghz);
		}
		static const size_t ms[] = { 192 << 10, 64 << 20 };
		for (int s = 0; s < 2; s++) {
			double one = 0;

			chain(buf, ms[s]);
			printf("mlp %zu KB, ns a load with K chases (K=1,2,4,8), speedup:", ms[s] >> 10);
			for (int K = 1; K <= 8; K *= 2) {
				double best = 1e9;

				for (int r = 0; r < 3; r++) {
					double v = chase(buf, ms[s], K, 0.1);

					best = v < best ? v : best;
				}
				if (K == 1)
					one = best;
				printf(" %.2f (%.2fx)", best, one / best);
			}
			printf("\n");
		}
		munmap(buf, 256 << 20);
	}

	{
		size_t n = 64 << 20;
		char *a = big(n), *b = big(n);
		double r = 0, w = 0, c = 0;

		for (int i = 0; i < 3; i++) {
			double t, v = rd((uint64_t *)a, n);

			r = v > r ? v : r;
			t = now();
			memset(b, i, n);
			t = now() - t;
			w = n / t / 1e9 > w ? n / t / 1e9 : w;
			t = now();
			memcpy(b, a, n);
			t = now() - t;
			c = n / t / 1e9 > c ? n / t / 1e9 : c;
		}
		sink = b[n / 2];
		printf("bw one core (64 MB): read %.2f GB/s, memset %.2f GB/s, memcpy %.2f GB/s (bytes copied)\n", r, w, c);
	}
	printf("cpufreq at the end: %.3f GHz\n", khz(cpu) / 1e6);
	return 0;
}
