# The USB network and a DHCP server exist before the system
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
The host asks for a lease the moment the gadget enumerates (Windows long before any system runs) and gives up after a few seconds; macOS ignores a link that comes up after its first CONNECT notification.

## Decision
`kernel/initramfs/init` binds the gadget `linux` last, after the vendor modules, brings `usb0` up at once and runs `udhcpd` on the gadget's netdev (`rndis0` with RNDIS) with the single address `.200` and a 120 s lease; the host's MAC goes to `/run/mu300-usb-host-mac`. NCM is the default, ECM the fallback, RNDIS on request (`etc/mu300/usb-net`). `openwrt/overlay/lib/preinit/06_mu300_early_usb` restarts the server on the system's subnet (one hour) until dnsmasq answers; `openwrt/overlay/etc/hotplug.d/iface/10-mu300-usb` ends it and joins the gadget netdev to `br-lan` first.

## Consequences
- Subnets are per board (F50 `192.168.77.0/24`, U30 Air `192.168.78.0/24`); the serial and MACs include the eMMC id, so two boards never collide.

## Revisit when
The system can answer DHCP early enough on its own.
