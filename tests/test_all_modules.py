"""upstream/all-modules.py (every tristate the boards can use, as a module) and check-dt-modules.py (none of them
may bind to the vendor device tree by itself)."""
import importlib.util
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[1]
UP = TOP / 'upstream'


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, UP / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


allmod = load('all_modules', 'all-modules.py')


class AllModules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def test_write_set_replaces_the_line_that_names_an_option(self):
        cfg = self.d / '.config'
        cfg.write_text('CONFIG_A=y\n# CONFIG_B is not set\nCONFIG_BB=m\nCONFIG_C="x"\n')
        allmod.write_set(str(cfg), {'B': 'm', 'A': 'n'})
        self.assertEqual(allmod.read(str(cfg)), {'A': 'n', 'B': 'm', 'BB': 'm', 'C': '"x"'})
        self.assertEqual(cfg.read_text().count('CONFIG_B'), 2)   # B once, BB untouched

    def test_tristates_are_found_with_their_directory(self):
        k = self.d / 'drivers' / 'foo'
        k.mkdir(parents=True)
        (k / 'Kconfig').write_text('menuconfig FOO\n\ttristate "foo"\nconfig FOO_OPT\n\tbool "opt"\n'
                                   'config FOO_LIB\n\tdef_tristate m\n')
        self.assertEqual(allmod.tristates(str(self.d)), {'FOO': 'drivers/foo', 'FOO_LIB': 'drivers/foo'})

    def test_the_rules_keep_board_wiring_tests_and_the_soc_gpu_out(self):
        dirs, keep, pats = allmod.rules()
        w = lambda sym, where: allmod.wanted(sym, where, dirs, keep, pats)
        for sym, where in (('USB_SERIAL_CH341', 'drivers/usb/serial'), ('BTRFS_FS', 'fs/btrfs'),
                           ('MMC_VUB300', 'drivers/mmc/host'), ('DRM_UDL', 'drivers/gpu/drm/udl'),
                           ('LEDS_TRIGGER_PATTERN', 'drivers/leds/trigger'), ('NFT_CT', 'net/netfilter'),
                           ('TOUCHSCREEN_USB_COMPOSITE', 'drivers/input/touchscreen'),
                           ('BRIDGE_NF_EBTABLES', 'net/bridge/netfilter'), ('LEDS_CLASS_MULTICOLOR', 'drivers/leds')):
            self.assertTrue(w(sym, where), sym)
        for sym, where in (('SND_SOC_WM8960', 'sound/soc/codecs'), ('DRM_PANFROST', 'drivers/gpu/drm/panfrost'),
                           ('REGULATOR_FIXED_VOLTAGE', 'drivers/regulator'), ('CRC_KUNIT_TEST', 'lib'),
                           ('RPMB', 'drivers/misc'), ('EXT2_FS', 'fs/ext2'), ('CORESIGHT', 'drivers/hwtracing/coresight'),
                           ('USB_DUMMY_HCD', 'drivers/usb/gadget/udc'), ('USB_ETH', 'drivers/usb/gadget/legacy'),
                           ('VFIO', 'drivers/vfio'), ('MMC_SDHCI_OF_ARASAN', 'drivers/mmc/host'),
                           ('SERIAL_DEV_BUS', 'drivers/tty/serdev'), ('POWER_SEQUENCING', 'drivers/power/sequencing'),
                           ('INPUT_SC27XX_VIBRA', 'drivers/input/misc'), ('SPI_SPRD', 'drivers/spi')):
            self.assertFalse(w(sym, where), sym)

    def test_what_must_stay_off_is_checked(self):
        for k in ('PERF_EVENTS', 'KPROBES', 'IOMMU_DMA', 'OF_IOMMU', 'DMA_CMA', 'COMPILE_TEST'):
            self.assertIn(k, allmod.FORBIDDEN_Y)
        self.assertTrue(allmod.NEW_BOOL_OFF.search('RTLWIFI_DEBUG'))
        self.assertFalse(allmod.NEW_BOOL_OFF.search('IOMMU_DEFAULT_DMA_STRICT'))   # "FAULT" in DEFAULT is not one
        fam = (UP / 'all-modules.config').read_text()
        self.assertIn('CONFIG_NETFILTER_XTABLES_LEGACY=y', fam)     # Docker's check-config: iptable_nat & co.
        self.assertIn('CONFIG_DRM_FBDEV_EMULATION=n', fam)

    def test_the_build_runs_both(self):
        b = (UP / 'build.sh').read_text()
        self.assertLess(b.index('olddefconfig'), b.index('python3 /work/all-modules.py . $O'))
        self.assertLess(b.index('python3 /work/all-modules.py . $O'), b.index('config options not taken'))
        self.assertIn('cp $O/all-modules.report $OUT/', b)
        m = (UP / 'build-modules.sh').read_text()
        self.assertIn('python3 /work/check-dt-modules.py', m)
        self.assertIn("(out / 'all-modules.report')", (UP / 'audit-bundle.py').read_text())


