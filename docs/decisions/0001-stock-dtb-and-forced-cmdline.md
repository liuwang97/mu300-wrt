# Stock device tree and a forced kernel command line
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
LK hands the kernel the device tree it prepared from the device's own partitions and does not forward the boot image's command line. One Image serves the F50 and the U30 Air, which differ in that tree.

## Decision
No device tree is built or shipped: `kernel/wrap-image.py` leaves x0 (the DTB) untouched. Where mainline disagrees with the vendor tree, a driver or patch absorbs it (pinctrl `0013`, reserved memory `0003`/`0022`, the USB PHY's extcon redirect in `kernel/port`).
`CONFIG_CMDLINE_FORCE=y` (`kernel/mu300-mainline.config`) compiles the command line in; user space reads the bootloader's `androidboot.*` from `/proc/device-tree/chosen/bootargs`. Its crutches are required: `regulator_ignore_unused` (SIM supply no driver claims), `fw_devlink=permissive` (the gadget otherwise waits for suppliers only the vendor tree names), `clk_ignore_unused` (modem and Wi-Fi touch clocks without the clock API).

## Consequences
- A new option is a rebuild; `MU300_CMDLINE_EXTRA` (`kernel/build.sh`) adds one to a test kernel, never to a release.

## Revisit when
LK passes a tree we may replace, or a defect cannot be absorbed by a driver.
