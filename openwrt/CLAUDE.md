# openwrt/

The root filesystem: OpenWrt 25.12.5 with the MU300 control panel (system name `openwrt-luci`), for the ZTE F50 and
the ZTE U30 Air. Read the root `CLAUDE.md` first. This tree never reads a file outside itself except the kernel
bundle: what it may assume about the kernel and the initramfs is in `docs/contract.md`, and nowhere else.

## What is here

- `build-rootfs.sh` — the image: OpenWrt's armsr rootfs (its sha256sums signed by the key in `keys/usign`),
  packages from the feed, the overlays, `opt/mu300`, the LuCI app with its Chinese catalog, the Aurora theme (pinned
  by hash), the signed regulatory database. No kernel modules (they come with the bundle). Needs docker with linux/arm64; inputs under `$MU300_INPUTS` (the static programs, a
  static busybox; never firmware or Android files in a release).
- `opt/mu300/` — the device scripts (`bin/`) and their data (`lib/`), installed at `/opt/mu300`.
- `overlay/`, `luci-overlay/` — files copied over the image: init scripts, hotplug, preinit, netifd protocols, ucode.
- `luci-app-mu300/` — the panel: views (`htdocs`), rpcd backends and the ucode plugin (`root`), `po/zh_Hans`.
- `src/` — static C programs: `atd-io` (the AT channel bridge), `carry` (update carry-over), `keys` (key and uevent
  reader), `logdw`, `bt-init`.
- `patches/` — fw4 (sipa offload), wifi-scripts (a DFS channel becomes 36).
- `hostapd/` — the hotspot's WPA3-SAE: wpad-basic-mbedtls, hostapd-common and wifi-scripts rebuilt (docker) to
  take the firmware's SAE PMK; `build-rootfs.sh` installs them instead of the feed's.
- `vendor/` — the list and the tools that pull the Android subset (modem_control & co.) from a device; never committed.
- `tools/` — `image-manifest.py`, `image-diff.py` (release diffs), `luci-i18n.py`, `po2lmo.py`, `usign-verify.py`.
- `docs/` — modem, Wi-Fi, USB, power, update, LuCI, system.
- `tests/` — `cd openwrt/tests && MU300_TEST_SHELLS='busybox sh' python3 -m unittest`.

## How the system works

- **Event-driven.** `mu300-modemd` (ucode, with the `mu300-atd-io` bridge) owns every AT channel and publishes
  `mu300.modem` and its URC events on ubus; `mu300-sysd` handles keys, LEDs and the thermal guard from uevents;
  `mu300-smsd`, `ndp-learn` and `mobile-data`'s watch wait for events. Never add a polling loop or an opt-in
  fallback to an older implementation.
- **The modem side never stops**: `android-vendor-start` runs `modem_control`; stopping it powers the board off.
- **Updates** (`mu300-update`): the kernel bundle `mu300-kernel-7.2.tar.gz` (checked against `devices`, `features`,
  `contract`) and the image `mu300-openwrt-luci-rootfs.tar.gz`, from liuwang97/mu300-wrt, a mirror or a directory;
  signatures checked with `release.pub`; the user's files carried over by the image manifest; rollback kept.
- **LuCI** speaks English (the msgids) and Chinese (`po/zh_Hans`) only.

## Rules

- A script runs under busybox ash: POSIX sh plus `read -t`; `sh -n` it and run the tests with `busybox sh`.
- No path into the kernel tree in code, tests or comments (`tools/check-boundary`). A test that needs the kernel
  tree's files belongs in that tree's tests.
- A change to something `docs/contract.md` lists (a `/run/mu300` file, a sysfs name, the bundle's layout) changes
  the kernel tree and the contract in the same commit.
- Never ship firmware, Android files, host keys or the web server's key in the image (`tools/make-release.sh`
  audits it).
- Comments say what holds and why; facts go to `openwrt/docs`, not into long comments.
