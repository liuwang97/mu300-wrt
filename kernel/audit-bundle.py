#!/usr/bin/env python3
"""Check the Image's embedded config and every expected in-tree module before packaging."""
import gzip
import os
import pathlib
import sys


def audit(out, fragment):
    """fragment: mu300-mainline.config; all-modules.report next to the Image (what all-modules.py added) is checked
    the same way when the build left one."""
    image = (out / 'Image').read_bytes()
    start = image.find(b'IKCFG_ST')
    end = image.find(b'IKCFG_ED', start + 8)
    if start < 0 or end < 0:
        raise ValueError('Image has no embedded kernel config')
    config = gzip.decompress(image[start + 8:end]).decode()
    actual = dict(line.split('=', 1) for line in config.splitlines() if line.startswith('CONFIG_'))
    wanted = fragment.read_text().splitlines()
    if (out / 'all-modules.report').is_file():
        wanted += (out / 'all-modules.report').read_text().splitlines()
    for line in wanted:
        if not line.startswith('CONFIG_'):
            continue
        key, value = line.split('=', 1)
        got = actual.get(key, 'n')
        if got != value:
            raise ValueError(f'Image: {key} wants {value}, got {got}')
    expected = (out / 'modules.in-tree').read_text().splitlines()
    if not expected:
        raise ValueError('empty in-tree module manifest')
    # by the names the directory lists, not is_file(): on a file system that ignores case xt_hl.ko "is" a file when
    # only xt_HL.ko is there
    present = set(os.listdir(out / 'modules'))
    for name in expected:
        if pathlib.Path(name).name != name or not name.endswith('.ko'):
            raise ValueError(f'invalid module name: {name}')
        if name not in present or not (out / 'modules' / name).is_file():
            folded = [p for p in present if p.lower() == name.lower()]
            raise ValueError(f'missing in-tree module: {name}' +
                             (f' (only {folded[0]}: built on a file system that ignores case?)' if folded else ''))
    (out / 'kernel.config').write_text(config)
    print(f'Image config verified; {len(expected)} in-tree modules present; KVM built in')


if __name__ == '__main__':
    try:
        audit(pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]))
    except (ValueError, OSError) as exc:
        sys.exit(str(exc))
