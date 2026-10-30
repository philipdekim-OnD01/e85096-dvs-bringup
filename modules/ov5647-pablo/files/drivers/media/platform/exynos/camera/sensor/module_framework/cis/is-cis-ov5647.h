/* SPDX-License-Identifier: GPL-2.0 */
/*
 * OmniVision OV5647 CIS definitions for the Exynos Pablo camera framework.
 * Register names follow mainline Linux drivers/media/i2c/ov5647.c.
 */
#ifndef IS_CIS_OV5647_H
#define IS_CIS_OV5647_H

#include "is-cis.h"

#define OV5647_CHIP_ID			0x5647

#define OV5647_REG_SW_STANDBY		0x0100
#define OV5647_REG_SW_RESET		0x0103
#define OV5647_REG_CHIPID_H		0x300a
#define OV5647_REG_CHIPID_L		0x300b
#define OV5647_REG_PAD_OUT		0x300d
#define OV5647_REG_FRAME_OFF_NUMBER	0x4202
#define OV5647_REG_MIPI_CTRL00		0x4800
#define OV5647_REG_MIPI_CTRL14		0x4814

/* MIPI_CTRL00 bits */
#define OV5647_MIPI_CTRL00_CLOCK_LANE_GATE	(1 << 5)
#define OV5647_MIPI_CTRL00_LINE_SYNC_ENABLE	(1 << 4)
#define OV5647_MIPI_CTRL00_BUS_IDLE		(1 << 2)
#define OV5647_MIPI_CTRL00_CLOCK_LANE_DISABLE	(1 << 0)
#define OV5647_MIPI_CTRL00_STREAM_OFF	(OV5647_MIPI_CTRL00_CLOCK_LANE_GATE | \
					 OV5647_MIPI_CTRL00_BUS_IDLE | \
					 OV5647_MIPI_CTRL00_CLOCK_LANE_DISABLE)

/* Default (power-on) mode: 640x480 RAW10, see is-cis-ov5647-setA.h */
#define SENSOR_OV5647_DEFAULT_WIDTH	640
#define SENSOR_OV5647_DEFAULT_HEIGHT	480

#endif
