# The stock thermal zone is registered by a module, with a userspace backstop
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

## Context
The vendor tree carries trips only on the virtual zone `soc-thmzone` (hottest of eleven sensors; passive 70 and 85 C, critical 110 C); mainline has no driver for it, so nothing throttled. With the core's default trend `step_wise` random-walks on the sensor noise and a full load went 38 -> 98.5 C in 185 s (6.18.56).

## Decision
`kernel/modules/mu300_thermal` registers the zone (hottest readable sensor; an unreadable one is never reported as cold) and supplies its own `get_trend` over about a second of readings (`mu300_trend.h`); `step_wise` and the 110 C critical shutdown do the rest. `openwrt/opt/mu300/bin/thermal-guard` watches only while that zone is bound to every `cpufreq-*` cooling device; otherwise it caps itself (85 C down, 75 C up). Always: backstop from 92 C (A76 clusters; A55 from 95 C), power-off at 105 C. `scaling_max_freq` shows the resolved maximum, so a profile ceiling (`mu300-cpu`) and the kernel cap coexist (lower wins).

## Consequences
- Full-load behaviour on 7.2 and the U30 Air is unmeasured (open issue "Thermal under full load"); 6.18.56 with the backstop: mean 87.6 C, peak 92.6 C.

## Revisit when
The module becomes a built-in `port/` driver, or an energy-model governor is wanted.
