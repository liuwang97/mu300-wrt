"""mu300-update, sourced for its functions (MU300_LIB=1) against a fake Linux partition (MU300_DISK): which files of a
release it takes, the kernel choice, the byte helpers it edits boot image headers with, and whether a kernel bundle
may go onto this device."""
import io
import struct
import tarfile
import unittest

from helpers import BIN, ShellTest


class Update(ShellTest):
    def setUp(self):
        super().setUp()
        self.disk = self.tmp / 'disk'
        (self.disk / 'ubuntu' / 'etc').mkdir(parents=True)
        self.root = self.tmp / 'root'
        (self.root / 'run/mu300').mkdir(parents=True)
        self.device('f50')

    def device(self, name):
        (self.root / 'run/mu300/device').write_text(name + '\n')

    def up(self, shell, code, **env):
        return self.sh(shell, f'. "{BIN}/mu300-update"; {code}', MU300_LIB=1, MU300_DISK=self.disk, MU300_BIN=BIN,
                       MU300_SYSROOT=self.root, **env)

    def test_sourcing_does_nothing(self):
        for shell in self.each_shell():
            r = self.up(shell, 'echo loaded')
            self.assertEqual((r.returncode, r.stdout), (0, 'loaded\n'), r.stderr)

    def test_fetch_retries_a_network_error(self):
        # right after boot the VPN is up some seconds after mobile data, and a request in between is reset
        # (curl 35/56): "mu300-update check" then said "could not reach GitHub". Network errors are retried a few
        # times; an HTTP error (22: 404 and the like) is an answer and is not, and neither is a timeout (28: curl's
        # own --retry has retried it already, and four more rounds of 120 s would hold "check" for half an hour).
        for shell in self.each_shell():
            for rc, fails, want_out, want_calls in ((56, 2, 'body', 3), (35, 1, 'body', 2), (22, 1, '', 1),
                                                     (7, 9, '', 4), (28, 1, '', 1), (6, 1, 'body', 2)):
                n = self.tmp / 'n'
                n.write_text('0')
                self.stub('curl', f'c=$(($(cat "$STUBLOG/n") + 1)); echo $c > "$STUBLOG/n"; '
                                  f'[ $c -gt {fails} ] && {{ echo body; exit 0; }}; exit {rc}')
                r = self.up(shell, 'fetch_stdout https://example.invalid/x; echo "rc=$?"', MU300_FETCH_DELAY=0)
                self.assertEqual(int(n.read_text()), want_calls, (rc, fails))
                self.assertEqual(r.stdout.replace('rc=0\n', '').replace('rc=1\n', '').strip(), want_out, (rc, r.stdout))
                self.assertIn('rc=0' if want_out else 'rc=1', r.stdout)

    def test_rootfs_asset(self):
        osr = self.disk / 'ubuntu' / 'etc' / 'os-release'
        cases = [('24.04', {}, 'mu300-ubuntu-rootfs.tar.gz'),
                 ('26.04', {}, 'mu300-ubuntu-26.04-rootfs.tar.gz'),
                 ('26.04', {'MU300_UBUNTU': '24.04'}, 'mu300-ubuntu-rootfs.tar.gz'),
                 ('24.04', {'MU300_UBUNTU': '26.04'}, 'mu300-ubuntu-26.04-rootfs.tar.gz'),
                 (None, {}, 'mu300-ubuntu-rootfs.tar.gz')]
        for shell in self.each_shell():
            for ver, env, want in cases:
                if ver:
                    osr.write_text(f'NAME="Ubuntu"\nVERSION_ID="{ver}"\n')
                else:
                    osr.unlink(missing_ok=True)
                self.assertEqual(self.up(shell, 'rootfs_asset ubuntu', **env).stdout.strip(), want, (ver, env))
            self.assertEqual(self.up(shell, 'rootfs_asset openwrt').stdout.strip(), 'mu300-openwrt-rootfs.tar.gz')

    def test_kernel_choice(self):
        boot = self.disk / 'boot'
        boot.mkdir()
        cases = [(None, '5.4', 'mu300-kernel.tar.gz'), ('6.18', '6.18', 'mu300-kernel-6.18.tar.gz'),
                 ('7.2', '7.2', 'mu300-kernel-7.2.tar.gz'), ('5.4', '5.4', 'mu300-kernel.tar.gz'),
                 ('6.1', '5.4', 'mu300-kernel.tar.gz'), ('', '5.4', 'mu300-kernel.tar.gz')]
        for shell in self.each_shell():
            for c, choice, asset in cases:
                f = boot / 'kernel'
                if c is None:
                    f.unlink(missing_ok=True)
                else:
                    f.write_text(c + '\n')
                self.assertEqual(self.up(shell, 'kernel_choice; kernel_asset').stdout.split(), [choice, asset], c)

    def test_byte_helpers(self):
        # the boot image header is edited with these: sizes little endian, the AVB footer big endian
        f = self.tmp / 'bytes.bin'
        for shell in self.each_shell():
            for v in (0, 1, 255, 256, 0x12345678, 0xFFFFFFFF):
                r = self.up(shell, f'bytes {v} 4 le > "{f}"; u32 "{f}" 0')
                self.assertEqual(f.read_bytes(), struct.pack('<I', v), v)
                self.assertEqual(r.stdout.strip(), str(v))
            for v in (0, 0x1234, 0x0000000100000000, 0x7FFFFFFF12345678):
                r = self.up(shell, f'bytes {v} 8 be > "{f}"; be64 "{f}" 0')
                self.assertEqual(f.read_bytes(), struct.pack('>Q', v), v)
                self.assertEqual(r.stdout.strip(), str(v))
            # poke: overwrite in place, the rest untouched
            f.write_bytes(bytes(16))
            self.up(shell, f'bytes 3735928559 4 le | poke "{f}" 4')
            self.assertEqual(f.read_bytes(), bytes(4) + struct.pack('<I', 0xDEADBEEF) + bytes(8))

    def test_pad_page(self):
        f = self.tmp / 'img'
        for shell in self.each_shell():
            for n, want in ((0, 0), (1, 4096), (4095, 4096), (4096, 4096), (4097, 8192)):
                f.write_bytes(b'x' * n)
                self.up(shell, f'pad_page "{f}"')
                self.assertEqual(f.stat().st_size, want, n)

    def test_installed_systems(self):
        for shell in self.each_shell():
            self.assertEqual(self.up(shell, 'installed_systems').stdout.split(), ['ubuntu'])
            (self.disk / 'openwrt').mkdir(exist_ok=True)
            self.assertEqual(self.up(shell, 'installed_systems').stdout.split(), ['ubuntu', 'openwrt'])
            (self.disk / 'openwrt').rmdir()

    def bundle(self, name, devices, features=None):
        p = self.tmp / name
        with tarfile.open(p, 'w:gz') as t:
            for fn, data in [('./Image', b'kernel'), ('./ramdisk-generic.lz4', b'rd')] + (
                    [('./devices', devices.encode())] if devices is not None else []) + (
                    [('./features', features.encode())] if features is not None else []):
                ti = tarfile.TarInfo(fn)
                ti.size = len(data)
                t.addfile(ti, io.BytesIO(data))
        return p

    def test_bundle_runs_here(self):
        new = self.bundle('new.tar.gz', 'f50 u30air\n')
        old = self.bundle('old.tar.gz', None)
        f50only = self.bundle('f50.tar.gz', 'f50\n')
        cases = [('f50', new, True), ('f50', old, True), ('u30air', new, True), ('u30air', old, False),
                 ('u30air', f50only, False)]
        for shell in self.each_shell():
            for dev, b, ok in cases:
                self.device(dev)
                d = self.tmp / 'x'
                d.mkdir(exist_ok=True)
                r = self.up(shell, f'tar -tzf "{b}" > "{d}/list"; bundle_runs_here "{b}" "{d}/list" "{d}" && echo YES || echo NO')
                self.assertEqual(r.stdout.strip(), 'YES' if ok else 'NO', (dev, b.name, r.stderr))
                (d / 'devices').unlink(missing_ok=True)

    def test_card_installation_needs_an_sd_capable_kernel(self):
        rootdev = self.tmp / 'root-dev'
        plain = self.bundle('plain.tar.gz', 'f50\n')
        empty = self.bundle('empty.tar.gz', 'f50\n', '')
        sd = self.bundle('sd.tar.gz', 'f50\n', 'other\nsdcard\n')
        for shell in self.each_shell():
            def run(dev, b):
                if dev is None:
                    rootdev.unlink(missing_ok=True)
                else:
                    rootdev.write_text(dev + '\n')
                d = self.tmp / 'x'
                d.mkdir(exist_ok=True)
                code = (f'MU300_ROOT_DEV_FILE="{rootdev}"; tar -tzf "{b}" > "{d}/list"; '
                        f'if root_on_sd && ! bundle_has_sd "{b}" "{d}/list"; then echo REFUSE; else echo OK; fi')
                r = self.up(shell, code)
                return r.stdout.strip()
            self.assertEqual(run('/dev/mmcblk1p1', plain), 'REFUSE')
            self.assertEqual(run('/dev/mmcblk1p1', empty), 'REFUSE')
            self.assertEqual(run('/dev/mmcblk1p1', sd), 'OK')
            self.assertEqual(run('mmcblk0@27762098176', plain), 'OK')
            self.assertEqual(run(None, plain), 'OK')


if __name__ == '__main__':
    unittest.main()
