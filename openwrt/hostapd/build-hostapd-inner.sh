#!/bin/sh
# Inside the build container (build-hostapd.sh): /build is the cache volume, /src this directory, /out the result.
set -eu
export FORCE_UNSAFE_CONFIGURE=1
cd /build
exec 9>/build/mu300-hostapd.lock
flock 9
[ -d openwrt/.git ] || git clone -q --depth 1 --branch "v$VER" \
    https://git.openwrt.org/openwrt/openwrt.git openwrt
cd openwrt
[ "$(git rev-parse HEAD)" = "$(git rev-parse "v$VER^{commit}")" ] || {
    echo "SAE hostapd: the tree in the cache is not OpenWrt v$VER" >&2; exit 1;
}
H=package/network/services/hostapd
W=package/network/config/wifi-scripts
# the hostapd source the patches are written for
grep -q 'PKG_SOURCE_VERSION:=ca266cc24d8705eb1a2a0857ad326e48b1408b20' "$H/Makefile"
grep -q 'PKG_MIRROR_HASH:=59ac677093f524ff98588abd9f33805a336a6e929d6814222f0d784c854f2343' "$H/Makefile"
# release 1001: newer than the feed's, so an apk upgrade never replaces one half of the driver/daemon pair
git checkout -q -- "$H" "$W"
rm -f "$H/patches/990-mu300-unisoc-sae.patch"   # made again below
sed -i 's/^PKG_RELEASE:=.*/PKG_RELEASE:=1001/' "$H/Makefile" "$W/Makefile"
python3 /src/patch-hostapd-sae.py wifi "$W/files"
python3 /src/patch-hostapd-sae.py reload "$H/files"
if [ ! -s .config ]; then
    printf '%s\n' 'CONFIG_TARGET_armsr=y' 'CONFIG_TARGET_armsr_armv8=y' 'CONFIG_TARGET_armsr_armv8_DEVICE_generic=y' \
        'CONFIG_ALL_KMODS=n' 'CONFIG_ALL_NONSHARED=n' > .config
fi
sed -i -E '/^CONFIG_PACKAGE_(wpad[^=]*|hostapd[^=]*)=/d' .config
printf '%s\n' 'CONFIG_PACKAGE_wpad-basic-mbedtls=m' 'CONFIG_PACKAGE_hostapd-common=m' \
    'CONFIG_PACKAGE_wifi-scripts=m' 'CONFIG_WIFI_SCRIPTS_UCODE=y' \
    'CONFIG_SIGNED_PACKAGES=n' >> .config
make defconfig
# a cache volume may hold packages without a prepared host toolchain
ls staging_dir/host/bin/openssl >/dev/null 2>&1 || make -j"${JOBS:-4}" tools/install
ls staging_dir/toolchain-*/bin/*-gcc >/dev/null 2>&1 || make -j"${JOBS:-4}" toolchain/install
# The out-of-tree sprd_wlan_combo selects no kmod-mac80211 option, so the AC/AX/BE support of the feed's daemon is
# asked for here; without it the new hostapd refuses a VHT/HE configuration.
hostapd_make() {
    make CONFIG_DRIVER_11AC_SUPPORT=y CONFIG_DRIVER_11AX_SUPPORT=y \
        CONFIG_DRIVER_11BE_SUPPORT=y "$@"
}
# The source patch becomes one more patch of the package, made from the prepared source (OpenWrt's patches applied):
# the compile prepares the source again whenever a dependency is newer, which would drop an edit of build_dir.
SAEPATCH=$H/patches/990-mu300-unisoc-sae.patch
hostapd_make "$H/clean"
hostapd_make "$H/prepare" V=s
source_dir=$(find build_dir/target-* -mindepth 2 -maxdepth 2 -type d -path '*/hostapd-wpad-basic-mbedtls/hostapd-*')
[ -n "$source_dir" ] && [ "$(printf '%s\n' "$source_dir" | wc -l)" = 1 ] || {
    echo 'SAE hostapd: expected one prepared wpad-basic-mbedtls source' >&2; exit 1;
}
work=$(mktemp -d)
cp -a "$source_dir" "$work/a"
cp -a "$source_dir" "$work/b"
python3 /src/patch-hostapd-sae.py source "$work/b"
(cd "$work" && diff -urN a b) > "$SAEPATCH" && { echo 'SAE hostapd: the source patch changed nothing' >&2; exit 1; }
[ -s "$SAEPATCH" ] || { echo 'SAE hostapd: no source patch' >&2; exit 1; }
rm -rf "$work"
hostapd_make "$H/clean"
hostapd_make -j"$JOBS" "$H/compile" V=s
# what was compiled has the import, and the daemon knows unisoc_sae
grep -q 'unisoc_sae_import(hapd, sta, req_ies, req_ies_len)' "$source_dir/src/ap/drv_callbacks.c" || {
    echo 'SAE hostapd: the compiled source lacks the import' >&2; exit 1;
}
wpad=$(find build_dir/target-*/hostapd-wpad-basic-mbedtls -path '*/usr/sbin/wpad' -type f | head -n 1)
[ -n "$wpad" ] && grep -aq unisoc_sae "$wpad" || { echo 'SAE hostapd: the built wpad lacks unisoc_sae' >&2; exit 1; }
make "$W/clean"
make -j"$JOBS" "$W/compile" V=s
stage=$(mktemp -d /out/.wifi-sae.XXXXXX)
trap 'rm -rf "$stage"' EXIT
for pkg in wpad-basic-mbedtls hostapd-common wifi-scripts; do
    apk=$(find bin -type f -name "$pkg-*-r1001.apk")
    [ -n "$apk" ] && [ "$(printf '%s\n' "$apk" | wc -l)" = 1 ] || {
        echo "SAE hostapd: expected exactly one $pkg APK" >&2; exit 1;
    }
    cp "$apk" "$stage/"
done
(cd "$stage" && sha256sum ./*.apk > SHA256SUMS)
printf '%s\n' "$BUILD_ID" > "$stage/build-id"
printf '%s\n' "OpenWrt $VER" "hostapd ca266cc24d8705eb1a2a0857ad326e48b1408b20" \
    'SAE: firmware authentication, hostapd four-way handshake' > "$stage/source-info"
rm -f /out/*.apk /out/build-id
cp "$stage/"*.apk "$stage/SHA256SUMS" "$stage/source-info" /out/
# the build id last: it marks every APK and its checksum in place
mv "$stage/build-id" /out/build-id
