"""A release's diffs (openwrt/tools/image-diff.py, tools/make-diffs.sh; mu300-update's diff block): a diff is made only
from a base the release key vouches for, is listed in the signed SHA256SUMS, is used on a device only after that
signature and its checksum are checked, and gives the new system only when the result is the release's signed manifest
of the image entry for entry - else the whole image is downloaded. The user's files, the VPN module and its kill switch are what they are after a whole-image update."""
import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest
import test_delta
from test_delta import BUSYBOX, CC, LINUX, build, elf, image, inode_shared, untar
from test_update_page import OPENSSL, Key

TOOL = [sys.executable, str(TOP / 'openwrt/tools/image-diff.py')]
BASE, TAG = 'v2026.10.20.1', 'v2026.10.27.1'
OS = 'openwrt-luci'
ASSET = 'mu300-openwrt-luci-rootfs.tar.gz'
MANIFEST = 'mu300-openwrt-luci-rootfs.manifest.gz'
DIFF = f'mu300-openwrt-luci-rootfs.diff-{BASE}.tar.gz'
# a library that does not change and does not compress: what a diff saves (the update's own parts are always in it)
HUGE = ('f', b'\x7fELF\x02\x01\x01\x00' + hashlib.shake_256(b'mu300').digest(6 << 20), 0o644)
# (and a name with a backslash, which a manifest escapes)
SLASH = 'usr/share/mu300/a\\x2dname'
OLD = dict(test_delta.V1, **{'usr/lib/libhuge.so': HUGE, SLASH: ('f', '[Unit]\n', 0o644)})
OLD['etc/mu300/image-version'] = ('f', BASE + '\n', 0o644)
NEW = dict(test_delta.V2, **{'usr/lib/libhuge.so': HUGE, SLASH: ('f', '[Unit]\n', 0o644)})
NEW['etc/mu300/image-version'] = ('f', TAG + '\n', 0o644)
# the VPN: its settings and the kill switch in the system (carried over), the module on the disk beside the systems
VPN = {'etc/mu300/vpn.conf': ('f', 'ENABLE=1\nKILL_SWITCH=1\nPROFILE=home\n', 0o600),
       'etc/mu300/vpn/home.json': ('f', '{"secret": "never in a release"}\n', 0o600)}


def setUpModule():
    test_delta.setUpModule()


def tearDownModule():
    test_delta.tearDownModule()


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def members(tarball):
    with tarfile.open(tarball) as t:
        return {m.name[2:] if m.name.startswith('./') else m.name: m for m in t}


def tool(*args):
    return subprocess.run(TOOL + [str(a) for a in args], capture_output=True, text=True)


def rewrite(tarball, change):
    """TARBALL with CHANGE(name, data) -> data | None (left out) applied to each regular member; ADD: more members"""
    out = io.BytesIO()
    with tarfile.open(tarball) as t, tarfile.open(fileobj=out, mode='w:gz', format=tarfile.GNU_FORMAT) as o:
        for m in t:
            data = t.extractfile(m).read() if m.isreg() else None
            if data is not None:
                data = change(m.name, data)
                if data is None:
                    continue
                m.size = len(data)
            o.addfile(m, io.BytesIO(data) if data is not None else None)
        for name, data in getattr(change, 'add', ()):
            ti = tarfile.TarInfo('./' + name)
            ti.size, ti.mode = len(data), 0o600
            o.addfile(ti, io.BytesIO(data))
    Path(tarball).write_bytes(out.getvalue())


