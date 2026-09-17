// SPDX-License-Identifier: GPL-2.0-only
/*
 * UMP9620 PMIC watchdog: the bootloader arms it and, on Android, the PM co-processor firmware (loaded by the modem
 * stack) takes it over ("watchdog rstoff"). Mainline has no PM firmware, so the watchdog would power the board off
 * after ~5 minutes. Stop its counter and reset output. MU300 bring-up driver.
 */
#include <linux/bits.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>

#define WDT_LOAD_LOW	0x00
#define WDT_LOAD_HIGH	0x04
#define WDT_CTRL	0x08
#define WDT_CNT_LOW	0x18
#define WDT_CNT_HIGH	0x1c
#define WDT_LOCK	0x20
#define WDT_INT_EN	BIT(0)
#define WDT_CNT_EN	BIT(1)
#define WDT_RST_EN	BIT(3)
#define WDT_UNLOCK_KEY	0xe551

static int ump9620_wdt_off_probe(struct platform_device *pdev)
{
	struct regmap *map = dev_get_regmap(pdev->dev.parent, NULL);
	u32 base, ctrl, ll, lh, cl, ch;

	if (!map)
		return -EPROBE_DEFER;
	if (of_property_read_u32(pdev->dev.of_node, "reg", &base))
		return -EINVAL;

	regmap_read(map, base + WDT_CTRL, &ctrl);
	regmap_read(map, base + WDT_LOAD_LOW, &ll);
	regmap_read(map, base + WDT_LOAD_HIGH, &lh);
	regmap_read(map, base + WDT_CNT_LOW, &cl);
	regmap_read(map, base + WDT_CNT_HIGH, &ch);
	dev_info(&pdev->dev, "before: ctrl %#x load %u s count %u s\n", ctrl,
		 ((lh << 16) | ll) / 32768, ((ch << 16) | cl) / 32768);

	regmap_write(map, base + WDT_LOCK, WDT_UNLOCK_KEY);
	regmap_update_bits(map, base + WDT_CTRL, WDT_CNT_EN | WDT_RST_EN | WDT_INT_EN, 0);
	regmap_write(map, base + WDT_LOCK, (u16)~WDT_UNLOCK_KEY);

	regmap_read(map, base + WDT_CTRL, &ctrl);
	dev_info(&pdev->dev, "after: ctrl %#x (counter and reset disabled)\n", ctrl);
	return 0;
}

static const struct of_device_id ump9620_wdt_off_match[] = {
	{ .compatible = "sprd,ump9620-wdt" },
	{ }
};

static struct platform_driver ump9620_wdt_off_driver = {
	.probe = ump9620_wdt_off_probe,
	.driver = { .name = "ump9620-wdt-off", .of_match_table = ump9620_wdt_off_match },
};
builtin_platform_driver(ump9620_wdt_off_driver);
MODULE_LICENSE("GPL");
