/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
/*
 * Userspace ABI of the E850-96 CSIS -> WDMA -> DRAM capture driver.
 *
 * Each read() on /dev/e850-csis<N> returns exactly one completed CSIS
 * frame: struct e850_dvs_frame_hdr followed by hdr.bytes payload bytes.
 * The buffer passed to read() must hold sizeof(hdr) + info.buf_size bytes.
 */
#ifndef _UAPI_E850_CSIS_DVS_H
#define _UAPI_E850_CSIS_DVS_H

#include <linux/ioctl.h>
#include <linux/types.h>

#define E850_DVS_FRAME_MAGIC 0x31535644U /* "DVS1", little endian */

/* hdr.flags */
#define E850_DVS_FLAG_CSIS_ERR (1U << 0)  /* CSIS link error during frame */
#define E850_DVS_FLAG_DMA_ERR (1U << 1)   /* WDMA FIFO full / overlap */
#define E850_DVS_FLAG_DROPPED (1U << 2)   /* frames lost before this one */

struct e850_dvs_frame_hdr {
	__u32 magic;
	__u32 seq;      /* driver frame sequence, starts at 0 per stream */
	__u64 ts_ns;    /* CLOCK_MONOTONIC at DMA frame-end interrupt */
	__u32 bytes;    /* payload bytes that follow this header */
	__u32 flags;
	__u32 hw_frame; /* CSIS FRM_CNT of the link (hardware counter) */
	__u32 reserved;
};

struct e850_dvs_info {
	__u32 csis_id;      /* 0..2 */
	__u32 csis_version; /* CSIS_VERSION register */
	__u32 lanes;
	__u32 lane_mbps;
	__u32 settle;
	__u32 data_type;    /* MIPI CSI-2 data identifier, e.g. 0x2a RAW8 */
	__u32 vc;
	__u32 line_bytes;   /* HRESOL: bytes per long packet */
	__u32 lines;        /* VRESOL: long packets per frame */
	__u32 buf_size;     /* bytes per DMA buffer */
	__u32 num_bufs;     /* 1..8 hardware address slots */
	__u32 reserved[5];
};

struct e850_dvs_stats {
	__u64 frames;        /* DMA frame-end events */
	__u64 delivered;     /* frames returned through read() */
	__u64 dropped;       /* overwritten before userspace read them */
	__u64 csis_errors;   /* any CSIS error interrupt */
	__u64 dma_errors;    /* WDMA error / OTF overlap */
	__u64 lost_fs;
	__u64 lost_fe;
	__u64 crc_ecc;
	__u64 overflow;
	__u64 resol_mismatch;
	__u32 last_int_src0;
	__u32 last_int_src1;
	__u32 last_dma_src;
	__u32 phy_status;
	__u64 reserved[4];
};

#define E850_DVS_IOC_MAGIC 'D'
#define E850_DVS_IOC_G_INFO _IOR(E850_DVS_IOC_MAGIC, 0, struct e850_dvs_info)
#define E850_DVS_IOC_STREAMON _IO(E850_DVS_IOC_MAGIC, 1)
#define E850_DVS_IOC_STREAMOFF _IO(E850_DVS_IOC_MAGIC, 2)
#define E850_DVS_IOC_G_STATS _IOR(E850_DVS_IOC_MAGIC, 3, struct e850_dvs_stats)

#endif /* _UAPI_E850_CSIS_DVS_H */
