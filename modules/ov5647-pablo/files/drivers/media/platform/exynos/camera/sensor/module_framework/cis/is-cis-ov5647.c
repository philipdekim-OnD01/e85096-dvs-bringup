// SPDX-License-Identifier: GPL-2.0
/*
 * OmniVision OV5647 CIS driver for the Exynos Pablo camera framework (E850-96 CN4).
 *
 * First bring-up stage, following the S5KRC1S bring-up driver's minimal shape:
 *  - probe binds the I2C client to the Pablo sensor peri (sensor_id SENSOR_NAME_OV5647)
 *  - init reads the chip ID (0x300A/0x300B = 0x5647)
 *  - global setting: output pads on, MIPI lanes parked in LP-11
 *  - mode change: mainline Linux register tables (RAW10, 2 lanes), virtual channel 0
 *  - stream on/off: same register sequence as mainline ov5647_stream_on/off
 *
 * Exposure and gain controls are not implemented; the framework skips NULL cis_ops.
 * Register tables and sequences come from mainline drivers/media/i2c/ov5647.c (v6.6).
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
#include "is-helper-i2c.h"
#include "is-cis-ov5647.h"
#include "is-cis-ov5647-setA.h"

#define SENSOR_NAME "OV5647"

/* Per-mode timing from mainline ov5647_modes[]; index matches sensor_ov5647_setfiles_A. */
static const struct {
	u32 width, height, pclk, hts, vts, fps;
} ov5647_modes[] = {
	{  640,  480, 55000000, 1852, 0x1f8, 60 },
	{ 1296,  972, 81666700, 1896, 0x59b, 46 },
	{ 1920, 1080, 81666700, 2416, 0x450, 30 },
	{ 2592, 1944, 87500000, 2844, 0x7b0, 15 },
};

static const struct v4l2_subdev_ops subdev_ops;

static struct is_cis *to_cis(struct v4l2_subdev *subdev)
{
	return (struct is_cis *)v4l2_get_subdevdata(subdev);
}

static int ov5647_write(struct is_cis *cis, u16 reg, u8 val)
{
	return is_sensor_write8(cis->client, reg, val);
}

static int ov5647_read(struct is_cis *cis, u16 reg, u8 *val)
{
	return is_sensor_read8(cis->client, reg, val);
}

static void ov5647_set_mode_data(cis_shared_data *d, u32 mode)
{
	d->cur_width = ov5647_modes[mode].width;
	d->cur_height = ov5647_modes[mode].height;
	d->pclk = ov5647_modes[mode].pclk;
	d->line_length_pck = ov5647_modes[mode].hts;
	d->frame_length_lines = ov5647_modes[mode].vts;
	d->max_fps = ov5647_modes[mode].fps;
	d->frame_time = 1000 / ov5647_modes[mode].fps;
	d->min_frame_us_time = 1000000 / ov5647_modes[mode].fps;
	d->cur_frame_us_time = d->min_frame_us_time;
}

static int sensor_ov5647_cis_init(struct v4l2_subdev *subdev)
{
	struct is_cis *cis;
	u8 id_h = 0, id_l = 0;
	int ret;

	FIMC_BUG(!subdev);
	cis = to_cis(subdev);
	FIMC_BUG(!cis);
	FIMC_BUG(!cis->cis_data);

	memset(cis->cis_data, 0, sizeof(cis_shared_data));
	ov5647_set_mode_data(cis->cis_data, 0);
	cis->cis_data->stream_on = false;
	cis->need_mode_change = false;

	/* Logged, not enforced: when the framework calls init the power sequence may not
	 * have run yet, and a failed init would hide every later register write. */
	ret = ov5647_read(cis, OV5647_REG_CHIPID_H, &id_h);
	if (!ret)
		ret = ov5647_read(cis, OV5647_REG_CHIPID_L, &id_l);
	if (ret)
		err("%s: chip ID read failed (%d)\n", __func__, ret);
	else if (((id_h << 8) | id_l) != OV5647_CHIP_ID)
		err("%s: unexpected chip ID 0x%02x%02x\n", __func__, id_h, id_l);
	else
		probe_info("%s: OV5647 chip ID 0x%02x%02x\n", __func__, id_h, id_l);
	return 0;
}

