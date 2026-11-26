// SPDX-License-Identifier: GPL-2.0
/*
 * E850-96 (Exynos850) MIPI CSIS v1.2 -> CSIS WDMA -> DRAM capture driver
 * for the NRV RC1S DVS.
 *
 * The ISP is bypassed: the CSIS WDMA stores the CSI-2 long-packet payload
 * of one virtual channel / data type into a ring of eight DMA buffers, one
 * buffer per CSI-2 frame (FS..FE). Userspace reads completed buffers from
 * /dev/e850-csis<N>; see include/uapi/e850-csis-dvs.h.
 *
 * Register layout and programming order follow the GPL-2.0 Linaro
 * android-exynos-4.14 BSP: is-hw-csi-v1_2.[ch], is-device-csi_v4.c and
 * phy-exynos-mipi.c (PHY config 0x0503_0008, D-PHY). Exynos850 has no
 * mainline CSIS/D-PHY driver, so this module owns both.
 */
#include <linux/bitfield.h>
#include <linux/clk.h>
#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/fs.h>
#include <linux/interrupt.h>
#include <linux/io.h>
#include <linux/iopoll.h>
#include <linux/ktime.h>
#include <linux/mfd/syscon.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/poll.h>
#include <linux/regmap.h>
#include <linux/sizes.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/wait.h>

#include "../include/uapi/e850-csis-dvs.h"

/* MIPI-CSIS link registers (is-hw-csi-v1_2.h csi_regs[]) */
#define CSIS_VERSION 0x0000
#define CSIS_CMN_CTRL 0x0004
#define CMN_CSI_EN BIT(0)
#define CMN_SW_RESET BIT(1)
#define CMN_LANE_NUMBER GENMASK(9, 8)
#define CMN_INTERLEAVE_MODE GENMASK(11, 10)
#define CMN_PHY_SEL BIT(21)
#define CSIS_UPD_SDW 0x000c
#define UPD_SDW_ALL GENMASK(3, 0)
#define CSIS_INT_MSK0 0x0010
#define CSIS_INT_SRC0 0x0014
#define CSIS_INT_MSK1 0x0018
#define CSIS_INT_SRC1 0x001c
#define CSIS_FS_INT_SRC 0x0024
#define CSIS_FE_INT_SRC 0x002c
#define CSIS_ISP_CONFIG_CH0 0x0040
#define CFG_DATAFORMAT GENMASK(7, 2)
#define CFG_POTF_ONLY BIT(11)
#define CFG_PIXEL_MODE GENMASK(13, 12)
#define CFG_VIRTUAL_CHANNEL GENMASK(20, 16)
#define CSIS_ISP_RESOL_CH0 0x0044
#define RESOL_HRESOL GENMASK(15, 0)
#define RESOL_VRESOL GENMASK(31, 16)
#define CSIS_FRM_CNT_CH0 0x0500
#define CSIS_ERR_LOST_FS 0x0614
#define CSIS_ERR_LOST_FE 0x061c
#define CSIS_ERR_VRESOL 0x0624
#define CSIS_ERR_HRESOL 0x062c
#define CSIS_DBG_OPTION_SUITE 0x0690
#define DBG_PACKED_PIXEL_MODE BIT(23)
#define CSIS_PHY_STATUS 0x0700
#define CSIS_PHY_CMN_CTRL 0x0704
#define PHY_ENABLE_CLK BIT(0)
#define PHY_ENABLE_DAT GENMASK(4, 1)

/* INT_SRC0 error bits; the BSP enables 0x00fff07f in INT_MSK0 */
#define CSIS_IRQ_MASK0 0x00fff07fU
#define SRC0_ERR_ID BIT(0)
#define SRC0_ERR_CRC BIT(1)
#define SRC0_ERR_ECC BIT(2)
#define SRC0_ERR_WRONG_CFG BIT(3)
#define SRC0_ERR_OVER BIT(4)
/* INT_SRC1: FS/FE are reported by the WDMA instead, keep errors only */
#define CSIS_IRQ_MASK1 0x00000078U
#define SRC1_ERR_LOST_FE BIT(3)
#define SRC1_ERR_LOST_FS BIT(4)
#define SRC1_HRESOL_MISMATCH BIT(5)
#define SRC1_VRESOL_MISMATCH BIT(6)

/* Per-VC WDMA channel (csi_vcdma_regs[]) */
#define DMA_CTRL 0x0000
#define DMA_CTRL_ENABLE BIT(0)
#define DMA_CTRL_UPDT_PTR_EN BIT(1)
#define DMA_FMT 0x0004
#define DMA_FMT_BYTESWAP BIT(2)
#define DMA_FMT_STORAGE_PACKET BIT(4)
#define DMA_FMT_DIM_1D BIT(5)
#define DMA_ADDR(n) (0x0010 + 4 * (n))
#define DMA_ACT_CTRL 0x0030
#define ACT_FRAMEPTR GENMASK(4, 2)
#define DMA_FCNTSEQ 0x0050
#define DMA_NUM_SLOTS 8

/* WDMA common (csi_vcdma_cmn_regs[]) */
#define DMA_DATA_CTRL 0x0000
#define DATA_INPUT_PATH_PRL BIT(0)
#define DMA_INT_MASK 0x0004
#define DMA_INT_SRC 0x0008
#define DMA_CMN_CTRL 0x0014
#define CMN_ABORT_REQ BIT(0)
#define DMA_ERR_CODE 0x0018
#define DMA_OTF_FORMAT 0x001c
#define OTF_FORMAT_8BIT 3
/* WDMA channel 0 interrupt bits */
#define DMA_INT_FRM_START BIT(4)
#define DMA_INT_FRM_END BIT(8)
#define DMA_INT_ERROR BIT(12)
#define DMA_INT_ABORT_DONE BIT(16)
#define DMA_INT_OTF_OVERLAP BIT(20)
#define DMA_INT_CH0 (DMA_INT_FRM_START | DMA_INT_FRM_END | DMA_INT_ERROR | \
		     DMA_INT_ABORT_DONE | DMA_INT_OTF_OVERLAP)

