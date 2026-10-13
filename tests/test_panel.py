"""The web panel for Ubuntu, the panel extra (rootfs/panel): Cockpit and the MU300 page. mu300-update's checks of the
extra's tarball, mu300-extra install (online from the release, offline --from), remove and link against a fake
partition and a fake system (MU300_SYSROOT) with apt-get, systemctl and systemd-run stubbed; mu300-panel itself (its
configuration: the LAN bridge only; info's JSON; the hotspot, its password on stdin); the firewall that keeps port
9090 off the uplink; the page's manifest and translations; the page's JavaScript under Node against stub command
outputs (argument arrays, secrets on stdin, text never as HTML); and `sms json`."""
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest
from test_extra import ExtrasBase, sums
from test_sms_pool import AT_STUB, AWK, PDU_GSM, PDU_UCS2, Pool

PANEL = TOP / 'rootfs' / 'panel'
CMD = PANEL / 'bin' / 'mu300-panel'
PAGE = PANEL / 'cockpit' / 'mu300'
FIREWALL = TOP / 'rootfs' / 'overlay' / 'etc' / 'mu300' / 'firewall.nft'


def panel_tarball(path, release='v2026.10.20', debs=(), extra=(), name='panel'):
    """the panel extra as tools/make-extra.sh builds it (the page's own files, bin/mu300-panel), DEBS: (release, file)"""
    files = [('./name', name + '\n', 0o644), ('./release', release + '\n', 0o644),
             ('./components', 'mu300-panel\n', 0o644), ('./bin/mu300-panel', CMD.read_text(), 0o755)]
    files += [(f'./cockpit/mu300/{p.name}', p.read_bytes(), 0o644) for p in sorted(PAGE.iterdir())]
    files += [(f'./debs/{u}/{f}', b'!<arch>\n', 0o644) for u, f in debs]
    files += list(extra)
    with tarfile.open(path, 'w:gz') as t:
        for fn, data, mode in files:
            data = data.encode() if isinstance(data, str) else data
            ti = tarfile.TarInfo(fn)
            ti.size, ti.mode = len(data), mode
            t.addfile(ti, io.BytesIO(data))
    return path


# apt-get: records its arguments; an install "installs" Cockpit (cockpit-ws appears in the fake system)
APT = ('echo "apt-get $*" >> "$STUBLOG/apt"; '
       'case " $* " in *" install "*) mkdir -p "$MU300_SYSROOT/usr/lib/cockpit" && : > "$MU300_SYSROOT/usr/lib/cockpit/cockpit-ws" '
       '&& chmod 755 "$MU300_SYSROOT/usr/lib/cockpit/cockpit-ws" ;; *" purge "*) rm -f "$MU300_SYSROOT/usr/lib/cockpit/cockpit-ws" ;; esac; '
       '[ -z "$APT_FAIL" ]')


