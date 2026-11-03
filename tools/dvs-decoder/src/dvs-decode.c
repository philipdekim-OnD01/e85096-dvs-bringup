// SPDX-License-Identifier: GPL-2.0
/*
 * dvs-decode: decode an E850 DVS capture (dvs-capture output).
 *
 *   dvs-decode capture.dvsc                     summary, frame2b 960x720
 *   dvs-decode -o events.csv capture.dvsc       + per-event CSV
 *   dvs-decode --pgm out/ capture.dvsc          + one PGM image per frame
 *   dvs-decode -f raw --dump out/ capture.dvsc  inspect unknown payloads
 */
#include <errno.h>
#include <getopt.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dvs_cap.h"
#include "dvs_decode.h"

enum fmt { FMT_FRAME2B, FMT_RAW };

struct opts {
	enum fmt fmt;
	struct dvs_frame2b_cfg f2b;
	const char *csv;
	const char *pgm_dir;
	const char *dump_dir;
	unsigned long max_frames;
	int quiet;
};

struct totals {
	unsigned long frames;
	unsigned long seq_gaps;
	unsigned long flag_dropped;
	unsigned long flag_csis;
	unsigned long flag_dma;
	unsigned long short_frames;
	uint64_t on;
	uint64_t off;
	uint64_t reserved;
	uint64_t first_ns;
	uint64_t last_ns;
};

struct csv_ctx {
	FILE *fp;
	uint32_t seq;
};

static int csv_event(const struct dvs_event *ev, void *ctx)
{
	struct csv_ctx *c = ctx;

	return fprintf(c->fp, "%" PRIu32 ",%" PRIu64 ",%u,%u,%u\n", c->seq,
		       ev->t, ev->x, ev->y, ev->p) < 0;
}

static int write_file(const char *dir, const char *fmt, uint32_t seq,
		      const char *pgm_hdr, const void *data, size_t len)
{
	char path[4096];
	FILE *fp;
	int ret = 0;

	snprintf(path, sizeof(path), "%s/", dir);
	snprintf(path + strlen(path), sizeof(path) - strlen(path), fmt, seq);
	fp = fopen(path, "wb");
	if (!fp) {
		perror(path);
		return -1;
	}
	if ((pgm_hdr && fputs(pgm_hdr, fp) < 0) ||
	    fwrite(data, 1, len, fp) != len)
		ret = -1;
	if (fclose(fp))
		ret = -1;
	return ret;
}

/* 128 = no event, 255 = ON, 0 = OFF (NRV viewer "Gray" convention). */
static int write_pgm(const struct opts *o, uint32_t seq, const uint8_t *buf)
{
	size_t n = (size_t)o->f2b.width * o->f2b.height, i;
	const uint8_t *pix = buf + (o->f2b.header ? DVS_FRAME2B_HDR_BYTES : 0);
	static const uint8_t lut[4] = { 128, 255, 0, 128 };
	char hdr[64];
	uint8_t *img = malloc(n);
	int ret;

	if (!img)
		return -1;
	for (i = 0; i < n; i++)
		img[i] = lut[(pix[i / 4] >> (6 - 2 * (i % 4))) & 3];
	snprintf(hdr, sizeof(hdr), "P5\n%u %u\n255\n", o->f2b.width,
		 o->f2b.height);
	ret = write_file(o->pgm_dir, "frame_%06u.pgm", seq, hdr, img, n);
	free(img);
	return ret;
}

/* Bytes up to the last one that differs from the trailing fill byte. */
static size_t used_bytes(const uint8_t *p, size_t len)
{
	uint8_t fill;

	if (!len)
		return 0;
	fill = p[len - 1];
	while (len && p[len - 1] == fill)
		len--;
	return len;
}

static void raw_report(const struct dvs_frame_hdr *h, const uint8_t *p)
{
	size_t i, n = h->bytes < 16 ? h->bytes : 16;

	printf("  used~%zu/%u B fill=0x%02x head:", used_bytes(p, h->bytes),
	       h->bytes, h->bytes ? p[h->bytes - 1] : 0);
	for (i = 0; i < n; i++)
		printf(" %02x", p[i]);
	printf("\n");
}

static void usage(FILE *fp)
{
	fprintf(fp,
		"usage: dvs-decode [options] CAPTURE\n"
		"  -f, --format FMT   frame2b (default) or raw\n"
		"  -W, --width N      frame2b width  (default 960)\n"
		"  -H, --height N     frame2b height (default 720)\n"
		"      --no-header    frame2b payload has no 8-byte header\n"
		"  -o, --csv FILE     write seq,frame_timestamp,x,y,polarity\n"
		"      --pgm DIR      write one PGM per frame (frame2b)\n"
		"      --dump DIR     write each raw payload as .bin\n"
		"  -n, --frames N     stop after N frames\n"
		"  -q, --quiet        no per-frame lines\n");
}

