# Linux in the spare A/B slot, Android as the fallback
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
LK decrements `tries_remaining` before booting a slot and rolls back at `tries == 1 && !successful`. Linux must be the default, fall back to Android after real failures, and survive one interrupted boot (plug pulled in the first minute).

## Decision
Linux boots from the slot Android does not run from; the 32-byte `bootloader_control` block is at offset 0x800 of `misc`. `openwrt/opt/mu300/bin/mu300-next-boot` re-arms the slot with `tries = N + 1` (N = 1-6, default 5, `.mu300/boot-attempts`) once USB network and SSH answer (`bootok` in `openwrt/overlay/etc/init.d/mu300-post`). `kernel/initramfs/init` keeps its own counter (`boot-tries`) as backstop and skips restoring Android's slot when `/etc/mu300/default-boot` says `linux`. `mu300-next-boot lock` sets `successful = 1` so LK never rolls back; `mu300-update` arms a trial before writing a boot image. `misc` is written and read back before `default-boot`, under `flock`; a block pair that does not match the slot names is never written.

## Consequences
- N unconfirmed boots stay in Linux, the next is Android (measured N=5: LK booted from tries 6, 5, 4).

## Revisit when
LK's rollback rule changes, or exhaustion at `tries = 1` is observed on a device (never run to exhaustion).
