# The CPU voltage offset is set only by the initramfs, one trial per value
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
The firmware sets CPU voltage and takes an offset only in the one-time `DVFS_INIT` call. The setting lives on the Linux disk, readable only by the initramfs (the command line is forced), and a wrong undervolt can damage the device.

## Decision
`CONFIG_ARM_SPRD_CPUFREQ_V2=y` holds `DVFS_INIT` until `kernel/initramfs/init` writes `/sys/module/sprd_cpufreq_v2_driver/parameters/voltage_offset_uv` (`0,0,0` = stock); without a word DVFS starts at stock after 45 s. The driver (`kernel/port/drivers/cpufreq/sprd-cpufreq-v2-voltage.h`) accepts -25000..+12500 uV in 3125 uV steps, all three domains or none, and refuses writes without `CAP_SYS_ADMIN`, before `SYSTEM_RUNNING`, or once DVFS runs. A new value is a one-time trial, good after 10 minutes up (`mu300-cpu voltage mark-good`); an `armed` marker removed only by a clean shutdown drops any crash, watchdog reset or power cut back to stock. Values are written only when `sml_a` and `sml_b` hash to a known build.

## Consequences
- DVFS starts at the root mount, 3.3-4.6 s later than without the hold (cpufreq registration 5.28-5.56 s vs 2.03 s on 6.18.56).

## Revisit when
The firmware's answer to a repeated `DVFS_INIT` is known, or the range must widen.
