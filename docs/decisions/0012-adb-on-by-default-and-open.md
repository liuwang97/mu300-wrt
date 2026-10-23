# adb over USB is on by default and accepts every computer
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
The owner chose usability over lockdown: adb accepts any computer without key authorisation, so tools reach a device without first placing a key.

## Decision
`openwrt/src/adbd/mu300-adbd.patch` reads `MU300_ADB_AUTH`, set by `openwrt/opt/mu300/bin/mu300-adbd` from `/etc/mu300/adb` (`on`/`off`) and `/etc/mu300/adb_auth` (`open`/`keys`), both written by `mu300-usb`. No state means on and open; `open` is honoured only from a regular root-owned file nobody else can write; a damaged file means keys (or off). There is no TCP or vsock listener.

## Consequences
- Anyone with a cable gets a passwordless root shell (hotspot key, VPN profiles, SMS pool, SSH host keys, modem); the LAN, hotspot clients and mobile network are unaffected. Close it with `mu300-usb adb auth keys` or `adb off`.
- adb cannot be on without `CONFIG_USB_CONFIGFS_F_FS` or while the U30 Air's port is a USB host.

## Revisit when
The tools that motivated "accept everyone" are gone, or a Windows host is measured.
