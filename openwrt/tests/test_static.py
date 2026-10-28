"""Checks over every script without running it: syntax under the shell that runs it, executable bits, and rules
that past bugs taught (see each test)."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, shells

OPENWRT = TOP / 'openwrt' / 'overlay'
LUCI_OVERLAY = TOP / 'openwrt' / 'luci-overlay'


def shebang(p):
    try:
        with open(p, 'rb') as f:
            first = f.readline()
    except OSError:
        return ''
    return first.decode(errors='replace').strip() if first.startswith(b'#!') else ''


def shell_scripts():
    """(path, 'sh'|'bash') of every shell script the image ships or the build runs."""
    cands = list(BIN.iterdir()) + [TOP / 'openwrt' / 'build-rootfs.sh']
    cands += [p for p in OPENWRT.rglob('*') if p.is_file()]
    cands += [p for p in LUCI_OVERLAY.rglob('*') if p.is_file()]
    cands += [p for p in (TOP / 'openwrt' / 'src').rglob('*.sh')]
    out = []
    for p in sorted(set(cands)):
        if not p.is_file():
            continue
        sb = shebang(p)
        if 'bash' in sb:
            out.append((p, 'bash'))
        elif sb.endswith('sh') or 'rc.common' in sb or (not sb and p.suffix == '.sh'):
            out.append((p, 'sh'))
    return out


class Syntax(unittest.TestCase):
    def test_every_script_parses(self):
        sh_shells = [s for s in shells() if s[0] != 'bash'] or shells()
        scripts = shell_scripts()
        self.assertGreater(len(scripts), 40)
        for p, kind in scripts:
            if kind == 'bash' and not shutil.which('bash'):
                continue
            for s in ([['bash']] if kind == 'bash' else sh_shells):
                with self.subTest(script=str(p.relative_to(TOP)), shell=' '.join(s)):
                    r = subprocess.run(s + ['-n', str(p)], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, r.stderr)

    def test_device_programs_are_executable(self):
        for p in BIN.iterdir():
            if p.is_file() and shebang(p):
                with self.subTest(p=p.name):
                    self.assertTrue(p.stat().st_mode & 0o111, 'not executable')


class Rules(unittest.TestCase):
    def test_power_page_is_wired(self):
        menu = json.loads((TOP / 'openwrt/luci-app-mu300/root/usr/share/luci/menu.d/luci-app-mu300.json').read_text())
        self.assertEqual(menu['admin/system/power']['action']['path'], 'mu300/power')
        common = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/common.js').read_text()
        self.assertIn("method: 'power_get'", common)
        self.assertIn("method: 'power_set', params: [ 'op', 'key', 'value' ]", common)
        view = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/power.js').read_text()
        for s in ('WIFI_IDLE', 'RADIO_IDLE', 'LEDS_IDLE', 'CPU', 'SAVER_BELOW', 'CHARGE_TO', 'callPowerSet'):
            self.assertIn(s, view)
        acl = json.loads((TOP / 'openwrt/luci-app-mu300/root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())
        self.assertIn('power_get', acl['luci-app-mu300']['read']['ubus']['mu300dash'])
        self.assertIn('power_set', acl['luci-app-mu300']['write']['ubus']['mu300dash'])

    def test_power_profiles_are_wired_in(self):
        # the daemon runs at boot, the keys wake it (mu300-sysd), the command is on PATH, OpenWrt's own power-key
        # handler (a tap = poweroff) is neutralised, and power.conf is kept
        self.assertIn('mu300-power', (TOP / 'openwrt/opt/mu300/lib/path-commands').read_text().split())
        self.assertIn("spawn([ 'mu300-power', 'wake', key ], null, null, true);", (BIN / 'mu300-sysd').read_text())
        init = (TOP / 'openwrt/overlay/etc/init.d/mu300-power').read_text()
        self.assertIn('procd_set_param command /opt/mu300/bin/mu300-power daemon', init)
        # stopped while idle, the TERM trap runs the whole wake (up to ~120 s for the radio lock)
        self.assertIn('procd_set_param term_timeout 150', init)
        self.assertRegex(init, r'START=9[6-9]')
        build = (TOP / 'openwrt/build-rootfs.sh').read_text()
        self.assertIn('for b in power wps rfkill', build)
        self.assertIn('mkdir -p $R/etc/rc.button', build)
        self.assertIn('rc.button/$b', build)
        self.assertLess(build.index('cp -a /in/overlay/. $R/'), build.index('for b in power wps rfkill'))
        self.assertIn('mu300-power', build.split('for s in mu300-accounts')[1].split('; do')[0])
        # etc/mu300 is kept whole, and power.conf lives in it
        update = (TOP / 'openwrt/opt/mu300/bin/mu300-update').read_text()
        self.assertRegex(update, r'openwrt\) echo "etc/config etc/mu300 ')
        self.assertIn('/etc/mu300/power.conf', (BIN / 'mu300-power').read_text())

    def test_single_quoted_scripts_have_no_apostrophes(self):
        # A script handed to `sh -c '...'` (docker run) ends at its first apostrophe: "the U30 Air's charger" in a
        # comment there broke OpenWrt's build at release time, while every parser still saw balanced quotes.
        for name in ('openwrt/build-rootfs.sh', 'tools/make-release.sh'):
            lines = (TOP / name).read_text().splitlines()
            for n, line in enumerate(lines):
                if not line.rstrip().endswith("-c '"):
                    continue
                for m in range(n + 1, len(lines)):
                    if "'" in lines[m]:
                        self.assertTrue(lines[m].rstrip().endswith("'") and lines[m].count("'") == 1,
                                        f'{name}:{m + 1}: an apostrophe inside the script of line {n + 1}')
                        break

    def test_openwrt_luci_build_wiring(self):
        # the panel, its Chinese catalog compiled from po/ and Aurora pinned by hash
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        for s in ('05f9015e0a4e2859f6a153f69e472f2984481490d4ce6db19b8a41bba7264f1e', 'po2lmo.py', 'luci-overlay',
                  'packages.txt', 'luci-i18n-$c-zh-cn', 'mu300.zh-cn.lmo', 'base.zh-cn.lmo'):
            self.assertIn(s, text)
        self.assertRegex(text, r'(?m)^SYSTEM=openwrt-luci$')
        self.assertIn('mu300-$SYSTEM-$VER-rootfs.tar.gz', text)
        # the Aurora hash is pinned in the script, only the file may be overridden
        self.assertNotIn('MU300_LUCI_THEME_SHA256', text)
        self.assertRegex(text, r'(?m)^\s*THEME_SHA=05f9015e0a4e2859f6a153f69e472f2984481490d4ce6db19b8a41bba7264f1e$')
        mk = (TOP / 'openwrt' / 'luci-app-mu300' / 'Makefile').read_text()
        self.assertRegex(mk, r'set -e; \$\(foreach', 'a failing catalog must fail the compile')
        f = LUCI_OVERLAY / 'etc' / 'uci-defaults' / '91-mu300-luci'
        self.assertTrue(f.is_file() and f.stat().st_mode & 0o111, f'{f} missing or not executable')
        self.assertIn('/luci-static/aurora', f.read_text())
        self.assertIn(f, [p for p, _ in shell_scripts()])   # so test_every_script_parses parses it

    def test_the_image_carries_no_kernel_modules(self):
        # the modules come with the kernel bundle (docs/contract.md), which mu300-update installs beside the systems
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.assertIn('rm -rf $R/lib/modules/* $R/boot', text)
        self.assertNotIn('KREL', text)

    def test_the_commands_on_path_are_the_same_everywhere(self):
        # the image and the boot-time links (mu300-extra link, for systems installed before a command had one) link
        # the same commands, from one file
        lst = (TOP / 'openwrt/opt/mu300/lib/path-commands').read_text().split()
        self.assertIn('/in/opt-mu300/lib/path-commands', (TOP / 'openwrt/build-rootfs.sh').read_text())
        self.assertIn('/lib/path-commands', (BIN / 'mu300-extra').read_text())
        self.assertIn('mu300-extra link', (OPENWRT / 'etc/init.d/mu300-post').read_text())
        for c in ('mu300-device', 'mu300-sim', 'mu300-led', 'mobile-data', 'mu300-update', 'mu300-ussd', 'sms', 'mu300-extra'):
            self.assertIn(c, lst)
        self.assertEqual(len(lst), len(set(lst)))
        for c in lst:
            self.assertTrue((BIN / c).is_file(), c)

    def test_reply_deadlines_are_at_least_t_whole_seconds(self):
        # date +%s counts whole seconds: "now + T" ends between T - 1 and T seconds away, so a command written late in
        # a second had almost none of its budget. Every file-clock deadline is now + T + 1 or later.
        src = (BIN / 'mu300-ussd').read_text()
        self.assertRegex(src, r'\$\(date \+%s\) \+ T \+ 1')
        self.assertNotRegex(src, r'date \+%s\) \+ T \)')
        # mu300-at waits for the daemon's answer until now + budget, the budget T + 2 at the least
        src = (BIN / 'mu300-at').read_text()
        self.assertIn('budget=$((T + 2))', src)
        self.assertIn('end=$(( $(date +%s) + budget ))', src)
        self.assertNotRegex(src, r'date \+%s\) \+ T \)')

    def test_images_carry_no_vpn(self):
        # the VPN is the module of dikeckaan/mu300-linux-vpn: no mu300-vpn, no drivers, no service, no engines in the
        # image or in this repository, and the release audit refuses an image that has any of it
        for f in ('openwrt/opt/mu300/bin/mu300-vpn', 'openwrt/opt/mu300/lib/vpn', 'openwrt/overlay/etc/init.d/mu300-vpn'):
            self.assertFalse((TOP / f).exists(), f)
        self.assertNotIn('mu300-vpn', (TOP / 'openwrt/opt/mu300/lib/path-commands').read_text().split())
        src = (TOP / 'openwrt/build-rootfs.sh').read_text()
        for e in ('xray', 'sing-box', 'hev-socks5-tunnel', 'mihomo', 'mu300-vpn'):
            self.assertNotRegex(src, rf'opt/mu300/bin/{e}\b', e)
        self.assertNotIn('vpn.conf.example', src)
        self.assertNotIn('lib/vpn', src)
        rel = (TOP / 'tools/make-release.sh').read_text()
        self.assertIn('opt/mu300/bin/(xray|sing-box|hev-socks5-tunnel|mihomo|mu300-vpn)', rel)
        for pat in ('^opt/mu300/lib/vpn(/|$)', 'etc/init\\.d/mu300-vpn', 'etc/mu300/vpn\\.conf\\.example'):
            self.assertIn(pat, rel)

    def test_the_release_source_is_wired(self):
        # a published image must not carry a configured release source, and the image has the example
        rel = (TOP / 'tools/make-release.sh').read_text()
        self.assertIn("^etc/mu300/(hotspot|vpn|toolkit|mobile-data)\\.conf$", rel)
        self.assertRegex(rel, r"\^etc/mu300/update-source\$")
        self.assertNotIn('update-source.example', rel)
        text = (TOP / 'openwrt/overlay/etc/mu300/update-source.example').read_text()
        for s in ('MU300_DOWNLOAD_URL=http://mirror.lan/', 'MU300_API_URL=https://', '403', 'MU300_MIRROR_FALLBACK=0'):
            self.assertIn(s, text)
        # the panel's field, the op behind it and the status field that feeds it name the same thing
        js = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/update.js').read_text()
        self.assertIn("op: 'source'", js)
        self.assertIn("confirm: 'source'", js)
        self.assertIn('st.source || {}', js)
        common = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/common.js').read_text()
        self.assertIn("'flags', 'confirm', 'url'", common)
        dash = (TOP / 'openwrt/luci-app-mu300/root/usr/libexec/rpcd/mu300dash').read_text()
        self.assertIn('|source|dismiss)', dash)
        self.assertIn('allowed "$url"', dash)
        adapter = (TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/update').read_text()
        self.assertIn('"$CMD" source', adapter)
        # the fallback tells an unreachable mirror from an answering one by curl's exit codes, so the image carries
        # curl (the wget family fails every way alike)
        self.assertRegex((TOP / 'openwrt/build-rootfs.sh').read_text(), r'apk add[^>]*\bcurl\b')
        updater = (TOP / 'openwrt/opt/mu300/bin/mu300-update').read_text()
        self.assertIn('source) shift; source_cmd "$@"', updater)
        self.assertIn('"source":%s}', updater)

    def test_the_vpn_module_wiring(self):
        # mu300-update knows the module (fixed asset names, the repository's latest release, MU300_VPN_URL), and
        # mu300-extra lists vpn and its engine extras from it (module v2: the core, and the bundle as the fallback)
        up = (BIN / 'mu300-update').read_text()
        self.assertRegex(up, r'(?m)^EXTRAS="vpn vpn-mihomo vpn-xray vpn-singbox vpn-dae audio"$')
        self.assertRegex(up, r'(?m)^VPN_ENGINE_EXTRAS="vpn-xray vpn-singbox vpn-mihomo vpn-dae"$')
        self.assertRegex(up, r'(?m)^VPN_REPO=dikeckaan/mu300-linux-vpn$')
        self.assertIn('VPN_URL=${MU300_VPN_URL:-https://github.com/$VPN_REPO/releases/latest/download}', up)
        self.assertIn('vpn) echo mu300-linux-vpn-core.tar.gz ;; vpn-bundle) echo mu300-linux-vpn.tar.gz ;;', up)
        self.assertIn('vpn-mihomo|vpn-xray|vpn-singbox|vpn-dae) echo "mu300-linux-$1.tar.gz" ;;', up)
        body = up[up.index('\napply() {'):up.index('\nclean_broken() {')]
        # the module is had before anything changes, and put in place before the systems are switched
        self.assertLess(body.index('modules_fetch'), body.index('all files are here and verified'))
        self.assertLess(body.index('modules_install'), body.index('apply_one'))
        self.assertLess(body.index('apply_one'), body.index('modules_link'))
        ex = (BIN / 'mu300-extra').read_text()
        self.assertIn('vpn-mihomo', ex.split('BIN=')[0])
        self.assertIn('--from FILE|DIR', ex.split('BIN=')[0])

    def test_images_carry_jq(self):
        # the VPN module's mu300-vpn reads vmess links and raw Xray/sing-box configs with jq, so the image has it; the
        # CI runner needs jq for the same tests
        owrt = (TOP / 'openwrt/build-rootfs.sh').read_text()
        first = re.search(r'\napk add (?!--allow-untrusted)(.*?)>/dev/null', owrt, re.S).group(1)  # from the feed
        self.assertIn('jq', first.replace('\\\n', ' ').split())
        ci = (TOP / '.github/workflows/tests.yml').read_text()
        self.assertTrue(any('jq' in m.split() for m in re.findall(r'sudo apt-get install -y -qq ([^>]*)>', ci)))

    def test_images_carry_docker(self):
        # Docker from OpenWrt's feed: dockerd does not start at boot, its data is on the Linux partition beside the
        # systems (an update never copies it), and it uses Docker's nftables backend (no iptables-nft translation)
        owrt = (TOP / 'openwrt/build-rootfs.sh').read_text()
        # OpenWrt: in the image with MU300_DOCKER=1, ready to install (its settings and zone only) by default - 190 MB,
        # twice over during an update, do not fit OpenWrt's smallest region (800 MiB)
        self.assertIn('case ${MU300_DOCKER:-0} in', owrt)
        self.assertIn('-e MU300_DOCKER="$DOCKER"', owrt)
        block = owrt[owrt.index('if [ "$MU300_DOCKER" = 1 ]; then'):]
        block = block[:block.index('\nfi\n')]
        self.assertIn('\n    apk add dockerd docker docker-compose >/dev/null\n', block)
        # the panel's page for it, before the loop that adds the translations of the luci-apps
        self.assertIn('apk add luci-app-dockerman >/dev/null', block)
        self.assertLess(owrt.index('apk add luci-app-dockerman'), owrt.index('for c in base $(apk list --installed'))
        # what its kmods put in /etc/modules.d goes; it does not start at boot
        self.assertLess(block.index('ls /etc/modules.d /etc/modules-boot.d > /tmp/modules.before'),
                        block.index('apk add dockerd'))
        self.assertIn('/etc/rc.d/S*dockerd', block)
        self.assertIn('echo "dockerd starts at boot"', owrt)
        self.assertIn('rm -f $R/etc/uhttpd.key $R/etc/uhttpd.crt', owrt)
        # the build script is one single-quoted sh -c argument: no apostrophe may be in it
        start = "mu300-openwrt-base:$VER /bin/sh -eu -c '"
        body = owrt[owrt.index(start) + len(start):owrt.rindex("ls -la /out/$OUT'")]
        self.assertNotIn("'", body)
        # the package reads Docker's own file (alt_config_file), which an apk add on the device leaves as it is
        cfg = (OPENWRT / 'etc/config/dockerd').read_text()
        self.assertIn("\toption alt_config_file '/etc/docker/daemon.json'\n", cfg)
        conf = json.loads((OPENWRT / 'etc/docker/daemon.json').read_text())
        self.assertEqual(conf['data-root'], '/mnt/mu300-disk/docker/openwrt-luci')
        self.assertIn('/mnt/mu300-disk/docker/$MU300_SYSTEM', owrt)
        self.assertEqual(conf['firewall-backend'], 'nftables')
        self.assertEqual(conf['storage-driver'], 'overlay2')
        self.assertIs(conf['features']['containerd-snapshotter'], False)
        self.assertEqual(conf['default-address-pools'], [{'base': '172.16.0.0/12', 'size': 20}])
        sysctl = (OPENWRT / 'etc/sysctl.d/13-mu300-docker.conf').read_text()
        self.assertIn('net.bridge.bridge-nf-call-iptables=0', sysctl)
        # the firewall: the docker zone covers only Docker's range, never br-lan; out to wan, in from the LAN only
        zone = (OPENWRT / 'etc/uci-defaults/95-mu300-docker').read_text()
        self.assertTrue(os.access(OPENWRT / 'etc/uci-defaults/95-mu300-docker', os.X_OK))
        for line in ("set network.docker.device='docker0'", "set network.@device[-1].name='docker0'",
                     "add_list firewall.docker.network='docker'", "add_list firewall.docker.device='br-+'", "add_list firewall.docker.device='!br-lan'",
                     "add_list firewall.docker.subnet='172.16.0.0/12'", "set firewall.docker_wan.dest='wan'",
                     "set firewall.lan_docker.src='lan'"):
            self.assertIn(line, zone)
        self.assertNotRegex(zone, r"src='wan'")
        # containers never reach the router itself, nor the LAN, nor one Docker network another (security review)
        self.assertIn("set firewall.docker.input='REJECT'", zone)
        self.assertIn("set firewall.docker.forward='REJECT'", zone)
        self.assertNotIn("firewall.docker.input='ACCEPT'", zone)
        self.assertNotRegex(zone, r"src='docker'\nset firewall\.[a-z_]*\.dest='lan'")
        # the marker is a file of the device, so a zone the user deleted is not made again after an update
        self.assertIn('[ -e /etc/mu300/docker-zone ] && exit 0', zone)

    def test_release_audit_refuses_the_audio_stack(self):
        # call audio is the audio extra's: the image carries none of its programs or services
        rel = (TOP / 'tools' / 'make-release.sh').read_text()
        self.assertIn('(opt/mu300/bin/(mu300-audio-dsp|mu300-voice|pcmhost|ctlset|mu300-hfp)|init\\.d/mu300-(audio|voice)|'
                      'modprobe\\.d/mu300-audio\\.conf)( |$)', rel)
        self.assertIn("carries the audio extra's programs or services", rel)

    def test_ttl_page_is_wired(self):
        # Cellular > TTL: the menu entry right after the AT terminal, its view, the rpc declarations in common.js,
        # ttl_get readable and ttl_set only with write access, the adapter executable
        import json
        app = TOP / 'openwrt' / 'luci-app-mu300'
        menu = json.loads((app / 'root/usr/share/luci/menu.d/luci-app-mu300.json').read_text())
        ttl = menu['admin/modem/ttl']
        self.assertEqual(ttl['title'], 'TTL')
        self.assertEqual(ttl['action'], {'type': 'view', 'path': 'mu300/ttl'})
        cellular = sorted((v['order'], k) for k, v in menu.items() if k.startswith('admin/modem/'))
        keys = [k for _, k in cellular]
        self.assertEqual(keys[keys.index('admin/modem/at') + 1], 'admin/modem/ttl')
        self.assertEqual(len({o for o, _ in cellular}), len(cellular), 'two Cellular pages with the same order')
        self.assertTrue((app / 'htdocs/luci-static/resources/view/mu300/ttl.js').is_file())
        common = (app / 'htdocs/luci-static/resources/mu300/common.js').read_text()
        self.assertIn("method: 'ttl_get'", common)
        self.assertIn("method: 'ttl_set', params: [ 'value' ]", common)
        self.assertIn('callTtlGet: callTtlGet, callTtlSet: callTtlSet', common)
        acl = json.loads((app / 'root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())['luci-app-mu300']
        self.assertIn('ttl_get', acl['read']['ubus']['mu300dash'])
        self.assertNotIn('ttl_set', acl['read']['ubus']['mu300dash'])
        self.assertIn('ttl_set', acl['write']['ubus']['mu300dash'])
        self.assertTrue((app / 'root/usr/libexec/unisoc-modem/ttl').stat().st_mode & 0o111)
        # the fast-path note belongs to the nft backend only
        view = (app / 'htdocs/luci-static/resources/view/mu300/ttl.js').read_text()
        self.assertRegex(view, r"if \(st\.backend === 'nft'\)\s*card\.appendChild\([^;]*firewall fast-path is off")

    def test_tcp_and_boot_cards_are_wired(self):
        # kanoqwq's b9f71e72 (the TCP card on the Device page) and f6b0668d (the boot state on the dashboard): rpc
        # declarations, tcp_get/boot_get readable, tcp_set only with write access, the adapters executable; the boot
        # card only reads (its changes stay the act os buttons)
        import json
        app = TOP / 'openwrt' / 'luci-app-mu300'
        common = (app / 'htdocs/luci-static/resources/mu300/common.js').read_text()
        self.assertIn("method: 'tcp_get'", common)
        self.assertIn("method: 'tcp_set', params: [ 'value' ]", common)
        self.assertIn("method: 'boot_get', expect", common)
        self.assertIn('callTcpGet: callTcpGet, callTcpSet: callTcpSet, callBootGet: callBootGet', common)
        acl = json.loads((app / 'root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())['luci-app-mu300']
        self.assertIn('tcp_get', acl['read']['ubus']['mu300dash'])
        self.assertIn('boot_get', acl['read']['ubus']['mu300dash'])
        self.assertNotIn('tcp_set', acl['read']['ubus']['mu300dash'])
        self.assertIn('tcp_set', acl['write']['ubus']['mu300dash'])
        self.assertNotIn('boot_set', json.dumps(acl))
        for name in ('tcp', 'boot'):
            self.assertTrue((app / 'root/usr/libexec/unisoc-modem' / name).stat().st_mode & 0o111, name)
        self.assertTrue((TOP / 'openwrt/opt/mu300/bin/mu300-tcp').stat().st_mode & 0o111)
        device = (app / 'htdocs/luci-static/resources/view/mu300/device.js').read_text()
        self.assertIn('M.callTcpGet()', device)
        self.assertIn('M.callTcpSet(value)', device)
        self.assertIn('s.net_current', device)
        self.assertIn('d.eligible === 0', device)
        home = (app / 'htdocs/luci-static/resources/view/mu300/home.js').read_text()
        self.assertIn('M.callBootGet()', home)

    def test_release_can_be_a_prerelease(self):
        # a release is published as a prerelease first and promoted to "latest" only after a board ran it: the
        # option reaches `gh release create`, and an unknown option stops the script instead of being ignored
        mr = (TOP / 'tools' / 'make-release.sh').read_text()
        self.assertIn('--prerelease) PRERELEASE=--prerelease ;;', mr)
        self.assertIn('--publish) PUBLISH=--publish ;;', mr)
        self.assertRegex(mr, r'\*\) echo "unknown option \$a')
        self.assertRegex(mr, r'gh release create "\$TAG" [^\n]*\$PRERELEASE')
        self.assertNotIn('--latest', mr)

    def test_proc_reads_are_braced(self):
        # `tr < /proc/$pid/cmdline 2>/dev/null` reports a failed redirection (the process just went) before its own
        # 2>/dev/null applies, so "can't open /proc/..." reaches the log: the read is braced, `{ tr < ...; } 2>/dev/null`
        files = [p for p, _ in shell_scripts()]
        files += [p for p in (TOP / 'openwrt' / 'luci-app-mu300' / 'root').rglob('*')
                  if p.is_file() and shebang(p).endswith('sh')]
        bad = re.compile(r'<\s*"?/proc/\$[^\s;|]*\s+2>\s*/dev/null')
        found = []
        for p in sorted(set(files)):
            for n, line in enumerate(p.read_text(errors='replace').splitlines(), 1):
                if bad.search(line):
                    found.append(f'{p.relative_to(TOP)}:{n}')
        self.assertEqual(found, [])

    def test_quiet_console_sysctl(self):
        # the drop-in procd's /etc/init.d/sysctl applies
        a = (TOP / 'openwrt' / 'overlay' / 'etc' / 'sysctl.d' / '99-mu300-console.conf').read_text()
        self.assertRegex(a, r'(?m)^kernel\.printk = 1$')

    def test_modem_control_is_released_early_and_without_a_fixed_wait(self):
        # S09 (leading zero kept, rc.common embeds START verbatim: S9 would sort after S19) and no
        # unconditional sleep before android-vendor-start, which waits for the modem nodes itself
        f = (TOP / 'openwrt' / 'overlay' / 'etc' / 'init.d' / 'mu300-vendor').read_text()
        self.assertRegex(f, r'(?m)^START=09$')
        self.assertIn('leading zero', f)
        self.assertIn('procd_set_param command /opt/mu300/bin/android-vendor-start\n', f)
        self.assertNotRegex(f, r'sleep 5')
        # nothing else may order itself in front of it
        for s in (TOP / 'openwrt' / 'overlay' / 'etc' / 'init.d').iterdir():
            m = re.search(r'(?m)^START=(\d+)', s.read_text())
            if s.name != 'mu300-vendor' and m:
                self.assertGreater(int(m.group(1)), 9, s.name)

    def test_sysd_replaces_the_loops(self):
        # mu300-sysd does the keys, the LED tick and the thermal guard on events, in one process
        build = (TOP / 'openwrt/build-rootfs.sh').read_text()
        enabled = build.split('for s in mu300-accounts')[1].split('; do')[0].split()
        self.assertIn('mu300-sysd', enabled)
        init = (OPENWRT / 'etc/init.d/mu300-sysd').read_text()
        self.assertRegex(init, r'(?m)^START=95$')
        self.assertIn('procd_set_param command /usr/bin/ucode /opt/mu300/bin/mu300-sysd\n', init)
        self.assertNotIn('/etc/mu300/sysd', init)
        self.assertNotIn('thermalguard', (OPENWRT / 'etc/init.d/mu300-post').read_text())
        sysd = (BIN / 'mu300-sysd').read_text()
        self.assertIn("'-u', UEVENTS", sysd)
        self.assertIn("const UEVENTS = 'power_supply,extcon';", sysd)   # the charger has no interrupt: the plug is extcon

    def test_atd_before_the_network_with_a_second_channel_on_nr2(self):
        # S19 (the dial at S20 asks the daemon), nr1 and nr2 (nr2 without a URC channel), and the
        # panel's nr6 and nr7, all brokers of mu300-modemd behind mu300-atd-io (the instances: test_sim2)
        f = (TOP / 'openwrt' / 'overlay' / 'etc' / 'init.d' / 'mu300-atd').read_text()
        self.assertRegex(f, r'(?m)^START=19$')
        sim1 = f[f.index('\nstart_sim1() {'):f.index('\nstart_sim2() {')]
        self.assertEqual(re.findall(r'procd_open_instance (\S+)', sim1), ['radio-warmup'])
        self.assertIn('modemd_broker atd "$cmddev" /run/mu300-at "$((slot * 3))" \'\' 1', sim1)
        self.assertIn("modemd_broker atd2 \"$datadev\" /run/mu300-at2 '' '' 1", sim1)
        self.assertIn('modemd_start 1', sim1)
        self.assertNotIn('/etc/mu300/modemd', f)
        self.assertNotIn('/opt/mu300/bin/mu300-atd ', f)
        # radio-warmup opens no channel: it waits for nr1's broker and asks it
        warm = sim1[sim1.index('procd_open_instance radio-warmup'):]
        warm = warm[:warm.index('procd_close_instance')]
        self.assertIn('until [ -p /run/mu300-at/cmd ]', warm)
        self.assertIn('exec /opt/mu300/bin/mobile-data radio-on', warm)
        # a charging boot (init's marker) keeps the radio off until the Wi-Fi key
        self.assertLess(warm.index('[ -e /run/mu300/charging-boot ] && exit 0'), warm.index('mobile-data radio-on'))
        self.assertNotIn('stty_nr', warm)
        self.assertNotIn('respawn', warm)   # one round per start; netifd's dial and watch retry
        self.assertIn('[ ! -x /usr/libexec/unisoc-modem/lock ] || : > /run/unisoc-modem-early-hook-pending', sim1)
        for gone in ('opt/mu300/bin/mu300-atd', 'luci-overlay/etc/init.d/mu300-atd-dash'):
            self.assertFalse((TOP / 'openwrt' / gone).exists(), gone)

    def test_cellular_downlink_in_the_software_flowtable(self):
        # mu300cell reports sipa_eth0 as l3_device only, so fw4 leaves it out of its flowtable and the
        # downlink takes the slow forwarding path. The patch puts it in; it is applied with --fuzz=0, so a changed fw4
        # fails the build instead of shipping without it.
        patch = (TOP / 'openwrt' / 'patches' / 'fw4-sipa-offload.patch').read_text()
        self.assertIn('+++ b/usr/share/ucode/fw4.uc', patch)
        self.assertIn("+\t\t\tif (fs.access('/sys/class/net/sipa_eth0'))", patch)
        self.assertIn("+\t\t\t\tpush(devices, 'sipa_eth0');", patch)
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.assertIn('-v "$FW4PATCH":/in/fw4-sipa-offload.patch:ro', text)
        apply = 'patch --batch --fuzz=0 -d $R -p1 -i /in/fw4-sipa-offload.patch'
        self.assertIn(apply, text)
        self.assertLess(text.index('cp -a /in/overlay/. $R/'), text.index(apply))
        self.assertLess(text.index(apply), text.index('cp -a /in/luci-overlay/. $R/'))
        # the patch tool is installed after the copy into $R: the build container has it, the image does not
        self.assertLess(text.index('for e in /*; do'), text.index('apk add patch'))
        self.assertLess(text.index(apply), text.index('apk del patch'))
        self.assertLess(text.index('apk del patch'), text.index('apk list --installed | sort'))
        # software offloading on, hardware off: the SIPA and SC2355 drivers have no nftables hardware offload
        uci = (OPENWRT / 'etc' / 'uci-defaults' / '90-mu300').read_text()
        self.assertIn("uci -q set firewall.@defaults[0].flow_offloading='1'", uci)
        self.assertIn("uci -q set firewall.@defaults[0].flow_offloading_hw='0'", uci)
        self.assertIn('uci commit firewall', uci)

    def test_build_stops_without_the_cellular_protocol(self):
        # without mu300cell.sh netifd has no wan, and the image would boot without mobile data; the first boot
        # selects relay mode, which runs mu300cell-v6.sh. Checked before any download, as the patches are.
        def run(files, extra_env=None):
            with tempfile.TemporaryDirectory() as d:
                top = Path(d)
                (top / 'openwrt').mkdir()
                shutil.copy(TOP / 'openwrt' / 'build-rootfs.sh', top / 'openwrt' / 'build-rootfs.sh')
                for rel, body in files.items():
                    (top / rel).parent.mkdir(parents=True, exist_ok=True)
                    (top / rel).write_text(body)
                env = dict(os.environ, MU300_INPUTS=d, PATH='/usr/bin:/bin', MU300_LUCI_THEME_APK=str(top / 'no-theme.apk'))
                env.update(extra_env or {})
                return subprocess.run(['sh', str(top / 'openwrt' / 'build-rootfs.sh'), 'x.tar.gz'], env=env,
                                      capture_output=True, text=True, timeout=30)
        need = {'openwrt/overlay/lib/netifd/proto/mu300cell.sh': 'x\n',
                'openwrt/luci-overlay/lib/netifd/proto/mu300cell-v6.sh': 'x\n',
                'openwrt/overlay/usr/share/ucode/mu300/cellparse.uc': 'x\n',
                'openwrt/patches/fw4-sipa-offload.patch': 'x\n',
                'openwrt/patches/wifi-scripts-no-dfs.patch': 'x\n',
                'openwrt/hostapd/build-hostapd.sh': 'x\n'}
        for missing in need:
            with self.subTest(missing=missing):
                r = run({k: v for k, v in need.items() if k != missing})
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn(missing.rsplit('/', 1)[1], r.stderr)
        # with every file present no check stops the build (it stops later, at the theme this tree lacks)
        r = run(need)
        self.assertNotEqual(r.returncode, 0, r.stderr)
        self.assertIn('theme package missing', r.stderr)
        self.assertNotIn('required file missing', r.stderr)

    def test_panel_mounts_survive_spaces_in_paths(self):
        # every docker mount is its own quoted argument: a checkout or a MU300_LUCI_THEME_APK path with a space must
        # not break the build
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        for m in ('-v "$THEME_APK:/in/luci-theme-aurora.apk:ro"', '-v "$TOP/openwrt/luci-app-mu300:/in/luci-plugin:ro"',
                  '-v "$TOP/openwrt/luci-overlay:/in/luci-overlay:ro"', '-v "$CAT:/in/catalogs:ro"',
                  '-v "$TOP/openwrt/opt/mu300":/in/opt-mu300:ro', '-v "$TOP/openwrt/overlay":/in/overlay:ro'):
            self.assertIn(m, text)

    def test_openwrt_luci_starts_ndp_learn(self):
        # init.d/mu300-ndp is enabled in openwrt-luci's image (it does nothing unless wan is in relay mode) and
        # is executable there, beside the SMS service
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        block = text[text.index('ln -sf ../init.d/unisoc-modem-ui $R/etc/rc.d/'):text.index('apk list --installed | sort')]
        self.assertIn('$R/etc/init.d/mu300-ndp', block)
        self.assertIn('ln -sf ../init.d/mu300-ndp $R/etc/rc.d/S${n}mu300-ndp', block)
        for f in ('etc/init.d/mu300-ndp', 'opt/mu300/bin/ndp-learn', 'lib/netifd/proto/mu300cell-v6.sh'):
            self.assertTrue((LUCI_OVERLAY / f).stat().st_mode & 0o111, f)

    def test_board_json_names_the_usb_lan_port(self):
        # panel page audit: armsr's 99-default_network gave board.json a LAN on eth0, which the MU300 does not have;
        # LuCI's Status > Overview showed an "eth0, no link" port and its ubus status call failed. board.d/05 names
        # usb0 first (board_detect runs board.d in name order, and 99-default_network leaves a network it finds)
        d = OPENWRT / 'etc' / 'board.d'
        self.assertEqual(sorted(p.name for p in d.iterdir()), ['05_mu300_network'])
        self.assertLess('05_mu300_network', '99-default_network')
        f = d / '05_mu300_network'
        self.assertTrue(f.stat().st_mode & 0o111)
        stub = ('board_config_update() { echo update; }\nboard_config_flush() { echo flush; }\n'
                'ucidef_set_interface_lan() { echo "lan $*"; }\n')
        body = f.read_text().replace('. /lib/functions/uci-defaults.sh', stub)
        self.assertNotEqual(body, f.read_text())
        for s in shells():
            r = subprocess.run(s + ['-c', body], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, 'update\nlan usb0\nflush\n'), (s, r.stderr))

    def test_sqm_default_queue_names_the_cellular_uplink(self):
        # panel page audit: the image's /etc/config/sqm (sqm-scripts' default, disabled) named eth1, which the
        # MU300 does not have; the build names sipa_eth0 instead, and stops when a queue still names eth1
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        start = text.index('if [ -f $R/etc/config/sqm ]; then')
        block = text[start:text.index('\nfi\n', start) + 4]
        # a uci stand-in over one file of "sqm.SECTION.OPTION='VALUE'" lines (what uci show prints)
        stub = ('uci() { while [ "${1#-}" != "$1" ]; do [ "$1" = -c ] && shift; shift; done\n'
                '  case $1 in show) cat "$CFG" ;; commit) echo "commit $2" >> "$LOG" ;;\n'
                '  set) k=${2%%=*}; v=${2#*=}; grep -v "^$k=" "$CFG" > "$CFG.n"; echo "$k=\'$v\'" >> "$CFG.n"; '
                'mv "$CFG.n" "$CFG" ;; esac; }\n')
        for s in shells():
            for queues, want in (
                    (["sqm.eth1=queue", "sqm.eth1.enabled='0'", "sqm.eth1.interface='eth1'"],
                     ["sqm.eth1.interface='sipa_eth0'"]),
                    (["sqm.mine=queue", "sqm.mine.interface='br-lan'"], ["sqm.mine.interface='br-lan'"])):
                with tempfile.TemporaryDirectory() as d, self.subTest(shell=s, queues=queues):
                    (Path(d) / 'etc' / 'config').mkdir(parents=True)
                    (Path(d) / 'etc' / 'config' / 'sqm').write_text('x\n')
                    cfg = Path(d) / 'show'
                    cfg.write_text('\n'.join(queues) + '\n')
                    r = subprocess.run(s + ['-c', f'R={d}\n{stub}{block}echo done'], capture_output=True, text=True,
                                       env=dict(os.environ, CFG=str(cfg), LOG=str(Path(d) / 'log')))
                    self.assertEqual((r.returncode, r.stdout), (0, 'done\n'), r.stderr)
                    lines = cfg.read_text().splitlines()
                    for w in want:
                        self.assertIn(w, lines)
                    self.assertNotIn("sqm.eth1.interface='eth1'", lines)
                    self.assertEqual((Path(d) / 'log').read_text(), 'commit sqm\n')


class ApkRetry(unittest.TestCase):
    """openwrt/build-rootfs.sh: the feed's packages are fetched while the image is built, and a download that broke
    off would fail the image; update, upgrade and add are tried three times."""

    def setUp(self):
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.block = re.search(r'# --- apk-retry begin\n(.*?)# --- apk-retry end\n', text, re.S).group(1)
        # the function stands before the first use, inside the one single-quoted script of the container
        self.assertLess(text.index('# --- apk-retry end'), text.index('\napk update >/dev/null'))
        self.assertNotIn("'", self.block)
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-test-'))
        stub = self.tmp / 'apk'
        # fails FAIL times (a counter in a file), then succeeds; records every call
        stub.write_text('#!/bin/sh\necho "$*" >> "$LOG"\nn=$(cat "$COUNT" 2>/dev/null || echo 0)\n'
                        '[ "$n" -lt "$FAIL" ] || { echo "ok $1"; exit 0; }\necho $((n + 1)) > "$COUNT"\nexit 1\n')
        stub.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_apk(self, shell, args, fail):
        for f in ('log', 'count'):
            (self.tmp / f).unlink(missing_ok=True)
        env = dict(os.environ, PATH=f'{self.tmp}{os.pathsep}{os.environ["PATH"]}', LOG=str(self.tmp / 'log'),
                   COUNT=str(self.tmp / 'count'), FAIL=str(fail), MU300_APK_RETRY_WAIT='0')
        # as the build runs it: sh -eu, the output thrown away
        r = subprocess.run(shell + ['-eu', '-c', self.block + f'apk {args} >/dev/null\necho done'], env=env,
                           capture_output=True, text=True, timeout=30)
        log = (self.tmp / 'log').read_text().splitlines() if (self.tmp / 'log').exists() else []
        return r, log

    def test_a_download_that_breaks_off_is_tried_again(self):
        for shell in shells():
            for cmd in ('update', 'upgrade', 'add wpad-basic-mbedtls iw', 'add --allow-untrusted /in/x.apk'):
                for fail, tries, ok in ((0, 1, True), (1, 2, True), (2, 3, True), (3, 3, False)):
                    r, log = self.run_apk(shell, cmd, fail)
                    self.assertEqual(log, [cmd] * tries, (shell, cmd, fail))     # the same arguments every time
                    self.assertEqual((r.returncode == 0, 'done' in r.stdout), (ok, ok), (shell, cmd, fail, r.stderr))
                    self.assertEqual(r.stderr.count('trying again'), tries - 1)

    def test_anything_else_runs_once(self):
        for shell in shells():
            r, log = self.run_apk(shell, 'del procd-ujail', 1)
            self.assertEqual((log, r.returncode), (['del procd-ujail'], 1), shell)
            self.assertNotIn('done', r.stdout)
            # "apk del ... || true", as the build has it
            env = dict(os.environ, PATH=f'{self.tmp}{os.pathsep}{os.environ["PATH"]}', LOG=str(self.tmp / 'log'),
                       COUNT=str(self.tmp / 'count'), FAIL='9')
            r = subprocess.run(shell + ['-eu', '-c', self.block + 'apk del x >/dev/null 2>&1 || true\necho done'],
                               env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'done'), r.stderr)


if __name__ == '__main__':
    unittest.main()
