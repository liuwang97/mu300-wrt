#!/bin/sh
# Maintainer: build a release - the Linux 7.2 kernel bundle and the OpenWrt image with the LuCI panel - audit it, and
# optionally publish it.
#   tools/make-release.sh --next-tag      print the tag of the next release: vYYYY.MM.DD.N for today (UTC), N one
#                                          more than the highest N of today's releases and tags (1 for the first)
#   tools/make-release.sh --check-tag TAG  only check TAG as a new release's tag (below); prints it
#   tools/make-release.sh TAG             build into release/TAG and audit the contents
#   tools/make-release.sh TAG --publish   also create/update the GitHub release (gh)
#   tools/make-release.sh TAG --publish --prerelease   create it as a prerelease: never "latest", so mu300-update does
#                                          not offer it until it is promoted on GitHub
#   --any-date                             take a TAG whose day is not today (a build that went on past midnight)
# The kernel must be built first (kernel/build.sh and kernel/build-modules.sh into $MU300_KERNEL_OUT, default
# kernel/out). Needs docker with linux/arm64 (the image, e2fsck) and an aarch64 C compiler for the static
# programs (aarch64-linux-gnu-gcc, or gcc on an arm64 host; CC overrides it).
# Assets: mu300-kernel-7.2.tar.gz, mu300-openwrt-luci-rootfs.tar.gz and its .manifest.gz, mu300-update,
# mu300-release, SHA256SUMS (and SHA256SUMS.sig with the release key). They must never contain proprietary files or
# anything from a particular device: the audit below fails the build if firmware, Android userspace, host keys or
# local settings end up in an asset.
set -eu
USAGE='usage: tools/make-release.sh TAG [--publish [--prerelease]] [--any-date] | --next-tag | --check-tag TAG [--any-date]'
TOP=$(cd "$(dirname "$0")/.." && pwd)
REPO=${MU300_REPO:-liuwang97/mu300-wrt}
# tag_ok, tag_key, newer: the release tags (the same block as mu300-update's)
. "$TOP/tools/releases.sh"
# today: the day of a release built now, as its tag writes it (MU300_TODAY stands in for the clock in the tests)
today() { echo "${MU300_TODAY:-$(date -u +%Y.%m.%d)}"; }
# next_tag: vYYYY.MM.DD.N for today. What counts: GitHub's releases (drafts and prereleases too) and the tags, here
# and on origin. Without GitHub's answer there is no next tag: a guess could take the name of a release that exists.
next_tag() {
    _day=v$(today)
    _rel=$(gh release list -R "$REPO" --limit 200 --json tagName -q '.[].tagName') ||
        { echo "cannot read the releases of $REPO (gh): no tag is guessed" >&2; return 1; }
    _n=0
    for _t in $_rel $(git -C "$TOP" tag -l "$_day.*") \
        $(git -C "$TOP" ls-remote --tags origin "refs/tags/$_day.*" 2>/dev/null | sed -n 's|.*refs/tags/\([^^]*\).*|\1|p'); do
        case $_t in "$_day".*) tag_ok "$_t" || continue; [ "${_t##*.}" -le "$_n" ] || _n=${_t##*.} ;; esac
    done
    [ "$_n" -lt 99 ] || { echo "$_day has 99 releases already" >&2; return 1; }
    echo "$_day.$((_n + 1))"
}
# check_tag TAG: a tag a new release may have - vYYYY.MM.DD.N, and of today unless --any-date
check_tag() {
    case $1 in
        *.*.*.*) tag_ok "$1" || { echo "$1 is not a release tag: vYYYY.MM.DD.N, N = 1..99 without a leading zero (tools/make-release.sh --next-tag)" >&2; return 1; } ;;
        *) echo "$1 is not a tag for a new release: those are vYYYY.MM.DD.N, the day and its N (tools/make-release.sh --next-tag)" >&2; return 1 ;;
    esac
    [ -n "$ANY_DATE" ] || [ "${1%.*}" = "v$(today)" ] ||
        { echo "$1 is not of today (v$(today)): a release is named after the day it is built (--any-date takes it anyway)" >&2; return 1; }
}
ARG= PUBLISH="" PRERELEASE="" ANY_DATE="" MODE=build
for a in "$@"; do
    case $a in
        --publish) PUBLISH=--publish ;;
        --prerelease) PRERELEASE=--prerelease ;;
        --any-date) ANY_DATE=1 ;;
        --next-tag) MODE=next ;;
        --check-tag) MODE=check ;;
        -*) echo "unknown option $a ($USAGE)" >&2; exit 1 ;;
        *) [ -z "$ARG" ] || { echo "$USAGE" >&2; exit 1; }; ARG=$a ;;
    esac
