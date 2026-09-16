#!/bin/sh
# Pull the Android vendor runtime needed by modem_control from a rooted device (adb + su) and build
# the chroot subset used by the initramfs (/android) and the rootfs (/opt/mu300/android).
# These files are proprietary (ZTE/Unisoc/Google) and are NOT part of this repository.
set -eu
OUT=${1:-android-subset}
TMP=$(mktemp -d)
adb exec-out 'su -c "tar -chf - /apex/com.android.runtime /system/lib64 /vendor/bin/modem_control /vendor/bin/sh /vendor/bin/toybox_vendor /vendor/bin/getprop /vendor/lib64/libkernelbootcp.trusty.so /vendor/etc /dev/__properties__ 2>/dev/null"' | tar -xf - -C "$TMP"
rm -rf "$OUT" && mkdir -p "$OUT"
cd "$TMP"
tar -cf - apex/com.android.runtime/bin/linker64 apex/com.android.runtime/lib64/bionic \
  system/lib64/libcutils.so system/lib64/libexpat.so system/lib64/liblog.so system/lib64/libhardware_legacy.so \
  system/lib64/libc++.so system/lib64/libbase.so system/lib64/libbinder.so system/lib64/libbinder_ndk.so \
  system/lib64/libhidlbase.so system/lib64/libutils.so system/lib64/android.system.suspend-V1-ndk.so \
  system/lib64/libtrusty.so system/lib64/libandroid_runtime_lazy.so system/lib64/libvndksupport.so \
  system/lib64/libz.so system/lib64/libcrypto.so system/lib64/libselinux.so system/lib64/libpcre2.so \
  system/lib64/libpackagelistparser.so system/lib64/libprocessgroup.so system/lib64/libcgrouprc.so \
  vendor/lib64/libkernelbootcp.trusty.so vendor/bin/modem_control vendor/bin/sh vendor/bin/toybox_vendor vendor/bin/getprop \
  vendor/etc/modem_cp_info.xml vendor/etc/modem_sp_info.xml vendor/etc/modem_ch_info.xml vendor/etc/cp_dump_info.xml \
  vendor/etc/ueventd.rc dev/__properties__ | tar -xf - -C "$OLDPWD/$OUT"
cd "$OLDPWD"
mkdir -p "$OUT/system/bin" "$OUT/linkerconfig"
ln -sfn /apex/com.android.runtime/bin/linker64 "$OUT/system/bin/linker64"
: > "$OUT/linkerconfig/ld.config.txt"
rm -rf "$TMP"
du -sh "$OUT"
