# Only one SIM's radio is on at a time (U30 Air)
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
The U30 Air has two SIM slots on separate SIPC AT channels. The firmware does not bring the second radio up beside the first, and the factory order (target radio on first) leaves `AT+SFUN=4` unanswered and SIM1 without a radio.

## Decision
A live switch is "card in use off, then the other on" (`openwrt/opt/mu300/bin/mu300-sim-u30`); work modes are read from the modem and written back as read (`134,134` only when unreadable); `+CME ERROR: 10` is not believed before the SIM power has been on. Every failure ends on SIM1 with AT; a restart (not before 120 s uptime) is used only when the radio state is unknown, and `network.wan.sim_restart '0'` turns it off. A second card must show data once (`network.wan.sim_data_wait`, 300 s) before it is trusted. A switch never turns data on that the user switched off. The panel's AT terminal refuses the commands that move the route.

## Consequences
- The switch holds `/run/mu300/sim-control.lock`; `mu300-at` answers "busy" to others meanwhile.
- Not proven with two working cards (open issue "SIM2 data").

## Revisit when
Two working cards run the switch order, or the firmware keeps the data route over a restart.
