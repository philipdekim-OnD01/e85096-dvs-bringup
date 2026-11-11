/*
 * Samsung S5KRC1S DVS CIS bring-up driver for Exynos Pablo
 *
 * Minimal first-stage implementation:
 *  - CCI/I2C probe
 *  - known transport registers
 *  - 2-lane selection
 *  - standby / stream control via 0x3000
 *
 * Conventional exposure/gain controls are intentionally not implemented.
 */

#include <linux/i2c.h>
#include <linux/slab.h>
#include <linux/delay.h>
#include <linux/module.h>
#include <linux/of.h>
#include <media/v4l2-subdev.h>

#include <exynos-is-sensor.h>
#include "is-core.h"
#include "is-device-sensor.h"
#include "is-device-sensor-peri.h"
#include "is-cis-s5krc1s.h"
#include "is-cis-s5krc1s-setA.h"
#include "is-helper-i2c.h"

#define SENSOR_NAME "S5KRC1S"

static const struct v4l2_subdev_ops subdev_ops;

/*
 * Applied at each mode change, after the setfile; 0 = setfile value. In a sparse scene the sensor sends
 * one packet per flush timeout, so a MIPI frame (= one VC0 buffer) takes packets x flush_us (11.25).
 * CSIS takes the short frame: the VRESOL check is off (csis-v1_2-err-clear.patch).
 */
static uint packets;
module_param(packets, uint, 0644);
MODULE_PARM_DESC(packets, "MIPI_NUM_OF_PACKETS 0x391A/B per frame, 1..127; CSIS needs it = the DT mode height (11.25)");

/* MIPI packets per frame = VC0 lines of each DT mode (module_s5krc1s.dtsi mode0, mode1); one setfile for all */
static const u16 sensor_s5krc1s_mode_lines[] = { S5KRC1S_BRINGUP_PACKET_COUNT, 32 };
static uint flush_us;
module_param(flush_us, uint, 0644);
MODULE_PARM_DESC(flush_us, "MIPI FIFO flush timeout 0x3912/13 in us (fine count 0x3911 = 1 us), 1..1023");
/* scan blank time = DTAG_FRM_MARGIN x 2^12 event clocks (manual Table 14); the setfile's 2 gives a 494 us scan period */
static uint margin;
module_param(margin, uint, 0644);
MODULE_PARM_DESC(margin, "DTAG_FRM_MARGIN 0x321D/E, blank after each scan in 2^12 event clocks, 1..65535 (11.29)");
/* register tests without a rebuild, written last: "30a7=01,321e=12" (hex, 8-bit registers) */
static char *regs;
module_param(regs, charp, 0644);
MODULE_PARM_DESC(regs, "extra writes at each mode change, \"addr=val,...\" in hex (11.29)");
/*
 * D-PHY PLL 0x30A5-0x30A7 for the link tests (docs/DVS-PLL-TESTS.md), written at each mode change (sensor in
 * standby, before the CSIS stream on) after the setfile, before regs. Empty or "setfile" keeps the setfile's
 * 00/75/00, which is experimental (11.29: CRC 0 for 3 min), not a validated setting (11.46).
 */
struct s5krc1s_pll {
	const char *name;
	u8 a5, a6, a7;
};
static const struct s5krc1s_pll sensor_s5krc1s_pll[] = {
	{ "experimental_00_75_00", 0x00, 0x75, 0x00 },
	{ "recommended_00_8b_b0", 0x00, 0x8b, 0xb0 },	/* 2244 Mbps/lane by formula; Jetson 64 h CRC 0 (Minsu) */
	{ "alternative_00_8b_f8", 0x00, 0x8b, 0xf8 },	/* 2298 Mbps/lane by formula */
	{ "default_00_8d_00", 0x00, 0x8d, 0x00 },	/* vendor Delta_10_2000FPS, 2496: ~4800 CRC/s here (11.28) */
};
static char *pll;
module_param(pll, charp, 0644);
MODULE_PARM_DESC(pll, "D-PHY PLL at each mode change: setfile (default, 00/75/00 experimental), experimental_00_75_00, "
		 "recommended_00_8b_b0, alternative_00_8b_f8, default_00_8d_00 (11.46)");