/* CSISX2 common DMA top (csi_dma_regs[]), shared by all CSIS links */
#define TOP_ARB_PRI 0x0000
#define TOP_CTRL 0x0008
#define TOP_CTRL_IP_PROCESSING BIT(1)
#define TOP_SRAM_SPLIT 0x000c
#define TOP_MATRIX 0x0010
#define TOP_CLK_CTRL 0x0014
#define TOP_MAX_MO 0x0018

/* D-PHY (phy-exynos-mipi.c __set_phy_cfg_0503_0008_dphy) */
#define DPHY_ACTRL_SC_06 0x1018
#define DPHY_ACTRL_SD_08(l) (0x143c + 0x400 * (l))
#define DPHY_DCTRL_SD_05(l) (0x14ac + 0x400 * (l))
#define DPHY_DCTRL_SD_06(l) (0x14b0 + 0x400 * (l))
#define DPHY_DCTRL_SD_13(l) (0x14e0 + 0x400 * (l))
#define DPHY_REF_SPEED_MBPS 1500
#define PMU_PHY_ISO_BYPASS BIT(0)

#define MIPI_DT_RAW8 0x2a

static unsigned int lane_mbps;
module_param(lane_mbps, uint, 0444);
MODULE_PARM_DESC(lane_mbps, "Override per-lane D-PHY rate in Mbps (0: DT)");
static unsigned int settle;
module_param(settle, uint, 0444);
MODULE_PARM_DESC(settle, "Override D-PHY HS settle count (0: from rate)");

struct e850_csis_buf {
	void *cpu;
	dma_addr_t dma;
	u64 ts_ns;
	u32 seq;
	u32 flags;
	u32 hw_frame;
};

struct e850_csis {
	struct device *dev;
	void __iomem *csis;
	void __iomem *phy;
	void __iomem *wdma;
	void __iomem *wdma_cmn;
	void __iomem *dma_top;
	void __iomem *phy_rst;
	struct regmap *pmu;
	u32 pmu_iso;
	u32 id;
	u32 version;

	u32 lanes;
	u32 lane_mbps;
	u32 settle;
	u32 data_type;
	u32 vc;
	u32 line_bytes;
	u32 lines;
	u32 buf_size;
	bool byteswap;

	struct e850_csis_buf bufs[DMA_NUM_SLOTS];

	struct miscdevice misc;
	char name[16];
	struct dentry *debugfs;

	/* Serializes open/stream state changes; not taken from IRQ context. */
	struct mutex lock;
	bool opened;
	bool streaming;

	/* Protects the ring state and statistics below against the IRQs. */
	spinlock_t slock;
	wait_queue_head_t wq;
	int fill_slot;
	int read_slot;
	bool read_overrun;
	u32 ready_mask;
	u32 seq;
	u32 pending_flags;
	struct e850_dvs_stats stats;
};

/*
 * The three CSIS D-PHYs share one PMU isolation bit and one reset register
 * (BSP "samsung,mipi-phy-m0s4s4s2": MIPI_PHY_MxSx_SHARED). Only taken from
 * stream on/off, i.e. process context.
 */
static DEFINE_MUTEX(e850_phy_lock);
static unsigned int e850_phy_users;

/* is-hw-settle-8nm-lpp.h: rate (Mbps, upper bound of a 10 Mbps step) -> settle */
static const u16 e850_settle_table[][2] = {
	{ 80, 5 }, { 100, 6 }, { 120, 7 }, { 140, 8 }, { 160, 9 }, { 180, 10 },
	{ 200, 11 }, { 220, 12 }, { 240, 13 }, { 270, 14 }, { 290, 15 },
	{ 310, 16 }, { 330, 17 }, { 350, 18 }, { 370, 19 }, { 390, 20 },
	{ 410, 21 }, { 430, 22 }, { 450, 23 }, { 470, 24 }, { 490, 25 },
	{ 510, 26 }, { 540, 27 }, { 560, 28 }, { 580, 29 }, { 600, 30 },
	{ 620, 31 }, { 640, 32 }, { 660, 33 }, { 680, 34 }, { 700, 35 },
	{ 720, 36 }, { 740, 37 }, { 760, 38 }, { 790, 39 }, { 810, 40 },
	{ 830, 41 }, { 850, 42 }, { 870, 43 }, { 890, 44 }, { 910, 45 },
	{ 930, 46 }, { 950, 47 }, { 970, 48 }, { 990, 49 }, { 1010, 50 },
	{ 1030, 51 }, { 1060, 52 }, { 1080, 53 }, { 1100, 54 }, { 1120, 55 },
	{ 1140, 56 }, { 1160, 57 }, { 1180, 58 }, { 1200, 59 }, { 1220, 60 },
	{ 1240, 61 }, { 1260, 62 }, { 1280, 63 }, { 1310, 64 }, { 1330, 65 },
	{ 1350, 66 }, { 1370, 67 }, { 1390, 68 }, { 1410, 69 }, { 1430, 70 },
	{ 1450, 71 }, { 1470, 72 }, { 1490, 73 }, { 1580, 7 }, { 1740, 8 },
	{ 1910, 9 }, { 2070, 10 }, { 2240, 11 }, { 2410, 12 }, { 2570, 13 },
};

