"""The hotspot never goes onto a DFS channel (the SC2355 driver has no radar detection: hostapd's start_dfs_cac()
fails and the AP stays down). A DFS channel becomes 36 with its width kept, or fails: never 149, never narrower.

* `lib/wifi-channels.sh`: the country table, a DFS or disallowed channel replaced with one line why, "auto", widths;
* `openwrt-wifi-config` and `mu300-wifi-band` on OpenWrt (a stub `uci`): the channel, htmode, the channel list for
  auto, and a DFS channel set in LuCI rewritten at boot;
* `openwrt/patches/wifi-scripts-no-dfs.patch`: how the build applies it, that it applies with --fuzz=0 to both layouts
  of hostapd.uc (the feed's and the SAE build's), and - with MU300_WRT_IMAGE naming an OpenWrt 25.12 docker image
  (e.g. mu300-openwrt-base:25.12.5, as openwrt/build-rootfs.sh imports it) - the hostapd configuration the patched
  wifi-scripts generate for a fake wiphy with and without radar detection; with MU300_WIFI_SAE_APKS (a directory
  openwrt/hostapd/build-hostapd.sh wrote) also with the SAE build's wifi-scripts, which the image has.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest

LIB = TOP / 'openwrt' / 'opt' / 'mu300' / 'lib' / 'wifi-channels.sh'
PATCH = TOP / 'openwrt' / 'patches' / 'wifi-scripts-no-dfs.patch'
FIXTURES = TOP / "openwrt" / "tests" / 'fixtures' / 'wifi-scripts'
FIVE = [36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144,
        149, 153, 157, 161, 165]


class Lib(ShellTest):
    def call(self, shell, code):
        r = self.sh(shell, f'. "{LIB}"; {code}')
        return r.stdout.strip(), r.stderr.strip()

    def test_parses(self):
        for shell in self.each_shell():
            r = subprocess.run(shell + ['-n', str(LIB)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_country_table(self):
        cases = {
            'CN': '36 40 44 48 149 153 157 161 165', 'US': '36 40 44 48 149 153 157 161 165',
            'de': '36 40 44 48 149 153 157 161 165', 'TR': '36 40 44 48', 'JP': '36 40 44 48',
            'BD': '149 153 157 161 165', 'PK': '149 153 157 161 165', 'ID': '36 40 44 48 149 153 157 161',
            '00': '', 'AM': '', 'ZZ': '36 40 44 48 149 153 157 161 165', '': '36 40 44 48 149 153 157 161 165',
        }
        for shell in self.each_shell():
            for cc, want in cases.items():
                self.assertEqual(self.call(shell, f'wifi_ap5_channels "{cc}"')[0], want, cc)
            # never a DFS channel in any row
            for cc in ('CN', 'TR', 'BD', 'ID', 'US'):
                got = self.call(shell, f'wifi_ap5_channels {cc}')[0].split()
                self.assertFalse([c for c in got if 52 <= int(c) <= 144], cc)

    def test_dfs(self):
        for shell in self.each_shell():
            for c in FIVE:
                out = self.call(shell, f'wifi_ap5_dfs {c} && echo yes || echo no')[0]
                self.assertEqual(out, 'yes' if 52 <= c <= 144 else 'no', c)
            self.assertEqual(self.call(shell, 'wifi_ap5_dfs "" && echo yes || echo no')[0], 'no')

    def test_default(self):
        for shell in self.each_shell():
            for cc, want in (('CN', '36'), ('TR', '36'), ('BD', '149'), ('00', '')):
                self.assertEqual(self.call(shell, f'wifi_ap5_default {cc}')[0], want, cc)

    def test_resolve(self):
        for shell in self.each_shell():
            # the user's report: channel 60 in CN
            out, err = self.call(shell, 'wifi_ap5_resolve CN 60')
            self.assertEqual(out, '36')
            self.assertEqual(err, 'hotspot: 5 GHz channel 60 is a DFS channel (radar detection), which this Wi-Fi '
                                  'driver cannot do: using channel 36')
            for ch in (52, 64, 100, 120, 144):
                out, err = self.call(shell, f'wifi_ap5_resolve CN {ch}')
                self.assertEqual(out, '36', ch)
                self.assertIn('DFS', err)
            # 36 at 80 MHz or nothing: never 149
            out, err = self.call(shell, 'wifi_ap5_resolve BD 100; echo rc=$?')
            self.assertEqual((out, err), ('rc=1', 'hotspot: DFS channel 100: channel 36 / 80 MHz is not permitted in '
                                                  'country BD; no narrower fallback'))
            self.assertEqual(self.call(shell, 'wifi_ap5_channels() { echo "36 40 149 153"; }; '
                                              'wifi_ap5_resolve XX 60; echo rc=$?')[0], 'rc=1')
            # allowed: kept, nothing said
            for cc, ch in (('CN', 36), ('CN', 48), ('CN', 149), ('CN', 165), ('TR', 44), ('BD', 161)):
                self.assertEqual(self.call(shell, f'wifi_ap5_resolve {cc} {ch}'), (str(ch), ''), (cc, ch))
            # auto (or nothing) is the default, silently
            for v in ('auto', '', '0'):
                self.assertEqual(self.call(shell, f'wifi_ap5_resolve CN "{v}"'), ('36', ''), v)
            self.assertEqual(self.call(shell, 'wifi_ap5_resolve BD auto'), ('149', ''))
            # not DFS, but not this country's (or not 5 GHz at all)
            out, err = self.call(shell, 'wifi_ap5_resolve TR 149')
            self.assertEqual((out, err), ('36', 'hotspot: 5 GHz channel 149 is not available to the hotspot in '
                                                'country TR: using channel 36'))
            self.assertEqual(self.call(shell, 'wifi_ap5_resolve CN 6')[0], '36')
            # no 5 GHz channel at all: empty, and why
            out, err = self.call(shell, 'wifi_ap5_resolve 00 60; echo rc=$?')
            self.assertEqual(out, 'rc=1')
            self.assertIn('channel 36 / 80 MHz is not permitted in country 00', err)
            out, err = self.call(shell, 'wifi_ap5_resolve 00 149')
            self.assertEqual(out, '')
            self.assertIn('country 00 has no 5 GHz channel without DFS', err)
            self.assertEqual(self.call(shell, 'wifi_ap5_resolve 00 auto'), ('', ''))

    def test_width(self):
        for shell in self.each_shell():
            for cc, ch, w in (('CN', 36, '80'), ('CN', 48, '80'), ('CN', 149, '80'), ('CN', 161, '80'),
                              ('CN', 165, '20'), ('TR', 40, '80'), ('ID', 157, '80'), ('BD', 153, '80'),
                              ('BD', 165, '20'), ('CN', 60, '20')):
                self.assertEqual(self.call(shell, f'wifi_ap5_width {cc} {ch}')[0], w, (cc, ch))
            # a block the country allows only partly: the pair, else 20 MHz (no such country today: a fake one)
            out = self.call(shell, 'wifi_ap5_channels() { echo "36 40 44"; }; '
                                   'for c in 36 40 44; do wifi_ap5_width XX $c; done')[0]
            self.assertEqual(out.split(), ['40', '40', '20'])


UCI = r'''
[ "$1" = -q ] && shift
case "$1" in
    show) grep -q '^wireless\.radio0\.' "$STUBLOG/uci.state" 2>/dev/null && echo "wireless.radio0=wifi-device" ;;
    get) v=$(sed -n "s/^$2=//p" "$STUBLOG/uci.state" 2>/dev/null | head -n1); [ -n "$v" ] || exit 1; echo "$v" ;;
    batch) cat >> "$STUBLOG/uci.batch" ;;
    *) echo "$*" >> "$STUBLOG/uci.batch" ;;
esac
exit 0
'''


class OpenWrt(ShellTest):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        (self.root / 'etc' / 'mu300').mkdir(parents=True)
        (self.root / 'etc' / 'config').mkdir()
        (self.root / 'sys' / 'class' / 'ieee80211' / 'phy0').mkdir(parents=True)
        self.stub('uci', UCI)
        self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/wifi.log"')
        self.stub('sleep', 'exit 0')
        self.stub('logger', 'exit 0')

    def state(self, text):
        (self.tmp / 'uci.state').write_text(text)
        for f in ('uci.batch',):
            (self.tmp / f).unlink(missing_ok=True)

    def batch(self):
        p = self.tmp / 'uci.batch'
        return p.read_text() if p.exists() else ''

    def config(self, shell, country=None):
        (self.root / 'etc' / 'mu300' / 'wifi-configured').unlink(missing_ok=True)
        self.state(f'wireless.radio0.country={country}\n' if country else '')
        r = self.script(shell, BIN / 'openwrt-wifi-config', MU300_SYSROOT=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_first_config(self):
        for shell in self.each_shell():
            r = self.config(shell, 'CN')
            b = self.batch()
            self.assertIn("set wireless.radio0.band='5g'\n", b)
            self.assertIn("set wireless.radio0.channel='36'\n", b)
            self.assertIn("set wireless.radio0.htmode='VHT80'\n", b)
            self.assertIn("set wireless.radio0.channels='36 40 44 48 149 153 157 161 165'\n", b)
            self.assertIn("set wireless.radio0.acs_exclude_dfs='1'\n", b)
            self.assertIn("set wireless.radio0.country='CN'\n", b)
            self.assertRegex(b, r"set wireless.ap0.key='[A-HJ-NP-Za-km-z2-9]{12}'\n")
            self.assertIn('wireless configured: 5g channel 36', r.stdout)
            self.assertTrue((self.root / 'etc' / 'mu300' / 'wifi-configured').exists())
            # the country's default channel without DFS; a radio without a country is TR's
            for cc, ch, ht, chans in (('JP', '36', 'VHT80', '36 40 44 48'),
                                      ('BD', '149', 'VHT80', '149 153 157 161 165'),
                                      (None, '36', 'VHT80', None)):
                self.config(shell, cc)
                b = self.batch()
                self.assertIn(f"set wireless.radio0.channel='{ch}'\n", b, cc)
                self.assertIn(f"set wireless.radio0.htmode='{ht}'\n", b, cc)
                self.assertIn(f"set wireless.radio0.country='{cc or 'TR'}'\n", b, cc)
                if chans:
                    self.assertIn(f"set wireless.radio0.channels='{chans}'\n", b, cc)
            # a country with no 5 GHz channel without DFS: 2.4 GHz, no 5 GHz channel list
            r = self.config(shell, '00')
            self.assertIn("set wireless.radio0.band='2g'\n", self.batch())
            self.assertIn("set wireless.radio0.channel='6'\n", self.batch())
            self.assertIn('delete wireless.radio0.channels\n', self.batch())
            self.assertIn('no 5 GHz channel without DFS in country 00', r.stderr)

    def test_dfs_channel_from_luci_rewritten_at_boot(self):
        (self.root / 'etc' / 'mu300' / 'wifi-configured').write_text('')
        for shell in self.each_shell():
            for ch, ht, cc in (('60', 'VHT80', 'CN'), ('52', 'HT40', 'CN'), ('120', 'VHT80', 'CN'),
                               ('100', 'HE80', 'TR'), ('64', 'NOHT', 'CN'), ('56', 'VHT20', 'CN')):
                self.state(f'wireless.radio0.band=5g\nwireless.radio0.channel={ch}\nwireless.radio0.htmode={ht}\n'
                           f'wireless.radio0.country={cc}\n')
                r = self.script(shell, BIN / 'openwrt-wifi-config', MU300_SYSROOT=self.root)
                self.assertEqual(r.returncode, 0, r.stderr)
                b = self.batch()
                self.assertIn("set wireless.radio0.channel='36'\n", b, ch)
                self.assertNotIn('htmode', b, ch)
                self.assertIn("set wireless.radio0.acs_exclude_dfs='1'\n", b, ch)
                self.assertIn('commit wireless', b)
                self.assertEqual(r.stderr.strip(), f'hotspot: channel {ch} is a DFS channel: using channel 36, keeping {ht}')
            # 36 cannot carry that width there (160 MHz never does without DFS; BD has no 36): nothing changes
            for ch, ht, cc in (('100', 'HE160', 'CN'), ('60', 'VHT80', 'BD'), ('60', 'VHT80', '00')):
                self.state(f'wireless.radio0.band=5g\nwireless.radio0.channel={ch}\nwireless.radio0.htmode={ht}\n'
                           f'wireless.radio0.country={cc}\n')
                r = self.script(shell, BIN / 'openwrt-wifi-config', MU300_SYSROOT=self.root)
                self.assertEqual((r.returncode, self.batch()), (1, ''), (ch, ht, cc))
                self.assertIn(f'DFS channel {ch}: channel 36 with {ht} is not permitted in country {cc}', r.stderr)
            # left alone: a non-DFS channel, auto, 2.4 GHz, no radio
            for st in ('wireless.radio0.band=5g\nwireless.radio0.channel=149\nwireless.radio0.country=CN\n',
                       'wireless.radio0.band=5g\nwireless.radio0.channel=auto\nwireless.radio0.country=CN\n',
                       'wireless.radio0.band=2g\nwireless.radio0.channel=11\n', ''):
                self.state(st)
                r = self.script(shell, BIN / 'openwrt-wifi-config', MU300_SYSROOT=self.root)
                self.assertEqual((r.returncode, self.batch(), r.stderr), (0, '', ''), st)

    def test_wifi_band(self):
        for shell in self.each_shell():
            for cc, ch, ht, chans in (('CN', '36', 'VHT80', '36 40 44 48 149 153 157 161 165'),
                                      ('JP', '36', 'VHT80', '36 40 44 48'), ('BD', '149', 'VHT80', '149 153 157 161 165'),
                                      ('', '36', 'VHT80', '36 40 44 48')):
                self.state(f'wireless.radio0.band=2g\nwireless.radio0.country={cc}\n')
                r = self.script(shell, BIN / 'mu300-wifi-band', '5', MU300_SYSROOT=self.root)
                self.assertEqual((r.returncode, r.stdout.strip()), (0, 'hotspot: 5 GHz'), r.stderr)
                b = self.batch()
                self.assertIn(f"set wireless.radio0.channel='{ch}'\n", b, cc)
                self.assertIn(f"set wireless.radio0.htmode='{ht}'\n", b, cc)
                self.assertIn(f"set wireless.radio0.channels='{chans}'\n", b, cc)
                self.assertIn("set wireless.radio0.acs_exclude_dfs='1'\n", b, cc)
            self.state('wireless.radio0.band=5g\nwireless.radio0.country=CN\n')
            r = self.script(shell, BIN / 'mu300-wifi-band', '2.4', MU300_SYSROOT=self.root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('delete wireless.radio0.channels\n', self.batch())
            self.assertIn("set wireless.radio0.channel='6'\n", self.batch())
            # no 5 GHz channel without DFS: refused, nothing changed
            self.state('wireless.radio0.band=2g\nwireless.radio0.country=00\n')
            r = self.script(shell, BIN / 'mu300-wifi-band', '5', MU300_SYSROOT=self.root)
            self.assertEqual(r.returncode, 1)
            self.assertIn('no 5 GHz channel without DFS in country 00', r.stderr)
            self.assertEqual(self.batch(), '')


# The two layouts of hostapd.uc the patch meets, cut to what its hunks touch: the SAE build's (OpenWrt v25.12.5, which
# the image installs) and the feed's (one line more in device_capabilities()).
HOSTAPD_UC = """'use strict';