static char pll_live[128];
module_param_string(pll_live, pll_live, sizeof(pll_live), 0444);
MODULE_PARM_DESC(pll_live, "last 0x30A5-0x30A7 readback with P M S and the formula rates (calculated, not measured)");

static int sensor_s5krc1s_write8(struct is_cis *cis, u16 reg, u8 value)
{
	if (!cis || !cis->client)
		return -EINVAL;

	return is_sensor_write8(cis->client, reg, value);
}

static int sensor_s5krc1s_read8(struct is_cis *cis, u16 reg, u8 *value)
{
	if (!cis || !cis->client || !value)
		return -EINVAL;

	return is_sensor_read8(cis->client, reg, value);
}

/*
 * Read back the D-PHY PLL word: P [18:13], M [12:3], S [2:0]. rate = 24 MHz x M / P (Minsu, S = 0 settings),
 * rate_s = 24 x M / (P x 2^S) (Karim's S divider: experimental, not in the datasheet). Both are calculations;
 * the readback shows what the sensor holds, not the PLL frequency or the lane rate.
 */
static void sensor_s5krc1s_log_pll(struct is_cis *cis, const char *when)
{
	char s[sizeof(pll_live)];
	u8 r[3];
	u32 word, p, m, sd;
	int i;

	for (i = 0; i < 3; i++)
		if (sensor_s5krc1s_read8(cis, 0x30A5 + i, &r[i]))
			break;
	if (i < 3) {
		snprintf(s, sizeof(s), "read 0x%04x failed", 0x30A5 + i);
	} else {
		word = r[0] << 16 | r[1] << 8 | r[2];
		p = word >> 13 & 0x3f;
		m = word >> 3 & 0x3ff;
		sd = word & 7;
		if (p)
			snprintf(s, sizeof(s), "%02x %02x %02x word 0x%05x P=%u M=%u S=%u rate=%u rate_s=%u",
				 r[0], r[1], r[2], word, p, m, sd, 24 * m / p, 24 * m / (p << sd));
		else
			snprintf(s, sizeof(s), "%02x %02x %02x word 0x%05x P=0 M=%u S=%u rate=none",
				 r[0], r[1], r[2], word, m, sd);
	}
	kernel_param_lock(THIS_MODULE);
	strscpy(pll_live, s, sizeof(pll_live));
	kernel_param_unlock(THIS_MODULE);
	probe_info("%s: D-PHY PLL %s: %s (Mbps/lane calculated, not measured)\n", __func__, when, s);
}

/* the pll profile; empty or "setfile" keeps the setfile's word, an unknown name fails the mode change */
static int sensor_s5krc1s_set_pll(struct is_cis *cis)
{
	const struct s5krc1s_pll *p = NULL;
	bool keep;
	int i, ret;

	kernel_param_lock(THIS_MODULE);
	keep = !pll || sysfs_streq(pll, "") || sysfs_streq(pll, "setfile");	/* echo > pll leaves "\n" */
	for (i = 0; !keep && !p && i < ARRAY_SIZE(sensor_s5krc1s_pll); i++)
		if (sysfs_streq(pll, sensor_s5krc1s_pll[i].name))
			p = &sensor_s5krc1s_pll[i];
	if (!keep && !p)
		err("%s: unknown pll profile \"%s\"\n", __func__, pll);
	kernel_param_unlock(THIS_MODULE);
	if (keep)
		return 0;
	if (!p)
		return -EINVAL;

	ret = sensor_s5krc1s_write8(cis, 0x30A5, p->a5);
	ret = ret ?: sensor_s5krc1s_write8(cis, 0x30A6, p->a6);
	ret = ret ?: sensor_s5krc1s_write8(cis, 0x30A7, p->a7);
	probe_info("%s: D-PHY PLL profile %s: %02x %02x %02x written (%d)\n",
		   __func__, p->name, p->a5, p->a6, p->a7, ret);
	return ret;
}

