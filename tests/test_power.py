"""mu300-power: profiles, idle radios and the charging boot, against a fake / (MU300_SYSROOT), a fake /run and
stub commands. The spec is docs/superpowers/specs/2026-10-06-power-profiles-design.md."""
import os
import unittest

from helpers import ShellTest, BIN

POWER = BIN / 'mu300-power'


class PowerTest(ShellTest):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        self.run_dir = self.tmp / 'run'
        (self.run_dir / 'mu300').mkdir(parents=True)
        self.conf = self.tmp / 'power.conf'
        for name in ('iw', 'wifi', 'systemctl', 'mobile-data', 'mu300-led', 'logger', 'poweroff'):
            self.stub(name, 'echo "$(basename "$0") $*" >> "$STUBLOG/calls"')
        self.stub('iw', 'echo "iw $*" >> "$STUBLOG/calls"; cat "$STUBLOG/stations" 2>/dev/null')
        self.stub('mu300-device', 'echo u30air')

    # --- the fake device
    def psy(self, name, **files):
        d = self.root / 'sys/class/power_supply' / name
        d.mkdir(parents=True, exist_ok=True)
        for k, v in files.items():
            (d / k).write_text(f'{v}\n')
        return d

    def battery(self, capacity=64, temp=281, status='Discharging', current=-470000):
        return self.psy('sc27xx-fgu', type='Battery', present=1, capacity=capacity, temp=temp, status=status,
                        voltage_now=3850000, current_now=current)

    def charger(self, online=1, usb_type='SDP', charge_type='Fast'):
        return self.psy('bq256xx-charger', type='USB', online=online, usb_type=usb_type, charge_type=charge_type)

    def write_conf(self, text):
        self.conf.write_text(text)

    def calls(self):
        p = self.tmp / 'calls'
        return p.read_text() if p.exists() else ''

    def power(self, shell, args, **env):
        return self.sh(shell, f'"{POWER}" {args}', MU300_SYSROOT=self.root, MU300_RUN=self.run_dir,
                       MU300_POWER_CONF=self.conf, MU300_POWER_INTERVAL=0, **env)


class Config(PowerTest):
    def test_defaults_without_a_file(self):
        self.battery()
        for shell in self.each_shell():
            r = self.power(shell, 'status')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('profile: battery (auto)', r.stdout)
            self.assertIn('battery: WIFI_IDLE=10 RADIO_IDLE=off LEDS_IDLE=off CPU=full', r.stdout)
            self.assertIn('plugged: WIFI_IDLE=0 RADIO_IDLE=keep LEDS_IDLE=on CPU=full', r.stdout)
            self.assertIn('saver: WIFI_IDLE=5 RADIO_IDLE=off LEDS_IDLE=off CPU=eco', r.stdout)

    def test_set_rewrites_one_key_and_validates(self):
        for shell in self.each_shell():
            self.write_conf('PROFILE=auto\nbattery_WIFI_IDLE=10\n')
            r = self.power(shell, 'set battery.WIFI_IDLE 15')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.conf.read_text(), 'PROFILE=auto\nbattery_WIFI_IDLE=15\n')
            r = self.power(shell, 'set saver.CPU eco')
            self.assertEqual(self.conf.read_text(), 'PROFILE=auto\nbattery_WIFI_IDLE=15\nsaver_CPU=eco\n')
            r = self.power(shell, 'set battery.RADIO_IDLE sometimes')
            self.assertEqual(r.returncode, 2)
            self.assertIn('RADIO_IDLE', r.stderr)
            self.assertNotIn('sometimes', self.conf.read_text())
            r = self.power(shell, 'set battery.COLOUR red')
            self.assertEqual(r.returncode, 2)
            r = self.power(shell, 'set CHARGE_TO 80')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('CHARGE_TO=80\n', self.conf.read_text())
            r = self.power(shell, 'set CHARGE_TO 90')
            self.assertEqual(r.returncode, 2)

    def test_bad_value_is_default_and_reported(self):
        self.battery()
        for shell in self.each_shell():
            self.write_conf('battery_WIFI_IDLE=ten\nSAVER_BELOW=abc\n')
            r = self.power(shell, 'status')
            self.assertIn('battery: WIFI_IDLE=10', r.stdout)
            self.assertIn('ignored in power.conf: SAVER_BELOW battery_WIFI_IDLE', r.stdout)

    def test_profile_selection(self):
        for shell in self.each_shell():
            self.battery(capacity=50)
            self.write_conf('')
            self.assertIn('profile: battery (auto)', self.power(shell, 'status').stdout)
            self.charger(online=1)
            self.assertIn('profile: plugged (auto)', self.power(shell, 'status').stdout)
            self.charger(online=0)
            self.battery(capacity=15)
            self.assertIn('profile: saver (auto, battery under 20 %)', self.power(shell, 'status').stdout)
            self.write_conf('SAVER_BELOW=0\n')
            self.assertIn('profile: battery (auto)', self.power(shell, 'status').stdout)
            r = self.power(shell, 'profile plugged')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('PROFILE=plugged\n', self.conf.read_text())
            self.assertIn('profile: plugged (forced)', self.power(shell, 'status').stdout)
            self.power(shell, 'profile auto')
            self.assertIn('(auto', self.power(shell, 'status').stdout)
            self.assertEqual(self.power(shell, 'profile loud').returncode, 2)

    def test_plugged_falls_back_to_extcon_without_a_charger_node(self):
        for shell in self.each_shell():
            self.battery()
            e = self.root / 'sys/class/extcon/extcon0'
            e.mkdir(parents=True, exist_ok=True)
            (e / 'state').write_text('USB=1\nUSB-HOST=0\n')
            self.assertIn('profile: plugged (auto)', self.power(shell, 'status').stdout)
            (e / 'state').write_text('USB=0\nUSB-HOST=0\n')
            self.assertIn('profile: battery (auto)', self.power(shell, 'status').stdout)

    def test_no_battery_is_plugged(self):
        # the F50: no battery node; the daemon runs with battery knobs inert and the device counts as plugged
        for shell in self.each_shell():
            r = self.power(shell, 'status')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('battery: none', r.stdout)
            self.assertIn('profile: plugged (auto)', r.stdout)


