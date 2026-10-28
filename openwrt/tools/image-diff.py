#!/usr/bin/env python3
"""The diff of a root filesystem image: the new image without the files an older release's image had as they are.

  openwrt/tools/image-diff.py make BASE.manifest.gz NEW-ROOTFS.tar.gz OUT.tar.gz    write the diff; prints its sizes
  openwrt/tools/image-diff.py check BASE.manifest.gz NEW-ROOTFS.tar.gz DIFF.tar.gz  the diff and the files of BASE it
                                                                    leaves out give NEW, entry for entry; exit 1 if not

The base is the manifest every release publishes (image-manifest.py); tools/make-diffs.sh makes the diffs while a
release is built, and the release's signed SHA256SUMS lists them like every other file of it.

A diff is a tar.gz of the new image's members in their order, the image's own etc/mu300/image-manifest.gz among them,
with the regular files left out that the base image had with the same manifest line (type, mode, owner, size, sha256)
- only where a device keeps them as the image brought them (OMIT_NOT: never etc, var, root, home, srv, usr/local or a
package database), never one with an extended attribute or a hard link, never the parts the update itself runs
(mu300-update's DELTA_TOOLS). Its first member, ./.mu300-diff, says what it is and what it left out:
  mu300-diff 1 IMAGE BASE_TAG NEW_TAG
  <the new manifest's line of each file left out>
mu300-update (the diff block) unpacks it, takes the files left out from the installed system when that system has
them byte for byte, else downloads the whole image, and installs nothing that is not, entry for entry, the image the
release's signed manifest describes.
"""
import gzip
import hashlib
import importlib.util
import io
import os
import sys
import tarfile

_spec = importlib.util.spec_from_file_location('image_manifest', os.path.join(os.path.dirname(__file__), 'image-manifest.py'))
im = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(im)

HEAD = '.mu300-diff'
# never left out: what the device changes or keeps as its own, and what the update runs from the new image
OMIT_NOT = ('etc/', 'var/', 'root/', 'home/', 'srv/', 'tmp/', 'run/', 'usr/local/', 'lib/apk/', 'usr/lib/opkg/')
TOOLS = {'opt/mu300/bin/mu300-carry', 'opt/mu300/bin/mu300-update', 'usr/bin/busybox', 'opt/mu300/bin/busybox'}


def name(ti):
    """a member's path as a manifest writes it (escaped)"""
    return im.escape(im.norm(ti.name))


def read_manifest(data):
    text = data.decode('utf-8', 'surrogateescape')
    head, *body = text.split('\n')
    h = head.split(' ')
    if len(h) != 4 or h[:2] != ['mu300-image-manifest', '1']:
        raise SystemExit('not an image manifest')
    lines = {}
    for line in body:
        if line:
            lines[line.split(' ', 6)[6]] = line
    return h[2], h[3], lines, text


def image_manifest(tarball):
    """the manifest the image carries, as bytes"""
    with tarfile.open(tarball) as t:
        for ti in t:
            if im.norm(ti.name) == im.SELF:
                return gzip.decompress(t.extractfile(ti).read())
    raise SystemExit(f'{tarball} carries no {im.SELF}')


def has_attrs(ti):
    return any('xattr' in k or 'acl' in k.lower() for k in ti.pax_headers)


def omissible(ti, path, line, base, linked):
    return (ti.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and path not in linked and not has_attrs(ti) and
            not path.startswith(OMIT_NOT) and path not in TOOLS and path != im.SELF and base.get(path) == line)