static int sensor_ov5647_cis_log_status(struct v4l2_subdev *subdev)
{
	static const u16 regs[] = {
		OV5647_REG_SW_STANDBY, OV5647_REG_PAD_OUT, OV5647_REG_FRAME_OFF_NUMBER,
		OV5647_REG_MIPI_CTRL00, OV5647_REG_MIPI_CTRL14,
		0x3034, 0x3035, 0x3036,		/* PLL: bit mode, system clock divider, multiplier */
		0x3808, 0x3809, 0x380a, 0x380b,	/* output width / height */
	};
	struct is_cis *cis = to_cis(subdev);
	int i, ret = 0;
	u8 val;

	for (i = 0; i < ARRAY_SIZE(regs); i++) {
		ret = ov5647_read(cis, regs[i], &val);
		if (ret)
			break;
		pr_info("[OV5647] 0x%04x = 0x%02x\n", regs[i], val);
	}
	return ret;
}

static int sensor_ov5647_cis_set_global_setting(struct v4l2_subdev *subdev)
{
	int ret;

	FIMC_BUG(!subdev);
	ret = sensor_cis_set_registers(subdev, sensor_ov5647_setfile_A_Global,
				       ARRAY_SIZE(sensor_ov5647_setfile_A_Global));
	if (ret < 0)
		err("%s: global setting failed (%d)\n", __func__, ret);
	return ret;
}

static int sensor_ov5647_cis_mode_change(struct v4l2_subdev *subdev, u32 mode)
{
	struct is_cis *cis;
	u8 val;
	int ret;

	FIMC_BUG(!subdev);
	cis = to_cis(subdev);
	FIMC_BUG(!cis);
	FIMC_BUG(!cis->cis_data);

	if (mode >= ARRAY_SIZE(sensor_ov5647_setfiles_A)) {
		err("%s: invalid mode %u\n", __func__, mode);
		return -EINVAL;
	}

	ret = sensor_cis_set_registers(subdev, sensor_ov5647_setfiles_A[mode],
				       sensor_ov5647_setfile_A_sizes[mode]);
	if (ret < 0)
		return ret;

	/* Virtual channel 0 (MIPI_CTRL14[7:6]), as mainline ov5647_set_virtual_channel(sd, 0). */
	ret = ov5647_read(cis, OV5647_REG_MIPI_CTRL14, &val);
	if (!ret)
		ret = ov5647_write(cis, OV5647_REG_MIPI_CTRL14, val & 0x3f);
	if (ret < 0)
		return ret;

	ov5647_set_mode_data(cis->cis_data, mode);
	probe_info("%s: mode %u = %ux%u RAW10\n", __func__, mode,
		   ov5647_modes[mode].width, ov5647_modes[mode].height);
	return 0;
}

static int sensor_ov5647_cis_stream_on(struct v4l2_subdev *subdev)
{
	struct is_cis *cis;
	int ret;

	FIMC_BUG(!subdev);
	cis = to_cis(subdev);
	FIMC_BUG(!cis);

	/* Continuous MIPI clock: only BUS_IDLE, no clock-lane gating. */
	ret = ov5647_write(cis, OV5647_REG_MIPI_CTRL00, OV5647_MIPI_CTRL00_BUS_IDLE);
	if (!ret)
		ret = ov5647_write(cis, OV5647_REG_FRAME_OFF_NUMBER, 0x00);
	if (!ret)
		ret = ov5647_write(cis, OV5647_REG_PAD_OUT, 0x00);
	if (ret < 0)
		return ret;

	cis->cis_data->stream_on = true;
	probe_info("%s: stream on\n", __func__);
	sensor_ov5647_cis_log_status(subdev);
	return 0;
}

static int sensor_ov5647_cis_stream_off(struct v4l2_subdev *subdev)
{
	struct is_cis *cis;
	int ret;

	FIMC_BUG(!subdev);
	cis = to_cis(subdev);
	FIMC_BUG(!cis);

	ret = ov5647_write(cis, OV5647_REG_MIPI_CTRL00, OV5647_MIPI_CTRL00_STREAM_OFF);
	if (!ret)
		ret = ov5647_write(cis, OV5647_REG_FRAME_OFF_NUMBER, 0x0f);
	if (!ret)
		ret = ov5647_write(cis, OV5647_REG_PAD_OUT, 0x01);
	if (ret < 0)
		return ret;

	cis->cis_data->stream_on = false;
	probe_info("%s: stream off\n", __func__);
	return 0;
}

