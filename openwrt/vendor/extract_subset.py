#!/usr/bin/env python3
"""Pull the Android vendor runtime needed by modem_control from a rooted device (adb + su) and build the chroot
subset used by the initramfs (/android) and the root filesystem (/opt/mu300/android).

    python3 android-vendor/extract_subset.py [OUTDIR]

These files are proprietary (ZTE/Unisoc/Google) and are NOT part of this repository. Same output as the older
extract-subset.sh; written in Python so it also runs on Windows.
"""
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

# what to ask the device for (dereferencing symlinks), and what to keep of it
PULL = [
    '/apex/com.android.runtime', '/system/lib64', '/vendor/bin/modem_control', '/vendor/bin/cp_diskserver',
    '/vendor/bin/refnotify', '/vendor/lib64/lib_crypto.so', '/vendor/bin/sh', '/vendor/bin/toybox_vendor',
    '/vendor/bin/getprop', '/vendor/lib64/libkernelbootcp.trusty.so', '/vendor/etc', '/dev/__properties__',
]
KEEP_PREFIXES = [
    'apex/com.android.runtime/bin/linker64', 'apex/com.android.runtime/lib64/bionic/',
    'vendor/bin/modem_control', 'vendor/bin/cp_diskserver', 'vendor/bin/refnotify', 'vendor/bin/sh',
    'vendor/bin/toybox_vendor', 'vendor/bin/getprop', 'vendor/lib64/libkernelbootcp.trusty.so',
    'vendor/lib64/lib_crypto.so', 'dev/__properties__/',
]
KEEP_EXACT = {
    'vendor/etc/modem_cp_info.xml', 'vendor/etc/modem_sp_info.xml', 'vendor/etc/modem_ch_info.xml',
    'vendor/etc/cp_dump_info.xml', 'vendor/etc/ueventd.rc', 'dev/__properties__',
    # refnotify asks for this RF/Wi-Fi coexistence table; the F50 firmware does not ship it (stock Android logs
    # the same error), so it is copied only when a device happens to have it
    'vendor/etc/wcn_to_mipi.xml',
}
KEEP_EXACT |= {'system/lib64/' + n for n in (
    'libcutils.so', 'libexpat.so', 'liblog.so', 'libhardware_legacy.so', 'libc++.so', 'libbase.so', 'libbinder.so',
    'libbinder_ndk.so', 'libhidlbase.so', 'libutils.so', 'android.system.suspend-V1-ndk.so', 'libtrusty.so',
    'libandroid_runtime_lazy.so', 'libvndksupport.so', 'libz.so', 'libcrypto.so', 'libselinux.so', 'libpcre2.so',
    'libpackagelistparser.so', 'libprocessgroup.so', 'libcgrouprc.so')}


def wanted(name):
    n = name.lstrip('./')
    return n in KEEP_EXACT or any(n.startswith(p) for p in KEEP_PREFIXES)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else 'android-subset').resolve()
    cmd = 'tar -chf - ' + ' '.join(PULL) + ' 2>/dev/null'
    with tempfile.TemporaryDirectory() as tmp:
        blob = Path(tmp) / 'vendor.tar'
        # Build the tar on the device and pull it as a file. Streaming it through `adb exec-out "su -c ..."`
        # is what the comment here used to claim was binary-clean, and on some devices it is not: su gives
        # the command a pty whose ONLCR turns every LF into CRLF, and the archive arrives corrupt (issue #2).
        devtar = '/data/local/tmp/mu300-subset.tar'
        subprocess.run(['adb', 'shell', f"su -c 'tar -chf {devtar} " + ' '.join(PULL) + " 2>/dev/null'"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        p = subprocess.run(['adb', 'pull', devtar, str(blob)],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['adb', 'shell', f"su -c 'rm -f {devtar}'"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        if p.returncode != 0 or not blob.exists() or blob.stat().st_size < 1_000_000:
            sys.exit('pulling the vendor files failed (is the device in rooted Android?)')
        # Everything is checked before the output directory is touched: a half-done run used to leave an
        # empty directory behind, and the callers' "skip if it exists" guard then skipped it for ever.
        with tarfile.open(blob) as tar:
            members = [m for m in tar.getmembers() if wanted(m.name)]
            if not members:
                sys.exit('the archive from the device contains none of the expected files')
            if out.exists():
                import shutil
                shutil.rmtree(out)
            out.mkdir(parents=True)
            for m in members:
                m.name = m.name.lstrip('./')
            kw = {'filter': 'fully_trusted'} if sys.version_info >= (3, 12) else {}
            tar.extractall(out, members=members, **kw)
    # the linker is looked up under /system/bin inside the chroot, and bionic needs a (empty) linker config
    (out / 'system' / 'bin').mkdir(parents=True, exist_ok=True)
    link = out / 'system' / 'bin' / 'linker64'
    if not link.is_symlink():
        link.symlink_to('/apex/com.android.runtime/bin/linker64')
    (out / 'linkerconfig').mkdir(exist_ok=True)
    (out / 'linkerconfig' / 'ld.config.txt').write_bytes(b'')
    size = sum(f.stat().st_size for f in out.rglob('*') if f.is_file() and not f.is_symlink())
    print(f'{out}: {size // 1024 // 1024} MiB')


if __name__ == '__main__':
    main()