done
case $MODE in
    next) [ -z "$ARG" ] || { echo "$USAGE" >&2; exit 1; }; next_tag; exit ;;
    check) [ -n "$ARG" ] || { echo "$USAGE" >&2; exit 1; }; check_tag "$ARG" || exit 1; echo "$ARG"; exit 0 ;;
esac
TAG=$ARG
[ -n "$TAG" ] || { echo "$USAGE" >&2; exit 1; }
check_tag "$TAG" || exit 1
KOUT=${MU300_KERNEL_OUT:-$TOP/kernel/out}
D=$TOP/release/$TAG
IN=$D/inputs
KV=$(cat "$TOP/kernel/VERSION")
[ -f "$KOUT/Image" ] && ls "$KOUT"/modules/*.ko >/dev/null 2>&1 ||
    { echo "kernel outputs missing in $KOUT (kernel/build.sh, kernel/build-modules.sh)" >&2; exit 1; }
rel=$(strings "$KOUT/Image" | sed -n 's/^Linux version \([^ ]*\) .*/\1/p' | head -1)
[ "$rel" = "$KV" ] || { echo "$KOUT holds Linux $rel, kernel/VERSION says $KV" >&2; exit 1; }
[ -z "$(git -C "$TOP" status --porcelain)" ] || { echo "commit your changes first: the release must match a commit" >&2; exit 1; }
rm -rf "$D" && mkdir -p "$IN"
# the module trees are staged here and in TMPDIR: both must keep xt_HL.ko and xt_hl.ko apart, which a file system
# that ignores case (macOS's default) does not
. "$TOP/kernel/case-sensitive.sh"
case_sensitive "$D" || exit 1
case_sensitive "${TMPDIR:-/tmp}" || exit 1

echo "==> kernel bundle"
# the ramdisk's static tools (busybox, logdw, e2fsck); the image takes the same busybox
sh "$TOP/kernel/initramfs/tools.sh" "$IN/ktools" >/dev/null
MU300_OUT=$KOUT MU300_TOOLS=$IN/ktools sh "$TOP/kernel/make-bundle.sh" "$D/mu300-kernel-7.2.tar.gz"

echo "==> static programs of the image"
case $(uname -m) in aarch64|arm64) CC=${CC:-gcc} ;; *) CC=${CC:-aarch64-linux-gnu-gcc} ;; esac
S=$TOP/openwrt/src
for p in logdw/logdw bt-init/mu300-bt-init keys/mu300-keys atd-io/mu300-atd-io carry/mu300-carry; do
    mkdir -p "$IN/openwrt/src/${p%/*}"
    "$CC" -O2 -static -o "$IN/openwrt/src/$p" "$S/$p.c"
done
cp "$IN/ktools/busybox" "$IN/busybox"

echo "==> OpenWrt image with the control panel"
MU300_INPUTS="$IN" MU300_VERSION="$TAG" sh "$TOP/openwrt/build-rootfs.sh" mu300-openwrt-luci-release.tar.gz >/dev/null
mv "$TOP/openwrt/mu300-openwrt-luci-release.tar.gz" "$D/mu300-openwrt-luci-rootfs.tar.gz"

