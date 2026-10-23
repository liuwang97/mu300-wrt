#!/bin/sh
# The static aarch64 tools of the generic ramdisk segment (make-bundle.sh), built into DIR:
#   kernel/initramfs/tools.sh DIR        writes DIR/busybox, DIR/logdw and DIR/e2fsck
#   busybox  Ubuntu 24.04's busybox-static for arm64: the pinned package, checked against the SHA-256 that the
#            archive's signed index gives for it; unpacked with dpkg-deb (or ar and zstd)
#   logdw    logdw.c (the logd socket sink for the Android vendor daemons init starts), static, with
#            aarch64-linux-gnu-gcc, or gcc on an arm64 host (CC overrides it)
#   e2fsck   e2fsck/build.sh: needs docker that runs linux/arm64 images
# A tool DIR already has is kept; MU300_TOOLS_REBUILD=1 builds every one again.
set -eu
DIR=${1:?usage: kernel/initramfs/tools.sh DIR}
H=$(cd "$(dirname "$0")" && pwd)
BB_URL=http://ports.ubuntu.com/ubuntu-ports/pool/main/b/busybox/busybox-static_1.36.1-6ubuntu3.1_arm64.deb
BB_SHA=d96535e0402c011e0ee43449799df2f4504d44b842e4f2b3a6cbc845508eaafc
mkdir -p "$DIR"
DIR=$(cd "$DIR" && pwd)
want() { [ "${MU300_TOOLS_REBUILD:-0}" = 1 ] || [ ! -s "$DIR/$1" ]; }
sha256() { { sha256sum "$1" 2>/dev/null || shasum -a 256 "$1"; } | cut -d' ' -f1; }
# every tool must be an aarch64 ELF, static: the ramdisk has no loader and no libraries
check_elf() {
    [ "$(od -An -tx1 -j0 -N4 "$1" | tr -d ' \n')" = 7f454c46 ] &&
    [ "$(od -An -tu2 -j18 -N2 "$1" | tr -d ' \n')" = 183 ] || { echo "$1 is not an aarch64 ELF" >&2; return 1; }
    ! { command -v readelf >/dev/null && readelf -l "$1" 2>/dev/null | grep -q 'Requesting program interpreter'; } ||
        { echo "$1 is not static" >&2; return 1; }
}
T=$(mktemp -d "${TMPDIR:-/tmp}/mu300-tools.XXXXXX")
trap 'rm -rf "$T"' EXIT

if want busybox; then
    curl -fsSL -o "$T/bb.deb" "$BB_URL" || { echo "cannot fetch $BB_URL" >&2; exit 1; }
    [ "$(sha256 "$T/bb.deb")" = "$BB_SHA" ] || { echo "busybox-static: SHA-256 mismatch for $BB_URL" >&2; exit 1; }
    mkdir "$T/bb"
    if command -v dpkg-deb >/dev/null; then
        dpkg-deb -x "$T/bb.deb" "$T/bb"
    else
        (cd "$T" && ar x bb.deb && zstd -dc data.tar.zst | tar -xf - -C bb)
    fi
    check_elf "$T/bb/usr/bin/busybox"
    cp "$T/bb/usr/bin/busybox" "$DIR/busybox"
    chmod 755 "$DIR/busybox"
fi

if want logdw; then
    if [ -n "${CC:-}" ]; then cc=$CC
    elif [ "$(uname -m)" = aarch64 ]; then cc=gcc
    else cc=aarch64-linux-gnu-gcc; fi
    $cc -O2 -static -s -o "$T/logdw" "$H/logdw.c"
    check_elf "$T/logdw"
    cp "$T/logdw" "$DIR/logdw"
    chmod 755 "$DIR/logdw"
fi

if want e2fsck; then
    sh "$H/e2fsck/build.sh" "$T/e2fsck" >/dev/null
    check_elf "$T/e2fsck"
    cp "$T/e2fsck" "$DIR/e2fsck"
fi

for t in busybox logdw e2fsck; do echo "$DIR/$t: $(wc -c < "$DIR/$t" | tr -d ' ') bytes, sha256 $(sha256 "$DIR/$t")"; done
