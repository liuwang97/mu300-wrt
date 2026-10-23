# Hardware

Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

The kernel-to-rootfs interface is in [`docs/contract.md`](../../docs/contract.md); this file describes the silicon and
the eMMC the kernel tree has to live with.

## SoC and boards

- Both boards use the Unisoc T760 (UMS9620, "qogirn6pro") and the board name `ums9620_2h10_feimao`. 2 GiB RAM, of which
  ~1.4 GiB is visible to Linux (see Memory). PMIC UMP9620 (+UMP9621), fuel gauge `sc27xx-fgu`.
- Wi-Fi/BT is the SC2355 "Marlin3" on PCIe (firmware `MARLIN3_20A_RLS2_W24.45.4`). USB is DWC3 (`25100000.dwc3`) plus
  MUSB (`musb-hdrc.1.auto`), Type-C on the PMIC.
- The bootloader is Unisoc LK ("sprdlk") with Trusty TEE and A/B slots. `boot` and `vendor_boot` are Android header
  v4, page size 4096, with LZ4 legacy ramdisks.
- The F50 has no battery: its fuel gauge reports made-up values. The device tree's charger entry (bq2560x) is not F50
  hardware, nothing answers at I2C 0x6b there. Patch 0016 binds the charger at 0x1a only where `/charger_policy_service`
  exists, so nothing binds on the F50.
- The U30 Air has the F50's SoC, 64 GB eMMC and partition layout (partitions end at sector 54218752, so the Linux
  region has the same offset). It differs by ZTE's "SQC" charger stack (`sqc_charger`, `sqc_bq2560x`,
  `charger_policy_service`, `zte_power_supply`), `SC27XX_PD`, GPIO/LDO LEDs, a SAR sensor and an NFC tag. Firmware
  `U30Air_SSV1.0.0B14`. Its charger is an SGM41511 (a bq25601) on I2C bus 6 at 0x6b.
- The U30 Air has no SD host (`/sys/class/mmc_host` holds `mmc0` only), so SD-root logic never applies there. Of its
  GPIO LEDs only `net_blue` is wired; the rest are LDO-switched camera supplies and the PMIC keypad backlight sink.
- The F50 and the U30 Air are told apart by the stock device tree (docs/contract.md, "Boards"):
  `kernel/initramfs/init` applies the rule and writes the board to `/run/mu300/device`.

## eMMC layout and the Linux region

- F50 (64 GB variant): 122159104 sectors (~58.25 GiB). `userdata` (20 GiB) ends at sector 54218752, the GPT's last
  usable LBA is 122155007, the backup GPT is at the very end. The ~32.4 GiB between belong to no partition and read as
  zeros: they hold the Linux region (`mu300root`). GPT, `userdata` and the Android partitions stay unchanged, so Android
  and its AVB chain keep working.
- `userdata` cannot hold the system: metadata encryption (`dm-default-key`, `inlinecrypt`) covers the whole partition and
  the key lives in keymaster and `metadata`, unwrapped by Android. From Linux it reads as ciphertext (4079 of the first
  4096 bytes non-zero, no ext4/f2fs magic). For the same reason it can be neither shrunk nor shared.
- `blackbox` (500 MiB) and `fulldumpdb` (2 GiB) are not spare: the firmware writes crash dumps there (`fulldumpdb`
  non-zero in 16 of 16 samples).
- A 32 GB unit (29.12 GiB, 61079552 sectors, partitions end at sector 40095744, `userdata` fills the rest) has no gap.
  `userdata` is the last partition, so shrinking its GPT entry moves only its end and leaves every other offset and the
  AVB descriptors valid; that argument holds for no other partition, and the change erases Android data. Such a unit
  uses the GPT partition `mu300root` or the SD card instead (`boot-chain.md`).
- A read past the end of the eMMC never completes on the stock kernel (unverified on 7.2): the `dd` stays runnable at
  100 % of a core, survives `kill -9`, and every retry adds one (four held the SoC at 97 C). The region probe could
  place a superblock past the end, e.g. on a unit whose partition table ends 2 MiB before the disk's end. Therefore a
  region whose boundaries cross counts as empty, and a candidate superblock is read only when it lies on the disk
  (`find_root_offset` in `kernel/initramfs/init`). Any other probe must apply the same rule.

## Memory

- Boot log on the F50: `Memory: 1430144K/2097084K available ... 617788K reserved` (unverified on 7.2). The device tree
  reserves 464 MiB for the modem firmware (`cp-modem@88000000`, needed for 4G/5G), 24 MiB Trusty (`tos-mem`), 8 MiB SIPC
  shared memory, 3 MiB DDR training data and small areas. The kernel image (~35 MiB) and page tables for 2 GiB (~32 MiB)
  make up the rest. `cma_share` (48 MiB) stays usable for movable pages.
- `logobuffer` (9 MiB, there is no display) and `sysdump-uboot` (16 MiB, bootloader crash dumps) are given back to Linux
  by `kernel/patches/0003-of-reserved-mem-skip.patch`: `CONFIG_OF_RESERVED_MEM_SKIP="logobuffer,sysdump-uboot"` in
  `kernel/mu300-mainline.config` (MemTotal 1447 -> 1473 MiB). A command-line option alone would not do, LK does not pass
  the boot image's command line on and the kernel is built with `CMDLINE_FORCE`. The modem and Trusty regions must stay.
  Shared regions with the modem are reserved inside System RAM (no `no-map`); the drivers map them non-cached
  (`vendor-modules.md`).

## Audio

- The F50 has no speaker path: the device tree enables the sound card, the UMP9620 codec and an AW883xx amplifier at
  `6-0034`, but nothing answers at I2C 0x34 and there is no AGDSP firmware partition (no `l_agdsp`). Only Bluetooth or
  USB-host audio exists (unverified on 7.2).
- ZTE kept the audio description and dropped the reservation: `audio-mem-mgr` carries every audio address but no
  `memory-region`. `kernel/patches/0022-of-reserved-mem-add.patch` (`CONFIG_OF_RESERVED_MEM_ADD="0xaf600000,3M;0xaf900000,7M"`)
  reserves the DSP's ranges with `memblock_reserve()`, so they keep their struct pages (the audio driver maps them with
  `vmap()`). The Unisoc call-audio stack itself is not built here (an out-of-tree extra); the kernel only keeps the
  reservation and the ASoC/dmaengine core options (`SND_SOC=m`, `SND_SOC_SPRD=m`, `SND_DYNAMIC_MINORS`, `DMADEVICES`).
- An AGDSP image has its addresses compiled in and boards differ: an image laid out for another board takes this one
  down. MMIO of the AGCP domain (VBC `0x56510000`, MCDT, the AGCP AHB syscon) hangs the bus while the AGDSP sleeps and
  can drop the board off USB, so never peek at those addresses; this kernel has no `/dev/mem`.
