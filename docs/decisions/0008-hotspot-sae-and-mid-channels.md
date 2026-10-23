# The hotspot offers WPA3-SAE and the 5 GHz mid channels
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
The SC2355 firmware runs SAE itself and returns the PMK as a trailing vendor element (EID 221, prefix `40:45:da:04`) in `EVT_NEW_STATION`.

## Decision
The hotspot offers WPA2/WPA3-SAE on its SSID and serves the 5 GHz mid channels (52-64, 100-144). It is not implemented: the code starts a WPA2-PSK access point on 36-48 and 149-165 only, and this has to be implemented.

## Consequences
- The gap is the open issue "The hotspot has no WPA3-SAE and no 5 GHz mid channels"; the current behaviour is in `openwrt/docs/wifi.md`.

## Revisit when
The hotspot offers SAE and the mid channels.