static u32 e850_find_settle(u32 mbps)
{
	u32 aligned = ALIGN(mbps, 10);
	size_t i;

	for (i = 0; i < ARRAY_SIZE(e850_settle_table); i++)
		if (aligned <= e850_settle_table[i][0])
			return e850_settle_table[i][1];
	return e850_settle_table[ARRAY_SIZE(e850_settle_table) - 1][1];
}

static void e850_update_bits(void __iomem *reg, u32 mask, u32 val)
{
	writel((readl(reg) & ~mask) | (val & mask), reg);
}

static void e850_phy_power(struct e850_csis *c, bool on)
{
	u32 bit = BIT(c->id);

	mutex_lock(&e850_phy_lock);
	if (on) {
		if (e850_phy_users++ == 0)
			regmap_update_bits(c->pmu, c->pmu_iso,
					   PMU_PHY_ISO_BYPASS,
					   PMU_PHY_ISO_BYPASS);
		/* Assert, then release this D-PHY's reset. */
		e850_update_bits(c->phy_rst, bit, 0);
		e850_update_bits(c->phy_rst, bit, bit);
	} else {
		e850_update_bits(c->phy_rst, bit, 0);
		if (e850_phy_users && --e850_phy_users == 0)
			regmap_update_bits(c->pmu, c->pmu_iso,
					   PMU_PHY_ISO_BYPASS, 0);
	}
	mutex_unlock(&e850_phy_lock);
}

static void e850_phy_config(struct e850_csis *c)
{
	u32 skew_delay_sel = 0, skew_cal_en = 0, hs_mode_sel = 1;
	u32 l;

	if (c->lane_mbps >= DPHY_REF_SPEED_MBPS) {
		skew_cal_en = 1;
		hs_mode_sel = 0;
		if (c->lane_mbps >= 3000 && c->lane_mbps <= 4500)
			skew_delay_sel = 1;
		else if (c->lane_mbps >= 2000 && c->lane_mbps < 3000)
			skew_delay_sel = 2;
		else if (c->lane_mbps >= 1500 && c->lane_mbps < 2000)
			skew_delay_sel = 3;
	}

	writel(0x00000002, c->phy + DPHY_ACTRL_SC_06);
	for (l = 0; l < c->lanes; l++) {
		e850_update_bits(c->phy + DPHY_ACTRL_SD_08(l), GENMASK(6, 5),
				 skew_delay_sel << 5);
		e850_update_bits(c->phy + DPHY_DCTRL_SD_13(l), BIT(0),
				 skew_cal_en);
		e850_update_bits(c->phy + DPHY_DCTRL_SD_05(l), BIT(2),
				 hs_mode_sel << 2);
		e850_update_bits(c->phy + DPHY_DCTRL_SD_06(l), GENMASK(7, 0),
				 c->settle);
	}
}

static int e850_csis_reset(struct e850_csis *c)
{
	u32 val;

	writel(readl(c->csis + CSIS_CMN_CTRL) | CMN_SW_RESET,
	       c->csis + CSIS_CMN_CTRL);
	return readl_poll_timeout_atomic(c->csis + CSIS_CMN_CTRL, val,
					 !(val & CMN_SW_RESET), 10, 1000);
}

static void e850_dma_top_config(struct e850_csis *c)
{
	/* csi_hw_s_dma_common_dynamic(): SRAM0/1 10 KiB each, split 0x1f0. */
	e850_update_bits(c->dma_top + TOP_ARB_PRI, GENMASK(1, 0), 0x2);
	e850_update_bits(c->dma_top + TOP_CTRL, TOP_CTRL_IP_PROCESSING,
			 TOP_CTRL_IP_PROCESSING);
	writel(0x1f0 | (0x1f0 << 16), c->dma_top + TOP_SRAM_SPLIT);
	e850_update_bits(c->dma_top + TOP_MATRIX, GENMASK(4, 0), 0);
	writel(0x20, c->dma_top + TOP_MAX_MO);
	writel(0x1, c->dma_top + TOP_CLK_CTRL);
}

static void e850_dma_abort(struct e850_csis *c)
{
	u32 val;

	writel(0, c->wdma + DMA_CTRL);
	writel(CMN_ABORT_REQ, c->wdma_cmn + DMA_CMN_CTRL);
	if (readl_poll_timeout_atomic(c->wdma_cmn + DMA_INT_SRC, val,
				      val & DMA_INT_ABORT_DONE, 10, 10000))
		dev_warn(c->dev, "WDMA abort timeout (src=0x%08x)\n", val);
	writel(0, c->wdma_cmn + DMA_CMN_CTRL);
	writel(DMA_INT_CH0, c->wdma_cmn + DMA_INT_SRC);
	writel(0, c->wdma + DMA_FCNTSEQ);
}

