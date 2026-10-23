# USB gadget and USB host on OpenWrt
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

The gadget is built in the initramfs; the interface is in `docs/contract.md`.

## Network gadget
- The link must be up before the host looks. `usb0` is brought up right after the bind, because f_ecm reports the link state in its first CONNECT and macOS `AppleUserECM` ignores a later "connected". The same holds after netifd reconfigures `usb0`, so `openwrt/overlay/etc/hotplug.d/iface/10-mu300-usb` re-enumerates the gadget on every LAN ifup.
- NCM is offered by default and ECM only as fallback: Windows 10 2004+ and 11 bind an inbox driver to NCM, none to ECM. RNDIS is selected by `etc/mu300/usb-net`.
- bcdDevice is 0x0301 (0x0302 with RNDIS): Windows caches MS OS descriptors per VID/PID/bcdDevice, "none" included.
- The initramfs DHCP server (120 s lease) ends at switch_root; `openwrt/overlay/lib/preinit/06_mu300_early_usb` restarts it on the system's LAN address (one-hour lease) because netifd and dnsmasq come up many seconds later and Windows gives up waiting. The LAN ifup hook ends it.
- In `10-mu300-usb` the gadget netdev (`rndis0`, or `usb0`) must join `br-lan` before the loop that takes the early server's address (192.168.77.1/24) off the bridge's ports. Otherwise bridge and port hold the same address with two equal routes, and neither Wi-Fi clients nor the USB host can reach the device. After its re-enumeration only a netdev that was a port before is made one again, so a user who took `usb0` out of `br-lan` keeps it so.

## Identity with several devices
- The LAN subnet follows the device (F50 `192.168.77.x`, U30 Air `192.168.78.x`, `openwrt/opt/mu300/bin/mu300-lan-ip`; `network.lan.ipaddr` overrides). Gadget MACs are `02:50:<md5 of the identity>:<subnet>:0x`, so two boards on one computer do not collide.
- The USB serial is `MU300LINUX-<serial>-<eMMC serial>`. `androidboot.emmcid` (CID characters 21-28, the product serial) is passed by the bootloader on both boards; a device restored from another's backup has the same `androidboot.serialno`, and macOS gives the one network interface to whichever enumerated last. Init reads it from the command line, not `/sys`: USB is set up before the eMMC exists, so the identity would change between boots.
- `mu300-vpn` keeps the device's own LAN out of the tunnel; a `vpn.conf` with `LAN_CIDRS=192.168.77.0/24` copied to a `.78` device would otherwise send replies to its clients into the tunnel.

## USB host (U30 Air)
- The F50 has no battery and its USB port is its power supply, so it cannot be a host. On the U30 Air dwc3 is dual-role with xHCI and the mass-storage, HID, network and serial class drivers, but starts as a gadget (`usb-role-switch`, default peripheral) so the USB network still comes up.
- The PMIC's Type-C block has no mainline driver, so `mu300-usb host|device` chooses the role through dwc3's role switch (with USB-C power delivery on, it asks the port controller through `/sys/class/typec` instead). dwc3 tells the PHY the role via `otg_set_vbus()`, so the kernel's SuperSpeed PHY driver has an otg structure (host eye pattern, A-type ID, pull-downs).
- The 5 V come from the charger's `otg-vbus` regulator. `mu300-usb` sets OTG_CONFIG (REG01 bit 5) with `i2cget/i2cset -f` while the `bq256xx` driver owns the chip, leaves the chip's watchdog off (it would drop the driver's settings and the boost 40 s after the write), turns the boost off at boot (the chip keeps registers across reboots) and refuses host mode while REG08 reports power good (unless outside power is present).
- Measured on 7.2 through a USB-C adapter: an HID receiver and a FAT flash drive (about 3 MB/s). HDMI needs DisplayPort alt mode, which is unavailable.
