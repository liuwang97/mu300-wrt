# Boot chain

Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

What LK, the boot image and `kernel/initramfs/init` do before OpenWrt runs; what init hands over is in
[`docs/contract.md`](../../docs/contract.md).

## Slots, misc and LK

- Linux boots from the slot Android does not run from. `linux_slot_detect` reads LK's `androidboot.slot_suffix` from
  `/proc/cmdline` or, because the kernel forces its own command line, `/proc/device-tree/chosen/bootargs`; then
  `etc/mu300-linux-slot` of the image, then b.
- The AOSP `bootloader_control` block (32 bytes, magic `BCAB`) is at offset 0x800 of `misc`. Slot a's byte is 12, slot
  b's 14 within it: priority | tries << 4 | successful << 7; the last four bytes are a CRC-32 of the first 28.
- Unisoc LK treats `tries_remaining == 1 && successful_boot == 0` as an already failed boot and rolls back. A one-shot
  trial of the Linux slot therefore needs tries 2: LK decrements to 1 and boots, a failure rolls back next time
  (unverified on 7.2). `mu300-next-boot` arms it.
- `mu300-next-boot` writes only a pair of blocks that agree: Linux's names Linux's slot, Android's names the other.
  Every command but `status` stops with "do not match" otherwise; init never publishes a mismatched pair
  (`publish_slot`).
- Android's slot block is restored once the eMMC exists (~10 s in). Default-Linux mode (`etc/mu300/default-boot` =
  `linux`) skips the restore and leaves LK's decremented tries.
- The `uboot_log` partition (16 MiB ring) holds LK's log: which slot was chosen and why it reset.
- Per slot, LK finds `uboot`, `sml`, `teecfg`, `trustos` already loaded by the SPL (eMMC boot areas
  `mmcblk0boot0`/`boot1`, identical, managed by LK). Its `avb_slot_verify()` reads `vbmeta_<slot>` and through it
  `boot`, `dtbo`, `vendor_boot`, `nr_modem`, `nr_phy`, `pm_sys`. The device is unlocked (`allow_verification_error`
  is 1): a mismatch is logged and the boot goes on.
- The modem images used are Android's slot's: `android-vendor-start` rewrites `androidboot.slot_suffix` to Android's
  slot in the command line it gives the vendor daemons, so `modem_control` loads them and hands them to the Trusty TA
  `kernelbootcp` of the Linux slot's Trusty. After an OTA the other slot still holds the factory `uboot`, `sml`,
  `teecfg`, `trustos`, `vbmeta`: Linux then runs the factory TEE against new modem images (unverified on 7.2). NV and
  calibration partitions (`nr_fixnv1/2`, `nr_runtimenv1/2`, `prodnv`, `persist`, ...) belong with their slot: never
  copy them between slots.

## Boot image and ramdisk