static int sensor_s5krc1s_cis_init(struct v4l2_subdev *subdev)
{
	struct is_cis *cis;
	u8 mode;

	FIMC_BUG(!subdev);

	cis = (struct is_cis *)v4l2_get_subdevdata(subdev);
	FIMC_BUG(!cis);
	FIMC_BUG(!cis->cis_data);

	memset(cis->cis_data, 0, sizeof(cis_shared_data));

	cis->cis_data->cur_width = SENSOR_S5KRC1S_MAX_WIDTH;
	cis->cis_data->cur_height = SENSOR_S5KRC1S_MAX_HEIGHT;
	cis->cis_data->max_fps = 30;
	cis->cis_data->stream_on = false;
	cis->need_mode_change = false;

	/*
	 * The manual has no chip ID register; a SEL_MODE read only shows the sensor answers.
	 * As in the OV5647 driver, only log: the power sequence may not have run yet.
	 */
	if (sensor_s5krc1s_read8(cis, S5KRC1S_REG_SEL_MODE, &mode))
		err("%s: no I2C response at 0x%02x\n", __func__, cis->client->addr);
	else
		probe_info("%s: S5KRC1S 960x720 DVS, SEL_MODE(0x3000)=0x%02x\n", __func__, mode);
	return 0;
}

static int sensor_s5krc1s_cis_log_status(struct v4l2_subdev *subdev)
{
	struct is_cis *cis;
	u8 val = 0;
	int ret = 0;

	FIMC_BUG(!subdev);

	cis = (struct is_cis *)v4l2_get_subdevdata(subdev);
	FIMC_BUG(!cis);

	if (!sensor_s5krc1s_read8(cis, S5KRC1S_REG_CCI_ADDRESS, &val))
		pr_info("[S5KRC1S] CCI_ADDRESS(0x3024)=0x%02x\n", val);

	if (!sensor_s5krc1s_read8(cis, S5KRC1S_REG_SEL_MODE, &val))
		pr_info("[S5KRC1S] SEL_MODE(0x3000)=0x%02x\n", val);

	if (!sensor_s5krc1s_read8(cis, S5KRC1S_REG_CSI2_DI, &val))
		pr_info("[S5KRC1S] CSI2_DI(0x3901)=0x%02x\n", val);

	if (!sensor_s5krc1s_read8(cis, S5KRC1S_REG_CSI2_WC_LOW, &val))
		pr_info("[S5KRC1S] WC_LOW(0x3902)=0x%02x\n", val);

	if (!sensor_s5krc1s_read8(cis, S5KRC1S_REG_CSI2_WC_HIGH, &val))
		pr_info("[S5KRC1S] WC_HIGH(0x3903)=0x%02x\n", val);

	if (!sensor_s5krc1s_read8(cis, S5KRC1S_REG_CSI2_EMBD_DI, &val))
		pr_info("[S5KRC1S] EMBD_DI(0x3906)=0x%02x\n", val);

	if (!sensor_s5krc1s_read8(cis, S5KRC1S_REG_MIPI_TIMEOUT, &val))
		pr_info("[S5KRC1S] MIPI_TIMEOUT(0x3910)=0x%02x\n", val);

	ret = sensor_s5krc1s_read8(cis, S5KRC1S_REG_MIPI_MODE, &val);
	if (!ret)
		pr_info("[S5KRC1S] MIPI_MODE(0x3915)=0x%02x\n", val);

	if (!sensor_s5krc1s_read8(cis, S5KRC1S_REG_PACKET_NUM_LOW, &val))
		pr_info("[S5KRC1S] PACKET_NUM_LOW(0x391A)=0x%02x\n", val);

	if (!sensor_s5krc1s_read8(cis, S5KRC1S_REG_PACKET_NUM_HIGH, &val))
		pr_info("[S5KRC1S] PACKET_NUM_HIGH(0x391B)=0x%02x\n", val);

	if (!sensor_s5krc1s_read8(cis, S5KRC1S_REG_GROUP_EVENT, &val))
		pr_info("[S5KRC1S] GROUP_EVENT(0x392C)=0x%02x\n", val);

	return ret;
}