static int e850_stream_on(struct e850_csis *c)
{
	u32 val;
	int i, ret;

	ret = e850_csis_reset(c);
	if (ret) {
		dev_err(c->dev, "CSIS software reset timeout\n");
		return ret;
	}

	spin_lock_irq(&c->slock);
	c->fill_slot = -1;
	c->read_slot = -1;
	c->read_overrun = false;
	c->ready_mask = 0;
	c->seq = 0;
	c->pending_flags = 0;
	memset(&c->stats, 0, sizeof(c->stats));
	spin_unlock_irq(&c->slock);

	/* D-PHY: isolation bypass, reset release, analog/digital tuning. */
	e850_phy_power(c, true);
	e850_phy_config(c);

	/* csi_hw_s_lane(): lane count is zero-based. */
	e850_update_bits(c->csis + CSIS_CMN_CTRL,
			 CMN_LANE_NUMBER | CMN_INTERLEAVE_MODE,
			 FIELD_PREP(CMN_LANE_NUMBER, c->lanes - 1));
	e850_update_bits(c->csis + CSIS_PHY_CMN_CTRL, PHY_ENABLE_DAT,
			 FIELD_PREP(PHY_ENABLE_DAT, GENMASK(c->lanes - 1, 0)));
	e850_update_bits(c->csis + CSIS_DBG_OPTION_SUITE,
			 DBG_PACKED_PIXEL_MODE, DBG_PACKED_PIXEL_MODE);

	/* csi_hw_s_config(): channel 0 captures the selected VC/DT. */
	val = FIELD_PREP(CFG_VIRTUAL_CHANNEL, c->vc) |
	      FIELD_PREP(CFG_DATAFORMAT, c->data_type) |
	      FIELD_PREP(CFG_PIXEL_MODE, 1);
	writel(val, c->csis + CSIS_ISP_CONFIG_CH0);
	writel(FIELD_PREP(RESOL_VRESOL, c->lines) |
	       FIELD_PREP(RESOL_HRESOL, c->line_bytes),
	       c->csis + CSIS_ISP_RESOL_CH0);

	writel(CSIS_IRQ_MASK0, c->csis + CSIS_INT_MSK0);
	writel(CSIS_IRQ_MASK1, c->csis + CSIS_INT_MSK1);
	writel(~0U, c->csis + CSIS_INT_SRC0);
	writel(~0U, c->csis + CSIS_INT_SRC1);

	/* csi_hw_s_config_dma(): 8-bit data uses packet storage, 1D DMA. */
	writel(DMA_FMT_STORAGE_PACKET | DMA_FMT_DIM_1D |
	       (c->byteswap ? DMA_FMT_BYTESWAP : 0), c->wdma + DMA_FMT);
	writel(0, c->wdma + DMA_FCNTSEQ);
	for (i = 0; i < DMA_NUM_SLOTS; i++)
		writel(lower_32_bits(c->bufs[i].dma), c->wdma + DMA_ADDR(i));
	writel(GENMASK(DMA_NUM_SLOTS - 1, 0), c->wdma + DMA_FCNTSEQ);

	/* csi_hw_s_config_dma_cmn(): RAW8/USER -> OTF format 3, PRL path. */
	writel(OTF_FORMAT_8BIT, c->wdma_cmn + DMA_OTF_FORMAT);
	e850_update_bits(c->wdma_cmn + DMA_DATA_CTRL, DATA_INPUT_PATH_PRL,
			 DATA_INPUT_PATH_PRL);
	writel(DMA_INT_CH0, c->wdma_cmn + DMA_INT_SRC);
	e850_update_bits(c->wdma_cmn + DMA_INT_MASK, DMA_INT_CH0, DMA_INT_CH0);

	writel(DMA_CTRL_ENABLE | DMA_CTRL_UPDT_PTR_EN, c->wdma + DMA_CTRL);
	e850_dma_top_config(c);

	/* csi_hw_enable() */
	writel(UPD_SDW_ALL, c->csis + CSIS_UPD_SDW);
	e850_update_bits(c->csis + CSIS_CMN_CTRL, CMN_PHY_SEL, 0);
	e850_update_bits(c->csis + CSIS_PHY_CMN_CTRL, PHY_ENABLE_CLK,
			 PHY_ENABLE_CLK);
	e850_update_bits(c->csis + CSIS_CMN_CTRL, CMN_CSI_EN, CMN_CSI_EN);

	WRITE_ONCE(c->streaming, true);
	dev_info(c->dev,
		 "stream on: %u lane(s) @ %u Mbps, settle %u, VC%u DT 0x%02x, %ux%u B\n",
		 c->lanes, c->lane_mbps, c->settle, c->vc, c->data_type,
		 c->line_bytes, c->lines);
	return 0;
}

static void e850_stream_off(struct e850_csis *c)
{
	WRITE_ONCE(c->streaming, false);

	e850_update_bits(c->wdma_cmn + DMA_INT_MASK, DMA_INT_CH0, 0);
	writel(0, c->csis + CSIS_INT_MSK0);
	writel(0, c->csis + CSIS_INT_MSK1);

	e850_dma_abort(c);

	/* csi_hw_disable() */
	e850_update_bits(c->csis + CSIS_PHY_CMN_CTRL,
			 PHY_ENABLE_CLK | PHY_ENABLE_DAT, 0);
	e850_update_bits(c->csis + CSIS_CMN_CTRL, CMN_CSI_EN, 0);
	e850_csis_reset(c);
	e850_phy_power(c, false);

	wake_up_interruptible(&c->wq);
	dev_info(c->dev, "stream off: %llu frames, %llu delivered, %llu dropped\n",
		 c->stats.frames, c->stats.delivered, c->stats.dropped);
}

