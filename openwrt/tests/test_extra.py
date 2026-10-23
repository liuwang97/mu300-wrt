"""Extras: optional components that are not part of the image and live on the Linux partition
(MU300_DISK/extra/<name>): the VPN module (vpn and its engines) from its own repository, dikeckaan/mu300-linux-vpn,
the audio extra, and extras of the user's own. mu300-update installs and updates them (sourced with MU300_LIB=1
against a fake partition), mu300-extra is the command, and the boot runs `mu300-extra link`."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import unittest

from helpers import BIN, ShellTest

ENGINES = ('xray', 'hev-socks5-tunnel', 'sing-box')
VPN_URL = 'http://10.0.0.2:8000/vpn-module'


def extra_tarball(path, name='vpn', release='v2026.10.10', bins=ENGINES, components='xray 26.3.27\n'):
    """an extra in the format of the mu300-linux releases (./name ./release ./components ./bin): for the VPN, what
    the releases before the module carried (the engines alone, no mu300-vpn)"""
    with tarfile.open(path, 'w:gz') as t:
        def add(fn, data, mode=0o644):
            ti = tarfile.TarInfo(fn)
            ti.size, ti.mode = len(data), mode
            t.addfile(ti, io.BytesIO(data))
        if name is not None:
            add('./name', (name + '\n').encode())
        add('./release', (release + '\n').encode())
        add('./components', components.encode())
        for b in bins:
            add(f'./bin/{b}', f'#!/bin/sh\necho {b} {release}\n'.encode(), 0o755)
    return path


# the module's hooks: they record what they were asked (into $STUBLOG/hooks) and, for a system on the disk, do what
# the real ones do in that system: mu300-vpn linked to the extra's
HOOK_LINK = ('echo "link [$1]" >> "$STUBLOG/hooks"\n'
             '[ -z "$1" ] || { mkdir -p "$1/opt/mu300/bin" && ln -sfn "$MU300_EXTRA_DIR/bin/mu300-vpn" "$1/opt/mu300/bin/mu300-vpn"; }\n')
HOOK_UNLINK = 'echo "unlink [$1]" >> "$STUBLOG/hooks"\n[ -z "$1" ] || rm -f "$1/opt/mu300/bin/mu300-vpn"\n'


def module_tarball(path, version='v1.0.0', requires='mu300-linux v2026.10.16', mu300_vpn=True, hooks=True,
                   bins=ENGINES, extra=()):
    """mu300-linux-vpn.tar.gz as the module repository publishes it (the spec's layout)"""
    files = [('./VERSION', version + '\n', 0o644), ('./components', f'mu300-linux-vpn {version}\nxray 26.3.27\n', 0o644)]
    if requires is not None:
        files.append(('./requires', requires + '\n', 0o644))
    if mu300_vpn:
        files.append(('./bin/mu300-vpn', f'#!/bin/sh\necho mu300-vpn {version} "$@"\n', 0o755))
    files += [(f'./bin/{b}', f'#!/bin/sh\necho {b} {version}\n', 0o755) for b in bins]
    files += [('./lib/vpn/x.sh', 'drv_start() { :; }\n', 0o644), ('./lib/vpn/openvpn-up', '#!/bin/sh\n', 0o755),
              ('./etc/vpn.conf.example', 'ENABLE=0\n', 0o644),
              ('./service/procd/mu300-vpn', '#!/bin/sh /etc/rc.common\n', 0o755)]
    if hooks:
        files += [('./hooks/link', HOOK_LINK, 0o755), ('./hooks/unlink', HOOK_UNLINK, 0o755)]
    files += list(extra)
    with tarfile.open(path, 'w:gz') as t:
        for fn, data, mode in files:
            data = data.encode() if isinstance(data, str) else data
            ti = tarfile.TarInfo(fn)
            ti.size, ti.mode = len(data), mode
            t.addfile(ti, io.BytesIO(data))
    return path


def mihomo_tarball(path, version='v1.0.0'):
    with tarfile.open(path, 'w:gz') as t:
        for fn, data, mode in (('./VERSION', f'{version}\n', 0o644), ('./components', 'mihomo 1.19.32\n', 0o644),
                               ('./requires', 'mu300-linux v2026.10.16\n', 0o644),
                               ('./bin/mihomo', '#!/bin/sh\necho mihomo\n', 0o755)):
            ti = tarfile.TarInfo(fn)
            ti.size, ti.mode = len(data), mode
            t.addfile(ti, io.BytesIO(data.encode()))
    return path


def sums(d, *names):
    """SHA256SUMS in directory D for the files NAMES there"""
    (d / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256((d / n).read_bytes()).hexdigest()}  {n}\n' for n in names))


class ExtrasBase(ShellTest):
    def setUp(self):
        super().setUp()
        self.disk = self.tmp / 'disk'
        (self.disk / 'openwrt-luci/etc/mu300').mkdir(parents=True)
        self.root = self.tmp / 'root'
        (self.root / 'run/mu300').mkdir(parents=True)
        (self.root / 'etc').mkdir(parents=True)
        (self.root / 'etc/openwrt_release').write_text("DISTRIB_ID='OpenWrt'\n")
        (self.root / 'run/mu300/device').write_text('f50\n')
        self.links = self.tmp / 'links'
        self.links.mkdir()
        self.stub('id', 'echo 0')

    def up(self, shell, code, **env):
        # (MU300_VPN_UNVERIFIED: the tests' lone module files have no SHA256SUMS; the refusal is tested with it empty)
        e = dict(MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root, MU300_FETCH_DELAY=0,
                 MU300_VPN_UNVERIFIED=1)
        e.update(env)
        return self.sh(shell, f'. "{BIN}/mu300-update"; {code}', **e)

    def ex(self, shell, *args, **env):
        # (a kernel that runs dae: the check before vpn-dae is downloaded reads these, not the test host's)
        e = dict(MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root, MU300_LINKDIR=self.links,
                 MU300_OPT=BIN.parent, MU300_FETCH_DELAY=0, MU300_VPN_UNVERIFIED=1, MU300_KREL='7.2.11',
                 MU300_BTF=BIN / 'mu300-extra')
        e.update(env)
        return self.script(shell, BIN / 'mu300-extra', *args, **e)

    def vpn(self):
        return self.disk / 'extra' / 'vpn'

    def old_vpn_extra(self, release='v2026.10.15'):
        """the vpn extra of a release before the module: the engines alone (an older mu300-update installed it)"""
        shutil.rmtree(self.vpn(), ignore_errors=True)
        (self.vpn() / 'bin').mkdir(parents=True)
        (self.vpn() / 'name').write_text('vpn\n')
        (self.vpn() / 'release').write_text(release + '\n')
        for e in ENGINES:
            (self.vpn() / 'bin' / e).write_text('#!/bin/sh\n')
            (self.vpn() / 'bin' / e).chmod(0o755)

    def versions(self, v):
        """every system on the disk, and the running one, is release V"""
        for os_ in ('openwrt-luci',):
            if (self.disk / os_).exists():
                (self.disk / os_ / 'etc/mu300/image-version').write_text(v + '\n')
        (self.root / 'etc/mu300').mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/mu300/image-version').write_text(v + '\n')

    def hooks(self):
        f = self.tmp / 'hooks'
        lines = f.read_text().splitlines() if f.exists() else []
        f.unlink(missing_ok=True)
        return lines

    def serve(self, release='v2026.10.10', assets=None):
        """a release on a 'server': a directory curl (stubbed) reads from, with its SHA256SUMS. The URL's last two
        parts name the directory and the file (.../vpn-module/SHA256SUMS: the module's 'release')."""
        d = self.tmp / 'server' / release
        d.mkdir(parents=True, exist_ok=True)
        if assets is None:
            assets = {}
        for name, src in assets.items():
            (d / name).write_bytes(src.read_bytes())
        sums(d, *assets)
        self.stub('curl', f'out=; url=; while [ $# -gt 0 ]; do case $1 in -o) out=$2; shift ;; -*) ;; *) url=$1 ;; esac; '
                          f'shift; done; echo "$url" >> "$STUBLOG/urls"; f=${{url##*/}}; r=${{url%/*}}; r=${{r##*/}}; '
                          f'case $url in */releases/latest) echo \'"tag_name": "v2026.10.10"\'; exit 0 ;; esac; '
                          f'src="{self.tmp}/server/$r/$f"; [ -f "$src" ] || exit 22; '
                          f'if [ -n "$out" ]; then cat "$src" > "$out"; else cat "$src"; fi')
        return d

    def serve_module(self, **kw):
        """the module's latest release at VPN_URL"""
        return self.serve('vpn-module', assets={
            'mu300-linux-vpn.tar.gz': module_tarball(self.tmp / 'module.tar.gz', **kw),
            'mu300-linux-vpn-mihomo.tar.gz': mihomo_tarball(self.tmp / 'mihomo.tar.gz')})

    def offline(self):
        self.stub('curl', 'for a; do last=$a; done; echo "$last" >> "$STUBLOG/urls"; exit 7')
        self.stub('wget', 'exit 4')

    def urls(self):
        f = self.tmp / 'urls'
        u = f.read_text() if f.exists() else ''
        f.unlink(missing_ok=True)
        return u


class Extras(ExtrasBase):
    # ---- mu300-update: the core -----------------------------------------------------------------------------
    def test_unpack_installs_and_replaces(self):
        for shell in self.each_shell():
            old = module_tarball(self.tmp / 'old.tar.gz', version='v1.0.0')
            new = module_tarball(self.tmp / 'new.tar.gz', version='v1.1.0')
            r = self.up(shell, f'extra_unpack vpn "{old}" && extra_release vpn && extra_installed')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.split(), ['v1.0.0', 'vpn'])
            r = self.up(shell, f'extra_unpack vpn "{new}" && extra_release vpn')
            self.assertEqual(r.stdout.split(), ['v1.1.0'])
            self.assertTrue(os.access(self.vpn() / 'bin/xray', os.X_OK))
            self.assertTrue(os.access(self.vpn() / 'bin/mu300-vpn', os.X_OK))
            self.assertTrue((self.vpn() / 'lib/vpn/x.sh').is_file())
            # nothing half-done is left next to it
            self.assertEqual(sorted(p.name for p in (self.disk / 'extra').iterdir()), ['vpn'])

    def test_unpack_refuses_the_wrong_payload_and_keeps_the_old(self):
        for shell in self.each_shell():
            good = module_tarball(self.tmp / 'good.tar.gz', version='v1')
            self.up(shell, f'extra_unpack vpn "{good}"')
            for bad in (extra_tarball(self.tmp / 'oldformat.tar.gz'),                     # the engines alone
                        module_tarball(self.tmp / 'novpn.tar.gz', mu300_vpn=False),
                        module_tarball(self.tmp / 'nohooks.tar.gz', hooks=False),
                        module_tarball(self.tmp / 'deep.tar.gz', extra=[('./a/b/c/d/e', 'x', 0o644)]),
                        module_tarball(self.tmp / 'odd.tar.gz', extra=[('./bin/x;reboot', 'x', 0o755)]),
                        module_tarball(self.tmp / 'nobin.tar.gz', mu300_vpn=False, bins=())):
                r = self.up(shell, f'extra_unpack vpn "{bad}"; echo "rc=$?"')
                self.assertIn('rc=1', r.stdout, bad.name)
                self.assertEqual((self.vpn() / 'VERSION').read_text().strip(), 'v1', bad.name)
            junk = self.tmp / 'junk.tar.gz'
            junk.write_bytes(b'not a tarball')
            r = self.up(shell, f'extra_unpack vpn "{junk}"; echo "rc=$?"')
            self.assertIn('rc=1', r.stdout)
            r = self.up(shell, f'extra_unpack ../x "{good}"; echo "rc=$?"')
            self.assertIn('rc=1', r.stdout)
            self.assertEqual(sorted(p.name for p in (self.disk / 'extra').iterdir()), ['vpn'])

    def test_requires_newer_than_the_system_is_refused(self):
        f = module_tarball(self.tmp / 'm.tar.gz', requires='mu300-linux v2026.10.16')
        for shell in self.each_shell():
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            for have, ok in (('v2026.10.15', False), ('v2026.10.16', True), ('v2026.11.01', True), ('', True),
                             ('dev', True)):
                r = self.up(shell, f'rm -rf "{self.disk}/extra"; extra_unpack vpn "{f}" "{have}"; echo "rc=$?"')
                self.assertIn('rc=0' if ok else 'rc=1', r.stdout, (shell, have, r.stderr))
                if not ok:
                    self.assertIn('needs mu300-linux v2026.10.16', r.stderr)
                    self.assertFalse(self.vpn().exists())

    def test_vpn_in_use(self):
        stub = self.stubs / 'mu300-vpn'
        cases = [
            # (vpn extra, mu300-vpn enabled says, ENABLE in the updated system, in use)
            (None, None, None, False),
            (None, '0', 0, False),
            (None, '1', None, True),                 # the running system's mu300-vpn: enabled
            (None, None, 1, True),                   # a system being updated has ENABLE=1
            ('old', None, None, True),               # the engines of an older release: replaced by the module
            ('module', None, None, True),
        ]
        for shell in self.each_shell():
            for extra, says, enable, want in cases:
                self.up(shell, f'rm -rf "{self.disk}/extra"')
                (self.disk / 'openwrt-luci/etc/mu300/vpn.conf').unlink(missing_ok=True)
                if enable is not None:
                    (self.disk / 'openwrt-luci/etc/mu300/vpn.conf').write_text(f'ENABLE={enable}\n')
                if extra == 'old':
                    self.old_vpn_extra()
                elif extra == 'module':
                    self.up(shell, f'extra_unpack vpn "{module_tarball(self.tmp / "m.tar.gz")}"')
                stub.unlink(missing_ok=True)
                if says is not None:
                    self.stub('mu300-vpn', f'[ "$1" = enabled ] && echo {says}')
                r = self.up(shell, 'vpn_in_use "openwrt-luci" && echo yes || echo no', MU300_VPN_CMD=stub)
                self.assertEqual(r.stdout.strip(), 'yes' if want else 'no', (shell, extra, says, enable, r.stderr))

    def test_fetch_verified_takes_another_server(self):
        self.stub('curl', 'for a; do last=$a; done; echo "$last" >> "$STUBLOG/urls"; '
                          'case $last in */SHA256SUMS) echo "0000  x" ;; esac; exit 0')
        for shell in self.each_shell():
            (self.tmp / 'urls').unlink(missing_ok=True)
            self.up(shell, 'fetch_verified v1 x >/dev/null 2>&1', MU300_RELEASE_URL='http://10.0.0.2:8000/rel')
            urls = (self.tmp / 'urls').read_text().split()
            self.assertEqual(urls[0], 'http://10.0.0.2:8000/rel/SHA256SUMS')

    # ---- mu300-extra: the VPN module -----------------------------------------------------------------------
    def test_install_the_module_online_links_it_into_every_system(self):
        self.serve_module()
        for shell in self.each_shell():
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            self.versions('v2026.10.16')
            self.hooks()
            # the running system is the openwrt-luci directory: its hook gets the running root (MU300_SYSROOT, ""
            # on a device)
            r = self.ex(shell, 'install', 'vpn', MU300_VPN_URL=VPN_URL, MU300_ROOT=self.disk / 'openwrt-luci')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((self.vpn() / 'VERSION').read_text().strip(), 'v1.0.0')
            urls = self.urls()
            self.assertIn(f'{VPN_URL}/SHA256SUMS', urls)
            self.assertIn(f'{VPN_URL}/mu300-linux-vpn.tar.gz', urls)
            self.assertEqual(self.hooks(), [f'link [{self.root}]'])
            # its commands are on the PATH, mu300-vpn among them
            self.assertEqual(os.readlink(self.links / 'mu300-vpn'), str(self.vpn() / 'bin/mu300-vpn'))
            self.assertEqual(os.readlink(self.links / 'xray'), str(self.vpn() / 'bin/xray'))
            self.assertFalse((self.disk / '.mu300-extra').exists())
            st = self.ex(shell, 'status')
            self.assertIn('v1.0.0', st.stdout)
            self.assertIn('mu300-vpn', st.stdout)
            # again: the release is the installed one, nothing is downloaded, the systems are linked again
            r = self.ex(shell, 'install', 'vpn', MU300_VPN_URL=VPN_URL, MU300_ROOT=self.disk / 'openwrt-luci')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('latest release already', r.stdout)
            self.assertNotIn('mu300-linux-vpn.tar.gz', self.urls())
            self.assertEqual(len(self.hooks()), 1)

    def test_the_default_place_is_the_module_repository(self):
        self.serve('download', assets={'mu300-linux-vpn.tar.gz': module_tarball(self.tmp / 'm.tar.gz')})
        for shell in self.each_shell():
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            self.versions('v2026.10.16')
            r = self.ex(shell, 'install', 'vpn')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('https://github.com/dikeckaan/mu300-linux-vpn/releases/latest/download/SHA256SUMS', self.urls())

    def test_the_module_online_with_a_bad_hash_is_refused(self):
        d = self.serve_module()
        (d / 'mu300-linux-vpn.tar.gz').write_bytes(module_tarball(self.tmp / 'other.tar.gz', version='v6.6.6').read_bytes())
        for shell in self.each_shell():
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            self.versions('v2026.10.16')
            r = self.ex(shell, 'install', 'vpn', MU300_VPN_URL=VPN_URL)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(self.vpn().exists())
            self.assertEqual(self.hooks(), [])

    def test_the_module_offline_from_a_file_or_a_directory(self):
        self.offline()
        d = self.tmp / 'usb'
        d.mkdir()
        module_tarball(d / 'mu300-linux-vpn.tar.gz')
        lone = module_tarball(self.tmp / 'lone.tar.gz', version='v1.0.1')
        for shell in self.each_shell():
            self.versions('v2026.10.16')
            self.hooks()
            # a directory without SHA256SUMS: not installed unverified
            (d / 'SHA256SUMS').unlink(missing_ok=True)
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            r = self.ex(shell, 'install', 'vpn', '--from', d)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('SHA256SUMS', r.stderr)
            self.assertFalse(self.vpn().exists())
            # with it: verified, installed, linked
            sums(d, 'mu300-linux-vpn.tar.gz')
            r = self.ex(shell, 'install', 'vpn', '--from', d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((self.vpn() / 'VERSION').read_text().strip(), 'v1.0.0')
            self.assertEqual(len(self.hooks()), 1)
            # a SHA256SUMS that does not match: refused, the installed one stays
            (d / 'SHA256SUMS').write_text('0' * 64 + '  mu300-linux-vpn.tar.gz\n')
            r = self.ex(shell, 'install', 'vpn', '--from', d)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual((self.vpn() / 'VERSION').read_text().strip(), 'v1.0.0')
            r = self.ex(shell, 'install', 'vpn', '--from', d / 'mu300-linux-vpn.tar.gz')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('does not match', r.stderr)
            # a file alone, no SHA256SUMS vouching for it: refused, unless asked for unverified
            r = self.ex(shell, 'install', 'vpn', '--from', lone, MU300_VPN_UNVERIFIED='')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('not installed unverified', r.stderr)
            self.assertEqual((self.vpn() / 'VERSION').read_text().strip(), 'v1.0.0')
            r = self.ex(shell, 'install', 'vpn', '--from', lone, MU300_VPN_UNVERIFIED='1')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((self.vpn() / 'VERSION').read_text().strip(), 'v1.0.1')
            # nothing went to the network
            self.assertEqual(self.urls(), '')

    def test_the_module_needs_a_new_enough_system(self):
        self.serve_module(requires='mu300-linux v2026.10.16')
        f = module_tarball(self.tmp / 'm.tar.gz', requires='mu300-linux v2026.10.16')
        for shell in self.each_shell():
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            self.versions('v2026.10.15')
            for args in (('install', 'vpn'), ('install', 'vpn', '--from', f)):
                r = self.ex(shell, *args, MU300_VPN_URL=VPN_URL)
                self.assertNotEqual(r.returncode, 0, args)
                self.assertIn('needs mu300-linux v2026.10.16', r.stderr)
                self.assertFalse(self.vpn().exists())
                self.assertEqual(self.hooks(), [])

    def test_remove_unlinks_it_from_every_system(self):
        f = module_tarball(self.tmp / 'm.tar.gz')
        for shell in self.each_shell():
            self.versions('v2026.10.16')
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            r = self.ex(shell, 'install', 'vpn', '--from', f, MU300_ROOT=self.disk / 'openwrt-luci')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.path.islink(self.root / 'opt/mu300/bin/mu300-vpn'))
            self.hooks()
            r = self.ex(shell, 'remove', 'vpn', MU300_ROOT=self.disk / 'openwrt-luci')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self.hooks(), [f'unlink [{self.root}]'])
            self.assertFalse(self.vpn().exists())
            self.assertFalse((self.disk / 'extra/.vpn.sha256').exists())
            self.assertFalse(os.path.lexists(self.root / 'opt/mu300/bin/mu300-vpn'))
            self.assertFalse(os.path.lexists(self.links / 'mu300-vpn'))

    def test_the_module_in_or_out_drops_the_luci_menu_cache_on_openwrt(self):
        # the panel's VPN menu depends on the module's mu300-vpn in the extra (menu.d, "fs"), which LuCI checks only
        # when it builds its index cache: the cache goes with the module, in and out
        f = module_tarball(self.tmp / 'm.tar.gz')
        cache = self.root / 'tmp'
        names = ['luci-indexcache', 'luci-indexcache.0a1b2c3d.json', 'other']

        def seed():
            cache.mkdir(exist_ok=True)
            for n in names:
                (cache / n).write_text('x')

        for shell in self.each_shell():
            self.versions('v2026.10.16')
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            seed()
            r = self.ex(shell, 'install', 'vpn', '--from', f, MU300_ROOT=self.disk / 'openwrt-luci')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual([p.name for p in cache.iterdir()], ['other'])
            seed()
            r = self.ex(shell, 'remove', 'vpn', MU300_ROOT=self.disk / 'openwrt-luci')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual([p.name for p in cache.iterdir()], ['other'])

    def test_link_runs_the_hook_for_this_system(self):
        # every boot: a new image has none of the module (mu300-post, rootfs-fixups: mu300-extra link)
        f = module_tarball(self.tmp / 'm.tar.gz')
        for shell in self.each_shell():
            self.versions('v2026.10.16')
            self.up(shell, f'rm -rf "{self.disk}/extra"; extra_unpack vpn "{f}"')
            self.hooks()
            r = self.ex(shell, 'link')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.hooks(), [f'link [{self.root}]'])
            # a system older than the module needs keeps its own
            self.versions('v2026.10.15')
            r = self.ex(shell, 'link')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.hooks(), [])
            # an older vpn extra (no hooks): nothing to run
            self.versions('v2026.10.16')
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            (self.vpn() / 'bin').mkdir(parents=True)
            r = self.ex(shell, 'link')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.hooks(), [])
            r = self.ex(shell, 'list')
            self.assertIn('the engines of an older release only', r.stdout)

    def test_install_from_a_local_file_and_remove(self):
        f = module_tarball(self.tmp / 'local.tar.gz', version='dev')
        for shell in self.each_shell():
            r = self.ex(shell, 'install', 'vpn', '--from', f)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.path.islink(self.links / 'sing-box'))
            # a file of the user's own with the same name is never replaced
            (self.links / 'hev-socks5-tunnel').unlink()
            (self.links / 'hev-socks5-tunnel').write_text('mine')
            r = self.ex(shell, 'remove', 'vpn')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(self.vpn().exists())
            self.assertFalse(os.path.lexists(self.links / 'sing-box'))
            self.assertEqual((self.links / 'hev-socks5-tunnel').read_text(), 'mine')
            (self.links / 'hev-socks5-tunnel').unlink()

    def test_remove_refuses_while_the_vpn_is_on(self):
        f = module_tarball(self.tmp / 'local.tar.gz', version='dev')
        (self.root / 'etc/mu300').mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/mu300/vpn.conf').write_text('ENABLE=1\n')
        for shell in self.each_shell():
            r = self.ex(shell, 'install', 'vpn', MU300_EXTRA_FILE=f)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.hooks()
            r = self.ex(shell, 'remove', 'vpn')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('--force', r.stderr)
            self.assertTrue(self.vpn().exists())
            self.assertEqual(self.hooks(), [])
            r = self.ex(shell, 'remove', 'vpn', '--force')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(self.vpn().exists())

    def test_a_swap_cut_off_between_its_renames_is_recovered(self):
        f = module_tarball(self.tmp / 'local.tar.gz', version='dev')
        for shell in self.each_shell():
            r = self.up(shell, f'extra_unpack vpn "{f}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            # cut off after "vpn -> .vpn.old": the next swap (here one that fails) must not throw the old one away
            os.rename(self.vpn(), self.vpn().parent / '.vpn.old')
            r = self.up(shell, f'extra_put vpn "{self.tmp}/nosuch"')
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue((self.vpn() / 'bin/xray').exists(), shell)
            shutil.rmtree(self.vpn())

    def test_unknown_and_usage(self):
        for shell in self.each_shell():
            r = self.ex(shell, 'install', 'nosuch')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('nosuch', r.stderr)
            r = self.ex(shell, 'frobnicate')
            self.assertEqual(r.returncode, 2)
            r = self.ex(shell, 'list')
            self.assertEqual(r.returncode, 0)
            self.assertIn('vpn', r.stdout)
            self.assertIn('vpn-mihomo', r.stdout)
            self.assertIn('github.com/dikeckaan/mu300-linux-vpn', r.stdout)
            self.assertIn('not installed', r.stdout)
            # adopting the engines of an older image is gone: the module carries its own
            r = self.ex(shell, 'adopt', 'vpn')
            self.assertEqual(r.returncode, 1)
            self.assertIn('mu300-extra install vpn', r.stderr)

    def test_link_puts_the_system_commands_on_the_path(self):
        for shell in self.each_shell():
            for p in self.links.iterdir():
                p.unlink()
            # a link left by an extra that is gone, and one of the user's that points somewhere else
            os.symlink(self.disk / 'extra/gone/bin/tool', self.links / 'tool')
            os.symlink('/somewhere/else', self.links / 'mine')
            r = self.ex(shell, 'link')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(os.readlink(self.links / 'mu300-ussd'), str(BIN / 'mu300-ussd'))
            self.assertEqual(os.readlink(self.links / 'mu300-extra'), str(BIN / 'mu300-extra'))
            self.assertFalse(os.path.lexists(self.links / 'tool'))
            self.assertTrue(os.path.lexists(self.links / 'mine'))
            # the images have no mu300-vpn: nothing links it without the module
            self.assertFalse(os.path.lexists(self.links / 'mu300-vpn'))


class UpdateWithTheVpn(ExtrasBase):
    """mu300-update apply and the VPN module: a device that uses the VPN gets the module before anything changes, or
    the update stops; the new systems are linked to it before the reboot; an older vpn extra is replaced"""

    def setUp(self):
        super().setUp()
        self.versions('v2026.10.15')
        self.stage = self.disk / '.mu300-update'
        img = self.tmp / 'img'
        for f, data in (('sbin/init', '#!/bin/sh\n'), ('etc/mu300/image-version', 'v2026.10.16\n')):
            (img / f).parent.mkdir(parents=True, exist_ok=True)
            (img / f).write_text(data)
        (img / 'sbin/init').chmod(0o755)
        self.img = img
        self.stub('df', 'echo "Filesystem 1K-blocks Used Available"; echo "x 1 1 99999999"')

    def put_image(self):
        self.stage.mkdir(exist_ok=True)
        subprocess.run(['tar', '-czf', str(self.stage / 'mu300-openwrt-luci-rootfs.tar.gz'), '-C', str(self.img), '.'],
                       check=True)

    def apply(self, shell, pre='', **env):
        """apply openwrt-luci to v2026.10.16, with the release's files already downloaded (the rootfs staged; no kernel
        bundle) and the boot image left alone"""
        self.put_image()
        code = (pre + 'latest_release() { echo v2026.10.16; }; '
                'fetch_verified() { [ -s "$STAGE/$2" ] && return 0; return 2; }; '
                'boot_update() { return 0; }; is_root() { false; }; '
                'apply openwrt-luci')
        e = dict(MU300_VPN_URL=VPN_URL)
        e.update(env)
        return self.up(shell, code, **e)

    def reset(self, shell, enable=1, extra=None):
        for d in ('openwrt-luci', 'openwrt-luci.old', 'extra', '.mu300-update'):
            shutil.rmtree(self.disk / d, ignore_errors=True)
        (self.disk / 'openwrt-luci/etc/mu300').mkdir(parents=True)
        (self.disk / 'openwrt-luci/sbin').mkdir()
        (self.disk / 'openwrt-luci/etc/mu300/image-version').write_text('v2026.10.15\n')
        (self.disk / 'openwrt-luci/etc/mu300/vpn.conf').write_text(f'ENABLE={enable}\n')
        if extra == 'old':
            self.old_vpn_extra()
        elif extra == 'module':
            f = module_tarball(self.tmp / 'm.tar.gz', version='v0.9.0', requires='mu300-linux v2026.10.01')
            r = self.up(shell, f'module_install vpn "{f}" "{"1" * 64}" v2026.10.15')
            self.assertEqual(r.returncode, 0, r.stderr)
        self.hooks()
        self.urls()

    def test_no_module_to_be_had_refuses_before_anything_changes(self):
        self.offline()
        for shell in self.each_shell():
            for enable, extra in ((1, None), (0, 'old'), (1, 'old')):
                self.reset(shell, enable, extra)
                before = sorted(p.name for p in (self.disk / 'extra').iterdir()) if extra else []
                r = self.apply(shell)
                self.assertNotEqual(r.returncode, 0, (shell, enable, extra, r.stdout))
                self.assertIn('nothing was changed', r.stderr)
                self.assertIn(f'{self.disk}/.mu300-update/vpn', r.stderr)            # how to stage it
                self.assertEqual((self.disk / 'openwrt-luci/etc/mu300/image-version').read_text(), 'v2026.10.15\n')
                self.assertFalse((self.disk / 'openwrt-luci.old').exists())
                if extra:
                    self.assertEqual(sorted(p.name for p in (self.disk / 'extra').iterdir()), before)
                    self.assertFalse((self.vpn() / 'bin/mu300-vpn').exists())
                self.assertEqual(self.hooks(), [])

    def test_a_staged_module_is_installed_and_linked_into_the_new_system(self):
        self.offline()
        for shell in self.each_shell():
            for how in ('stage', 'env-file', 'env-dir'):
                self.reset(shell, 1, 'old')                     # the engines of an older release: replaced
                usb = self.tmp / 'usb'
                shutil.rmtree(usb, ignore_errors=True)
                usb.mkdir()
                env = {}
                if how == 'stage':
                    d = self.disk / '.mu300-update/vpn'
                    d.mkdir(parents=True)
                    module_tarball(d / 'mu300-linux-vpn.tar.gz')
                    sums(d, 'mu300-linux-vpn.tar.gz')
                elif how == 'env-file':
                    env['MU300_VPN_MODULE'] = module_tarball(usb / 'whatever.tar.gz')
                else:
                    module_tarball(usb / 'mu300-linux-vpn.tar.gz')
                    sums(usb, 'mu300-linux-vpn.tar.gz')
                    env['MU300_VPN_MODULE'] = usb
                r = self.apply(shell, **env)
                self.assertEqual(r.returncode, 0, (shell, how, r.stdout, r.stderr))
                self.assertEqual((self.disk / 'openwrt-luci/etc/mu300/image-version').read_text(), 'v2026.10.16\n')
                self.assertEqual((self.vpn() / 'VERSION').read_text().strip(), 'v1.0.0', how)
                self.assertTrue(os.access(self.vpn() / 'bin/mu300-vpn', os.X_OK))
                self.assertIn('extra vpn: v1.0.0\n', r.stdout, how)         # named, not "extra 4" (_n reused)
                self.assertEqual(self.hooks(), [f'link [{self.disk}/openwrt-luci]'], how)
                self.assertTrue(os.path.islink(self.disk / 'openwrt-luci/opt/mu300/bin/mu300-vpn'))
                self.assertNotIn('WARNING', r.stderr)
                self.assertEqual(self.urls(), '', how)                  # offline all along
                self.assertFalse(self.stage.exists())                   # the staged copy is used up

    def test_a_module_that_cannot_be_put_in_place_refuses_before_anything_changes(self):
        # fails late in extra_unpack, after it counted the programs: the loop must still know it is the vpn module
        self.offline()
        for shell in self.each_shell():
            self.reset(shell, 1, 'old')
            d = self.disk / '.mu300-update/vpn'
            d.mkdir(parents=True)
            module_tarball(d / 'mu300-linux-vpn.tar.gz')
            sums(d, 'mu300-linux-vpn.tar.gz')
            r = self.apply(shell, pre='extra_put() { return 1; }; ')
            self.assertNotEqual(r.returncode, 0, (shell, r.stdout))
            self.assertIn('the VPN module could not be installed', r.stderr)
            self.assertIn('nothing was changed', r.stderr)
            self.assertEqual((self.disk / 'openwrt-luci/etc/mu300/image-version').read_text(), 'v2026.10.15\n')
            self.assertFalse((self.disk / 'openwrt-luci.old').exists())
            self.assertEqual(self.hooks(), [])

    def test_online_the_module_comes_from_its_repository(self):
        self.serve_module()
        for shell in self.each_shell():
            self.reset(shell, 1, None)
            r = self.apply(shell)
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertIn(f'{VPN_URL}/mu300-linux-vpn.tar.gz', self.urls())
            self.assertTrue(os.access(self.vpn() / 'bin/mu300-vpn', os.X_OK))
            self.assertEqual(self.hooks(), [f'link [{self.disk}/openwrt-luci]'])

    def test_an_installed_module_follows_its_latest_release_or_stays(self):
        for shell in self.each_shell():
            # online: v0.9.0 -> v1.0.0
            self.serve_module()
            self.reset(shell, 1, 'module')
            r = self.apply(shell)
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertEqual((self.vpn() / 'VERSION').read_text().strip(), 'v1.0.0')
            # offline: the installed one stays and the update goes on
            self.offline()
            self.reset(shell, 1, 'module')
            r = self.apply(shell)
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertIn('the installed one (v0.9.0) stays', r.stderr)
            self.assertEqual((self.vpn() / 'VERSION').read_text().strip(), 'v0.9.0')
            self.assertEqual(self.hooks(), [f'link [{self.disk}/openwrt-luci]'])

    def test_a_module_newer_than_the_release_stops_a_device_without_one(self):
        self.serve_module(requires='mu300-linux v2026.12.01')
        for shell in self.each_shell():
            self.reset(shell, 1, 'old')
            r = self.apply(shell)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('needs mu300-linux v2026.12.01', r.stderr)
            self.assertIn('nothing was changed', r.stderr)
            self.assertEqual((self.disk / 'openwrt-luci/etc/mu300/image-version').read_text(), 'v2026.10.15\n')
            self.assertEqual((self.vpn() / 'release').read_text().strip(), 'v2026.10.15')

    def test_a_module_that_fails_its_checks_stops_a_kill_switch_device_before_any_switch(self):
        # the module can be had but is refused at install (not the module, or one that needs a newer system): with
        # the VPN on and its kill switch on, apply dies with nothing switched, the old extra and system in place
        self.offline()
        for shell in self.each_shell():
            for bad in ('notmodule', 'newer'):
                self.reset(shell, 1, 'old')
                (self.disk / 'openwrt-luci/etc/mu300/vpn.conf').write_text('ENABLE=1\nKILL_SWITCH=1\n')
                f = (extra_tarball(self.tmp / 'bad.tar.gz') if bad == 'notmodule' else
                     module_tarball(self.tmp / 'bad.tar.gz', requires='mu300-linux v2027.01.01'))
                r = self.apply(shell, MU300_VPN_MODULE=f)
                self.assertNotEqual(r.returncode, 0, (bad, r.stdout))
                self.assertIn('nothing was changed', r.stderr, bad)
                self.assertEqual((self.disk / 'openwrt-luci/etc/mu300/image-version').read_text(), 'v2026.10.15\n', bad)
                self.assertFalse((self.disk / 'openwrt-luci.old').exists(), bad)
                self.assertEqual((self.vpn() / 'release').read_text().strip(), 'v2026.10.15', bad)
                self.assertEqual(self.hooks(), [], bad)

    def test_a_kill_switch_system_left_without_mu300_vpn_is_a_loud_warning(self):
        # a hook that links nothing (it failed): the systems are switched already, so the warning says the system
        # keeps its uplinks down (lib/vpn-orphan.sh) until the module is linked
        self.offline()
        for shell in self.each_shell():
            for ks, loud in (('1', True), ('0', False)):
                self.reset(shell, 1, None)
                (self.disk / 'openwrt-luci/etc/mu300/vpn.conf').write_text(f'ENABLE=1\nKILL_SWITCH={ks}\n')
                f = module_tarball(self.tmp / 'm.tar.gz', hooks=False,
                                   extra=[('./hooks/link', 'exit 0\n', 0o755), ('./hooks/unlink', 'exit 0\n', 0o755)])
                r = self.apply(shell, MU300_VPN_MODULE=f)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual('!!! WARNING: openwrt-luci has the VPN on with its kill switch' in r.stderr, loud, ks)
                self.assertIn('WARNING', r.stderr)

    def test_without_the_vpn_nothing_is_fetched(self):
        self.offline()
        for shell in self.each_shell():
            self.reset(shell, 0, None)
            r = self.apply(shell)
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertFalse(self.vpn().exists())
            self.assertEqual(self.urls(), '')

    def test_vpn_mihomo_follows_and_is_never_fatal(self):
        for shell in self.each_shell():
            self.serve_module()
            self.reset(shell, 0, None)
            self.up(shell, f'module_install vpn-mihomo "{mihomo_tarball(self.tmp / "mh.tar.gz", version="v0.9.0")}" x v2026.10.16')
            r = self.apply(shell)
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertEqual((self.disk / 'extra/vpn-mihomo/VERSION').read_text().strip(), 'v1.0.0')
            self.offline()
            self.reset(shell, 0, None)
            self.up(shell, f'module_install vpn-mihomo "{mihomo_tarball(self.tmp / "mh.tar.gz", version="v0.9.0")}" x v2026.10.16')
            r = self.apply(shell)
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertEqual((self.disk / 'extra/vpn-mihomo/VERSION').read_text().strip(), 'v0.9.0')


class Unpack(ExtrasBase):
    """extra_unpack against what an archive can hide: links out of the tree, a manifest that does not match, a bomb,
    the modes of its members, a file swapped between the check and the unpack"""

    def test_a_directory_swapped_for_a_link_writes_nothing_outside(self):
        # ./bin as a link to a directory outside, then a program written "into" it: refused before unpacking, and
        # nothing lands outside
        for shell in self.each_shell():
            out = self.tmp / 'outside'
            out.mkdir(exist_ok=True)
            f = self.tmp / 'swap-vpn.tar.gz'
            with tarfile.open(f, 'w:gz') as t:
                for fn, data, typ, link in (('./VERSION', b'v9\n', tarfile.REGTYPE, ''), ('./bin', b'', tarfile.SYMTYPE, str(out)),
                                            ('./bin/xray', b'x', tarfile.REGTYPE, '')):
                    ti = tarfile.TarInfo(fn)
                    ti.type, ti.linkname, ti.size, ti.mode = typ, link, len(data) if typ == tarfile.REGTYPE else 0, 0o755
                    t.addfile(ti, io.BytesIO(data) if typ == tarfile.REGTYPE else None)
            r = self.up(shell, f'extra_unpack vpn "{f}"; echo "rc=$?"')
            self.assertIn('rc=1', r.stdout, shell)
            self.assertEqual(list(out.iterdir()), [], shell)
            self.assertFalse(self.vpn().exists(), shell)

    def test_a_vpn_extra_with_a_manifest_is_held_to_it(self):
        # a manifest in the VPN module is checked as well; one without is still taken (SHA256SUMS covers the tarball)
        for shell in self.each_shell():
            for manifest, ok in (('good', True), ('bad', False), (None, True)):
                f = self.tmp / 'vpn.tar.gz'
                files = {'./VERSION': b'v1\n', './bin/mu300-vpn': b'#!/bin/sh\n', './hooks/link': b'#!/bin/sh\n',
                         './hooks/unlink': b'#!/bin/sh\n'}
                with tarfile.open(f, 'w:gz') as t:
                    for n, d in list(files.items()) + ([('./manifest', ''.join(
                            f'{hashlib.sha256(d if manifest == "good" else b"x").hexdigest()}  {n}\n'
                            for n, d in sorted(files.items())).encode())] if manifest else []):
                        ti = tarfile.TarInfo(n)
                        ti.size, ti.mode = len(d), 0o755
                        t.addfile(ti, io.BytesIO(d))
                self.up(shell, f'rm -rf "{self.disk}/extra/vpn"')
                r = self.up(shell, f'extra_unpack vpn "{f}"; echo "rc=$?"')
                self.assertIn('rc=0' if ok else 'rc=1', r.stdout, (shell, manifest, r.stderr))
                self.assertEqual(self.vpn().exists(), ok, (shell, manifest))

    def test_a_pack_that_unpacks_too_large_is_refused(self):
        # 700 MB of zeros compress to about 700 kB: refused before anything is unpacked
        f = module_tarball(self.tmp / 'bomb.tar.gz', extra=[('./lib/vpn/blob', bytes(700 << 20), 0o644)])
        self.assertLess(f.stat().st_size, 2 << 20)
        for shell in self.each_shell():
            r = self.up(shell, f'extra_unpack vpn "{f}"; echo "rc=$?"')
            self.assertIn('rc=1', r.stdout, shell)
            self.assertIn('too large', r.stderr)
            self.assertFalse((self.disk / 'extra' / '.vpn.new').exists())

    def test_modes_from_the_archive_are_not_kept(self):
        # a set-id bit or a file anyone may write would be one on a disk mounted without nosuid
        f = module_tarball(self.tmp / 'modes.tar.gz', extra=[('./bin/suid', '#!/bin/sh\n', 0o4777), ('./lib/vpn/open', 'x', 0o666)])
        for shell in self.each_shell():
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            r = self.up(shell, f'extra_unpack vpn "{f}"; echo "rc=$?"')
            self.assertIn('rc=0', r.stdout, (shell, r.stderr))
            for rel, want in (('bin/suid', 0o755), ('lib/vpn/open', 0o644)):
                self.assertEqual(oct((self.vpn() / rel).stat().st_mode & 0o7777), oct(want), (shell, rel))

    def test_the_tarball_is_read_once(self):
        # checked and unpacked from one private copy: a tarball that can be read only once (a FIFO: what a file
        # swapped between the check and the unpacking would look like to a second read) still installs
        import threading
        data = module_tarball(self.tmp / 'm.tar.gz', version='v1.2.3').read_bytes()
        for shell in self.each_shell():
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            fifo = self.tmp / 'once.tar.gz'
            fifo.unlink(missing_ok=True)
            os.mkfifo(fifo)

            def feed():
                with open(fifo, 'wb') as f:
                    f.write(data)
            t = threading.Thread(target=feed, daemon=True)
            t.start()
            r = self.up(shell, f'extra_unpack vpn "{fifo}"; echo "rc=$?"')
            t.join(5)
            self.assertIn('rc=0', r.stdout, (shell, r.stderr))
            self.assertEqual((self.vpn() / 'VERSION').read_text().strip(), 'v1.2.3')
            # and nothing of the copy is left
            self.assertEqual(sorted(p.name for p in (self.disk / 'extra').iterdir()), ['vpn'])


AUDIO_URL = 'http://10.0.0.2:8000/audio-module'
AUDIO_HOOK_LINK = ('echo "audio-link [$1]" >> "$STUBLOG/hooks"\n'
                   '[ -z "$1" ] || { mkdir -p "$1/opt/mu300/bin" && ln -sfn "$MU300_EXTRA_DIR/bin/mu300-audio-dsp" "$1/opt/mu300/bin/mu300-audio-dsp"; }\n'
                   # as the real hook: the firmware the extra fetched into a system that has none
                   'f=$MU300_DISK/extra/.audio-firmware/agdsp.bin\n'
                   '[ -z "$1" ] || [ ! -f "$f" ] || [ -e "$1/lib/firmware/agdsp.bin" ] || '
                   '{ mkdir -p "$1/lib/firmware" && cp "$f" "$1/lib/firmware/agdsp.bin"; }\n')
AUDIO_HOOK_UNLINK = 'echo "audio-unlink [$1]" >> "$STUBLOG/hooks"\n[ -z "$1" ] || rm -f "$1/opt/mu300/bin/mu300-audio-dsp"\n'


def audio_tarball(path, kernel='7.2', version='v1.0.0', requires='mu300-linux v2026.10.19.1', kernel_file=None):
    """mu300-audio-KERNEL.tar.gz as github.com/dikeckaan/mu300-audio publishes it"""
    krel = {'7.1': '7.1.9', '7.2': '7.2.11'}.get(kernel, kernel)
    files = [('./VERSION', version + '\n', 0o644), ('./release', version + '\n', 0o644), ('./name', 'audio\n', 0o644),
             ('./requires', requires + '\n', 0o644),
             ('./kernel', (kernel if kernel_file is None else kernel_file) + '\n', 0o644),
             ('./components', f'mu300-audio {version}\nkernel {kernel}\n', 0o644),
             ('./bin/mu300-audio-dsp', f'#!/bin/sh\necho mu300-audio-dsp {version} "$@"\n', 0o755),
             ('./bin/mu300-voice', '#!/bin/sh\necho mu300-voice\n', 0o755),
             ('./bin/pcmhost', '#!/bin/sh\n', 0o755),
             (f'./modules/{krel}/audio_mem.ko', f'vermagic={krel} SMP\n', 0o644),
             (f'./modules/{krel}/KERNEL', f'KEY={kernel}\nKREL={krel}\n', 0o644),
             ('./service/procd/mu300-audio', '#!/bin/sh /etc/rc.common\n', 0o755),
             ('./hooks/link', AUDIO_HOOK_LINK, 0o755), ('./hooks/unlink', AUDIO_HOOK_UNLINK, 0o755)]
    body = [(fn, data.encode()) for fn, data, _ in files]
    manifest = ''.join(f'{hashlib.sha256(d).hexdigest()}  {fn}\n' for fn, d in sorted(body))
    files.append(('./manifest', manifest, 0o644))
    with tarfile.open(path, 'w:gz') as t:
        for fn, data, mode in files:
            data = data.encode()
            ti = tarfile.TarInfo(fn)
            ti.size, ti.mode = len(data), mode
            t.addfile(ti, io.BytesIO(data))
    return path


def agdsp(path, size=1 << 20, header=b'SharkL5_AUDCP_2022Y_VER_2548'):
    """a stand-in DSP image: the size and the header an AGDSP image has"""
    path.write_bytes(header + b'\0' * (size - len(header)))
    return path


class AudioExtra(ExtrasBase):
    """mu300-extra install|remove audio and mu300-update's audio_*: the module of github.com/dikeckaan/mu300-audio, one
    tarball per kernel series (this one's, 7.2), linked into every system like the VPN module; an update brings it to a
    device that uses call audio"""

    def audio(self):
        return self.disk / 'extra' / 'audio'

    def reset(self, shell):
        self.up(shell, f'rm -rf "{self.disk}/extra" "{self.disk}/.mu300-update"')
        for os_ in ('openwrt-luci',):
            shutil.rmtree(self.disk / os_ / 'lib', ignore_errors=True)
            shutil.rmtree(self.disk / os_ / 'opt', ignore_errors=True)
        self.versions('v2026.10.19.1')
        self.hooks()
        self.urls()

    def serve_audio(self, **kw):
        return self.serve('audio-module', assets={
            f'mu300-audio-{k}.tar.gz': audio_tarball(self.tmp / f'a-{k}.tar.gz', kernel=k, **kw) for k in ('7.1', '7.2')})

    def ax(self, shell, *args, **env):
        e = dict(MU300_AUDIO_URL=AUDIO_URL, MU300_AUDIO_FIRMWARE='none', MU300_ROOT=self.disk / 'openwrt-luci')
        e.update(env)
        return self.ex(shell, *args, **e)

    def test_the_names(self):
        for shell in self.each_shell():
            r = self.up(shell, 'module_asset audio; extra_known audio && extra_is_module audio && echo module')
            self.assertEqual(r.stdout.split(), ['mu300-audio-7.2.tar.gz', 'module'])
            r = self.up(shell, 'for a in mu300-audio-7.1.tar.gz mu300-audio-7.0.tar.gz mu300-audio-.tar.gz mu300-audio-..tar.gz '
                               'mu300-audio-../x.tar.gz mu300-audio-7.1x.tar.gz mu300-audio-.6.tar.gz mu300-audio.tar.gz; do '
                               'module_asset_ok "$a" && echo "ok $a"; done')
            self.assertEqual(r.stdout.split('\n')[:-1], ['ok mu300-audio-7.1.tar.gz', 'ok mu300-audio-7.0.tar.gz'])

    def test_install_online_links_it_into_every_system(self):
        self.serve_audio()
        for shell in self.each_shell():
            self.reset(shell)
            r = self.ax(shell, 'install', 'audio')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((self.audio() / 'kernel').read_text(), '7.2\n')
            self.assertTrue((self.audio() / 'modules/7.2.11/audio_mem.ko').is_file())
            urls = self.urls()
            self.assertIn(f'{AUDIO_URL}/SHA256SUMS', urls)
            self.assertIn(f'{AUDIO_URL}/mu300-audio-7.2.tar.gz', urls)
            self.assertNotIn('mu300-audio-7.1', urls)
            self.assertEqual(self.hooks(), [f'audio-link [{self.root}]'])
            self.assertEqual(os.readlink(self.links / 'mu300-audio-dsp'), str(self.audio() / 'bin/mu300-audio-dsp'))
            self.assertIn('firmware was not fetched', r.stdout)
            st = self.ax(shell, 'status', 'audio')
            self.assertIn('v1.0.0', st.stdout)
            self.assertIn('kernel 7.2', st.stdout)
            lst = self.ax(shell, 'list')
            self.assertIn('installed (v1.0.0), kernel 7.2', lst.stdout)
            # again: the installed one is that release's, nothing is downloaded, the systems are linked again
            r = self.ax(shell, 'install', 'audio')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('latest release already', r.stdout)
            self.assertNotIn('mu300-audio-7.2.tar.gz', self.urls())
            self.assertEqual(len(self.hooks()), 1)

    def test_the_default_place_is_the_audio_repository(self):
        self.serve('download', assets={'mu300-audio-7.2.tar.gz': audio_tarball(self.tmp / 'a.tar.gz')})
        for shell in self.each_shell():
            self.reset(shell)
            r = self.ax(shell, 'install', 'audio', MU300_AUDIO_URL='')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('https://github.com/dikeckaan/mu300-audio/releases/latest/download/mu300-audio-7.2.tar.gz', self.urls())

    def test_only_the_extra_of_the_kernel_in_use(self):
        d = self.serve_audio()
        # what the release calls the 7.2 one is built for 7.1
        (d / 'mu300-audio-7.2.tar.gz').write_bytes(audio_tarball(self.tmp / 'x.tar.gz', kernel='7.1').read_bytes())
        sums(d, 'mu300-audio-7.1.tar.gz', 'mu300-audio-7.2.tar.gz')
        bad = audio_tarball(self.tmp / 'bad.tar.gz', kernel_file='7.2 ; reboot')
        for shell in self.each_shell():
            self.reset(shell)
            r = self.ax(shell, 'install', 'audio')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('for kernel 7.1, and this device uses 7.2', r.stderr)
            self.assertFalse(self.audio().exists())
            self.assertEqual(self.hooks(), [])
            # a ./kernel that is not this series is not the audio extra
            r = self.up(shell, f'module_install audio "{bad}" "{"1" * 64}" v2026.10.19.1')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('is not the audio extra', r.stderr)

    def test_bad_hash_and_offline(self):
        usb = self.tmp / 'usb'
        usb.mkdir()
        audio_tarball(usb / 'mu300-audio-7.2.tar.gz')
        lone = audio_tarball(self.tmp / 'lone.tar.gz', version='v1.0.1')
        for shell in self.each_shell():
            d = self.serve_audio()
            (d / 'mu300-audio-7.2.tar.gz').write_bytes(audio_tarball(self.tmp / 'o.tar.gz', version='v6.6.6').read_bytes())
            self.reset(shell)
            r = self.ax(shell, 'install', 'audio')
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(self.audio().exists())
            self.offline()
            r = self.ax(shell, 'install', 'audio')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('--from DIR', r.stderr)
            self.urls()
            # a directory: only with its SHA256SUMS
            (usb / 'SHA256SUMS').unlink(missing_ok=True)
            r = self.ax(shell, 'install', 'audio', '--from', usb)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('SHA256SUMS', r.stderr)
            sums(usb, 'mu300-audio-7.2.tar.gz')
            r = self.ax(shell, 'install', 'audio', '--from', usb)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((self.audio() / 'VERSION').read_text(), 'v1.0.0\n')
            self.assertEqual(len(self.hooks()), 1)
            # a file alone: refused unless asked for unverified
            r = self.ax(shell, 'install', 'audio', '--from', lone)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('MU300_AUDIO_UNVERIFIED=1', r.stderr)
            r = self.ax(shell, 'install', 'audio', '--from', lone, MU300_AUDIO_UNVERIFIED=1)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((self.audio() / 'VERSION').read_text(), 'v1.0.1\n')
            self.assertEqual(self.urls(), '')                       # offline all along

    def test_the_audio_extra_needs_the_release_its_kernels_are(self):
        f = audio_tarball(self.tmp / 'u' / 'mu300-audio-7.2.tar.gz', requires='mu300-linux v2026.10.19.1') \
            if (self.tmp / 'u').mkdir() is None else None
        sums(self.tmp / 'u', 'mu300-audio-7.2.tar.gz')
        for shell in self.each_shell():
            self.reset(shell)
            self.versions('v2026.10.18.1')
            r = self.ax(shell, 'install', 'audio', '--from', self.tmp / 'u')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('needs mu300-linux v2026.10.19.1', r.stderr)
            self.assertFalse(self.audio().exists())

    def test_firmware_pinned_download_own_file_and_none(self):
        self.offline()
        img = agdsp(self.tmp / 'agdsp.bin')
        pin = hashlib.sha256(img.read_bytes()).hexdigest()
        srv = self.tmp / 'server' / 'v1.0'
        srv.mkdir(parents=True)
        (srv / 'agdsp.bin').write_bytes(img.read_bytes())
        self.serve('audio-module', assets={'mu300-audio-7.2.tar.gz': audio_tarball(self.tmp / 'a.tar.gz')})
        cache = self.disk / 'extra/.audio-firmware/agdsp.bin'
        for shell in self.each_shell():
            self.reset(shell)
            # the download, kept only with the pinned sha256
            r = self.up(shell, f'AUDIO_FW_SHA256={pin}; AUDIO_FW_URL=http://x/v1.0/agdsp.bin; audio_firmware')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(cache.read_bytes(), img.read_bytes())
            self.assertEqual(oct(cache.parent.stat().st_mode & 0o777), '0o700')
            shutil.rmtree(cache.parent)
            r = self.up(shell, f'AUDIO_FW_SHA256={"0" * 64}; AUDIO_FW_URL=http://x/v1.0/agdsp.bin; audio_firmware')
            self.assertIn('not the pinned DSP image', r.stderr)
            self.assertFalse(cache.exists())
            # every system has one: nothing fetched
            for os_ in ('openwrt-luci',):
                (self.disk / os_ / 'lib/firmware').mkdir(parents=True, exist_ok=True)
                (self.disk / os_ / 'lib/firmware/agdsp.bin').write_bytes(b'theirs')
            self.urls()
            r = self.up(shell, f'AUDIO_FW_SHA256={pin}; AUDIO_FW_URL=http://x/v1.0/agdsp.bin; audio_firmware')
            self.assertEqual(self.urls(), '')
            self.assertFalse(cache.exists())
            # your own image: an AGDSP image by its size and header, whatever its hash
            own = agdsp(self.tmp / 'mine.img', size=6 << 20)
            r = self.up(shell, 'audio_firmware', MU300_AUDIO_FIRMWARE=own)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('your own image', r.stdout)
            self.assertEqual(cache.read_bytes(), own.read_bytes())
            for bad in (agdsp(self.tmp / 'big.img', size=(6 << 20) + 1), agdsp(self.tmp / 'nohdr.img', header=b'ELF')):
                shutil.rmtree(cache.parent, ignore_errors=True)
                r = self.up(shell, 'audio_firmware', MU300_AUDIO_FIRMWARE=bad)
                self.assertFalse(cache.exists(), bad)
            # none: nothing at all
            r = self.up(shell, 'audio_firmware', MU300_AUDIO_FIRMWARE='none')
            self.assertIn('not fetched', r.stdout)

    def test_firmware_offline_from_the_directory_goes_into_the_systems(self):
        self.offline()
        usb = self.tmp / 'usb'
        usb.mkdir()
        audio_tarball(usb / 'mu300-audio-7.2.tar.gz')
        sums(usb, 'mu300-audio-7.2.tar.gz')
        agdsp(usb / 'agdsp.bin')
        for shell in self.each_shell():
            self.reset(shell)
            # not the pinned image: the extra is installed, the firmware is not
            r = self.ax(shell, 'install', 'audio', '--from', usb, MU300_AUDIO_FIRMWARE='')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('not the pinned DSP image', r.stderr)
            self.assertFalse((self.disk / 'openwrt-luci/lib/firmware/agdsp.bin').exists())
            self.assertIn('openwrt-luci has no DSP firmware', r.stdout)
            # your own image, brought along: into every system that has none
            r = self.ax(shell, 'install', 'audio', '--from', usb, MU300_AUDIO_FIRMWARE=usb / 'agdsp.bin')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # (the running system's hook gets its root)
            self.assertEqual((self.root / 'lib/firmware/agdsp.bin').read_bytes(), (usb / 'agdsp.bin').read_bytes())
            # remove: the extra, its hooks for every system, and the firmware it kept
            self.hooks()
            r = self.ax(shell, 'remove', 'audio')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(self.hooks(), [f'audio-unlink [{self.root}]'])
            self.assertFalse(self.audio().exists())
            self.assertFalse((self.disk / 'extra/.audio-firmware').exists())
            self.assertFalse((self.disk / 'extra/.audio.sha256').exists())
            self.assertFalse(os.path.lexists(self.links / 'mu300-audio-dsp'))

    def test_in_use(self):
        for shell in self.each_shell():
            self.reset(shell)
            r = self.up(shell, 'audio_in_use "openwrt-luci" && echo yes || echo no')
            self.assertEqual(r.stdout.strip(), 'no')
            # the DSP firmware of mu300-audio-dsp install
            (self.disk / 'openwrt-luci/lib/firmware').mkdir(parents=True)
            (self.disk / 'openwrt-luci/lib/firmware/agdsp.bin').write_bytes(b'x')
            self.assertEqual(self.up(shell, 'audio_in_use "openwrt-luci" && echo yes').stdout.strip(), 'yes')
            shutil.rmtree(self.disk / 'openwrt-luci/lib')


# (this base updates to v2026.10.16: extras that run on it)
OLD = 'mu300-linux v2026.10.01'


class UpdateWithAudio(UpdateWithTheVpn):
    """mu300-update apply and the audio extra: a device that uses call audio (a DSP firmware in a system, or the extra)
    gets the extra of its kernel before the systems are switched, and the new systems are linked to it; without it the
    update goes on and says so loudly at the end"""

    def areset(self, shell, firmware=True, extra=False):
        self.reset(shell, 0, None)
        if firmware:
            (self.disk / 'openwrt-luci/lib/firmware').mkdir(parents=True)
            (self.disk / 'openwrt-luci/lib/firmware/agdsp.bin').write_bytes(b'dsp')
        if extra:
            f = audio_tarball(self.tmp / 'old-audio.tar.gz', version='v0.9.0', requires=OLD)
            r = self.up(shell, f'module_install audio "{f}" "{"1" * 64}" v2026.10.19.1')
            self.assertEqual(r.returncode, 0, r.stderr)
        self.hooks()
        self.urls()

    def serve_audio(self):
        return self.serve('audio-module', assets={'mu300-audio-7.2.tar.gz': audio_tarball(self.tmp / 'a72.tar.gz', requires=OLD)})

    def test_online_the_extra_comes_and_the_new_system_is_linked(self):
        self.serve_audio()
        for shell in self.each_shell():
            for extra in (False, True):
                self.areset(shell, firmware=not extra, extra=extra)
                r = self.apply(shell, MU300_AUDIO_URL=AUDIO_URL)
                self.assertEqual(r.returncode, 0, (shell, extra, r.stdout, r.stderr))
                self.assertIn(f'{AUDIO_URL}/mu300-audio-7.2.tar.gz', self.urls())
                self.assertEqual((self.disk / 'extra/audio/VERSION').read_text(), 'v1.0.0\n')
                self.assertIn('extra audio: v1.0.0 (kernel 7.2)', r.stdout)
                self.assertEqual(self.hooks(), [f'audio-link [{self.disk}/openwrt-luci]'])
                self.assertTrue(os.path.islink(self.disk / 'openwrt-luci/opt/mu300/bin/mu300-audio-dsp'))
                self.assertNotIn('!!! Call audio', r.stderr)

    def test_without_the_extra_the_update_goes_on_and_says_so(self):
        self.offline()
        for shell in self.each_shell():
            self.areset(shell)
            r = self.apply(shell, MU300_AUDIO_URL=AUDIO_URL)
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertEqual((self.disk / 'openwrt-luci/etc/mu300/image-version').read_text(), 'v2026.10.16\n')
            self.assertIn('!!! Call audio', r.stderr)
            self.assertIn('mu300-extra install audio', r.stderr)
            self.assertIn(f'{self.disk}/.mu300-update/audio', r.stderr)
            # an installed extra that cannot be updated stays, quietly
            self.areset(shell, firmware=False, extra=True)
            r = self.apply(shell, MU300_AUDIO_URL=AUDIO_URL)
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertIn('the installed one (v0.9.0) stays', r.stderr)
            self.assertNotIn('!!! Call audio', r.stderr)

    def test_a_staged_copy_is_used_offline(self):
        self.offline()
        for shell in self.each_shell():
            self.areset(shell)
            d = self.disk / '.mu300-update/audio'
            d.mkdir(parents=True)
            audio_tarball(d / 'mu300-audio-7.2.tar.gz', requires=OLD)
            sums(d, 'mu300-audio-7.2.tar.gz')
            r = self.apply(shell, MU300_AUDIO_URL=AUDIO_URL)
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertEqual((self.disk / 'extra/audio/kernel').read_text(), '7.2\n')
            self.assertEqual(self.hooks(), [f'audio-link [{self.disk}/openwrt-luci]'])
            self.assertEqual(self.urls().count(AUDIO_URL), 0)

    def test_a_device_without_call_audio_fetches_nothing(self):
        self.serve_audio()
        for shell in self.each_shell():
            self.areset(shell, firmware=False)
            r = self.apply(shell, MU300_AUDIO_URL=AUDIO_URL)
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertNotIn(AUDIO_URL, self.urls())
            self.assertFalse((self.disk / 'extra/audio').exists())


if __name__ == '__main__':
    unittest.main()


def core_tarball(path, version='v2.0.0'):
    """mu300-linux-vpn-core.tar.gz of module v2: the vpn extra without an engine"""
    return module_tarball(path, version=version, bins=())


def engine_tarball(path, name, bins, version='v2.0.0'):
    """mu300-linux-vpn-ENGINE.tar.gz of module v2: an engine extra, with its hooks"""
    with tarfile.open(path, 'w:gz') as t:
        for fn, data, mode in ([('./VERSION', f'{version}\n', 0o644), ('./name', f'{name}\n', 0o644),
                                ('./components', ''.join(f'{b} 1.0\n' for b in bins), 0o644),
                                ('./requires', 'mu300-linux v2026.10.16\n', 0o644),
                                ('./hooks/link', f'echo "{name} link [$1]" >> "$STUBLOG/hooks"\n', 0o755),
                                ('./hooks/unlink', f'echo "{name} unlink [$1]" >> "$STUBLOG/hooks"\n', 0o755)]
                               + [(f'./bin/{b}', f'#!/bin/sh\necho {b} {version}\n', 0o755) for b in bins]):
            ti = tarfile.TarInfo(fn)
            ti.size, ti.mode = len(data), mode
            t.addfile(ti, io.BytesIO(data.encode()))
    return path


ENGINE_BINS = {'vpn-xray': ('xray', 'hev-socks5-tunnel'), 'vpn-singbox': ('sing-box',), 'vpn-mihomo': ('mihomo',),
               'vpn-dae': ('dae',)}


class EngineExtrasBase(ExtrasBase):
    """Module v2: the VPN's engines as extras of their own (vpn-xray, vpn-singbox, vpn-mihomo, vpn-dae) next to the
    core (vpn). Installing or updating the core brings the engines the device has and its profiles need, all from
    one source, engines first; a v1 device (the engines in the vpn extra) keeps every engine it had; when an engine is
    not to be had the bundle (mu300-linux-vpn.tar.gz, the core with xray and sing-box) is the fallback, and without it
    nothing changes."""

    def setUp(self):
        super().setUp()
        self.versions('v2026.10.20')

    def release(self, engines=('vpn-xray', 'vpn-singbox', 'vpn-mihomo', 'vpn-dae'), bundle=True, core=True):
        assets = {}
        if core:
            assets['mu300-linux-vpn-core.tar.gz'] = core_tarball(self.tmp / 'core.tar.gz')
        if bundle:
            assets['mu300-linux-vpn.tar.gz'] = module_tarball(self.tmp / 'bundle.tar.gz', version='v2.0.0')
        for e in engines:
            assets[f'mu300-linux-{e}.tar.gz'] = engine_tarball(self.tmp / f'{e}.tar.gz', e, ENGINE_BINS[e])
        return assets

    def serve_release(self, **kw):
        return self.serve('vpn-module', assets=self.release(**kw))

    def v1(self):
        """module v1.0.0 installed: the core with xray, hev-socks5-tunnel and sing-box in the vpn extra"""
        f = module_tarball(self.tmp / 'v1.tar.gz', version='v1.0.0')
        r = self.up(self.shells[0], f'module_install vpn "{f}" "{"1" * 64}" v2026.10.20')
        self.assertEqual(r.returncode, 0, r.stderr)

    def profile(self, os_, pid, typ, active=False, enable=None):
        d = self.disk / os_ / 'etc/mu300/vpn'
        (d / 'profiles' / pid).mkdir(parents=True, exist_ok=True)
        (d / 'profiles' / pid / 'meta').write_text(f"NAME='{pid}'\nTYPE='{typ}'\n")
        (d / 'settings').write_text('')
        if active:
            (d / 'active').write_text(pid + '\n')
        if enable is not None:
            (self.disk / os_ / 'etc/mu300/vpn.conf').write_text(f'ENABLE={enable}\n')

    def reset(self):
        for p in ('extra', '.mu300-extra', '.mu300-update'):
            shutil.rmtree(self.disk / p, ignore_errors=True)
        for os_ in ('openwrt-luci',):
            shutil.rmtree(self.disk / os_ / 'etc/mu300/vpn', ignore_errors=True)
            (self.disk / os_ / 'etc/mu300/vpn.conf').unlink(missing_ok=True)
        self.hooks()
        self.urls()

    def have(self):
        """{extra: VERSION} of what is installed"""
        out = {}
        for d in sorted((self.disk / 'extra').glob('*')):
            if (d / 'bin').is_dir():
                out[d.name] = (d / 'VERSION').read_text().strip()
        return out



class EngineExtras(EngineExtrasBase):
    """the engine extras: names, the set an install or an update takes, refusals"""

    def test_the_names(self):
        for shell in self.each_shell():
            r = self.up(shell, 'for x in vpn vpn-bundle vpn-xray vpn-singbox vpn-mihomo vpn-dae; do module_asset $x; done; '
                               'for x in vpn-xray vpn-singbox vpn-dae vpn-mihomo vpn vpn-foo; do '
                               'extra_known $x && echo "known $x"; extra_is_module $x && echo "module $x"; done; true')
            self.assertEqual(r.stdout.splitlines(), [
                'mu300-linux-vpn-core.tar.gz', 'mu300-linux-vpn.tar.gz', 'mu300-linux-vpn-xray.tar.gz',
                'mu300-linux-vpn-singbox.tar.gz', 'mu300-linux-vpn-mihomo.tar.gz', 'mu300-linux-vpn-dae.tar.gz',
                'known vpn-xray', 'module vpn-xray', 'known vpn-singbox', 'module vpn-singbox', 'known vpn-dae',
                'module vpn-dae', 'known vpn-mihomo', 'module vpn-mihomo', 'known vpn', 'module vpn'])
            r = self.ex(shell, 'install', 'vpn-foo')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("no extra called 'vpn-foo'", r.stderr)

    def test_engines_wanted(self):
        for shell in self.each_shell():
            self.reset()
            r = self.up(shell, 'vpn_engines_wanted | tr "\\n" " "')
            self.assertEqual(r.stdout.split(), ['vpn-xray'])          # a first install: the default
            r = self.up(shell, 'vpn_engines_wanted | tr "\\n" " "', MU300_VPN_ENGINES='dae,sing-box')
            self.assertEqual(r.stdout.split(), ['vpn-dae', 'vpn-singbox'])
            r = self.up(shell, 'vpn_engines_wanted | wc -l', MU300_VPN_ENGINES='none')
            self.assertEqual(r.stdout.strip(), '0')
            # v1: both its engines, and every profile's (in any system)
            self.v1()
            self.profile('openwrt-luci', 'clash', 'mihomo')
            self.profile('openwrt-luci', 'wg', 'wireguard')
            self.profile('openwrt-luci', 'd', 'dae')
            r = self.up(shell, 'vpn_engines_wanted | tr "\\n" " "')
            self.assertEqual(r.stdout.split(), ['vpn-dae', 'vpn-mihomo', 'vpn-singbox', 'vpn-xray'])
            # a vpn.conf never migrated, on a core without engines
            self.reset()
            (self.disk / 'extra/vpn/bin').mkdir(parents=True)
            (self.disk / 'openwrt-luci/etc/mu300/vpn.conf').write_text("ENABLE=1\nVLESS_URI='vless://x@h:1'\n")
            r = self.up(shell, 'vpn_engines_wanted | tr "\\n" " "')
            self.assertEqual(r.stdout.split(), ['vpn-singbox', 'vpn-xray'])

    def test_v1_upgrades_to_the_core_and_keeps_its_engines(self):
        for shell in self.each_shell():
            self.reset()
            self.v1()
            self.profile('openwrt-luci', 'home', 'xray', active=True, enable=1)
            self.profile('openwrt-luci', 'clash', 'mihomo')
            self.serve_release()
            r = self.ex(shell, 'install', 'vpn', MU300_VPN_URL=VPN_URL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.have(), {'vpn': 'v2.0.0', 'vpn-mihomo': 'v2.0.0', 'vpn-singbox': 'v2.0.0',
                                           'vpn-xray': 'v2.0.0'})
            # the core is the core now: no engine in it
            self.assertFalse((self.vpn() / 'bin/xray').exists())
            self.assertTrue((self.disk / 'extra/vpn-xray/bin/xray').exists())
            # the engines were linked before the core
            h = self.hooks()
            self.assertLess(max(i for i, l in enumerate(h) if l.startswith('vpn-')),
                            min(i for i, l in enumerate(h) if l.startswith('link [')), h)
            self.urls()
            # again: everything is up to date, nothing downloaded but SHA256SUMS
            r = self.ex(shell, 'install', 'vpn', MU300_VPN_URL=VPN_URL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn('.tar.gz', self.urls())

    def test_an_engine_not_to_be_had_takes_the_bundle_or_changes_nothing(self):
        for shell in self.each_shell():
            # the release has the core but not vpn-singbox: v1's sing-box must not be lost - the bundle instead
            self.reset()
            self.v1()
            self.serve_release(engines=('vpn-xray',))
            r = self.ex(shell, 'install', 'vpn', MU300_VPN_URL=VPN_URL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('taking mu300-linux-vpn.tar.gz', r.stderr)
            self.assertEqual(self.have(), {'vpn': 'v2.0.0'})
            self.assertTrue((self.vpn() / 'bin/sing-box').exists())
            # without the bundle either: refused, v1 untouched
            self.reset()
            self.v1()
            self.serve_release(engines=('vpn-xray',), bundle=False)
            r = self.ex(shell, 'install', 'vpn', MU300_VPN_URL=VPN_URL)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('nothing was changed', r.stderr)
            self.assertEqual(self.have(), {'vpn': 'v1.0.0'})
            self.assertTrue((self.vpn() / 'bin/sing-box').exists())
            # a profile's engine that was never there is no reason to refuse: it is said and skipped
            self.reset()
            self.profile('openwrt-luci', 'd', 'dae')
            self.serve_release(engines=('vpn-xray',), bundle=False)
            r = self.ex(shell, 'install', 'vpn', MU300_VPN_URL=VPN_URL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('extra vpn-dae: a profile runs on it and it is not to be had', r.stderr)
            self.assertEqual(self.have(), {'vpn': 'v2.0.0', 'vpn-xray': 'v2.0.0'})

    def test_a_pinned_v1_release_is_the_bundle(self):
        for shell in self.each_shell():
            self.reset()
            self.serve('vpn-module', assets={'mu300-linux-vpn.tar.gz': module_tarball(self.tmp / 'b.tar.gz')})
            r = self.ex(shell, 'install', 'vpn', MU300_VPN_URL=VPN_URL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.have(), {'vpn': 'v1.0.0'})
            self.assertTrue((self.vpn() / 'bin/xray').exists())

    def test_offline_from_a_directory(self):
        for shell in self.each_shell():
            self.reset()
            self.v1()
            self.offline()
            d = self.tmp / 'usb'
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir()
            for name, src in self.release(engines=('vpn-xray',), bundle=False).items():
                (d / name).write_bytes(src.read_bytes())
            sums(d, *[p.name for p in d.iterdir()])
            # vpn-singbox is not there, v1 has sing-box: refused, nothing changed
            r = self.ex(shell, 'install', 'vpn', '--from', d)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.have(), {'vpn': 'v1.0.0'})
            (d / 'mu300-linux-vpn-singbox.tar.gz').write_bytes(
                engine_tarball(self.tmp / 's.tar.gz', 'vpn-singbox', ('sing-box',)).read_bytes())
            sums(d, *[p.name for p in d.iterdir() if p.name != 'SHA256SUMS'])
            r = self.ex(shell, 'install', 'vpn', '--from', d)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.have(), {'vpn': 'v2.0.0', 'vpn-singbox': 'v2.0.0', 'vpn-xray': 'v2.0.0'})
            # one engine on its own, from the same directory
            r = self.ex(shell, 'install', 'vpn-xray', '--from', d)
            self.assertEqual(r.returncode, 0, r.stderr)
            # a tampered engine is refused
            (d / 'mu300-linux-vpn-xray.tar.gz').write_bytes(b'x')
            r = self.ex(shell, 'install', 'vpn-xray', '--from', d)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('does not match its SHA256SUMS', r.stderr)

    def test_vpn_dae_is_refused_before_the_download_on_a_kernel_that_cannot_run_it(self):
        for shell in self.each_shell():
            self.reset()
            self.serve_release()
            for krel, btf, why in (('7.2.11', self.tmp / 'no-btf', 'has none'),):
                r = self.ex(shell, 'install', 'vpn-dae', MU300_VPN_URL=VPN_URL, MU300_KREL=krel, MU300_BTF=btf)
                self.assertEqual(r.returncode, 1)
                self.assertIn(why, r.stderr)
                self.assertIn('vpn-dae was not installed', r.stderr)
                self.assertEqual(self.urls(), '')                      # nothing was downloaded
                self.assertFalse((self.disk / 'extra/vpn-dae').exists())
            r = self.ex(shell, 'install', 'vpn-dae', MU300_VPN_URL=VPN_URL, MU300_BTF=self.tmp / 'no-btf',
                        MU300_DAE_ANY_KERNEL='1')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((self.disk / 'extra/vpn-dae/bin/dae').exists())

    def test_a_download_the_kill_switch_drops_says_so(self):
        """uclient-fetch's "Operation not permitted" is any connection it could not make; behind the VPN's kill switch
        with the tunnel down the line that ends the install names the kill switch, not the modem"""
        for shell in self.each_shell():
            self.reset()
            self.offline()
            r = self.ex(shell, 'install', 'vpn-dae', MU300_VPN_URL=VPN_URL)
            self.assertEqual(r.returncode, 1)
            self.assertIn('is the device online?', r.stderr)
            self.assertNotIn('kill switch', r.stderr)
            # the kill switch's table is there, and this device's own traffic leaves on the uplink (or has no route)
            self.stub('nft', '[ "$*" = "list table inet mu300_vpn" ]')
            for route in ('1.1.1.1 via 10.64.0.1 dev sipa_eth0 src 10.64.0.2 uid 0', '',
                          '1.1.1.1 via 192.168.1.1 dev wlan0 src 192.168.1.5 uid 0'):
                r = self.ex(shell, 'install', 'vpn-dae', MU300_VPN_URL=VPN_URL, MU300_KS_ROUTE=route)
                self.assertEqual(r.returncode, 1)
                self.assertIn('blocked by the VPN kill switch', r.stderr)
                self.assertIn('--from DIR', r.stderr)
                self.assertNotIn('is the device online?', r.stderr)
            # one try, not four: the kill switch drops the next ones the same way
            self.urls()
            self.ex(shell, 'install', 'vpn-dae', MU300_VPN_URL=VPN_URL, MU300_KS_ROUTE='')
            self.assertEqual(self.urls().count('SHA256SUMS'), 1)
            # mu300-vpn's own download window (its engine fetch) lets the release hosts out: not the kill switch
            self.stub('nft', '[ "$*" = "list table inet mu300_vpn" ] && echo "set fetch4 {"')
            r = self.ex(shell, 'install', 'vpn-dae', MU300_VPN_URL=VPN_URL, MU300_KS_ROUTE='')
            self.assertIn('is the device online?', r.stderr)
            self.assertEqual(self.urls().count('SHA256SUMS'), 4)
            self.stub('nft', '[ "$*" = "list table inet mu300_vpn" ]')
            # into a tunnel: then it is not the kill switch
            r = self.ex(shell, 'install', 'vpn-dae', MU300_VPN_URL=VPN_URL,
                        MU300_KS_ROUTE='1.1.1.1 via 172.19.0.2 dev sbtun table 2022 src 172.19.0.1 uid 0')
            self.assertIn('is the device online?', r.stderr)
            # mu300-update's own lines
            r = self.up(shell, 'offline_why', MU300_KS_ROUTE='1.1.1.1 dev sipa_eth0 src 10.0.0.2')
            self.assertIn('blocked by the VPN kill switch', r.stdout)
            (self.stubs / 'nft').unlink()
            r = self.up(shell, 'offline_why', MU300_KS_ROUTE='1.1.1.1 dev sipa_eth0 src 10.0.0.2')
            self.assertEqual(r.stdout.strip(), 'is the device online?')

    def test_remove_an_engine_the_vpn_runs_on(self):
        for shell in self.each_shell():
            self.reset()
            self.serve_release()
            self.assertEqual(self.ex(shell, 'install', 'vpn-dae', MU300_VPN_URL=VPN_URL).returncode, 0)
            self.assertEqual(self.ex(shell, 'install', 'vpn-xray', MU300_VPN_URL=VPN_URL).returncode, 0)
            self.profile('openwrt-luci', 'd', 'dae', active=True, enable=1)
            r = self.ex(shell, 'remove', 'vpn-dae')
            self.assertEqual(r.returncode, 1)
            self.assertIn('runs on vpn-dae', r.stderr)
            self.assertTrue((self.disk / 'extra/vpn-dae/bin/dae').exists())
            # an xray profile may run on sing-box: vpn-singbox is in use too; and the engine the service recorded counts
            self.assertEqual(self.ex(shell, 'install', 'vpn-singbox', MU300_VPN_URL=VPN_URL).returncode, 0)
            self.profile('openwrt-luci', 'x', 'xray', active=True, enable=1)
            xdir = self.disk / 'openwrt-luci/etc/mu300/vpn'
            # (only a VLESS link behind the kill switch runs on sing-box, as mu300-vpn's engine_in_use has it)
            (xdir / 'profiles/x/uri').write_text('vmess://e30=\n')
            r = self.ex(shell, 'remove', 'vpn-singbox')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.ex(shell, 'install', 'vpn-singbox', MU300_VPN_URL=VPN_URL).returncode, 0)
            (xdir / 'profiles/x/uri').write_text('vless://id@example.net:443\n')
            (xdir / 'settings').write_text('KILL_SWITCH=0\n')
            r = self.ex(shell, 'remove', 'vpn-singbox')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.ex(shell, 'install', 'vpn-singbox', MU300_VPN_URL=VPN_URL).returncode, 0)
            (xdir / 'settings').write_text('KILL_SWITCH=1\n')
            self.assertEqual(self.ex(shell, 'remove', 'vpn-singbox').returncode, 1)
            self.profile('openwrt-luci', 'd', 'dae', active=True, enable=1)
            (self.root / 'run/mu300-vpn').mkdir(parents=True, exist_ok=True)
            (self.root / 'run/mu300-vpn/engine').write_text('sing-box\n')
            self.assertEqual(self.ex(shell, 'remove', 'vpn-singbox').returncode, 1)
            (self.root / 'run/mu300-vpn/engine').unlink()
            self.assertEqual(self.ex(shell, 'remove', 'vpn-singbox').returncode, 0)
            # another engine goes; with --force this one too
            self.assertEqual(self.ex(shell, 'remove', 'vpn-xray').returncode, 0)
            self.assertEqual(self.ex(shell, 'remove', 'vpn-dae', '--force').returncode, 0)
            self.assertEqual(self.have(), {})

    def test_an_update_brings_the_engines_along(self):
        """mu300-update's module step on a v1 device whose VPN is on: the core and its engines, from the staging
        directory, the engines first"""
        for shell in self.each_shell():
            self.reset()
            self.v1()
            self.profile('openwrt-luci', 'home', 'xray', active=True, enable=1)
            self.offline()
            staged = self.disk / '.mu300-update/vpn'
            staged.mkdir(parents=True)
            for name, src in self.release(engines=('vpn-xray', 'vpn-singbox'), bundle=False).items():
                (staged / name).write_bytes(src.read_bytes())
            sums(staged, *[p.name for p in staged.iterdir()])
            r = self.up(shell, 'modules_fetch v2026.10.21 openwrt-luci; printf "%s\\n" "$MODULES_NEW" | cut -d" " -f1')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.split(), ['vpn-singbox', 'vpn-xray', 'vpn'])
            r = self.up(shell, 'modules_fetch v2026.10.21 openwrt-luci && modules_install v2026.10.21')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.have(), {'vpn': 'v2.0.0', 'vpn-singbox': 'v2.0.0', 'vpn-xray': 'v2.0.0'})

    def test_an_update_offline_without_the_engines_stops(self):
        for shell in self.each_shell():
            self.reset()
            # no module installed, the VPN on in the system being replaced: the same refusal as for the core
            self.profile('openwrt-luci', 'home', 'xray', active=True, enable=1)
            self.offline()
            r = self.up(shell, 'modules_fetch v2026.10.21 openwrt-luci')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('nothing was changed', r.stderr)
            self.assertIn('mu300-linux-vpn-core.tar.gz', r.stderr)
            self.assertEqual(self.have(), {})


