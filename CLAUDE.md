# mu300-wrt

Linux 7.2 and OpenWrt 25.12.6 (with the LuCI panel) for two UMS9620 boards: the ZTE F50 (MU300) and the ZTE U30 Air.
A hard fork of dikeckaan/mu300-linux; `UPSTREAM` names the last upstream commit reviewed. Nothing else is supported:
no other kernel (5.4, 6.18, 7.3), no other system (Ubuntu, Arch, ImmortalWrt, OpenWrt without LuCI), no V50, no
installer, no Magisk module, no adbd, no out-of-tree GPU driver (the GPU waits for Panfrost), no OpenWrt failsafe.
Do not bring any of it back.

## Layout

- `kernel/` — the kernel: patches, the mainline port (`port/`), out-of-tree modules (`modules/`), the initramfs, the
  bundle. See `kernel/CLAUDE.md`.
- `openwrt/` — the root filesystem image: device scripts (`opt/mu300`), overlays, the LuCI app, static programs,
  the updater. See `openwrt/CLAUDE.md`.
- `docs/contract.md` — the only interface between the two trees: the kernel bundle's contents, where it is installed,
  what the initramfs hands to the system, the device nodes and sysfs names the system uses.
- `docs/decisions/` — decisions still in force, one per file. `docs/open-issues.md` — what is not solved.
- `tools/` — release (`make-release.sh`, `sign-release.sh`, `releases.sh`), device access (`mu300-ssh.py` & co.,
  `smoke.py`), and the repository rules (`check-boundary`, `doc-lint`).

## Rules

- **The trees never read each other.** `kernel/` names no file under `openwrt/` and the reverse, comments included;
  `openwrt/` uses only the kernel bundle. `tools/check-boundary` fails CI otherwise. A change to an interface changes
  both trees and `docs/contract.md` in one commit (raise the contract version when an old counterpart would break).
- **Event-driven only.** Daemons wait for events (uevents, ubus events, `ip monitor`, modemd's URC events); no
  polling loop as a fallback for a missing component, and no opt-in marker that keeps an older implementation alive.
- **Never stop the modem side.** `modem_control` and the modem must keep running: the PM co-processor cuts the power
  about 290 s after it loses its peer (`docs/decisions/0003-modem-control-never-stopped.md`).
- **Never write `boot_a`/`boot_b` without a backup** of the partition you write, and never copy NV partitions between
  slots.
- **The device is shared.** Before rebooting, flashing, deploying or killing daemons on the U30 Air, read and take the
  device lock `C:\Users\Akira\mu300-linux\U30AIR-DEVICE-LOCK.txt` (WSL: `/mnt/c/Users/Akira/mu300-linux/`). Short
  read-only commands need no lock.

## Build and test

```sh
cd kernel/tests && python3 -m unittest            # kernel tree
cd openwrt/tests && python3 -m unittest           # OpenWrt tree (MU300_TEST_SHELLS='busybox sh' for OpenWrt's shell only)
sh tools/check-boundary && sh tools/doc-lint
MU300_SRC=$HOME/mu300src bash kernel/build.sh && MU300_SRC=$HOME/mu300src bash kernel/build-modules.sh
sh kernel/make-bundle.sh mu300-kernel-7.2.tar.gz
sh tools/make-release.sh TAG                      # the bundle and the openwrt-luci image, audited (docker)
```

`MU300_SRC` holds the verified kernel source (`linux-7.2.11`, objects in `out-7.2.11`); never build in a source tree
another session uses.

## Documentation

- Facts live in `kernel/docs/` and `openwrt/docs/` (≤200 lines each), decisions in `docs/decisions/` (≤30 lines),
  open problems in `docs/open-issues.md`. `tools/doc-lint` enforces the sizes (≤120 KiB in all).
- A fact changed: edit it where it is; never append a correction below it.
- A problem solved: delete its entry from `docs/open-issues.md`; one line of the result goes into the fact document,
  the story into the commit message.
- No plan or design documents in the repository: plans go into the pull request.
- Comments say what holds and why, in the present tense: no history, no dates, no "used to", no notebook numbers.
- Commit messages: a title, the why, and `Tested-on: <board>, <kernel>, OpenWrt <version>` when it ran on a device.
