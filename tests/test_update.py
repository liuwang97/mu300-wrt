"""mu300-update, sourced for its functions (MU300_LIB=1) against a fake Linux partition (MU300_DISK): which files of a
release it takes, the kernel choice, the byte helpers it edits boot image headers with, and whether a kernel bundle
may go onto this device."""
import io
import shutil
import struct
import subprocess
import sys
import tarfile
import unittest

from helpers import BIN, TOP, ShellTest
from test_boot_image import HAVE_LZ4, fake_stock, fake_misc, BOOT


class UpdateBase(ShellTest):
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


class Update(UpdateBase):
    def test_sourcing_does_nothing(self):
        for shell in self.each_shell():
            r = self.up(shell, 'echo loaded')
            self.assertEqual((r.returncode, r.stdout), (0, 'loaded\n'), r.stderr)

    def test_linux_slot(self):
        f = self.root / 'run/mu300/linux-slot'
        for shell in self.each_shell():
            f.unlink(missing_ok=True)
            self.assertEqual(self.up(shell, 'linux_slot').stdout.strip(), 'b')
            for v, want in (('a\n', 'a'), ('b\n', 'b'), ('x\n', 'b')):
                f.write_text(v)
                self.assertEqual(self.up(shell, 'linux_slot').stdout.strip(), want, v)

    def test_linux_slot_without_the_file_is_the_booted_one(self):
        # an initramfs that did not publish it (or a garbled file): the slot LK booted, from the command line or the
        # bootargs in the device tree; b only when neither says
        f = self.root / 'run/mu300/linux-slot'
        cmdline = self.root / 'proc/cmdline'
        bootargs = self.root / 'proc/device-tree/chosen/bootargs'
        bootargs.parent.mkdir(parents=True)
        cases = [(None, 'console=x androidboot.slot_suffix=_a quiet', None, 'a'),
                 ('x\n', 'loglevel=5', b'console=x\0androidboot.slot_suffix=_a\0', 'a'),
                 (None, 'loglevel=5', None, 'b'),
                 (None, 'androidboot.slot_suffix=_b', b'androidboot.slot_suffix=_a\0', 'b'),
                 ('b\n', 'androidboot.slot_suffix=_a', None, 'b')]       # what the initramfs published wins
        for shell in self.each_shell():
            for v, cl, ba, want in cases:
                for p, data in ((f, v), (cmdline, cl), (bootargs, ba)):
                    p.unlink(missing_ok=True)
                    if data is not None:
                        p.write_bytes(data if isinstance(data, bytes) else data.encode())
                self.assertEqual(self.up(shell, 'linux_slot').stdout.strip(), want, (v, cl, ba))

    def test_rollback_stays_on_its_slot(self):
        # the kept image was taken from the slot Linux ran from then (no prev.slot: b); after Linux moved to a,
        # rolling back would put an init that knows only slot b onto a - refused before anything is written
        boot = self.disk / 'boot'
        boot.mkdir()
        (boot / 'prev.body').write_bytes(b'old image')
        f = self.root / 'run/mu300/linux-slot'
        for shell in self.each_shell():
            for prev, now, refused in ((None, 'a', True), ('b', 'a', True), ('a', 'b', True), ('a', 'a', False),
                                       (None, 'b', False), ('b', 'b', False)):
                (boot / 'prev.slot').unlink(missing_ok=True)
                if prev:
                    (boot / 'prev.slot').write_text(prev + '\n')
                f.write_text(now + '\n')
                r = self.up(shell, 'part_dev() { echo "PART $1" >&2; return 1; }; rollback_boot')
                self.assertNotEqual(r.returncode, 0)
                if refused:
                    self.assertIn('was taken from slot', r.stderr, (prev, now))
                    self.assertNotIn('PART', r.stderr)
                else:
                    self.assertIn(f'PART boot_{now}', r.stderr, (prev, now))

    def test_boot_update_keeps_the_slot_of_the_image(self):
        src = (BIN / 'mu300-update').read_text()
        self.assertIn('linux_slot > "$BOOTDIR/prev.slot"', src)
        self.assertLess(src.index('cp "$tmp/body" "$BOOTDIR/prev.body"'),
                        src.index('linux_slot > "$BOOTDIR/prev.slot"'))

    def test_slot_a_needs_a_slot_aware_bundle(self):
        # an older bundle's init knows only slot b: on Linux-on-a it would restore the wrong block and log into
        # Android's boot partition
        old = self.bundle('old.tar.gz', 'f50\n', 'sdcard\n')
        new = self.bundle('new.tar.gz', 'f50\n', 'sdcard\nlinux-slot\n')
        f = self.root / 'run/mu300/linux-slot'
        for shell in self.each_shell():
            for slot, b, ok in (('a', old, False), ('a', new, True), ('b', old, True), ('b', new, True)):
                f.write_text(slot + '\n')
                d = self.tmp / 'x'
                d.mkdir(exist_ok=True)
                r = self.up(shell, f'tar -tzf "{b}" > "{d}/list"; '
                                   f'bundle_fits_slot "{b}" "{d}/list" && echo OK || echo REFUSE')
                self.assertEqual(r.stdout.strip(), 'OK' if ok else 'REFUSE', (slot, b.name, r.stderr))

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


