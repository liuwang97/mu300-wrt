# Power, charging, LEDs and CPU
Applies to: Linux 7.2.11, OpenWrt 25.12.5 (F50, U30 Air). Checked against the code: 2026-10-25.

"(unverified on 7.2)": measured on 6.18/5.4 or only offline. The F50 has no battery.

## Profiles and idle
`openwrt/opt/mu300/bin/mu300-power` (init: `openwrt/overlay/etc/init.d/mu300-power`) has the profiles `plugged`, `battery`, `saver`.
- Picked from the fuel gauge's `capacity` alone (saver below `SAVER_BELOW` %, default 20, on battery) or forced with `mu300-power profile NAME`.
- Knobs per profile: `WIFI_IDLE` (minutes without a client, 0 never), `RADIO_IDLE` `keep|lte|off`, `LEDS_IDLE` `on|off`, `CPU` `full|eco`. Defaults: plugged `0 keep on full`, battery `10 off off full`, saver `5 off off eco`. A bad value in `/etc/mu300/power.conf` is the default.
- Idle = no Wi-Fi station, USB host or key press for `WIFI_IDLE` minutes: hotspot down, LEDs per `LEDS_IDLE`, modem per `RADIO_IDLE`.
- `mobile-data suspend lte` only switches EN-DC off (`AT+SPENDC=2`; the data connection stays). `suspend off` is `ifdown wan` then `AT+SFUN=5`; `resume` is `ifup wan`, so netifd keeps owning address, route, DNS and IPv6 (`openwrt/opt/mu300/bin/mobile-data`).
- Idle ends on a key, a computer on USB or a Wi-Fi station; the wake brings up the hotspot, then LEDs and CPU, then the modem (its dial can take minutes).
- The TERM trap wakes the radios without a dial, not during a shutdown (`/run/mu300/power/shutdown`); procd `term_timeout 150` covers the radio lock.
- The loop runs every 10 s only when something is close (idle, charging boot, battery <= 20 % off the charger, retry, wake), else every 30 s on the charger and 60 s on battery.

