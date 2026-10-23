"""The U30 Air's firmware SAE offload is disabled, in every layer: the PMK record the firmware hands back cannot be
told from one a client sends, so the hotspot is WPA2-PSK only on every device (F50, U30 Air: the SC2355). The driver's
part is tested with the kernel tree.

* no hostapd with the import is built or installed, and the build refuses MU300_SAE_APK_DIR;
* the wifi-scripts refuse an SAE access point (static, and in the OpenWrt feed's scripts with MU300_WRT_IMAGE);
* openwrt-wifi-wpa2-only: a hotspot left on SAE becomes WPA2-PSK (CCMP) with the same password at boot, or goes off
  (never open), with one log line and the notice for LuCI; the notice module itself under Node;
* mu300-api never puts SAE on the hotspot."""
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest

WIFI_PATCH = TOP / 'openwrt/patches/wifi-scripts-wpa2-only.patch'
MIGRATE = BIN / 'openwrt-wifi-wpa2-only'
NOTICE_JS = TOP / 'openwrt/overlay/www/luci-static/resources/view/status/include/05_mu300_wpa2.js'


class NoImportIsBuilt(ShellTest):
    def test_the_hostapd_patch_and_its_recipe_are_gone(self):
        self.assertFalse((TOP / 'openwrt/patches/hostapd').exists())
        self.assertFalse((TOP / 'openwrt/build-u30-sae.sh').exists())
        self.assertFalse((BIN / 'openwrt-wifi-sae-guard').exists())
        build = (TOP / 'openwrt/build-rootfs.sh').read_text()
        for gone in ('/in/u30-sae', 'wpad-basic-openssl', 'SAE_APKS'):
            self.assertNotIn(gone, build)
        self.assertIn('apk add wpad-basic-mbedtls wifi-scripts', build)

    def test_no_code_turns_it_on(self):
        """u30_sae_offload is named only where it is refused or removed (and in the docs and tests)"""
        allowed = {'openwrt/patches/wifi-scripts-wpa2-only.patch', 'openwrt/opt/mu300/bin/openwrt-wifi-wpa2-only',
                   'openwrt/opt/mu300/bin/mu300-api'}
        out = subprocess.run(['git', '-C', str(TOP), 'grep', '-l', 'u30_sae_offload', '--', 'openwrt', ':!openwrt/docs',
                              ':!openwrt/tests'], capture_output=True, text=True)
        if out.returncode not in (0, 1):
            self.skipTest('not a git checkout')
        self.assertEqual(set(out.stdout.split()) - allowed, set())