static irqreturn_t e850_csis_irq(int irq, void *data)
{
	struct e850_csis *c = data;
	u32 src0, src1, lost_fs, lost_fe, vres, hres;

	src0 = readl(c->csis + CSIS_INT_SRC0) & readl(c->csis + CSIS_INT_MSK0);
	src1 = readl(c->csis + CSIS_INT_SRC1) & readl(c->csis + CSIS_INT_MSK1);
	if (!src0 && !src1)
		return IRQ_NONE;

	lost_fs = readl(c->csis + CSIS_ERR_LOST_FS);
	lost_fe = readl(c->csis + CSIS_ERR_LOST_FE);
	vres = readl(c->csis + CSIS_ERR_VRESOL);
	hres = readl(c->csis + CSIS_ERR_HRESOL);
	writel(lost_fs, c->csis + CSIS_ERR_LOST_FS);
	writel(lost_fe, c->csis + CSIS_ERR_LOST_FE);
	writel(vres, c->csis + CSIS_ERR_VRESOL);
	writel(hres, c->csis + CSIS_ERR_HRESOL);
	writel(src0, c->csis + CSIS_INT_SRC0);
	writel(src1, c->csis + CSIS_INT_SRC1);

	spin_lock(&c->slock);
	c->stats.csis_errors++;
	c->stats.last_int_src0 = src0;
	c->stats.last_int_src1 = src1;
	if (src0 & (SRC0_ERR_CRC | SRC0_ERR_ECC | SRC0_ERR_ID))
		c->stats.crc_ecc++;
	if (src0 & SRC0_ERR_OVER)
		c->stats.overflow++;
	if (src1 & SRC1_ERR_LOST_FS)
		c->stats.lost_fs++;
	if (src1 & SRC1_ERR_LOST_FE)
		c->stats.lost_fe++;
	if (src1 & (SRC1_HRESOL_MISMATCH | SRC1_VRESOL_MISMATCH))
		c->stats.resol_mismatch++;
	c->pending_flags |= E850_DVS_FLAG_CSIS_ERR;
	spin_unlock(&c->slock);

	dev_dbg_ratelimited(c->dev,
			    "CSIS err src0=0x%08x src1=0x%08x fs=%x fe=%x v=%x h=%x\n",
			    src0, src1, lost_fs, lost_fe, vres, hres);
	return IRQ_HANDLED;
}

static void e850_frame_start(struct e850_csis *c)
{
	u32 ptr = FIELD_GET(ACT_FRAMEPTR, readl(c->wdma + DMA_ACT_CTRL));
	int slot = ptr < DMA_NUM_SLOTS ? ptr : c->seq % DMA_NUM_SLOTS;

	/* The previous frame never reached FRM_END: it is incomplete. */
	if (c->fill_slot >= 0) {
		c->stats.dropped++;
		c->pending_flags |= E850_DVS_FLAG_DROPPED;
	}

	/* The WDMA now overwrites @slot: an unread frame there is lost. */
	if (c->ready_mask & BIT(slot)) {
		c->ready_mask &= ~BIT(slot);
		c->stats.dropped++;
		c->pending_flags |= E850_DVS_FLAG_DROPPED;
	}
	if (slot == c->read_slot)
		c->read_overrun = true;
	c->fill_slot = slot;
}

static void e850_frame_end(struct e850_csis *c)
{
	struct e850_csis_buf *b;

	if (c->fill_slot < 0)
		return;
	b = &c->bufs[c->fill_slot];
	b->ts_ns = ktime_get_ns();
	b->seq = c->seq++;
	b->flags = c->pending_flags;
	b->hw_frame = readl(c->csis + CSIS_FRM_CNT_CH0);
	c->pending_flags = 0;
	c->ready_mask |= BIT(c->fill_slot);
	c->fill_slot = -1;
	c->stats.frames++;
}

static irqreturn_t e850_dma_irq(int irq, void *data)
{
	struct e850_csis *c = data;
	u32 src = readl(c->wdma_cmn + DMA_INT_SRC) &
		  readl(c->wdma_cmn + DMA_INT_MASK) & DMA_INT_CH0;
	bool wake = false;

	if (!src)
		return IRQ_NONE;
	writel(src, c->wdma_cmn + DMA_INT_SRC);

	spin_lock(&c->slock);
	c->stats.last_dma_src = src;
	if (src & (DMA_INT_ERROR | DMA_INT_OTF_OVERLAP)) {
		c->stats.dma_errors++;
		c->pending_flags |= E850_DVS_FLAG_DMA_ERR;
	}
	/*
	 * A frame end and the next frame start may be latched together;
	 * the end always belongs to the frame already in flight.
	 */
	if (src & DMA_INT_FRM_END) {
		e850_frame_end(c);
		wake = true;
	}
	if (src & DMA_INT_FRM_START)
		e850_frame_start(c);
	spin_unlock(&c->slock);

	if (wake)
		wake_up_interruptible(&c->wq);
	return IRQ_HANDLED;
}

/* Oldest completed slot, or -1. Caller holds slock. */
static int e850_pick_ready(struct e850_csis *c)
{
	int i, best = -1;

	for (i = 0; i < DMA_NUM_SLOTS; i++) {
		if (!(c->ready_mask & BIT(i)))
			continue;
		if (best < 0 || (s32)(c->bufs[i].seq - c->bufs[best].seq) < 0)
			best = i;
	}
	return best;
}

static struct e850_csis *to_csis(struct file *f)
{
	return container_of(f->private_data, struct e850_csis, misc);
}

static int e850_fop_open(struct inode *inode, struct file *f)
{
	struct e850_csis *c = to_csis(f);
	int ret = 0;

	mutex_lock(&c->lock);
	if (c->opened)
		ret = -EBUSY;
	else
		c->opened = true;
	mutex_unlock(&c->lock);
	return ret ? ret : nonseekable_open(inode, f);
}

static int e850_fop_release(struct inode *inode, struct file *f)
{
	struct e850_csis *c = to_csis(f);

	mutex_lock(&c->lock);
	if (c->streaming)
		e850_stream_off(c);
	c->opened = false;
	mutex_unlock(&c->lock);
	return 0;
}

