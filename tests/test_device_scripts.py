"""Small device scripts, against a fake / (MU300_SYSROOT) and stub commands: mu300-device, mu300-lan-ip, mu300-led,
mu300-ttl. They run on Ubuntu (dash, bash) and OpenWrt (busybox ash)."""
import unittest

from helpers import BIN, ShellTest


class Device(ShellTest):
    def root(self, device=None, dt_u30=False):
        r = self.tmp / 'root'
        (r / 'run' / 'mu300').mkdir(parents=True, exist_ok=True)
        if device is not None:
            (r / 'run' / 'mu300' / 'device').write_text(device + '\n')
        if dt_u30:
            (r / 'proc' / 'device-tree' / 'charger_policy_service').mkdir(parents=True, exist_ok=True)
        return r

    def test_mu300_device(self):
        cases = [(None, False, 'f50'), (None, True, 'u30air'), ('u30air', False, 'u30air'),
                 ('f50', True, 'f50')]  # what init wrote wins over the device tree
        for device, dt, want in cases:
            r = self.root(device, dt)
            for shell in self.each_shell():
                out = self.script(shell, BIN / 'mu300-device', MU300_SYSROOT=r).stdout.strip()
                self.assertEqual(out, want, (device, dt))
            (r / 'run' / 'mu300' / 'device').unlink(missing_ok=True)

    def test_lan_ip(self):
        for device, want in (('f50', '192.168.77.1'), ('u30air', '192.168.78.1'), ('', '192.168.77.1')):
            r = self.root(device)
            for shell in self.each_shell():
                out = self.script(shell, BIN / 'mu300-lan-ip', MU300_SYSROOT=r, MU300_BIN=BIN).stdout.strip()
                self.assertEqual(out, want, device)