class ModuleIntegrity(EngineExtrasBase):
    """What is checked is what is installed: the tarball is copied into a private directory and the copy is hashed
    and unpacked; links and names that are not the module's are refused."""

    def usb(self, engines=('vpn-xray',)):
        d = self.tmp / 'usb'
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
        for name, src in self.release(engines=engines, bundle=False).items():
            (d / name).write_bytes(src.read_bytes())
        sums(d, *[p.name for p in d.iterdir()])
        return d

    def test_a_tarball_swapped_after_the_check_is_not_what_is_installed(self):
        for shell in self.each_shell():
            self.reset()
            d = self.usb()
            evil = engine_tarball(self.tmp / 'evil.tar.gz', 'vpn-xray', ('xray', 'hev-socks5-tunnel'), version='v6.6.6')
            # verified, then the source is replaced before the install (another process writing the directory)
            r = self.up(shell, f'module_get vpn-xray "$STAGE" "{d}" && cp "{evil}" "{d}/mu300-linux-vpn-xray.tar.gz" && '
                               'case $MODULE_FILE in "$EXTRA"/.verified.*) ;; *) echo "NOT A PRIVATE COPY"; exit 1 ;; esac && '
                               'module_install vpn-xray "$MODULE_FILE" "$MODULE_SHA" v2026.10.20 && module_vdir_clean')
            self.assertEqual(r.returncode, 0, (r.stdout, r.stderr))
            self.assertEqual(self.have(), {'vpn-xray': 'v2.0.0'})
            self.assertEqual([p.name for p in (self.disk / 'extra').glob('.verified.*')], [])

    def test_the_private_copy_is_roots_alone(self):
        for shell in self.each_shell():
            self.reset()
            d = self.usb()
            r = self.up(shell, f'module_get vpn-xray "$STAGE" "{d}" && ls -ld "$MODULE_VDIR" && ls -l "$MODULE_FILE"')
            self.assertEqual(r.returncode, 0, r.stderr)
            dirmode, filemode = r.stdout.split('\n')[0][:10], r.stdout.split('\n')[1][:10]
            self.assertEqual(dirmode, 'drwx------')
            self.assertEqual(filemode, '-rw-------')

    def test_a_symlinked_tarball_or_sums_is_refused(self):
        for shell in self.each_shell():
            self.reset()
            d = self.usb()
            real = self.tmp / 'real.tar.gz'
            shutil.move(d / 'mu300-linux-vpn-xray.tar.gz', real)
            (d / 'mu300-linux-vpn-xray.tar.gz').symlink_to(real)
            r = self.ex(shell, 'install', 'vpn-xray', '--from', d)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('not a regular file', r.stderr)
            self.assertEqual(self.have(), {})
            d = self.usb()
            shutil.move(d / 'SHA256SUMS', self.tmp / 'sums')
            (d / 'SHA256SUMS').symlink_to(self.tmp / 'sums')
            r = self.ex(shell, 'install', 'vpn-xray', '--from', d)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(self.have(), {})

    def test_names_that_are_not_the_modules(self):
        for shell in self.each_shell():
            r = self.up(shell, 'for n in mu300-linux-vpn.tar.gz mu300-linux-vpn-xray.tar.gz ../mu300-linux-vpn.tar.gz '
                               'mu300-linux-vpn-../x.tar.gz -x.tar.gz mu300-linux-vpn-X.tar.gz "mu300-linux-vpn- a.tar.gz" '
                               'mu300-linux-vpn.tar.gz.sh; do module_asset_ok "$n" && echo "ok $n" || echo "no $n"; done')
            self.assertEqual(r.stdout.splitlines(), [
                'ok mu300-linux-vpn.tar.gz', 'ok mu300-linux-vpn-xray.tar.gz', 'no ../mu300-linux-vpn.tar.gz',
                'no mu300-linux-vpn-../x.tar.gz', 'no -x.tar.gz', 'no mu300-linux-vpn-X.tar.gz',
                'no mu300-linux-vpn- a.tar.gz', 'no mu300-linux-vpn.tar.gz.sh'])
            # an asset name from elsewhere never becomes a path
            self.reset()
            d = self.usb()
            (self.tmp / 'x.tar.gz').write_bytes((d / 'mu300-linux-vpn-xray.tar.gz').read_bytes())
            r = self.up(shell, f'module_from "{d}" vpn-xray ../x.tar.gz')
            self.assertEqual(r.returncode, 2)
            self.assertIn('not a file name of a module', r.stderr)

    def test_traversal_names_in_sha256sums_are_never_looked_up(self):
        for shell in self.each_shell():
            self.reset()
            d = self.usb()
            # SHA256SUMS lists a traversal name with the hash of the real file and no line for the asset itself
            h = hashlib.sha256((d / 'mu300-linux-vpn-xray.tar.gz').read_bytes()).hexdigest()
            (d / 'SHA256SUMS').write_text(f'{h}  ../mu300-linux-vpn-xray.tar.gz\n{h}  -mu300-linux-vpn-xray.tar.gz\n')
            r = self.ex(shell, 'install', 'vpn-xray', '--from', d)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('does not list mu300-linux-vpn-xray.tar.gz', r.stderr)
            self.assertEqual(self.have(), {})

    def test_an_engine_not_in_the_allow_list(self):
        for shell in self.each_shell():
            self.reset()
            for name in ('vpn-evil', '../vpn-xray', 'vpn-xray/..', '-vpn-xray', 'vpn-xray;id'):
                r = self.ex(shell, 'install', name)
                self.assertNotEqual(r.returncode, 0, name)
            r = self.up(shell, 'vpn_engines_wanted | tr "\\n" " "', MU300_VPN_ENGINES='../x,vpn-evil,-y,xray')
            self.assertEqual(r.stdout.split(), ['vpn-xray'])
            # a profile type that is no engine's adds nothing
            self.profile('openwrt-luci', 'p', '../../evil')
            r = self.up(shell, 'vpn_engines_wanted | tr "\\n" " "', MU300_VPN_ENGINES='none')
            self.assertEqual(r.stdout.split(), [])

    def test_verification_that_cannot_be_done_refuses(self):
        """fail closed: no SHA256SUMS, no line for the asset, an empty or broken hash, a hash tool that fails or lies,
        a file that does not match - none of them installs anything, from a directory, a download or the update's
        staging directory"""
        for shell in self.each_shell():
            for case in ('no-sums', 'no-entry', 'empty-hash', 'short-hash', 'mismatch', 'tool-fails', 'tool-empty'):
                self.reset()
                self.stubs.joinpath('sha256sum').unlink(missing_ok=True)
                d = self.usb()
                h = hashlib.sha256((d / 'mu300-linux-vpn-xray.tar.gz').read_bytes()).hexdigest()
                lines = {'no-sums': None, 'no-entry': f'{h}  mu300-linux-vpn-dae.tar.gz\n',
                         'empty-hash': '  mu300-linux-vpn-xray.tar.gz\n',
                         'short-hash': f'{h[:63]}  mu300-linux-vpn-xray.tar.gz\n',
                         'mismatch': f'{"0" * 64}  mu300-linux-vpn-xray.tar.gz\n'}.get(
                    case, f'{h}  mu300-linux-vpn-xray.tar.gz\n')
                if lines is None:
                    (d / 'SHA256SUMS').unlink()
                else:
                    (d / 'SHA256SUMS').write_text(lines)
                if case == 'tool-fails':
                    self.stub('sha256sum', 'exit 1')
                elif case == 'tool-empty':
                    self.stub('sha256sum', 'echo "  $1"')
                r = self.ex(shell, 'install', 'vpn-xray', '--from', d)
                self.assertNotEqual(r.returncode, 0, case)
                self.assertEqual(self.have(), {}, case)
                # the same served as a release: the download refuses too
                assets = {'mu300-linux-vpn-xray.tar.gz': d / 'mu300-linux-vpn-xray.tar.gz'}
                srv = self.serve('vpn-module', assets=assets)
                if lines is None:
                    (srv / 'SHA256SUMS').unlink()
                else:
                    (srv / 'SHA256SUMS').write_text(lines)
                r = self.ex(shell, 'install', 'vpn-xray', MU300_VPN_URL=VPN_URL)
                self.assertNotEqual(r.returncode, 0, case)
                self.assertEqual(self.have(), {}, case)
                # an update's staged copy, for an installed engine: it stays as it is
                self.offline()
                staged = self.disk / '.mu300-update/vpn'
                staged.mkdir(parents=True, exist_ok=True)
                for p in d.iterdir():
                    shutil.copy(p, staged / p.name)
                (self.disk / 'extra/vpn-xray/bin').mkdir(parents=True, exist_ok=True)
                (self.disk / 'extra/vpn-xray/VERSION').write_text('v1.0.0\n')
                self.up(shell, 'modules_fetch v2026.10.21 ""; modules_install v2026.10.21', MU300_VPN_URL=VPN_URL)
                self.assertEqual((self.disk / 'extra/vpn-xray/VERSION').read_text().strip(), 'v1.0.0', case)
            self.stubs.joinpath('sha256sum').unlink(missing_ok=True)


