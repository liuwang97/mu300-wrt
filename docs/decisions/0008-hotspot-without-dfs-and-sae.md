# The hotspot never uses a DFS channel and never offers WPA3
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
The SC2355 driver has no `start_radar_detection`, so hostapd fails on 52-64 and 100-144. Its firmware does SAE itself and returns the PMK in the same element list as the client's own association elements, so a client could forge it (the driver has no SAE-completion signal).

## Decision
A DFS channel is replaced, never refused: `openwrt/opt/mu300/lib/wifi-channels.sh` picks non-DFS channels per country (default 36, else 149), `openwrt/opt/mu300/bin/openwrt-wifi-config` and `mu300-wifi-band` write them, and `openwrt/patches/wifi-scripts-no-dfs.patch` makes hostapd replace a radar-flagged channel. SAE offload is disabled in every layer: `sprd_report_softap()` (`kernel/modules/sprd_wlan_combo/common/report.c`) strips PMK records, `vendor_set_sae_password()` returns `-EOPNOTSUPP`, `openwrt/patches/wifi-scripts-wpa2-only.patch` keeps SAE out of hostapd, and `openwrt/opt/mu300/bin/openwrt-wifi-wpa2-only` migrates an SAE hotspot to `psk2+ccmp` with the same key (or off, never open).

## Consequences
- LuCI still lists DFS channels; the Wi-Fi client still joins DFS networks.
- No SAE or mixed WPA2/WPA3 hotspot; a notice shows after a migration.

## Revisit when
Radar detection is ported, or SAE completion becomes observable.
