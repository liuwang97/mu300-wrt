# Updating: mu300-update, carry-over, signatures, mirrors, rollback
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

`openwrt/opt/mu300/bin/mu300-update` installs the `openwrt-luci` root filesystem and the 7.2 kernel bundle (`docs/contract.md`).

## Download and checks
- Everything is downloaded first and checked against the release's `SHA256SUMS`; only then anything changes. Stalled transfers stop and partial files resume, so a dead downlink is an error, not a hang. The data watchdog is stopped during the install so no redial interrupts the boot write.
- Diffs: a release carries `mu300-openwrt-luci-rootfs.diff-<tag>.tar.gz` from each of the five releases before it (`tools/make-diffs.sh`, `openwrt/tools/image-diff.py`; only from bases whose `SHA256SUMS` is signed, listed in this release's signed `SHA256SUMS`). A device running `<tag>` downloads the diff and the image manifest instead of the image; after every download `diffs_unpack` fills the left-out files from the installed system where it has them byte for byte and keeps the result only when `mu300-carry manifest` of it equals the signed manifest, else it downloads the whole image. `MU300_DIFF=0` always takes the whole image.
- `apply` execs the release's own updater in the same process (`MU300_SELF_UPDATED`); the keep-alive runs with `9>&-` so it cannot hold the lock, and a continued run waits up to 10 s for its own lock.

## Signatures
- `tools/sign-release.sh` signs `SHA256SUMS` into `SHA256SUMS.sig` (usign Ed25519 format; secret `MU300_RELEASE_KEY`). The public key is `openwrt/opt/mu300/lib/release.pub` (on the device `/opt/mu300/lib/`), not under `/etc/mu300`, which an update carries over: devices would never receive a new key. `usign` checks it.
- Always refused: a bad signature; a key file that is not root's own regular file; checksums of another release (the tag is signed inside the sums as `mu300-release`, compared by `tag_sha`).
- Without the key a system takes releases unchecked; with it, unsigned ones are refused unless `--unsigned` (`MU300_ALLOW_UNSIGNED=1`) and older ones unless `--downgrade` (`MU300_ALLOW_DOWNGRADE=1`); `rollback` is not a downgrade.
- The panel cannot set either waiver; the staged rootfs must name in `etc/mu300/image-version` the release judged (`payload_is`).

## Mirrors
`/etc/mu300/update-source` (root's, not a link) sets `MU300_API_URL=` and `MU300_DOWNLOAD_URL=`. A mirror counts as absent (the rest of the run uses github.com) only for curl exit 6/7/35/52 or a stall before any byte; a transfer that delivered bytes resumes. Fallback is automatic only for an https mirror on a public name (`MU300_MIRROR_FALLBACK=0|1` overrides).

## Carry-over
The rule asks which files came with the image. Every image carries `etc/mu300/image-manifest.gz` and `etc/mu300/image-version`; older releases' manifests are in `opt/mu300/lib/image-manifests/`. What the old system holds beyond its image's manifest is the user's.
- The new image's own `mu300-update carry OLD NEW OS MODE` builds the new system with its static busybox and `mu300-carry` (`openwrt/src/carry`); `cp -a` densifies sparse files and drops xattrs, capabilities and sub-second times.
- Under `/etc /var /home /root /srv /usr/local /opt /mnt /media` the user's file wins; changed image files elsewhere (`/usr /bin /lib /www`) and the project's programs, units and init scripts: the release's wins. The loser is set aside. A deleted image file stays deleted under the user's places; `/etc/uci-defaults` is never touched.
- Security settings (ssh, dropbear, firewall, sudoers, rpcd, uhttpd) keep the user's modes; an account new in the image is locked; files follow accounts by name, not number; passwords stay.
- Set-aside files go to `/etc/mu300/update-kept/<old release>/user|image/<path>` (mode 700, not beside the file: OpenWrt runs every file in `/etc/hotplug.d`); each decision is a line of `/etc/mu300/update-report`. `MU300_UPDATE_KEEP=list` keeps only the fixed `keep_list` (`etc/config etc/mu300 etc/unisoc-modem etc/dropbear etc/rc.local etc/crontabs root`).
- `90-mu300` sets first-install defaults only behind `system.mu300.defaults`, so LAN address, APN, hostname and time zone survive. `/etc/unisoc-modem` (saved locks) and `/etc/crontabs` are carried; with an empty `/etc/crontabs`, `init.d/cron` does not start `crond`.
- Packages added with `apk` vanish with the image: `apply` records them (`/etc/apk/world` minus `image-packages`), sets their `etc/config` files aside in `/etc/mu300/orphaned-config/`, and `mu300-user-packages` reinstalls them at first boot and restores the files. `apk_update` counts online when fewer feeds failed than configured (one dead feed fails the whole `apk update`).

## Rollback and space
- `apply` keeps the old system whole as `<os>.old` for `rollback` and needs what it writes plus 5 % and 32 MB against `df` (without manifests `space_needed`: 1 GB plus 1 GB per system); else it says needed and free and changes nothing. User data is never deleted to make room.
- `apply --no-rollback` carries by hard link (8 MB margin); the old system becomes `<os>.broken`, not offered by `rollback`, removed by the next `apply` or `clean`. `apply`, `rollback`, `clean` test `[ / -ef dir ]` before removing `.old`/`.broken`, else they can delete the running system.
- The 7.2 bundle is 55 MB and unpacks to 160 MB with 3034 modules. `boot_update` builds module sets with hard links (`mod_link`) and removes names instead of writing, since bundle, systems and `boot/` share one filesystem; copying needs about 580 MB, which does not fit the F50's 804 MiB region. Measured peak: about 210 MB (F50, 7.2.11).
- `kernel_need_kb` checks room before the download (three times the bundle plus the bundle or two images, plus 16 MB), `boot_room` after; a refusal states MB needed and free and changes nothing. When room is short the boot image kept for `rollback-boot` and its module set are given up together, and module sets no image runs are removed. `boot_update` accepts only a block device (`boot_dev`) as the boot partition.

## Panel update page
Each action is one `mu300-update` run started by `openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/update` with `setsid`, because rpcd ends a call at 30 s. Changes need the ACL write grant plus a `confirm` word.

## Settings backup
`openwrt/opt/mu300/bin/mu300-backup export|import|show` writes one JSON document (0600) without engine paths (a file would choose what runs as root); import changes nothing until every step passes its setter's rules. OpenWrt's jq has no regex, so its programs use none.