echo "==> audit"
fail=0
list() { tar -tzf "$D/$1.tar.gz" | sed 's|^\./||'; }
K=mu300-kernel-7.2 I=mu300-openwrt-luci-rootfs
for a in $K $I; do
    bad=$(list "$a" | grep -E \
        -e '(^|/)lib/firmware/(wcnmodem|gnssmodem|wifi_board_config|bt_configure)' \
        -e '^opt/mu300/android/.+' -e '__properties__|dev-properties' \
        -e '(^|/)(libmali|libOpenCL|libGLES|libEGL|libvulkan)[^/]*\.so' \
        -e '^etc/ssh/ssh_host_' -e '^etc/uhttpd\.(key|crt)$' -e '^etc/dropbear/dropbear_.*_host_key' \
        -e '^etc/mu300/(hotspot|vpn|toolkit|mobile-data)\.conf$' -e '^etc/mu300/update-source$' \
        -e '^opt/mu300/bin/(xray|sing-box|hev-socks5-tunnel|mihomo|mu300-vpn)$' -e '^opt/mu300/lib/vpn(/|$)' \
        -e '^etc/init\.d/mu300-vpn$' -e '^etc/mu300/vpn\.conf\.example$' \
        | grep -vE '^opt/mu300/android/system/?$|^opt/mu300/android/system/bin/?$|^opt/mu300/android/system/bin/cltest$' || true)
    [ -z "$bad" ] || { echo "$a contains files that must not be published:"; echo "$bad" | head -20; fail=1; }
    [ -z "$(tar -xzOf "$D/$a.tar.gz" ./etc/machine-id 2>/dev/null || true)" ] || { echo "$a has a machine-id"; fail=1; }
done
# The kernel bundle is what docs/contract.md says, for the boards of this project
kl=$(list $K)
for f in Image ramdisk-generic.lz4 kernel.release devices features contract autoload-blocked modules.builtin \
         modules.builtin.modinfo modules.in-tree; do
    printf '%s\n' "$kl" | grep -qx "$f" || { echo "$K lacks $f"; fail=1; }
done
[ "$(tar -xzOf "$D/$K.tar.gz" ./kernel.release)" = "$KV" ] || { echo "$K is not Linux $KV"; fail=1; }
[ "$(tar -xzOf "$D/$K.tar.gz" ./devices)" = "f50 u30air" ] || { echo "$K: devices is not 'f50 u30air'"; fail=1; }
for f in sdcard linux-slot gpt-root; do
    tar -xzOf "$D/$K.tar.gz" ./features | grep -qx "$f" || { echo "$K does not list $f in ./features"; fail=1; }
done
[ "$(tar -xzOf "$D/$K.tar.gz" ./contract)" = "$(sed -n 's/^Contract version: \*\*\([0-9]*\)\*\*.*/\1/p' "$TOP/docs/contract.md")" ] ||
    { echo "$K follows another contract version than docs/contract.md"; fail=1; }
# every generic ramdisk segment brings init's e2fsck: a root filesystem with recorded errors is checked before it is
# mounted
tar -xzOf "$D/$K.tar.gz" ./ramdisk-generic.lz4 > "$D/ramdisk.check"
python3 -c 'import importlib.util as u, sys
s = u.spec_from_file_location("b", sys.argv[1]); b = u.module_from_spec(s); s.loader.exec_module(b)
names = b.cpio_names(b.unlz4_legacy(open(sys.argv[2], "rb").read()))
sys.exit(next((n for n in ("init", "bin/busybox", "sbin/e2fsck") if n not in names), None) and "the ramdisk lacks a file")' \
    "$TOP/kernel/initramfs/build-boot-image.py" "$D/ramdisk.check" || { echo "$K: ramdisk-generic.lz4 is incomplete"; fail=1; }
rm -f "$D/ramdisk.check"
# the kernel's own modules next to the out-of-tree ones, by their exact names (xt_HL.ko and xt_hl.ko are two files)
for m in sch_htb.ko ntfs3.ko dm-crypt.ko cdc_mbim.ko vhost_net.ko sprd_wlan_combo.ko wcn_bsp.ko sprdbt_tty.ko \
         sipa_eth.ko mu300_thermal.ko ums9620_lpintc.ko xt_HL.ko xt_hl.ko xt_TCPMSS.ko xt_tcpmss.ko; do
    printf '%s\n' "$kl" | grep -qx "modules/$m" || { echo "$K lacks modules/$m"; fail=1; }
