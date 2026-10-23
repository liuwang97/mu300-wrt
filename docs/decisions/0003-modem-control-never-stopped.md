# Android's modem_control runs unmodified and is never stopped
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
The PM co-processor cuts power about 290 s after boot (LK: `charge first poweron reset`) unless `sprd_pmic_wdt` (`kernel/modules/sprd_pmic_wdt`) disarms it over an SIPC channel that is ready only after `modem_control` has reloaded `pm_sys` and the modem through Trusty.

## Decision
The vendor `modem_control` and `refnotify` run in a chroot with Android's bionic linker (`openwrt/opt/mu300/bin/android-vendor-start`, started by `openwrt/overlay/etc/init.d/mu300-vendor`) and are never stopped, restarted or unloaded; radios go on and off through AT commands only. It is exec'd directly because `sprd_modem_loader` accepts ioctls only from a task whose comm is `modem_control`; `androidboot.slot_suffix` names Android's slot so its modem images load. The daemons switch to uid 1000 themselves, so device nodes are uid/gid 1000 (`openwrt/opt/mu300/bin/ueventd-perms.sh`).

## Consequences
- A stopped `modem_control` or missing modem image means a power cut within about five minutes.
- A closed SIPC AT channel stays silent until reboot, so AT owners hold their channel (`openwrt/src/atd-io/mu300-atd-io.c`).

## Revisit when
The watchdog can be disarmed without the modem.
