# Mainline port, patches and configuration

Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-23.

`kernel/port/` (clocks, PCIe, PHY, regulator, cpufreq, thermal, LEDs) is installed into the tree by
`kernel/port/install.py`, then `kernel/patches/` is applied and `kernel/mu300-mainline.config` merged. Vendor modules:
`vendor-modules.md`.

## Build

- `kernel/build.sh` applies a patch only if not applied yet and stops on anything else. Order matters:
  0011 builds on 0010, 0012/0015/0016/0019 on 0009, 0018 on 0011, 0029 on 0016, 0020 and 0023. 0003, 0005 and 0019 apply to 7.2.11 with fuzz.
- The build stops when Kconfig drops a fragment option. `kernel/all-modules.py` turns every `tristate` that is off into
  `m`, except what `all-modules.exclude` lists: tests/debug, ASoC, display panels, SoC GPU drivers (panfrost waits for
  the GPU bring-up), VFIO, the dummy UDC (`mu300-usb` takes the first UDC), chip drivers of other boards.
  `MU300_ALL_MODULES=0` builds the fragment alone.
- `kernel/check-dt-modules.py` fails the build if a module's `of:` alias matches a compatible in `dt-compatibles.txt`, so
  nothing loads by itself. `kernel/build-modules.sh` writes all modules flat, refuses an in-tree name for a vendor module and
  keeps `.BTF` when stripping.
- Four netfilter module pairs differ only in case (`xt_HL`/`xt_hl`, ...): the scripts demand a case-sensitive volume
  (`kernel/case-sensitive.sh`), else one of each pair is lost.
- `KVM` is built in: the firmware starts every CPU at EL2 (VHE, IPA size 40 bits, no GICv2 emulation). `DEBUG_INFO_BTF`,
  `BPF_JIT`, `NETKIT` serve dae (eBPF proxy); BTF costs ~4 MiB RAM and needs `pahole`. `NO_HZ_IDLE` +
  `HIGH_RES_TIMERS` are required: with the periodic tick `tick_broadcast_offline()` removes a CPU from the broadcast mask
  for good, so after any hotplug it powers down without a timer interrupt and the buddy watchdog panics ~30 s later.
  The PMIC watchdog takes at most 300 s.

## On-demand module loading

With every module present, any local user could make the kernel load rare protocol, filesystem or line-discipline
modules. The bundle's `autoload-blocked` lists them (TIPC, SCTP, CAN, NFC, VSOCK, 802.15.4, old filesystems).
The root filesystem's modprobe helper (docs/contract.md) refuses a request that resolves to a listed
module by name or alias, dependants included (`kernel/check-autoload-blocked.py` checks this against the build), and fails
closed for `net-pf-*`, `fs-*`, `tty-ldisc-*`, `rtnl-*` without its table. Filesystem aliases are listed because user
namespaces are open and there is no AppArmor. `touch /etc/mu300/autoload-allow-all` turns the guard off.

## Power-off, suspend

- `0001-spi-sprd-adi-add-UMS9620-restart.patch` registers the PMIC restart and a power-off handler (`SLP_CTRL` 0x2248, then
  `PWR_PD_HW` 0x2020) ahead of PSCI, whose restart never returns here. Without a battery on a powered USB port the PMIC
  switches on again after ~1 min.
- PSCI `SYSTEM_SUSPEND` works: DDR survives, the modem keeps running (unverified on 7.2). `ump9620-pmic-wdt` stops in
  `suspend_noirq`, restarts in `resume_noirq`. `pcie-sprd` WAKE# wakes only when `.../26100000.pcie/power/wakeup` is
  `enabled` (default off): the Marlin3 pulls it within 0-2 s of every L2 entry and would end each sleep at once.
- A powered-down CPU cluster is woken by the always-on LP_AP_INTC (8 blocks of 32 lines, INTCn line b = SPI 32n + b), a
  wake source only where its IRQ_EN bit is set, which TF-A does once. dwc3, the IPA and the Wi-Fi MSI lie in unset blocks,
  so their interrupts waited up to 120 ms. `kernel/modules/ums9620_lpintc` adds every SPI enabled in the GIC at load and
  every 5 s (U30 Air ping average 15.7 -> 9.5 ms on 6.18.57).

## CPU voltage offset (experimental)

SML/ATF runs DVFS and takes an offset only in `DVFS_INIT` (`0x8200060b`), once per boot. The setting lives on the Linux disk
and the command line is forced, so only init can supply it: `ARM_SPRD_CPUFREQ_V2=y` holds `DVFS_INIT` until init writes
`/sys/module/sprd_cpufreq_v2_driver/parameters/voltage_offset_uv` (`0,0,0` stock; stock after 45 s without a write). Limits in `kernel/port/drivers/cpufreq/sprd-cpufreq-v2-voltage.h`: -25000..+12500 uV in
3125 uV steps, all three domains (CPUs 0-3, 4-6, 7) or none, `CAP_SYS_ADMIN`, `-EBUSY` once DVFS runs. The guard
(`cpu_voltage_boot` in `kernel/initramfs/init`): a new value is a one-time trial, good after 10 min up; an `armed` marker
is synced first and removed only by a clean shutdown, so a crash or power cut drops back to `0,0,0`; written only if
`sml_a` and `sml_b` hash to a known build (F50 `ff478269...`, U30 Air `f27ac16f...`). Measured on 7.2.11 (F50-B):
`-3125,0,0`, `-3125,-3125,-3125`, `-6250,-6250,0` lowered exactly those domains' OPP tables and passed
`mu300-cpu voltage stress`; nothing beyond -6250 uV has been tried. DVFS starts 3.3-4.6 s later.