@unittest.skipUnless(CC and LINUX and BUSYBOX and OPENSSL, 'needs a C compiler, busybox and OpenSSL 3 on Linux')
class Diff(ShellTest):

    def setUp(self):
        super().setUp()
        self.key = Key(self.tmp / 'key')
        self.disk = self.tmp / 'disk'
        self.root = self.tmp / 'root'
        (self.root / 'run/mu300').mkdir(parents=True)
        self.base = image(self.tmp / 'base.tar.gz', OLD, self.tmp, BASE)
        self.base_manifest = self.tmp / 'base.manifest.gz'
        self.base_manifest.write_bytes(subprocess.run(['tar', '-xzOf', str(self.base), './etc/mu300/image-manifest.gz'],
                                                      capture_output=True, check=True).stdout)
        self.rel = self.tmp / 'rel'
        self.rel.mkdir()
        image(self.rel / ASSET, NEW, self.tmp, TAG)
        (self.rel / MANIFEST).write_bytes(subprocess.run(['tar', '-xzOf', str(self.rel / ASSET), './etc/mu300/image-manifest.gz'],
                                                         capture_output=True, check=True).stdout)
        r = tool('make', self.base_manifest, self.rel / ASSET, self.rel / DIFF)
        self.assertEqual(r.returncode, 0, r.stderr)
        (self.rel / 'mu300-release').write_text(TAG + '\n')
        self.sums()

    def sums(self, sign=True):
        names = sorted(p.name for p in self.rel.iterdir() if p.name not in ('SHA256SUMS', 'SHA256SUMS.sig'))
        (self.rel / 'SHA256SUMS').write_text(''.join(f'{sha(self.rel / n)}  {n}\n' for n in names))
        (self.rel / 'SHA256SUMS.sig').unlink(missing_ok=True)
        if sign:
            self.key.sign(self.rel / 'SHA256SUMS')

    def fresh(self):
        shutil.rmtree(self.disk, ignore_errors=True)
        (self.disk / '.mu300-update').mkdir(parents=True)
        self.old = untar(self.base, self.disk / OS)
        build(self.old, {'etc/mine.conf': ('f', 'mine\n', 0o600), 'var/lib/app/db': ('f', elf('db'), 0o644), **VPN})
        build(self.disk / 'extra/vpn', {'bin/mu300-vpn': ('f', '#!/bin/sh\n', 0o755), 'VERSION': ('f', 'v2.0.0\n', 0o644)})

    def up(self, shell, code, **env):
        """CODE after mu300-update is sourced, with the release served from self.rel (the downloads it asked for in
        $STUBLOG/fetched)"""
        e = dict(MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN, MU300_SYSROOT=self.root, MU300_RELEASE_PUB=self.key.pub)
        e.update(env)
        serve = (f'fetch_stdout() {{ cat "{self.rel}/${{1##*/}}" 2>/dev/null; }}; '
                 f'fetch_file() {{ echo "${{1##*/}}" >> "$STUBLOG/fetched"; cp "{self.rel}/${{1##*/}}" "$2" 2>/dev/null; }}; '
                 'sleep() { :; }; is_root() { false; }; carry_need() { echo 0; }; ')
        return self.sh(shell, f'. "{BIN}/mu300-update"; {serve}{code}', **e)

    def fetched(self):
        f = self.tmp / 'fetched'
        out = f.read_text().split() if f.exists() else []
        f.unlink(missing_ok=True)
        return out

    def update(self, shell, **env):
        return self.up(shell, f'{{ diff_fetch {TAG} {OS} || fetch_verified {TAG} {ASSET} || fetch_stop $?; }} && '
                              f'payload_is {TAG} {OS} && diffs_unpack {TAG} {OS} && apply_one {OS} {TAG}', **env)

    def whole(self):
        """the new system as the whole image gives it"""
        return test_delta.tree(untar(self.rel / ASSET, self.tmp / 'whole'))

    def check_installed(self, before_extra):
        new = self.disk / OS
        got = test_delta.tree(new)
        for p, v in self.whole().items():
            if p.startswith('etc/mu300/image-manifest') or not os.path.lexists(new / p):
                continue
            self.assertEqual(got[p][:2] + got[p][4:], v[:2] + v[4:], p)
        # the user's files, the VPN's settings and kill switch: carried over as they were
        self.assertEqual((new / 'etc/mine.conf').read_text(), 'mine\n')
        for p, v in VPN.items():
            self.assertEqual((new / p).read_bytes(), v[1].encode(), p)
            self.assertEqual(oct((new / p).stat().st_mode & 0o777), oct(v[2]), p)
        self.assertEqual(test_delta.snapshot(self.disk / 'extra'), before_extra)
        self.assertFalse((new / '.mu300-diff').exists())
        for d in (OS + '.new', '.mu300-delta-' + OS):
            self.assertFalse((self.disk / d).exists(), d)

    # --- the tool

    def test_the_tool_leaves_out_only_what_a_device_keeps_as_the_image_brought_it(self):
        d = members(self.rel / DIFF)
        self.assertEqual(list(d)[0], '.mu300-diff')
        head, *left = (tarfile.open(self.rel / DIFF).extractfile('./.mu300-diff').read().decode().splitlines())
        self.assertEqual(head, f'mu300-diff 1 {OS} {BASE} {TAG}')
        left = {l.split(' ', 6)[6] for l in left}
        # unchanged program trees out; what changed, what is new, etc and var, the update's own parts, hard links in
        self.assertIn('usr/lib/libbig.so', left)
        self.assertIn('usr/lib/python3/mod.py', left)
        self.assertIn(SLASH.replace('\\', '\\\\'), left)
        for p in ('usr/bin/changed', 'usr/bin/new-in-v2', 'etc/binary.db', 'var/lib/state.bin', 'usr/local/bin/tool',
                  'opt/mu300/bin/mu300-update', 'opt/mu300/bin/mu300-carry', 'usr/bin/busybox', 'usr/bin/hard1',
                  'usr/bin/hard2', 'etc/mu300/image-version', 'etc/mu300/image-manifest.gz'):
            self.assertNotIn(p, left, p)
            self.assertIn(p, d, p)
        self.assertLess((self.rel / DIFF).stat().st_size, (self.rel / ASSET).stat().st_size)
        r = tool('check', self.base_manifest, self.rel / ASSET, self.rel / DIFF)
        self.assertEqual(r.returncode, 0, r.stderr)
        # one that does not give the image, or is from another base, is caught
        rewrite(self.rel / DIFF, lambda n, b: b + b'x' if n == './usr/bin/changed' else b)
        self.assertNotEqual(tool('check', self.base_manifest, self.rel / ASSET, self.rel / DIFF).returncode, 0)
        other = gzip.compress(gzip.decompress(self.base_manifest.read_bytes()).replace(b' openwrt-luci ', b' openwrt '))
        (self.tmp / 'other.gz').write_bytes(other)
        r = tool('make', self.tmp / 'other.gz', self.rel / ASSET, self.tmp / 'x.tar.gz')
        self.assertNotEqual(r.returncode, 0)

    # --- the device

    def test_a_signed_diff_applies_and_gives_the_image(self):
        for shell in self.each_shell():
            self.fresh()
            before = test_delta.snapshot(self.old)
            extra = test_delta.snapshot(self.disk / 'extra')
            r = self.update(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("signed with the project's release key", r.stdout)
            self.assertIn(f'only what changed since {BASE}', r.stdout)
            self.assertIn("the diff gives the release's image", r.stdout)
            got = self.fetched()
            self.assertIn(DIFF, got)
            self.assertNotIn(ASSET, got)
            self.check_installed(extra)
            # the previous system is kept whole for a rollback, sharing only binaries of the program trees
            self.assertEqual(test_delta.snapshot(self.disk / (OS + '.old')), before)
            for p in inode_shared(self.disk / OS, self.disk / (OS + '.old')):
                self.assertRegex(p, r'^(usr/(?!local/)|bin/|sbin/|lib/|opt/mu300/)', p)
            self.assertFalse((self.disk / '.mu300-update' / DIFF).exists())
        src = (BIN / 'mu300-update').read_text()
        apply = src[src.index('\napply() {'):src.index('\nclean_broken() {')]
        # the diffs are unpacked after every download, and before the network is said to be needed no more
        self.assertLess(apply.index('modules_fetch "$tag" "$todo"'), apply.index('diffs_unpack "$tag" $todo'))
        self.assertLess(apply.index('diffs_unpack "$tag" $todo'), apply.index('from now on the network is not needed'))

    def test_without_rollback_sharing_the_diff_still_gives_the_image(self):
        for shell in self.each_shell():
            self.fresh()
            r = self.update(shell, MU300_DELTA=0)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("the diff gives the release's image", r.stdout)
            self.assertEqual(inode_shared(self.disk / OS, self.disk / (OS + '.old')) - {'var/lib/app/db'}, set())

    def test_a_tampered_diff_is_refused(self):
        rewrite(self.rel / DIFF, lambda n, b: elf('evil') if n == './usr/bin/new-in-v2' else b)
        for shell in self.each_shell():
            # its checksum is not the signed one: never kept, never unpacked; the whole image instead
            self.fresh()
            extra = test_delta.snapshot(self.disk / 'extra')
            r = self.update(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('the whole image is downloaded', r.stdout)
            self.assertNotIn('evil'.encode(), (self.disk / OS / 'usr/bin/new-in-v2').read_bytes())
            self.assertIn(ASSET, self.fetched())
            self.check_installed(extra)
        # and with its checksum changed to match, the signature no longer does: nothing at all is taken
        self.sums(sign=False)
        self.key.sign(self.key.pub, self.rel / 'SHA256SUMS.sig')     # the key's, but of another file
        for shell in self.each_shell():
            self.fresh()
            before = test_delta.snapshot(self.old)
            r = self.update(shell)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('does not match the project\'s release key', r.stderr)
            self.assertIn('nothing was changed', r.stderr)
            self.assertEqual(test_delta.snapshot(self.disk / OS), before)
            self.assertFalse((self.disk / (OS + '.old')).exists())

    def test_an_unsigned_release_index_is_refused(self):
        # SHA256SUMS is the index of the diffs: without its signature a device with the key takes none of them
        self.sums(sign=False)
        for shell in self.each_shell():
            self.fresh()
            before = test_delta.snapshot(self.old)
            r = self.update(shell)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("is not signed with the project's release key", r.stderr)
            self.assertEqual(test_delta.snapshot(self.disk / OS), before)
            self.assertNotIn(DIFF, self.fetched())
            self.assertFalse((self.disk / '.mu300-update' / DIFF).exists())
        # a diff the signed index does not list is not used either
        (self.rel / DIFF).rename(self.tmp / 'unlisted')
        self.sums()
        (self.tmp / 'unlisted').rename(self.rel / DIFF)
        for shell in self.each_shell():
            self.fresh()
            r = self.update(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn(DIFF, self.fetched() + [p.name for p in (self.disk / '.mu300-update').iterdir()])
            self.assertNotIn('the diff', r.stdout)

    def test_a_wrong_base_falls_back_to_the_whole_image(self):
        for shell in self.each_shell():
            # a file the diff takes from the installed system changed there (a package, a hand-made fix)
            self.fresh()
            extra = test_delta.snapshot(self.disk / 'extra')
            (self.old / 'usr/lib/libbig.so').write_bytes(elf('patched by hand', 200000))
            r = self.update(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(f'are not as {BASE} had them', r.stdout)
            self.assertIn('the whole image is downloaded', r.stdout)
            self.assertEqual(self.fetched(), [DIFF, MANIFEST, ASSET])
            self.check_installed(extra)
            # another release installed: there is no diff from it, so it is not even asked for
            self.fresh()
            extra = test_delta.snapshot(self.disk / 'extra')
            (self.old / 'etc/mu300/image-manifest.gz').write_bytes(gzip.compress(
                gzip.decompress((self.old / 'etc/mu300/image-manifest.gz').read_bytes()).replace(BASE.encode(), b'v2026.10.13.1')))
            r = self.update(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn(DIFF, self.fetched())
            self.check_installed(extra)

    def test_a_signed_diff_that_does_not_give_the_image_is_not_installed(self):
        # signed, but its result is not the release's manifest (a build fault): checked entry for entry, not trusted
        rewrite(self.rel / DIFF, lambda n, b: b + b'built wrong' if n == './usr/bin/new-in-v2' else b)
        self.sums()
        for shell in self.each_shell():
            self.fresh()
            extra = test_delta.snapshot(self.disk / 'extra')
            r = self.update(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("the result is not the release's image", r.stdout)
            self.assertIn('the whole image is downloaded', r.stdout)
            self.check_installed(extra)

    def test_a_diff_never_brings_vpn_files(self):
        # a diff with the VPN's settings or kill switch in it (signed all the same): they are not the image's, the
        # result is refused, and the device's own settings are carried over as they were
        def change(n, b):
            return b
        change.add = [('etc/mu300/vpn.conf', b'ENABLE=0\nKILL_SWITCH=0\n'), ('opt/mu300/lib/vpn/hook', b'#!/bin/sh\n')]
        rewrite(self.rel / DIFF, change)
        self.sums()
        for shell in self.each_shell():
            self.fresh()
            extra = test_delta.snapshot(self.disk / 'extra')
            r = self.update(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("the result is not the release's image", r.stdout)
            self.check_installed(extra)
            self.assertFalse((self.disk / OS / 'opt/mu300/lib/vpn').exists())

    def test_the_panel_sees_the_diff_as_the_download(self):
        listing = (f'R {TAG} 0 0 2026-10-27T10:00:00Z MU300 Linux {TAG}\n'
                   f'A {TAG} 50000000 {ASSET}\nA {TAG} 9000000 {DIFF}\nA {TAG} 30000000 mu300-kernel-7.2.tar.gz\n'
                   f'A {TAG} 100000 mu300-update\n')
        (self.tmp / 'listing').write_text(listing)
        for shell in self.each_shell():
            self.fresh()
            for env, want in (({}, (1, 9100000 + 30000000)), ({'MU300_DIFF': 0}, (0, 50100000 + 30000000))):
                r = self.up(shell, f'RELEASES=$(cat "{self.tmp}/listing"); extras_to_fetch() {{ :; }}; release_json {TAG}', **env)
                self.assertEqual(r.returncode, 0, r.stderr)
                o = json.loads(r.stdout)
                self.assertEqual((o['diffs'], o['download']), want, r.stdout)


@unittest.skipUnless(CC and LINUX and BUSYBOX and OPENSSL, 'needs a C compiler, busybox and OpenSSL 3 on Linux')
class MakeDiffs(ShellTest):
    """tools/make-diffs.sh: the bases a release is diffed from, and only signed ones"""

    def setUp(self):
        super().setUp()
        self.key = Key(self.tmp / 'key')
        self.d = self.tmp / 'release'
        self.d.mkdir()
        image(self.d / ASSET, NEW, self.tmp, TAG)
        self.bases = self.tmp / 'bases'
        tags = ['v2026.10.13.1', 'v2026.10.14.1', 'v2026.10.15.1', 'v2026.10.16.1', 'v2026.10.18.1', BASE, 'v2026.11.01.1']
        for t in tags:
            b = self.bases / t
            b.mkdir(parents=True)
            spec = dict(OLD)
            spec['etc/mu300/image-version'] = ('f', t + '\n', 0o644)
            img = image(self.tmp / f'{t}.tar.gz', spec, self.tmp, t)
            (b / MANIFEST).write_bytes(subprocess.run(['tar', '-xzOf', str(img), './etc/mu300/image-manifest.gz'],
                                                      capture_output=True, check=True).stdout)
            (b / 'mu300-release').write_text(t + '\n')
            (b / 'SHA256SUMS').write_text(f'{sha(b / MANIFEST)}  {MANIFEST}\n{sha(b / "mu300-release")}  mu300-release\n')
            if t != 'v2026.10.18.1':
                self.key.sign(b / 'SHA256SUMS')

    def run_it(self, shell, **env):
        e = dict(MU300_DIFF_BASES_DIR=self.bases, MU300_DIFF_PUB=self.key.pub, TMPDIR=self.tmp)
        e.update(env)
        return self.script(shell, TOP / 'tools/make-diffs.sh', self.d, TAG, **e)

    def made(self):
        return sorted(p.name.split('.diff-')[1][:-len('.tar.gz')] for p in self.d.glob('*.diff-*'))

    def test_the_five_signed_releases_before(self):
        for shell in list(self.each_shell())[:1]:
            r = self.run_it(shell)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # not the newer one, not the unsigned one; the five newest of the rest
            self.assertEqual(self.made(), ['v2026.10.13.1', 'v2026.10.14.1', 'v2026.10.15.1', 'v2026.10.16.1', BASE])
            self.assertIn('v2026.10.18.1: its SHA256SUMS is not signed', r.stderr)
            for t in self.made():
                c = tool('check', self.bases / t / MANIFEST, self.d / ASSET, self.d / f'mu300-openwrt-luci-rootfs.diff-{t}.tar.gz')
                self.assertEqual(c.returncode, 0, c.stderr)
            for p in self.d.glob('*.diff-*'):
                p.unlink()
            # fewer when asked; none with 0; a manifest that is not the one its SHA256SUMS lists stops the release
            self.assertEqual(self.run_it(shell, MU300_DIFF_BASES=2).returncode, 0)
            self.assertEqual(self.made(), ['v2026.10.16.1', BASE])
            for p in self.d.glob('*.diff-*'):
                p.unlink()
            self.assertEqual(self.run_it(shell, MU300_DIFF_BASES=0).returncode, 0)
            self.assertEqual(self.made(), [])
            with open(self.bases / BASE / MANIFEST, 'ab') as f:
                f.write(b'x')
            r = self.run_it(shell)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('does not match its SHA256SUMS', r.stderr)

    def test_make_release_lists_the_diffs_in_the_signed_sums(self):
        rel = (TOP / 'tools/make-release.sh').read_text()
        self.assertLess(rel.index('tools/make-diffs.sh" "$D" "$TAG"'), rel.index('> SHA256SUMS)'))
        self.assertIn('shasum -a 256 *.tar.gz', rel)


if __name__ == '__main__':
    unittest.main()