class PanelExtra(ExtrasBase):
    def setUp(self):
        super().setUp()
        (self.root / 'etc').mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/os-release').write_text('ID=ubuntu\nVERSION_ID="24.04"\n')
        self.stub('apt-get', APT)
        self.stub('systemctl', 'echo "systemctl $*" >> "$STUBLOG/systemctl"')
        self.stub('systemd-run', 'echo "systemd-run $*" >> "$STUBLOG/systemd-run"')
        self.stub('dpkg-query', 'exit 1')

    def log(self, name):
        f = self.tmp / name
        out = f.read_text() if f.exists() else ''
        f.unlink(missing_ok=True)
        return out

    def panel(self):
        return self.disk / 'extra' / 'panel'

    def reset(self, shell):
        self.up(shell, f'rm -rf "{self.disk}/extra"')
        for p in ('usr', 'etc/cockpit', 'etc/systemd'):
            shutil.rmtree(self.root / p, ignore_errors=True)
        for f in ('apt', 'systemctl', 'systemd-run'):
            self.log(f)

    # ---- the tarball ---------------------------------------------------------------------------------------
    def test_unpack_takes_the_panel_and_refuses_anything_else(self):
        good = panel_tarball(self.tmp / 'p.tar.gz', debs=[('24.04', 'cockpit-ws_314-1_arm64.deb')])
        for shell in self.each_shell():
            self.reset(shell)
            r = self.up(shell, f'extra_unpack panel "{good}" && extra_release panel && extra_installed')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.split(), ['v2026.10.20', 'panel'])
            self.assertTrue(os.access(self.panel() / 'bin/mu300-panel', os.X_OK))
            self.assertTrue((self.panel() / 'cockpit/mu300/manifest.json').is_file())
            for bad in (panel_tarball(self.tmp / 'b1.tar.gz', extra=[('./etc/sudoers', 'x', 0o644)]),
                        panel_tarball(self.tmp / 'b2.tar.gz', extra=[('./bin/other', '#!/bin/sh\n', 0o755)]),
                        panel_tarball(self.tmp / 'b3.tar.gz', extra=[('./debs/24.04/x.sh', 'x', 0o644)]),
                        panel_tarball(self.tmp / 'b4.tar.gz', extra=[('./cockpit/other/index.html', 'x', 0o644)]),
                        panel_tarball(self.tmp / 'b5.tar.gz', extra=[('./debs/x/y.deb', 'x', 0o644)]),
                        panel_tarball(self.tmp / 'b6.tar.gz', name='lang')):
                r = self.up(shell, f'extra_unpack panel "{bad}"; echo "rc=$?"')
                self.assertIn('rc=1', r.stdout, bad.name)
                self.assertTrue((self.panel() / 'cockpit/mu300/manifest.json').is_file(), bad.name)

    # ---- install, remove, link --------------------------------------------------------------------------------
    def installed_files(self):
        page = self.root / 'usr/share/cockpit/mu300'
        conf = (self.root / 'etc/cockpit/cockpit.conf')
        drop = (self.root / 'etc/systemd/system/cockpit.socket.d/mu300-listen.conf')
        return page, conf, drop

    def test_offline_install_from_the_extra_packages(self):
        (self.tmp / 'off').mkdir()
        off = panel_tarball(self.tmp / 'off' / 'mu300-extra-panel-offline.tar.gz',
                            debs=[('24.04', 'cockpit-ws_314-1_arm64.deb'), ('24.04', 'python3_3.12_arm64.deb'),
                                  ('26.04', 'cockpit-ws_360-1_arm64.deb')])
        for shell in self.each_shell():
            self.reset(shell)
            self.offline()
            r = self.ex(shell, 'install', 'panel', '--from', off)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            apt = self.log('apt')
            # only this release's packages, by path, and nothing from the archive
            self.assertIn('install -y -q --no-install-recommends', apt)
            self.assertIn(f'{self.panel()}/debs/24.04/cockpit-ws_314-1_arm64.deb', apt)
            self.assertIn('python3_3.12_arm64.deb', apt)
            self.assertNotIn('26.04', apt)
            self.assertNotIn('update', apt)
            page, conf, drop = self.installed_files()
            self.assertEqual(os.path.realpath(page), os.path.realpath(self.panel() / 'cockpit/mu300'))
            self.assertIn('LoginTo = false', conf.read_text())
            self.assertIn('IdleTimeout = 15', conf.read_text())
            self.assertEqual([l for l in drop.read_text().splitlines() if not l.startswith('#')],
                             ['[Socket]', 'ListenStream=', 'ListenStream=9090', 'BindToDevice=br-lan'])
            self.assertTrue((self.links / 'mu300-panel').is_symlink())
            self.assertIn('restart cockpit.socket', self.log('systemctl'))

    def test_offline_copies_are_checked(self):
        d = self.tmp / 'dir'
        d.mkdir()
        panel_tarball(d / 'mu300-extra-panel-offline.tar.gz')
        for shell in self.each_shell():
            self.reset(shell)
            r = self.ex(shell, 'install', 'panel', '--from', d)          # a directory needs its SHA256SUMS
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('SHA256SUMS', r.stderr)
            (d / 'SHA256SUMS').write_text('0' * 64 + '  mu300-extra-panel-offline.tar.gz\n')
            r = self.ex(shell, 'install', 'panel', '--from', d)
            self.assertIn('does not match', r.stderr)
            sums(d, 'mu300-extra-panel-offline.tar.gz')
            r = self.ex(shell, 'install', 'panel', '--from', d)
            self.assertEqual(r.returncode, 0, r.stderr)
            (d / 'SHA256SUMS').unlink()
            self.assertFalse((self.disk / 'extra/.panel.new').exists())

    def test_online_install_takes_cockpit_from_the_archive(self):
        self.serve('v2026.10.20', assets={'mu300-extra-panel.tar.gz': panel_tarball(self.tmp / 'on.tar.gz')})
        self.versions('v2026.10.20')
        for shell in self.each_shell():
            self.reset(shell)
            r = self.ex(shell, 'install', 'panel')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            apt = self.log('apt').splitlines()
            self.assertEqual(apt[0], 'apt-get update -q')
            self.assertTrue(apt[1].endswith('install -y -q --no-install-recommends -o Dpkg::Options::=--force-confold '
                                            'cockpit-ws cockpit-system cockpit-bridge'), apt)
            # the configuration was in place before the packages (their postinst starts the socket)
            self.assertTrue(self.installed_files()[2].is_file())

    def test_offline_without_packages_says_how(self):
        self.serve('v2026.10.20', assets={'mu300-extra-panel.tar.gz': panel_tarball(self.tmp / 'on.tar.gz')})
        self.versions('v2026.10.20')
        for shell in self.each_shell():
            self.reset(shell)
            r = self.ex(shell, 'install', 'panel', APT_FAIL=1)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('mu300-extra-panel-offline.tar.gz', r.stderr)

    def test_openwrt_refuses(self):
        (self.root / 'etc/openwrt_release').write_text('x\n')
        off = panel_tarball(self.tmp / 'p.tar.gz')
        for shell in self.each_shell():
            r = self.ex(shell, 'install', 'panel', '--from', off)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('for Ubuntu', r.stderr)
            self.assertFalse(self.panel().exists())
            self.assertIn('for Ubuntu only', self.ex(shell, 'list').stdout)

    def test_remove_takes_it_all_out(self):
        off = panel_tarball(self.tmp / 'p.tar.gz', debs=[('24.04', 'cockpit-ws_314-1_arm64.deb')])
        for shell in self.each_shell():
            self.reset(shell)
            self.ex(shell, 'install', 'panel', '--from', off)
            self.log('apt')
            r = self.ex(shell, 'remove', 'panel')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('purge -y -q cockpit-ws cockpit-system cockpit-bridge', self.log('apt'))
            self.assertIn('disable --now cockpit.socket', self.log('systemctl'))
            for p in self.installed_files():
                self.assertFalse(p.exists() or p.is_symlink(), p)
            self.assertFalse(self.panel().exists())
            self.assertFalse((self.links / 'mu300-panel').is_symlink())

    def test_link_after_an_update_puts_cockpit_back_in_the_background(self):
        off = panel_tarball(self.tmp / 'p.tar.gz', debs=[('24.04', 'cockpit-ws_314-1_arm64.deb')])
        for shell in self.each_shell():
            self.reset(shell)
            self.ex(shell, 'install', 'panel', '--from', off)
            self.log('systemctl')
            # nothing to do while Cockpit and its configuration are there: no reload at boot
            self.assertEqual(self.ex(shell, 'link').returncode, 0)
            self.assertEqual(self.log('systemctl'), '')
            self.assertEqual(self.log('systemd-run'), '')
            # an update: a new system without Cockpit, its configuration or the page
            for p in ('usr', 'etc/cockpit', 'etc/systemd'):
                shutil.rmtree(self.root / p, ignore_errors=True)
            self.log('apt')
            self.assertEqual(self.ex(shell, 'link').returncode, 0)
            run = self.log('systemd-run')
            self.assertIn('--no-block', run)
            self.assertIn('After=network-online.target', run)
            self.assertIn(f'{self.panel()}/bin/mu300-panel restore', run)
            self.assertEqual(self.log('apt'), '')                  # the boot never runs apt itself
            page, conf, drop = self.installed_files()
            self.assertTrue(page.is_symlink() and conf.is_file() and drop.is_file())

    def test_restore_installs_and_stops_trying(self):
        off = panel_tarball(self.tmp / 'p.tar.gz')
        for shell in self.each_shell():
            self.reset(shell)
            self.up(shell, f'extra_unpack panel "{off}"')
            env = dict(MU300_SYSROOT=self.root, MU300_PANEL_DIR=self.panel(), MU300_RUN=self.tmp / 'run',
                       MU300_PANEL_RETRY=0, MU300_PANEL_TRIES=3)
            r = self.script(shell, CMD, 'restore', APT_FAIL=1, **env)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.log('apt').count('update'), 3)
            r = self.script(shell, CMD, 'restore', **env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((self.root / 'usr/lib/cockpit/cockpit-ws').exists())


class Command(ShellTest):
    """mu300-panel info and hotspot-set"""

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        (self.root / 'etc/mu300').mkdir(parents=True)
        (self.root / 'etc/os-release').write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
        (self.root / 'etc/mu300/hotspot.conf').write_text('SSID=MU300-1\nPSK=oldpassword\nBAND=5\nCHANNEL=auto\n')
        z = self.root / 'sys/class/thermal/thermal_zone0'
        z.mkdir(parents=True)
        (z / 'type').write_text('soc-thmzone\n')
        (z / 'temp').write_text('48250\n')
        b = self.root / 'sys/class/power_supply/battery'
        b.mkdir(parents=True)
        for k, v in (('type', 'Battery'), ('capacity', '81'), ('status', 'Discharging'), ('voltage_now', '3900000')):
            (b / k).write_text(v + '\n')
        self.stub('id', 'echo 0')
        self.stub('systemctl', 'echo "systemctl $*" >> "$STUBLOG/systemctl"; [ "$1" = is-active ] && echo active; exit 0')
        self.stub('mu300-device', 'cat "$STUBLOG/device"')
        (self.tmp / 'device').write_text('u30air\n')
        self.stub('mu300-lan-ip', 'echo 192.168.77.1')
        self.stub('mu300-cpu', 'echo \'{"ok":1,"profile":"saving"}\'')
        self.stub('ip', 'echo "5: sipa_eth0    inet 10.0.0.2/30 scope global sipa_eth0"')
        # an operator name with a backslash and markup, as a hostile network could name itself
        (self.tmp / 'cops').write_text('+COPS: 0,0,"Evil\\x <b>",7\r\nOK\r\n')
        self.stub('mu300-at', 'echo "$*" >> "$STUBLOG/at"; case "$*" in '
                  '*COPS*) cat "$STUBLOG/cops" ;; '
                  '*CEREG*) printf \'+CEREG: 0,5\\r\\nOK\\r\\n\' ;; *CESQ*) printf \'+CESQ: 99,99,255,255,30,60\\r\\nOK\\r\\n\' ;; esac')

    def env(self, **extra):
        e = super().env(**extra)
        e.update(MU300_SYSROOT=str(self.root), MU300_RUN=str(self.tmp / 'run'))
        return e

    def test_info_is_json(self):
        for shell in self.each_shell():
            shutil.rmtree(self.tmp / 'run', ignore_errors=True)
            r = self.script(shell, CMD, 'info')
            self.assertEqual(r.returncode, 0, r.stderr)
            i = json.loads(r.stdout)
            self.assertEqual(i['device'], 'u30air')
            self.assertEqual(i['mobile_data'], {'service': 'active', 'address': '10.0.0.2/30'})
            self.assertEqual(i['signal']['rat'], 'LTE')
            self.assertEqual(i['signal']['registration'], 'roaming')
            self.assertEqual((i['signal']['rsrp'], i['signal']['rsrq']), (-80, -4.5))
            self.assertEqual(i['signal']['operator'], 'Evil\\x <b>')
            self.assertEqual(i['battery']['capacity'], 81)
            self.assertEqual(i['temperatures'], [{'name': 'soc-thmzone', 'millicelsius': 48250}])
            self.assertEqual(i['cpu']['profile'], 'saving')
            self.assertEqual(i['hotspot'], {'ssid': 'MU300-1'})
            self.assertNotIn('oldpassword', r.stdout)
            # the radio readings are cached: a second call within 15 s sends no AT command
            (self.tmp / 'at').unlink()
            self.script(shell, CMD, 'info')
            self.assertFalse((self.tmp / 'at').exists())
            # the F50 has no battery, whatever its charger driver lists
            (self.tmp / 'device').write_text('f50\n')
            self.assertIsNone(json.loads(self.script(shell, CMD, 'info').stdout)['battery'])
            (self.tmp / 'device').write_text('u30air\n')

    def test_hotspot_set(self):
        conf = self.root / 'etc/mu300/hotspot.conf'
        for shell in self.each_shell():
            conf.write_text('SSID=MU300-1\nPSK=oldpassword\nBAND=5\nCHANNEL=auto\n')
            for ssid, psk in (('', 'longenough'), ('x' * 33, 'longenough'), ('ok', 'short'), ('ok', 'x' * 64),
                              ('a\tb', 'longenough'), (' lead', 'longenough'), ('ok', 'pässword1')):
                r = self.script(shell, CMD, 'hotspot-set', ssid, stdin=psk + '\n')
                self.assertNotEqual(r.returncode, 0, (ssid, psk))
                self.assertIn('PSK=oldpassword', conf.read_text())
            r = self.script(shell, CMD, 'hotspot-set', 'Café $(id) "x"', stdin='p@ss $(reboot)\n')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(conf.read_text().splitlines(),
                             ['BAND=5', 'CHANNEL=auto', 'SSID=Café $(id) "x"', 'PSK=p@ss $(reboot)'])
            self.assertEqual(conf.stat().st_mode & 0o777, 0o600)
            self.assertIn('restart mu300-hotspot', (self.tmp / 'systemctl').read_text())

    def test_not_on_openwrt(self):
        (self.root / 'etc/openwrt_release').write_text('x\n')
        for shell in self.each_shell():
            r = self.script(shell, CMD, 'install')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('for Ubuntu', r.stderr)


class Static(unittest.TestCase):
    def test_the_uplink_never_reaches_9090(self):
        # the host firewall: nothing new from the cellular side gets in, Cockpit's port included; nothing opens it
        fw = [l.strip() for l in FIREWALL.read_text().splitlines()]
        chain = fw[fw.index('chain input {'):]
        chain = chain[:chain.index('}')]
        self.assertEqual(chain[-1], 'iifname "sipa_eth*" counter drop')
        for line in chain:
            if 'accept' in line and 'iifname' in line:
                self.assertRegex(line, r'ct state established,related accept|icmp|icmpv6')
        self.assertNotIn('9090', FIREWALL.read_text())
        # the Wi-Fi uplink is closed the same way
        self.assertIn('iifname "%s" counter drop', (BIN / 'wifi-client').read_text())
        # and Cockpit listens on the LAN bridge only
        cmd = CMD.read_text()
        self.assertIn("printf '[Socket]\\nListenStream=\\nListenStream=9090\\nBindToDevice=br-lan\\n'", cmd)

    def test_manifest_and_page(self):
        m = json.loads((PAGE / 'manifest.json').read_text())
        self.assertEqual(m['menu']['index']['label'], 'MU300')
        html = (PAGE / 'index.html').read_text()
        for s in ('../base1/cockpit.js', 'po.js', 'mu300.js', 'mu300.css'):
            self.assertIn(s, html)
        self.assertNotRegex(html, r'<script>[^<]')            # Cockpit's policy: no inline script
        js = (PAGE / 'mu300.js').read_text()
        for bad in ('innerHTML', 'eval(', "'sh'", '"sh"', "'-c'", 'bash'):
            self.assertNotIn(bad, js)
        self.assertIn("superuser: 'try'", js)

    def test_translations(self):
        r = subprocess.run(['python3', str(TOP / 'tools/cockpit-i18n.py'), 'check'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        d = Path(tempfile.mkdtemp(prefix='mu300-cockpit-po-'))
        try:
            subprocess.run(['python3', str(TOP / 'tools/cockpit-i18n.py'), 'build', str(d)], check=True)
            for lang in ('tr', 'zh_CN'):
                text = (d / f'po.{lang}.js').read_text(encoding='utf-8')
                self.assertTrue(text.startswith('cockpit.locale({'))
                self.assertIn('"Overview": [', text)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_release_and_installers(self):
        rel = (TOP / 'tools/make-release.sh').read_text()
        self.assertIn('make-extra.sh" panel "$D/mu300-extra-panel.tar.gz"', rel)
        self.assertIn('make-extra.sh" panel-offline "$D/mu300-extra-panel-offline.tar.gz"', rel)
        self.assertIn('EXTRAS="vpn lang vpn-mihomo panel vpn-xray vpn-singbox vpn-dae"', (BIN / 'mu300-update').read_text())
        sh = (TOP / 'install.sh').read_text()
        self.assertIn('adb push "$EXTRA_PANEL" $T/mu300-extra-panel.tar.gz', sh)
        self.assertIn("Install the web panel for Ubuntu (about 24 MB more to download, 50 MB on the device)? (yes/no)')\" no", sh)
        ps = (TOP / 'install.ps1').read_text()
        self.assertIn('& adb push $EXTRA_PANEL "$T/mu300-extra-panel.tar.gz"', ps)


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed for the page tests')
class Page(unittest.TestCase):
    """mu300.js under Node: a small DOM, and a cockpit object whose spawn records the argument arrays and answers
    from a table of stub outputs."""

    HARNESS = r'''
const fs = require('fs');
const calls = [];
class Node_ { constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.on = {}; this._text = '';
    this.classList = { toggle: () => {}, add: () => {}, remove: () => {} }; this.disabled = false; this.value = ''; }
  appendChild(c) { this.children.push(c); c.parent = this; return c; }
  insertBefore(c) { this.children.unshift(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; }
  get firstChild() { return this.children[0] || null; }
  setAttribute(k, v) { this.attrs[k] = v; if (k === 'id') ids[v] = this; } getAttribute(k) { return this.attrs[k]; }
  addEventListener(t, f) { (this.on[t] = this.on[t] || []).push(f); }
  click() { (this.on.click || []).forEach((f) => f()); }
  set textContent(t) { this.children = []; this._text = String(t); } get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  set id(v) { this.setAttribute('id', v); } set className(v) { this.attrs.class = v; }
}
const ids = {};
const text = (t) => { const n = new Node_('#text'); n._text = t; return n; };
const document = { readyState: 'complete', createElement: (t) => new Node_(t), createTextNode: text,
  getElementById: (i) => ids[i] || null, querySelectorAll: () => [], addEventListener: () => {} };
ids.app = new Node_('div');
const find = (n, pred) => { if (pred(n)) return n; for (const c of n.children || []) { const r = find(c, pred); if (r) return r; } return null; };
const all = (n, pred, out = []) => { if (pred(n)) out.push(n); (n.children || []).forEach((c) => all(c, pred, out)); return out; };
const OUT = __OUT__;
function spawn(argv, opts) {
  const call = { argv: argv, opts: opts, input: undefined };
  calls.push(call);
  const key = argv.join(' ');
  const hit = Object.keys(OUT).find((k) => key.startsWith(k));
  const p = new Promise((res, rej) => setTimeout(() => hit === undefined ? rej({ problem: 'not-found' }) : res(OUT[hit]), 0));
  p.input = (d) => { call.input = d; return p; };
  p.stream = () => p;
  return p;
}
const cockpit = { gettext: (s) => s, spawn: spawn, format: (f, ...a) => f.replace(/\$(\d)/g, (m, i) => a[i]),
  file: () => ({ read: () => Promise.resolve('Inter-|\n eth0: 100 1 0 0 0 0 0 0 200 2 0 0 0 0 0 0\n') }),
  location: { path: [], go: (p) => { cockpit.location.path = p; } }, translate: () => {}, addEventListener: () => {} };
const window = { confirm: () => true };
global.TextEncoder = TextEncoder;
const setInterval_ = () => 0;
new Function('cockpit', 'document', 'window', 'setInterval', 'clearInterval', fs.readFileSync(process.argv[2], 'utf8'))(
  cockpit, document, window, setInterval_, () => {});
window.MU300 = window.MU300;
const flush = () => new Promise((r) => setTimeout(r, 5));
(async function() { __BODY__ })().then((r) => process.stdout.write(JSON.stringify({ result: r, calls: calls })),
  (e) => { console.error(e); process.exit(1); });
'''

    OUT = {
        'mu300-panel info': json.dumps({'device': 'u30air', 'version': 'v1', 'mobile_data': {'service': 'active'},
                                        'signal': {'operator': '<img src=x onerror=alert(1)>', 'rat': 'LTE'},
                                        'battery': None, 'temperatures': [], 'cpu': None,
                                        'hotspot': {'ssid': 'MU300'}}),
        'mu300-traffic json': json.dumps({'today': {'rx': 1, 'tx': 1}}),
        'sms json': json.dumps({'messages': [{'index': [1, 2], 'sender': '<b>x</b>', 'time': 't', 'parts': 2,
                                              'text': 'hello'}, {'index': ['3;CFUN=0'], 'sender': 'y', 'time': 't',
                                                                 'parts': 1, 'text': 'z'}]}),
        'sms send': 'sent', 'sms delete': 'deleted',
        'mu300-vpn status --json': json.dumps({'enabled': False, 'running': False}),
        'mu300-vpn profile list --json': json.dumps([{'id': 'ok-1', 'name': 'A', 'type': 'xray'},
                                                     {'id': '../x', 'name': 'B', 'type': 'xray'}]),
        'mu300-vpn profile use': 'used', 'mu300-panel hotspot-set': 'hotspot', 'wifi-client': 'status',
        # the VPN module is installed (mu300-extra's test, the page runs it as test -x); a stub table without this
        # key is a device without the module
        'test -x /mnt/mu300-disk/extra/vpn/bin/mu300-vpn': '',
    }

    def run_page(self, body, out=None):
        h = self.HARNESS.replace('__OUT__', json.dumps(self.OUT if out is None else out)).replace('__BODY__', body)
        r = subprocess.run(['node', '-', str(PAGE / 'mu300.js')], input=h, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_overview_shows_values_as_text(self):
        out = self.run_page("await flush(); await flush(); return document.getElementById('app').textContent;")
        self.assertIn('<img src=x onerror=alert(1)>', out['result'])
        argvs = [c['argv'] for c in out['calls']]
        self.assertIn(['mu300-panel', 'info'], argvs)
        self.assertTrue(all(c['opts'] == {'superuser': 'try', 'err': 'message'} for c in out['calls']))

    def test_sms_send_and_delete(self):
        out = self.run_page(r'''
window.MU300.show('sms'); await flush(); await flush();
ids['mu-sms-number'].value = '+905551112233'; ids['mu-sms-text'].value = 'hi $(reboot); `id`';
find(ids['mu-sms-send'], (n) => n.tag === 'button').click(); await flush();
ids['mu-sms-number'].value = '123; reboot'; find(ids['mu-sms-send'], (n) => n.tag === 'button').click(); await flush();
all(ids['mu-sms-list'], (n) => n.tag === 'button' && n.textContent === 'Delete')[0].click(); await flush(); await flush();
return document.getElementById('app').textContent;''')
        argvs = [c['argv'] for c in out['calls']]
        self.assertIn(['sms', 'send', '+905551112233', 'hi $(reboot); `id`'], argvs)
        self.assertFalse(any(a[:2] == ['sms', 'send'] and a[2] == '123; reboot' for a in argvs))
        # the newest first: the message with the bad index is the first Delete; its index is never sent
        deletes = [a for a in argvs if a[:2] == ['sms', 'delete']]
        self.assertTrue(all(len(a) == 3 and a[2].isdigit() for a in deletes), deletes)
        self.assertIn('<b>x</b>', out['result'])

    def test_secrets_go_on_stdin(self):
        out = self.run_page(r'''
window.MU300.show('wifi'); await flush();
ids['mu-hs-ssid'].value = 'Net'; ids['mu-hs-psk'].value = 'secret123';
find(ids['mu-hotspot'], (n) => n.tag === 'button').click(); await flush();
ids['mu-wc-ssid'].value = 'Cafe'; ids['mu-wc-psk'].value = 'cafepass';
all(ids['mu-wifi-client'], (n) => n.tag === 'button')[0].click(); await flush();
return null;''')
        calls = {' '.join(c['argv']): c.get('input') for c in out['calls']}
        self.assertEqual(calls['mu300-panel hotspot-set Net'], 'secret123\n')
        self.assertEqual(calls['wifi-client connect Cafe -'], 'cafepass\n')
        self.assertFalse(any('secret123' in k or 'cafepass' in k for k in calls))

    def test_vpn_profile_ids_are_checked(self):
        out = self.run_page(r'''await flush();
window.MU300.show('vpn'); await flush(); await flush(); await flush();
all(ids['mu-vpn-profiles'], (n) => n.tag === 'button').forEach((b) => b.click()); await flush();
return null;''')
        uses = [c['argv'] for c in out['calls'] if c['argv'][:3] == ['mu300-vpn', 'profile', 'use']]
        self.assertEqual(uses, [['mu300-vpn', 'profile', 'use', 'ok-1']])

    def test_the_vpn_tab_only_with_the_module(self):
        body = r'''await flush(); await flush();
const tabs = all(ids.app, (n) => n.tag === 'button' && n.attrs['data-tab']).map((n) => n.attrs['data-tab']);
window.MU300.show('vpn'); await flush(); await flush();
return { tabs: tabs, shown: cockpit.location.path[0], main: document.getElementById('mu-main').textContent };'''
        check = ['test', '-x', '/mnt/mu300-disk/extra/vpn/bin/mu300-vpn']
        # with the module (its mu300-vpn in the vpn extra, mu300-extra's test): the tab, and the VPN page opens
        out = self.run_page(body)
        self.assertIn('vpn', out['result']['tabs'])
        self.assertEqual(out['result']['shown'], 'vpn')
        self.assertIn(check, [c['argv'] for c in out['calls']])
        # without it: no tab, no word of a VPN, and the VPN page is not to be had - the overview opens instead
        without = {k: v for k, v in self.OUT.items() if k[:7] != 'test -x'}
        out = self.run_page(body, without)
        self.assertNotIn('vpn', out['result']['tabs'])
        self.assertEqual(sorted(out['result']['tabs']), ['overview', 'power', 'sms', 'updates', 'wifi'])
        self.assertEqual(out['result']['shown'], 'overview')
        self.assertNotIn('VPN', out['result']['main'])
        self.assertNotIn('VPN', ''.join(t for t in self.run_page(
            "await flush(); return document.getElementById('app').textContent;", without)['result']))
        self.assertFalse([c['argv'] for c in out['calls'] if c['argv'][0] == 'mu300-vpn'])

    def test_helpers(self):
        out = self.run_page(r'''const M = window.MU300;
return [M.netDev('Inter-|\n  lo: 1 0 0 0 0 0 0 0 2 0\n sipa_eth0: 300 5 0 0 0 0 0 0 400 6 0 0 0 0 0 0\n'),
        M.fmtBytes(1536), M.fmtRate(125000), M.errText({ problem: 'not-found' })];''')
        self.assertEqual(out['result'], [{'lo': {'rx': 1, 'tx': 2}, 'sipa_eth0': {'rx': 300, 'tx': 400}}, '1.5 KiB',
                                         '1.0 Mbit/s', 'The command is not on this system.'])


class SmsJson(ShellTest):
    """`sms json`: the SIM's messages, long ones joined, as one JSON object (the Ubuntu panel's SMS page)"""

    def setUp(self):
        super().setUp()
        self.stub('mu300-at', AT_STUB)
        (self.tmp / 'at').mkdir()
        (self.tmp / 'at' / 'CPMS').write_text('+CPMS: 2,50,2,50,2,50\nOK\n')

    def run_json(self, shell, cmgl):
        (self.tmp / 'at' / 'CMGL4').write_text(cmgl)
        r = self.script(shell, BIN / 'sms', 'json', MU300_SMS_AWK=AWK, MU300_SMS_STATE=self.tmp / 'state')
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_messages(self):
        hostile = Pool.part(0x45, 1, 1, 'a "quote" \\ back\tslash ü')
        for shell in self.each_shell():
            j = self.run_json(shell, f'+CMGL: 3,0,,40\n{PDU_GSM}\n+CMGL: 7,1,,56\n{PDU_UCS2}\n+CMGL: 9,0,,30\n{hostile}\nOK\n')
            self.assertEqual(j['store'], 'SM')
            m = {x['index'][0]: x for x in j['messages']}
            self.assertEqual(m[3]['sender'], '+31641600986')
            self.assertEqual(m[7]['sender'], 'ADANA BLD')
            self.assertEqual(m[9]['text'], 'a "quote" \\ back slash ü')
            self.assertEqual(self.run_json(shell, 'OK\n'), {'store': 'SM', 'messages': []})


if __name__ == '__main__':
    unittest.main()
