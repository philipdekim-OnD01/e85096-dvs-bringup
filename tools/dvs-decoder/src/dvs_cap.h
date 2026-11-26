/* SPDX-License-Identifier: GPL-2.0 */
#ifndef DVS_CAP_H
#define DVS_CAP_H

#include <stdio.h>

#include "dvs_format.h"

struct dvs_cap_reader {
	FILE *fp;
	struct dvs_cap_info info;
	unsigned char *payload; /* holds the current frame's payload */
	size_t payload_cap;
};

/* Returns 0 or -1 with a message on stderr. */
int dvs_cap_open(struct dvs_cap_reader *r, const char *path);
/* Returns 1 for a frame, 0 at end of file, -1 on a corrupt record. */
int dvs_cap_next(struct dvs_cap_reader *r, struct dvs_frame_hdr *hdr);
void dvs_cap_close(struct dvs_cap_reader *r);

int dvs_cap_write_header(FILE *fp, const struct dvs_cap_info *info);
int dvs_cap_write_frame(FILE *fp, const struct dvs_frame_hdr *hdr,
			const void *payload);

#endif /* DVS_CAP_H */