static ssize_t e850_fop_read(struct file *f, char __user *ubuf, size_t len,
			     loff_t *ppos)
{
	struct e850_csis *c = to_csis(f);
	struct e850_dvs_frame_hdr hdr = { .magic = E850_DVS_FRAME_MAGIC };
	struct e850_csis_buf *b;
	bool overrun;
	int slot, ret;

	if (len < sizeof(hdr) + c->buf_size)
		return -EINVAL;

	for (;;) {
		spin_lock_irq(&c->slock);
		slot = e850_pick_ready(c);
		if (slot < 0) {
			spin_unlock_irq(&c->slock);
			if (!READ_ONCE(c->streaming))
				return 0;
			if (f->f_flags & O_NONBLOCK)
				return -EAGAIN;
			ret = wait_event_interruptible(c->wq,
						       READ_ONCE(c->ready_mask) ||
						       !READ_ONCE(c->streaming));
			if (ret)
				return ret;
			continue;
		}
		b = &c->bufs[slot];
		c->ready_mask &= ~BIT(slot);
		c->read_slot = slot;
		c->read_overrun = false;
		hdr.seq = b->seq;
		hdr.ts_ns = b->ts_ns;
		hdr.flags = b->flags;
		hdr.hw_frame = b->hw_frame;
		spin_unlock_irq(&c->slock);

		if (copy_to_user(ubuf + sizeof(hdr), b->cpu, c->buf_size))
			ret = -EFAULT;
		else
			ret = 0;

		spin_lock_irq(&c->slock);
		overrun = c->read_overrun;
		c->read_slot = -1;
		if (overrun && !ret) {
			/* The WDMA restarted on this slot mid-copy. */
			c->stats.dropped++;
			c->pending_flags |= E850_DVS_FLAG_DROPPED;
		} else if (!ret) {
			c->stats.delivered++;
		}
		spin_unlock_irq(&c->slock);

		if (ret)
			return ret;
		if (!overrun)
			break;
	}

	hdr.bytes = c->buf_size;
	if (copy_to_user(ubuf, &hdr, sizeof(hdr)))
		return -EFAULT;
	return sizeof(hdr) + c->buf_size;
}

static __poll_t e850_fop_poll(struct file *f, poll_table *wait)
{
	struct e850_csis *c = to_csis(f);
	__poll_t mask = 0;

	poll_wait(f, &c->wq, wait);
	if (READ_ONCE(c->ready_mask))
		mask |= EPOLLIN | EPOLLRDNORM;
	else if (!READ_ONCE(c->streaming))
		mask |= EPOLLHUP;
	return mask;
}

static long e850_fop_ioctl(struct file *f, unsigned int cmd, unsigned long arg)
{
	struct e850_csis *c = to_csis(f);
	void __user *uarg = (void __user *)arg;
	struct e850_dvs_stats stats;
	struct e850_dvs_info info;
	int ret = 0;

	switch (cmd) {
	case E850_DVS_IOC_G_INFO:
		memset(&info, 0, sizeof(info));
		info.csis_id = c->id;
		info.csis_version = c->version;
		info.lanes = c->lanes;
		info.lane_mbps = c->lane_mbps;
		info.settle = c->settle;
		info.data_type = c->data_type;
		info.vc = c->vc;
		info.line_bytes = c->line_bytes;
		info.lines = c->lines;
		info.buf_size = c->buf_size;
		info.num_bufs = DMA_NUM_SLOTS;
		return copy_to_user(uarg, &info, sizeof(info)) ? -EFAULT : 0;
	case E850_DVS_IOC_STREAMON:
		mutex_lock(&c->lock);
		ret = c->streaming ? -EBUSY : e850_stream_on(c);
		mutex_unlock(&c->lock);
		return ret;
	case E850_DVS_IOC_STREAMOFF:
		mutex_lock(&c->lock);
		if (c->streaming)
			e850_stream_off(c);
		mutex_unlock(&c->lock);
		return 0;
	case E850_DVS_IOC_G_STATS:
		spin_lock_irq(&c->slock);
		stats = c->stats;
		spin_unlock_irq(&c->slock);
		stats.phy_status = readl(c->csis + CSIS_PHY_STATUS);
		return copy_to_user(uarg, &stats, sizeof(stats)) ? -EFAULT : 0;
	default:
		return -ENOTTY;
	}
}

static const struct file_operations e850_csis_fops = {
	.owner = THIS_MODULE,
	.open = e850_fop_open,
	.release = e850_fop_release,
	.read = e850_fop_read,
	.poll = e850_fop_poll,
	.unlocked_ioctl = e850_fop_ioctl,
	.compat_ioctl = compat_ptr_ioctl,
	.llseek = noop_llseek,
};

struct e850_reg_name {
	u16 off;
	const char *name;
};

static const struct e850_reg_name e850_csis_regs[] = {
	{ CSIS_VERSION, "CSIS_VERSION" }, { CSIS_CMN_CTRL, "CSIS_CMN_CTRL" },
	{ CSIS_INT_MSK0, "INT_MSK0" }, { CSIS_INT_SRC0, "INT_SRC0" },
	{ CSIS_INT_MSK1, "INT_MSK1" }, { CSIS_INT_SRC1, "INT_SRC1" },
	{ CSIS_FS_INT_SRC, "FS_INT_SRC" }, { CSIS_FE_INT_SRC, "FE_INT_SRC" },
	{ CSIS_ISP_CONFIG_CH0, "ISP_CONFIG_CH0" },
	{ CSIS_ISP_RESOL_CH0, "ISP_RESOL_CH0" },
	{ CSIS_FRM_CNT_CH0, "FRM_CNT_CH0" },
	{ CSIS_ERR_LOST_FS, "ERR_LOST_FS" }, { CSIS_ERR_LOST_FE, "ERR_LOST_FE" },
	{ CSIS_ERR_VRESOL, "ERR_VRESOL" }, { CSIS_ERR_HRESOL, "ERR_HRESOL" },
	{ CSIS_DBG_OPTION_SUITE, "DBG_OPTION_SUITE" },
	{ CSIS_PHY_STATUS, "PHY_STATUS" }, { CSIS_PHY_CMN_CTRL, "PHY_CMN_CTRL" },
};

