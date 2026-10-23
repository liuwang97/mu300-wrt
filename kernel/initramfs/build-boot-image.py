#!/usr/bin/env python3
"""Build the boot ramdisk of the ZTE F50 and U30 Air (Unisoc T760, UMS9620), or a whole Linux boot image.

--generic-ramdisk writes the device-independent ramdisk segment of a kernel bundle (init, busybox, logdw, e2fsck,
the modules of module-order.txt): mu300-update puts it behind the device segment of the boot image on the device.

Without it, a whole boot image for the Linux slot: the stock boot image header and AVB footer layout (the device runs
a boot-verification bypass, the AVB descriptor is kept as-is), this kernel and ramdisk, and the bootloader_control
blocks that init writes back to misc so that the next reboot returns to Android's slot. Its inputs are the owner's own
dumps; nothing device-specific is embedded in this script.

Example:
  build-boot-image.py --generic-ramdisk --modules out/modules --busybox tools/busybox --logdw tools/logdw \
    --e2fsck tools/e2fsck --ueventd-perms kernel/initramfs/ueventd-perms.sh --out ramdisk-generic.lz4
"""
import argparse
import hashlib
import json
import os
import stat
import struct
import subprocess
import sys
import zlib
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
PAGE = 4096
BOOT_CMDLINE = b'loglevel=5'
MISC_BC_OFFSET = 0x800
# persistent init log lives at 48 MiB inside boot_b (8 MiB); the image must end before it
PERSIST_LOG_OFFSET = 48 << 20


def cpio_record(name, data, mode, ino, rdev=(0, 0)):
    nb = name.encode() + b'\0'
    fields = [ino, mode, 0, 0, 1, 0, len(data), 0, 0, rdev[0], rdev[1], len(nb), 0]
    h = b'070701' + b''.join(('%08x' % v).encode() for v in fields)
    x = h + nb
    x += b'\0' * ((-len(x)) % 4)
    x += data
    x += b'\0' * ((-len(data)) % 4)
    return x


def lz4_legacy(data):
    """LZ4 legacy frame, the format vendor_boot uses. Prefers the lz4 command, falls back to the lz4 Python module
    (pip install lz4) on hosts without the command line tool."""
    try:
        return subprocess.run(['lz4', '-l', '-12', '-c'], input=data, capture_output=True, check=True).stdout
    except FileNotFoundError:
        pass
    try:
        import lz4.block
    except ImportError:
        sys.exit("need the 'lz4' command or the lz4 Python module (pip install lz4)")
    # legacy frame: magic, then for every 8 MiB of input a little-endian block length followed by the LZ4 block
    out = bytearray(bytes.fromhex('02214c18'))
    for i in range(0, len(data), 8 << 20):
        block = lz4.block.compress(data[i:i + (8 << 20)], mode='high_compression', compression=12, store_size=False)
        out += struct.pack('<I', len(block)) + block
    return bytes(out)


def unlz4_legacy(data):
    """LZ4 legacy frames (the magic, then blocks of <u32 length><LZ4 block>) back to bytes: the lz4 command, else the
    lz4 Python module, as lz4_legacy."""
    try:
        return subprocess.run(['lz4', '-dc'], input=data, capture_output=True, check=True).stdout
    except FileNotFoundError:
        pass
    except subprocess.CalledProcessError:
        sys.exit('a ramdisk segment does not decompress')
    try:
        import lz4.block
    except ImportError:
        sys.exit("need the 'lz4' command or the lz4 Python module (pip install lz4)")
    out, i = bytearray(), 0
    while i < len(data):
        if data[i:i + 4] != bytes.fromhex('02214c18'):
            sys.exit('a ramdisk segment is not LZ4 legacy')
        i += 4
        while i + 4 <= len(data) and data[i:i + 4] != bytes.fromhex('02214c18'):
            n = struct.unpack_from('<I', data, i)[0]
            out += lz4.block.decompress(data[i + 4:i + 4 + n], uncompressed_size=8 << 20)
            i += 4 + n
    return bytes(out)


