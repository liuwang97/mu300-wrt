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
