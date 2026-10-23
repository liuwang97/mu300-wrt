#!/bin/sh
# Package the build of build.sh and build-modules.sh as the kernel bundle (docs/contract.md), which mu300-update installs:
#   kernel/make-bundle.sh OUT.tar.gz
#     ./Image                LK-loadable kernel (wrap-image.py: LK copies it to 0x80080000)
#     ./ramdisk-generic.lz4  initramfs/init, busybox, logdw, e2fsck and the modules of initramfs/module-order.txt
#     ./modules/*.ko         every module - the kernel's own (=m) and the out-of-tree ones - flat, for
#                            /lib/modules/<release> on the root filesystem (OpenWrt's kmodloader loads them by name)
#     ./kernel.release       the kernel's release string (uname -r)
#     ./devices              the boards it runs on: f50 u30air
#     ./features             what the updater checks before it replaces a kernel: sdcard, linux-slot, gpt-root
#     ./contract             the version of docs/contract.md this bundle follows
#     ./autoload-blocked     the modules the kernel must not load on demand (autoload-blocked)
#     ./modules.builtin*     what the kernel has built in, for modprobe
#     ./modules.in-tree, ./modules.list, ./kernel.config   the build's module list, the bundle's, its .config
# The build is read from $MU300_OUT (default kernel/out); the ramdisk's static tools from $MU300_TOOLS, which
# initramfs/tools.sh fills when they are missing (default $MU300_OUT/tools).
set -eu
K=$(cd "$(dirname "$0")" && pwd)
I=$K/initramfs
CONTRACT=1
UO=${MU300_OUT:-$K/out}
TOOLS=${MU300_TOOLS:-$UO/tools}
OUT=${1:?usage: kernel/make-bundle.sh OUT.tar.gz}
[ -f "$UO/Image" ] || { echo "no $UO/Image (run build.sh)" >&2; exit 1; }
# the build's module tree and the staging directory must keep xt_HL.ko and xt_hl.ko apart: a tree built on a file
# system that ignores case has lost one of each pair already (audit-bundle.py also finds that)
. "$K/case-sensitive.sh"
case_sensitive "$UO/modules" || exit 1

python3 "$K/audit-bundle.py" "$UO" "$K/mu300-mainline.config"
# the modules the system must not load on demand, against this build's dependencies
python3 "$K/check-autoload-blocked.py" "$UO" "$K/autoload-blocked"

krel=$(strings "$UO/Image" | sed -n 's/^Linux version \([^ ]*\) .*/\1/p' | head -1)
[ -n "$krel" ] || { echo "cannot read the kernel release from $UO/Image" >&2; exit 1; }
# a module left over from another kernel release would only fail on the device, at insmod
for m in "$UO"/modules/*.ko; do
    v=$(strings "$m" | sed -n 's/^vermagic=\([^ ]*\) .*/\1/p' | head -1)
    [ "$v" = "$krel" ] || { echo "$m is built for '$v', the kernel is '$krel'" >&2; exit 1; }
done
for m in $(cat "$I/module-order.txt"); do
    [ -f "$UO/modules/$m" ] || { echo "module-order.txt names $m, which was not built" >&2; exit 1; }
done
sh "$I/tools.sh" "$TOOLS" >/dev/null

W=$(mktemp -d "${TMPDIR:-/tmp}/mu300-bundle.XXXXXX")
trap 'rm -rf "$W"' EXIT
case_sensitive "$W" || exit 1
mkdir "$W/b" "$W/b/modules"
python3 "$K/wrap-image.py" "$UO/Image" "$W/b/Image"
python3 "$I/build-boot-image.py" --generic-ramdisk --modules "$UO/modules" \
    --module-order "$I/module-order.txt" --busybox "$TOOLS/busybox" --logdw "$TOOLS/logdw" --e2fsck "$TOOLS/e2fsck" \
    --ueventd-perms "$I/ueventd-perms.sh" --out "$W/b/ramdisk-generic.lz4" >/dev/null
cp "$UO"/modules/*.ko "$W/b/modules/"
for f in modules.builtin modules.builtin.modinfo; do [ ! -f "$UO/$f" ] || cp "$UO/$f" "$W/b/"; done
cp "$UO/kernel.config" "$UO/modules.in-tree" "$W/b/"
( cd "$W/b/modules" && LC_ALL=C ls ) > "$W/b/modules.list"
echo "$krel" > "$W/b/kernel.release"
# the boards this kernel runs on; mu300-update refuses a bundle that does not name the device's
echo "f50 u30air" > "$W/b/devices"
# what an older bundle may lack, and mu300-update checks before it replaces a kernel: sdcard (the card slot's host is
# probed, so a system on the SD card boots), linux-slot (init runs from slot a as well, when Android is on b), gpt-root
# (init also boots a system in a GPT partition named mu300root, another installer's layout)
printf 'sdcard\nlinux-slot\ngpt-root\n' > "$W/b/features"
echo "$CONTRACT" > "$W/b/contract"
cp "$K/autoload-blocked" "$W/b/autoload-blocked"
tar -C "$W/b" -czf "$OUT" .
echo "$OUT: kernel $krel, $(ls "$W/b/modules" | wc -l | tr -d ' ') modules ($(du -sk "$W/b/modules" | cut -f1) KiB), ramdisk segment $(wc -c < "$W/b/ramdisk-generic.lz4" | tr -d ' ') bytes"