done
for m in $(tar -xzOf "$D/$K.tar.gz" ./modules.in-tree); do
    printf '%s\n' "$kl" | grep -qx "modules/$m" || { echo "$K lacks modules/$m"; fail=1; }
done
# The image: no kernel modules (they come with the bundle); what an update needs to carry the user's files over (the
# carry helper, a static busybox, the manifest of its own files, which must be the one this tool makes from the
# tarball); the panel and its Chinese catalog
il=$(list $I)
! printf '%s\n' "$il" | grep -q '^lib/modules/.' || { echo "$I carries kernel modules"; fail=1; }
for f in opt/mu300/bin/mu300-carry opt/mu300/bin/busybox opt/mu300/bin/mu300-update etc/mu300/image-manifest.gz \
         usr/share/ucode/mu300/cellparse.uc usr/share/luci/menu.d/luci-app-mu300.json \
         www/luci-static/resources/view/mu300/home.js usr/libexec/rpcd/mu300dash usr/lib/lua/luci/i18n/mu300.zh-cn.lmo \
         www/luci-static/aurora/main.css etc/mu300/packages.txt; do
    printf '%s\n' "$il" | grep -qx "$f" || { echo "$I lacks $f"; fail=1; }
done
! printf '%s\n' "$il" | grep -E '^usr/lib/lua/luci/i18n/[^/]+\.lmo$' | grep -vqE '\.(zh-cn)\.lmo$' ||
    { echo "$I carries catalogs of other languages than Chinese"; fail=1; }
python3 "$TOP/openwrt/tools/image-manifest.py" check "$D/$I.tar.gz" || fail=1
tar -xzOf "$D/$I.tar.gz" ./etc/mu300/image-manifest.gz > "$D/$I.manifest.gz" 2>/dev/null || { echo "$I: no manifest to publish"; fail=1; }
# call audio is the audio extra's: no image carries its programs or services
! gzip -dc "$D/$I.manifest.gz" | grep -Eq '(opt/mu300/bin/(mu300-audio-dsp|mu300-voice|pcmhost|ctlset|mu300-hfp)|init\.d/mu300-(audio|voice)|modprobe\.d/mu300-audio\.conf)( |$)' ||
    { echo "$I carries the audio extra's programs or services"; fail=1; }
# what the package list changed since the last release (MU300_PREV_RELEASE: its directory, with the assets)
if [ -n "${MU300_PREV_RELEASE:-}" ] && [ -f "$MU300_PREV_RELEASE/$I.tar.gz" ]; then
    echo "==> $I: etc/mu300/packages.txt against the previous release"
    tar -xzOf "$MU300_PREV_RELEASE/$I.tar.gz" ./etc/mu300/packages.txt > "$D/packages.prev" 2>/dev/null || : > "$D/packages.prev"
    tar -xzOf "$D/$I.tar.gz" ./etc/mu300/packages.txt > "$D/packages.new"
    diff -u "$D/packages.prev" "$D/packages.new" || true
    rm -f "$D/packages.prev" "$D/packages.new"
