// SPDX-License-Identifier: GPL-2.0
/*
 * dvs-synth: write a synthetic capture for testing dvs-decode without a
 * board. Frame k holds a vertical ON edge at x = 10 + k and, from k = 1,
 * the OFF trailing edge at x = 9 + k, on every row. Payloads are padded to
 * a 4 KiB multiple like the driver's DMA buffers, and one sequence number
 * is skipped (with the DROPPED flag) to exercise gap reporting.
 *
 *   dvs-synth [-n FRAMES] [-W 960] [-H 720] -o synth.dvsc
 */
#include <getopt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dvs_cap.h"
#include "dvs_decode.h"

#define SYNTH_SKIP_SEQ 5

int main(int argc, char **argv)
{
	struct dvs_frame2b_cfg cfg = { .width = 960, .height = 720, .header = true };
	struct dvs_cap_info info = { 0 };
	unsigned int n = 10, k, y;
	const char *out = NULL;
	size_t frame, buf_size;
	uint8_t *buf;
	FILE *fp;
	int c;

	while ((c = getopt(argc, argv, "n:W:H:o:")) != -1) {
		switch (c) {
		case 'n':
			n = strtoul(optarg, NULL, 0);
			break;
		case 'W':
			cfg.width = strtoul(optarg, NULL, 0);
			break;
		case 'H':
			cfg.height = strtoul(optarg, NULL, 0);
			break;
		case 'o':
			out = optarg;
			break;
		default:
			goto usage;
		}
	}
	if (!out || cfg.width < 10 + n || !cfg.height)
		goto usage;

	frame = dvs_frame2b_size(&cfg);
	buf_size = (frame + 4095) & ~(size_t)4095;
	buf = malloc(buf_size);
	fp = fopen(out, "wb");
	if (!buf || !fp) {
		perror(out);
		return 1;
	}

	info.lanes = 4;
	info.lane_mbps = 1000;
	info.data_type = 0x2a;
	info.line_bytes = 2048;
	info.lines = buf_size / 2048;
	info.buf_size = buf_size;
	info.num_bufs = 8;
	if (dvs_cap_write_header(fp, &info))
		goto io_err;

	for (k = 0; k < n; k++) {
		uint32_t seq = k < SYNTH_SKIP_SEQ ? k : k + 1;
		struct dvs_frame_hdr h = {
			.magic = DVS_FRAME_MAGIC,
			.seq = seq,
			.ts_ns = 1000000ULL * k, /* 1000 fps */
			.bytes = buf_size,
			.flags = k == SYNTH_SKIP_SEQ ? DVS_FLAG_DROPPED : 0,
			.hw_frame = seq,
		};

		memset(buf, 0, buf_size);
		dvs_frame2b_put_header(buf, 1000 * k, k);
		for (y = 0; y < cfg.height; y++) {
			dvs_frame2b_put(buf, &cfg, 10 + k, y, 1);
			if (k)
				dvs_frame2b_put(buf, &cfg, 9 + k, y, 2);
		}
		if (dvs_cap_write_frame(fp, &h, buf))
			goto io_err;
	}
	if (fclose(fp))
		goto io_err_closed;
	free(buf);
	printf("wrote %u frames (%zu B payload, %zu B buffers) to %s\n", n,
	       frame, buf_size, out);
	return 0;

io_err:
	fclose(fp);
io_err_closed:
	perror(out);
	free(buf);
	return 1;
usage:
	fprintf(stderr, "usage: dvs-synth [-n FRAMES] [-W W] [-H H] -o FILE\n");
	return 2;
}
