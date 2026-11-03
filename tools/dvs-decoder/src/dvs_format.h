/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Capture container written by dvs-capture and read by dvs-decode.
 *
 *   struct dvs_cap_file_hdr
 *   struct dvs_cap_info            (copy of struct e850_dvs_info)
 *   { struct dvs_frame_hdr, payload[hdr.bytes] } * N
 *
 * Each frame record is exactly what one read() of /dev/e850-csis<N>
 * returns, so the layouts below mirror modules/csis/include/uapi.
 * All fields are little endian.
 */
#ifndef DVS_FORMAT_H
#define DVS_FORMAT_H

#include <stdint.h>

#define DVS_CAP_MAGIC "E850DVSC"
#define DVS_CAP_VERSION 1
#define DVS_FRAME_MAGIC 0x31535644U /* "DVS1" */

#define DVS_FLAG_CSIS_ERR (1U << 0)
#define DVS_FLAG_DMA_ERR (1U << 1)
#define DVS_FLAG_DROPPED (1U << 2)

struct dvs_cap_file_hdr {
	char magic[8];
	uint32_t version;
	uint32_t info_size;
};

struct dvs_cap_info {
	uint32_t csis_id;
	uint32_t csis_version;
	uint32_t lanes;
	uint32_t lane_mbps;
	uint32_t settle;
	uint32_t data_type;
	uint32_t vc;
	uint32_t line_bytes;
	uint32_t lines;
	uint32_t buf_size;
	uint32_t num_bufs;
	uint32_t reserved[5];
};

struct dvs_frame_hdr {
	uint32_t magic;
	uint32_t seq;
	uint64_t ts_ns;
	uint32_t bytes;
	uint32_t flags;
	uint32_t hw_frame;
	uint32_t reserved;
};

_Static_assert(sizeof(struct dvs_cap_file_hdr) == 16, "file header layout");
_Static_assert(sizeof(struct dvs_cap_info) == 64, "info layout");
_Static_assert(sizeof(struct dvs_frame_hdr) == 32, "frame header layout");

#endif /* DVS_FORMAT_H */