class WifiScriptsRefuseSae(unittest.TestCase):
    def test_patch(self):
        p = WIFI_PATCH.read_text()
        self.assertTrue(p.startswith('--- a/usr/share/ucode/wifi/hostapd.uc\n+++ b/usr/share/ucode/wifi/hostapd.uc\n'))
        self.assertIn("[ 'sae', 'psk3', 'sae-mixed', 'psk3-mixed', 'sae-compat' ]", p)
        self.assertIn("'u30_sae_offload*'", p)
        self.assertIn('+\t\tif (mu300_sae_refused(k, interface.config))\n+\t\t\tcontinue;', p)
        self.assertIn('WPA2-PSK only', p)
        build = (TOP / 'openwrt/build-rootfs.sh').read_text()
        self.assertIn('-v "$WPA2PATCH":/in/wifi-scripts-wpa2-only.patch:ro', build)
        # after the DFS patch, whose log import it uses, and checked like it
        self.assertLess(build.index('-i /in/wifi-scripts-no-dfs.patch'), build.index('-i /in/wifi-scripts-wpa2-only.patch'))
        self.assertIn('grep -q "mu300_sae_refused(k, interface.config)" $R/usr/share/ucode/wifi/hostapd.uc ||', build)

    @unittest.skipUnless(os.environ.get('MU300_WRT_IMAGE') and shutil.which('docker'),
                         'MU300_WRT_IMAGE (an OpenWrt 25.12 arm64 docker image) not set')
    def test_feed_scripts_start_wpa2_and_refuse_sae(self):
        import test_wifi_channels as channels
        fake = channels.WifiScriptsInOpenWrt
        cases = {'psk2': {}, 'psk2-ccmp': {'encryption': 'psk2+ccmp'}, 'sae': {'encryption': 'sae'},
                 'sae-mixed': {'encryption': 'sae-mixed'}, 'sae-compat': {'encryption': 'sae-compat'},
                 'psk3': {'encryption': 'psk3'},
                 'offload': {'encryption': 'psk2', 'hostapd_bss_options': ['u30_sae_offload=1']}}
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'cases').mkdir()
            (d / 'out').mkdir()
            for name, iface in cases.items():
                data = fake.data(36, 'VHT80')
                data['interfaces']['ap0']['config'].update(iface)
                (d / 'cases' / f'{name}.wiphy.json').write_text(json.dumps(fake.wiphy(False, {})))
                (d / 'cases' / f'{name}.data.json').write_text(json.dumps(data))
            script = r"""
mkdir -p /var/lock /var/run /tmp /fake/wifi
apk update >/dev/null && apk add wifi-scripts patch >/dev/null 2>&1 || exit 1
patch --batch --fuzz=0 -p1 -d / -i /in/wifi-scripts-no-dfs.patch || exit 1
patch --batch --fuzz=0 -p1 -d / -i /in/wifi-scripts-wpa2-only.patch || exit 1
sed -e "s/^export function wiphy_info(phy) {/function nl_wiphy_info(phy) {/" /usr/share/ucode/wifi/common.uc > /fake/wifi/common.uc
echo "export function wiphy_info(phy) { return json(fs.readfile(getenv(\"FAKE_WIPHY\"))); };" >> /fake/wifi/common.uc
for f in /t/cases/*.data.json; do
    n=$(basename "$f" .data.json)
    rm -f /var/run/hostapd-phy0.conf
    FAKE_DATA=$f FAKE_WIPHY=/t/cases/$n.wiphy.json FAKE_CC=CN \
        ucode -L "/fake/*.uc" -L "/usr/share/ucode/*.uc" /fx/run.uc > /t/out/$n.conf 2>&1 || echo "rc=$?" >> /t/out/$n.conf
done
"""
            r = subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/arm64',
                                '-v', f'{WIFI_PATCH.parent}:/in:ro', '-v', f'{channels.FIXTURES}:/fx:ro', '-v', f'{d}:/t',
                                os.environ['MU300_WRT_IMAGE'], '/bin/sh', '-c', script],
                               capture_output=True, text=True, timeout=900)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for name in cases:
                text = (d / 'out' / f'{name}.conf').read_text()
                with self.subTest(case=name):
                    self.assertNotIn('rc=', text)
                    conf = dict(l.split('=', 1) for l in text.splitlines() if '=' in l and not l.startswith('#'))
                    if name.startswith('psk2'):
                        self.assertEqual((conf['wpa_key_mgmt'], conf.get('wpa_pairwise', conf.get('rsn_pairwise'))), ('WPA-PSK', 'CCMP'), text)
                        self.assertNotIn('refused', text, text)
                        self.assertIn('ssid2="MU300-TEST"', text)
                    else:
                        self.assertIn("wifi-scripts: ap0: encryption '", text)
                        self.assertIn('WPA2-PSK only', text)
                        self.assertNotIn('interface=wlan0', text)
                        self.assertNotIn('wpa_passphrase', text)
                        self.assertNotIn('wpa_key_mgmt', text)
                        self.assertNotIn('12345678', text)


