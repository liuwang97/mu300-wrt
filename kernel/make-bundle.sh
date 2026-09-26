#!/bin/sh
# Package the mainline build (build.sh + build-modules.sh) as a kernel bundle, laid out like the release's
# mu300-kernel.tar.gz so that mu300-update can install it:
#   upstream/make-bundle.sh OUT.tar.gz [KERNEL_BUNDLE_5.4]
#     ./Image                LK-loadable kernel (wrap-image.py: LK copies it to 0x80080000)
#     ./ramdisk-generic.lz4  boot/init, busybox, logdw and the modules of upstream/module-order.txt
#     ./modules/*.ko         every module, for /lib/modules/<release> on the root filesystem
#     ./kernel.release       the kernel's release string (uname -r)
# busybox and logdw are the static helpers of the 5.4 bundle (default: the newest one under release/).
set -eu
TOP=$(cd "$(dirname "$0")/.." && pwd)
U=$TOP/upstream
OUT=${1:?usage: upstream/make-bundle.sh OUT.tar.gz [mu300-kernel.tar.gz]}
REF=${2:-$(ls -t "$TOP"/release/*/mu300-kernel.tar.gz 2>/dev/null | head -1)}
[ -f "$U/out/Image" ] || { echo "no upstream/out/Image (run build.sh)" >&2; exit 1; }
[ -f "$REF" ] || { echo "no 5.4 kernel bundle for busybox/logdw (give one as the second argument)" >&2; exit 1; }

krel=$(strings "$U/out/Image" | sed -n 's/^Linux version \([^ ]*\) .*/\1/p' | head -1)
[ -n "$krel" ] || { echo "cannot read the kernel release from upstream/out/Image" >&2; exit 1; }
# a module left over from another kernel release would only fail on the device, at insmod
for m in "$U"/out/modules/*.ko; do
    v=$(strings "$m" | sed -n 's/^vermagic=\([^ ]*\) .*/\1/p' | head -1)
    [ "$v" = "$krel" ] || { echo "$m is built for '$v', the kernel is '$krel'" >&2; exit 1; }
done
for m in $(cat "$U/module-order.txt"); do
    [ -f "$U/out/modules/$m" ] || { echo "module-order.txt names $m, which was not built" >&2; exit 1; }
done

W=$(mktemp -d)
trap 'rm -rf "$W"' EXIT
tar -xzf "$REF" -C "$W" ./busybox ./logdw
mkdir "$W/b" "$W/b/modules"
python3 "$U/wrap-image.py" "$U/out/Image" "$W/b/Image"
python3 "$TOP/boot/build-boot-image.py" --generic-ramdisk --modules "$U/out/modules" \
    --module-order "$U/module-order.txt" --busybox "$W/busybox" --logdw "$W/logdw" \
    --ueventd-perms "$TOP/android-vendor/ueventd-perms.sh" --out "$W/b/ramdisk-generic.lz4" >/dev/null
cp "$U"/out/modules/*.ko "$W/b/modules/"
echo "$krel" > "$W/b/kernel.release"
tar -C "$W/b" -czf "$OUT" .
echo "$OUT: kernel $krel, $(ls "$W/b/modules" | wc -l | tr -d ' ') modules, ramdisk segment $(wc -c < "$W/b/ramdisk-generic.lz4" | tr -d ' ') bytes"