def own_tarball(path, name='mytool', release='1.2.3', manifest=True, hooks=True, extra=(), bad_hash=False):
    """an extra of the user's own: ./name ./release ./manifest, programs in ./bin, hooks/ and files of its own"""
    files = [('./name', name + '\n', 0o644), ('./release', release + '\n', 0o644),
             ('./bin/mytool', '#!/bin/sh\necho mytool\n', 0o755),
             ('./lib/mytool/web/index.html', '<p>x</p>\n', 0o644)]
    if hooks:
        files += [('./hooks/link', 'echo "own link [$1] $MU300_EXTRA_DIR" >> "$STUBLOG/hooks"\n', 0o755),
                  ('./hooks/unlink', 'echo "own unlink [$1]" >> "$STUBLOG/hooks"\n', 0o755)]
    files += list(extra)
    if manifest:
        lines = ''.join(f'{hashlib.sha256(d.encode()).hexdigest() if not bad_hash else "0" * 64}  {fn}\n'
                        for fn, d, _ in files)
        files.append(('./manifest', lines, 0o644))
    with tarfile.open(path, 'w:gz') as t:
        for fn, data, mode in files:
            data = data.encode()
            ti = tarfile.TarInfo(fn)
            ti.size, ti.mode = len(data), mode
            t.addfile(ti, io.BytesIO(data))
    return path


