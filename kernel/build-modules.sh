#!/bin/bash
# Build the out-of-tree vendor modules (WCN) against the mainline tree built by build.sh.
# Run inside the mu300-mainline-build container: bash /work/build-modules.sh [module-dir...]
set -eo pipefail
K=/src/linux-6.18.52
O=/src/out-6.18.52
mods=${*:-wcn_bsp}
mkdir -p /work/out/modules
# Module.symvers for the built-in exports (pcie-sprd etc.)
make -C $K O=$O ARCH=arm64 -j"$(nproc)" modules > $O/modules.log 2>&1 || { tail -20 $O/modules.log; exit 1; }
extra=
for m in $mods; do
    rm -rf /src/mod-build/$m && mkdir -p /src/mod-build && cp -r /work/modules/$m /src/mod-build/$m
    # wlan/bt use wcn_bsp's exports and its vendor headers (../wcn_bsp/kinclude)
    [ -d /src/mod-build/wcn_bsp ] || cp -r /work/modules/wcn_bsp /src/mod-build/wcn_bsp
    # the Mali DDK needs its own configuration switches (same ones the 5.4 build uses)
    margs=
    kcflags=
    [ "$m" = mali ] && kcflags="-I/src/mod-build/mali/kinclude"
    # the Mali driver calls Trusty for protected mode, so it needs the vendor trusty headers that ship with
    # the modem modules
    [ "$m" = mali ] && [ ! -d /src/mod-build/mali/kinclude ] && cp -r /work/modules/sprd_modem/kinclude /src/mod-build/mali/kinclude
    [ "$m" = mali ] && margs="src=/src/mod-build/mali CONFIG_MALI_MIDGARD=m CONFIG_MALI_PLATFORM_NAME=qogirn6pro CONFIG_MALI_DEVFREQ=y CONFIG_DEVFREQ_THERMAL=y CONFIG_MALI_DEBUG=n CONFIG_MALI_FENCE_DEBUG=n BUILD=no"
    make -C $O ARCH=arm64 M=/src/mod-build/$m KBUILD_EXTRA_SYMBOLS="$extra" KCFLAGS="$kcflags" $margs -j"$(nproc)" modules 2>&1 | tee /work/out/modules/$m.log
    [ -f /src/mod-build/$m/Module.symvers ] && extra="$extra /src/mod-build/$m/Module.symvers"
    find /src/mod-build/$m -name '*.ko' -exec cp {} /work/out/modules/ \;
done
ls -la /work/out/modules/*.ko