static const struct e850_reg_name e850_wdma_regs[] = {
	{ DMA_CTRL, "DMA0_CTRL" }, { DMA_FMT, "DMA0_FMT" },
	{ DMA_ADDR(0), "DMA0_ADDR1" }, { DMA_ACT_CTRL, "DMA0_ACT_CTRL" },
	{ DMA_FCNTSEQ, "DMA0_FCNTSEQ" },
};

static const struct e850_reg_name e850_wdma_cmn_regs[] = {
	{ DMA_DATA_CTRL, "DMA_DATA_CTRL" }, { DMA_INT_MASK, "DMA_INT_MASK" },
	{ DMA_INT_SRC, "DMA_INT_SRC" }, { DMA_CMN_CTRL, "DMA_CMN_CTRL" },
	{ DMA_ERR_CODE, "DMA_ERR_CODE" }, { DMA_OTF_FORMAT, "OTF_FORMAT" },
};

static void e850_dump(struct seq_file *s, const char *blk, void __iomem *base,
		      const struct e850_reg_name *r, size_t n)
{
	size_t i;

	for (i = 0; i < n; i++)
		seq_printf(s, "%-5s %-18s [0x%04x] 0x%08x\n", blk, r[i].name,
			   r[i].off, readl(base + r[i].off));
}

