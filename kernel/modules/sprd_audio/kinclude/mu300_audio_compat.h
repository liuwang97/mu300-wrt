/* SPDX-License-Identifier: GPL-2.0 */
/*
 * MU300: what the 5.4 vendor audio drivers use and mainline renamed or removed (upstream/modules/sprd_audio).
 * Included first by every file of the stack (Kbuild); one place instead of a hunk in every driver.
 */
#ifndef MU300_AUDIO_COMPAT_H
#define MU300_AUDIO_COMPAT_H
#include <linux/version.h>
#include <linux/io.h>
#include <linux/string.h>
#include <linux/gpio.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/of_gpio.h>
#include <linux/device.h>
#include <linux/vmalloc.h>
#include <linux/mod_devicetable.h>

#ifndef ioremap_nocache
#define ioremap_nocache ioremap
#endif
/* strlcpy() is gone; no caller here uses its return value */
#define strlcpy(d, s, n) strscpy(d, s, n)

#include <linux/device.h>
#include <dt-bindings/gpio/gpio.h>

/*
 * The legacy GPIO calls of the vendor code that mainline no longer has, in terms of what it has: from
 * Enceka/linux-lts-e5's include/linux/soc/sprd/unisoc_gpio_compat.h (GPL-2.0-only).
 */
static inline void mu300_devm_gpio_free(void *gpio)
{
	gpio_free((unsigned long)gpio);
}

static inline int mu300_devm_gpio_request(struct device *dev, unsigned int gpio, const char *label)
{
	int ret = gpio_request(gpio, label);

	if (ret)
		return ret;
	return devm_add_action_or_reset(dev, mu300_devm_gpio_free, (void *)(unsigned long)gpio);
}
#define devm_gpio_request(dev, gpio, label)	mu300_devm_gpio_request(dev, gpio, label)

enum of_gpio_flags {
	OF_GPIO_ACTIVE_LOW = 0x1,
};

static inline int mu300_of_get_named_gpio_flags(const struct device_node *np, const char *list_name,
						int index, enum of_gpio_flags *flags)
{
	struct of_phandle_args args;
	int ret;

	if (flags)
		*flags = 0;
	ret = of_parse_phandle_with_args(np, list_name, "#gpio-cells", index, &args);
	if (ret)
		return ret;
	of_node_put(args.np);
	if (flags && args.args_count > 1 && (args.args[1] & GPIO_ACTIVE_LOW))
		*flags = OF_GPIO_ACTIVE_LOW;
	return of_get_named_gpio(np, list_name, index);
}
#define of_get_named_gpio_flags(np, name, index, flags) mu300_of_get_named_gpio_flags(np, name, index, flags)
#define of_get_gpio_flags(np, index, flags) mu300_of_get_named_gpio_flags(np, "gpios", index, flags)
#define gpio_set_debounce(gpio, us) gpiod_set_debounce(gpio_to_desc(gpio), us)

/* the legacy GPIO flag bits: an output is direction 0 now, the initial level bit 1 */
#ifndef GPIOF_DIR_OUT
#define GPIOF_DIR_OUT	0
#endif
#ifndef GPIOF_INIT_LOW
#define GPIOF_INIT_LOW	0
#endif
#ifndef GPIOF_INIT_HIGH
#define GPIOF_INIT_HIGH	GPIOF_OUT_INIT_HIGH
#endif

/* the wakeup source of 5.4 came in four calls; mainline registers and unregisters it in one */
#define wakeup_source_create(name)	wakeup_source_register(NULL, name)
#define wakeup_source_add(ws)		do { } while (0)
#define wakeup_source_remove(ws)	do { } while (0)
#define wakeup_source_destroy(ws)	wakeup_source_unregister(ws)

/* ASoC */
#define snd_soc_component_read32(c, r) snd_soc_component_read(c, r)

/*
 * 7.x made the DAPM context opaque and renamed the accessors (6.18 has both the old names and the context in the
 * structs); the component's PCM constructor and destructor went back to pcm_new/pcm_free with 6.18's signatures.
 */
#if LINUX_VERSION_CODE >= KERNEL_VERSION(7, 0, 0)
#include <sound/soc.h>
#define snd_soc_component_get_dapm(c)	snd_soc_component_to_dapm(c)
#define snd_soc_dapm_kcontrol_dapm(k)	snd_soc_dapm_kcontrol_to_dapm(k)
#define snd_soc_kcontrol_component(k)	((struct snd_soc_component *)snd_kcontrol_chip(k))
#define pcm_construct			pcm_new
#define pcm_destruct			pcm_free
/* strncpy() is gone: the shim of the WCN modules' mu300_compat.h (same semantics, padding included) */
static inline char *mu300_audio_strncpy(char *dst, const char *src, size_t n)
{
	size_t i = 0;

	for (; i < n && src[i]; i++)
		dst[i] = src[i];
	for (; i < n; i++)
		dst[i] = '\0';
	return dst;
}
#define strncpy(dst, src, n) mu300_audio_strncpy(dst, src, n)
#endif

#endif