static int sensor_s5krc1s_cis_set_global_setting(struct v4l2_subdev *subdev)
{
	int ret;

	FIMC_BUG(!subdev);

	/* the RPi5 init tool waits 0.5 s after RSTN high before writing the setfile */
	msleep(500);

	ret = sensor_cis_set_registers(
		subdev,
		sensor_s5krc1s_setfile_A_Global,
		ARRAY_SIZE(sensor_s5krc1s_setfile_A_Global));
	if (ret < 0)
		err("%s: global setfile write failed (%d)\n", __func__, ret);
	else
		probe_info("%s: Delta_10_2000FPS written\n", __func__);

	return ret;
}

static int sensor_s5krc1s_set_two_lane(struct is_cis *cis)
{
	u8 value;
	int ret;

	ret = sensor_s5krc1s_read8(cis, S5KRC1S_REG_MIPI_MODE, &value);
	if (ret < 0)
		return ret;

	/*
	 * 0x3915: [5:4] mipi_ck_mode, [3] SOL/EOL, [2] SOF/EOF, [1] 2-lane, [0] 1-lane.
	 * Only the lane bits change: the setfile's 0x24 (clock always on, SOF/EOF) -> 0x26.
	 */
	value &= ~0x03;
	value |= 0x02;

	ret = sensor_s5krc1s_write8(cis, S5KRC1S_REG_MIPI_MODE, value);
	if (!ret)
		probe_info("%s: 0x3915=0x%02x (2-lane)\n", __func__, value);

	return ret;
}

static int sensor_s5krc1s_cis_mode_change(struct v4l2_subdev *subdev, u32 mode)
{
	struct is_cis *cis;
	int ret;
	u32 lines;
	const char *s;
	unsigned int a, v;
	int n;

	FIMC_BUG(!subdev);

	cis = (struct is_cis *)v4l2_get_subdevdata(subdev);
	FIMC_BUG(!cis);
	FIMC_BUG(!cis->cis_data);

	if (mode >= ARRAY_SIZE(sensor_s5krc1s_mode_lines))
		return -EINVAL;

	ret = sensor_s5krc1s_write8(cis,
				    S5KRC1S_REG_SEL_MODE,
				    S5KRC1S_MODE_STANDBY);
	if (ret < 0)
		return ret;

	ret = sensor_cis_set_registers(
		subdev,
		sensor_s5krc1s_setfiles_A[0],
		sensor_s5krc1s_setfile_A_sizes[0]);
	if (ret < 0)
		return ret;

	ret = sensor_s5krc1s_set_two_lane(cis);
	if (ret < 0)
		return ret;

	lines = packets >= 1 && packets <= S5KRC1S_BRINGUP_PACKET_COUNT ? packets : sensor_s5krc1s_mode_lines[mode];
	ret = sensor_s5krc1s_write8(cis, 0x391A, lines & 0xff);
	ret = ret ?: sensor_s5krc1s_write8(cis, 0x391B, lines >> 8);
	if (ret < 0)
		return ret;
	if (flush_us >= 1 && flush_us <= 1023) {
		ret = sensor_s5krc1s_write8(cis, 0x3912, flush_us & 0xff);
		ret = ret ?: sensor_s5krc1s_write8(cis, 0x3913, flush_us >> 8);
		if (ret < 0)
			return ret;
	}
	if (margin >= 1 && margin <= 0xffff) {
		ret = sensor_s5krc1s_write8(cis, 0x321D, margin >> 8);
		ret = ret ?: sensor_s5krc1s_write8(cis, 0x321E, margin & 0xff);
		if (ret < 0)
			return ret;
	}
	ret = sensor_s5krc1s_set_pll(cis);
	if (ret < 0)
		return ret;
	/* the lock keeps a sysfs write from freeing the string under us; parsing stops at the first bad entry */
	kernel_param_lock(THIS_MODULE);
	s = regs;
	while (ret >= 0 && s && sscanf(s, "%x=%x%n", &a, &v, &n) == 2 && a <= 0xffff && v <= 0xff) {
		ret = sensor_s5krc1s_write8(cis, a, v);
		probe_info("%s: 0x%04x=0x%02x (regs) %d\n", __func__, a, v, ret);
		s += n;
		s = *s == ',' ? s + 1 : NULL;
	}
	kernel_param_unlock(THIS_MODULE);
	if (ret < 0)
		return ret;

	/* Bring-up readback: verify programmed CSI-2 transport registers. */
	sensor_s5krc1s_cis_log_status(subdev);
	sensor_s5krc1s_log_pll(cis, "mode change");

	cis->cis_data->cur_width = 8192;
	cis->cis_data->cur_height = sensor_s5krc1s_mode_lines[mode];

	probe_info("%s: mode %u, RAW8 8192x%u transport, %u packets\n",
		   __func__, mode, sensor_s5krc1s_mode_lines[mode], lines);

	return 0;
}