import {
	append, append_raw, append_vars, dump_config, flush_config, set_default,
	wiphy_info, wiphy_band
} from 'wifi.common';
import { validate } from 'wifi.validate';
import * as netifd from 'wifi.netifd';

function device_capabilities(config) {
	let phy = config.phy;

	phy = wiphy_info(phy);
	let band = wiphy_band(phy, config.band);

	phy_capabilities.ht_capa = band.ht_capa ?? 0;
	phy_features.radar_background = device_extended_features(phy.extended_features, NL80211_EXT_FEATURE_RADAR_BACKGROUND);
CIPHER}

function generate(config) {
	if (!config)
		die(`${config.path} is an unknown phy`);

	device_capabilities(config);

	append('driver', 'nl80211');

	set_device_defaults(config);

	device_log_append(config);

	device_country_code(config);
}
"""
CIPHER = '\tphy_features.cipher_gcmp256 = WLAN_CIPHER_SUITE_GCMP_256 in (phy.cipher_suites ?? []);\n'


class WifiScriptsPatch(unittest.TestCase):
    def test_build_applies_it(self):
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.assertIn('-v "$NODFSPATCH":/in/wifi-scripts-no-dfs.patch:ro', text)
        apply = 'patch --batch --fuzz=0 -d $R -p1 -i /in/wifi-scripts-no-dfs.patch'
        self.assertIn(apply, text)
        self.assertIn('grep -q "device_no_dfs(config);" $R/usr/share/ucode/wifi/hostapd.uc || {', text)
        # on the SAE build's wifi-scripts (installed first), after the copy of the overlay into $R, before the panel's
        # files and before the patch tool goes
        self.assertLess(text.index('apk add --allow-untrusted /in/wifi-sae/*.apk'), text.index(apply))
        self.assertLess(text.index('cp -a /in/overlay/. $R/'), text.index(apply))
        self.assertLess(text.index(apply), text.index('cp -a /in/luci-overlay/. $R/'))
        self.assertLess(text.index(apply), text.index('apk del patch'))

    def test_patch(self):
        p = PATCH.read_text()
        self.assertTrue(p.startswith('--- a/usr/share/ucode/wifi/hostapd.uc\n+++ b/usr/share/ucode/wifi/hostapd.uc\n'))
        added = '\n'.join(l[1:] for l in p.splitlines() if l.startswith('+') and not l.startswith('+++'))
        # the capability is read from the driver, not assumed: radar_detect_widths in an interface combination
        self.assertIn('phy.interface_combinations', added)
        self.assertIn('c.radar_detect_widths', added)
        self.assertIn('config.acs_exclude_dfs = true;', added)
        self.assertIn('device_no_dfs(config);', added)
        # 36 and nothing else; the width is never touched
        self.assertIn('config.channel = 36;', added)
        self.assertNotRegex(added, r'config\.channel = (?!36;)')
        self.assertNotRegex(added, r'config\.htmode\s*=(?!=)')

    @unittest.skipUnless(shutil.which('patch'), 'no patch')
    def test_applies_to_both_layouts_without_fuzz(self):
        for cipher in ('', CIPHER):
            with self.subTest(feed=bool(cipher)), tempfile.TemporaryDirectory() as d:
                f = Path(d) / 'usr/share/ucode/wifi/hostapd.uc'
                f.parent.mkdir(parents=True)
                f.write_text(HOSTAPD_UC.replace('CIPHER', cipher))
                r = subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', d, '-i', str(PATCH)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                text = f.read_text()
                self.assertEqual(text.count('function device_no_dfs(config)'), 1)
                self.assertEqual(text.count('\tdevice_no_dfs(config);\n'), 1)
                self.assertLess(text.index('\tset_device_defaults(config);'), text.index('\tdevice_no_dfs(config);'))
                self.assertLess(text.index('\tdevice_no_dfs(config);'), text.index('\tdevice_log_append(config);'))
                self.assertIn('wiphy_info, wiphy_band, log\n', text)


# what the container runs: install, patch, a wifi.common whose wiphy_info() reads $FAKE_WIPHY, then run.uc per case
GENERATE = r'''
mkdir -p /var/lock /var/run /tmp /fake/wifi
apk update >/dev/null && apk add $PKGS patch >/dev/null 2>&1 || exit 1
patch --batch --fuzz=0 -p1 -d / -i /in/wifi-scripts-no-dfs.patch || exit 1
grep -q "device_no_dfs(config);" /usr/share/ucode/wifi/hostapd.uc || exit 1
sed -e "s/^export function wiphy_info(phy) {/function nl_wiphy_info(phy) {/" /usr/share/ucode/wifi/common.uc > /fake/wifi/common.uc
grep -q "^function nl_wiphy_info" /fake/wifi/common.uc || exit 1
echo "export function wiphy_info(phy) { return json(fs.readfile(getenv(\"FAKE_WIPHY\"))); };" >> /fake/wifi/common.uc
if [ -n "$MODULE" ]; then
    # the driver module of phy0, under /fake (the container has no wiphy in /sys)
    sed -e "s|/sys/class/ieee80211/|/fake/sys/class/ieee80211/|" /usr/share/ucode/wifi/ap.uc > /fake/wifi/ap.uc
    sed -e "s|/sys/module/sprd_wlan_combo|/fake/sys/module/sprd_wlan_combo|" /usr/share/ucode/wifi/unisoc-sae.uc > /fake/wifi/unisoc-sae.uc
    grep -q /fake/sys/class /fake/wifi/ap.uc && grep -q /fake/sys/module /fake/wifi/unisoc-sae.uc || exit 1
    mkdir -p /fake/sys/class/ieee80211/phy0/device/driver /fake/sys/module/$MODULE
    ln -s /fake/sys/module/$MODULE /fake/sys/class/ieee80211/phy0/device/driver/module
fi
for f in /t/cases/*.data.json; do
    n=$(basename "$f" .data.json)
    rm -f /var/run/hostapd-phy0.conf
    FAKE_DATA=$f FAKE_WIPHY=/t/cases/$n.wiphy.json FAKE_CC=CN \
        ucode -L "/fake/*.uc" -L "/usr/share/ucode/*.uc" /fx/run.uc > /t/out/$n.conf 2>&1 || echo "rc=$?" >> /t/out/$n.conf
done
'''


@unittest.skipUnless(os.environ.get('MU300_WRT_IMAGE') and shutil.which('docker'),
                     'MU300_WRT_IMAGE (an OpenWrt 25.12 arm64 docker image) not set')
class WifiScriptsInOpenWrt(unittest.TestCase):
    """The patched wifi-scripts, for a fake wiphy: the hostapd configuration they write (the feed's wifi-scripts, and
    with MU300_WIFI_SAE_APKS the SAE build's)."""

    @staticmethod
    def wiphy(radar, flags):
        comb = {'maxnum': 3, 'num_channels': 2}
        if radar:
            comb['radar_detect_widths'] = 63
        freqs = []
        for c in FIVE:
            e = {'freq': 5000 + 5 * c}
            e.update(flags.get(c, {'radar': True, 'no_ir': True} if 52 <= c <= 144 else {}))
            freqs.append(e)
        return {'extended_features': [0] * 16, 'cipher_suites': [], 'interface_combinations': [comb],
                'wiphy_bands': [{'freqs': [{'freq': 2412}]}, {'freqs': freqs, 'ht_capa': 0x1fe,
                                                               'vht_capa': 0x339071b2}]}

    @staticmethod
    def data(channel, htmode, extra=None):
        cfg = {'band': '5g', 'channel': str(channel), 'htmode': htmode, 'country': 'CN'}
        cfg.update(extra or {})
        return {'phy': 'phy0', 'phy_suffix': '', 'vif_phy_suffix': '', 'config': cfg,
                'interfaces': {'ap0': {'config': {'mode': 'ap', 'ssid': 'MU300-TEST', 'encryption': 'psk2',
                                                  'key': '12345678', 'ifname': 'wlan0', 'network': ['lan']},
                                       'bridge': 'br-lan', 'vlans': {}, 'stas': {}}}}

    @staticmethod
    def generate(cases, sae_apks=None, module=''):
        """{name: (wiphy, data)} -> {name: what run.uc printed}. sae_apks: a directory with the SAE build's APKs,
        installed instead of the feed's wifi-scripts; module: the driver module the AP generator finds for phy0."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'cases').mkdir()
            (d / 'out').mkdir()
            for n, (wiphy, data) in cases.items():
                (d / 'cases' / f'{n}.wiphy.json').write_text(json.dumps(wiphy))
                (d / 'cases' / f'{n}.data.json').write_text(json.dumps(data))
            argv = ['docker', 'run', '--rm', '--platform', 'linux/arm64', '-v', f'{PATCH.parent}:/in:ro',
                    '-v', f'{FIXTURES}:/fx:ro', '-v', f'{d}:/t', '-e', f'MODULE={module}']
            if sae_apks:
                argv += ['-v', f'{sae_apks}:/apks:ro', '-e', 'PKGS=--allow-untrusted /apks/*.apk']
            else:
                argv += ['-e', 'PKGS=wifi-scripts']
            r = subprocess.run(argv + [os.environ['MU300_WRT_IMAGE'], '/bin/sh', '-c', GENERATE],
                               capture_output=True, text=True, timeout=900)
            if r.returncode:
                raise AssertionError(r.stdout + r.stderr)
            return {n: (d / 'out' / f'{n}.conf').read_text() for n in cases}

    @staticmethod
    def conf(text):
        return dict(l.split('=', 1) for l in text.splitlines() if '=' in l and not l.startswith('#'))

    def check_dfs(self, sae_apks):
        no36 = {c: {'disabled': True} for c in (36, 40, 44, 48)}
        cases = {
            # name: (driver detects radar, channel flags, channel, htmode, extra)
            'cn60': (False, {}, 60, 'VHT80', None),
            'cn60-radar': (True, {}, 60, 'VHT80', None),
            'cn36': (False, {}, 36, 'VHT80', None),
            'cn149': (False, {}, 149, 'VHT80', None),
            'cn52-he160': (False, {}, 52, 'HE160', None),
            'cn120-vht40': (False, {}, 120, 'VHT40', None),
            'cn100-ht20': (False, {}, 100, 'HT20', None),
            'auto': (False, {}, 'auto', 'VHT80', None),
            'auto-radar': (True, {}, 'auto', 'VHT80', None),
            'auto-list': (False, {}, 'auto', 'VHT80', {'channels': '36 40 44 48 149 153 157 161 165'}),
            'no36-60': (False, no36, 60, 'VHT80', None),
            'no80-60': (False, {44: {'no_80mhz': True}}, 60, 'VHT80', None),
        }
        out = self.generate({n: (self.wiphy(radar, flags), self.data(ch, ht, extra))
                             for n, (radar, flags, ch, ht, extra) in cases.items()}, sae_apks)

        def conf(n):
            self.assertNotIn('rc=', out[n], n + ': ' + out[n])
            return out[n], self.conf(out[n])

        text, c = conf('cn60')
        self.assertIn('wifi-scripts: channel 60 is a DFS channel and this driver cannot detect radar: using channel 36, '
                      'keeping VHT80', text)
        self.assertEqual((c['channel'], c['vht_oper_chwidth'], c['vht_oper_centr_freq_seg0_idx']), ('36', '1', '42'))
        # a driver that can detect radar keeps the channel (hostapd does the CAC)
        text, c = conf('cn60-radar')
        self.assertEqual((c['channel'], c['vht_oper_centr_freq_seg0_idx']), ('60', '58'))
        self.assertNotIn('DFS channel', text)
        for n, ch, seg0 in (('cn36', '36', '42'), ('cn149', '149', '155')):
            text, c = conf(n)
            self.assertEqual((c['channel'], c['vht_oper_centr_freq_seg0_idx']), (ch, seg0), n)
            self.assertNotIn('DFS channel', text)
        # the width as configured
        _, c = conf('cn120-vht40')
        self.assertEqual((c['channel'], c['vht_oper_chwidth'], c['ht_capab'][:7]), ('36', '0', '[HT40+]'))
        _, c = conf('cn100-ht20')
        self.assertEqual(c['channel'], '36')
        self.assertNotIn('[HT40', c.get('ht_capab', ''))
        # auto: ACS without the DFS channels (and the channel list as given)
        _, c = conf('auto')
        self.assertEqual((c['channel'], c['acs_exclude_dfs']), ('0', '1'))
        _, c = conf('auto-radar')
        self.assertEqual((c['channel'], c['acs_exclude_dfs']), ('0', '0'))
        _, c = conf('auto-list')
        self.assertEqual((c['acs_exclude_dfs'], c['chanlist']), ('1', '36 40 44 48 149 153 157 161 165'))
        # 36 cannot carry the width (160 MHz on 36 needs 52-64): the setup fails with why and writes no access point;
        # never 149, never narrower
        for n, why in (('cn52-he160', 'channel 36 with HE160 is not permitted'),
                       ('no36-60', 'channel 36 with VHT80 is not permitted'),
                       ('no80-60', 'channel 36 with VHT80 is not permitted')):
            self.assertIn('rc=', out[n], n)
            self.assertIn(why, out[n], n)
            self.assertNotIn('interface=wlan0', out[n], n)

    def test_feed_wifi_scripts(self):
        self.check_dfs(None)

    @unittest.skipUnless(os.environ.get('MU300_WIFI_SAE_APKS'), 'MU300_WIFI_SAE_APKS (openwrt/hostapd) not set')
    def test_sae_wifi_scripts(self):
        self.check_dfs(os.environ['MU300_WIFI_SAE_APKS'])


if __name__ == '__main__':
    unittest.main()