## Charging boot
- Init writes `/run/mu300/boot-mode` and, for a charger boot, `/run/mu300/charging-boot` (`docs/contract.md`). The daemon enters the charging boot only on its first start of a boot and leaves it only by the Wi-Fi key (`mu300-power wake wifi`), not USB or the power key, with a reboot (shutdown marker first): a warm reboot on the charger is a normal boot, and only a normal boot has the modem.
- It powers off cleanly when unplugged by every account (no charger online, no extcon `USB=1`, battery not `Charging`) for two loops 10 s apart; the power key held 3 s starts the device (the same reboot).
- `POWER_ON_CHARGER=1` in `/etc/mu300/power.conf` (default 0) makes a charger boot a normal one (no marker, starts `active`).
- In a U30 Air charger boot LK loads no CP and sets `modem=shutdown`; the stock `modem_control` then loads nothing, `/dev/stty_nr*` answer ENODEV and the modem is absent until a normal reboot (unverified on 7.2).
- `openwrt/opt/mu300/bin/mu300-modem-coldboot` (opt-in: `touch /etc/mu300/modem-coldboot`) runs a RAM copy of `modem_control` with `0xcf0c mov w19,w0 -> mov w19,wzr` and `0xcf50 mov w0,#63 -> mov w0,#34` (load the CP and CH images, not the running PM's). Stock sha256 `43a3bc8c...819a3`, copy `9e3490f2...37c2ae`; it runs only for a `u30air` with `modem=shutdown`, no `/dev/stty_nr1` and the stock hash, else the stock program. The copy keeps the name `modem_control` because `sprd_modem_loader` checks the task's comm.

## Charge guard, limit, low battery
- Guard (in `mu300-power`): charging off at battery `temp` above 45.0 C or below 0 C, on again below 40.0 C and above 3.0 C, via the bq256xx charger's `charge_type` (`N/A` off, `Fast` on). The driver reads `N/A` whenever it is not charging, so `Fast` is written only when the guard itself turned charging off (marker `/run/mu300/power/charge-off`); the driver resets its switch at probe, so a lost marker leaves nothing off.
- `CHARGE_TO` (`mu300-power charge-limit [N]`, System > Power, U30 Air) is 0-100: 1-99 stops charging at N % and restarts under N-5, 0 never charges, 100 lets go at once. The charger still tops up a load its input does not cover from the battery, so on a 500 mA port the battery drains while plugged in.
- Charging by hand (`mu300-power charge pause|resume|full`, U30 Air): `RESUME_AT` 1..N-1 restarts charging under it instead of N-5 (with `CHARGE_TO=100`, a full battery waits until it drops under it); `CHARGE_PAUSE=1` holds charging off across boots; `FULL_ONCE=1` sets the limit aside until `status` reads Full at 100 %, then clears itself. A pause by `charge_type` `N/A` keeps the input current limit, `online` and the USB and cellular links; it is not a battery bypass. The temperature keeps the last word; the pause and the limit give way under 5 %.
- Low battery powers off in every state, unplugged only, by the stock rules (unverified on 7.2): battery at or under shutdown voltage + 120 mV for 5 samples in a row, then off at capacity <= 1 % or voltage <= shutdown voltage (device tree `cm-shutdown-voltage`, 3.10 V on the U30 Air); or `capacity_level` Critical (else 0 %) not charging for three loops (30 s). The poweroff is the clean one, shutdown marker first.
- The kernel (its fuel gauge patch 0023) arms the gauge's low-voltage and coulomb-counter delta interrupts once both IRQs exist (mainline arms them in `suspend` only and the boards never suspend), else the low-voltage calibration never runs and no 1 % change sends a uevent. U30 Air only (device tree with `/charger_policy_service`): the F50 probes this gauge with made-up readings.

## LEDs (`openwrt/opt/mu300/bin/mu300-led`)
- Battery LED (U30 Air; PMIC RGB `sc27xx-bltc`, green is white), as ZTE's firmware: white (green 120) from 60 %, blue (200) from 10 %, red (150) below. Not charging (Full too): steady; charging: breathing on the PMIC pattern trigger (`hw_pattern` "b 320 b 480 b 480 b 720"; timer blink 800/1200 ms without it), written only on a state change, so a tick does not restart the breath.
- SC27xx `pattern_set` leaves the steady TYPE bit set, so steady -> breathing stays steady; `breathe` writes brightness 0 first when the channel is not yet on the pattern trigger.
- On battery LEDs time out after `LED_TIMEOUT` seconds (`/etc/mu300/led.conf`, default 60, 0 always on); on outside power nothing times out; below 10 % red stays lit.
- Wi-Fi LED: white (`zte-ldo1`) 5 GHz, blue (`zte-ldo2`) 2.4 GHz, steady; dark with the hotspot off.
- U30 Air network LED shows the SIM in use: white (`zte-ldo0`) slot 0 (nano-SIM), blue (`net_blue`) slot 1 (eSIM), red alone for no service, dark for data off; the slot is `/run/mu300/sim-slot` (missing or not `1` is slot 0).
- 4G vs 5G (`led_up` in `mobile-data`): the AcT of `AT+CEREG?`; a 5G SA registration is NR-only (CEREG: not registered), so `AT+C5GREG?` decides.

## CPU (`openwrt/opt/mu300/bin/mu300-cpu`)
- `mu300-cpu core N on|off` takes CPU N offline or back and saves it (`CORES_OFF` in `/etc/mu300/cpu.conf`); `apply` (boot service) re-applies. CPU0 is refused: kernel timekeeping and the vendor drivers' interrupts start there. `mu300-power`'s idle takes CPU7 offline and leaves a core kept offline alone.
- `mu300-cpu lowlatency on|off` writes every `cpu0/cpuidle/state*/disable` (`LOWLATENCY=1`; boot applies "on" only), off by default. ZTE's firmware disables all three CPU0 idle states: Wi-Fi event waits 34 ms average / 100 ms worst with deeper states against 0.6 / 2 ms with WFI only; power cost not measured.
- `mu300-cpu services little|all` (default `little`) pins procd's `services` cgroup (`cpuset.cpus`) to the lowest-capacity CPUs.

## Event-driven daemon
`openwrt/opt/mu300/bin/mu300-sysd` (ucode + uloop, always on) does the keys, the LED tick and the thermal guard in one process, from kernel uevents (`mu300-keys -u power_supply,extcon`) and timers; no loop runs while nothing happens. The U30 Air's charger sends no uevent and the gauge counts 1 % steps only, so the LED tick also looks every 30 s (charger) or 90 s (battery).
- The Wi-Fi key held 3 s wakes and toggles the hotspot in one press, asleep too, and writes `wireless.@wifi-device[0].disabled` as the panel's Wi-Fi switch does (so either undoes the other); in the charging boot it only wakes. A short press on a sleeping device only wakes.
