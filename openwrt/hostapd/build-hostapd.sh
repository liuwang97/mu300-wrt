#!/bin/sh
# The hotspot's WPA3-SAE: wpad-basic-mbedtls, hostapd-common and wifi-scripts of OpenWrt 25.12.5, rebuilt with
# patch-hostapd-sae.py (the SC2355 firmware does SAE; hostapd takes the PMK it hands back and runs the four-way
# handshake). The three APKs go together (release r1001): the image installs them instead of the feed's.
#   openwrt/hostapd/build-hostapd.sh [OUT]     OUT: default work/wifi-sae in the checkout
# OUT gets the APKs, SHA256SUMS, source-info and build-id (the sha256 of the sources here, written last); an OUT whose
# build-id and checksums match is kept as it is. Runs on the host; needs docker (the build container runs on the
# host's architecture and cross-compiles for armsr/armv8). The OpenWrt tree and its toolchain stay in a docker volume
# ($MU300_HOSTAPD_CACHE, default mu300-openwrt-src) between builds; MU300_JOBS: make -j (default 4).
set -eu
TOP=$(cd "$(dirname "$0")/../.." && pwd)
H=$TOP/openwrt/hostapd
VER=${MU300_WRT_VER:-25.12.5}
[ "$VER" = 25.12.5 ] || {
    echo "SAE hostapd: the source and build checks are for OpenWrt 25.12.5, not $VER" >&2
    exit 1
}
OUT=${1:-$TOP/work/wifi-sae}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
stamp=$(python3 "$H/wifi-sae-state.py" --build-id)
if [ -s "$OUT/build-id" ] && [ -s "$OUT/SHA256SUMS" ] && python3 "$H/wifi-sae-state.py" "$OUT" >/dev/null 2>&1; then
    echo "SAE hostapd: $OUT is up to date"
    exit 0
fi
case "$(uname -m)" in
    x86_64) PLAT=linux/amd64 ;;
    aarch64|arm64) PLAT=linux/arm64 ;;
    *) echo "SAE hostapd: unsupported build host $(uname -m)" >&2; exit 1 ;;
esac
docker build --platform "$PLAT" -t mu300-hostapd-build -f "$H/hostapd.Dockerfile" "$H"
docker run --rm --platform "$PLAT" \
    -v "${MU300_HOSTAPD_CACHE:-mu300-openwrt-src}:/build" \
    -v "$H:/src:ro" -v "$OUT:/out" \
    -e "VER=$VER" -e "BUILD_ID=$stamp" -e "JOBS=${MU300_JOBS:-4}" \
    mu300-hostapd-build sh /src/build-hostapd-inner.sh
python3 "$H/wifi-sae-state.py" "$OUT" >/dev/null
