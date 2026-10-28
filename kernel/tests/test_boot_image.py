"""kernel/initramfs/build-boot-image.py: the generic ramdisk segment (what every kernel bundle ships and mu300-update
puts on the device) holds init, busybox, logdw, e2fsck and the modules of initramfs/module-order.txt; a whole boot image
adds the bootloader_control blocks of both slots."""
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

from helpers import TOP

try:
    import lz4.block
except ImportError:
    lz4 = None
HAVE_LZ4 = lz4 is not None or shutil.which('lz4') is not None

BOOT = TOP / 'kernel' / 'initramfs'
RELEASE = '7.2.11-mu300'


def unlz4_legacy(data):
    """Concatenated LZ4 legacy frames (magic 0x184C2102, then blocks of <u32 size><data>) -> bytes."""
    if lz4 is None:
        return subprocess.run(['lz4', '-dc'], input=data, capture_output=True, check=True).stdout
    out, i = b'', 0
    while i < len(data):
        assert data[i:i + 4] == b'\x02\x21\x4c\x18', 'not an LZ4 legacy frame'
        i += 4
        while i + 4 <= len(data) and data[i:i + 4] != b'\x02\x21\x4c\x18':
            n = int.from_bytes(data[i:i + 4], 'little')
            i += 4
            out += lz4.block.decompress(data[i:i + n], uncompressed_size=8 << 20)
            i += n
    return out


def cpio_files(data):
    """newc cpio -> {name: (mode, data)}"""
    files, i = {}, 0
    while True:
        h = data[i:i + 110]
        assert h[:6] == b'070701', 'not newc'
        mode = int(h[14:22], 16)
        size = int(h[54:62], 16)
        nlen = int(h[94:102], 16)
        name = data[i + 110:i + 110 + nlen - 1].decode()
        i = (i + 110 + nlen + 3) & ~3
        body = data[i:i + size]
        i = (i + size + 3) & ~3
        if name == 'TRAILER!!!':
            return files
        files[name] = (mode, body)


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-bootimg-'))
        self.mods = self.tmp / 'modules'
        self.mods.mkdir()
        for n in (BOOT / 'module-order.txt').read_text().split():
            (self.mods / n).write_bytes(b'\x7fELF ' + n.encode() + b'\0vermagic=' + RELEASE.encode() + b' SMP\0')
        for f in ('busybox', 'logdw'):
            (self.tmp / f).write_bytes(b'\x7fELF ' + f.encode())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, *extra):
        out = self.tmp / 'ramdisk-generic.lz4'
        r = subprocess.run([sys.executable, str(BOOT / 'build-boot-image.py'), '--generic-ramdisk',
                            '--modules', str(self.mods), '--busybox', str(self.tmp / 'busybox'),
                            '--logdw', str(self.tmp / 'logdw'),
                            '--ueventd-perms', str(TOP / 'kernel' / 'initramfs' / 'ueventd-perms.sh'),
                            '--out', str(out), *extra], capture_output=True, text=True)
        return r, out