def make(base_gz, new, out):
    bimage, btag, base, _ = read_manifest(gzip.decompress(open(base_gz, 'rb').read()))
    nimage, ntag, lines, _ = read_manifest(image_manifest(new))
    if bimage != nimage:
        raise SystemExit(f'{base_gz} is a manifest of {bimage}, {new} is {nimage}')
    if btag == ntag:
        raise SystemExit(f'{base_gz} is of the same release as {new}')
    linked, pax = set(), False
    with tarfile.open(new) as t:
        for ti in t:
            if ti.type == tarfile.LNKTYPE:
                linked.update((im.escape(im.norm(ti.linkname)), name(ti)))
            pax = pax or bool(ti.pax_headers)
    left = []
    with tarfile.open(new) as t:
        for ti in t:
            path = name(ti)
            if path and omissible(ti, path, lines.get(path), base, linked):
                left.append(lines[path])
    text = f'mu300-diff 1 {nimage} {btag} {ntag}\n' + ''.join(line + '\n' for line in left)
    skip = {line.split(' ', 6)[6] for line in left}
    tmp = out + '.part'
    with open(tmp, 'wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0, compresslevel=9) as gz, \
            tarfile.open(fileobj=gz, mode='w', format=tarfile.PAX_FORMAT if pax else tarfile.GNU_FORMAT) as o, \
            tarfile.open(new) as t:
        data = text.encode('utf-8', 'surrogateescape')
        hi = tarfile.TarInfo('./' + HEAD)
        hi.size, hi.mode, hi.mtime = len(data), 0o600, 0
        o.addfile(hi, io.BytesIO(data))
        for ti in t:
            if name(ti) in skip:
                continue
            if ti.issparse():
                ti.type, ti.sparse = tarfile.REGTYPE, None
            o.addfile(ti, t.extractfile(ti) if ti.isreg() else None)
    os.replace(tmp, out)
    print(f'{os.path.basename(out)}: {os.path.getsize(out)} bytes of {os.path.getsize(new)}, '
          f'{len(left)} files of {btag} left out')


def check(base_gz, new, diff):
    bimage, btag, base, _ = read_manifest(gzip.decompress(open(base_gz, 'rb').read()))
    nimage, ntag, lines, _ = read_manifest(image_manifest(new))
    with tarfile.open(diff) as t:
        first = next(iter(t))
        if im.norm(first.name) != HEAD:
            raise SystemExit(f'{diff}: its first member is not {HEAD}')
        head, *left = t.extractfile(first).read().decode('utf-8', 'surrogateescape').split('\n')
    left = [line for line in left if line]
    if head != f'mu300-diff 1 {nimage} {btag} {ntag}':
        raise SystemExit(f'{diff}: "{head}" is not the diff of {nimage} from {btag} to {ntag}')
    got = {im.escape(p): line for p, line in im.entries(diff).items()}
    got.pop(HEAD, None)
    linked = set()
    with tarfile.open(new) as t:
        for ti in t:
            if ti.type == tarfile.LNKTYPE:
                linked.update((im.escape(im.norm(ti.linkname)), name(ti)))
    for line in left:
        path = line.split(' ', 6)[6]
        if lines.get(path) != line or base.get(path) != line or path in got:
            raise SystemExit(f'{diff}: {path} is left out, but it is not a file of {btag} as it is in {ntag}')
        if path.startswith(OMIT_NOT) or path in TOOLS or path in linked or not line.startswith('f '):
            raise SystemExit(f'{diff}: {path} must not be left out')
        got[path] = line
    if got != lines:
        bad = sorted(set(got.items()) ^ set(lines.items()))[:20]
        raise SystemExit(f'{diff} and {btag} do not give {ntag}: ' + '; '.join(f'{p} {l}' for p, l in bad))
    if image_manifest(diff) != image_manifest(new):
        raise SystemExit(f'{diff} does not carry the manifest of {ntag}')
    h = hashlib.sha256(open(diff, 'rb').read()).hexdigest()
    print(f'{os.path.basename(diff)}: with {len(left)} files of {btag} it is {nimage} {ntag} ({h[:12]})')


def main(argv):
    if len(argv) == 5 and argv[1] == 'make':
        make(argv[2], argv[3], argv[4])
    elif len(argv) == 5 and argv[1] == 'check':
        check(argv[2], argv[3], argv[4])
    else:
        raise SystemExit(__doc__)


if __name__ == '__main__':
    main(sys.argv)
