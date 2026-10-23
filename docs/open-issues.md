# Open issues
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Rare hard hang in the first seconds of boot
**Symptom**: About 2 in 50 boots on 6.18.55 stopped dead with no panic or lockup report, at the vendor Mali driver's probe or at `random: crng init done`. Unverified on 7.2, which loads no GPU driver.
**Impact**: The PMIC watchdog resets; LK dumps RAM and is reset again, each round burns a slot try, and Android takes over after about 20 minutes.
**Known**: Ruled out on a running board: CPU DVFS and 400 GPU power cycles. Unloading the vendor GPU driver panicked with an SError: a GPU-side access at the wrong time is fatal on this SoC, which matters for the Panfrost bring-up.
**Next**: A reproducer, or 100+ boots per variant before any change (for example a delay before a GPU driver's first register read).
**Workaround**: Power-cycle; after a fallback to Android, Linux has to be armed again from the Android side.

## Tracing options overwrite kernel memory at boot
**Symptom**: With `KPROBES`, `KPROBE_EVENTS`, `BPF_EVENTS` (plus `PERF_EVENTS`, `FTRACE`) 7.2.9 failed 2 of 36 reboots on an F50, about 11.5 s in: page tables and the printk buffer overwritten, panic, Android after about 20 minutes.
**Impact**: The options stay off. The same kernel without them did 47 of 47 reboots (stock 7.2.9: 100 of 100).
**Known**: The writer is unknown; the options probably only move early allocations, so a DMA write by the modem or Wi-Fi into kernel memory may hit another layout too.
**Next**: Any change that moves early allocations needs a reboot loop of 40 or more.
**Workaround**: Leave kprobes and tracing off.

## eMMC fails HS400ES after a long power-off
**Symptom**: After a cold boot (off 5+ minutes) one unit failed `mmc_select_hs400es` with -84 (CRC error on a CMD13 check) on all four rescan attempts: no `mmcblk0`, no modem firmware. After hot shutdowns the next boot once found no eMMC (6.18.57).
**Impact**: Patch `0021` falls back HS400ES -> HS200 -> DDR52 -> HS, so the system boots, at HS200 instead of HS400ES; reads should drop from 270-300 MB/s to at most about 190 (unmeasured).
**Known**: The root cause (PHY DLL, DT delay, supply settling) is unknown. On 7.2.11 only the forced-failure hook `sdhci_sprd.fail_hs400es=N` (build with `MU300_CMDLINE_EXTRA`) was run.
**Next**: Cold-boot soak per board, runtime unbind/bind of `22200000.sdio`, throughput and a write/verify/e2fsck soak at HS200.
**Workaround**: None needed; the fallback boots.

## Thermal under full load on 7.2 and on the U30 Air
**Symptom**: Full-load behaviour of `kernel/modules/mu300_thermal` with the trend fix is measured only on 6.18.56 (mean 87.6 C, peak 92.6 C with the backstop); the U30 Air is not measured.
**Impact**: Unknown whether trips and handoff match on the U30 Air, whose tree may name other `thmzone-cells`; hot shutdowns have been followed by boots without eMMC (see above).
**Known**: Mainline has no energy-model governor or cluster hotplug for this SoC; the vendor kernel held the same load at 80-91 C with both.
**Next**: Load runs on 7.2 and on the U30 Air; decide whether an energy-model governor is needed.
**Workaround**: the thermal guard in `openwrt/opt/mu300/bin/mu300-sysd` caps from userspace; keep stress runs capped.

## SD card slot: hot insert and spontaneous removal (F50)
**Symptom**: A card inserted by hand into a slot polled empty for minutes was detected but unusable (`Card stuck being busy`, -110; polled fallback before patch `0014`). Once an SD-rooted system saw the card leave and every exec failed (6.18.56).
**Impact**: Hot insert of a card, and a running SD-rooted system until reboot. A boot with the card in works.
**Known**: Rebinding the `sdio_sd` host found the card five of five times. Cause of the removal is not found.
**Next**: Test hot insert on an F50 with patch `0014` (interrupt-driven card detect); watch card detect on 7.2 with a card in the slot.
**Workaround**: Reboot with the card in; hard reboot (sysrq `b` works).

## Wi-Fi RX interrupt can fault while a channel is torn down
**Symptom**: An RX MSI on another CPU during channel teardown could still fault; never seen.
**Impact**: Possible panic in the RX interrupt on Wi-Fi power-down.
**Known**: `edma_chn_deinit` (`kernel/modules/wcn_bsp/pcie/edma_engine.c`) frees a channel's ring lock and mbuf pool after a bare `msleep(20)` with no channel mask or `synchronize_irq`; `mchn_hw_pop_link` reads `mchn->ops[chn]` twice.
**Next**: Mask the channel and `synchronize_irq` before freeing; read `ops[chn]` once.
**Workaround**: None needed so far.

## Wi-Fi PCIe RX refill after a full pending ring
**Symptom**: A full pending ring (three usable slots) refuses a submission with `-EAGAIN` and the refill retries from a timer (20 ms doubling to 1 s). `sprd_wlan_combo.refill_bus_resume=1` releases it at once but is off: unit-tested only.
**Impact**: Refill delay of up to 1 s. The idle hotspot's 20-150 ms ping is a different wait (in the chip or firmware; not settled).
**Known**: Builds warning-free for 7.2.11; whether `refill_bus_resume` helps is not measured.
**Next**: iperf3 both ways at full rate for minutes, idle and loaded ping, hotspot restart under traffic; then make it the default.
**Workaround**: None needed.

## U30 Air USB 3 link follows the plug orientation
**Symptom**: In device mode `current_speed` is `super-speed-plus` only in the orientation latched at power-on; flipped it falls back to `high-speed` and stays there. Verified on 7.2.11.
**Impact**: 480 Mbps instead of SuperSpeed in the wrong orientation (325.9 Mbps down over SuperSpeed vs 244 Mbps over the fallback on cellular).
**Known**: `mu300-usb pd on` (patch `0020`) gives CC and roles but does not move the SS lanes; the PHY's TCA and the PTN38003A redriver are written once at power-on. With `orientation` = `auto` orientation B does not enumerate (`redriver: -5`, no answer at I2C 0x30): the redriver is absent or unpowered, or `USB_SW` disagrees with the PMIC's CC1.
**Next**: Repeat the flip with `orientation` = `cc`, then `off`; guard the TCA flip when the redriver did not answer (restores the USB 2 fallback only).
**Workaround**: Replug in the power-on orientation, or power-cycle after flipping.

## U30 Air variant with an unreadable charger bus
**Symptom**: On the `U30Air_ABSSV1.0.0B14` variant (chip id `UMS9620-AB`) the charger never binds (`bq256xx-charger 6-006b: error -19: no charger: -5`, an I2C NACK) on 6.18 and 7.2; Android and the 5.4 kernel charge.
**Impact**: These units do not charge under mainline; the battery discharges on the cable.
**Known**: NACK, not timeout; bus 2 works in the same boot. Hypothesis, not measured: an SGM41513 at 0x1a (patch `0015`).
**Next**: Boot patch `0015` on such a unit; if 0x1a also fails, try mainline's SCL duty and start-hold timing and `I2C_NACK_EN | I2C_TRANS_EN`.
**Workaround**: None.

## U30 Air has no modem after LK's charger boot
**Symptom**: When LK boots for a charger it runs the PM co-processor but loads no CP and sets `modem=shutdown`; the stock `modem_control` loads nothing, `/dev/stty_nr*` answer ENODEV (unverified on 7.2).
**Impact**: No mobile data until a normal reboot.
**Known**: `openwrt/opt/mu300/bin/mu300-modem-coldboot` (opt-in: `touch /etc/mu300/modem-coldboot`) patches a RAM copy of `modem_control` to load the CP images; only on a U30 Air with the stock hash. Checked offline only.
**Next**: On a flat or switched-off U30 Air on a charger: `/run/mu300/boot-mode`, `/dev/stty_nr1`, `mu300-at ATI`, the Wi-Fi key, a dial; then drop the opt-in.
**Workaround**: Reboot once the charger boot is up.

## System suspend and CPU cluster wake sources
**Symptom**: A sleep with the radio on ends after 10-20 s: the modem's mailbox irq is `IRQF_NO_SUSPEND`, and its unsolicited `+CSQ`/`+CESQ` reports and `mobile-data`'s 2 s registration poll wake it (with `AT+CFUN=4` it runs to the alarm). `mu300-power` does not use suspend.
**Impact**: A long suspend with the radio on is impossible until those reports and the poll are turned off first.
**Known**: Untested wake sources: power key, USB plug, SMS (`+CMTI`). `kernel/modules/ums9620_lpintc` (adds GIC-enabled SPIs to the always-on controller's wake set; USB ping 9.5 ms vs 15.7 ms) is measured on 6.18.57 only; its suspend hooks never ran on a device.
**Next**: Turn reports and poll off before sleep, test the other wake sources, run suspend/resume with the module on 7.2.11 (U30 Air; the F50 has no battery).
**Workaround**: None; unloading the module restores TF-A's wake bits.

## Dead downlink after a dropped PDP context
**Symptom**: The context dropped (`+CGACT:1,0`) while `sipa_eth0` kept its old address, so address checks passed and `mobile-data watch` did not reconnect (unverified on 7.2).
**Impact**: No downlink (`rx_packets` 0) until `mobile-data up` is run by hand.
**Known**: The watchdog also compares the interface address with the context's; whether that covers this case is not recorded. A sub-millisecond ping reply from a public address is the VPN tun answering itself; resolve a name against the `AT+CGCONTRDP` address and watch `rx_packets`.
**Next**: Reproduce with a dropped cid 1 and read the watch loop's decision.
**Workaround**: `mobile-data up`.

## Dead downlink while every bearer check passes (VPN engine does not follow the uplink)
**Symptom**: A bearer that passes all checks carries no downlink when the VPN engine started a second or two before netifd finished the bearer; the engine never recovers by itself. Seen on the U30 Air (6.18.56/57); after repeated WAN redials it once logged `network: missing default interface`.
**Impact**: No data until `mu300-vpn restart`; a plain reboot is a new race.
**Known**: `openwrt/opt/mu300/bin/mu300-vpn-uplink` (hotplug `96-mu300-vpn-uplink`) restarts the VPN once when its engine began no later than the bearer; `mobile-data watch` detects a stalled downlink (at least 20 TX and at most 2 RX packets over 90 s) and steps through VPN restart, `ifup wan`, radio off/on, at most 3 reboots. Unverified on 7.2.
**Next**: The engine should restart itself when its uplink changes and re-detect the default interface.
**Workaround**: `mu300-vpn restart`.

## Watchdog cannot recover a radio switched off on a dual-SIM U30 Air
**Symptom**: After `AT+CFUN=0` on a dual-SIM-managed U30 Air the watchdog's `radio_on` returns early (`/run/mu300/sim-managed` exists); `+CFUN: 1` comes back with the SIM powered off (`+CME ERROR: 10`, `+CEREG: 2,8`) and every attach fails (unverified on 7.2).
**Impact**: No recovery from a radio-off without manual steps.
**Known**: Only `AT+SPACTCARD=0;+SFUN=2` then `AT+SFUN=4` by hand restores it.
**Next**: Make `radio_on` power the card back on in dual-SIM mode.
**Workaround**: That AT sequence.

## SIM2 data is unproven
**Symptom**: SIM2 data on `sipa_eth8` and the new live-switch order have not run with two working cards: the maintainer's second card registers for emergency service only (`+CEREG: 2,8`, `+CGATT: 0`).
**Impact**: The cold-start SIM2 success path is unproven on the U30 Air.
**Known**: The no-data, `no_data_kept` and killed-controller paths ran; the restart after `no-radio`, an empty position and a PIN-locked card did not.
**Next**: Run with two working cards; check the `brokers-missing` row if a kernel's `/dev/sipc_sbuf` text differs.
**Workaround**: Boot on SIM1 (default; live switching off).

## Data roaming is untested with a real roaming SIM
**Symptom**: The roaming hold in `mobile-data` (`network.wan.roaming`, per card) has only run through the test hook that reads `stat` 1 as 5; unknown whether this modem reports 5 for national roaming or an MVNO at home (unverified on 7.2).
**Impact**: The hold may trigger, or fail to, on real networks; the SIM2 hold and `sim_data_wait`'s "no verdict while held" never ran.
**Known**: Roaming is read from `AT+CEREG?` and `AT+C5GREG?` `<stat>`; the default is allowed.
**Next**: Test abroad or with a SIM that really roams; run the SIM2 hold.
**Workaround**: Leave data roaming allowed.

## Credentialed APNs are unverified
**Symptom**: `mobile-data` sends `AT+CGPCO=0,"<user>","<password>",<cid>,<auth>` for the external SIM only; nothing has been sent to a modem (unit tests only). Unknown whether the firmware answers OK to it or to the empty form.
**Impact**: SIMs on credential-checking or private APNs are unverified; if the empty form is refused, removing credentials ends in "did not take back the earlier APN credentials" and no dial.
**Known**: The mark `/etc/mu300/apn-auth-set.sim0` stays until the modem answers OK to the empty form; an empty APN sends nothing, so an earlier context may outlive the setting. An installed `block-mount` would probe SIPC channel nodes and desynchronise them; the images do not ship it.
**Next**: With a credential-checking APN: `AT+CGDCONT?`, `AT+CGPCO?`, no password in `ps`, `/run`, `logread`.
**Workaround**: Set another APN explicitly; `rm /etc/mu300/apn-auth-set.sim0` after a refused removal.

## Update leaves minor inconsistencies
**Symptom**: The VPN extra is brought to "latest" even when the installed one is newer (`module_download` compares hashes, not versions). Also: a rollback leaves `<os>.broken` (271 MB) until the next `apply` or `clean`.
**Impact**: A newer VPN module is replaced by an older one; disk use after a rollback.
**Known**: Seen, not changed (unverified on 7.2).
**Next**: Compare versions before replacing the VPN module.
**Workaround**: Put the module back by hand; run `mu300-update clean`.

## VPN extra install can fail behind the kill switch
**Symptom**: `mu300-extra install vpn-dae` ended in "vpn-dae was not installed" after the download completed when the tunnel was not carrying traffic; `Failed to send request: Operation not permitted` from uclient-fetch means only that no connection could be made.
**Impact**: The VPN extra may fail to install while the kill switch (`inet mu300_vpn`) is on and the tunnel is down.
**Known**: `mu300-update` names the kill switch as the cause; no exemption for downloads exists. `killswitch off` (restarts the VPN) made the download work.
**Next**: Examine such a device.
**Workaround**: `mu300-extra install NAME --from DIR`, or wait until the tunnel is up.

## /run is world-writable
**Symptom**: `kernel/initramfs/init` mounts `/run` as plain tmpfs (mode 1777), so any local user can create `/run/<name>` before its daemon; for an AT command fifo in `/run/mu300-at` or the voice extra's state that would let it inject AT commands as root.
**Impact**: A nuisance for the checked directories; `/run/mu300-at/answer.<pid>` and `/run/mu300/` are as before.
**Known**: `openwrt/opt/mu300/lib/run-dir.sh` requires a real directory owned by the daemon's uid, not group/other writable, and moves a squatted non-empty one aside (`/run/.<name>.foreign.<pid>`, 0700).
**Next**: Mount with `-o mode=755` in `init` and test a boot.
**Workaround**: The `run-dir.sh` checks.

## Linux slot's boot chain after an OTA
**Symptom**: After an OTA the Linux slot still holds the factory `uboot`, `sml`, `teecfg`, `trustos`, `vbmeta`, while `modem_control` hands Android's slot's modem images to its Trusty; copying those five from Android's slot has never run on a device whose slots differ (unverified on 7.2).
**Impact**: A possible failed CP start (`kbc_start_cp`) after an OTA.
**Known**: Never copy NV/calibration partitions (`nr_fixnv*`, `nr_runtimenv*`, `prodnv`, `persist`, ...) or boot/dtbo/super/userdata/misc; only from a successfully booted Android slot, with sha256 read-back and rollback.
**Next**: Test on an OTA-updated device; decide where the sync lives (`mu300-update` or an Android-side tool).
**Workaround**: Copy the five partitions from Android's slot by hand, from Android.