class AutoloadGuard(unittest.TestCase):
    """The kernel's on-demand loads go through mu300-modprobe-guard on both systems (review of #256)."""
    GUARD = TOP / 'rootfs/overlay/opt/mu300/bin/mu300-modprobe-guard'
    LIST = TOP / 'rootfs/overlay/opt/mu300/lib/autoload-blocked'

    def run_guard(self, name, allow=False, shell='sh'):
        with tempfile.TemporaryDirectory() as d:
            log, kmsg = Path(d) / 'log', Path(d) / 'kmsg'
            fake = Path(d) / 'modprobe'
            fake.write_text('#!/bin/sh\necho "$@" > "%s"\n' % log)
            fake.chmod(0o755)
            env = {'PATH': '/usr/bin:/bin', 'MU300_MODPROBE': str(fake), 'MU300_AUTOLOAD_BLOCKED': str(self.LIST),
                   'MU300_KMSG': str(kmsg), 'MU300_AUTOLOAD_ALLOW': str(Path(d) / 'allow')}
            if allow:
                (Path(d) / 'allow').write_text('')
            r = subprocess.run([shell, str(self.GUARD), '-q', '--', name], env=env, capture_output=True, text=True)
            return r.returncode, log.read_text() if log.exists() else None, kmsg.read_text() if kmsg.exists() else ''

    def test_the_families_of_the_review_are_refused(self):
        for name in ('net-pf-30', 'net-pf-30-proto-0-type-2', 'net-pf-29', 'net-pf-29-proto-1', 'net-pf-36',
                     'net-pf-39', 'net-pf-40', 'net-pf-2-proto-132', 'net-pf-10-proto-132-type-1',
                     'net-pf-16-proto-16-family-TIPCv2', 'net-pf-16-proto-16-family-nfc', 'fs-hfs', 'fs-jfs',
                     'fs-nilfs2', 'fs-ocfs2', 'fs-gfs2'):
            rc, called, kmsg = self.run_guard(name)
            self.assertEqual((rc, called), (1, None), name)
            self.assertIn('on-demand loading of %s refused' % name, kmsg)

    def test_everything_else_reaches_modprobe_unchanged(self):
        # crypto, netfilter, the filesystems people plug in, the families the system uses, and net-pf-3x neighbours
        for name in ('crypto-gcm(aes)', 'nft-chain-2-nat', 'fs-ntfs3', 'fs-exfat', 'fs-hfsplus', 'fs-btrfs', 'fs-fuse',
                     'net-pf-24', 'net-pf-2-proto-115', 'net-pf-10', 'net-pf-3', 'net-pf-300', 'rtnl-link-wireguard',
                     'tcp-ulp-tls', 'char-major-10-238', 'tipc'):
            rc, called, _ = self.run_guard(name)
            self.assertEqual((rc, called), (0, '-q -- %s\n' % name), name)

    def test_the_switch_turns_it_off_and_busybox_runs_it(self):
        self.assertEqual(self.run_guard('net-pf-30', allow=True)[:2], (0, '-q -- net-pf-30\n'))
        if shutil.which('busybox'):
            self.assertEqual(self.run_guard('net-pf-30', shell='busybox')[:2], (1, None))
            self.assertEqual(self.run_guard('fs-ext4', shell='busybox')[:2], (0, '-q -- fs-ext4\n'))

    def test_both_systems_install_it(self):
        for system in ('rootfs', 'openwrt'):
            conf = (TOP / system / 'overlay/etc/sysctl.d/98-mu300-modules.conf').read_text()
            self.assertIn('kernel.modprobe = /opt/mu300/bin/mu300-modprobe-guard', conf)
            self.assertIn('dev.tty.ldisc_autoload = 0', conf)
        # OpenWrt's image takes /opt/mu300 from the Ubuntu overlay, and both copy their overlay whole
        build = (TOP / 'openwrt/build-rootfs.sh').read_text()
        self.assertIn('rootfs/overlay/opt/mu300":/in/opt-mu300', build)
        self.assertIn('cp -a /in/overlay/. $R/', build)
        self.assertTrue(self.GUARD.stat().st_mode & 0o111)

    def test_the_dead_families_are_not_built(self):
        dirs, keep, pats = allmod.rules()
        for sym, where in (('N_GSM', 'drivers/tty'), ('N_HDLC', 'drivers/tty'), ('ATM', 'net/atm'),
                           ('USB_ATM', 'drivers/usb/atm'), ('AX25', 'net/ax25'), ('X25', 'net/x25'),
                           ('RDS', 'net/rds'), ('AF_RXRPC', 'net/rxrpc'), ('AFS_FS', 'fs/afs'), ('PHONET', 'net/phonet'),
                           ('ISDN', 'drivers/isdn'), ('PARPORT', 'drivers/parport'), ('AF_KCM', 'net/kcm')):
            self.assertFalse(allmod.wanted(sym, where, dirs, keep, pats), sym)
        self.assertNotIn('CONFIG_HAMRADIO=y', (UP / 'all-modules.config').read_text())


