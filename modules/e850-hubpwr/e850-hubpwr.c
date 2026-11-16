// SPDX-License-Identifier: GPL-2.0
/*
 * e850-hubpwr: power the E850-96 on-board USB hub (U16 LAN9514).
 *
 * The hub's supply VDD_LAN is PMIC S2MPU12 LDO24 (E850-96_Circuits_v1.0, sheets 14 and 15). The ERD3830 device
 * tree we boot calls LDO24 "vmmc" and hands it to the SD slot (dwmmc2), which keeps it off without a card, so the
 * hub never shows up on the host port and the U14 port power switch stays off. The board's own Android 10 DTBO
 * (rev 2) has LDO24 at 3.3 V, always on. Loading this module holds LDO24 on at 3.3 V; unloading lets it go.
 * The SD slot is unused on these boards (rootfs on eMMC).
 */
#include <linux/module.h>
#include <linux/regulator/consumer.h>

static char *supply = "vmmc";
module_param(supply, charp, 0444);
MODULE_PARM_DESC(supply, "regulator-name of S2MPU12 LDO24 in the running device tree");

static struct regulator *reg;

static int __init e850_hubpwr_init(void)
{
	int ret;

	reg = regulator_get(NULL, supply);	/* no consumer device: looked up by regulator-name */
	if (IS_ERR(reg))
		return PTR_ERR(reg);
	ret = regulator_set_voltage(reg, 3300000, 3300000);
	if (!ret)
		ret = regulator_enable(reg);
	pr_info("e850-hubpwr: %s at 3.3 V: %d (%d uV, %s)\n", supply, ret, regulator_get_voltage(reg),
		regulator_is_enabled(reg) ? "on" : "off");
	if (ret)
		regulator_put(reg);
	return ret;
}

static void __exit e850_hubpwr_exit(void)
{
	regulator_disable(reg);
	regulator_put(reg);
}

module_init(e850_hubpwr_init);
module_exit(e850_hubpwr_exit);
MODULE_DESCRIPTION("E850-96: hold PMIC LDO24 (LAN9514 hub supply) on at 3.3 V");
MODULE_LICENSE("GPL");
