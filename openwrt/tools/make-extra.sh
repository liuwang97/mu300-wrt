#!/bin/sh
# Build an extra: the optional parts that are not in the images and that mu300-extra installs on the device.
#   tools/make-extra.sh NAME OUT.tar.gz [TAG]     (TAG: the release it belongs to, default dev)
# Extras:
#   vpn   Xray-core + hev-socks5-tunnel (tools/fetch-xray.sh) and sing-box (tools/fetch-sing-box.sh), pinned by hash
# Layout (what mu300-update's extra_unpack checks): ./name ./release ./components ./bin/<programs>, owned by root.
set -eu
NAME=${1:?usage: tools/make-extra.sh NAME OUT.tar.gz [TAG]}
OUT=${2:?usage: tools/make-extra.sh NAME OUT.tar.gz [TAG]}
TAG=${3:-dev}
TOP=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/x/bin"
case $NAME in
    vpn)
        sh "$TOP/tools/fetch-xray.sh" "$tmp/x/bin" >&2
        sh "$TOP/tools/fetch-sing-box.sh" "$tmp/x/bin/sing-box" >&2
        {
            printf 'xray %s\n' "$(sed -n 's/^XRAY_VER=//p' "$TOP/tools/fetch-xray.sh")"
            printf 'hev-socks5-tunnel %s\n' "$(sed -n 's/^HEV_VER=//p' "$TOP/tools/fetch-xray.sh")"
            printf 'sing-box %s\n' "$(sed -n 's/^VER=//p' "$TOP/tools/fetch-sing-box.sh")"
        } > "$tmp/x/components" ;;
    *) echo "unknown extra '$NAME' (vpn)" >&2; exit 2 ;;
esac
echo "$NAME" > "$tmp/x/name"
echo "$TAG" > "$tmp/x/release"
# python's tarfile, not tar: the same root-owned archive from macOS (bsdtar) and Linux (GNU tar)
python3 - "$tmp/x" "$OUT" <<'PY'
import os, sys, tarfile
src, out = sys.argv[1], sys.argv[2]
def root(ti):
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = 'root'
    return ti
with tarfile.open(out, 'w:gz', format=tarfile.GNU_FORMAT) as t:
    for name in ('name', 'release', 'components', 'bin'):
        t.add(os.path.join(src, name), arcname='./' + name, filter=root)
PY
echo "$NAME extra ($TAG): $OUT, $(du -h "$OUT" | cut -f1)"
