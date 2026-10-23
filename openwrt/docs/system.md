# System: boot, vendor chroot, network, Docker
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

"(unverified on 7.2)": measured on 6.18 or 5.4. Kernel hand-over: `docs/contract.md`.

## Vendor chroot and the PM watchdog
- Without Android's `modem_control` the PM co-processor cuts power about 290 s after boot: `sprd_pmic_wdt` disarms it with `watchdog rstoff`, over a channel that is ready only after `modem_control` has reloaded `pm_sys` and the modem through Trusty. So `/vendor/bin/modem_control` runs in a chroot with Android's bionic linker (`openwrt/opt/mu300/bin/android-vendor-start`, `android-run`).
- It is exec'd directly: `sprd_modem_loader` rejects every ioctl/write unless `current->comm` is `modem_control`. Runners unset `LD_PRELOAD` (procd preloads `/lib/libsetlbf.so`, which breaks the bionic linker). The 85 MiB property area is bind-mounted, not copied: procd's `/dev` tmpfs is 512 KiB. `/proc/cmdline` is a copy with `androidboot.slot_suffix=_a` so the `_a` modem images load. `openwrt/src/logdw` is the liblog sink.
- The daemons do not run as root, so the trees under `/opt/mu300/android` must be root-owned: directories 0755, `bin` files 0755, property files 0444, others 0644. A 0700 `vendor/etc` ends in `Permission denied`, `run_state: 255` and no modem. `android-vendor-start` repairs modes at boot and creates `/var/log` before `logdw` (S09 runs before S10 creates it).
- OpenWrt has no util-linux `setpriv`, so the daemons switch to uid 1000 themselves and the device nodes must be uid/gid 1000, else `modem_control` gets EACCES on `/dev/modem`. `android-run --switches/--user` is the single decision; installing `shadow-useradd` changes nothing (`openwrt/tests/test_vendor_ids.py`).

## Boot and logs
- The initramfs sets console loglevel 4, starts the random pool early and skips procd's first `modules-boot.d` scan (docs/contract.md). `openwrt/overlay/lib/preinit/31_mu300_failsafe_wait` turns OpenWrt's failsafe mode off altogether: no wait for a key on a UART only test pads reach, and no failsafe address that would take the early DHCP server's address off `usb0`. Recovery is the slot fallback to Android.
- U30 Air (6.18.57): hotspot up at 10.5-11.7 s, mobile data at 14.4-14.6 s after kernel start. `mu300-hw` restarts Wi-Fi only if settings changed or the AP did not come up (a restart costs about 4 s; unverified on 7.2).
- `openwrt/overlay/lib/preinit/05_mu300_early_recorder` writes `dmesg`, `ps`, `logread` to the boot partition's log area for up to 420 s (every 2 s for 90 s, then every 30 s), only what it collected: rewriting the whole 8 MiB window wore the eMMC. It writes only to a block device: procd mounts a fresh tmpfs on `/dev` after preinit, and a write to a missing node makes a 50 MB file in RAM.
- `/sys/fs/pstore/console-ramoops-0` ends mid-sentence after a power cut and with `reboot: Restarting system with command ...` after a reboot. Never wrap `/sbin/reboot`: procd runs it on the way down, straight back into the wrapper, and the device never reboots; call `reboot(2)` (`busybox reboot -f`).
- `/run` is plain tmpfs 1777, so a local user can create `/run/<name>` before its daemon. `openwrt/opt/mu300/lib/run-dir.sh` (`run_dir_take`) requires a real directory owned by the daemon's uid and not group/other-writable, uses plain `mkdir` (not `-p`, which follows symlinks) and never deletes recursively: a foreign non-empty directory is moved to `/run/.<name>.foreign.<pid>`.

## Network and firewall
- Cellular WAN is a netifd protocol (`proto mu300cell`, option `apn`), so LuCI and fw4 do routing, DNS and NAT. fw4 needs `CONFIG_NF_TABLES_SET`, else the whole ruleset is rejected and there is no NAT. `openwrt-wifi-config` replaces netifd's open first-boot network once (`/etc/mu300/wifi-configured`).
- `S19 firewall` runs before `S20 network`, so `90-mu300` names the devices of the `lan` and `wan` zones (`br-lan`, `sipa_eth0`) next to their networks; otherwise the boot ruleset lacks LAN input and wan masquerade.
- TTL: an offloaded flow skips the nft postrouting rule, so `mu300-ttl set N` puts a `matchall` clsact egress filter (`pedit ex munge ip ttl set N pipe csum ip`, and `ip6 hoplimit`) on every `sipa_eth*` and keeps flow offloading on. It needs `NET_SCH_INGRESS`, `NET_CLS_ACT`, `NET_CLS_MATCHALL`, `NET_ACT_PEDIT`, `NET_ACT_CSUM` and a `tc` with pedit's `ex` syntax. `tc filter replace` answers EEXIST, so `apply` deletes and re-adds. Measured on 7.2.9: `set 64` put both filters on every `sipa_eth*`; counters grew 538 packets for 1 MB. Without `act_pedit` it uses nft and turns offloading off.
- IPv6 is relayed and NAT66'd (`masq6`, `91-mu300-luci`): the carrier routes one address per bearer, so replies to a client's public address would not come back. After a renumber the old SLAAC address stays on `sipa_eth0`, so `ndp-learn` routes every /64 the bearer holds to `br-lan`, and `ra-deprecate` sends RAs for a gone /64 (preferred 0, valid 2 h) because odhcpd relays lifetimes unchanged (unverified on 7.2).
- Busybox `nc` has no `-w`: test reachability with `wget -T` or `curl -m`. Behind the VPN kill switch (`inet mu300_vpn`) `wget` says "Operation not permitted": no connection could be made.
- Every LAN `ifup` re-enumerates the USB gadget (`openwrt/overlay/etc/hotplug.d/iface/10-mu300-usb`, for macOS's DHCP): a LAN change drops the USB link.

## Docker (ready, not installed)
`openwrt/overlay/etc/docker/daemon.json`, `/etc/config/dockerd` and the fw4 zone `docker` (`uci-defaults/95-mu300-docker`) are in the image; `apk add dockerd docker docker-compose` installs it (about 234 MiB); `MU300_DOCKER=1` builds it in. Built in, the system is 472 MiB instead of 263 MiB and an update, which unpacks the new system beside the old, runs out of space on the 804 MiB region.
- `data-root` is `/mnt/mu300-disk/docker/openwrt-luci`, outside the system directory, so updates never copy it. Classic overlay2 (`"containerd-snapshotter": false`: containerd keeps every image twice) and the nftables backend (the kernel has no `IP_NF_*`).
- The zone (input and forward REJECT; forwardings docker->wan and lan->docker only) keeps containers off the router's services and the LAN; fw4 rejects what no zone allows, and an accept in Docker's own table does not end the way through fw4. `13-mu300-docker.conf` turns bridge firewalling off after apk's `12-br-netfilter-ip.conf`, else every hotspot-to-USB frame goes through fw4.
- The image build removes `/etc/uhttpd.key`, so uhttpd makes its own at first start (else all devices share one key).

## Misc
- `sysupgrade` allows configuration backups only (`openwrt/overlay/usr/libexec/mu300-sysupgrade`): a whole-disk image would overwrite the eMMC partition table.
- NFC (U30 Air, `openwrt/opt/mu300/bin/mu300-nfc`): the tag is a Fudan FM11NT08 on I2C bus 2, address 0x57, answering only while GPIO 190 is low. The RF side is silent while bit 5 of byte 0x3bf is set; `on|off` flips that bit alone.