UCI = r"""
[ "$1" = -q ] && shift
f=$STUBLOG/uci.state
case "$1" in
    show) if [ -n "${2:-}" ]; then grep "^$2[.=]" "$f"; else cat "$f"; fi; exit 0 ;;
    get) v=$(sed -n "s/^$2='\(.*\)'$/\1/p" "$f" | head -n1); [ -n "$v" ] || exit 1; echo "$v"; exit 0 ;;
    set) k=${2%%=*}
         case "$2" in *.encryption=*) [ -z "${UCI_STUCK:-}" ] || { echo "$*" >> "$STUBLOG/uci.log"; exit 1; } ;; esac
         grep -v "^$k=" "$f" > "$f.n"; echo "$k='${2#*=}'" >> "$f.n"; mv "$f.n" "$f" ;;
    delete) grep -v "^$2=" "$f" > "$f.n"; mv "$f.n" "$f" ;;
    del_list) k=${2%%=*}; sed -e "/^$k=/s/ *'${2#*=}'//" -e "/^$k=\$/d" "$f" > "$f.n"; mv "$f.n" "$f" ;;
    commit) ;;
esac
echo "$*" >> "$STUBLOG/uci.log"
exit 0
"""
PASSWORD = 'synthetic-test-password'
# what `uci show wireless` printed for the documented opt-in, with a synthetic passphrase
SAE_STATE = f"""wireless.radio0=wifi-device
wireless.radio0.band='5g'
wireless.ap0=wifi-iface
wireless.ap0.device='radio0'
wireless.ap0.mode='ap'
wireless.ap0.ifname='wlan0'
wireless.ap0.ssid='MU300-TEST'
wireless.ap0.encryption='sae'
wireless.ap0.key='{PASSWORD}'
wireless.ap0.ieee80211w='2'
wireless.ap0.sae_pwe='0'
wireless.ap0.hostapd_bss_options='max_listen_interval=10' 'u30_sae_offload=1'
"""
WPA2_STATE = f"""wireless.radio0=wifi-device
wireless.ap0=wifi-iface
wireless.ap0.device='radio0'
wireless.ap0.mode='ap'
wireless.ap0.ifname='wlan0'
wireless.ap0.encryption='psk2'
wireless.ap0.key='{PASSWORD}'
"""


