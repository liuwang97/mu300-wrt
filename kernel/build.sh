#!/bin/bash
# Build the kernel Image for the F50 and the U30 Air.
#   kernel/build.sh                 Linux $(cat VERSION) into $MU300_OUT (default kernel/out)
# The source tree lives under $MU300_SRC (default /src): linux-$KV unpacked there, objects in out-$KV.
# On an arm64 host it compiles natively (the Dockerfile's image); elsewhere with CROSS_COMPILE=aarch64-linux-gnu-.
set -eo pipefail
W=$(cd "$(dirname "$0")" && pwd)
KV=${KV:-$(cat "$W/VERSION")}
SRC=${MU300_SRC:-/src}
OUT=${MU300_OUT:-$W/out}
export ARCH=arm64
[ "$(uname -m)" = aarch64 ] || export CROSS_COMPILE=${CROSS_COMPILE:-aarch64-linux-gnu-}
# The source is fetched once and unpacked only after verify-kernel-src.sh passed (kernel.org's signed sha256sums.asc
# and the releaser's tar.sign, against keys/). It is unpacked beside the tree and moved into place last, so an
# interrupted run never leaves a tree that counts. sha256sums.asc comes from www.kernel.org: the CDN's copy lags a
# new release by hours, and it is signed, so where it comes from does not change what is checked.
if [ ! -d "$SRC/linux-$KV" ]; then
    base=https://cdn.kernel.org/pub/linux/kernel/v${KV%%.*}.x
    dl=$SRC/.download-$KV
    rm -rf "$dl" && mkdir -p "$dl"
    for f in linux-$KV.tar.xz linux-$KV.tar.sign sha256sums.asc; do
        u=$base/$f; [ "$f" != sha256sums.asc ] || u=https://www.kernel.org/pub/linux/kernel/v${KV%%.*}.x/$f
        curl -fsSL -o "$dl/$f" "$u" || { rm -rf "$dl"; echo "cannot fetch $u" >&2; exit 1; }
    done
    bash "$W/verify-kernel-src.sh" "$dl" "$KV" || { rm -rf "$dl"; exit 1; }
    tar -xJf "$dl/linux-$KV.tar.xz" -C "$dl" && mv "$dl/linux-$KV" "$SRC/linux-$KV"
    rm -rf "$dl"
fi
cd "$SRC/linux-$KV"
python3 "$W/port/install.py" .
# A patch either applies or is applied already; anything else stops the build. .mu300-patches lists the applied ones
# by checksum: a patch that a later one builds on (0009 under 0012) can neither be applied again nor reversed alone.
for p in "$W"/patches/*.patch; do
    s=$(sha256sum < "$p" | cut -d' ' -f1)
    grep -qx "$s" .mu300-patches 2>/dev/null && continue
    if patch -p1 -N -s --dry-run < "$p" >/dev/null 2>&1; then patch -p1 -N -s < "$p"
    elif patch -p1 -R -s -f --dry-run < "$p" >/dev/null 2>&1; then :
    else echo "patch does not apply to $KV: $p" >&2; exit 1; fi
    echo "$s" >> .mu300-patches
done
O=$SRC/out-$KV
mkdir -p "$O"
make O="$O" allnoconfig >/dev/null
./scripts/kconfig/merge_config.sh -m -O "$O" "$O/.config" "$W/mu300-mainline.config" >/dev/null
make O="$O" olddefconfig >/dev/null
# Every other tristate option the boards can use becomes a module (all-modules.py); MU300_ALL_MODULES=0 builds the
# fragment alone.
rm -f "$O/all-modules.report"
[ "${MU300_ALL_MODULES:-1}" = 0 ] || python3 "$W/all-modules.py" . "$O"
# An option Kconfig did not take stops the build: a silently dropped option has cost a working feature before.
bad=
while IFS= read -r l; do
  case "$l" in CONFIG_*=*) k=${l%%=*}; v=${l#*=}; g=$(grep -E "^$k=" "$O/.config" | cut -d= -f2- || true);
    [ "$v" = n ] && { grep -q "^$k=y" "$O/.config" && bad="$bad\nNOT DISABLED: $k" || true; continue; }
    [ "$g" = "$v" ] || bad="$bad\nNOT SET: $k want $v got ${g:-unset}";; esac
done < "$W/mu300-mainline.config"
[ -z "$bad" ] || { printf "config options not taken:$bad\n" >&2; exit 1; }
# Extra kernel arguments for a test build only: CONFIG_CMDLINE_FORCE means the bootloader's command line is never
# read. E.g. MU300_CMDLINE_EXTRA=sdhci_sprd.fail_hs400es=1.
if [ -n "${MU300_CMDLINE_EXTRA:-}" ]; then
    c=$(sed -n 's/^CONFIG_CMDLINE="\(.*\)"$/\1/p' "$O/.config")
    ./scripts/config --file "$O/.config" --set-str CMDLINE "$c $MU300_CMDLINE_EXTRA"
    make O="$O" olddefconfig >/dev/null
    grep '^CONFIG_CMDLINE=' "$O/.config"
fi
# On failure show the compiler's own messages; a plain grep for "error" also matches names like fdt_strerror.o.
make O="$O" -j"$(nproc)" Image > "$O/build.log" 2>&1 || {
    grep -n -E ": (fatal )?error: |-Werror|treated as errors|undefined reference|No such file|Killed|internal compiler error|\*\*\*" -A3 "$O/build.log" | head -80 || true
    echo "--- end of build.log:"; tail -25 "$O/build.log" || true
    exit 1
}
mkdir -p "$OUT"
cp "$O/arch/arm64/boot/Image" "$OUT/"
# for modprobe on the device: a built-in driver is found through modules.builtin
cp "$O/modules.builtin" "$O/modules.builtin.modinfo" "$OUT/"
# what all-modules.py added: audit-bundle.py checks the Image against it as against the fragment
rm -f "$OUT/all-modules.report"
[ ! -f "$O/all-modules.report" ] || cp "$O/all-modules.report" "$OUT/"
ls -la "$OUT"