static int sensor_s5krc1s_cis_stream_on(struct v4l2_subdev *subdev)
{
	struct is_cis *cis;
	int ret;

	FIMC_BUG(!subdev);

	cis = (struct is_cis *)v4l2_get_subdevdata(subdev);
	FIMC_BUG(!cis);
	FIMC_BUG(!cis->cis_data);

	ret = sensor_s5krc1s_write8(cis,
				    S5KRC1S_REG_SEL_MODE,
				    S5KRC1S_MODE_STREAM);
	if (ret < 0)
		return ret;

	cis->cis_data->stream_on = true;
	probe_info("%s: stream ON (0x3000=0x02)\n", __func__);

	/* Bring-up readback: confirm stream state after 0x3000 write. */
	sensor_s5krc1s_cis_log_status(subdev);
	sensor_s5krc1s_log_pll(cis, "stream on");

	return 0;
}

static int sensor_s5krc1s_cis_stream_off(struct v4l2_subdev *subdev)
{
	struct is_cis *cis;
	int ret;

	FIMC_BUG(!subdev);

	cis = (struct is_cis *)v4l2_get_subdevdata(subdev);
	FIMC_BUG(!cis);
	FIMC_BUG(!cis->cis_data);

	ret = sensor_s5krc1s_write8(cis,
				    S5KRC1S_REG_SEL_MODE,
				    S5KRC1S_MODE_STANDBY);
	if (ret < 0)
		return ret;

	cis->cis_data->stream_on = false;
	probe_info("%s: stream OFF (0x3000=0x00)\n", __func__);

	return 0;
}

static struct is_cis_ops cis_ops = {
	.cis_init = sensor_s5krc1s_cis_init,
	.cis_log_status = sensor_s5krc1s_cis_log_status,
	.cis_set_global_setting = sensor_s5krc1s_cis_set_global_setting,
	.cis_mode_change = sensor_s5krc1s_cis_mode_change,
	.cis_stream_on = sensor_s5krc1s_cis_stream_on,
	.cis_stream_off = sensor_s5krc1s_cis_stream_off,
};