## Thermal

`step_wise` follows the noise of the "hottest of eleven" sensor: under 8 loops the SoC went 38 -> 98.5 C in
185 s (6.18.56, unmeasured on 7.2). `kernel/modules/mu300_thermal` supplies `get_trend` (slope between 0.5 s means a second apart, dead band
1 C/s): from trip+3 C raising, trip-5..trip stable, below dropping (`soc-thmzone` trip 85 C); 10 min full load: mean
82.3 C, peak 91.2 C. The root filesystem's thermal guard adds a backstop from 92 C (A55 from 95 C) and powers off at 105 C.

## eMMC and SD host

- `sprd,sdhci-r11` hosts: eMMC `sdio@22200000` (8 bit, HS400ES), slot `sdio@22210000` (4 bit, UHS, `cd-gpios` EIC sync
  line 35), `sdio@22220000` unused. `0014-gpio-eic-sprd-eight-EIC-banks.patch` lifts mainline's 3 EIC banks to 8,
  otherwise the CD lookup defers for ever; the polling fallback re-initialises the card every second.
- `kernel/port/sdhci_order.py` makes the SDHCI accessors ordered (not `_relaxed`): a relaxed write can start a transfer
  before the ADMA descriptors are visible.
- `0021-mmc-HS400ES-failure-falls-back-to-HS200.patch`: a failed timing (`-84`) is dropped; the next attempt takes HS200,
  then HS/DDR52.

## USB, charger, fuel gauge

- On the U30 Air the PHY's `extcon` names the PMIC Type-C block, which has no driver, so `usb_add_phy_dev()` deferred for
  ever; `phy-sprd-ums9620-ssphy.c` takes the VBUS GPIO node instead (F50: `extcon-usb-gpio`).
- `0008` sends small NCM frames at once (NCM otherwise waits 300 us to aggregate: multi-ms pings). `0027` clears `f->os_desc_table` after `kfree()` in
  `ncm_unbind()`/`rndis_unbind()` (double free when OS descriptors go 1 then 0);
- U30 Air: `0004`-`0006` add UMP9620 to efuse, ADC and fuel gauge. The SGM41511 (bq25601, I2C bus 6, 0x6b) is driven by
  `bq256xx` (`0009`; no register cache, so Android's values and `mu300-usb`'s OTG bit survive). Input current is 500 mA
  until BC1.2 reports (status register 0x239c); `0012` takes the larger of BC1.2 and the Type-C source's Rp. `0015`/`0016`:
  the SGM41513 at 0x1a, only on the board with a battery (an F50 has a chip there and reset in a loop when told to
  charge). `0029`: one test of that board for the charger, the fuel gauge and the port controller,
  `ump9620_has_battery_policy()` (`/charger_policy_service` present and enabled). `0024`: CV from the battery node (4.272 V; a low CV ends a charge early), `EN_TIMER` cleared. `0025`: 3 A only
  with `bq256xx_charger.u30air_3a=1` (kernel command line).
- The fuel gauge keeps the last capacity in its user area in Android's format (bits 7:0 percent, 11:8 tenths), written with
  `regmap_update_bits()`; `0018` also at shutdown. The charger's Full counts as 100 % when |I| <=
  `fullbatt-current` (120 mA), with no voltage test (the gauge sees ~4.147 V when Full). `i2cdetect -y 6` wedges the bus. `0020` (`ump9620_tcpc`, `enable=1`) is experimental.

## LEDs, pinctrl, PCIe

`leds-zte-ldo` exposes the U30 Air's LDO-switched lights as `zte-ldo0..2` and starts them dark (else the bootloader's "on"
stays); `leds-sc27xx-kpled` drives the keypad sink used as a red LED.  `0013` ports `pinctrl-sprd-qogirn6pro` (511 pins). `0028` lets `pcie-sprd` skip the 0.9 s link wait: `wcn_bsp` powers the Marlin3 later.

## Unused power domains

- The bootloader leaves two top domains on that nothing uses: MM (camera; PMU APB `0x508[28:24]`, its ISP/DCAM/VDSP
  sub-domains off) and DPU_VSP (`0x510[28:24]`, VPU enc0/enc1/dec off), with their bus enables (`mm-eb`,
  `dpu-vsp-eb`) and the pixel PLL running. GPU, AI and DP are off; IPA and PCIe are on and in use. State 0 is on, 7 off.
- `port/drivers/pmdomain/sprd/ums9620-unused-pd.c` (built in) binds to `mm_domain` and the top `sprd,vpu-pd` and
  runs the vendor sequence: bus enable off (AON APB), auto shutdown off, force shutdown on, state 7 within 15 ms, then
  the pixel PLL off. It acts only on a domain that is on with every sub-domain off, switches a domain that does not
  reach 7 back on, writes the set/clear registers (+0x1000/+0x2000) and takes regmaps of the PMU and AON APB only.
  It registers no genpd provider, so no driver of a device in these domains can probe.
- A read of a block without power can hang the bus, so nothing may map one: `ums9620-clk` registers no MM
  (`0x30000000`, `0x30010000`) or DPU/VSP (`0x30100000`, `0x30110000`) controller, and no syscon of those blocks is
  ever looked up (no `/sys/kernel/debug/regmap` entry, nothing in `clk_summary`). `0x318xxxxx` (`dispc1` glb,
  USB31/DP PHY, `dptx`) belongs to the IPA domain and stays.
