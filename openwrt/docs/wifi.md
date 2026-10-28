# Wi-Fi on OpenWrt (SC2355 access point and station)
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

The chip is the SC2355 on PCIe, driven by the kernel bundle's out-of-tree `sprd_wlan_combo` module. Its firmware holds parts of the 802.11 state machine (SAE, association), which is why several mainline assumptions fail below.

## Access point
- `iw list` shows `#{ managed, AP } <= 1`: one AP interface, so 2.4 GHz and 5 GHz cannot be served at once. `openwrt/opt/mu300/bin/mu300-wifi-band` switches the single radio between them.
- The firmware takes the AP channel from the DS Parameter Set element in the beacon, which upstream hostapd adds only on 2.4 GHz. Without it `CMD_START_AP` fails with `SPRD_CMD_STATUS_NOT_SUPPORT_ERROR` or the AP never beacons, so the driver inserts it (`WLAN_EID_DS_PARAMS`) when missing.
- The firmware offers a 5 GHz AP only on channels 36-48 and 149-165 (its ACS list). The driver has no radar detection, so hostapd cannot start an AP on a DFS channel. A DFS channel becomes 36 with htmode kept (never 149, never a narrower width), in `openwrt/patches/wifi-scripts-no-dfs.patch` at every start and in `openwrt/opt/mu300/bin/openwrt-wifi-config` in UCI at boot; where the country does not allow 36 at that width the AP is not started. "auto" excludes the DFS channels (`openwrt/opt/mu300/lib/wifi-channels.sh`); a country with no non-DFS 5 GHz channel gets 2.4 GHz.
- Extended-rate entries that list HT MCS rates as legacy bitrates are ignored by the firmware, as on Android.
- `iw dev wlan0 station dump` prints no per-client figures: the driver's `get_station` leaves them out.

## Bridging
- `br-lan` bridges the gadget netdev and `wlan0` under one dnsmasq. The default subnet is F50 `192.168.77.1/24` and U30 Air `192.168.78.1/24` (`openwrt/opt/mu300/bin/mu300-lan-ip`); LuCI (`network.lan.ipaddr`) can change it.
- cfg80211 refuses to enslave `wlan0` ("Device does not allow enslaving to a bridge") while `IFF_DONT_BRIDGE` stays set. The SC2355 driver returns an error from `change_virtual_intf` when tearing down the previous firmware mode fails, so cfg80211 skips clearing the flag; the module clears `IFF_DONT_BRIDGE` itself (`common/cfg80211.c`, `common/iface.c`).

## Regulatory database
- cfg80211 wants a signed `regulatory.db`; it requests the file about 1.6 s after boot, before the root filesystem exists. Under the unregulated world domain `00` 5 GHz is `NO-IR`, so the country must be applied before netifd starts hostapd.
- `openwrt/opt/mu300/bin/regdb-load` (called from `openwrt/overlay/etc/init.d/mu300-hw`) answers a pending firmware request through `/sys/class/firmware` and runs `iw reg reload`. A mainline kernel has no sysfs firmware fallback (`/sys/class/firmware` is absent), so the script skips the wait and only reloads (unverified on 7.2 whether any boot still has one pending).

## WPA3 and SAE
- SAE is done in the firmware (`iw phy` lists `connect`, no `authenticate`), so stock hostapd and wpa_supplicant cannot do it: as a client every SAE association is rejected with `status_code=1`, and wpa_supplicant leaves SAE out of `get_capability key_mgmt`. `openwrt/opt/mu300/bin/wifi-client` refuses a network whose every access point is SAE-only; WPA2 and WPA2/WPA3 mixed (as WPA2) work.
- The access point offers WPA2-PSK (`psk2`), WPA3-SAE (`sae`) and mixed (`sae-mixed`); LuCI and `mu300-api wifi.set` (`security`: `wpa2`, `wpa3`, `wpa2-wpa3`) set them.
- The firmware runs SAE for the AP: hunting-and-pecking, group 19, no H2E. The driver advertises `SAE_OFFLOAD_AP`, hands the password to the firmware at `START_AP` and returns the PMK as a trailing vendor element (EID 221, `40:45:da:04`, PMK 32 + PMKID 16 bytes) in the association-request IEs of `EVT_NEW_STATION`.
- `openwrt/hostapd/` rebuilds wpad-basic-mbedtls, hostapd-common and wifi-scripts of OpenWrt 25.12.6 (hostapd `ca266cc2`, release r1001) in docker; `openwrt/build-rootfs.sh` installs them instead of the feed's and copies `wifi-sae.build-id`/`wifi-sae.source-info` into `/etc/mu300`. For the SC2355 the AP generator adds `unisoc_sae=1`, PMF required (`sae`) or optional (`sae-mixed`), `sae_pwe=0`, `sae_groups=19`, `beacon_prot=0` (unless set) and no RSN Override; hostapd imports the PMK record (exact length, once, SAE AKM with PMF, else a cached PMKSA) and runs the four-way handshake. Any other driver gets OpenWrt's own SAE.
- A changed SAE BSS restarts the AP on `wifi reload` (cfg80211's `SET_BEACON` carries no password).
- Known risk: the PMK record shares the client's element list, so a client could append a forged one if the firmware admits a non-SAE station with the SAE AKM.
- The stock driver printed secrets to the kernel log (SAE passphrase, SoftAP passphrase, station PSK, TX commands, up to 400 bytes of every RX message). Those prints are removed in `sprd_wlan_combo`; keep them out. hostapd logs the association IEs only as a key dump.

## Station mode
- Station mode is chosen at boot and works one way: hostapd turns the station `wlan0` into an AP, and `iw dev wlan0 set type managed` is refused. Netifd deletes the station interface and adds an AP interface on each boot, which works on 6.18 (unverified on 7.2).
- With Wi-Fi connected the default route has metric 50 and mobile data has 100 (`openwrt/opt/mu300/bin/wifi-client`), so Wi-Fi wins.

## Startup order
- After power-on netifd starts the radio once before `mu300-hw` does; it ends `AP-DISABLED` under the world domain (15-23 s). `mu300-hw`'s `wifi down; wifi up` at about 37 s brings the hotspot up (`AP-ENABLED` at 39.9-43.4 s on the U30 Air).
- A `wifi reload` that adds a second BSS can take the whole radio down; use `wifi down; wifi up` and keep another way into the device.
- The logd ring is 128 KiB (about 1300 lines) and one Wi-Fi restart writes hundreds, so a boot-time line can be gone within about 135 s; important reasons are also written to a file.