class OwnExtraTest(ExtrasBase):
    """an extra of the user's own: installed from a file by its name, hooked into every system, never listed"""

    def own(self):
        return self.disk / 'extra' / 'mytool'

    def test_install_from_a_file_links_status_by_name_only_and_remove(self):
        f = own_tarball(self.tmp / 'mytool.tar.gz')
        for shell in self.each_shell():
            self.versions('v2026.10.17')
            self.hooks()
            r = self.ex(shell, 'install', 'mytool', '--from', f)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('mytool 1.2.3 installed', r.stdout)
            self.assertTrue((self.own() / 'lib/mytool/web/index.html').exists())
            self.assertTrue(os.path.islink(self.links / 'mytool'))
            hooks = self.hooks()
            self.assertIn(f'own link [{self.disk}/openwrt-luci] {self.own()}', hooks)
            # not in the list, not in the status of all extras, not in mu300-update's version line
            r = self.ex(shell, 'list')
            self.assertNotIn('mytool', r.stdout)
            r = self.ex(shell, 'status')
            self.assertNotIn('mytool', r.stdout)
            r = self.up(shell, 'version', MU300_LIB=1)
            self.assertNotIn('mytool', r.stdout)
            # by name it is there
            r = self.ex(shell, 'status', 'mytool')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('1.2.3', r.stdout)
            # every boot: its hook for the running system
            r = self.ex(shell, 'link')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.hooks(), [f'own link [{self.root}] {self.own()}'])
            r = self.ex(shell, 'remove', 'mytool')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(self.own().exists())
            self.assertFalse(os.path.lexists(self.links / 'mytool'))
            self.assertIn(f'own unlink [{self.disk}/openwrt-luci]', self.hooks())

    def test_without_a_file_the_name_is_unknown(self):
        for shell in self.each_shell():
            r = self.ex(shell, 'install', 'mytool')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("no extra called 'mytool'", r.stderr)
            r = self.ex(shell, 'remove', 'mytool')
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(self.own().exists())

    def test_refused_without_a_manifest_with_a_wrong_one_or_odd_names(self):
        cases = {
            'no manifest': own_tarball(self.tmp / 'a.tar.gz', manifest=False),
            'bad hash': own_tarball(self.tmp / 'b.tar.gz', bad_hash=True),
            'other name': own_tarball(self.tmp / 'c.tar.gz', name='other'),
            'odd name': own_tarball(self.tmp / 'd.tar.gz', extra=[('./lib/a b', 'x', 0o644)]),
            'too deep': own_tarball(self.tmp / 'e.tar.gz', extra=[('./a/b/c/d/e', 'x', 0o644)]),
        }
        for shell in self.each_shell():
            for case, f in cases.items():
                r = self.ex(shell, 'install', 'mytool', '--from', f)
                self.assertNotEqual(r.returncode, 0, case)
                self.assertFalse(self.own().exists(), case)
            # a name of a release extra, or one that is not a name, is never an own one
            for name in ('vpn-foo', 'My', '-x', 'a/b'):
                r = self.ex(shell, 'install', name, '--from', cases['no manifest'])
                self.assertNotEqual(r.returncode, 0, name)

    def test_from_a_directory_needs_its_sha256sums(self):
        d = self.tmp / 'stick'
        d.mkdir()
        own_tarball(d / 'mytool.tar.gz')
        for shell in self.each_shell():
            (d / 'SHA256SUMS').unlink(missing_ok=True)
            r = self.ex(shell, 'install', 'mytool', '--from', d)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('not installed unverified', r.stderr)
            sums(d, 'mytool.tar.gz')
            r = self.ex(shell, 'install', 'mytool', '--from', d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # a file next to a SHA256SUMS that lists it with another hash is refused
            (d / 'SHA256SUMS').write_text('0' * 64 + '  mytool.tar.gz\n')
            r = self.ex(shell, 'install', 'mytool', '--from', d / 'mytool.tar.gz')
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('does not match', r.stderr)
            self.assertTrue(self.own().exists())
            shutil.rmtree(self.own())