class Led(ShellTest):
    LEDS = ['sc27xx:blue', 'sc27xx:red', 'pwr_green', 'net_blue', 'net_red', 'wifi_blue', 'wifi_white']

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        for n in self.LEDS:
            d = self.root / 'sys' / 'class' / 'leds' / n
            d.mkdir(parents=True)
            (d / 'brightness').write_text('0\n')
            (d / 'max_brightness').write_text('255\n')
            (d / 'trigger').write_text('[none] timer\n')
        (self.root / 'run' / 'mu300').mkdir(parents=True)
        (self.root / 'proc').mkdir()
        self.conf = self.tmp / 'led.conf'
        self.uptime(100)
        self.stub('iw', 'cat "$STUBLOG/iw.out" 2>/dev/null')

    def uptime(self, s):
        (self.root / 'proc' / 'uptime').write_text(f'{s}.42 1234.00\n')

    def reset(self):
        for n in self.LEDS:
            (self.root / 'sys/class/leds' / n / 'brightness').write_text('0\n')
        for f in (self.root / 'run/mu300').glob('led/*'):
            f.unlink()
        for f in (self.root / 'run/mu300').glob('led/.awake-until'):
            f.unlink()
        self.conf.unlink(missing_ok=True)

    def state(self):
        return {n: (self.root / 'sys/class/leds' / n / 'brightness').read_text().strip() for n in self.LEDS}

    def led(self, shell, device, *args):
        (self.root / 'run/mu300/device').write_text(device + '\n')
        return self.script(shell, BIN / 'mu300-led', *args, MU300_SYSROOT=self.root, MU300_BIN=BIN,
                           MU300_LED_CONF=self.conf)

    def test_u30air(self):
        for shell in self.each_shell():
            self.reset()
            self.assertEqual(self.led(shell, 'u30air', 'power', 'on').returncode, 0)
            self.assertEqual(self.led(shell, 'u30air', 'data', 'error').returncode, 0)
            s = self.state()
            self.assertEqual((s['pwr_green'], s['net_red'], s['net_blue']), ('255', '255', '0'))
            self.led(shell, 'u30air', 'data', 'on')
            s = self.state()
            self.assertEqual((s['net_red'], s['net_blue']), ('0', '255'))
            self.led(shell, 'u30air', 'data', 'off')
            self.assertEqual(self.state()['net_blue'], '0')
            self.assertEqual(self.state()['sc27xx:blue'], '0')  # the F50's LED is not touched

    def test_wifi_colour_follows_the_band(self):
        for shell in self.each_shell():
            self.reset()
            self.led(shell, 'u30air', 'power', 'on')
            for iw, lit, dark in (('channel 36 (5180 MHz), width: 80 MHz', 'wifi_blue', 'wifi_white'),
                                  ('channel 6 (2437 MHz), width: 20 MHz', 'wifi_white', 'wifi_blue')):
                (self.tmp / 'iw.out').write_text(f'Interface wlan0\n\ttype AP\n\t{iw}\n')
                self.led(shell, 'u30air', 'wifi', 'on')
                s = self.state()
                self.assertEqual((s[lit], s[dark]), ('255', '0'), iw)
            self.led(shell, 'u30air', 'wifi', 'off')
            self.assertEqual((self.state()['wifi_blue'], self.state()['wifi_white']), ('0', '0'))

    def test_timeout(self):
        for shell in self.each_shell():
            self.reset()
            self.uptime(100)
            self.led(shell, 'u30air', 'power', 'on')          # boot: lit for 60 s
            self.led(shell, 'u30air', 'data', 'on')
            self.uptime(150)
            self.led(shell, 'u30air', 'sleep', '--if-due')    # not yet
            self.assertEqual(self.state()['net_blue'], '255')
            self.uptime(161)
            self.led(shell, 'u30air', 'sleep', '--if-due')
            self.assertEqual({v for v in self.state().values()}, {'0'})
            # a change while dark is kept, and shown on the next wake
            self.led(shell, 'u30air', 'data', 'error')
            self.assertEqual(self.state()['net_red'], '0')
            self.led(shell, 'u30air', 'wake')
            s = self.state()
            self.assertEqual((s['pwr_green'], s['net_red'], s['net_blue']), ('255', '255', '0'))
            # and the timeout counts from the wake
            self.uptime(161 + 59)
            self.led(shell, 'u30air', 'sleep', '--if-due')
            self.assertEqual(self.state()['pwr_green'], '255')
            self.uptime(161 + 61)
            self.led(shell, 'u30air', 'sleep', '--if-due')
            self.assertEqual(self.state()['pwr_green'], '0')

    def test_no_timeout(self):
        for shell in self.each_shell():
            for device, conf in (('u30air', 'LED_TIMEOUT=0\n'), ('f50', '')):
                self.reset()
                if conf:
                    self.conf.write_text(conf)
                self.uptime(100)
                self.led(shell, device, 'power', 'on')
                self.led(shell, device, 'data', 'on')
                self.uptime(100000)
                self.led(shell, device, 'sleep', '--if-due')
                lit = 'net_blue' if device == 'u30air' else 'sc27xx:blue'
                self.assertEqual(self.state()[lit], '255', device)

    def test_f50(self):
        for shell in self.each_shell():
            self.reset()
            for args in (('power', 'on'), ('wifi', 'on'), ('data', 'error'), ('wake',), ('sleep',)):
                # nothing to show on an F50: no error, and no LED of the U30 Air switched
                self.assertEqual(self.led(shell, 'f50', *args).returncode, 0, args)
            self.led(shell, 'f50', 'data', 'on')
            s = self.state()
            self.assertEqual(s['sc27xx:blue'], '255')
            self.assertEqual({v for k, v in s.items() if k != 'sc27xx:blue'}, {'0'})

    def test_missing_leds_and_bad_usage(self):
        for shell in self.each_shell():
            empty = self.tmp / 'empty'
            (empty / 'run/mu300').mkdir(parents=True, exist_ok=True)
            (empty / 'run/mu300/device').write_text('u30air\n')
            r = self.script(shell, BIN / 'mu300-led', 'data', 'on', MU300_SYSROOT=empty, MU300_BIN=BIN)
            self.assertEqual(r.returncode, 0)  # an LED the device has not is skipped
            self.assertEqual(self.led(shell, 'u30air', 'blink').returncode, 2)


class WifiBand(ShellTest):
    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'hotspot.conf'

    def band(self, shell, *args):
        return self.script(shell, BIN / 'mu300-wifi-band', *args, MU300_HOTSPOT_CONF=self.conf)

    def test_toggle(self):
        for shell in self.each_shell():
            self.conf.write_text("SSID=U30 AIR\nPSK=pa$$ 'w\"d\nBAND=5\nCHANNEL=40\nCOUNTRY=TR\n")
            self.conf.chmod(0o600)
            self.assertEqual(self.band(shell).stdout.strip(), 'hotspot: 5 GHz')
            r = self.band(shell, 'toggle')
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'hotspot: 2.4 GHz'), r.stderr)
            self.assertEqual(self.conf.read_text(),
                             "SSID=U30 AIR\nPSK=pa$$ 'w\"d\nBAND=2.4\nCHANNEL=auto\nCOUNTRY=TR\n")
            self.assertEqual(self.conf.stat().st_mode & 0o777, 0o600)
            self.band(shell, 'toggle')
            self.assertIn('BAND=5\n', self.conf.read_text())
            self.band(shell, '2.4')
            self.band(shell, '2.4')
            self.assertEqual(self.conf.read_text().count('BAND='), 1)

    def test_missing_keys_and_file(self):
        for shell in self.each_shell():
            self.conf.write_text('SSID=x\nPSK=12345678\n')   # an old file without BAND/CHANNEL: 5 GHz
            self.assertEqual(self.band(shell).stdout.strip(), 'hotspot: 5 GHz')
            self.band(shell, 'toggle')
            self.assertEqual(self.conf.read_text(), 'SSID=x\nPSK=12345678\nBAND=2.4\nCHANNEL=auto\n')
            self.conf.unlink()
            self.assertEqual(self.band(shell, '5').returncode, 1)
            self.assertEqual(self.band(shell, '6').returncode, 2)


