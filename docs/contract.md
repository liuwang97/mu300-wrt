# Kernel ↔ OpenWrt contract

Contract version: **1** (the bundle's `contract` file). Applies to Linux 7.2.11 and OpenWrt 25.12.6.

This is the only document that both trees depend on. `kernel/` never reads a file under `openwrt/`, and `openwrt/`
never reads a file under `kernel/`: it uses the bundle the kernel tree builds (a local file, or a release asset
checked against `SHA256SUMS`). `tools/check-boundary` enforces this. A change to anything below changes both trees and
this file in one commit, and raises the contract version when an older counterpart would break.

## Boards

ZTE F50 (MU300) and ZTE U30 Air, both UMS9620. One kernel and one image serve both. The initramfs tells them apart by
the stock device tree: only the U30 Air's has a `charger_policy_service` node. The ramdisk's device segment may name
the board in `/etc/mu300-device` (`f50` or `u30air`); that label wins.

## Kernel bundle: `mu300-kernel-7.2.tar.gz`

| entry | content |
|---|---|
| `Image` | the LK-loadable kernel (`kernel/wrap-image.py`) |
| `ramdisk-generic.lz4` | the generic ramdisk segment: `init`, busybox, logdw, e2fsck, `ueventd-perms.sh`, the modules of `module-order.txt` |
| `ramdisk-generic.json` | the builder's list of what that segment holds |
| `modules/*.ko` | every module, the kernel's own and the out-of-tree ones, flat |
| `kernel.release` | `uname -r` of the Image |
| `devices` | `f50 u30air` |
| `features` | one per line: `sdcard`, `linux-slot`, `gpt-root` |
| `contract` | the contract version this bundle follows (`1`) |
| `autoload-blocked` | module names the on-demand loader must refuse (see below) |
| `modules.builtin`, `modules.builtin.modinfo` | built-in drivers, for modprobe |
| `modules.in-tree`, `modules.list`, `kernel.config` | the build's module list, the bundle's module list, its `.config` |

The updater refuses a bundle whose `devices` does not name the board, whose `features` lacks what the installed system
needs, or whose `contract` it does not know.

## Where the bundle goes on the root filesystem

- `modules/*.ko` → `/lib/modules/<kernel.release>/`, flat; OpenWrt's kmodloader loads them by name.
- `autoload-blocked` → `/lib/modules/<kernel.release>/autoload-blocked`. `mu300-modprobe-guard` (the kernel's
  `modprobe` helper on the device) reads it at run time; a request that resolves to a listed module is refused.
- `modules.builtin*` → `/lib/modules/<kernel.release>/`.
- `Image` and the generic ramdisk segment go into the Linux slot's boot partition, behind the device segment of the
  ramdisk that is already there.

The root filesystem image carries no kernel modules.

## What the initramfs hands to the root filesystem

- Systems on the Linux disk: `$DISK/openwrt-luci`, chosen by `$DISK/.mu300/boot-os` (a name with a dot is never
  booted; `openwrt-luci.old` is a kept copy).
- The disk: the mu300root region on the eMMC, the eMMC GPT partition `mu300root`, or an SD card with the ext4 label
  `mu300sd`. Mounted at `/mnt/mu300-disk` inside the system.
- `/run/mu300/{device,linux-slot,disk-dev,misc-dev,boot-mode,usb-net-applied,misc-bc-android.bin,misc-bc-linux-trial.bin}`,
  `/run/mu300/charging-boot`, `/run/mu300/fsck.log`, `/run/mu300-root-dev`, `/run/mu300-usb-host-mac`, `/run/stages`.
- `$DISK/.mu300/{boot-tries,boot-attempts,boot-lock,root-on-sd}` and the CPU voltage files
  `$DISK/.mu300/cpu-voltage{,-trial,-good,-armed,-rejected}`.
- Read from the system before switch_root: `etc/mu300/usb-net` (`ncm`, `ecm`, `rndis`), `etc/mu300/default-boot`
  (`linux`), `etc/mu300/power.conf` (`POWER_ON_CHARGER=1`).
- The USB gadget `linux` in configfs, bound to `25100000.dwc3`, with `usb0` (NCM/ECM) or `rndis0`, and an early DHCP
  server that the system replaces. Subnets: F50 `192.168.77.0/24`, U30 Air `192.168.78.0/24`.
- `/sbin/kmodloader` of the system is overlaid once by a stub that skips procd's first `modules-boot.d` scan.

## Device nodes and sysfs the system uses

- `/dev/{modem,pmsys,sctl_pm,trusty-ipc-dev0,stty_nr*,spipe*}`, `/proc/mdbg`.
- `/sys/class/udc/25100000.dwc3`, the Type-C port, `ump9620_tcpc` parameter `enable=1` (U30 Air).
- LEDs `sc27xx:*` and `zte-ldo0` … `zte-ldo2`.
- power_supply `sc27xx-fgu` and `bq256xx-charger` (U30 Air).
- `/sys/module/sprd_cpufreq_v2_driver/parameters/{voltage_offset_uv,voltage_state}`.

## Wi-Fi access point (`sprd_wlan_combo`)

When the SC2355 firmware can run SAE for a soft AP, the wiphy advertises `NL80211_EXT_FEATURE_SAE_OFFLOAD_AP` and
`max_num_akm_suites` 3. START_AP hands the firmware nl80211's SAE password (hunting-and-pecking only: H2E, FT-SAE, an
empty or longer than 63-byte password fail START_AP); AUTH frames stay with the firmware. A new station's association
elements reach cfg80211 with the firmware's PMK record appended (`dd 34 40:45:da:04 <PMK 32><PMKID 16>`); hostapd
takes the PMK from it and runs the 4-way handshake.

## CPU voltage offset

The initramfs is the only writer of `voltage_offset_uv`; the root filesystem's `mu300-cpu` only writes the request
files above. Both sides hold the same limits: three values (domains CPU 0-3, 4-6, 7) in whole 3125 µV steps within
-25000 … 12500 µV, applied only when both SML copies (`sml_a`, `sml_b`) hash to a known build:
`ff4782690fd805ff54fe34853ef8ee5c7e51c3259e20077c55373b50a4b259c2` (F50) and
`f27ac16f44e6cdc87a8ee00a9b53c499c8dde0c3b92c771388e256e84ef12e70` (U30 Air). Anything else boots at 0,0,0.

## Firmware

`/lib/firmware/{wcnmodem.bin,gnssmodem.bin,wifi_board_config*.ini,bt_configure_*.ini}` come from the device's own
Android partitions and are never published. `regulatory.db` is the signed upstream database.

## Command line

The kernel is built with `CMDLINE_FORCE`: the bootloader's arguments are ignored. User space reads `androidboot.*`
from `/proc/device-tree/chosen/bootargs`.
