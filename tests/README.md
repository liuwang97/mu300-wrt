# Tests

```sh
python3 -m unittest discover -s tests            # everything this machine can run
MU300_TEST_SHELLS="busybox sh" python3 -m unittest discover -s tests   # one shell only
powershell -File tests\installer.Tests.ps1        # Windows: install.ps1's functions
```

Standard library only (`lz4`, the command or `pip install lz4`, for the boot image tests). CI runs them on Ubuntu,
macOS and Windows (`.github/workflows/tests.yml`).

| file | what |
|---|---|
| `test_static.py` | every script parses under each shell that runs it; device programs are executable; rules from past bugs (no double quotes in `install.ps1`'s device commands, ASCII-only PowerShell, init looks for partitions after the modules) |
| `test_i18n.py` | `tools/i18n.sh`: every translation with every placeholder, arguments passed through untouched, answers in all three languages |
| `test_device_scripts.py` | `mu300-device`, `mu300-lan-ip`, `mu300-led` (both devices, 4G/5G, the timeout, the siren, the 5.4 LDO switches), `thermal-guard` (the heat alarm), `mu300-nfc` (a fake NFC tag: ZTE's own Wi-Fi record byte for byte, URLs, text, what `sync` leaves alone), `mu300-ttl` (stub `nft`), `mu300-wifi-band`, `mu300-buttons`, `mu300-usb` (a fake charger: never 5 V against a supply), `mu300-next-boot` and `early-recorder` on slot a and slot b (`NextBoot`, `EarlyRecorder`) |
| `test_installer.py` | which adb device the installers take: they ask whenever it is not the only one and an F50/U30 Air; the storage choice (`Storage`), the settings the installer hands to the device (`InstallEnv`), the hotspot import on a card (`ImportHotspot`), erasing the card (`SdErase`) and the free region (`Region`) |
| `test_vpn.py` | `mu300-vpn`: VLESS URI parsing, JSON, which networks stay out of the tunnel, the sing-box config, where the engines are found and how a VPN that is on gets them back |
| `test_extra.py` | extras: `mu300-update`'s install, adopt and update of extras (a VPN in use keeps its engines), `mu300-extra` install/remove/link against a fake release server |
| `test_update.py` | `mu300-update`: release files per system and kernel, boot image byte helpers, whether a kernel bundle may go onto this device, the Linux slot it works on, and the boot image it builds from a device's own stock image (`FromStock`) |
| `test_boot_init.py` | `boot/init`: the card is looked for before the internal region, under any `mmcblkN`; a foreign or empty card falls back; the 300 s timer and the conditional wait; the slot init takes (`Slot`: command line, device tree, image, a mismatch) and the block it restores |
| `test_emmc_lookup.py` | every Linux-side lookup by partition name (init, `early-recorder`, `mu300-update`, `android-vendor-start`) takes the eMMC's partition, never a card's with the same GPT names, whichever of `mmcblk0`/`mmcblk1` the card is; `emmc_dev` never falls back to a card; `ueventd-perms.sh` touches no block device by number |
| `test_android_mount.py` | `tools/android-mount-mu300root.sh -u`: an unmount whose loop Android's umount already freed succeeds, a loop left attached is detached, a failed umount or detach fails |
| `test_android_install.py` | `tools/android-install.sh` on the SD card: a blank card is formatted, a foreign ext4 and an adopted or busy card are refused; the marker of an internal installation; pushed extras, and the engines a VPN in use keeps on update |
| `test_boot_image.py` | `boot/build-boot-image.py`: the generic ramdisk, the U30 Air's modules and order, the four `misc` blocks and `--linux-slot` (`SlotBlocks`) |
| `test_magisk_installer.py` | `android/magisk/installer/mu300-install.sh` on a fake device (`fakedevice.py`): `Conf` (the settings files: trusted and untrusted, validation, never run), `AndroidSide` (Android's own shell, not Magisk's busybox), `Plan` (model, slots, where Linux goes, refusals, the dry run, the example file), `WorkDirectory` (the root-only directory, nothing mounted is deleted), `Install` (what is written, verified, `misc` armed last; a failure leaves Android booting), `Password` (generated, hashed, where it is written, removed from the settings file) |
| `test_magisk_zip.py` | `customize.sh` in a fake Magisk environment (`Customize`), `tools/make-magisk-zips.sh` (`Builder`: eight zips, the allow-list, the manifest) and, with `MU300_MAGISK_ZIPS` pointing at a release's real zips, `ReleaseZips` |
| `test_magisk_switch.py` | `android/magisk/mu300-linux-switch/switch.sh`: the block goes to the slot Android is not on, with the CRC the bootloader checks; refuses a copy of Android's boot image |
| `test_android_boot_image.py` | `tools/android-boot-image.sh`: the pieces of the boot image only the device can make (the `misc` blocks, Android's files, the device segment), compared with `boot/build-boot-image.py` on the same inputs |
| `fakedevice.py` | not a test: the fake F50 the Magisk tests run on (block devices as files, a sysfs tree, stubs for Android's tools and a fake mount table) |
| `installer.Tests.ps1` | `install.ps1`: `T` with every translation, `NormalizeAnswer`, `Gib` |

The device scripts run under dash (Ubuntu's `/bin/sh`), bash and busybox ash (OpenWrt); every shell test runs under
each of them that is installed. Commands that touch the device (`nft`, `ip`, `id`, `sing-box`) are stubs, and the
scripts read a fake `/` through `MU300_SYSROOT`, `MU300_DISK` and friends; `mu300-update` and `mu300-vpn` are sourced
with `MU300_LIB=1`, which defines their functions and runs nothing.
