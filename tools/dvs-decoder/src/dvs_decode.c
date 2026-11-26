// SPDX-License-Identifier: GPL-2.0
#include "dvs_decode.h"

static uint32_t get_le32(const uint8_t *p)
{
	return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 |
	       (uint32_t)p[3] << 24;
}

static void put_le32(uint8_t *p, uint32_t v)
{
	p[0] = v;
	p[1] = v >> 8;
	p[2] = v >> 16;
	p[3] = v >> 24;
}

size_t dvs_frame2b_size(const struct dvs_frame2b_cfg *cfg)
{
	size_t pixels = (size_t)cfg->width * cfg->height;

	return (cfg->header ? DVS_FRAME2B_HDR_BYTES : 0) + (pixels + 3) / 4;
}

int dvs_frame2b_decode(const uint8_t *buf, size_t len,
		       const struct dvs_frame2b_cfg *cfg,
		       struct dvs_frame2b_frame *out, dvs_event_cb cb,
		       void *ctx)
{
	size_t pixels = (size_t)cfg->width * cfg->height;
	size_t i, nbytes = (pixels + 3) / 4;
	const uint8_t *pix = buf;
	struct dvs_event ev;
	int ret;

	if (len < dvs_frame2b_size(cfg))
		return -1;

	out->timestamp = 0;
	out->frame_num = 0;
	if (cfg->header) {
		out->timestamp = get_le32(buf);
		out->frame_num = get_le32(buf + 4);
		pix += DVS_FRAME2B_HDR_BYTES;
	}
	out->on = out->off = out->reserved = 0;
	ev.t = out->timestamp;

	for (i = 0; i < nbytes; i++) {
		uint8_t byte = pix[i];
		unsigned int k;

		/* Most pixels carry no event: skip empty bytes cheaply. */
		if (!byte)
			continue;
		for (k = 0; k < 4; k++) {
			unsigned int code = (byte >> (6 - 2 * k)) & 3;
			size_t idx = i * 4 + k;

			if (!code || idx >= pixels)
				continue;
			if (code == 3) {
				out->reserved++;
				continue;
			}
			if (code == 1) {
				out->on++;
				ev.p = DVS_POL_ON;
			} else {
				out->off++;
				ev.p = DVS_POL_OFF;
			}
			if (!cb)
				continue;
			ev.x = idx % cfg->width;
			ev.y = idx / cfg->width;
			ret = cb(&ev, ctx);
			if (ret)
				return ret;
		}
	}
	return 0;
}

void dvs_frame2b_put(uint8_t *buf, const struct dvs_frame2b_cfg *cfg,
		     unsigned int x, unsigned int y, unsigned int code)
{
	size_t idx = (size_t)y * cfg->width + x;
	uint8_t *pix = buf + (cfg->header ? DVS_FRAME2B_HDR_BYTES : 0);
	unsigned int shift = 6 - 2 * (idx % 4);

	pix[idx / 4] = (pix[idx / 4] & ~(3U << shift)) | (code & 3) << shift;
}

void dvs_frame2b_put_header(uint8_t *buf, uint32_t timestamp,
			    uint32_t frame_num)
{
	put_le32(buf, timestamp);
	put_le32(buf + 4, frame_num);
}