static int parse_opts(int argc, char **argv, struct opts *o)
{
	static const struct option lo[] = {
		{ "format", required_argument, NULL, 'f' },
		{ "width", required_argument, NULL, 'W' },
		{ "height", required_argument, NULL, 'H' },
		{ "no-header", no_argument, NULL, 'N' },
		{ "csv", required_argument, NULL, 'o' },
		{ "pgm", required_argument, NULL, 'P' },
		{ "dump", required_argument, NULL, 'D' },
		{ "frames", required_argument, NULL, 'n' },
		{ "quiet", no_argument, NULL, 'q' },
		{ "help", no_argument, NULL, 'h' },
		{ NULL, 0, NULL, 0 },
	};
	int c;

	*o = (struct opts){
		.fmt = FMT_FRAME2B,
		.f2b = { .width = 960, .height = 720, .header = true },
	};
	while ((c = getopt_long(argc, argv, "f:W:H:o:n:qh", lo, NULL)) != -1) {
		switch (c) {
		case 'f':
			if (!strcmp(optarg, "frame2b"))
				o->fmt = FMT_FRAME2B;
			else if (!strcmp(optarg, "raw"))
				o->fmt = FMT_RAW;
			else
				return -1;
			break;
		case 'W':
			o->f2b.width = strtoul(optarg, NULL, 0);
			break;
		case 'H':
			o->f2b.height = strtoul(optarg, NULL, 0);
			break;
		case 'N':
			o->f2b.header = false;
			break;
		case 'o':
			o->csv = optarg;
			break;
		case 'P':
			o->pgm_dir = optarg;
			break;
		case 'D':
			o->dump_dir = optarg;
			break;
		case 'n':
			o->max_frames = strtoul(optarg, NULL, 0);
			break;
		case 'q':
			o->quiet = 1;
			break;
		case 'h':
			usage(stdout);
			exit(0);
		default:
			return -1;
		}
	}
	if (optind != argc - 1 || !o->f2b.width || !o->f2b.height)
		return -1;
	return 0;
}

int main(int argc, char **argv)
{
	struct dvs_cap_reader r;
	struct dvs_frame_hdr h;
	struct totals t = { 0 };
	struct csv_ctx csv = { 0 };
	uint32_t next_seq = 0;
	struct opts o;
	int ret, status = 0;

	if (parse_opts(argc, argv, &o)) {
		usage(stderr);
		return 2;
	}
	if (dvs_cap_open(&r, argv[optind]))
		return 1;

	printf("capture: CSIS%u ver 0x%08x, %u lane(s) @ %u Mbps, VC%u DT 0x%02x, "
	       "%ux%u B/frame, buf %u B\n",
	       r.info.csis_id, r.info.csis_version, r.info.lanes,
	       r.info.lane_mbps, r.info.vc, r.info.data_type,
	       r.info.line_bytes, r.info.lines, r.info.buf_size);

	if (o.csv) {
		csv.fp = fopen(o.csv, "w");
		if (!csv.fp) {
			perror(o.csv);
			dvs_cap_close(&r);
			return 1;
		}
		fputs("seq,frame_timestamp,x,y,polarity\n", csv.fp);
	}

	while ((ret = dvs_cap_next(&r, &h)) == 1) {
		struct dvs_frame2b_frame f;

		if (t.frames && h.seq != next_seq)
			t.seq_gaps++;
		next_seq = h.seq + 1;
		if (!t.frames)
			t.first_ns = h.ts_ns;
		t.last_ns = h.ts_ns;
		t.frames++;
		t.flag_dropped += !!(h.flags & DVS_FLAG_DROPPED);
		t.flag_csis += !!(h.flags & DVS_FLAG_CSIS_ERR);
		t.flag_dma += !!(h.flags & DVS_FLAG_DMA_ERR);

		if (!o.quiet)
			printf("frame seq=%u hw=%u t=%" PRIu64 " ns bytes=%u flags=0x%x\n",
			       h.seq, h.hw_frame, h.ts_ns, h.bytes, h.flags);
		if (o.dump_dir &&
		    write_file(o.dump_dir, "payload_%06u.bin", h.seq, NULL,
			       r.payload, h.bytes)) {
			status = 1;
			break;
		}

		if (o.fmt == FMT_RAW) {
			if (!o.quiet)
				raw_report(&h, r.payload);
		} else {
			csv.seq = h.seq;
			ret = dvs_frame2b_decode(r.payload, h.bytes, &o.f2b, &f,
						 csv.fp ? csv_event : NULL,
						 &csv);
			if (ret < 0) {
				t.short_frames++;
				if (!o.quiet)
					printf("  short frame: %u < %zu B\n",
					       h.bytes, dvs_frame2b_size(&o.f2b));
			} else if (ret) {
				fprintf(stderr, "CSV write failed\n");
				status = 1;
				break;
			} else {
				t.on += f.on;
				t.off += f.off;
				t.reserved += f.reserved;
				if (!o.quiet)
					printf("  sensor ts=%u frame=%u on=%" PRIu64
					       " off=%" PRIu64 "\n",
					       f.timestamp, f.frame_num, f.on,
					       f.off);
				if (o.pgm_dir && write_pgm(&o, h.seq, r.payload)) {
					status = 1;
					break;
				}
			}
		}
		if (o.max_frames && t.frames >= o.max_frames)
			break;
	}
	if (ret < 0) {
		fprintf(stderr, "corrupt frame record after %lu frames\n",
			t.frames);
		status = 1;
	}

	printf("summary: frames=%lu seq_gaps=%lu dropped=%lu csis_err=%lu "
	       "dma_err=%lu short=%lu",
	       t.frames, t.seq_gaps, t.flag_dropped, t.flag_csis, t.flag_dma,
	       t.short_frames);
	if (o.fmt == FMT_FRAME2B)
		printf(" events=%" PRIu64 " on=%" PRIu64 " off=%" PRIu64
		       " reserved=%" PRIu64,
		       t.on + t.off, t.on, t.off, t.reserved);
	if (t.frames > 1 && t.last_ns > t.first_ns)
		printf(" fps=%.1f", (t.frames - 1) * 1e9 /
					    (double)(t.last_ns - t.first_ns));
	printf("\n");

	if (csv.fp && fclose(csv.fp)) {
		perror(o.csv);
		status = 1;
	}
	dvs_cap_close(&r);
	return status;
}