class Migration(ShellTest):
    """openwrt-wifi-wpa2-only migrate: SAE becomes WPA2-PSK (CCMP) with the same password, or off; never open."""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        for d in ('etc/mu300', 'etc/config'):
            (self.root / d).mkdir(parents=True)
        (self.root / 'etc/config/wireless').write_text('the file as it was\n')
        self.stub('uci', UCI)
        self.stub('logger', 'echo "$*" >> "$STUBLOG/logger.log"')
        self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/wifi.log"')

    def migrate(self, shell, state, **env):
        (self.tmp / 'uci.state').write_text(state)
        for f in ('uci.log', 'logger.log', 'wifi.log'):
            (self.tmp / f).unlink(missing_ok=True)
        for f in ('wpa3-disabled', 'wireless.before-wpa2'):
            (self.root / 'etc/mu300' / f).unlink(missing_ok=True)
        r = self.script(shell, MIGRATE, 'migrate', MU300_SYSROOT=self.root, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def log(self, name):
        p = self.tmp / name
        return p.read_text().splitlines() if p.exists() else []

    def state(self):
        return (self.tmp / 'uci.state').read_text()

    def notice(self):
        p = self.root / 'etc/mu300/wpa3-disabled'
        return dict(l.split('=', 1) for l in p.read_text().splitlines()) if p.exists() else None

    def assertUntouched(self, r, state):
        self.assertEqual(r.stderr, '')
        self.assertEqual(self.state(), state)
        self.assertEqual([c for c in self.log('uci.log') if not c.startswith(('show', 'get '))], [])
        self.assertEqual(self.log('logger.log') + self.log('wifi.log'), [])
        self.assertEqual(sorted(p.name for p in (self.root / 'etc/mu300').iterdir()), [])

    def assertNoPassword(self, r):
        written = r.stdout + r.stderr + '\n'.join(self.log('logger.log')) + (self.root / 'etc/mu300/wpa3-disabled').read_text()
        self.assertNotIn(PASSWORD, written)

    def test_sae_hotspot_becomes_wpa2_with_the_same_password(self):
        for shell in self.each_shell():
            r = self.migrate(shell, SAE_STATE)
            s = self.state()
            self.assertIn("wireless.ap0.encryption='psk2+ccmp'\n", s)
            self.assertIn(f"wireless.ap0.key='{PASSWORD}'\n", s)
            self.assertIn("wireless.ap0.hostapd_bss_options='max_listen_interval=10'\n", s)
            for gone in ('u30_sae_offload', 'ieee80211w', 'sae_pwe', 'disabled'):
                self.assertNotIn(gone, s)
            self.assertEqual(self.log('uci.log')[-1], 'commit wireless')
            # one line, a warning: the hotspot is up and encrypted
            self.assertEqual(len(self.log('logger.log')), 1)
            self.assertIn('-p daemon.warn', self.log('logger.log')[0])
            self.assertIn('ap0 is now WPA2-PSK (CCMP) with the same password', r.stderr)
            n = self.notice()
            self.assertEqual((n['state'], n['migrated'], n['down']), ('migrated', 'ap0', ''))
            self.assertRegex(n['time'], r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$')
            self.assertEqual((self.root / 'etc/mu300/wpa3-disabled').stat().st_mode & 0o777, 0o644)
            saved = self.root / 'etc/mu300/wireless.before-wpa2'
            self.assertEqual(saved.read_text(), 'the file as it was\n')
            self.assertEqual(saved.stat().st_mode & 0o077, 0)
            self.assertNoPassword(r)
            # Wi-Fi is not restarted here: mu300-hw's "wifi down; wifi up" comes right after
            self.assertEqual(self.log('wifi.log'), [])

    def test_every_sae_mode_and_the_option_alone(self):
        for shell in self.each_shell():
            for enc in ('sae-mixed', 'sae-compat', 'psk3', 'psk3-mixed', 'sae+ccmp', 'psk2'):
                state = SAE_STATE.replace("encryption='sae'", f"encryption='{enc}'")
                with self.subTest(encryption=enc):
                    self.migrate(shell, state)
                    self.assertIn("wireless.ap0.encryption='psk2+ccmp'\n", self.state())
                    self.assertNotIn('u30_sae_offload', self.state())
                    self.assertEqual(self.notice()['state'], 'migrated')

    def test_without_a_usable_password_the_hotspot_goes_off_not_open(self):
        for shell in self.each_shell():
            for key in (None, 'short', 'x' * 64, 'caf\u00e9-password'):
                state = SAE_STATE.replace(f"wireless.ap0.key='{PASSWORD}'\n", '' if key is None else f"wireless.ap0.key='{key}'\n")
                with self.subTest(key=key):
                    r = self.migrate(shell, state)
                    s = self.state()
                    self.assertIn("wireless.ap0.disabled='1'\n", s)
                    self.assertIn("wireless.ap0.encryption='psk2+ccmp'\n", s)
                    self.assertNotIn("encryption='none'", s)
                    self.assertIn('-p daemon.err', self.log('logger.log')[0])
                    self.assertIn('off, not open', r.stderr)
                    n = self.notice()
                    self.assertEqual((n['state'], n['down'], n['migrated']), ('down', 'ap0', ''))
            # a raw key of 64 hex digits is a WPA2 key
            self.migrate(shell, SAE_STATE.replace(PASSWORD, 'a' * 64))
            self.assertEqual(self.notice()['state'], 'migrated')

    def test_a_change_that_did_not_stick_is_reported_down(self):
        for shell in self.each_shell():
            r = self.migrate(shell, SAE_STATE, UCI_STUCK=1)
            self.assertEqual(self.notice()['state'], 'down')
            self.assertIn('-p daemon.err', self.log('logger.log')[0])
            self.assertIn('the change was not saved', r.stderr)

    def test_nothing_to_do(self):
        for shell in self.each_shell():
            for state in (WPA2_STATE, SAE_STATE.replace("mode='ap'", "mode='sta'"),
                          WPA2_STATE.replace("encryption='psk2'", "encryption='none'")):
                self.assertUntouched(self.migrate(shell, state), state)
            # once migrated, the next boot does nothing (one log line, once)
            self.migrate(shell, SAE_STATE)
            after = self.state()
            (self.tmp / 'logger.log').unlink()
            r = self.script(shell, MIGRATE, 'migrate', MU300_SYSROOT=self.root)
            self.assertEqual((r.returncode, r.stderr, self.state()), (0, '', after))
            self.assertEqual(self.log('logger.log'), [])

    def test_boot_migrates_before_wifi_up_and_nothing_else_checks_the_offload(self):
        hw = (TOP / 'openwrt/overlay/etc/init.d/mu300-hw').read_text()
        self.assertLess(hw.index('openwrt-wifi-config'), hw.index('openwrt-wifi-wpa2-only migrate'))
        self.assertLess(hw.index('openwrt-wifi-wpa2-only migrate'), hw.index('\twifi down >>'))
        post = (TOP / 'openwrt/overlay/etc/init.d/mu300-post').read_text()
        self.assertNotIn('sae', post)


@unittest.skipUnless(shutil.which('node'), 'no node')
class NoticeModule(unittest.TestCase):
    """the LuCI notice (Status > Overview and the panel's home): nothing without the file, the message in the
    language of the page (English, Chinese), the dismissal removes the file"""

    def render(self, content, lang):
        harness = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const content = process.argv[3] === '-' ? null : process.argv[3];
const removed = [];
function E(tag, attrs, children) {
  const kids = (Array.isArray(children) ? children : children == null ? [] : [children]);
  return { tag, attrs, style: {}, children: kids, appendChild(c) { kids.push(c); } };
}
global.E = E;
global.document = { documentElement: { getAttribute: () => process.argv[4] } };
global.L = { resolveDefault: (p, d) => Promise.resolve(p).catch(() => d) };
const baseclass = { extend: (o) => o };
const fsmod = { read: (p) => content == null ? Promise.reject(new Error('NotFound')) : Promise.resolve(content),
                remove: (p) => { removed.push(p); return Promise.resolve(0); } };
const ui = { createHandlerFn: (ctx, fn) => fn.bind(ctx) };
const body = src.split('\n').filter(l => !/^'require /.test(l) && l !== "'use strict';").join('\n');
const mod = new Function('baseclass', 'fs', 'ui', body)(baseclass, fsmod, ui);
function text(n) { return typeof n === 'string' ? n : (n.children || []).map(text).join(''); }
mod.load().then(async (n) => {
  const box = mod.render(n);
  const out = { title: mod.title, box: box ? text(box) : null, cls: box ? box.attrs['class'] : null };
  if (box) { await box.children[1].attrs.click(); out.removed = removed; out.hidden = box.style.display; }
  console.log(JSON.stringify(out));
});
"""
        with tempfile.TemporaryDirectory() as d:
            h = Path(d) / 'h.js'
            h.write_text(harness)
            r = subprocess.run(['node', str(h), str(NOTICE_JS), content if content is not None else '-', lang],
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)

    def test_no_file_no_notice(self):
        self.assertIsNone(self.render(None, 'en')['box'])
        self.assertIsNone(self.render('garbage\n', 'en')['box'])

    def test_messages(self):
        migrated = 'state=migrated\ntime=2026-10-17 10:00:00\nmigrated=ap0\ndown=\n'
        down = 'state=down\ntime=2026-10-17 10:00:00\nmigrated=\ndown=ap0\n'
        o = self.render(migrated, 'en')
        self.assertIn('WPA2-PSK (CCMP) with the same password: ap0', o['box'])
        self.assertIn('2026-10-17 10:00:00', o['box'])
        self.assertIn('warning', o['cls'])
        self.assertEqual((o['removed'], o['hidden']), (['/etc/mu300/wpa3-disabled'], 'none'))
        o = self.render(down, 'en')
        self.assertIn('ap0 is off, not open', o['box'])
        self.assertIn('error', o['cls'])
        zh = self.render(migrated, 'zh-cn')
        self.assertIn('WPA2-PSK\uff08CCMP\uff09', zh['box'])
        self.assertIn('\u5b89\u5168', zh['title'])
        self.assertNotIn('FINDINGS', zh['box'])
        # any other language: English
        for lang in ('de', 'tr'):
            self.assertIn('Dismiss', self.render(migrated, lang)['box'])

    def test_panel_home_loads_it_and_acl_allows_only_this_file(self):
        home = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/home.js').read_text()
        self.assertIn("L.require('view.status.include.05_mu300_wpa2')", home)
        acl = json.loads((TOP / 'openwrt/overlay/usr/share/rpcd/acl.d/mu300-wpa2-notice.json').read_text())
        g = acl['mu300-wpa2-notice']
        self.assertEqual(list(g['read']['file']), ['/etc/mu300/wpa3-disabled'])
        self.assertEqual(list(g['write']['file']), ['/etc/mu300/wpa3-disabled'])
        self.assertEqual(g['write']['ubus'], {'file': ['remove']})


if __name__ == '__main__':
    unittest.main()
