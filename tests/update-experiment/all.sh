#!/bin/sh
# Every image of a release updated by both paths (lab.sh), inside the container:
#   all.sh OLD_RELEASE_DIR NEW_RELEASE_DIR NEW_TAG OUT_DIR LABEL [ANDROID_INSTALL_SH] [IMAGES]
# OUT_DIR gets LABEL-device-<image>.tsv and LABEL-android-<image>.tsv, and one table per image, LABEL-<image>.md.
# IMAGES: of "ubuntu ubuntu-26.04 openwrt openwrt-luci" the ones to run (default: all four).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
old=$1 new=$2 tag=$3 out=$4 label=$5
script=${6:-$HERE/../../tools/android-install.sh}
images=${7:-ubuntu ubuntu-26.04 openwrt openwrt-luci}
mkdir -p "$out"
for image in $images; do
    os=$image; ubuntu=
    case $image in ubuntu-26.04) os=ubuntu; ubuntu=26.04 ;; esac
    asset=mu300-$image-rootfs.tar.gz
    echo "== $image: the device's updater"
    sh "$HERE/lab.sh" device "$os" "$old/$asset" "$new" "$tag" "$out/$label-device-$image.tsv" || echo "FAILED"
    echo "== $image: the installers' update"
    sh "$HERE/lab.sh" android "$os" "$old/$asset" "$new/$asset" "$script" "$out/$label-android-$image.tsv" || echo "FAILED"
    python3 "$HERE/experiment.py" table "mu300-update apply=$out/$label-device-$image.tsv" \
        "installer, Magisk zip=$out/$label-android-$image.tsv" > "$out/$label-$image.md"
    tail -n 2 "$out/$label-$image.md"
done
