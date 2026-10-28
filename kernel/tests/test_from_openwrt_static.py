"""Checks over the kernel tree without running it, moved here from the root filesystem tree's test_static (they read
this tree only): the init's order, the mainline port's SD and eMMC hosts, the U30 Air's charger, suspend, the config's
module set, the Wi-Fi driver's fixes."""
import os
import re
import tempfile
import unittest
from pathlib import Path

from helpers import TOP


class Static(unittest.TestCase):
    def test_init_restores_androids_slot(self):
        # every restore goes through restore_android, so Linux on slot a returns to Android on b
        init = (TOP / 'kernel' / 'initramfs' / 'init').read_text()
        self.assertNotIn('restore_slot_a', init)
        self.assertNotIn('slot_suffix=_b/androidboot.slot_suffix=_a', init)
        self.assertIn('sleep 300', init)

    def test_init_finds_partitions_after_the_modules(self):
        # the eMMC driver is one of the vendor modules: misc and boot_b cannot be found before they are loaded
        init = (TOP / 'kernel' / 'initramfs' / 'init').read_text()
        calls = [l.strip() for l in init.splitlines() if l.strip() in ('load_vendor_modules', 'find_partitions')]
        self.assertEqual(calls, ['load_vendor_modules', 'find_partitions'])

    def test_mainline_keeps_the_sd_host(self):
        # the SD card can hold the Linux filesystem: the port lets the card slot's host probe next to the eMMC, and
        # still keeps any other sdhci host (the stock DT's sdio_wifi; Wi-Fi is on PCIe) out
        port = (TOP / 'kernel' / 'port' / 'install.py').read_text()
        self.assertIn('MU300: only the eMMC and the card slot', port)
        self.assertIn('if (!of_property_read_bool(pdev->dev.of_node, "non-removable") &&', port)
        self.assertIn('of_property_match_string(pdev->dev.of_node, "sprd,name", "sdio_sd") < 0)', port)
        self.assertIn('MU300: CD GPIO deferred', port)
        # a deferral that is only masked leaves the rest of mmc_of_parse() undone (UHS modes, no-sdio, no-mmc): the
        # unresolvable cd-gpios is dropped and the parse run again
        self.assertIn('of_remove_property(pdev->dev.of_node, cd)', port)
        self.assertIn("'MU300: only the eMMC and the card slot', 'MU300: CD GPIO deferred', "
                      "'of_remove_property(pdev->dev.of_node, cd)'", port)
        self.assertRegex((TOP / 'kernel' / 'make-bundle.sh').read_text(),
                         r"printf 'sdcard\\nlinux-slot\\ngpt-root\\n' > \"\$W/b/features\"")

    def test_mainline_keeps_the_emmc_at_mmc0(self):
        # Both hosts probe asynchronously and the stock DT has no mmc aliases, so the card slot could take mmc0 and
        # leave mmcblk1 to the eMMC: init then never found the card and rebooted at 300 s. The card
        # slot's host waits until the eMMC's host is added.
        port = (TOP / 'kernel' / 'port' / 'install.py').read_text()
        self.assertIn('MU300: the eMMC is mmc0', port)
        self.assertIn('static bool sdhci_sprd_emmc_added;', port)
        self.assertIn('for_each_compatible_node(np, NULL, "sprd,sdhci-r11")', port)
        # the deferral sits right after the host filter, before sdhci_pltfm_init() allocates the host index
        self.assertIn('\\t    sdhci_sprd_emmc_pending(pdev->dev.of_node))\n\\t\\treturn -EPROBE_DEFER;\n', port)
        # and says what that costs: without a working eMMC host the card slot never binds
        self.assertIn("no SD root without a working eMMC host", port)
        self.assertLess(port.index("t.replace(filt, filt + '''"), port.index('add_old = '))
        # the flag is set only once the eMMC's host is added
        self.assertIn("add_old = '\\tret = __sdhci_add_host(host);\\n\\tif (ret)\\n\\t\\tgoto err_cleanup_host;\\n'",
                      port)
        self.assertIn('\\t\\tWRITE_ONCE(sdhci_sprd_emmc_added, true);', port)
        # and the build stops when the edit did not apply
        self.assertIn("'MU300: the eMMC is mmc0', 'WRITE_ONCE(sdhci_sprd_emmc_added, true);'", port)

    def test_mainline_emmc_timing_falls_back(self):
        """An eMMC whose HS400ES selection fails at a cold boot is initialised again at
        HS200, then HS/DDR52, then HS, instead of being given up after mmc_rescan's attempts at HS400ES; a boot whose
        HS400ES works drops nothing. The test hook in sdhci-sprd is off by default and touches only CMD13 while a new
        card is set up at HS400ES; a test build sets it through build.sh's MU300_CMDLINE_EXTRA, never a release."""
        p = (TOP / 'kernel' / 'patches' / '0021-mmc-HS400ES-failure-falls-back-to-HS200.patch').read_text()
        self.assertEqual(re.findall(r'(?m)^\+\+\+ (\S+)', p), ['b/drivers/mmc/core/mmc.c', 'b/drivers/mmc/host/sdhci-sprd.c'])
        core = p[p.index('+++ b/drivers/mmc/core/mmc.c'):p.index('--- a/drivers/mmc/host/sdhci-sprd.c')]
        # the ladder: HS400ES drops HS400 with it, then HS200, then DDR52; a removed card drops nothing
        for s in ('+\tif (err == -ENOMEDIUM)\n+\t\treturn;',
                  '+\tif (card->mmc_avail_type & EXT_CSD_CARD_TYPE_HS400ES) {\n'
                  '+\t\thost->caps2 &= ~(MMC_CAP2_HS400_ES | MMC_CAP2_HS400);',
                  '+\t\thost->caps2 &= ~(MMC_CAP2_HS200 | MMC_CAP2_HS400);',
                  '+\t} else if (card->mmc_avail_type & EXT_CSD_CARD_TYPE_DDR_52) {\n+\t\thost->caps &= ~MMC_CAP_DDR;',
                  '+\tpr_warn("%s: %s failed (%d), the next initialisation uses %s\\n",',
                  '+timing_failed:\n+\tmmc_timing_fallback(card, err);\n free_card:'):
            self.assertIn(s, core)
        # exactly the four failures of the timing selection go through it, nothing before or after
        self.assertEqual(len(re.findall(r'(?m)^-\t+goto free_card;$', core)), 4)
        self.assertEqual(len(re.findall(r'(?m)^\+\t+goto timing_failed;$', core)), 4)
        self.assertEqual(core.count('goto timing_failed;'), 4)
        # the card type is chosen again from the host caps at every initialisation, also of a known card: what the
        # caps drop relies on (kept in the context of the patch would be too far; the header says it)
        self.assertIn('"since host caps could have been changed when debugging"', p)
        hook = p[p.index('+++ b/drivers/mmc/host/sdhci-sprd.c'):]
        for s in ('+static unsigned int fail_hs400es;\n+module_param(fail_hs400es, uint, 0644);',
                  '+\tif (unlikely(sprd_host->fail_hs400es) && mmc->ios.enhanced_strobe &&\n'
                  '+\t    !mmc->card && mrq->cmd->opcode == MMC_SEND_STATUS) {\n+\t\tmrq->cmd->error = -EILSEQ;',
                  '+\tsprd_host->fail_hs400es = fail_hs400es > 0;'):
            self.assertIn(s, hook)
        self.assertNotRegex(hook, r'(?m)^\+static unsigned int fail_hs400es\s*=')
        build = (TOP / 'kernel' / 'build.sh').read_text()
        self.assertIn('--set-str CMDLINE "$c $MU300_CMDLINE_EXTRA"', build)
        # after the check of the config file (which would refuse a changed CMDLINE), before the build
        self.assertLess(build.index('config options not taken'), build.index('MU300_CMDLINE_EXTRA'))
        self.assertLess(build.index('MU300_CMDLINE_EXTRA'), build.index('make O="$O" -j"$(nproc)" Image'))
        for wf in ('release.yml', 'mainline.yml'):
            self.assertNotIn('MU300_CMDLINE_EXTRA', (TOP / '.github' / 'workflows' / wf).read_text())
        self.assertNotIn('fail_hs400es', (TOP / 'kernel' / 'mu300-mainline.config').read_text())

    def test_mainline_drives_the_u30_air_charger(self):
        # Without a driver the charger kept what Android left in it: charging off (CHG_CONFIG 0), 500 mA input, and
        # the battery drained while plugged in. bq256xx drives the vendor node "ti,bq2560x_chg" now.
        cfg = (TOP / 'kernel' / 'mu300-mainline.config').read_text()
        for k in ('CONFIG_CHARGER_BQ256XX=y', 'CONFIG_REGMAP_I2C=y', 'CONFIG_I2C_SPRD=y', 'CONFIG_EXTCON_USB_GPIO=y'):
            self.assertRegex(cfg, rf'(?m)^{k}$')
        port = (TOP / 'kernel' / 'port' / 'install.py').read_text()
        # the edit itself, on 7.2's designated I2C table, twice (idempotent)
        code = port[port.index("bp = os.path.join(tree, 'drivers/power/supply/bq256xx_charger.c')"):
                    port.index('# Every edit above is a text substitution')]
        of = ('static const struct of_device_id bq256xx_of_match[] = {\n'
              '\t{ .compatible = "ti,bq25601", .data = &bq256xx_chip_info_tbl[BQ25601] },\n\t{}\n};\n')
        acpi = ('static const struct acpi_device_id bq256xx_acpi_match[] = {\n'
                '\t{ "bq25601", (kernel_ulong_t)&bq256xx_chip_info_tbl[BQ25601] },\n\t{}\n};\n')
        for entry, want in (('{ .name = "bq25601", .driver_data = (kernel_ulong_t)&bq256xx_chip_info_tbl[BQ25601] }',
                             '\t{ .name = "bq2560x_chg", .driver_data = (kernel_ulong_t)&bq256xx_chip_info_tbl[BQ25601] },\n'),):
            with tempfile.TemporaryDirectory() as tree:
                f = Path(tree, 'drivers/power/supply/bq256xx_charger.c')
                f.parent.mkdir(parents=True)
                f.write_text('static const struct i2c_device_id bq256xx_i2c_ids[] = {\n\t' + entry + ',\n\t{}\n};\n'
                             'MODULE_DEVICE_TABLE(i2c, bq256xx_i2c_ids);\n' + of + acpi)
                for _ in range(2):
                    exec(code, {'os': os, 'tree': tree})
                t = f.read_text()
                self.assertEqual(t.count(want), 1, t)
                self.assertEqual(t.count('"ti,bq2560x_chg", .data = &bq256xx_chip_info_tbl[BQ25601]'), 1, t)
                self.assertNotIn('bq2560x_chg', t[t.index('acpi_device_id'):])   # the ACPI table stays as it was
        # and the build stops when it did not apply
        self.assertIn("('drivers/power/supply/bq256xx_charger.c', ['\"ti,bq2560x_chg\"", port)
        # the driver's handling of that node: no stale cache, Android's 1.98 A and 4.208 V at most, the chip's own
        # input current from the PMIC's charger detection on every VBUS change from the extcon, the chip's own only as
        # a fallback, charging on unless userspace said "N/A"
        p9 = (TOP / 'kernel' / 'patches' / '0009-power-bq256xx-drive-the-U30-Air-charger.patch').read_text()
        for s in ('.cache_type = REGCACHE_NONE', 'BQ2560X_CHG_ICHG_MAX_uA\t\t1980000', 'BQ2560X_CHG_VBATREG_MAX_uV\t4208000',
                  'BQ256XX_IINDET_EN', 'devm_extcon_register_notifier(dev, bq->vbus_edev, EXTCON_USB',
                  '"linux,extcon-usb-gpio"', 'bq->init_data.iindpm = 500000;', 'WRITE_ONCE(bq->chg_off',
                  'off ? 0 : BQ256XX_CHG_CONFIG_MASK', 'case BQ256XX_VBUS_STAT_USB_CDP:\n+\t\treturn 1500000;',
                  'case BQ256XX_VBUS_STAT_USB_DCP:\n+\t\treturn 2000000;', 'case BQ256XX_VBUS_STAT_NONSTD:\n+\t\treturn 1000000;',
                  'static char *supplied_to[] = { "sc27xx-fgu" };',
                  # the PMIC's charger detection first: the charger's own (IINDET_EN) drives D+/D-, the gadget's lines
                  'sprd_pmic_detect_charger_type(bq->pmic)', 'of_find_compatible_node(NULL, NULL, "sprd,ump9620")',
                  'bus_find_device_by_of_node(&spi_bus_type, np)', 'no charger type from the PMIC'):
            self.assertIn(s, p9)
        # the fuel gauge asks the charger by the name the driver registers
        p10 = (TOP / 'kernel' / 'patches' / '0010-power-sc27xx-fuel-gauge-status-from-bq256xx.patch').read_text()
        self.assertIn('+\t"bq256xx-charger",', p10)
        # the U30 Air's other charger (issue #102): when nothing answers at the node's address, ZTE's driver goes on
        # at 0x1a (an SGM41513), and so does this one, with that chip's charge and termination current steps; a
        # charger that answers at the node's address takes none of it
        p15 = (TOP / 'kernel' / 'patches' / '0015-power-bq256xx-SGM41513-at-0x1a.patch').read_text()
        for s in ('#define SGM41513_I2C_ADDR\t\t0x1a', 'if (part < 0 && client->addr != SGM41513_I2C_ADDR) {',
                  'devm_i2c_new_dummy_device(dev, client->adapter, SGM41513_I2C_ADDR)',
                  'bq->regmap = devm_regmap_init_i2c(chip,', 'no charger: %d (and %d at 0x%02x)',
                  '{ 0, 40000, 130000, 300000, 540000, 1020000, 1500000, 2460000 }',
                  '{ 5000, 10000, 20000, 30000, 60000, 60000, 120000, 120000 }',
                  '80000, 100000, 120000, 140000, 160000, 180000, 200000, 240000,',
                  'info->bq256xx_set_ichg = sgm41513_set_ichg_curr;', 'info->bq256xx_set_iterm = sgm41513_set_term_curr;',
                  'info->bq256xx_set_iprechg = sgm41513_set_prechrg_curr;'):
            self.assertIn(s, p15)
        # ... but only on a board with a battery (the U30 Air's charger policy service): the F50 has a charger at
        # 0x1a too, and bound and told to charge it reset the board in a loop on a router's port (issue #225)
        p16 = (TOP / 'kernel' / 'patches' / '0016-power-bq256xx-SGM41513-only-with-a-battery.patch').read_text()
        for s in ('of_find_node_by_path("/charger_policy_service")', 'of_node_put(np);',
                  '+\t\tif (part < 0 && client->addr != SGM41513_I2C_ADDR && battery) {'):
            self.assertIn(s, p16)
        # the PMIC's charger detection reads the UMP9620's own register (0x239c), not the SC2730's (0x1b9c)
        self.assertIn(r"#define SPRD_UMP9620_CHG_DET\t\t0x239c", port)
        self.assertIn("'.charger_det = SPRD_UMP9620_CHG_DET,'", port)
        # Android's last capacity in the FGU's user area is whole percent in bits 7:0 and tenths in bits 11:8 (575 =
        # 0x23f = 63.2 %), not 0.1 %; mainline writes whole percent; a valid value is taken as it is, only an invalid
        # one goes to the OCV (no migration guess); the charger's Full (from a charger driver) means 100 %
        # with the battery current within the node's fullbatt-current, no OCV percentage or voltage (the 4.146 V of a
        # real Full is below fullbatt-voltage); the capacity is saved as it changes and stays 0-100
        patches = TOP / 'kernel' / 'patches'
        self.assertFalse((patches / '0011-power-sc27xx-fuel-gauge-UMP9620-capacity-in-tenths.patch').exists())
        p11 = (patches / '0011-power-sc27xx-fuel-gauge-UMP9620-saved-capacity-and-full.patch').read_text()
        for s in ('+#define UMP9620_FGU_CAP_INTEGER_MASK\tGENMASK(7, 0)', '+#define UMP9620_FGU_CAP_DECIMAL_MASK\tGENMASK(11, 8)',
                  '+\t\tcap = clamp(cap, 0, 100);', '+\tvalid = whole <= 100 && tenths <= 9 && !(whole == 100 && tenths);',
                  '+\tif (valid) {',
                  '+\t*cap = power_supply_ocv2cap_simple(data->cap_table, data->table_len, ocv);',
                  '+\tif (!is_first_poweron && data->var == &ump9620_info)',
                  '+\tif (data->var == &ump9620_info && chg_sts == POWER_SUPPLY_STATUS_FULL && cap != 100 &&',
                  '+\t    sc27xx_fgu_has_charger() && ump9620_fgu_full_current(data)) {',
                  '+\treturn abs(cur) <= (int)(data->full_current_ua / 1000);',
                  '+\t\t\tof_property_read_u32(bat, "fullbatt-current", &data->full_current_ua);',
                  '+\t\tsc27xx_fgu_adjust_cap(data, 100);',
                  '+\t\tif (*cap != data->saved_cap && sc27xx_fgu_save_last_cap(data, *cap))',
                  '+\t\tval->intval = clamp(value, 0, 100);'):
            self.assertIn(s, p11)
        self.assertNotIn('* 10;', p11)
        for s in ('ambiguous', 'UMP9620_FGU_RELAXED_MA', 'UMP9620_FGU_FULL_MIN_CAP', 'fullbatt-voltage"'):
            self.assertNotIn(s, ''.join(l for l in p11.splitlines(True) if l.startswith('+')))
        # the capacity is read and kept once more at a power-off, on UMP9620
        # with a battery only; the user area's steps stay regmap_update_bits() as in Unisoc's driver and the U30 Air's
        # stock module, with its 200 us wait after the release too
        p18 = (patches / '0018-power-sc27xx-fuel-gauge-UMP9620-capacity-kept-at-power-off.patch').read_text()
        for s in ('+static void sc27xx_fgu_shutdown(struct platform_device *pdev)',
                  '+\tif (!data || data->var != &ump9620_info || !data->bat_present)',
                  '+\t\tret = sc27xx_fgu_save_last_cap(data, clamp(cap, 0, 100));',
                  '+\t\tret = sc27xx_fgu_save_boot_mode(data, SC27XX_FGU_NORMAIL_POWERTON);',
                  '+\t.shutdown = sc27xx_fgu_shutdown,'):
            self.assertIn(s, p18)
        self.assertNotIn('regmap_write_bits(data', p18)
        self.assertEqual(p18.count('+\tif (!ret && data->var == &ump9620_info)\n+\t\tudelay(200);'), 2)

    def test_mainline_can_suspend(self):
        # System suspend (PSCI SYSTEM_SUSPEND) and a tickless idle: under the periodic tick a CPU brought back online
        # was left out of the timer broadcast and locked up, which every suspend (and `cpu7 offline/online`) hit
        cfg = (TOP / 'kernel' / 'mu300-mainline.config').read_text()
        for k in ('CONFIG_SUSPEND', 'CONFIG_PM_SLEEP', 'CONFIG_NO_HZ_IDLE', 'CONFIG_HIGH_RES_TIMERS'):
            self.assertRegex(cfg, rf'(?m)^{k}=y$')
        drv = TOP / 'kernel' / 'port' / 'drivers'
        # the PMIC watchdog keeps counting while the AP sleeps: stopped for the sleep, armed again after it
        wdt = (drv / 'watchdog' / 'ump9620-pmic-wdt.c').read_text()
        self.assertIn('NOIRQ_SYSTEM_SLEEP_PM_OPS(ump9620_wdt_suspend_noirq, ump9620_wdt_resume_noirq)', wdt)
        self.assertIn('.pm = pm_sleep_ptr(&ump9620_wdt_pm_ops)', wdt)
        self.assertIn('platform_set_drvdata(pdev, w);', wdt)
        # PCIe WAKE# is a wakeup only when power/wakeup says so, and is masked (unlazily) for the sleep otherwise
        pcie = (drv / 'pci' / 'controller' / 'dwc' / 'pcie-sprd.c').read_text()
        self.assertIn('if (device_may_wakeup(pci->dev))\n\t\tpm_wakeup_hard_event(pci->dev);', pcie)
        self.assertNotIn('IRQF_TRIGGER_FALLING | IRQF_NO_SUSPEND', pcie)
        self.assertIn('irq_set_status_flags(ctrl->wakeup_irq, IRQ_DISABLE_UNLAZY);', pcie)
        self.assertNotIn('device_init_wakeup(dev, true)', pcie)
        self.assertIn('SET_SYSTEM_SLEEP_PM_OPS(sprd_pcie_pm_suspend, sprd_pcie_pm_resume)', pcie)
        # the firmware's debug log pulled WAKE# right after every L2 entry: off by default, and a resume leaves it be
        mods = TOP / 'kernel' / 'modules'
        sysfs = (mods / 'wcn_bsp' / 'platform' / 'sysfs.c').read_text()
        self.assertRegex(sysfs, r'#endif\n(\t.*\n)*\tsysfs_info\.armlog_status = 0;\n')
        ep = (mods / 'wcn_bsp' / 'pcie' / 'pcie.c').read_text()
        resume = ep[ep.index('static int sprd_ep_resume('):ep.index('const struct dev_pm_ops sprd_ep_pm_ops')]
        self.assertNotIn('wcn_set_armlog(true)', resume)
        # without a WoWLAN configuration cfg80211 closed the interfaces while the bus was going down, and the
        # firmware asserted at the next Wi-Fi open: WoWLAN "any" from the wiphy's registration on
        iface = (mods / 'sprd_wlan_combo' / 'common' / 'iface.c').read_text()
        self.assertIn('wowlan->any = true;\n\t\t\twiphy->wowlan_config = wowlan;', iface)
        self.assertLess(iface.index('ret = wiphy_register(wiphy);'), iface.index('wiphy->wowlan_config = wowlan;'))

    def test_mainline_config_has_kvm_and_the_module_set(self):
        # /dev/kvm (the CPUs start at EL2) and the router/container modules; the kernel's own modules reach the bundle
        cfg = (TOP / 'kernel' / 'mu300-mainline.config').read_text()
        opts = {}
        for line in cfg.splitlines():
            if line.startswith('CONFIG_'):
                k, v = line.split('=', 1)
                # an option given twice must say the same thing both times: merge_config takes the last one silently
                self.assertEqual(opts.setdefault(k, v), v, k)
        for k in ('CONFIG_VIRTUALIZATION', 'CONFIG_KVM', 'CONFIG_MODULES', 'CONFIG_WIREGUARD', 'CONFIG_NET_SCH_CAKE',
                  'CONFIG_TUN', 'CONFIG_NF_FLOW_TABLE', 'CONFIG_IKCONFIG_PROC', 'CONFIG_CHECKPOINT_RESTORE',
                  'CONFIG_MPTCP', 'CONFIG_IP_MROUTE', 'CONFIG_NFSD_V4', 'CONFIG_FS_ENCRYPTION'):
            self.assertEqual(opts.get(k), 'y', k)
        for k in ('CONFIG_VHOST_NET', 'CONFIG_NTFS3_FS', 'CONFIG_DM_CRYPT', 'CONFIG_NET_SCH_HTB', 'CONFIG_USB_NET_CDC_MBIM',
                  'CONFIG_XFRM_USER', 'CONFIG_BRIDGE_NETFILTER', 'CONFIG_BINFMT_MISC', 'CONFIG_SND_USB_AUDIO',
                  'CONFIG_VIRTIO_FS', 'CONFIG_VHOST_SCSI', 'CONFIG_9P_FS', 'CONFIG_DM_INTEGRITY',
                  'CONFIG_MPLS_ROUTING', 'CONFIG_USBIP_HOST', 'CONFIG_USB_VIDEO_CLASS'):
            self.assertEqual(opts.get(k), 'm', k)
        mods = (TOP / 'kernel' / 'build-modules.sh').read_text()
        self.assertIn('INSTALL_MOD_STRIP=1 DEPMOD=true modules_install', mods)
        self.assertIn('has the name of an in-tree module', mods)

    def test_wifi_per_frame_logs_are_bounded(self):
        # three lines the TX path wrote for every frame - a
        # group frame's LUT lookup (pr_info), a frame for a client that has left (pr_err) and a send that failed
        # (netdev_err) - are debug or rate-limited; the frames are still dropped and freed as before
        wlan = TOP / 'kernel' / 'modules' / 'sprd_wlan_combo'
        for name in ('pcie.c', 'sdio.c'):
            text = (wlan / 'sc2355' / name).read_text()
            self.assertNotIn('pr_info("%s, %d, group lut_index=%d\\n"', text, name)
            self.assertNotIn('pr_err("%s, %d, sta disconn, no data tx!"', text, name)
            self.assertIn('pr_debug_ratelimited("%s, %d, group lut_index=%d\\n"', text, name)
            self.assertIn('\t\tpr_debug_ratelimited("%s, %d, sta disconn, no data tx!\\n", __func__, __LINE__);\n'
                          '\t\treturn -EPERM;', text, name)
        iface = (wlan / 'common' / 'iface.c').read_text()
        body = iface[iface.index('static netdev_tx_t iface_start_xmit('):]
        body = body[:body.index('\n}\n')]
        self.assertIn('\t\tif (net_ratelimit())\n\t\t\tnetdev_err(ndev, "%s drop msg due to TX Err\\n", __func__);', body)
        after = body[body.index('drop msg due to TX Err'):]
        self.assertLess(after.index('dev_kfree_skb(skb);'), after.index('return NETDEV_TX_OK;'))
        self.assertLess(after.index('sprd_chip_free_msg(&vif->priv->chip, msg);'), after.index('return NETDEV_TX_OK;'))

    def test_wifi_notify_init_unwinds_and_registers_the_reset_notifier_last(self):
        # kanoqwq/mu300-linux@23361ce: a failed notify_init left the WCN reset notifier registered on a freed priv
        iface = (TOP / 'kernel' / 'modules' / 'sprd_wlan_combo' / 'common' / 'iface.c').read_text()
        body = iface[iface.index('static int iface_notify_init('):]
        body = body[:body.index('\n}\n')]
        reg = body.index('atomic_notifier_chain_register(&wcn_reset_notifier_list,')
        for step in ('misc_register(&wlan_misc_device);', 'register_inetaddr_notifier(&iface_inetaddr_cb);',
                     'register_inet6addr_notifier(&iface_inet6addr_cb);'):
            self.assertLess(body.index(step), reg, step)
        # nothing returns between the first registration and the end without undoing what it registered
        self.assertEqual(body.count('return ret;'), 2)
        self.assertIn('\t\treturn ret;\n\t}\n\n\tret = register_inetaddr_notifier', body)
        for label in ('goto err_misc;', 'goto err_inet;', 'err_inet:\n\tunregister_inetaddr_notifier',
                      'err_misc:\n\tmisc_deregister(&wlan_misc_device);\n\treturn ret;'):
            self.assertIn(label, body)

    def test_wifi_rx_teardown_stops_the_refill_producers_first(self):
        # kanoqwq/mu300-linux@23361ce: the RX work queues refills on sprd_work and a refill arms the timed retry;
        # rx_deinit drained them in an order that let one re-arm the other over a freed rx_mgmt
        wlan = TOP / 'kernel' / 'modules' / 'sprd_wlan_combo' / 'sc2355'
        self.assertIn('\tbool stopping;\n};', (wlan / 'rx.h').read_text())
        rx = (wlan / 'rx.c').read_text()

        def body(sig):
            b = rx[rx.index(sig):]
            return b[:b.index('\n}\n')]
        q = body('void sc2355_queue_rx_buff_work(')
        self.assertLess(q.index('READ_ONCE(rx_mgmt->stopping)'), q.index('sprd_alloc_work(0)'))
        self.assertIn('if (!READ_ONCE(rx_mgmt->stopping))\n\t\tsc2355_queue_rx_buff_work(', body('static void rx_refill_retry('))
        f = body('int sc2355_mm_fill_buffer(')
        # read under refill_lock since the bus-completion resume
        self.assertLess(f.index('if (rx_mgmt->stopping) {'), f.index('atomic_xchg(&mm_entry->alloc_num, 0)'))
        d = body('int sc2355_rx_deinit(')
        order = ['WRITE_ONCE(rx_mgmt->stopping, true);', 'cancel_delayed_work_sync(&rx_mgmt->refill_retry);',
                 'flush_workqueue(rx_mgmt->rx_queue);', 'flush_workqueue(rx_mgmt->rx_net_workq);',
                 'sprd_clean_work(hif->priv);']
        pos = [d.index(s) for s in order]
        self.assertEqual(pos, sorted(pos))
        self.assertEqual(d.count('cancel_delayed_work_sync(&rx_mgmt->refill_retry);'), 2)
        self.assertLess(d.index('sprd_clean_work(hif->priv);'), d.rindex('cancel_delayed_work_sync(&rx_mgmt->refill_retry);'))
        self.assertLess(d.rindex('cancel_delayed_work_sync'), d.index('kfree(rx_mgmt);'))

    def test_wifi_remove_tears_the_driver_down(self):
        # issue #146: the port's platform remove only logged (the vendor's calls sprd_iface_remove), so rmmod freed
        # the module with the recovery watchdog's delayed work still armed: __run_timers panicked within a second
        wlan = TOP / 'kernel' / 'modules' / 'sprd_wlan_combo'
        drv = (wlan / 'common' / 'sprd_wlan.c').read_text()
        rm = drv[drv.index('static void sprd_wlan_remove('):]
        rm = rm[:rm.index('\n}\n')]
        self.assertIn('\tif (p_match_data->hw_type == SPRD_HW_SC2355_PCIE)\n\t\tsprd_iface_remove(pdev);', rm)
        iface = (wlan / 'common' / 'iface.c').read_text()
        body = iface[iface.index('int sprd_iface_remove('):]
        body = body[:body.index('\n}\n')]
        # the watchdog stops first, and a failed power-on does not leave the teardown undone
        self.assertLess(body.index('iface_wd_stop();'), body.index('sprd_iface_set_power(hif, true);'))
        self.assertEqual(body.count('\n\treturn '), 1)
        self.assertIn('\tif (!ret)\n\t\tsprd_iface_set_power(hif, false);', body)
        wd = iface[iface.index('static void iface_wd_stop(void)'):]
        wd = wd[:wd.index('\n}\n')]
        self.assertIn('cancel_delayed_work_sync(&iface_wd.tick);', wd)
        # the timers that live in priv are shut down where priv is freed
        core_free = (wlan / 'common' / 'cfg80211.c').read_text()
        core_free = core_free[core_free.index('void sprd_core_free('):]
        core_free = core_free[:core_free.index('\n}\n')]
        self.assertLess(core_free.index('timer_shutdown_sync(&priv->scan_timer);'), core_free.index('wiphy_free(wiphy);'))
        ack = (wlan / 'sc2355' / 'tcp_ack.c').read_text()
        ack = ack[ack.index('void sc2355_tcp_ack_deinit('):]
        ack = ack[:ack.index('\n}\n')]
        self.assertLess(ack.index('sprd_chip_drop_tcp_msg('), ack.index('timer_shutdown_sync(&ack_m->ack_info[i].timer);'))

    def test_wifi_tx_backpressure_is_not_logged_as_an_error(self):
        # kanoqwq/mu300-linux@3386928: a full TX ring is flow control, hit on every scheduling pass under load;
        # the vendor printed two pr_err lines for it each time
        wlan = TOP / 'kernel' / 'modules' / 'sprd_wlan_combo' / 'sc2355'
        pcie = (wlan / 'pcie.c').read_text()
        fc = pcie[pcie.index('int sc2355_pcie_fc_test_send_num('):]
        fc = fc[:fc.index('\n}\n')]
        self.assertNotIn('pr_err(', fc)
        self.assertIn('pr_debug_ratelimited("%s,%d free_num=%d, data_num=%d\\n"', fc)
        tx = (wlan / 'tx.c').read_text()
        self.assertNotIn('pr_err("%s, %d: _fc_ no credit!\\n"', tx)
        self.assertIn('pr_debug_ratelimited("%s, %d: _fc_ no credit!\\n"', tx)
    def test_mainline_rewrites_the_ttl_in_tc(self):
        # mu300-ttl's tc backend (clsact egress, matchall, pedit, csum): the flowtable transmits through egress, so
        # flow offloading stays on
        cfg = (TOP / 'kernel' / 'mu300-mainline.config').read_text()
        for k in ('CONFIG_NET_SCH_INGRESS', 'CONFIG_NET_CLS_ACT'):
            self.assertRegex(cfg, rf'(?m)^{k}=y$')
        for k in ('CONFIG_NET_CLS_MATCHALL', 'CONFIG_NET_ACT_PEDIT', 'CONFIG_NET_ACT_CSUM'):
            self.assertRegex(cfg, rf'(?m)^{k}=[ym]$')

    def test_e2fsck_is_pinned_and_in_the_ramdisk(self):
        # kernel/initramfs/e2fsck/build.sh: a pinned e2fsprogs (its signed SHA-256) in a pinned image, handed to the
        # ramdisk; init checks before every mount of the root, and never as a gate (root_fsck always returns 0)
        text = (TOP / 'kernel' / 'initramfs' / 'e2fsck' / 'build.sh').read_text()
        self.assertRegex(text, r'(?m)^VER=1\.47\.4$')
        self.assertRegex(text, r'(?m)^SHA=[0-9a-f]{64}$')
        self.assertRegex(text, r'(?m)^IMG=alpine:[0-9.]+@sha256:[0-9a-f]{64}$')
        self.assertIn('sha256sum -c', text)
        init = (TOP / 'kernel' / 'initramfs' / 'init').read_text()
        self.assertGreaterEqual(init.count('root_fsck "$'), 1)
        block = re.search(r'# --- fsck begin\n(.*?)# --- fsck end', init, re.S).group(1)
        self.assertNotRegex(block, r'return [1-9]')

    def test_mainline_has_zram_with_lz4_and_zstd(self):
        cfg = (TOP / 'kernel/mu300-mainline.config').read_text().splitlines()
        for opt in ('SWAP', 'ZRAM', 'ZSMALLOC', 'ZRAM_BACKEND_LZ4', 'ZRAM_BACKEND_ZSTD', 'ZRAM_DEF_COMP_LZ4',
                    'CRYPTO_LZ4', 'CRYPTO_ZSTD'):
            self.assertIn(f'CONFIG_{opt}=y', cfg)

    def test_driver_failures_are_not_taken_for_success(self):
        mods = TOP / 'kernel' / 'modules'
        # the PMIC watchdog: a failed read at suspend is not "stopped" (it would reset the device in its sleep)
        wdt = (TOP / 'kernel/port/drivers/watchdog/ump9620-pmic-wdt.c').read_text()
        sus = wdt[wdt.index('static int ump9620_wdt_suspend_noirq('):wdt.index('static int ump9620_wdt_resume_noirq(')]
        self.assertIn('ret = regmap_read(w->map, w->base + WDT_CTRL, &ctrl);\n\tif (ret)\n\t\treturn ret;', sus)
        # ums9620_lpintc: an enabled block that cannot be mapped fails the load, nothing touched
        lp = (mods / 'ums9620_lpintc' / 'ums9620_lpintc.c').read_text()
        self.assertRegex(lp, r'if \(!intc\[n\]\) \{\n(\t.*\n)*?\t\t\tlpintc_unmap\(\);\n\t\t\treturn -ENOMEM;')
        # sipc: a failed driver registration leaves no panic notifier and no wakeup source behind
        sipc = (mods / 'sprd_modem' / 'sipc' / 'sipc.c').read_text()
        init = sipc[sipc.index('static int __init sprd_ipc_init('):]
        self.assertRegex(init, r'ret = platform_driver_register\(&sprd_ipc_driver\);\n\tif \(ret\) \{\n'
                               r'(.*\n)*?.*atomic_notifier_chain_unregister\(&panic_notifier_list, &senddie_panic_nb\);'
                               r'\n(.*\n)*?\t\tsmsg_remove_wakeup\(\);')
        # the Wi-Fi recovery watchdog: a new probe starts clean but keeps this boot's one WCN reset used, and a tick
        # never queues another once the stop has begun
        iface = (mods / 'sprd_wlan_combo' / 'common' / 'iface.c').read_text()
        start = iface[iface.index('static void iface_wd_start('):iface.index('static void iface_wd_stop(')]
        self.assertIn('memset(&iface_wd, 0, sizeof(iface_wd));\n\tiface_wd.reset_used = reset_used;', start)
        stop = iface[iface.index('static void iface_wd_stop('):]
        self.assertLess(stop.index('WRITE_ONCE(iface_wd.stopping, true);'), stop.index('cancel_delayed_work_sync'))
        tick = iface[iface.index('static void iface_wd_tick('):iface.index('static void iface_wd_start(')]
        self.assertIn('if (!READ_ONCE(iface_wd.stopping))\n\t\tqueue_delayed_work(system_wq, &iface_wd.tick', tick)

    def test_mainline_command_line_panics_on_an_oops_and_a_lockup(self):
        # an oops before init sets panic_on_oops hung the boot until LK fell back to Android: oops=panic reboots it
        # with the dump in pstore. hardlockup_panic= is no boot parameter; nmi_watchdog=panic is
        cfg = (TOP / 'kernel/mu300-mainline.config').read_text()
        cmdline = re.search(r'(?m)^CONFIG_CMDLINE="(.*)"$', cfg).group(1).split()
        for arg in ('oops=panic', 'nmi_watchdog=panic', 'softlockup_panic=1', 'panic=5'):
            self.assertIn(arg, cmdline)
        self.assertFalse([a for a in cmdline if a.startswith('hardlockup_panic')])
        for k in ('CONFIG_SOFTLOCKUP_DETECTOR', 'CONFIG_HARDLOCKUP_DETECTOR'):
            self.assertRegex(cfg, rf'(?m)^{k}=y$')


if __name__ == '__main__':
    unittest.main()