@unittest.skipIf(not HAVE_LZ4, 'neither the lz4 command nor the lz4 Python module is installed')
class GenericRamdisk(Fixtures):
    def test_base(self):
        r, out = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        files = cpio_files(unlz4_legacy(out.read_bytes()))
        self.assertEqual(files['init'][1], (BOOT / 'init').read_bytes())
        self.assertTrue(files['init'][0] & 0o111, 'init is not executable')
        self.assertEqual(files['etc/module-order'][1], (BOOT / 'module-order.txt').read_bytes())
        for n in (BOOT / 'module-order.txt').read_text().split():
            self.assertIn('linux-modules/' + n, files)
        self.assertNotIn('etc/mu300-device', files)
        self.assertEqual(files['bin/busybox'][1], b'\x7fELF busybox')
        self.assertEqual(files['bin/logdw'][1], b'\x7fELF logdw')

    def test_e2fsck(self):
        # init's e2fsck for a root filesystem whose superblock records an error; none without it
        self.assertNotIn('sbin/e2fsck', cpio_files(unlz4_legacy(self.build()[1].read_bytes())))
        (self.tmp / 'e2fsck').write_bytes(b'\x7fELF e2fsck')
        r, out = self.build('--e2fsck', str(self.tmp / 'e2fsck'))
        self.assertEqual(r.returncode, 0, r.stderr)
        files = cpio_files(unlz4_legacy(out.read_bytes()))
        self.assertEqual(files['sbin/e2fsck'][1], b'\x7fELF e2fsck')
        self.assertTrue(files['sbin/e2fsck'][0] & 0o111, 'e2fsck is not executable')

    def test_crlf_checkout(self):
        # a Windows clone with core.autocrlf=true (issue #7): the device's shell must still get LF
        crlf = {}
        for name, src in (('init', BOOT / 'init'), ('perms', TOP / 'kernel' / 'initramfs' / 'ueventd-perms.sh'),
                          ('order', BOOT / 'module-order.txt')):
            crlf[name] = self.tmp / name
            crlf[name].write_bytes(src.read_bytes().replace(b'\n', b'\r\n'))
        r, out = self.build('--init', str(crlf['init']), '--ueventd-perms', str(crlf['perms']),
                            '--module-order', str(crlf['order']))
        self.assertEqual(r.returncode, 0, r.stderr)
        files = cpio_files(unlz4_legacy(out.read_bytes()))
        self.assertEqual(files['init'][1], (BOOT / 'init').read_bytes())
        self.assertEqual(files['etc/ueventd-perms.sh'][1], (TOP / 'kernel' / 'initramfs' / 'ueventd-perms.sh').read_bytes())
        self.assertEqual(files['etc/module-order'][1], (BOOT / 'module-order.txt').read_bytes())

    def test_generic_refuses_device_settings(self):
        # the generic segment is the same for every device: it must not name one, nor a slot
        for extra in (['--device', 'u30air'], ['--linux-slot', 'a']):
            r, _ = self.build(*extra)
            self.assertNotEqual(r.returncode, 0, extra)

    def test_missing_module(self):
        (self.mods / (BOOT / 'module-order.txt').read_text().split()[0]).unlink()
        r, _ = self.build()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('missing module', r.stderr)


def fake_stock(path, size=4 << 20, vbmeta_offset=1 << 20, vbmeta=b'V' * 2304):
    """An Android boot image header v4 with an AVB footer, as far as build-boot-image.py and mu300-update read it."""
    img = bytearray(size)
    img[0:8] = b'ANDROID!'
    struct.pack_into('<I', img, 40, 4)
    img[4096:8192] = b'k' * 4096
    img[vbmeta_offset:vbmeta_offset + len(vbmeta)] = vbmeta
    footer = bytearray(64)
    footer[0:4] = b'AVBf'
    struct.pack_into('>IIQQQ', footer, 4, 1, 0, vbmeta_offset, vbmeta_offset, len(vbmeta))
    img[-64:] = footer
    path.write_bytes(bytes(img))


# the slot-a block of the F50 test board: AOSP bootloader_control, nothing device-specific in it
LIVE_A = bytes.fromhex('5f61000042434142010200009f001e000000000000000000000000000be17146')


def fake_misc(path, live=LIVE_A):
    path.write_bytes(bytes(0x800) + live + bytes(4096 - 0x800 - 32))


def with_slots(live, suffix, a, b):
    x = bytearray(live)
    x[0:4] = suffix
    x[12] = a
    x[14] = b
    x[28:32] = struct.pack('<I', zlib.crc32(bytes(x[:28])))
    return bytes(x)


