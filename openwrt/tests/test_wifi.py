"""wifi-start loads the WCN modules without modules.dep (the bundle ships none); mu300-hw restarts the AP once the
country is live unless wifi-ready finds the Wi-Fi up as configured, and makes a broken /etc/board.json again before
the wifi-scripts read it."""
import subprocess
import sys
import unittest

from helpers import BIN, TOP, ShellTest, ucode_argv

HW = TOP / 'openwrt' / 'overlay' / 'etc' / 'init.d' / 'mu300-hw'


class WifiStart(ShellTest):
    def run_start(self, shell, flat=(), modprobe_ok=()):
        kmod = self.tmp / 'kmod'
        kmod.mkdir(parents=True, exist_ok=True)
        for m in flat:
            (kmod / (m + '.ko')).write_text('')
        log = self.tmp / 'calls'
        log.write_text('')
        self.stub('depmod', 'true')
        self.stub('sleep', 'true')
        self.stub('ls', 'true')
        self.stub('modprobe', 'echo "modprobe $*" >> "$STUBLOG/calls"\n'
                  'case " pcie-sprd-misc pcie-sprd %s " in *" $1 "*) exit 0;; esac\nexit 1' % ' '.join(modprobe_ok))
        self.stub('insmod', 'echo "insmod $*" >> "$STUBLOG/calls"')
        env = {'PATH': '%s:/usr/bin:/bin' % self.stubs, 'STUBLOG': str(self.tmp), 'MU300_KMOD': str(kmod)}
        r = subprocess.run(shell + [str(BIN / 'wifi-start')], capture_output=True, text=True, env=env)
        return r, log.read_text()

    def test_modprobe_first(self):
        for shell in self.shells:
            with self.subTest(shell=shell):
                r, calls = self.run_start(shell, modprobe_ok=['wcn_bsp', 'sprd_wlan_combo'])
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertNotIn('insmod', calls)

    def test_insmod_flat_when_modprobe_fails(self):
        for shell in self.shells:
            with self.subTest(shell=shell):
                r, calls = self.run_start(shell, flat=['wcn_bsp', 'sprd_wlan_combo'])
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn('insmod %s/kmod/wcn_bsp.ko' % self.tmp, calls)
                self.assertIn('insmod %s/kmod/sprd_wlan_combo.ko' % self.tmp, calls)

    def test_missing_module_exits_1(self):
        for shell in self.shells:
            with self.subTest(shell=shell):
                r, calls = self.run_start(shell, flat=['sprd_wlan_combo'])
                self.assertEqual(r.returncode, 1)
                self.assertIn('missing wcn_bsp.ko', r.stderr)
                self.assertNotIn('insmod', calls)


class HwBringup(unittest.TestCase):
    def test_wifi_restarted_once_after_country(self):
        s = HW.read_text()
        down, up = s.index('wifi down'), s.index('wifi up')
        self.assertLess(s.index('iw reg set'), down)
        self.assertLess(down, up)
        self.assertEqual(s.count('wifi down'), 1)
        self.assertEqual(s.count('wifi up'), 1)
        self.assertLess(up, s.index('\nstart()'))

    def test_board_json_checked_and_readiness_from_wifi_ready(self):
        s = HW.read_text()
        self.assertLess(s.index('openwrt-board-check'), s.index('/opt/mu300/bin/wifi-start'))
        self.assertLess(s.index('/opt/mu300/bin/wifi-ready 20'), s.index('wifi down'))
        self.assertNotIn('hostapd.wlan0', s)       # a hotspot switched off has none: 20 s and a restart for nothing
        self.assertNotIn('sleep 0.2', s)


class BoardCheck(ShellTest):
    """openwrt-board-check, with ucode's JSON check played by Python."""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        (self.root / 'etc').mkdir(parents=True)
        self.board = self.root / 'etc' / 'board.json'
        parser = self.stubs / 'ucode'
        parser.write_text('#!%s\nimport json, sys\ntry:\n    sys.exit(0 if isinstance(json.load(open(sys.argv[-1])), '
                          'dict) else 1)\nexcept Exception:\n    sys.exit(1)\n' % sys.executable)
        parser.chmod(0o755)
        self.stub('board_detect', 'printf \'{"model":{"id":"zte,u30air"}}\' > "$1"')
        self.stub('bad_detect', 'printf \'{"model":\' > "$1"')
        self.stub('sync', ':')

    def check(self, shell, detector='board_detect'):
        return self.script(shell, BIN / 'openwrt-board-check', MU300_SYSROOT=self.root,
                           MU300_BOARD_DETECTOR=self.stubs / detector)

    def leftovers(self):
        return sorted(p.name for p in self.board.parent.iterdir() if p.name != 'board.json')

    def test_valid_is_kept_whatever_it_holds(self):
        for shell in self.each_shell():
            self.board.write_text('{"custom": 1}')
            r = self.check(shell)
            self.assertEqual((r.returncode, r.stdout), (0, ''), r.stderr)
            self.assertEqual(self.board.read_text(), '{"custom": 1}')

    def test_broken_is_made_again_and_kept_beside_it(self):
        for shell in self.each_shell():
            for old in ('{"model":{', '', '[1]'):
                for p in self.board.parent.iterdir():
                    p.unlink()
                self.board.write_text(old)
                r = self.check(shell)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.board.read_text(), '{"model":{"id":"zte,u30air"}}')
                kept = self.leftovers()
                self.assertEqual(len(kept), 1)
                self.assertTrue(kept[0].startswith('board.json.corrupt.'))
                self.assertEqual((self.board.parent / kept[0]).read_text(), old)

    def test_missing_is_made(self):
        for shell in self.each_shell():
            self.board.unlink(missing_ok=True)
            self.assertEqual(self.check(shell).returncode, 0)
            self.assertEqual(self.board.read_text(), '{"model":{"id":"zte,u30air"}}')
            self.assertEqual(self.leftovers(), [])
            self.board.unlink()

    def test_a_bad_rebuild_leaves_the_file_as_it_was(self):
        for shell in self.each_shell():
            self.board.write_text('[1]')
            r = self.check(shell, 'bad_detect')
            self.assertEqual(r.returncode, 1)
            self.assertIn('no valid JSON', r.stderr)
            self.assertEqual(self.board.read_text(), '[1]')
            self.assertEqual(self.leftovers(), [])
            r = self.check(shell, 'no_such_detector')
            self.assertEqual(r.returncode, 1)
            self.assertEqual(self.board.read_text(), '[1]')

    def test_a_symlink_is_not_replaced(self):
        for shell in self.each_shell():
            real = self.root / 'etc' / 'real'
            real.write_text('x')
            self.board.unlink(missing_ok=True)
            self.board.symlink_to('real')
            self.assertEqual(self.check(shell).returncode, 1)
            self.assertEqual(real.read_text(), 'x')
            self.board.unlink()
            real.unlink()