# Update's setUp, device() and up() live in UpdateBase, which has no tests; Update and FromStock both derive from it
# (deriving from Update would run its tests twice).
@unittest.skipIf(not HAVE_LZ4, 'neither the lz4 command nor the lz4 Python module is installed')
class FromStock(UpdateBase):
    """bootimg_from_stock gives the bytes boot/build-boot-image.py gives, from the same stock image, kernel and
    ramdisk: the header page of Android's image with the new sizes, loglevel=5 and no signature, its vbmeta, its
    footer with the new sizes."""

    def python_image(self, stock):
        mods = self.tmp / 'mods'; mods.mkdir(exist_ok=True)
        for n in (BOOT / 'module-order.txt').read_text().split():
            (mods / n).write_bytes(b'\x7fELF ' + n.encode())
        for f in ('busybox', 'logdw'):
            (self.tmp / f).write_bytes(b'\x7fELF ' + f.encode())
        fake_misc(self.tmp / 'misc.bin')
        (self.tmp / 'Image').write_bytes(b'\x7fkernel' * 12345)
        out = self.tmp / 'py.img'
        r = subprocess.run([sys.executable, str(BOOT / 'build-boot-image.py'), '--stock-boot', str(stock),
                            '--misc-head', str(self.tmp / 'misc.bin'), '--kernel', str(self.tmp / 'Image'),
                            '--modules', str(mods), '--busybox', str(self.tmp / 'busybox'), '--logdw', str(self.tmp / 'logdw'),
                            '--ueventd-perms', str(TOP / 'android-vendor' / 'ueventd-perms.sh'), '--out', str(out)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        img = out.read_bytes()
        ksz, rsz = struct.unpack_from('<II', img, 8)
        roff = 4096 + (ksz + 4095) // 4096 * 4096
        (self.tmp / 'ramdisk').write_bytes(img[roff:roff + rsz])
        osz, _, vbs = struct.unpack_from('>QQQ', img, len(img) - 64 + 12)
        return img[:osz + vbs], img[-64:]

    def check(self, **stock_args):
        stock = self.tmp / 'stock.img'
        fake_stock(stock, **stock_args)
        head, footer = self.python_image(stock)
        d = self.tmp / 'out'
        for shell in self.each_shell():
            if d.exists():
                shutil.rmtree(d)
            d.mkdir()
            r = self.up(shell, f'bootimg_from_stock "{stock}" "{self.tmp}/Image" "{self.tmp}/ramdisk" "{d}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((d / 'new').read_bytes() + (d / 'vbmeta').read_bytes(), head, shell)
            self.assertEqual((d / 'newfooter').read_bytes(), footer, shell)

    def test_same_bytes_as_the_python_builder(self):
        self.check()

    def test_unaligned_vbmeta(self):
        self.check(vbmeta_offset=(1 << 20) + 100)

    def test_cmdline_is_loglevel_5_whatever_the_stock_one_is(self):
        # the Android-side switch arms a slot only when it sees this marker; stock headers have it empty, but a
        # stock image with a command line of its own must not leak it into the Linux image
        stock = self.tmp / 'stock.img'
        fake_stock(stock)
        raw = bytearray(stock.read_bytes())
        raw[44:44 + 1536] = b'console=ttyMSM0 androidboot.x=1'.ljust(1536, b'\0')
        stock.write_bytes(bytes(raw))
        (self.tmp / 'k').write_bytes(b'k'); (self.tmp / 'r').write_bytes(b'r')
        d = self.tmp / 'out'
        for shell in self.each_shell():
            if d.exists():
                shutil.rmtree(d)
            d.mkdir()
            r = self.up(shell, f'bootimg_from_stock "{stock}" "{self.tmp}/k" "{self.tmp}/r" "{d}"')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((d / 'new').read_bytes()[44:44 + 1536], b'loglevel=5'.ljust(1536, b'\0'), shell)

    def test_refuses_what_is_not_a_v4_image(self):
        bad = self.tmp / 'bad.img'
        bad.write_bytes(bytes(1 << 20))
        (self.tmp / 'k').write_bytes(b'k'); (self.tmp / 'r').write_bytes(b'r')
        for shell in self.each_shell():
            r = self.up(shell, f'bootimg_from_stock "{bad}" "{self.tmp}/k" "{self.tmp}/r" "{self.tmp}"')
            self.assertNotEqual(r.returncode, 0)

    def test_modules_into_every_system(self):
        b = self.tmp / 'bundle'
        (b / 'modules').mkdir(parents=True)
        (b / 'modules' / 'a.ko').write_bytes(b'a')
        (b / 'kernel.release').write_text('6.18.55-mu300\n')
        (b / 'modules.builtin').write_text('kernel/x.ko\n')
        (self.disk / 'openwrt').mkdir()
        for shell in self.each_shell():
            r = self.up(shell, f'kernel_modules_into_systems "{b}"', MU300_NO_DEPMOD=1)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.disk / 'ubuntu/lib/modules/6.18.55-mu300/extra/a.ko').read_bytes(), b'a')
            self.assertTrue((self.disk / 'ubuntu/lib/modules/6.18.55-mu300/modules.builtin').exists())
            self.assertEqual((self.disk / 'ubuntu/lib/modules/6.18.55-mu300/modules.order').read_bytes(), b'')
            self.assertEqual((self.disk / 'openwrt/lib/modules/6.18.55-mu300/a.ko').read_bytes(), b'a')
        (b / 'kernel.release').write_text('../evil\n')
        for shell in self.each_shell():
            self.assertNotEqual(self.up(shell, f'kernel_modules_into_systems "{b}"', MU300_NO_DEPMOD=1).returncode, 0)
