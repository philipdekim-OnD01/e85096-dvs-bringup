/*
 * simg-write - write an Android sparse image to a partition, and grow the ext4 on it (docs 11.120).
 * The SD card installer (e850-install, in the initramfs) uses it: busybox cannot write sparse images.
 *
 *   simg-write IMG|- DEV      RAW chunks copied, FILL written, DONT_CARE skipped, CRC32 ignored.
 *                             The header is checked against DEV's size before anything is written.
 *                             The image is read once, front to back, so it can come from a pipe (-).
 *   simg-write --grow MNT DEV grow the ext4 mounted at MNT to all of DEV (the ioctl online resize2fs uses)
 *
 * Progress on stderr every 5 %. Exit status 0 only when every chunk was written and fsync succeeded.
 * Build (static, for the initramfs): gcc -O2 -static -o simg-write simg-write.c   (build-bootimg.sh)
 * Test: tools/linux510/test_simg_write.sh
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/fs.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>

#ifndef EXT4_IOC_RESIZE_FS
#define EXT4_IOC_RESIZE_FS _IOW('f', 16, uint64_t)
#endif

#define MAGIC 0xed26ff3a
#define RAW 0xCAC1
#define FILL 0xCAC2
#define DONT_CARE 0xCAC3
#define CRC32 0xCAC4
#define BUF (4 << 20)

static int in;

static void die(const char *m)
{
	fprintf(stderr, "simg-write: %s%s%s\n", m, errno ? ": " : "", errno ? strerror(errno) : "");
	exit(1);
}

static void get(void *p, size_t n)        /* exactly n bytes of the image, or die */
{
	while (n) {
		ssize_t r = read(in, p, n);
		if (r <= 0) {
			if (r == 0)
				errno = 0;
			die("image ends early or cannot be read");
		}
		p = (char *)p + r;
		n -= r;
	}
}

static void put(int fd, const void *p, size_t n, uint64_t off)
{
	while (n) {
		ssize_t w = pwrite(fd, p, n, off);
		if (w <= 0)
			die("write failed");
		p = (const char *)p + w;
		n -= w;
		off += w;
	}
}

static uint64_t dev_size(int fd, int *is_blk)
{
	struct stat st;
	uint64_t sz = 0;
	if (fstat(fd, &st))
		die("stat");
	*is_blk = S_ISBLK(st.st_mode);
	if (*is_blk && ioctl(fd, BLKGETSIZE64, &sz))
		die("BLKGETSIZE64");
	return *is_blk ? sz : UINT64_MAX;         /* a regular file (tests) grows */
}

static int grow(const char *mnt, const char *dev)
{
	struct statvfs v;
	int is_blk, d = open(dev, O_RDONLY), m = open(mnt, O_RDONLY);
	if (d < 0 || m < 0)
		die("open");
	uint64_t sz = dev_size(d, &is_blk);
	if (!is_blk || statvfs(mnt, &v))
		die("--grow needs a mounted ext4 and its block device");
	uint64_t before = v.f_blocks * v.f_frsize / v.f_bsize, n = sz / v.f_bsize;
	if (n > before && ioctl(m, EXT4_IOC_RESIZE_FS, &n))
		die("EXT4_IOC_RESIZE_FS");
	statvfs(mnt, &v);
	fprintf(stderr, "simg-write: %s %llu -> %llu blocks of %lu (device %llu)\n", mnt, (unsigned long long)before,
		(unsigned long long)(v.f_blocks * v.f_frsize / v.f_bsize), v.f_bsize, (unsigned long long)n);
	return 0;
}

int main(int argc, char **argv)
{
	if (argc == 4 && !strcmp(argv[1], "--grow"))
		return grow(argv[2], argv[3]);
	if (argc != 3) {
		fprintf(stderr, "usage: simg-write IMG|- DEV | --grow MNT DEV\n");
		return 2;
	}
	in = strcmp(argv[1], "-") ? open(argv[1], O_RDONLY) : 0;
	int out = open(argv[2], O_WRONLY | O_CREAT, 0644), is_blk;
	if (in < 0 || out < 0)
		die("open");
	uint64_t size = dev_size(out, &is_blk);

	unsigned char h[28];
	get(h, sizeof(h));
	uint32_t magic, blk, blocks, chunks;
	uint16_t major, fhdr, chdr;
	memcpy(&magic, h, 4);
	memcpy(&major, h + 4, 2);
	memcpy(&fhdr, h + 8, 2);
	memcpy(&chdr, h + 10, 2);
	memcpy(&blk, h + 12, 4);
	memcpy(&blocks, h + 16, 4);
	memcpy(&chunks, h + 20, 4);
	errno = 0;
	if (magic != MAGIC || major != 1 || fhdr < 28 || chdr < 12 || fhdr > 28 + 256 || chdr > 12 + 256 || !blk ||
	    blk % 4 || !blocks)
		die("not an Android sparse image (v1)");
	uint64_t total = (uint64_t)blk * blocks;
	if (total > size) {
		fprintf(stderr, "simg-write: image is %llu bytes, %s only %llu\n", (unsigned long long)total, argv[2],
			(unsigned long long)size);
		return 1;
	}
	char *buf = malloc(BUF), skip[256];
	if (!buf)
		die("malloc");
	get(skip, fhdr - 28);

	uint64_t pos = 0;                         /* in blocks */
	int pct = 0;
	for (uint32_t c = 0; c < chunks; c++) {
		unsigned char ch[12];
		uint16_t type;
		uint32_t n, tsz;
		get(ch, sizeof(ch));
		get(skip, chdr - 12);
		memcpy(&type, ch, 2);
		memcpy(&n, ch + 4, 4);
		memcpy(&tsz, ch + 8, 4);
		uint64_t len = (uint64_t)n * blk, body = tsz - chdr;
		errno = 0;
		if (tsz < chdr || pos + n > blocks)
			die("chunk outside the image");
		if (type == RAW) {
			if (body != len)
				die("RAW chunk size");
			for (uint64_t o = 0; o < len; o += BUF) {
				size_t k = len - o < BUF ? len - o : BUF;
				get(buf, k);
				put(out, buf, k, pos * blk + o);
			}
		} else if (type == FILL) {
			if (body != 4)
				die("FILL chunk size");
			get(buf, 4);
			for (size_t i = 4; i < BUF; i += 4)
				memcpy(buf + i, buf, 4);
			for (uint64_t o = 0; o < len; o += BUF)
				put(out, buf, len - o < BUF ? len - o : BUF, pos * blk + o);
		} else if (type == DONT_CARE || type == CRC32) {
			if (body > sizeof(skip))
				die("DONT_CARE/CRC32 chunk size");
			get(skip, body);
			if (type == CRC32)
				n = 0;
		} else {
			die("unknown chunk type");
		}
		pos += n;
		if (pos * 20 / blocks > (uint64_t)pct) {
			pct = pos * 20 / blocks;
			fprintf(stderr, "simg-write: %d%%\n", pct * 5);
		}
	}
	errno = 0;
	if (pos != blocks)
		die("chunks do not cover the image");
	if (!is_blk && ftruncate(out, total))     /* trailing DONT_CARE in a file */
		die("ftruncate");
	if (fsync(out) || close(out))
		die("fsync");
	fprintf(stderr, "simg-write: %u chunks, %llu bytes written to %s\n", chunks, (unsigned long long)total, argv[2]);
	return 0;
}
