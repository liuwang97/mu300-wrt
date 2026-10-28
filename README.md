# mu300-wrt

Linux 7.2 and OpenWrt with the MU300 control panel for two UMS9620 pocket routers: the **ZTE F50 (MU300)** and the
**ZTE U30 Air**. One kernel bundle and one root filesystem image serve both; the boot ramdisk tells them apart.

This is a hard fork of [dikeckaan/mu300-linux](https://github.com/dikeckaan/mu300-linux) (the base is in `UPSTREAM`),
narrowed to one kernel, one system and two boards:

| | here | not here (any more) |
|---|---|---|
| kernel | mainline Linux 7.2 | vendor 5.4, 6.18, 7.3 |
| system | OpenWrt 25.12.6 + LuCI panel (`openwrt-luci`) | Ubuntu, Arch, ImmortalWrt, plain OpenWrt |
| boards | F50, U30 Air | V50 |
| installing | `mu300-update` on a device that runs Linux | install.sh/ps1/cmd, GUI, web installer, Magisk zips |
| LuCI languages | English, Chinese (zh_Hans) | the other 29 |
| access | SSH, the LuCI panel, the USB serial console | adbd, OpenWrt failsafe |
| GPU | none yet (Panfrost later) | the vendor Mali driver and its userspace |

Kept on purpose: Bluetooth, the audio DSP memory reservation, the SD card as the home of the
system, and the eMMC GPT partition `mu300root`.

## Getting it onto a device

There is no installer. A device that already runs OpenWrt with the panel on Linux 7.2 (installed with the upstream
project's installer) switches to this project's releases by pointing its updater here:

```sh
# once: this project's release key (an upstream image carries upstream's, which refuses these signatures)
wget -O /opt/mu300/lib/release.pub https://raw.githubusercontent.com/liuwang97/mu300-wrt/main/openwrt/opt/mu300/lib/release.pub
MU300_REPO=liuwang97/mu300-wrt mu300-update apply   # the first time; this release's updater then defaults to it
reboot
mu300-update check        # later: newest release vs installed
mu300-update apply        # kernel bundle and image: download, verify, unpack beside the old system, switch
```

The panel does the same under System > Update. Everything you added or changed is carried over (the image manifest
tells the image's files from yours); the previous system stays as `openwrt-luci.old` for `mu300-update rollback`
until `mu300-update clean`, and the previous kernel and boot image for `mu300-update rollback-boot`. A kernel that
does not come up sends the device back to Android after the usual number of failed boots.

An update can also come from files: `mu300-update apply --from DIR` with `mu300-kernel-7.2.tar.gz`,
`mu300-openwrt-luci-rootfs.tar.gz` and `SHA256SUMS` in DIR.

## Building

Two trees, each with its own build and tests; they meet only in the kernel bundle (`docs/contract.md`).

```sh
# kernel/: Linux 7.2.13 + port + patches + out-of-tree modules (cross-compiles off arm64)
MU300_SRC=$HOME/mu300src bash kernel/build.sh
MU300_SRC=$HOME/mu300src bash kernel/build-modules.sh
sh kernel/make-bundle.sh mu300-kernel-7.2.tar.gz          # needs docker with linux/arm64 (e2fsck)

# openwrt/: the image (docker with linux/arm64)
sh openwrt/build-rootfs.sh

# both, audited, as a release (and --publish to put it on GitHub)
sh tools/make-release.sh "$(sh tools/make-release.sh --next-tag)"
```

The kernel build needs `aarch64-linux-gnu-gcc`, `bc bison flex libssl-dev libelf-dev pahole lz4 cpio kmod gpgv`
(or `docker build kernel/` on an arm64 host). `kernel/build.sh` checks the kernel.org signatures of the source it
downloads (`kernel/keys/`).

## Tests

```sh
cd kernel/tests && python3 -m unittest
cd openwrt/tests && MU300_TEST_SHELLS='busybox sh' python3 -m unittest
sh tools/check-boundary     # kernel/ and openwrt/ never read each other's files
sh tools/doc-lint           # documentation sizes; no references to the old notebook
```

## Layout

| path | what |
|---|---|
| `kernel/` | patches, mainline port, out-of-tree modules (modem, Wi-Fi, Bluetooth, thermal, wake sources), the initramfs, the bundle |
| `openwrt/` | device scripts (`opt/mu300`), overlays, the LuCI app, static programs, the updater, the image build |
| `docs/contract.md` | the interface between the two trees |
| `docs/decisions/`, `docs/open-issues.md` | decisions in force; problems not solved |
| `kernel/docs/`, `openwrt/docs/` | how things are, per subject |
| `tools/` | release, signing, device access, repository checks |

## Safety

- Android stays on the device; Linux boots from the other slot and falls back to Android on its own.
- Never stop `modem_control` or the modem on a running device: the PM co-processor cuts the power about 290 s later.
- Back up `boot_a`/`boot_b` before writing them by hand; never copy NV partitions between slots.

## Credits and licenses

- Linux from kernel.org, the Unisoc drivers from ZTE's GPL release for the U30 Air, the Wi-Fi and Bluetooth
  drivers from the realme C51/C53 kernel release: GPL-2.0, as are the patches in `kernel/patches`.
- dikeckaan/mu300-linux and its contributors: everything this fork grew from. kanoqwq: `openwrt/luci-app-mu300`, the
  SD card installation, and fixes ported from his fork. eamonxg: the Aurora LuCI theme, downloaded at build time,
  pinned by hash.
- Scripts, tools and documentation: MIT (`LICENSE`).
- Stock firmware, Android vendor components and bootloaders belong to their owners and are never distributed here.
