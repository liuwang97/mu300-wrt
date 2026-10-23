#!/usr/bin/env python3
"""The SAE hostapd APKs in a directory: built from the sources here (build-id), complete and unchanged (SHA256SUMS).

    wifi-sae-state.py DIR        check DIR, print its build id (exit 1 with the reason otherwise)
    wifi-sae-state.py --build-id print the build id of the sources here
"""
from pathlib import Path
import hashlib
import re
import sys

HERE = Path(__file__).resolve().parent
# the build id is the sha256 of these files, in this order: any change rebuilds the APKs
SOURCES = ('build-hostapd.sh', 'build-hostapd-inner.sh', 'hostapd.Dockerfile', 'patch-hostapd-sae.py',
           'hostapd-unisoc-sae.h', 'hostapd-unisoc.sh', 'hostapd-unisoc.uc', 'wifi-sae-state.py')
PACKAGES = ('wpad-basic-mbedtls', 'hostapd-common', 'wifi-scripts')
RELEASE = 'r1001'


def build_id():
    return hashlib.sha256(b''.join((HERE / n).read_bytes() for n in SOURCES)).hexdigest()


def inspect(directory):
    directory = Path(directory)
    if (directory / 'build-id').read_text().strip() != build_id():
        raise ValueError('the SAE hostapd APKs are stale: run openwrt/hostapd/build-hostapd.sh')
    expected = {}
    for line in (directory / 'SHA256SUMS').read_text().splitlines():
        checksum, name = line.split()
        name = name.removeprefix('./')
        if not re.fullmatch(r'(?:%s)-[a-zA-Z0-9.~+-]+-%s\.apk' % ('|'.join(PACKAGES), RELEASE), name):
            raise ValueError('unexpected APK: ' + name)
        if name in expected or not re.fullmatch(r'[a-f0-9]{64}', checksum):
            raise ValueError('invalid SHA256SUMS')
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != checksum:
            raise ValueError('checksum mismatch: ' + name)
        expected[name] = checksum
    for pkg in PACKAGES:
        if sum(n.startswith(pkg + '-') for n in expected) != 1:
            raise ValueError('expected one APK of ' + pkg)
    if len(expected) != len(PACKAGES) or {p.name for p in directory.glob('*.apk')} != set(expected):
        raise ValueError('unexpected extra APKs')
    return {'build_id': build_id(), 'apks': expected}


if __name__ == '__main__':
    if sys.argv[1:] == ['--build-id']:
        print(build_id())
    elif len(sys.argv) == 2 and not sys.argv[1].startswith('-'):
        try:
            print(inspect(sys.argv[1])['build_id'])
        except (OSError, ValueError) as e:
            sys.exit(f'{sys.argv[1]}: {e}')
    else:
        sys.exit(__doc__)
