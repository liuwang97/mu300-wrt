"""Extras: optional components (the VPN engines) that are not part of the images and live on the Linux partition
(MU300_DISK/extra/<name>). mu300-update installs and updates them (sourced with MU300_LIB=1 against a fake partition),
mu300-extra is the command, and both systems' boot runs `mu300-extra link`."""
import hashlib
import io
import os
import tarfile
import unittest

from helpers import BIN, ShellTest

ENGINES = ('xray', 'hev-socks5-tunnel', 'sing-box')


def extra_tarball(path, name='vpn', release='v2026.10.10', bins=ENGINES, components='xray 26.3.27\n'):
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


class Extras(ShellTest):
    def setUp(self):
        super().setUp()
        self.disk = self.tmp / 'disk'
        for os_ in ('ubuntu', 'openwrt'):
            (self.disk / os_ / 'etc/mu300').mkdir(parents=True)
        self.root = self.tmp / 'root'
        (self.root / 'run/mu300').mkdir(parents=True)
        (self.root / 'run/mu300/device').write_text('f50\n')
        self.links = self.tmp / 'links'
        self.links.mkdir()
        self.stub('id', 'echo 0')

    def up(self, shell, code, **env):
        return self.sh(shell, f'. "{BIN}/mu300-update"; {code}', MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN,
                       MU300_SYSROOT=self.root, **env)

    def ex(self, shell, *args, **env):
        e = dict(MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root, MU300_LINKDIR=self.links,
                 MU300_OPT=BIN.parent, MU300_FETCH_DELAY=0)
        e.update(env)
        return self.script(shell, BIN / 'mu300-extra', *args, **e)

    def baked(self, os_, enabled):
        """an installed system from before extras: engines in its image, and vpn.conf ENABLE=1 or 0"""
        b = self.disk / os_ / 'opt/mu300/bin'
        b.mkdir(parents=True, exist_ok=True)
        for e in ENGINES:
            (b / e).write_text(f'#!/bin/sh\necho baked {e}\n')
            (b / e).chmod(0o755)
        (self.disk / os_ / 'etc/mu300/vpn.conf').write_text(f'ENABLE={enabled}\nVLESS_URI="vless://x@y"\n')
        (self.disk / os_ / 'etc/mu300/image-version').write_text('v2026.10.06\n')

    def vpn(self):
        return self.disk / 'extra' / 'vpn'

    # ---- mu300-update: the core -----------------------------------------------------------------------------
    def test_unpack_installs_and_replaces(self):
        for shell in self.each_shell():
            old = extra_tarball(self.tmp / 'old.tar.gz', release='v2026.10.01')
            new = extra_tarball(self.tmp / 'new.tar.gz', release='v2026.10.10')
            r = self.up(shell, f'extra_unpack vpn "{old}" && extra_release vpn && extra_installed')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.split(), ['v2026.10.01', 'vpn'])
            r = self.up(shell, f'extra_unpack vpn "{new}" && extra_release vpn')
            self.assertEqual(r.stdout.split(), ['v2026.10.10'])
            self.assertTrue(os.access(self.vpn() / 'bin/xray', os.X_OK))
            # nothing half-done is left next to it
            self.assertEqual(sorted(p.name for p in (self.disk / 'extra').iterdir()), ['vpn'])

    def test_unpack_refuses_the_wrong_payload_and_keeps_the_old(self):
        for shell in self.each_shell():
            good = extra_tarball(self.tmp / 'good.tar.gz', release='v1')
            self.up(shell, f'extra_unpack vpn "{good}"')
            for bad in (extra_tarball(self.tmp / 'other.tar.gz', name='other'),
                        extra_tarball(self.tmp / 'noname.tar.gz', name=None),
                        extra_tarball(self.tmp / 'nobin.tar.gz', bins=())):
                r = self.up(shell, f'extra_unpack vpn "{bad}"; echo "rc=$?"')
                self.assertIn('rc=1', r.stdout, bad.name)
                self.assertEqual((self.vpn() / 'release').read_text().strip(), 'v1', bad.name)
            junk = self.tmp / 'junk.tar.gz'
            junk.write_bytes(b'not a tarball')
            r = self.up(shell, f'extra_unpack vpn "{junk}"; echo "rc=$?"')
            self.assertIn('rc=1', r.stdout)
            r = self.up(shell, f'extra_unpack ../x "{good}"; echo "rc=$?"')
            self.assertIn('rc=1', r.stdout)
            self.assertEqual(sorted(p.name for p in (self.disk / 'extra').iterdir()), ['vpn'])

    def test_adopt_takes_the_engines_of_an_older_image(self):
        for shell in self.each_shell():
            self.baked('ubuntu', 1)
            r = self.up(shell, f'extra_adopt vpn "{self.disk}/ubuntu"; echo "rc=$?"; extra_release vpn')
            self.assertEqual(r.stdout.split()[-2:], ['rc=0', 'v2026.10.06'], r.stderr)
            for e in ENGINES:
                self.assertTrue(os.access(self.vpn() / 'bin' / e, os.X_OK), e)
            # a root without engines has nothing to give
            r = self.up(shell, f'rm -rf "{self.disk}/extra"; extra_adopt vpn "{self.disk}/openwrt"; echo "rc=$?"')
            self.assertIn('rc=1', r.stdout)
            self.assertFalse(self.vpn().exists())

    def test_which_extras_an_update_fetches(self):
        cases = [
            # (installed release or None, ubuntu ENABLE, systems updated, wanted)
            (None, None, 'ubuntu', []),
            (None, 0, 'ubuntu', []),
            (None, 1, 'ubuntu', ['vpn']),             # uses the VPN with the engines of its image
            (None, 1, 'openwrt', []),                 # the system that uses it is not being updated
            ('v2026.10.01', None, 'ubuntu', ['vpn']),  # installed: follows the release
            ('v2026.10.10', 1, 'ubuntu', []),          # already this release's
        ]
        for shell in self.each_shell():
            for inst, enable, todo, want in cases:
                (self.disk / 'ubuntu/etc/mu300/vpn.conf').unlink(missing_ok=True)
                if enable is not None:
                    (self.disk / 'ubuntu/etc/mu300/vpn.conf').write_text(f'ENABLE={enable}\n')
                self.up(shell, f'rm -rf "{self.disk}/extra"')
                if inst:
                    self.up(shell, f'extra_unpack vpn "{extra_tarball(self.tmp / "i.tar.gz", release=inst)}"')
                r = self.up(shell, f'extras_to_fetch v2026.10.10 "{todo}"')
                self.assertEqual(r.stdout.split(), want, (inst, enable, todo, r.stderr))

    def test_update_keeps_a_configured_vpn_working(self):
        # the system was updated to an image without engines; the release's extra goes in, and when that fails the
        # engines of the image it replaced are taken over
        for shell in self.each_shell():
            for fetched in (True, False):
                self.up(shell, f'rm -rf "{self.disk}/extra" "{self.disk}/ubuntu.old"')
                self.baked('ubuntu', 1)
                os.rename(self.disk / 'ubuntu', self.disk / 'ubuntu.old')
                (self.disk / 'ubuntu/etc/mu300').mkdir(parents=True)
                (self.disk / 'ubuntu/etc/mu300/vpn.conf').write_text('ENABLE=1\n')
                stage = self.disk / '.mu300-update'
                stage.mkdir(exist_ok=True)
                if fetched:
                    extra_tarball(stage / 'mu300-extra-vpn.tar.gz', release='v2026.10.10')
                got = 'vpn' if fetched else ''
                r = self.up(shell, f'extras_install v2026.10.10 "ubuntu" "{got}"; echo "rc=$?"; extra_release vpn')
                self.assertEqual(r.stdout.split()[-2:], ['rc=0', 'v2026.10.10' if fetched else 'v2026.10.06'],
                                 (fetched, r.stdout, r.stderr))
                self.assertTrue(os.access(self.vpn() / 'bin/xray', os.X_OK))
                self.assertFalse((stage / 'mu300-extra-vpn.tar.gz').exists())

    def test_fetch_verified_takes_another_server(self):
        self.stub('curl', 'for a; do last=$a; done; echo "$last" >> "$STUBLOG/urls"; '
                          'case $last in */SHA256SUMS) echo "0000  x" ;; esac; exit 0')
        for shell in self.each_shell():
            (self.tmp / 'urls').unlink(missing_ok=True)
            self.up(shell, 'fetch_verified v1 x >/dev/null 2>&1', MU300_RELEASE_URL='http://10.0.0.2:8000/rel')
            urls = (self.tmp / 'urls').read_text().split()
            self.assertEqual(urls[0], 'http://10.0.0.2:8000/rel/SHA256SUMS')

    # ---- mu300-extra ---------------------------------------------------------------------------------------
    def serve(self, release='v2026.10.10', assets=None):
        """a release on a 'server': a directory curl (stubbed) reads from, with its SHA256SUMS"""
        d = self.tmp / 'server' / release
        d.mkdir(parents=True, exist_ok=True)
        if assets is None:
            assets = {'mu300-extra-vpn.tar.gz': extra_tarball(self.tmp / 'payload.tar.gz', release=release)}
        sums = []
        for name, src in assets.items():
            data = src.read_bytes()
            (d / name).write_bytes(data)
            sums.append(f'{hashlib.sha256(data).hexdigest()}  {name}')
        (d / 'SHA256SUMS').write_text('\n'.join(sums) + '\n')
        # curl -o OUT URL or curl URL (stdout); the URL's last two parts name the release and the file
        self.stub('curl', f'out=; url=; while [ $# -gt 0 ]; do case $1 in -o) out=$2; shift ;; -*) ;; *) url=$1 ;; esac; '
                          f'shift; done; echo "$url" >> "$STUBLOG/urls"; f=${{url##*/}}; r=${{url%/*}}; r=${{r##*/}}; '
                          f'case $url in */releases/latest) echo \'"tag_name": "v2026.10.10"\'; exit 0 ;; esac; '
                          f'src="{self.tmp}/server/$r/$f"; [ -f "$src" ] || exit 22; '
                          f'if [ -n "$out" ]; then cat "$src" > "$out"; else cat "$src"; fi')
        return d

    def test_install_from_the_release_of_the_system(self):
        self.serve('v2026.10.10')
        for shell in self.each_shell():
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            for os_ in ('ubuntu', 'openwrt'):
                (self.disk / os_ / 'etc/mu300/image-version').write_text('v2026.10.10\n')
            (self.root / 'etc/mu300').mkdir(parents=True, exist_ok=True)
            (self.root / 'etc/mu300/image-version').write_text('v2026.10.10\n')
            r = self.ex(shell, 'install', 'vpn')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((self.vpn() / 'release').read_text().strip(), 'v2026.10.10')
            self.assertIn('/v2026.10.10/mu300-extra-vpn.tar.gz', (self.tmp / 'urls').read_text())
            # the commands are on the PATH
            self.assertEqual(os.readlink(self.links / 'xray'), str(self.vpn() / 'bin/xray'))
            st = self.ex(shell, 'status')
            self.assertIn('vpn', st.stdout)
            self.assertIn('v2026.10.10', st.stdout)
            # the download is not left behind
            self.assertFalse((self.disk / '.mu300-extra').exists())

    def test_install_falls_back_to_the_newest_release(self):
        # a system from before extras (v2026.10.06 has no mu300-extra-vpn.tar.gz) takes the newest release's
        other = self.tmp / 'other'
        other.write_text('x')
        self.serve('v2026.10.06', assets={'mu300-update': other})
        self.serve('v2026.10.10')
        (self.root / 'etc/mu300').mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/mu300/image-version').write_text('v2026.10.06\n')
        for shell in self.each_shell():
            self.up(shell, f'rm -rf "{self.disk}/extra"')
            r = self.ex(shell, 'install', 'vpn')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((self.vpn() / 'release').read_text().strip(), 'v2026.10.10')

    def test_install_refuses_a_bad_checksum(self):
        d = self.serve('v2026.10.10')
        (d / 'mu300-extra-vpn.tar.gz').write_bytes(b'tampered')
        (self.root / 'etc/mu300').mkdir(parents=True, exist_ok=True)
        (self.root / 'etc/mu300/image-version').write_text('v2026.10.10\n')
        for shell in self.each_shell():
            r = self.ex(shell, 'install', 'vpn', MU300_RELEASE='v2026.10.10')
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(self.vpn().exists())

    def test_install_from_a_local_file_and_remove(self):
        f = extra_tarball(self.tmp / 'local.tar.gz', release='dev')
        for shell in self.each_shell():
            r = self.ex(shell, 'install', 'vpn', MU300_EXTRA_FILE=f)
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
            self.assertIn('not installed', r.stdout)

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

    def test_adopt_command(self):
        for shell in self.each_shell():
            self.up(shell, f'rm -rf "{self.disk}/extra" "{self.disk}/ubuntu.old"')
            self.baked('ubuntu', 1)
            os.rename(self.disk / 'ubuntu', self.disk / 'ubuntu.old')
            (self.disk / 'ubuntu/etc/mu300').mkdir(parents=True)
            r = self.ex(shell, 'adopt', 'vpn', MU300_RUNNING_OS='ubuntu')
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(os.access(self.vpn() / 'bin/xray', os.X_OK))
            # nothing to take: fails, quietly enough for mu300-vpn to try the download next
            self.up(shell, f'rm -rf "{self.disk}/extra" "{self.disk}/ubuntu.old"')
            r = self.ex(shell, 'adopt', 'vpn', MU300_RUNNING_OS='ubuntu')
            self.assertNotEqual(r.returncode, 0)
            import shutil
            shutil.rmtree(self.disk / 'ubuntu')
            (self.disk / 'ubuntu/etc/mu300').mkdir(parents=True)


if __name__ == '__main__':
    unittest.main()
