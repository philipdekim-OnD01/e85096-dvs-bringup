/* SPDX-License-Identifier: GPL-2.0 */
/*
 * DVS payload decoders.
 *
 * frame2b: the 2-bit-per-pixel event frame used by NRV's public FPGA
 * platform (github.com/nrvcorp/embeddedsw, 1.Single_DVS host code):
 *   bytes 0..3  frame timestamp, u32 little endian
 *   bytes 4..7  frame number,    u32 little endian
 *   bytes 8..   width*height pixels, 4 per byte, first pixel in bits 7:6
 *               0 = no event, 1 = ON, 2 = OFF, 3 = reserved
 *
 * The sensor's native MIPI "MGROUP" layout is not publicly documented;
 * capture it with dvs-decode -f raw --dump and add a decoder here once the
 * layout is confirmed against NRV documentation.
 */
#ifndef DVS_DECODE_H
#define DVS_DECODE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define DVS_POL_OFF 0
#define DVS_POL_ON 1

struct dvs_event {
	uint64_t t; /* frame timestamp (sensor units) */
	uint16_t x;
	uint16_t y;
	uint8_t p; /* DVS_POL_ON / DVS_POL_OFF */
};

/* Return non-zero to stop decoding. */
typedef int (*dvs_event_cb)(const struct dvs_event *ev, void *ctx);

struct dvs_frame2b_cfg {
	unsigned int width;
	unsigned int height;
	bool header; /* 8-byte timestamp/frame-number header present */
};

struct dvs_frame2b_frame {
	uint32_t timestamp;
	uint32_t frame_num;
	uint64_t on;
	uint64_t off;
	uint64_t reserved; /* pixels with the reserved code 3 */
};

#define DVS_FRAME2B_HDR_BYTES 8

size_t dvs_frame2b_size(const struct dvs_frame2b_cfg *cfg);

/*
 * Decode one frame. @len may exceed the frame size (DMA buffer padding).
 * Returns 0, -1 if @len is too short, or the callback's non-zero value.
 */
int dvs_frame2b_decode(const uint8_t *buf, size_t len,
		       const struct dvs_frame2b_cfg *cfg,
		       struct dvs_frame2b_frame *out, dvs_event_cb cb,
		       void *ctx);

/* Set one pixel in a frame2b buffer (code: 0 none, 1 ON, 2 OFF). */
void dvs_frame2b_put(uint8_t *buf, const struct dvs_frame2b_cfg *cfg,
		     unsigned int x, unsigned int y, unsigned int code);

void dvs_frame2b_put_header(uint8_t *buf, uint32_t timestamp,
			    uint32_t frame_num);

#endif /* DVS_DECODE_H */
