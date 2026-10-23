"""kernel/all-modules.py (every tristate the boards can use, as a module), check-dt-modules.py (none of them may bind
to the vendor device tree by itself) and the autoload-blocked list with check-autoload-blocked.py."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOP = Path(__file__).resolve().parents[2]
UP = TOP / 'kernel'


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
        self.assertLess(b.index('olddefconfig'), b.index('python3 "$W/all-modules.py" . "$O"'))
        self.assertLess(b.index('python3 "$W/all-modules.py" . "$O"'), b.index('config options not taken'))
        self.assertIn('cp "$O/all-modules.report" "$OUT/"', b)
        m = (UP / 'build-modules.sh').read_text()
        self.assertIn('python3 "$W/check-dt-modules.py"', m)
        self.assertIn("(out / 'all-modules.report')", (UP / 'audit-bundle.py').read_text())


class AutoloadList(unittest.TestCase):
    """The modules the kernel must not load on demand (autoload-blocked): shipped in the bundle, enforced on the device
    by the system's modprobe helper (docs/contract.md); here the list itself and its closure over the build."""
    LIST = UP / 'autoload-blocked'
    SAMPLE = UP / 'tests/data/autoload-7.2.11'   # a subset of the 7.2.11 build's modules.dep and modules.alias

    def test_the_list_holds_module_names(self):
        names = [l.split('#')[0].strip() for l in self.LIST.read_text().splitlines()]
        names = [n for n in names if n]
        self.assertEqual(len(names), len(set(names)))
        for n in names:
            self.assertRegex(n, r'^[a-z0-9_]+$')
        for n in ('tipc', 'sctp', 'can', 'can_raw', 'can_bcm', 'nfc', 'ieee802154', 'slcan', 'hfs', 'jfs', 'nilfs2'):
            self.assertIn(n, names)
        for n in ('l2tp_core', 'l2tp_ppp', 'can_dev', 'hfsplus', 'wireguard', 'vhost_net'):
            self.assertNotIn(n, names)

    def test_the_build_sample_passes(self):
        check = load('autoload_check', 'check-autoload-blocked.py')
        check.check(self.SAMPLE, self.LIST)

    def run_check(self, listing, dep):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'list').write_text(listing)
            (d / 'modules.dep').write_text(dep)
            return subprocess.run([sys.executable, str(UP / 'check-autoload-blocked.py'), str(d), str(d / 'list')],
                                  capture_output=True, text=True)

    def test_a_dependant_left_off_the_list_fails(self):
        dep = 'kernel/net/tipc/tipc.ko:\nkernel/x/evil-helper.ko: kernel/net/tipc/tipc.ko\nkernel/x/other.ko:\n'
        r = self.run_check('tipc\n', dep)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('evil_helper depends on tipc but is not in the list', r.stderr)
        self.assertEqual(self.run_check('tipc\nevil_helper\n', dep).returncode, 0)

    def test_what_the_system_needs_must_not_be_listed(self):
        dep = 'kernel/net/wireguard/wireguard.ko: kernel/x/helper.ko\nkernel/x/helper.ko:\n'
        for listing, word in (('wireguard\n', 'wireguard is loaded on demand every day'),
                              ('helper\n', 'wireguard depends on helper but is not in the list'),
                              ('tipc\ntipc\n', 'listed twice: tipc'), ('Not-A-Name\n', 'not a module name')):
            r = self.run_check(listing, dep)
            self.assertNotEqual(r.returncode, 0, listing)
            self.assertIn(word, r.stderr)

    def test_the_families_the_review_named_are_not_built(self):
        gone = ('rds rds_tcp rds_rdma atm clip br2684 lec mpoa pppoatm ax25 netrom rose mkiss 6pack bpqether x25 lapb '
                'lapbether appletalk phonet cdc_phonet af_rxrpc rxkad kafs kcm smc smc_diag caif caif_socket chnl_net '
                'mISDN_core mISDN_dsp capi n_gsm n_hdlc dccp dccp_ipv4 dccp_ipv6 dccp_diag qrtr psnap p8022 llc2').split()
        listed = {l.split('#')[0].strip() for l in self.LIST.read_text().splitlines()}
        names = set((self.SAMPLE / 'modules.names-7.2.11').read_text().split())
        self.assertGreater(len(names), 2500)
        self.assertEqual(sorted(set(gone) & names), [])
        # L2TP stays, on purpose (L2TP VPNs), and is not listed
        self.assertIn('l2tp_ppp', names)
        self.assertNotIn('l2tp_ppp', listed)
        # every line discipline that is built is listed
        ldisc = {l.split()[2] for l in (self.SAMPLE / 'modules.alias').read_text().splitlines()
                 if l.startswith('alias tty-ldisc-')}
        self.assertTrue(ldisc)
        self.assertLessEqual(ldisc, listed)

    def test_the_bundle_checks_and_ships_it(self):
        b = (UP / 'make-bundle.sh').read_text()
        self.assertIn('python3 "$K/check-autoload-blocked.py" "$UO" "$K/autoload-blocked"', b)
        self.assertIn('cp "$K/autoload-blocked" "$W/b/autoload-blocked"', b)

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
