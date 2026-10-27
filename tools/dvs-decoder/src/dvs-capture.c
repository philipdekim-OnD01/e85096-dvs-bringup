// SPDX-License-Identifier: GPL-2.0
/*
 * dvs-capture: record frames from the E850 CSIS WDMA driver.
 *
 *   dvs-capture [-d /dev/e850-csis2] [-n FRAMES] [-t SECONDS] -o out.dvsc
 *
 * Starts the CSIS stream (start the sensor afterwards, e.g.
 * echo 1 > /sys/bus/i2c/devices/<bus>-0010/stream), writes every frame
 * returned by read() and prints the driver statistics at the end.
 * Ctrl-C stops cleanly.
 */
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <inttypes.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#include "../../../modules/csis/include/uapi/e850-csis-dvs.h"
#include "dvs_cap.h"

_Static_assert(sizeof(struct e850_dvs_info) == sizeof(struct dvs_cap_info),
	       "capture info must mirror the driver ABI");
_Static_assert(sizeof(struct e850_dvs_frame_hdr) ==
		       sizeof(struct dvs_frame_hdr),
	       "frame header must mirror the driver ABI");

static volatile sig_atomic_t stop;

static void on_signal(int sig)
{
	(void)sig;
	stop = 1;
}

static double now_s(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

static void print_stats(int fd)
{
	struct e850_dvs_stats s;

	if (ioctl(fd, E850_DVS_IOC_G_STATS, &s)) {
		perror("E850_DVS_IOC_G_STATS");
		return;
	}
	printf("driver: frames=%llu delivered=%llu dropped=%llu csis_err=%llu "
	       "dma_err=%llu lost_fs=%llu lost_fe=%llu crc_ecc=%llu "
	       "overflow=%llu resol=%llu\n",
	       (unsigned long long)s.frames, (unsigned long long)s.delivered,
	       (unsigned long long)s.dropped,
	       (unsigned long long)s.csis_errors,
	       (unsigned long long)s.dma_errors,
	       (unsigned long long)s.lost_fs, (unsigned long long)s.lost_fe,
	       (unsigned long long)s.crc_ecc, (unsigned long long)s.overflow,
	       (unsigned long long)s.resol_mismatch);
	printf("driver: int_src0=0x%08x int_src1=0x%08x dma_src=0x%08x "
	       "phy_status=0x%08x\n",
	       s.last_int_src0, s.last_int_src1, s.last_dma_src, s.phy_status);
}

int main(int argc, char **argv)
{
	const char *dev = "/dev/e850-csis2", *out = NULL;
	unsigned long max_frames = 0, frames = 0;
	double seconds = 0, start;
	struct e850_dvs_info info;
	struct sigaction sa;
	size_t rec_size;
	FILE *fp = NULL;
	uint8_t *rec;
	int c, fd, status = 0;

	while ((c = getopt(argc, argv, "d:n:t:o:")) != -1) {
		switch (c) {
		case 'd':
			dev = optarg;
			break;
		case 'n':
			max_frames = strtoul(optarg, NULL, 0);
			break;
		case 't':
			seconds = strtod(optarg, NULL);
			break;
		case 'o':
			out = optarg;
			break;
		default:
			fprintf(stderr, "usage: dvs-capture [-d DEV] [-n FRAMES] "
					"[-t SECONDS] -o FILE\n");
			return 2;
		}
	}
	if (!out) {
		fprintf(stderr, "dvs-capture: -o FILE is required\n");
		return 2;
	}

	fd = open(dev, O_RDONLY);
	if (fd < 0) {
		perror(dev);
		return 1;
	}
	if (ioctl(fd, E850_DVS_IOC_G_INFO, &info)) {
		perror("E850_DVS_IOC_G_INFO");
		return 1;
	}
	rec_size = sizeof(struct e850_dvs_frame_hdr) + info.buf_size;
	rec = malloc(rec_size);
	fp = fopen(out, "wb");
	if (!rec || !fp ||
	    dvs_cap_write_header(fp, (const struct dvs_cap_info *)&info)) {
		perror(out);
		return 1;
	}

	memset(&sa, 0, sizeof(sa));
	sa.sa_handler = on_signal;
	sigaction(SIGINT, &sa, NULL);
	sigaction(SIGTERM, &sa, NULL);

	if (ioctl(fd, E850_DVS_IOC_STREAMON)) {
		perror("E850_DVS_IOC_STREAMON");
		return 1;
	}
	printf("streaming CSIS%u: %u lane(s) @ %u Mbps, VC%u DT 0x%02x, "
	       "buffer %u B; start the sensor now\n",
	       info.csis_id, info.lanes, info.lane_mbps, info.vc,
	       info.data_type, info.buf_size);

	start = now_s();
	while (!stop && (!max_frames || frames < max_frames) &&
	       (seconds <= 0 || now_s() - start < seconds)) {
		struct pollfd pfd = { .fd = fd, .events = POLLIN };
		const struct e850_dvs_frame_hdr *h = (const void *)rec;
		ssize_t n;
		int pr = poll(&pfd, 1, 200);

		if (pr < 0 && errno == EINTR)
			continue;
		if (pr < 0) {
			perror("poll");
			status = 1;
			break;
		}
		if (pr == 0)
			continue;
		n = read(fd, rec, rec_size);
		if (n < 0 && (errno == EINTR || errno == EAGAIN))
			continue;
		if (n <= 0) {
			if (n < 0)
				perror("read");
			status = n < 0;
			break;
		}
		if (dvs_cap_write_frame(fp, (const struct dvs_frame_hdr *)h,
					rec + sizeof(*h))) {
			perror(out);
			status = 1;
			break;
		}
		frames++;
		if (frames % 100 == 0)
			fprintf(stderr, "\r%lu frames", frames);
	}
	fprintf(stderr, "\n");

	ioctl(fd, E850_DVS_IOC_STREAMOFF);
	print_stats(fd);
	printf("wrote %lu frames to %s in %.2f s\n", frames, out,
	       now_s() - start);
	if (fclose(fp)) {
		perror(out);
		status = 1;
	}
	free(rec);
	close(fd);
	return status;
}