class DtModules(unittest.TestCase):
    def run_check(self, aliases, *allowed):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / 'modules.alias'
            a.write_text(aliases)
            c = Path(d) / 'compat'
            c.write_text('# boards\nsprd,ums9620-dwc3\nregulator-fixed\nst,st21nfc\n')
            return subprocess.run([sys.executable, str(UP / 'check-dt-modules.py'), str(a), str(c), *allowed],
                                  capture_output=True, text=True)

    def test_a_module_that_matches_a_node_fails_the_build(self):
        r = self.run_check('alias of:N*T*Cregulator-fixed reg_fixed_voltage\n'
                           'alias of:N*T*Cregulator-fixedC* reg_fixed_voltage\nalias usb:v1234p* foo\n')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('reg_fixed_voltage binds to regulator-fixed', r.stderr)

    def test_unrelated_and_allowed_modules_pass(self):
        self.assertEqual(self.run_check('alias of:N*T*Cvendor,otherC* other\nalias usb:v1234p* foo\n'
                                        'alias of:NGAISLER_APBPS2T(null)C* apbps2\n').returncode, 0)
        self.assertEqual(self.run_check('alias of:N*T*Cst,st21nfc nfc-thing\n', 'nfc_thing').returncode, 0)

    def test_the_compatibles_of_both_boards_are_listed(self):
        compat = (UP / 'dt-compatibles.txt').read_text().split()
        for c in ('sprd,ums9620', 'zte-ldo-leds', 'arm,gic-v3', 'sprd,sc9860-spi'):
            self.assertIn(c, compat)


if __name__ == '__main__':
    unittest.main()
