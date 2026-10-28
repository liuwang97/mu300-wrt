# The hotspot offers WPA3-SAE and the 5 GHz mid channels
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
The SC2355 firmware runs SAE itself and returns the PMK as a trailing vendor element (EID 221, prefix `40:45:da:04`) in `EVT_NEW_STATION`.

## Decision
The hotspot offers WPA2-PSK, WPA3-SAE and WPA2/WPA3 mixed on its SSID, with the firmware SAE bridge of upstream
`clean-tf-7.2`: the driver hands the firmware the SAE password at START_AP, hostapd imports the firmware's PMK record
and runs the 4-way handshake. The record's provenance is accepted as an open risk. It is to serve the 5 GHz mid
channels (52-64, 100-144); until the driver detects radar, a DFS channel becomes 36 with its width kept, as upstream.

## Consequences
- WPA3 relies on the record being the firmware's: open issue "The firmware SAE PMK record is not proven to come from
  the firmware". The mid channels are the open issue "The hotspot has no 5 GHz mid channels".
- hostapd, hostapd-common and wifi-scripts are rebuilt from OpenWrt 25.12.6 with the bridge (`openwrt/hostapd/`).

## Revisit when
A crafted client joins without the password, or the driver detects radar.