class Robustness(PowerTest):
    def test_empty_sysfs_values_do_not_abort_status(self):
        for shell in self.each_shell():
            d = self.battery()
            for k in ('voltage_now', 'current_now', 'temp'):
                (d / k).write_text('')
            r = self.power(shell, 'status')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('battery: 64 %', r.stdout)

    def test_any_charger_online_counts(self):
        for shell in self.each_shell():
            self.battery()
            self.psy('a-usb', type='USB', online=0)
            self.psy('b-mains', type='Mains', online=1)
            self.assertIn('profile: plugged (auto)', self.power(shell, 'status').stdout)

    def test_second_battery_node_is_used_and_ignored_not_repeated(self):
        for shell in self.each_shell():
            self.psy('a-gone', type='Battery', present=0, capacity=1)
            self.battery(capacity=64)
            self.write_conf('SAVER_BELOW=abc')
            self.power(shell, 'status')
            r = self.power(shell, 'status')
            self.assertIn('battery: 64 %', r.stdout)
            self.assertEqual((self.run_dir / 'mu300/power/ignored').read_text().count('SAVER_BELOW'), 1)
            self.assertEqual(self.power(shell, 'status').returncode, 0)

    def test_set_appends_after_a_file_without_a_newline(self):
        for shell in self.each_shell():
            self.write_conf('PROFILE=auto')
            self.power(shell, 'set CHARGE_TO 80')
            self.assertEqual(self.conf.read_text(), 'PROFILE=auto\nCHARGE_TO=80\n')