def cpio_names(data):
    """The names of a newc cpio archive (a ramdisk segment, decompressed)."""
    names, i = set(), 0
    while i + 110 <= len(data) and data[i:i + 6] == b'070701':
        size, nlen = int(data[i + 54:i + 62], 16), int(data[i + 94:i + 102], 16)
        name = data[i + 110:i + 110 + nlen - 1].decode('utf-8', 'replace')
        if name == 'TRAILER!!!':
            break
        names.add(name)
        i += 110 + nlen
        i += (-i) % 4
        i += size + (-size) % 4
    return names


def cpio_archive(dirs, files):
    # every parent directory of an entry goes in first: the kernel's unpacker does not create missing parents, so a
    # file under a directory no entry names would be dropped (kanoqwq's b8d4f222). An appended archive whose
    # directories the main one created gets them again, which is harmless.
    dirs = set(dirs)
    for name in list(files) + list(dirs):
        dirs.update(str(p) for p in PurePosixPath(name).parents if str(p) != '.')
    cpio = bytearray()
    ino = 1
    for d in sorted(dirs, key=lambda x: (x.count('/'), x)):
        cpio += cpio_record(d, b'', stat.S_IFDIR | 0o755, ino)
        ino += 1
    for name, (data, mode) in files.items():
        cpio += cpio_record(name, data, mode, ino)
        ino += 1
    # the kernel opens /dev/console before running /init
    cpio += cpio_record('dev/console', b'', stat.S_IFCHR | 0o600, ino, (5, 1))
    ino += 1
    cpio += cpio_record('TRAILER!!!', b'', 0, ino)
    return bytes(cpio)