static struct is_cis_ops cis_ops = {
	.cis_init = sensor_ov5647_cis_init,
	.cis_log_status = sensor_ov5647_cis_log_status,
	.cis_set_global_setting = sensor_ov5647_cis_set_global_setting,
	.cis_mode_change = sensor_ov5647_cis_mode_change,
	.cis_stream_on = sensor_ov5647_cis_stream_on,
	.cis_stream_off = sensor_ov5647_cis_stream_off,
};

/* Not __init: as a module, probe can run again after -EPROBE_DEFER. */
static int cis_ov5647_probe(struct i2c_client *client, const struct i2c_device_id *id)
{
	struct is_core *core;
	struct is_device_sensor *device;
	struct is_device_sensor_peri *sensor_peri;
	struct v4l2_subdev *subdev_cis;
	struct is_cis *cis;
	u32 sensor_instance;
	int ret;

	FIMC_BUG(!client);
	FIMC_BUG(!is_dev);

	core = (struct is_core *)dev_get_drvdata(is_dev);
	if (!core) {
		probe_info("%s: core is not yet probed\n", __func__);
		return -EPROBE_DEFER;
	}

	ret = of_property_read_u32(client->dev.of_node, "id", &sensor_instance);
	if (ret) {
		err("%s: id property read failed (%d)\n", __func__, ret);
		return ret;
	}

	device = &core->sensor[sensor_instance];
	sensor_peri = find_peri_by_cis_id(device, SENSOR_NAME_OV5647);
	if (!sensor_peri) {
		probe_info("%s: sensor peri is not yet probed\n", __func__);
		return -EPROBE_DEFER;
	}

	cis = &sensor_peri->cis;
	subdev_cis = kzalloc(sizeof(*subdev_cis), GFP_KERNEL);
	if (!subdev_cis)
		return -ENOMEM;

	cis->cis_data = kzalloc(sizeof(cis_shared_data), GFP_KERNEL);
	if (!cis->cis_data) {
		kfree(subdev_cis);
		return -ENOMEM;
	}

	sensor_peri->subdev_cis = subdev_cis;
	cis->id = SENSOR_NAME_OV5647;
	cis->subdev = subdev_cis;
	cis->device = sensor_instance;
	cis->client = client;
	sensor_peri->module->client = client;
	cis->ctrl_delay = N_PLUS_ONE_FRAME;
	cis->cis_ops = &cis_ops;
	cis->bayer_order = OTF_INPUT_ORDER_BAYER_BG_GR;	/* mainline: MEDIA_BUS_FMT_SBGGR10_1X10 */
	cis->use_dgain = false;
	cis->hdr_ctrl_by_again = false;

	v4l2_i2c_subdev_init(subdev_cis, client, &subdev_ops);
	v4l2_set_subdevdata(subdev_cis, cis);
	v4l2_set_subdev_hostdata(subdev_cis, device);
	snprintf(subdev_cis->name, V4L2_SUBDEV_NAME_SIZE, "cis-subdev.%d", cis->id);

	probe_info("%s: OV5647 bound to sensor instance %u, addr 0x%02x\n",
		   __func__, sensor_instance, client->addr);
	return 0;
}

static const struct of_device_id sensor_cis_ov5647_match[] = {
	{ .compatible = "samsung,exynos-is-cis-ov5647" },
	{},
};
MODULE_DEVICE_TABLE(of, sensor_cis_ov5647_match);

static const struct i2c_device_id sensor_cis_ov5647_idt[] = {
	{ SENSOR_NAME, 0 },
	{},
};
MODULE_DEVICE_TABLE(i2c, sensor_cis_ov5647_idt);

static struct i2c_driver sensor_cis_ov5647_driver = {
	.probe = cis_ov5647_probe,
	.driver = {
		.name = SENSOR_NAME,
		.owner = THIS_MODULE,
		.of_match_table = sensor_cis_ov5647_match,
		.suppress_bind_attrs = true,
	},
	.id_table = sensor_cis_ov5647_idt,
};
module_i2c_driver(sensor_cis_ov5647_driver);

MODULE_DESCRIPTION("OmniVision OV5647 CIS driver for Exynos Pablo");
MODULE_LICENSE("GPL");