class DaemonTest(PowerTest):
    def setUp(self):
        super().setUp()
        self.battery()
        (self.root / 'proc').mkdir(parents=True, exist_ok=True)
        self.uptime(0)
        udc = self.root / 'sys/class/udc/25100000.dwc3'
        udc.mkdir(parents=True)
        (udc / 'state').write_text('not attached\n')
        cpu = self.root / 'sys/devices/system/cpu'
        (cpu / 'cpu7').mkdir(parents=True)
        (cpu / 'cpu7/online').write_text('1\n')
        (cpu / 'cpufreq/policy4').mkdir(parents=True)
        (cpu / 'cpufreq/policy4/scaling_max_freq').write_text('2301000\n')
        (cpu / 'cpufreq/policy4/cpuinfo_max_freq').write_text('2301000\n')
        (self.root / 'etc').mkdir(exist_ok=True)
        (self.root / 'etc/openwrt_release').write_text('DISTRIB_ID=OpenWrt\n')

    def uptime(self, seconds):
        (self.root / 'proc/uptime').write_text(f'{seconds}.00 0.00\n')

    def stations(self, n):
        (self.tmp / 'stations').write_text(''.join(f'Station 02:00:00:00:00:0{i} (on wlan0)\n' for i in range(n)))

    def usb_host(self, attached):
        (self.root / 'sys/class/udc/25100000.dwc3/state').write_text('configured\n' if attached else 'not attached\n')

    def loops(self, shell, n=1, **env):
        r = self.power(shell, 'daemon', MU300_POWER_LOOPS=n, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def state(self):
        return (self.run_dir / 'mu300/power/state').read_text().strip()


class Daemon(DaemonTest):
    def test_idle_after_wifi_idle_minutes_without_clients(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=10\nbattery_RADIO_IDLE=off\nbattery_LEDS_IDLE=off\n')
            self.stations(0)
            self.uptime(100); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.uptime(100 + 9 * 60); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.uptime(100 + 10 * 60); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            c = self.calls()
            # the order: LEDs, hotspot, modem, CPU
            self.assertLess(c.index('mu300-led idle on'), c.index('wifi down'))
            self.assertLess(c.index('wifi down'), c.index('mobile-data suspend off'))
            self.assertIn('idle', (self.run_dir / 'mu300/power/reason').read_text())

    def test_a_station_or_a_usb_host_restarts_the_timer(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=10\n')
            self.uptime(0); self.stations(1); self.loops(shell)
            self.uptime(9 * 60); self.stations(1); self.loops(shell)
            self.uptime(18 * 60); self.stations(0); self.loops(shell)   # 9 min since the last station
            self.assertEqual(self.state(), 'active')
            self.uptime(19 * 60 + 1); self.usb_host(True); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.uptime(29 * 60); self.usb_host(False); self.loops(shell)   # still 10 min since the USB host
            self.assertEqual(self.state(), 'active')
            self.uptime(29 * 60 + 2); self.loops(shell)
            self.assertEqual(self.state(), 'idle')

    def test_wifi_idle_zero_never_idles(self):
        for shell in self.each_shell():
            self.setUp()
            self.charger(online=1)   # plugged: WIFI_IDLE=0
            self.stations(0)
            self.uptime(0); self.loops(shell)
            self.uptime(24 * 3600); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertNotIn('wifi down', self.calls())

    def test_wake_reverses_in_the_opposite_order(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\nbattery_CPU=eco\n')
            self.stations(0)
            self.uptime(0); self.loops(shell)
            self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.assertEqual((self.root / 'sys/devices/system/cpu/cpu7/online').read_text().strip(), '0')
            self.assertEqual((self.root / 'sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq').read_text().strip(), '1500000')
            (self.tmp / 'calls').unlink()
            r = self.power(shell, 'wake')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.loops(shell)
            self.assertEqual(self.state(), 'active')
            c = self.calls()
            self.assertLess(c.index('mobile-data resume'), c.index('wifi up'))
            self.assertLess(c.index('wifi up'), c.index('mu300-led idle off'))
            self.assertEqual((self.root / 'sys/devices/system/cpu/cpu7/online').read_text().strip(), '1')
            self.assertEqual((self.root / 'sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq').read_text().strip(), '2301000')

    def test_idle_command_enters_idle_now_and_ubuntu_uses_systemctl(self):
        for shell in self.each_shell():
            self.setUp()
            (self.root / 'etc/openwrt_release').unlink()
            self.stations(0)
            self.power(shell, 'idle')
            self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.assertIn('systemctl stop mu300-hotspot', self.calls())

    def test_radio_idle_lte_and_keep(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=lte\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertIn('mobile-data suspend lte', self.calls())
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=keep\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertNotIn('mobile-data suspend', self.calls())

    def test_plugging_in_while_idle_wakes(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.charger(online=1)
            self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertIn('plugged', (self.run_dir / 'mu300/power/reason').read_text())

    def test_station_dump_failure_is_zero_stations(self):
        for shell in self.each_shell():
            self.setUp()
            self.stub('iw', 'echo "command failed: No such device (-19)" >&2; exit 237')
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')

    def test_exit_trap_wakes(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            (self.tmp / 'calls').unlink()
            # the daemon's loop gets TERM: the trap must leave the radios up
            r = self.sh(shell, f'"{POWER}" daemon & p=$!; sleep 1; kill -TERM $p; wait $p; echo rc=$?',
                        MU300_SYSROOT=self.root, MU300_RUN=self.run_dir, MU300_POWER_CONF=self.conf,
                        MU300_POWER_INTERVAL=1)
            self.assertIn('rc=0', r.stdout)
            self.assertIn('wifi up', self.calls())
            self.assertEqual(self.state(), 'active')

    def idle_after_a_minute(self, shell, conf):
        self.write_conf(conf)
        self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
        self.assertEqual(self.state(), 'idle')

    def test_profile_change_while_idle_applies_eco(self):
        for shell in self.each_shell():
            self.setUp()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nsaver_WIFI_IDLE=1\n')
            cpu = self.root / 'sys/devices/system/cpu'
            self.assertEqual((cpu / 'cpu7/online').read_text().strip(), '1')
            self.battery(capacity=10)   # saver: eco
            self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.assertEqual((cpu / 'cpu7/online').read_text().strip(), '0')
            self.assertEqual((cpu / 'cpufreq/policy4/scaling_max_freq').read_text().strip(), '1500000')

    def test_usb_host_wakes_from_idle(self):
        for shell in self.each_shell():
            self.setUp()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            self.usb_host(True)
            self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertIn('USB', (self.run_dir / 'mu300/power/reason').read_text())

    def test_leds_idle_on_leaves_the_leds(self):
        for shell in self.each_shell():
            self.setUp()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\nbattery_LEDS_IDLE=on\n')
            self.assertNotIn('mu300-led idle', self.calls())

    def test_state_is_idle_before_the_first_action(self):
        for shell in self.each_shell():
            self.setUp()
            self.stub('wifi', 'echo "wifi $* state=$(cat "$MU300_RUN/mu300/power/state")" >> "$STUBLOG/calls"')
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            self.assertIn('wifi down state=idle', self.calls())

    def test_a_failed_action_is_retried(self):
        for shell in self.each_shell():
            self.setUp()
            self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/calls"; if [ ! -e "$STUBLOG/once" ]; then touch "$STUBLOG/once"; exit 1; fi')
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            self.assertEqual(self.calls().count('wifi down'), 1)
            self.loops(shell)
            self.assertEqual(self.calls().count('wifi down'), 2)
            self.loops(shell)
            self.assertEqual(self.calls().count('wifi down'), 2)

    def test_a_retry_the_state_no_longer_wants_is_dropped(self):
        for shell in self.each_shell():
            self.setUp()
            self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/calls"; [ "$1" = up ] || exit 1')
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')
            self.power(shell, 'wake')
            self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.loops(shell)
            self.assertEqual(self.calls().count('wifi down'), 1)

    def test_an_unknown_state_is_treated_as_active(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.stations(0); self.uptime(0); self.loops(shell)
            (self.run_dir / 'mu300/power/state').write_text('bogus\n')
            self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')

    def test_wake_does_not_touch_cpu_limits_it_did_not_set(self):
        for shell in self.each_shell():
            self.setUp()
            self.idle_after_a_minute(shell, 'battery_WIFI_IDLE=1\n')   # CPU=full
            pol = self.root / 'sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq'
            pol.write_text('1800000\n')
            self.power(shell, 'wake'); self.loops(shell)
            self.assertEqual(pol.read_text().strip(), '1800000')


class Guards(DaemonTest):
    def charging_boot(self, shell, capacity=3, online=1):
        (self.run_dir / 'mu300/boot-mode').write_text('charger\n')
        self.charger(online=online); self.battery(capacity=capacity)
        return self.power(shell, 'daemon', MU300_POWER_LOOPS=1, MU300_POWER_FRESH=1)

    def test_charging_boot_exits_only_on_key(self):
        for shell in self.each_shell():
            self.setUp()
            r = self.charging_boot(shell)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.state(), 'charging-boot')
            self.assertTrue((self.run_dir / 'mu300/charging-boot').exists())
            c = self.calls()
            self.assertIn('wifi down', c); self.assertIn('mobile-data suspend off', c); self.assertIn('mu300-led charge on', c)
            self.usb_host(True); self.loops(shell)
            self.assertEqual(self.state(), 'charging-boot')      # a computer does not end a charging boot
            self.charger(online=0); self.battery(capacity=50); self.loops(shell); self.loops(shell); self.loops(shell)
            self.assertEqual(self.state(), 'charging-boot')      # unplugged above 5 %: nothing happens
            self.assertNotIn('poweroff', self.calls())
            self.power(shell, 'wake'); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertIn('mu300-led charge off', self.calls())
            self.assertIn('mobile-data resume', self.calls())
            self.assertFalse((self.run_dir / 'mu300/charging-boot').exists())

    def test_a_restart_after_the_key_stays_active(self):
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell)
            self.power(shell, 'wake'); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.power(shell, 'daemon', MU300_POWER_LOOPS=1, MU300_POWER_FRESH=1)   # boot-mode still says charger
            self.assertEqual(self.state(), 'active')

    def test_a_restart_inside_the_charging_boot_stays_in_it(self):
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell)
            self.power(shell, 'daemon', MU300_POWER_LOOPS=1, MU300_POWER_FRESH=1)
            self.assertEqual(self.state(), 'charging-boot')

    def test_ubuntu_wake_starts_the_modem_unit(self):
        for shell in self.each_shell():
            self.setUp()
            (self.root / 'etc/openwrt_release').unlink()
            self.charging_boot(shell)
            self.assertIn('systemctl stop mu300-hotspot', self.calls())
            self.power(shell, 'wake'); self.loops(shell)
            self.assertIn('systemctl start mu300-mobile-data', self.calls())
            self.assertIn('systemctl start mu300-hotspot', self.calls())

    def test_term_in_the_charging_boot_leaves_the_radios_down(self):
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell)
            (self.tmp / 'calls').unlink()
            r = self.sh(shell, f'"{POWER}" daemon & p=$!; sleep 1; kill -TERM $p; wait $p; echo rc=$?',
                        MU300_SYSROOT=self.root, MU300_RUN=self.run_dir, MU300_POWER_CONF=self.conf,
                        MU300_POWER_INTERVAL=1)
            self.assertIn('rc=0', r.stdout)
            self.assertNotIn('wifi up', self.calls())
            self.assertEqual(self.state(), 'charging-boot')

    def test_low_battery_needs_three_unplugged_loops(self):
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell, capacity=3, online=0)
            self.loops(shell)
            self.assertNotIn('poweroff', self.calls())
            self.charger(online=1); self.loops(shell)          # a plug in between resets the count
            self.charger(online=0); self.loops(shell); self.loops(shell)
            self.assertNotIn('poweroff', self.calls())
            self.loops(shell)
            self.assertIn('poweroff', self.calls())

    def test_one_low_sample_does_not_power_off(self):
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell, capacity=50, online=0)
            self.battery(capacity=2); self.loops(shell)
            self.battery(capacity=50); self.loops(shell)
            self.battery(capacity=2); self.loops(shell); self.loops(shell)
            self.assertNotIn('poweroff', self.calls())

    def test_temperature_guard_with_hysteresis(self):
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1); self.battery(temp=460)
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.assertEqual((self.run_dir / 'mu300/power/charge-off').read_text().strip(), 'temp')
            self.battery(temp=420); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')   # not yet under 40.0
            self.battery(temp=390); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')
            self.battery(temp=-5); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.battery(temp=20); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')   # not yet over 3.0
            self.battery(temp=40); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')

    def test_charge_limit_80(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('CHARGE_TO=80\n')
            ch = self.charger(online=1); self.battery(capacity=81)
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.assertEqual((self.run_dir / 'mu300/power/charge-off').read_text().strip(), 'limit')
            self.battery(capacity=77); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.battery(capacity=74); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')

    def test_guard_without_a_reading_or_a_switch_changes_nothing(self):
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1)
            (ch / 'charge_type').unlink()
            self.battery(temp=460); self.loops(shell)
            self.assertFalse((ch / 'charge_type').exists())
            self.setUp()
            ch = self.charger(online=1); b = self.battery(); (b / 'temp').unlink()
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')

    def test_log_line(self):
        for shell in self.each_shell():
            self.setUp()
            self.battery(capacity=64, temp=281, current=-470000)
            out = self.tmp / 'power.csv'
            r = self.power(shell, f'log 0 {out}', MU300_POWER_LOOPS=2)
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = out.read_text().splitlines()
            self.assertEqual(lines[0], 'epoch,mV,mA,mW,capacity,temp,state,profile')
            self.assertEqual(len(lines), 3)
            f = lines[1].split(',')
            self.assertEqual(f[1:6], ['3850', '-470', '-1809', '64', '281'])   # 3.85 V * -0.47 A = -1.81 W
            self.assertEqual(f[6:], ['active', 'battery'])

    def test_log_into_a_missing_directory_fails(self):
        for shell in self.each_shell():
            self.setUp()
            r = self.power(shell, f'log 0 {self.tmp}/nodir/power.csv', MU300_POWER_LOOPS=2)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('nodir', r.stderr)

    def test_cpu_full_restores_when_eco_could_not_read_the_limit(self):
        for shell in self.each_shell():
            self.setUp()
            cpu = self.root / 'sys/devices/system/cpu'
            (cpu / 'cpufreq/policy4/scaling_max_freq').unlink()
            self.write_conf('battery_WIFI_IDLE=1\nbattery_CPU=eco\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.assertEqual((cpu / 'cpu7/online').read_text().strip(), '0')
            self.power(shell, 'wake'); self.loops(shell)
            self.assertEqual((cpu / 'cpu7/online').read_text().strip(), '1')
            self.assertEqual((cpu / 'cpufreq/policy4/scaling_max_freq').read_text().strip(), '2301000')

    def test_retried_actions_do_not_eat_the_retry_list(self):
        for shell in self.each_shell():
            self.setUp()
            (self.root / 'etc/openwrt_release').unlink()
            self.stub('systemctl', 'echo "systemctl $*" >> "$STUBLOG/calls"; cat >/dev/null; exit 1')
            self.stub('mobile-data', 'echo "mobile-data $*" >> "$STUBLOG/calls"; exit 1')
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.calls().count('mobile-data suspend off'), 1)
            self.loops(shell)
            self.assertEqual(self.calls().count('mobile-data suspend off'), 2)

    def test_no_reading_keeps_charging_off(self):
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1); b = self.battery(temp=460)
            self.loops(shell)
            (b / 'temp').unlink(); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.setUp()
            self.write_conf('CHARGE_TO=80\n')
            ch = self.charger(online=1); b = self.battery(capacity=85)
            self.loops(shell)
            (b / 'capacity').unlink(); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')

    def test_node_and_marker_disagree_the_node_wins(self):
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1, charge_type='N/A'); self.battery(temp=250)   # no marker: lost with /run
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')
            self.setUp()
            ch = self.charger(online=1, charge_type='Fast'); self.battery(temp=460)
            (self.run_dir / 'mu300/power').mkdir(parents=True, exist_ok=True)
            (self.run_dir / 'mu300/power/charge-off').write_text('temp\n')   # marker says off, node was reset to on
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')

    def test_no_switch_is_logged_once(self):
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1); (ch / 'charge_type').unlink()
            r1 = self.loops(shell); r2 = self.loops(shell)
            self.assertIn('no charge switch', r1.stderr)
            self.assertNotIn('no charge switch', r2.stderr)

    def test_idle_request_in_the_charging_boot_is_dropped(self):
        for shell in self.each_shell():
            self.setUp()
            self.charging_boot(shell)
            self.power(shell, 'idle'); self.loops(shell)
            self.power(shell, 'wake'); self.loops(shell)
            self.loops(shell)
            self.assertEqual(self.state(), 'active')

    def test_log_validates_seconds(self):
        for shell in self.each_shell():
            self.setUp()
            out = self.tmp / 'p.csv'
            self.assertEqual(self.power(shell, f'log abc {out}').returncode, 2)
            self.assertEqual(self.power(shell, f'log 0 {out}').returncode, 2)   # 0 only with MU300_POWER_LOOPS