static int e850_regs_show(struct seq_file *s, void *unused)
{
	struct e850_csis *c = s->private;

	e850_dump(s, "csis", c->csis, e850_csis_regs,
		  ARRAY_SIZE(e850_csis_regs));
	e850_dump(s, "wdma", c->wdma, e850_wdma_regs,
		  ARRAY_SIZE(e850_wdma_regs));
	e850_dump(s, "cmn", c->wdma_cmn, e850_wdma_cmn_regs,
		  ARRAY_SIZE(e850_wdma_cmn_regs));
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(e850_regs);

static int e850_stats_show(struct seq_file *s, void *unused)
{
	struct e850_csis *c = s->private;
	struct e850_dvs_stats st;

	spin_lock_irq(&c->slock);
	st = c->stats;
	spin_unlock_irq(&c->slock);

	seq_printf(s, "streaming      %d\n", READ_ONCE(c->streaming));
	seq_printf(s, "frames         %llu\n", st.frames);
	seq_printf(s, "delivered      %llu\n", st.delivered);
	seq_printf(s, "dropped        %llu\n", st.dropped);
	seq_printf(s, "csis_errors    %llu\n", st.csis_errors);
	seq_printf(s, "dma_errors     %llu\n", st.dma_errors);
	seq_printf(s, "lost_fs        %llu\n", st.lost_fs);
	seq_printf(s, "lost_fe        %llu\n", st.lost_fe);
	seq_printf(s, "crc_ecc        %llu\n", st.crc_ecc);
	seq_printf(s, "overflow       %llu\n", st.overflow);
	seq_printf(s, "resol_mismatch %llu\n", st.resol_mismatch);
	seq_printf(s, "last_int_src0  0x%08x\n", st.last_int_src0);
	seq_printf(s, "last_int_src1  0x%08x\n", st.last_int_src1);
	seq_printf(s, "last_dma_src   0x%08x\n", st.last_dma_src);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(e850_stats);

/* Shared blocks (DMA top, PHY reset, DMA1 common) must not be claimed. */
static void __iomem *e850_map(struct platform_device *pdev, const char *name)
{
	struct resource *res;

	res = platform_get_resource_byname(pdev, IORESOURCE_MEM, name);
	if (!res) {
		dev_err(&pdev->dev, "missing reg \"%s\"\n", name);
		return IOMEM_ERR_PTR(-EINVAL);
	}
	return devm_ioremap(&pdev->dev, res->start, resource_size(res)) ?:
	       IOMEM_ERR_PTR(-ENOMEM);
}

static int e850_parse_dt(struct e850_csis *c)
{
	struct device_node *np = c->dev->of_node;
	u32 max_lanes;

	if (of_property_read_u32(np, "samsung,csis-id", &c->id) || c->id > 2)
		return dev_err_probe(c->dev, -EINVAL, "invalid samsung,csis-id\n");
	/* CSIS0/1 have 4-lane D-PHYs, CSIS2 a 2-lane one. */
	max_lanes = c->id == 2 ? 2 : 4;

	c->lanes = 4;
	of_property_read_u32(np, "nrv,data-lanes", &c->lanes);
	if (!c->lanes || c->lanes > max_lanes)
		return dev_err_probe(c->dev, -EINVAL,
				     "CSIS%u supports 1..%u lanes, not %u\n",
				     c->id, max_lanes, c->lanes);

	c->lane_mbps = 1000;
	of_property_read_u32(np, "nrv,lane-rate-mbps", &c->lane_mbps);
	if (lane_mbps)
		c->lane_mbps = lane_mbps;
	c->settle = e850_find_settle(c->lane_mbps);
	of_property_read_u32(np, "samsung,hs-settle", &c->settle);
	if (settle)
		c->settle = settle;

	c->data_type = MIPI_DT_RAW8;
	of_property_read_u32(np, "nrv,data-type", &c->data_type);
	c->vc = 0;
	of_property_read_u32(np, "nrv,virtual-channel", &c->vc);
	c->line_bytes = 2048;
	of_property_read_u32(np, "nrv,line-bytes", &c->line_bytes);
	c->lines = 128;
	of_property_read_u32(np, "nrv,lines-per-frame", &c->lines);
	c->byteswap = of_property_read_bool(np, "nrv,dma-byteswap");

	if (c->data_type > 0x3f || c->vc > 3 || !c->line_bytes ||
	    c->line_bytes > 0xffff || !c->lines || c->lines > 0xffff)
		return dev_err_probe(c->dev, -EINVAL, "invalid stream format\n");

	if ((u64)c->line_bytes * c->lines > SZ_64M)
		return dev_err_probe(c->dev, -EINVAL,
				     "frame buffer larger than 64 MiB\n");
	c->buf_size = PAGE_ALIGN(c->line_bytes * c->lines);
	return 0;
}

static int e850_csis_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct clk_bulk_data *clks;
	struct e850_csis *c;
	int i, irq, ret;

	c = devm_kzalloc(dev, sizeof(*c), GFP_KERNEL);
	if (!c)
		return -ENOMEM;
	c->dev = dev;
	mutex_init(&c->lock);
	spin_lock_init(&c->slock);
	init_waitqueue_head(&c->wq);
	platform_set_drvdata(pdev, c);

	ret = e850_parse_dt(c);
	if (ret)
		return ret;

	c->csis = devm_platform_ioremap_resource_byname(pdev, "csis");
	if (IS_ERR(c->csis))
		return PTR_ERR(c->csis);
	c->phy = devm_platform_ioremap_resource_byname(pdev, "phy");
	if (IS_ERR(c->phy))
		return PTR_ERR(c->phy);
	c->wdma = devm_platform_ioremap_resource_byname(pdev, "wdma");
	if (IS_ERR(c->wdma))
		return PTR_ERR(c->wdma);
	c->wdma_cmn = e850_map(pdev, "wdma-cmn");
	if (IS_ERR(c->wdma_cmn))
		return PTR_ERR(c->wdma_cmn);
	c->dma_top = e850_map(pdev, "dma-top");
	if (IS_ERR(c->dma_top))
		return PTR_ERR(c->dma_top);
	c->phy_rst = e850_map(pdev, "phy-reset");
	if (IS_ERR(c->phy_rst))
		return PTR_ERR(c->phy_rst);

	c->pmu = syscon_regmap_lookup_by_phandle_args(dev->of_node,
						      "samsung,pmu-syscon",
						      1, &c->pmu_iso);
	if (IS_ERR(c->pmu))
		return dev_err_probe(dev, PTR_ERR(c->pmu),
				     "samsung,pmu-syscon lookup failed\n");

	/* CSIS ACLK, CSIS_DMA and (without an IOMMU) the SysMMU bypass clock. */
	ret = devm_clk_bulk_get_all_enabled(dev, &clks);
	if (ret < 0)
		return dev_err_probe(dev, ret, "failed to enable clocks\n");

	ret = dma_set_mask_and_coherent(dev, DMA_BIT_MASK(32));
	if (ret)
		return dev_err_probe(dev, ret, "no 32-bit DMA\n");
	for (i = 0; i < DMA_NUM_SLOTS; i++) {
		c->bufs[i].cpu = dmam_alloc_coherent(dev, c->buf_size,
						     &c->bufs[i].dma,
						     GFP_KERNEL);
		if (!c->bufs[i].cpu)
			return dev_err_probe(dev, -ENOMEM,
					     "cannot allocate %u x %u B DMA buffers\n",
					     DMA_NUM_SLOTS, c->buf_size);
	}

	irq = platform_get_irq_byname(pdev, "csis");
	if (irq < 0)
		return irq;
	ret = devm_request_irq(dev, irq, e850_csis_irq, 0, dev_name(dev), c);
	if (ret)
		return ret;
	/* DMA1 serves both CSIS1 and CSIS2. */
	irq = platform_get_irq_byname(pdev, "dma");
	if (irq < 0)
		return irq;
	ret = devm_request_irq(dev, irq, e850_dma_irq, IRQF_SHARED,
			       dev_name(dev), c);
	if (ret)
		return ret;

	c->version = readl(c->csis + CSIS_VERSION);

	snprintf(c->name, sizeof(c->name), "e850-csis%u", c->id);
	c->misc.minor = MISC_DYNAMIC_MINOR;
	c->misc.name = c->name;
	c->misc.fops = &e850_csis_fops;
	c->misc.parent = dev;
	ret = misc_register(&c->misc);
	if (ret)
		return ret;

	c->debugfs = debugfs_create_dir(c->name, NULL);
	debugfs_create_file("regs", 0444, c->debugfs, c, &e850_regs_fops);
	debugfs_create_file("stats", 0444, c->debugfs, c, &e850_stats_fops);

	dev_info(dev,
		 "CSIS%u version 0x%08x, /dev/%s, %u x %u B buffers (%u x %u)\n",
		 c->id, c->version, c->name, DMA_NUM_SLOTS, c->buf_size,
		 c->line_bytes, c->lines);
	return 0;
}

static void e850_csis_remove(struct platform_device *pdev)
{
	struct e850_csis *c = platform_get_drvdata(pdev);

	debugfs_remove_recursive(c->debugfs);
	misc_deregister(&c->misc);
	mutex_lock(&c->lock);
	if (c->streaming)
		e850_stream_off(c);
	mutex_unlock(&c->lock);
}

static const struct of_device_id e850_csis_of_match[] = {
	{ .compatible = "nrv,e850-csis-dma" },
	{}
};
MODULE_DEVICE_TABLE(of, e850_csis_of_match);

static struct platform_driver e850_csis_driver = {
	.probe = e850_csis_probe,
	.remove = e850_csis_remove,
	.driver = {
		.name = "e850-csis-dma",
		.of_match_table = e850_csis_of_match,
	},
};
module_platform_driver(e850_csis_driver);

MODULE_DESCRIPTION("Exynos850 CSIS WDMA raw capture for the NRV RC1S DVS");
MODULE_LICENSE("GPL");