fi
[ $fail = 0 ] || { echo "audit failed, nothing published" >&2; exit 1; }
rm -rf "$IN"
# the updater itself: an older mu300-update fetches this one and continues with it
cp "$TOP/openwrt/opt/mu300/bin/mu300-update" "$D/mu300-update"
# mu300-release: the tag, as a file, so that SHA256SUMS - and with it the signature - says which release these
# checksums are of (mu300-update's tag_sha: a signed SHA256SUMS cannot be handed out as another release's)
echo "$TAG" > "$D/mu300-release"
(cd "$D" && { shasum -a 256 *.tar.gz *.manifest.gz mu300-update mu300-release 2>/dev/null || sha256sum *.tar.gz *.manifest.gz mu300-update mu300-release; } > SHA256SUMS)
# The signature of SHA256SUMS (tools/sign-release.sh; mu300-update checks it where the system has the public key):
# made when the release key is here - MU300_RELEASE_KEY (the Release workflow's secret of that name) or
# MU300_RELEASE_KEY_FILE. Devices whose image carries the public key refuse a newer release that is not signed, so
# such a release is never published without its signature.
PUB=$TOP/openwrt/opt/mu300/lib/release.pub
SIG=
if [ -n "${MU300_RELEASE_KEY:-}${MU300_RELEASE_KEY_FILE:-}" ]; then
    if [ -s "$PUB" ]; then sh "$TOP/tools/sign-release.sh" "$D/SHA256SUMS" "$PUB"
    else
        echo "signing without $PUB in the tree: no device checks this signature until the public key is committed" >&2
        sh "$TOP/tools/sign-release.sh" "$D/SHA256SUMS"
    fi
    SIG=$D/SHA256SUMS.sig
elif [ -s "$PUB" ]; then
    echo "the images carry the release key ($PUB) and there is no MU300_RELEASE_KEY to sign with:" >&2
    echo "devices would refuse this release. Nothing is published without its signature." >&2
    [ "$PUBLISH" != --publish ] || exit 1
fi
ls -la "$D"

[ "$PUBLISH" = --publish ] || { echo "built release/$TAG (run again with --publish to upload)"; exit 0; }
commit=$(git -C "$TOP" rev-parse HEAD)
notes=$(mktemp "${TMPDIR:-/tmp}/mu300-notes.XXXXXX")
cat > "$notes" <<EOF
Linux $KV and OpenWrt with the MU300 control panel for the ZTE F50 (MU300) and the ZTE U30 Air. Update a device with
\`mu300-update apply\` (or System > Update in the panel).

| file | contents |
|---|---|
| mu300-kernel-7.2.tar.gz | Linux $KV: \`Image\`, every module (the kernel's own and the out-of-tree Wi-Fi, Bluetooth, modem, thermal and wake-source drivers), the generic boot ramdisk segment; the bundle of \`docs/contract.md\` |
| mu300-openwrt-luci-rootfs.tar.gz | OpenWrt with the MU300 control panel (luci-app-mu300 by kanoqwq, Aurora theme by eamonxg); English and Chinese |
| mu300-openwrt-luci-rootfs.manifest.gz | the list of the image's files (type, mode, owner, size, sha256): an update reads it to tell the image's files from yours, and carries yours over |
| mu300-update | the on-device updater of this release (\`mu300-update apply\` switches to it before it changes anything) |

The assets contain **no proprietary files**: the Wi-Fi/Bluetooth firmware and the Android modem userspace stay on the
device and are carried over by every update.

Built from $REPO@$commit with \`kernel/build.sh\`, \`kernel/build-modules.sh\` and \`tools/make-release.sh\`.
Corresponding source (GPL): Linux from kernel.org with \`kernel/port\` and \`kernel/patches\`; the out-of-tree modules
in \`kernel/modules\`. OpenWrt and its packages come from downloads.openwrt.org, busybox from Ubuntu's archive; Aurora
(luci-theme-aurora 1.4.0) from https://github.com/eamonxg/luci-theme-aurora/releases/tag/v1.4.0.
EOF
if gh release view "$TAG" -R "$REPO" >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    gh release upload "$TAG" -R "$REPO" --clobber "$D"/*.tar.gz "$D"/*.manifest.gz "$D/mu300-update" "$D/mu300-release" "$D/SHA256SUMS" $SIG
else
    # shellcheck disable=SC2086
    gh release create "$TAG" -R "$REPO" --target "$commit" --title "mu300-wrt $TAG" --notes-file "$notes" $PRERELEASE \
      "$D"/*.tar.gz "$D"/*.manifest.gz "$D/mu300-update" "$D/mu300-release" "$D/SHA256SUMS" $SIG
fi
rm -f "$notes"