class Buttons(ShellTest):
    def test_actions(self):
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'mu300-wifi-band', 'systemctl', 'logger', 'poweroff'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"; [ "{name} $*" != "systemctl is-active --quiet mu300-hotspot" ]')
        cases = [('116 short', ['mu300-led wake']),
                 ('138 short', ['mu300-led wake', 'mu300-wifi-band toggle']),
                 ('138 long', ['mu300-led wake', 'systemctl is-active --quiet mu300-hotspot', 'systemctl start mu300-hotspot']),
                 ('0 tick', ['mu300-led sleep --if-due']),
                 ('116 long', ['mu300-led wake', 'systemctl poweroff']),
                 ('115 short', [])]
        for shell in self.each_shell():
            for event, want in cases:
                (self.tmp / 'keys.in').write_text(event + '\n')
                (self.tmp / 'calls').unlink(missing_ok=True)
                r = self.script(shell, BIN / 'mu300-buttons')
                self.assertEqual(r.returncode, 0, r.stderr)
                calls = (self.tmp / 'calls').read_text().splitlines() if (self.tmp / 'calls').exists() else []
                self.assertEqual([c for c in calls if not c.startswith('logger')], want, event)


class Ttl(ShellTest):
    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'etc' / 'ttl.conf'
        self.stub('id', 'echo 0')
        # nft records its arguments, and the ruleset it is given on stdin
        self.stub('nft', 'echo "$*" >> "$STUBLOG/nft.args"; [ "$1" = -f ] && cat >> "$STUBLOG/nft.in"; '
                         '[ "$1" = list ] && exit 1; exit 0')

    def ttl(self, shell, *args):
        return self.script(shell, BIN / 'mu300-ttl', *args, MU300_TTL_CONF=self.conf)

    def reset(self):
        for f in ('nft.args', 'nft.in'):
            (self.tmp / f).unlink(missing_ok=True)
        self.conf.unlink(missing_ok=True)

    def test_set(self):
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.conf.read_text(), 'TTL=64\n')
            rules = (self.tmp / 'nft.in').read_text()
            self.assertIn('table inet mu300_ttl', rules)
            self.assertIn('oifname "sipa_eth*" ip ttl set 64', rules)
            self.assertIn('oifname "sipa_eth*" ip6 hoplimit set 64', rules)
            self.assertIn('fixed at 64', r.stdout)

    def test_invalid_values_change_nothing(self):
        for shell in self.each_shell():
            for v in ('0', '256', 'abc', '-1', '6 4', ''):
                self.reset()
                r = self.ttl(shell, 'set', v)
                self.assertEqual(r.returncode, 2, v)
                self.assertFalse(self.conf.exists(), v)
                self.assertFalse((self.tmp / 'nft.in').exists(), v)

    def test_off_and_apply(self):
        for shell in self.each_shell():
            self.reset()
            self.ttl(shell, 'set', '128')
            (self.tmp / 'nft.in').unlink()
            r = self.ttl(shell, 'off')
            self.assertEqual(r.returncode, 0)
            self.assertFalse(self.conf.exists())
            self.assertIn('delete table inet mu300_ttl', (self.tmp / 'nft.args').read_text())
            self.assertFalse((self.tmp / 'nft.in').exists())  # no rule without a setting
            self.assertIn('not changed', r.stdout)
            # a hand-edited file with junk sets nothing
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('TTL=999\n')
            self.ttl(shell, 'apply')
            self.assertFalse((self.tmp / 'nft.in').exists())

    def test_not_root(self):
        self.stub('id', 'echo 1000')
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 1)
            self.assertFalse(self.conf.exists())
            self.assertEqual(self.ttl(shell).returncode, 0)  # status works without root


if __name__ == '__main__':
    unittest.main()