/* Not __init: as a module, probe can run again after -EPROBE_DEFER. */
static int cis_s5krc1s_probe(struct i2c_client *client,
				    const struct i2c_device_id *id)
{
	struct is_core *core;
	struct is_device_sensor *device;
	struct is_device_sensor_peri *sensor_peri;
	struct v4l2_subdev *subdev_cis;
	struct is_cis *cis;
	struct device_node *dnode;
	u32 sensor_instance;
	const char *setfile = NULL;
	int ret;

	FIMC_BUG(!client);
	FIMC_BUG(!is_dev);

	core = (struct is_core *)dev_get_drvdata(is_dev);
	if (!core) {
		probe_info("%s: core is not yet probed\n", __func__);
		return -EPROBE_DEFER;
	}

	dnode = client->dev.of_node;

	ret = of_property_read_u32(dnode, "id", &sensor_instance);
	if (ret) {
		err("%s: id property read failed (%d)\n", __func__, ret);
		return ret;
	}

	device = &core->sensor[sensor_instance];

	sensor_peri = find_peri_by_cis_id(device, SENSOR_NAME_S5KRC1S);
	if (!sensor_peri) {
		probe_info("%s: sensor peri is not yet probed\n", __func__);
		return -EPROBE_DEFER;
	}

	cis = &sensor_peri->cis;

	subdev_cis = kzalloc(sizeof(struct v4l2_subdev), GFP_KERNEL);
	if (!subdev_cis)
		return -ENOMEM;

	sensor_peri->subdev_cis = subdev_cis;

	cis->id = SENSOR_NAME_S5KRC1S;
	cis->subdev = subdev_cis;
	cis->device = sensor_instance;
	cis->client = client;
	sensor_peri->module->client = client;
	cis->ctrl_delay = N_PLUS_ONE_FRAME;

	cis->cis_data = kzalloc(sizeof(cis_shared_data), GFP_KERNEL);
	if (!cis->cis_data) {
		kfree(subdev_cis);
		return -ENOMEM;
	}

	cis->cis_ops = &cis_ops;

	/* Framework ABI field only; S5KRC1S DVS data is not Bayer. */
	cis->bayer_order = OTF_INPUT_ORDER_BAYER_GR_BG;
	cis->use_dgain = false;
	cis->hdr_ctrl_by_again = false;

	if (!of_property_read_string(dnode, "setfile", &setfile))
		probe_info("%s: setfile=%s\n", __func__, setfile);

	v4l2_i2c_subdev_init(subdev_cis, client, &subdev_ops);
	v4l2_set_subdevdata(subdev_cis, cis);
	v4l2_set_subdev_hostdata(subdev_cis, device);
	snprintf(subdev_cis->name, V4L2_SUBDEV_NAME_SIZE,
		 "cis-subdev.%d", cis->id);

	probe_info("%s: S5KRC1S bound to sensor instance %u, addr=0x%02x\n",
		   __func__, sensor_instance, client->addr);

	return 0;
}

static const struct of_device_id sensor_cis_s5krc1s_match[] = {
	{
		.compatible = "samsung,exynos-is-cis-s5krc1s",
	},
	{},
};
MODULE_DEVICE_TABLE(of, sensor_cis_s5krc1s_match);

static const struct i2c_device_id sensor_cis_s5krc1s_idt[] = {
	{ SENSOR_NAME, 0 },
	{},
};
MODULE_DEVICE_TABLE(i2c, sensor_cis_s5krc1s_idt);

static struct i2c_driver sensor_cis_s5krc1s_driver = {
	.probe = cis_s5krc1s_probe,
	.driver = {
		.name = SENSOR_NAME,
		.owner = THIS_MODULE,
		.of_match_table = sensor_cis_s5krc1s_match,
		.suppress_bind_attrs = true,
	},
	.id_table = sensor_cis_s5krc1s_idt,
};

module_i2c_driver(sensor_cis_s5krc1s_driver);

MODULE_DESCRIPTION("Samsung S5KRC1S DVS CIS bring-up driver");
MODULE_LICENSE("GPL");
