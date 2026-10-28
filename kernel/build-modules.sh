#!/bin/bash
# Build the out-of-tree vendor modules against the tree build.sh made, and collect the kernel's own modules next to
# them, flat, in $MU300_OUT/modules (default kernel/out/modules).
#   kernel/build-modules.sh [module-dir...]
set -eo pipefail
W=$(cd "$(dirname "$0")" && pwd)
KV=${KV:-$(cat "$W/VERSION")}
SRC=${MU300_SRC:-/src}
K=$SRC/linux-$KV
O=$SRC/out-$KV
OUT=${MU300_OUT:-$W/out}
export ARCH=arm64
[ "$(uname -m)" = aarch64 ] || export CROSS_COMPILE=${CROSS_COMPILE:-aarch64-linux-gnu-}
STRIP=${CROSS_COMPILE:-}strip
# Every out-of-tree module, in dependency order: wlan and bt use wcn_bsp, the PMIC watchdog and the DDR DFS driver the
# modem's headers; mu300_thermal and ums9620_lpintc need only the kernel.
mods=${*:-wcn_bsp sprd_wlan_combo sprdbt_tty sprd_modem sprd_pmic_wdt sprd_ddr_dfs mu300_thermal ums9620_lpintc}
mkdir -p "$OUT/modules"
# xt_HL.ko and xt_hl.ko (and three more pairs) must stay two files
. "$W/case-sensitive.sh"
. "$W/module-sources.sh"
case_sensitive "$OUT/modules" || exit 1
# a full build starts clean, so no module of another kernel release ends up next to the new ones
[ $# -gt 0 ] || rm -f "$OUT"/modules/*.ko "$OUT"/modules/*.log "$OUT"/modules/*.sources
# Module.symvers for the built-in exports (pcie-sprd etc.)
make -C "$K" O="$O" -j"$(nproc)" modules > "$O/modules.log" 2>&1 || { tail -20 "$O/modules.log"; exit 1; }
# the kernel's own modules, stripped, flat next to the vendor ones: OpenWrt's kmodloader loads them by name
rm -rf "$O/mod-install"
make -C "$K" O="$O" INSTALL_MOD_PATH="$O/mod-install" INSTALL_MOD_STRIP=1 DEPMOD=true modules_install >> "$O/modules.log" 2>&1 ||
    { tail -20 "$O/modules.log"; exit 1; }
# the set of the previous run goes first: an option no longer =m leaves no module behind on a partial run
if [ -f "$O/modules.in-tree" ]; then while read -r ko; do rm -f "$OUT/modules/$ko"; done < "$O/modules.in-tree"; fi
find "$O/mod-install/lib/modules" -name '*.ko' -exec basename {} \; | sort > "$O/modules.in-tree"
# one flat directory holds them all, and modprobe takes - and _ for the same: two modules of one name would shadow
# each other
tr - _ < "$O/modules.in-tree" | sort > "$O/modules.in-tree.names"
[ -z "$(uniq -d "$O/modules.in-tree.names")" ] || { echo "in-tree modules share a name: $(uniq -d "$O/modules.in-tree.names")" >&2; exit 1; }
# none of them may load by itself on the boards: udev loads what matches a node of the vendor device tree
krel=$(cat "$O/include/config/kernel.release")
# (depmod lives in /sbin, which a user's PATH may lack)
DEPMOD=$(command -v depmod || echo /sbin/depmod)
"$DEPMOD" -b "$O/mod-install" "$krel"
python3 "$W/check-dt-modules.py" "$O/mod-install/lib/modules/$krel/modules.alias" "$W/dt-compatibles.txt" \
    $(sed 's/#.*//' "$W/dt-modules-allowed.txt")
# depmod's index, for make-bundle.sh's check of the autoload-blocked list (check-autoload-blocked.py)
cp "$O/mod-install/lib/modules/$krel/modules.dep" "$O/mod-install/lib/modules/$krel/modules.alias" "$OUT/"
find "$O/mod-install/lib/modules" -name '*.ko' -exec cp {} "$OUT/modules/" \;
B=$SRC/mod-build
extra=
for m in $mods; do
    # the sources as they are built: make-bundle.sh refuses the module once the tree's differ
    rm -f "$OUT/modules/$m.sources"
    module_sources "$W/modules/$m" > "$SRC/$m.sources"
    rm -rf "$B/$m" && mkdir -p "$B" && cp -r "$W/modules/$m" "$B/$m"
    # wlan and bt use wcn_bsp's exports and its headers (../wcn_bsp/kinclude); the PMIC watchdog and the DDR DFS
    # driver talk to pm_sys over SIPC, so they need the modem stack's headers next to them
    [ -d "$B/wcn_bsp" ] || cp -r "$W/modules/wcn_bsp" "$B/wcn_bsp"
    [ -d "$B/sprd_modem" ] || cp -r "$W/modules/sprd_modem" "$B/sprd_modem"
    make -C "$O" M="$B/$m" KBUILD_EXTRA_SYMBOLS="$extra" -j"$(nproc)" modules 2>&1 | tee "$OUT/modules/$m.log"
    [ -f "$B/$m/Module.symvers" ] && extra="$extra $B/$m/Module.symvers"
    for ko in $(find "$B/$m" -name '*.ko'); do
        ! echo "${ko##*/}" | tr - _ | grep -Fqx -f - "$O/modules.in-tree.names" || { echo "$m: ${ko##*/} has the name of an in-tree module" >&2; exit 1; }
        cp "$ko" "$OUT/modules/"
        # the kernel's own modules are stripped by modules_install; these carry the DWARF of CONFIG_DEBUG_INFO
        # (several MiB each) - --strip-debug keeps their .BTF
        $STRIP --strip-debug "$OUT/modules/${ko##*/}"
    done
    mv "$SRC/$m.sources" "$OUT/modules/$m.sources"
done
cp "$O/modules.in-tree" "$OUT/modules.in-tree"
ls -la "$OUT"/modules/*.ko | wc -l