@unittest.skipIf(not HAVE_LZ4, 'neither the lz4 command nor the lz4 Python module is installed')
class SlotBlocks(Fixtures):
    def image(self, *extra):
        fake_stock(self.tmp / 'stock.img')
        fake_misc(self.tmp / 'misc.bin')
        (self.tmp / 'Image').write_bytes(b'\x7fkernel' * 100)
        out = self.tmp / 'boot.img'
        r = subprocess.run([sys.executable, str(BOOT / 'build-boot-image.py'), '--stock-boot', str(self.tmp / 'stock.img'),
                            '--misc-head', str(self.tmp / 'misc.bin'), '--kernel', str(self.tmp / 'Image'),
                            '--modules', str(self.mods), '--busybox', str(self.tmp / 'busybox'),
                            '--logdw', str(self.tmp / 'logdw'), '--device', 'f50',
                            '--ueventd-perms', str(TOP / 'kernel' / 'initramfs' / 'ueventd-perms.sh'),
                            '--out', str(out), *extra], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        img = out.read_bytes()
        ksz, rsz = struct.unpack_from('<II', img, 8)
        roff = 4096 + (ksz + 4095) // 4096 * 4096
        return cpio_files(unlz4_legacy(img[roff:roff + rsz]))

    def test_all_four_blocks(self):
        files = self.image()
        want = {'etc/misc-bc-slot-a.bin': with_slots(LIVE_A, b'_a\0\0', 0x9f, 0x1e),
                'etc/misc-bc-slot-b-trial.bin': with_slots(LIVE_A, b'_b\0\0', 0x9e, 0x2f),
                'etc/misc-bc-slot-b.bin': with_slots(LIVE_A, b'_b\0\0', 0x1e, 0x9f),
                'etc/misc-bc-slot-a-trial.bin': with_slots(LIVE_A, b'_a\0\0', 0x2f, 0x9e)}
        for name, data in want.items():
            self.assertEqual(files[name][1], data, name)

    def test_slot_default_and_choice(self):
        self.assertEqual(self.image()['etc/mu300-linux-slot'][1], b'b\n')
        self.assertEqual(self.image('--linux-slot', 'a')['etc/mu300-linux-slot'][1], b'a\n')

    def test_trial_block_next_to_the_image_is_the_one_of_its_slot(self):
        # the block beside the image is what goes to misc: for Linux on a it must arm a, and no block that arms b
        # (the slot Android runs from then) may lie next to it
        for slot, other, block in (('b', 'a', with_slots(LIVE_A, b'_b\0\0', 0x9e, 0x2f)),
                                   ('a', 'b', with_slots(LIVE_A, b'_a\0\0', 0x2f, 0x9e))):
            for f in self.tmp.glob('boot.misc-slot-*'):
                f.unlink()
            self.image('--linux-slot', slot)
            self.assertEqual((self.tmp / f'boot.misc-slot-{slot}-trial.bin').read_bytes(), block, slot)
            self.assertFalse((self.tmp / f'boot.misc-slot-{other}-trial.bin').exists(), slot)
            self.assertEqual(json.loads((self.tmp / 'boot.json').read_text())['linux_slot'], slot)

    def test_header_says_linux(self):
        # loglevel=5 in the header's command line tells a Linux image from an Android one (after an OTA the other
        # slot holds the previous Android); mu300-update keeps the header page as it is
        for extra in ((), ('--linux-slot', 'a')):
            self.image(*extra)
            hdr = (self.tmp / 'boot.img').read_bytes()[:4096]
            self.assertEqual(hdr[44:55], b'loglevel=5\0', extra)

    def test_the_manifest_counts_the_modules(self):
        self.image()
        manifest = json.loads((self.tmp / 'boot.json').read_text())
        self.assertEqual(manifest['modules'], len((BOOT / 'module-order.txt').read_text().split()))

    def test_generic_has_no_slot(self):
        r, _ = self.build('--linux-slot', 'a')
        self.assertNotEqual(r.returncode, 0)


class CpioParents(unittest.TestCase):
    """every entry's parent directories come before it: the kernel's unpacker does not create them (kanoqwq's
    b8d4f222); an appended segment that names only a file gets its directory too"""

    def test_parents_first(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('bbi', TOP / 'kernel' / 'initramfs' / 'build-boot-image.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        raw = mod.cpio_archive({'dev'}, {'etc/mu300/linux-slot': (b'a\n', 0o100644), 'init': (b'#!/bin/sh\n', 0o100755)})
        order, i = [], 0
        while True:
            h = raw[i:i + 110]
            self.assertEqual(h[:6], b'070701')
            mode, size, nlen = int(h[14:22], 16), int(h[54:62], 16), int(h[94:102], 16)
            name = raw[i + 110:i + 110 + nlen - 1].decode()
            i = (i + 110 + nlen + 3) & ~3
            i = (i + size + 3) & ~3
            if name == 'TRAILER!!!':
                break
            order.append((name, mode))
        names = [n for n, _ in order]
        for name in names:
            if '/' in name:
                self.assertIn(name.rsplit('/', 1)[0], names[:names.index(name)], name)
        self.assertEqual(dict(order)['etc/mu300'] & 0o170000, 0o040000)
        self.assertEqual(len(names), len(set(names)))

class ModuleOrder(unittest.TestCase):
    def test_order(self):
        order = (BOOT / 'module-order.txt').read_text().split()
        self.assertEqual(len(order), len(set(order)), 'a module is listed twice')
        # dependencies first: the modem stack's transport before its users, the PMIC watchdog last
        for a, b in (('sipc-core.ko', 'spipe.ko'), ('sprd_modem_loader.ko', 'sipa-core.ko'),
                     ('sipa-core.ko', 'sipa_eth.ko'), ('sipc-core.ko', 'sprd_ddr_dfs.ko')):
            self.assertLess(order.index(a), order.index(b), f'{a} must load before {b}')
        self.assertEqual(order[-1], 'sprd_pmic_wdt.ko')


if __name__ == '__main__':
    unittest.main()