- vendor_boot's ramdisk is LZ4 legacy. On 5.4 a gzip boot ramdisk appended to it was not unpacked (`RAMDISK:
  incomplete write`, `VFS: Unable to mount root fs on unknown-block(1,0)`) (unverified on 7.2).
  `kernel/initramfs/build-boot-image.py` compresses with `lz4 -l` and puts a `dev/console` node in the cpio.
- With mainline the boot ramdisk is the device segment followed by the generic segment (`init`, `bin/busybox`,
  `bin/logdw`, `sbin/e2fsck`, `etc/module-order`, the modules of `kernel/initramfs/module-order.txt`). The generic
  segment's `init` replaces the device's, so init may not trust device-specific constants in its own file (the region
  offset below). `cpio_archive()` writes every parent directory: the kernel's unpacker creates none and would drop files.
- The boot partition is 64 MiB; kernel, ramdisks and vbmeta must end below 48 MiB, where the persistent log starts.
- Without a serial console init logs every stage to `/dev/kmsg` at KERN_ERR, `/dev/pmsg0` and `/run/stages`. pstore
  survives only a warm reset and most failures end in a power cut, so init also writes the stage list, `/proc/modules`
  and `dmesg` every 15 s into 8 MiB at 48 MiB of the Linux slot's boot partition (`persist`), readable from Android.
- A CDC-ACM function (`acm.GS0`) sits beside the network function: a console that works when networking does not.
- Command line (`CMDLINE_FORCE`, `kernel/mu300-mainline.config`): `regulator_ignore_unused` keeps `LDO_VDDSIM0`, the
  modem's SIM supply that no Linux driver claims. `fw_devlink=permissive` is needed or the USB gadget waits for
  suppliers of the vendor device tree that no driver provides, and the boot still counts as good. `clk_ignore_unused`
  keeps 315 of 551 clocks that nothing holds: the modem, the Wi-Fi firmware and vendor modules touch hardware behind
  some without the clock API (unverified on 7.2). `pd_ignore_unused` keeps nothing in particular.

## Finding the root filesystem (`kernel/initramfs/init`)

- The eMMC and the card are told apart by type (`/sys/block/mmcblkN/device/type`: `MMC`, `SD`), never by number: both
  sdhci-sprd hosts probe asynchronously. `kernel/port/install.py` makes the slot's host return `-EPROBE_DEFER` until the
  eMMC's host is added, so the eMMC is `mmc0`; if that host never binds there is no root on the card either. Every
  GPT-name lookup (`misc`, `boot_<slot>`, `by-name` links, `mu300-update`'s kernel partition) uses only the eMMC: a card
  may carry the same names (raw clone of a backup) and these are written to.
- `misc` and `boot_<slot>` are looked up only after the vendor modules (sdhci-sprd is one): an earlier lookup gives
  `misc-NOT-FOUND`, no slot restore and no persistent log.
- Order: (1) if the device segment holds `/etc/mu300-desktop-root-mode` = `part`, the GPT partition first; this is a
  preference, not a gate. (2) the SD card. (3) the region, else the GPT partition, else the default offset.
- Region (`find_root_offset`): the number `ROOT_OFFSET` in init is only the last candidate. init tries the first 2 MiB
  boundary after the last partition and takes the first candidate that holds ext4 (magic at 1080) with label
  `mu300root`; log `stage=root-offset found=... (default ...)`. It is attached with `losetup -o` and mounted noatime;
  `/run/mu300-root-dev` reads `<eMMC>@<offset>`. A wrong offset ends in the rescue below.
- GPT partition (`gpt_root`, kept on purpose; the partition path is unverified on 7.2): the eMMC's partition named
  `mu300root` holding ext4 whose whole 16-byte label is `mu300root` (`mu300rootfs` is refused, a foreign ext4 is never
  used). Mounted as is, no loop; `/run/mu300-root-dev` reads `<eMMC>@mu300root`; stage `root-part`. Of several
  partitions with that name the first is used.
- SD card (`find_sd_root`): ext4 with label `mu300sd`, on the first partition of an `SD` disk or on the whole card; the
  eMMC is never looked at. The label differs from `mu300root` so a device holding both never confuses them. A
  system on the internal disk may leave `.mu300/root-on-sd`: init then waits up to 8 s for the card (30 s while the host
  has a card whose block device is still being set up, `sd_coming`) and otherwise boots the internal system
  (`stage=sd-root-missing`). A card with the label but no system (`sd-root-empty`) falls through to the internal root (unverified on 7.2).
  With no internal system the card gets the same wait. `mu300-update` refuses a kernel bundle without `sdcard` in its
  `features` while the running system is on the card.
- Before every root mount `root_fsck` reads one byte (`s_state`, offset 1082). With the ext4 ERROR_FS bit set it runs
  `/sbin/e2fsck -fy` on the unmounted device with `broken_system_clock = 1` (no NTP yet), TERM after
  `MU300_FSCK_TIMEOUT` = 120 s, KILL 15 s later. It always returns 0: a damaged filesystem or a hang never stops a boot
  (unverified on 7.2). Output in `/run/mu300/fsck.log`; stage lines `fsck-*`; `MU300_FSCK=force|off`.
  e2fsck is a static musl build of e2fsprogs 1.47.4 (`kernel/initramfs/e2fsck/build.sh`).
- No root: stage `rootfs-unavailable`, Android's slot is restored, a rescue shell (telnet, httpd on the USB address)
  runs, and a timer started at init reboots after 300 s unless `/run/stay` exists. When Linux is locked
  (`mu300-next-boot lock`) the rescue stays up and `/run/to-android` goes to Android by hand.