class WifiReady(ShellTest):
    """wifi-ready's one look (no SECONDS), against stubbed `uci -q show wireless` and `ubus call` (tests/ucode-stubs)."""

    UCI = ("wireless.radio0=wifi-device\n"
           "wireless.ap0=wifi-iface\nwireless.ap0.device='radio0'\nwireless.ap0.mode='ap'\n"
           "wireless.ap0.ifname='wlan0'\n")

    def ready(self, uci, radio, hostapd=None):
        argv = ucode_argv(BIN / 'wifi-ready')
        if not argv:
            self.skipTest('no ucode')
        (self.tmp / 'uci.out').write_text(uci)
        (self.tmp / 'status.json').write_text('{"radio0": %s}' % radio)
        (self.tmp / 'hostapd.json').write_text('{"status": "%s"}' % hostapd if hostapd else '')
        self.stub('uci', '[ "$*" = "-q show wireless" ] && cat "$STUBLOG/uci.out"')
        self.stub('ubus', 'case "$2 $3" in\n'
                          '  "network.wireless status") cat "$STUBLOG/status.json" ;;\n'
                          '  "hostapd.wlan0 get_status") [ -s "$STUBLOG/hostapd.json" ] && cat "$STUBLOG/hostapd.json" ;;\n'
                          '  *) exit 4 ;;\nesac')
        return subprocess.run(argv, capture_output=True, text=True, env=self.env()).returncode

    def test_access_point(self):
        up = '{"up": true, "pending": false, "interfaces": [{"section": "ap0", "ifname": "wlan0"}]}'
        self.assertEqual(self.ready(self.UCI, up, 'ENABLED'), 0)
        self.assertEqual(self.ready(self.UCI, up, 'DISABLED'), 2)
        self.assertEqual(self.ready(self.UCI, up, 'COUNTRY_UPDATE'), 1)
        self.assertEqual(self.ready(self.UCI, '{"up": false, "pending": true}'), 1)     # not there yet
        self.assertEqual(self.ready(self.UCI, '{"up": false, "retry_setup_failed": true}'), 2)

    def test_a_hotspot_switched_off_is_ready(self):
        off = self.UCI + "wireless.ap0.disabled='1'\n"
        self.assertEqual(self.ready(off, '{"up": false, "pending": false}'), 0)
        self.assertEqual(self.ready(self.UCI + "wireless.radio0.disabled='1'\n", '{"up": false}'), 0)
        self.assertEqual(self.ready('', '{}'), 1)                                        # no radio configured


if __name__ == '__main__':
    unittest.main()


UAPSD = TOP / 'openwrt' / 'overlay' / 'etc' / 'uci-defaults' / '94-mu300-ap-uapsd'


class ApUapsd(ShellTest):
    """U-APSD advertisement off on every access point, a new configuration and a kept one; stations
    and the other options are left alone."""

    def test_new_configuration_has_it_off(self):
        self.assertIn("set wireless.ap0.uapsd='0'", (BIN / 'openwrt-wifi-config').read_text())

    def test_migration_sets_only_the_access_points(self):
        fns = self.tmp / 'functions.sh'
        # sections: ap0 (ap, no uapsd), ap1 (ap, uapsd 1), sta (sta), ap2 (ap, already 0)
        fns.write_text('''
config_load() { :; }
config_foreach() { for s in ap0 ap1 sta ap2; do "$1" "$s"; done; }
config_get() {
    case $2:$3 in
        sta:mode) v=sta ;; *:mode) v=ap ;;
        ap1:uapsd) v=1 ;; ap2:uapsd) v=0 ;; *) v= ;;
    esac
    eval "$1=\\$v"
}
''')
        script = self.tmp / 'uapsd'
        script.write_text(UAPSD.read_text().replace('. /lib/functions.sh', f'. "{fns}"')
                          .replace('/etc/config/wireless', str(fns)))
        self.stub('uci', 'echo "$*" >> "$STUBLOG/uci"')
        for shell in self.each_shell():
            (self.tmp / 'uci').unlink(missing_ok=True)
            r = self.sh(shell, f'. "{script}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.tmp / 'uci').read_text().splitlines(),
                             ['-q set wireless.ap0.uapsd=0', '-q set wireless.ap1.uapsd=0', 'commit wireless'])
