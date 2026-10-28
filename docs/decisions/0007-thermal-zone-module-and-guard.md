# The stock thermal zone is registered by a module and runs power_allocator, with a userspace backstop
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-29.

## Context
The vendor tree carries trips only on the virtual zone `soc-thmzone` (hottest of eleven sensors; passive 70 and 85 C, critical 110 C, sustainable power 3000 mW, weighted cooling maps from the 85 C trip); mainline has no driver for it, so nothing throttled. `step_wise` with the core's default trend random-walks on the sensor noise: a full load went 38 -> 98.5 C in 185 s (6.18.56). The cpufreq driver registers an energy model from its final OPP table, so the cpufreq cooling devices can be power actors.

## Decision
`kernel/modules/mu300_thermal` registers the zone (hottest readable sensor; an unreadable one is never reported as cold) and selects `power_allocator` for it alone (`thermal_zone_device_set_policy()`, exported by `kernel/patches/0030`); the default governor stays `step_wise`, which the tripless zones keep. The device tree's sustainable power, trips and weights are used as they are (the tree is forced); the budget can be tuned at run time through the zone's `sustainable_power`. When `power_allocator` refuses the zone (a bound cooling device that is no power actor), it keeps `step_wise` with the module's own `get_trend` (`mu300_trend.h`). The 110 C critical shutdown is the core's. The thermal guard in `openwrt/opt/mu300/bin/mu300-sysd` watches only while that zone is bound to every `cpufreq-*` cooling device, whatever its governor; otherwise it caps itself (85 C down, 75 C up). Always: backstop from 92 C (A76 clusters; A55 from 95 C), power-off at 105 C. `scaling_max_freq` shows the resolved maximum, so a profile ceiling (`mu300-cpu`) and the kernel cap coexist (lower wins).

## Consequences
- A full load runs free up to about 76 C; at 85 C the budget grants about 1846/1326/936 MHz (A55 / CPUs 4-6 / CPU 7) before the integral term, which only lowers it until the zone falls under 70 C.
- The 3000 mW covers CPU dynamic power only (the vendor's budget also held cluster leakage, GPU and NPU).
- Full-load behaviour with `power_allocator` is unmeasured (open issue "Thermal under full load").

## Revisit when
A load run shows the zone settling far under 85 C or overshooting past 90 C (tune `sustainable_power`), or the module becomes a built-in `port/` driver.