def bootloader_control(misc_head):
    """Return (android_a, linux_b_trial, android_b, linux_a_trial) blocks derived from the live misc bootloader_control."""
    bc = misc_head[MISC_BC_OFFSET:MISC_BC_OFFSET + 32]
    if bc[4:8] != b'BCAB':
        sys.exit('misc head has no bootloader_control magic at 0x800')
    if zlib.crc32(bc[:28]) != struct.unpack('<I', bc[28:])[0]:
        sys.exit('misc bootloader_control CRC mismatch')

    def with_slots(suffix, a, b):
        x = bytearray(bc)
        x[0:4] = suffix
        x[12] = a
        x[14] = b
        x[28:32] = struct.pack('<I', zlib.crc32(bytes(x[:28])))
        return bytes(x)

    # slot_info byte: priority (4 bits) | tries_remaining (3 bits) | successful_boot (1 bit)
    # Unisoc LK treats tries==1 && !successful as an already failed boot, so a one-shot trial needs tries=2 (LK
    # decrements to 1; a failed boot then rolls back to Android's slot). Linux goes on the slot Android is not on,
    # and Android may be on either (after an OTA it is often b): one pair of blocks for each case.
    android_a = with_slots(b'_a\0\0', 0x9f, 0x1e)        # a: prio 15, successful
    linux_b_trial = with_slots(b'_b\0\0', 0x9e, 0x2f)    # a: prio 14 successful, b: prio 15 tries 2
    android_b = with_slots(b'_b\0\0', 0x1e, 0x9f)        # b: prio 15, successful
    linux_a_trial = with_slots(b'_a\0\0', 0x2f, 0x9e)    # b: prio 14 successful, a: prio 15 tries 2
    return android_a, linux_b_trial, android_b, linux_a_trial


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--stock-boot', type=Path, help='stock boot_a.img dump (64 MiB, header v4)')
    ap.add_argument('--misc-head', type=Path, help='first 4 KiB of the misc partition')
    ap.add_argument('--kernel', type=Path, help='the wrapped arm64 Image (wrap-image.py)')
    ap.add_argument('--generic-ramdisk', action='store_true',
                    help='write only the device-independent ramdisk segment (init, busybox, modules) to --out, for '
                         'mu300-update to put behind the ramdisk of the boot image already on the device')
    ap.add_argument('--modules', type=Path, required=True, help='flat directory with the built .ko files')
    ap.add_argument('--module-order', type=Path, default=HERE / 'module-order.txt')
    ap.add_argument('--device', help='the device this image is for (f50, u30air): written to /etc/mu300-device, '
                                     'which init trusts over its own guess from the device tree')
    ap.add_argument('--linux-slot', choices=['a', 'b'],
                    help='the slot this image goes to (default b; a when Android runs from slot b): written to '
                         '/etc/mu300-linux-slot, which init uses when the kernel command line does not say')
    ap.add_argument('--init', type=Path, default=HERE / 'init')
    ap.add_argument('--busybox', type=Path, required=True, help='static arm64 busybox (initramfs/tools.sh)')
    ap.add_argument('--logdw', type=Path, required=True, help='static arm64 logdw (initramfs/tools.sh)')
    ap.add_argument('--e2fsck', type=Path,
                    help='kernel/initramfs/e2fsck/build.sh build (static arm64): /sbin/e2fsck, which init runs on a root '
                         'filesystem whose superblock records an error; without it init boots such a one as it is')
    ap.add_argument('--ueventd-perms', required=True, type=Path)
    ap.add_argument('--android-subset', type=Path,
                    help="directory with the device's own Android files that init runs (modem_control and the rest)")
    ap.add_argument('--out', required=True, type=Path)
    a = ap.parse_args()
    if not a.generic_ramdisk and not (a.stock_boot and a.misc_head and a.kernel):
        ap.error('--stock-boot, --misc-head and --kernel are required unless --generic-ramdisk is given')

    def text(path):  # scripts and lists for the device's shell, LF whatever the checkout did (issue #7)
        return path.read_bytes().replace(b'\r\n', b'\n')

    files = {
        'init': (text(a.init), stat.S_IFREG | 0o755),
        'bin/sh': (b'busybox', stat.S_IFLNK | 0o777),
        'etc/ueventd-perms.sh': (text(a.ueventd_perms), stat.S_IFREG | 0o755),
        'bin/busybox': (a.busybox.read_bytes(), stat.S_IFREG | 0o755),
        'bin/logdw': (a.logdw.read_bytes(), stat.S_IFREG | 0o755),
        'etc/module-order': (text(a.module_order), stat.S_IFREG | 0o644),
    }
    if a.e2fsck:
        files['sbin/e2fsck'] = (a.e2fsck.read_bytes(), stat.S_IFREG | 0o755)
    order = a.module_order.read_text().split()
    dirs = {'bin', 'sbin', 'etc', 'proc', 'sys', 'dev', 'run', 'tmp', 'root', 'config', 'linux-modules'}
    for name in order:
        ko = a.modules / name
        if not ko.exists():
            sys.exit(f'missing module {ko}')
        files['linux-modules/' + name] = (ko.read_bytes(), stat.S_IFREG | 0o644)
    if a.linux_slot and a.generic_ramdisk:
        ap.error('--linux-slot does not go into a generic ramdisk')
    if a.device:
        # only in the device segment: a generic segment is the same for every device
        if a.generic_ramdisk:
            ap.error('--device does not go into a generic ramdisk')
        files['etc/mu300-device'] = (a.device.encode() + b'\n', stat.S_IFREG | 0o644)
    if a.generic_ramdisk:
        # The kernel unpacks concatenated ramdisk segments in turn and a later file replaces an earlier one of the
        # same name, so this segment behind the device's own ramdisk updates everything that is not the device's.
        ram = lz4_legacy(cpio_archive(dirs, files))
        a.out.write_bytes(ram)
        manifest = {
            'ramdisk': a.out.name,
            'sha256': hashlib.sha256(ram).hexdigest(),
            'size': len(ram),
            'modules': len(order),
            'init_sha256': hashlib.sha256(files['init'][0]).hexdigest(),
        }
        a.out.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print(json.dumps(manifest, indent=2))
        return

    base = a.stock_boot.read_bytes()
    if base[:8] != b'ANDROID!' or struct.unpack_from('<I', base, 40)[0] != 4:
        sys.exit('stock boot is not an Android boot image header v4')
    android_a_bc, linux_b_bc, android_b_bc, linux_a_bc = bootloader_control(a.misc_head.read_bytes())
    linux_slot = a.linux_slot or 'b'
    files['etc/misc-bc-slot-a.bin'] = (android_a_bc, stat.S_IFREG | 0o644)
    files['etc/misc-bc-slot-b-trial.bin'] = (linux_b_bc, stat.S_IFREG | 0o644)
    files['etc/misc-bc-slot-b.bin'] = (android_b_bc, stat.S_IFREG | 0o644)
    files['etc/misc-bc-slot-a-trial.bin'] = (linux_a_bc, stat.S_IFREG | 0o644)
    files['etc/mu300-linux-slot'] = (linux_slot.encode() + b'\n', stat.S_IFREG | 0o644)
    if a.android_subset:
        dirs.add('android')
        for f in sorted(a.android_subset.rglob('*')):
            rel = 'android/' + f.relative_to(a.android_subset).as_posix()
            if f.is_symlink():
                files[rel] = (os.readlink(f).encode(), stat.S_IFLNK | 0o777)
            elif f.is_dir():
                dirs.add(rel)
            else:
                files[rel] = (f.read_bytes(), stat.S_IFREG | (0o755 if os.access(f, os.X_OK) else 0o644))

    # vendor_boot's LZ4 legacy framing: LK hands the ramdisk on as it is, and the boot images on the device use it
    ram = lz4_legacy(cpio_archive(dirs, files))
    assert ram[:4] == bytes.fromhex('02214c18')

    kern = a.kernel.read_bytes()
    hdr = bytearray(base[:PAGE])
    struct.pack_into('<I', hdr, 8, len(kern))
    struct.pack_into('<I', hdr, 12, len(ram))
    hdr[44:44 + 1536] = BOOT_CMDLINE + b'\0' * (1536 - len(BOOT_CMDLINE))
    struct.pack_into('<I', hdr, 1580, 0)  # signature_size
    body = bytes(hdr) + kern + b'\0' * ((-len(kern)) % PAGE) + ram
    body += b'\0' * ((-len(body)) % PAGE)
    original_size = len(body)

    avb_off, avb_size = struct.unpack_from('>QQ', base, len(base) - 44)
    body += base[avb_off:avb_off + avb_size]
    if len(body) > PERSIST_LOG_OFFSET:
        sys.exit(f'image data ({len(body)} bytes) overlaps the persistent log area at 48 MiB')
    body += b'\0' * (len(base) - 64 - len(body))
    footer = bytearray(base[-64:])
    struct.pack_into('>QQQ', footer, 12, original_size, original_size, avb_size)
    image = body + bytes(footer)
    assert len(image) == len(base)

    a.out.write_bytes(image)
    # the block that arms this image as a one-shot trial, for the slot it goes to, to be written to misc
    # (only that one sits next to the image, so nothing can arm the slot Android runs from by mistake)
    a.out.with_suffix(f'.misc-slot-{linux_slot}-trial.bin').write_bytes(linux_a_bc if linux_slot == 'a' else linux_b_bc)
    manifest = {
        'image': a.out.name,
        'sha256': hashlib.sha256(image).hexdigest(),
        # boot_b's tail holds the persistent init log, so on-device checks compare only the first 48 MiB
        'sha256_head48m': hashlib.sha256(image[:PERSIST_LOG_OFFSET]).hexdigest(),
        'kernel_sha256': hashlib.sha256(kern).hexdigest(),
        'ramdisk_size': len(ram),
        'modules': len(order),
        'misc_slot_b_trial_hex': linux_b_bc.hex(),
        'misc_slot_a_hex': android_a_bc.hex(),
        'misc_slot_b_hex': android_b_bc.hex(),
        'misc_slot_a_trial_hex': linux_a_bc.hex(),
        'linux_slot': linux_slot,
    }
    a.out.with_suffix('.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
