"""mu300-sysd: the buttons, the LED tick and the thermal guard in one event-driven ucode daemon, and its helper's
uevent option (mu300-keys -u).

The keys (ButtonsCases) and the thermal round (ThermalGuardCases) run with a stand-in for a shell that starts the
daemon - for the keys until its helper's output ends (MU300_SYSD_ONCE), for the guard one round (--round). On top: the
LED tick on events and on time (a uevent, the hint's due time, the end of a key's wake; a 1 % step of the battery
starts nothing), the thermal rounds on their own timer, the helper started again, the init script, and mu300-keys -u
itself (built here with the machine's C compiler; Linux only).

Needs a ucode with the uloop, socket and fs modules (MU300_TEST_UCODE, e.g. /root/ucbuild/full/bin/ucode-run)."""
import os
import re
import select
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest, ucode, ucode_paths

SYSD = BIN / 'mu300-sysd'
# what the stand-in shell (sysd_shell) runs: the keys and the LED tick, or one thermal round
KEYS_RUN = BIN / 'mu300-sysd-keys'
ROUND = BIN / 'mu300-sysd-round'
INIT = TOP / 'openwrt' / 'overlay' / 'etc' / 'init.d'
KEYS_SRC = TOP / 'openwrt' / 'src' / 'keys' / 'mu300-keys.c'
CC = shutil.which('gcc') or shutil.which('cc')
LINUX = sys.platform.startswith('linux')
KEYS = None
_build = None


def setUpModule():
    global KEYS, _build
    if not (CC and LINUX):
        return
    _build = tempfile.mkdtemp(prefix='mu300-keys-build-')
    out = Path(_build) / 'mu300-keys'
    r = subprocess.run([CC, '-O2', '-Wall', '-Wextra', '-Werror', '-o', str(out), str(KEYS_SRC)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('mu300-keys.c does not build without warnings:\n' + r.stderr)
    KEYS = out


def tearDownModule():
    if _build:
        shutil.rmtree(_build, ignore_errors=True)


def has_modules(u):
    code = "import * as a from 'uloop'; import * as b from 'socket'; import * as c from 'fs'; print('ok');"
    try:
        return subprocess.run(u + ['-e', code], capture_output=True, text=True, timeout=10).stdout == 'ok'
    except (OSError, subprocess.TimeoutExpired):
        return False


UCODE = ucode()
OK = bool(UCODE and has_modules(UCODE))
NEEDS = 'a ucode with uloop, socket and fs is needed (MU300_TEST_UCODE)'


def sysd_argv():
    return UCODE + ucode_paths() + [str(SYSD)]


def wait_for(pred, timeout=8.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


def sysd_shell(tmp):
    """A stand-in for the shell the tests run a script with: KEYS_RUN starts mu300-sysd for the keys and the LED tick
    until the helper's output ends (with a hint of a minute at most), ROUND runs one thermal round with its log lines
    where the logger stubs put them. Anything else runs under /bin/sh."""
    w = tmp / 'sysd-shell'
    argv = ' '.join(shlex.quote(a) for a in sysd_argv())
    w.write_text(f'''#!/bin/sh
case ${{1:-}} in
    */mu300-sysd-keys) shift; MU300_SYSD_ONCE=1 MU300_SYSD_PARTS="keys led" MU300_SYSD_LED_HINT_MAX=60 \\
        MU300_SYSD_LOG="$STUBLOG/sysd.log" exec {argv} "$@" ;;
    */mu300-sysd-round) shift; MU300_SYSD_LOG="$STUBLOG/log" exec {argv} --round "$@" ;;
    *) exec /bin/sh "$@" ;;
esac
''')
    w.chmod(0o755)
    return [str(w)]


# ------------------------------------------------------------------ the keys and the thermal round
class ButtonsCases:
    """the keys, as mu300-sysd handles them (run by ButtonsUnderSysd)"""
    def test_actions(self):
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'mu300-wifi-band', 'ubus', 'wifi', 'logger', 'poweroff', 'mu300-power'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"; [ "{name} $*" != "ubus -t 5 call hostapd.wlan0 get_status" ]')
        # Every press lights the LEDs first (a key always shows life, whatever the state), then wakes mu300-power with
        # the key's name in the background (only wifi ends a charging boot; other keys count as power), which the
        # rest does not wait for: its order against the band and hotspot toggles is free.
        WP, WW = 'mu300-power wake power', 'mu300-power wake wifi'
        cases = [('116 short', [WP]),
                 ('138 short', [WW, 'mu300-wifi-band toggle']),
                 ('138 long', [WW, 'ubus -t 5 call hostapd.wlan0 get_status', 'wifi up', 'mu300-led wifi sync 120']),
                 ('0 tick', None),   # a tick is no press: it must not wake, only let the LEDs time out (and the battery LED follow)
                 # held power: no mu300-power wake (the radios would come back on the way down); the shutdown marker
                 ('116 long', ['poweroff']),
                 ('115 short', [WP])]
        marker = self.tmp / 'run/mu300/power/shutdown'
        for shell in self.each_shell():
            for event, want in cases:
                (self.tmp / 'keys.in').write_text(event + '\n')
                (self.tmp / 'calls').unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                r = self.script(shell, KEYS_RUN, MU300_RUN=self.tmp / 'run')
                self.assertEqual(r.returncode, 0, r.stderr)
                calls = (self.tmp / 'calls').read_text().splitlines() if (self.tmp / 'calls').exists() else []
                calls = [c.rstrip() for c in calls if not c.startswith('logger')]
                if want is None:
                    self.assertEqual(calls, ['mu300-led tick'], event)
                else:
                    self.assertEqual(calls[0], 'mu300-led wake', event)
                    self.assertEqual(sorted(calls[1:]), sorted(want), event)
                    if 'ubus -t 5 call hostapd.wlan0 get_status' in want:
                        self.assertLess(calls.index('ubus -t 5 call hostapd.wlan0 get_status'),
                                        calls.index('wifi up'))
                self.assertEqual(marker.exists(), event == '116 long', event)

    def test_a_tick_starts_mu300_led_only_when_it_has_something_to_do(self):
        """The tick every 5 s starts mu300-led only when a file its hint names changed, its due time
        came (a timeout running out; a minute at the latest) or there is no hint - the LEDs still time out on time and
        the battery LED still follows the charger"""
        root = self.tmp / 'root'
        for n in ('sc27xx:red', 'sc27xx:green', 'sc27xx:blue', 'zte-ldo0'):   # zte-ldo0: SIM 1's network LED
            d = root / 'sys/class/leds' / n
            d.mkdir(parents=True, exist_ok=True)
            (d / 'brightness').write_text('0\n'); (d / 'max_brightness').write_text('255\n')
            (d / 'trigger').write_text('[none] timer\n')
        ps = root / 'sys/class/power_supply'
        for d, files in (('sc27xx-fgu', {'capacity': 80, 'status': 'Discharging'}), ('bq256xx-charger', {'online': 0})):
            (ps / d).mkdir(parents=True, exist_ok=True)
            for k, v in files.items():
                (ps / d / k).write_text(f'{v}\n')
        (root / 'proc').mkdir(parents=True, exist_ok=True)
        (root / 'run/mu300').mkdir(parents=True, exist_ok=True)
        (root / 'run/mu300/device').write_text('u30air\n')
        self.stub('mu300-keys', 'echo "0 tick"')
        self.stub('mu300-led', f'echo "mu300-led $*" >> "$STUBLOG/calls"; exec "{BIN}/mu300-led" "$@"')
        env = dict(MU300_RUN=self.tmp / 'run', MU300_SYSROOT=root, MU300_BIN=BIN, MU300_LED_CONF=self.tmp / 'none')

        def uptime(s):
            (root / 'proc/uptime').write_text(f'{s}.50 0.00\n')

        def ticks(shell):
            (self.tmp / 'calls').unlink(missing_ok=True)
            r = self.script(shell, KEYS_RUN, **env)
            self.assertEqual(r.returncode, 0, r.stderr)
            calls = (self.tmp / 'calls').read_text().splitlines() if (self.tmp / 'calls').exists() else []
            return calls.count('mu300-led tick')

        def lit():
            return (root / 'sys/class/leds/zte-ldo0/brightness').read_text().strip()

        for shell in self.each_shell():
            for f in (root / 'run/mu300/led').glob('.*'):
                f.unlink()
            (ps / 'bq256xx-charger/online').write_text('0\n')
            uptime(100)
            self.assertEqual(self.script(shell, BIN / 'mu300-led', 'data', 'on', **env).returncode, 0)
            self.assertEqual(self.script(shell, BIN / 'mu300-led', 'wake', **env).returncode, 0)   # lit until 160
            self.assertEqual(lit(), '255')
            self.assertEqual(ticks(shell), 1)          # no hint yet
            uptime(101)
            self.assertEqual(ticks(shell), 0)          # nothing changed, nothing due
            uptime(160)
            self.assertEqual(ticks(shell), 1)          # the timeout ran out: dark
            self.assertEqual(lit(), '0')
            uptime(170)
            self.assertEqual(ticks(shell), 0)
            (ps / 'bq256xx-charger/online').write_text('1\n')
            self.assertEqual(ticks(shell), 1)          # plugged in: lit at once
            self.assertEqual(lit(), '255')
            uptime(200)
            self.assertEqual(ticks(shell), 0)
            uptime(230)
            self.assertEqual(ticks(shell), 1)          # a minute without one: a tick all the same
            (ps / 'bq256xx-charger/online').write_text('0\n')
            self.assertEqual(ticks(shell), 1)          # unplugged: the timeout starts again (until 290)
            uptime(250)
            self.assertEqual(ticks(shell), 0)
            self.assertEqual(self.script(shell, BIN / 'mu300-led', 'wake', **env).returncode, 0)   # a key: until 310
            self.assertEqual(ticks(shell), 1)          # .awake-until changed
            uptime(300)
            self.assertEqual(ticks(shell), 0)
            self.assertEqual(lit(), '255')
            uptime(310)
            self.assertEqual(ticks(shell), 1)
            self.assertEqual(lit(), '0')

    def test_held_power_in_the_charging_boot_starts(self):
        # the stock charger mode: hold power to switch the device on; no shutdown, no marker
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'ubus', 'wifi', 'logger', 'poweroff', 'mu300-power'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        st = self.tmp / 'run/mu300/power'
        st.mkdir(parents=True, exist_ok=True)
        (st / 'state').write_text('charging-boot\n')
        (self.tmp / 'keys.in').write_text('116 long\n')
        for shell in self.each_shell():
            (self.tmp / 'calls').unlink(missing_ok=True)
            r = self.script(shell, KEYS_RUN, MU300_RUN=self.tmp / 'run')
            self.assertEqual(r.returncode, 0, r.stderr)
            calls = [c for c in (self.tmp / 'calls').read_text().splitlines() if not c.startswith('logger')]
            self.assertEqual(sorted(calls), ['mu300-led wake', 'mu300-power wake'])
            self.assertFalse((st / 'shutdown').exists())

    def test_keys_light_the_leds_in_idle_without_any_daemon(self):
        """The LEDs held dark by `mu300-led idle on` while the state file says "active": with the real mu300-led and a
        mu300-power that hangs, each press still lights them, at once."""
        import time
        root = self.tmp / 'root'
        for n in ('sc27xx:green', 'net_blue'):
            d = root / 'sys/class/leds' / n
            d.mkdir(parents=True, exist_ok=True)
            (d / 'brightness').write_text('0\n'); (d / 'max_brightness').write_text('255\n'); (d / 'trigger').write_text('none\n')
        (root / 'proc').mkdir(parents=True, exist_ok=True)
        (root / 'proc/uptime').write_text('500.00 0.00\n')
        (root / 'run/mu300/led').mkdir(parents=True, exist_ok=True)
        (root / 'run/mu300/device').write_text('u30air\n')
        (root / 'run/mu300/led/sc27xx:green').write_text('1\n'); (root / 'run/mu300/led/net_blue').write_text('1\n')
        (root / 'run/mu300/led/.idle').touch()
        (self.tmp / 'run/mu300/power').mkdir(parents=True, exist_ok=True)
        (self.tmp / 'run/mu300/power/state').write_text('active\n')
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        self.stub('mu300-power', 'echo "mu300-power $*" >> "$STUBLOG/calls"; sleep 3')   # no daemon, a slow wake
        for name in ('mu300-wifi-band', 'ubus', 'wifi', 'logger'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        (self.tmp / 'keys.in').write_text('116 short\n138 short\n')
        for shell in self.each_shell():
            for n in ('sc27xx:green', 'net_blue'):
                (root / 'sys/class/leds' / n / 'brightness').write_text('0\n')
            (self.tmp / 'calls').unlink(missing_ok=True)
            env = self.env(MU300_RUN=self.tmp / 'run', MU300_SYSROOT=root, MU300_BIN=BIN, MU300_LED_CONF=self.tmp / 'none')
            env['PATH'] = f'{self.stubs}{os.pathsep}{BIN}{os.pathsep}{env["PATH"]}'
            p = subprocess.Popen(shell + [str(KEYS_RUN)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            time.sleep(1.5)   # well inside the slow wake
            lit = {n: (root / 'sys/class/leds' / n / 'brightness').read_text().strip() for n in ('sc27xx:green', 'net_blue')}
            p.communicate(timeout=30)
            self.assertEqual(lit, {'sc27xx:green': '255', 'net_blue': '255'})
            calls = (self.tmp / 'calls').read_text()
            # the Wi-Fi press on a device held dark only wakes it: no band toggle
            self.assertNotIn('mu300-wifi-band', calls)
            self.assertIn('mu300-power wake power', calls); self.assertIn('mu300-power wake wifi', calls)

    def test_a_hung_direction_probe_is_bounded(self):
        """hostapd may not answer: the hold's direction is decided within the probe deadline."""
        import time
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'logger', 'mu300-power'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        self.stub('ubus', 'echo "ubus $*" >> "$STUBLOG/calls"; exec sleep 60')
        self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/calls"')
        (self.tmp / 'keys.in').write_text('138 long\n')
        for shell in self.each_shell():
            (self.tmp / 'calls').unlink(missing_ok=True)
            t0 = time.time()
            r = self.script(shell, KEYS_RUN, MU300_RUN=self.tmp / 'run', MU300_BUTTONS_PROBE_DEADLINE=1)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLess(time.time() - t0, 10)
            self.assertIn('wifi up', (self.tmp / 'calls').read_text())   # no answer: not up

    def test_the_hold_marker_is_there_before_the_wake_of_the_same_press(self):
        """The wake started by a Wi-Fi hold must find the user's marker already, or its restore sees the
        hotspot still up (or deactivating) and starts it again."""
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'logger'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        self.stub('mu300-power', 'echo "mu300-power $* marker=$([ -e "$MU300_RUN/mu300/power/hotspot-off" ] && echo yes || echo no)" >> "$STUBLOG/calls"')
        self.stub('ubus', 'exit 0')   # up: the hold turns it off
        self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/calls"')
        (self.tmp / 'keys.in').write_text('138 long\n')
        for shell in self.each_shell():
            (self.tmp / 'calls').unlink(missing_ok=True)
            self.script(shell, KEYS_RUN, MU300_RUN=self.tmp / 'run')
            calls = (self.tmp / 'calls').read_text()
            self.assertIn('mu300-power wake wifi marker=yes', calls)
            self.assertIn('wifi down', calls)

    def test_a_hung_toggle_does_not_hold_the_next_press(self):
        """The hotspot toggle runs in the background under a deadline: the next press shows life at once."""
        import time
        self.stub('mu300-keys', 'echo "138 long"; sleep 1; echo "116 short"; sleep 2')
        for name in ('logger', 'mu300-power'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        self.stub('mu300-led', 'echo "mu300-led $* $(date +%s)" >> "$STUBLOG/calls"')
        self.stub('ubus', 'exit 0')   # up: the hold turns it off, and that hangs
        self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/calls"; [ "$1" != down ] || { sleep 30; echo "stop done" >> "$STUBLOG/calls"; }')
        for shell in self.each_shell():
            (self.tmp / 'calls').unlink(missing_ok=True)
            t0 = time.time()
            r = self.script(shell, KEYS_RUN, MU300_RUN=self.tmp / 'run', MU300_BUTTONS_DEADLINE=2)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLess(time.time() - t0, 15)
            calls = (self.tmp / 'calls').read_text()
            self.assertEqual(calls.count('mu300-led wake'), 2, calls)
            self.assertNotIn('stop done', calls)   # killed at its deadline

    def test_the_hotspot_toggle_leaves_a_marker_while_off(self):
        """mu300-power's wake and self-check leave a hotspot alone that the user turned off with the key."""
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'logger', 'mu300-power'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        (self.tmp / 'keys.in').write_text('138 long\n')
        marker = self.tmp / 'run/mu300/power/hotspot-off'
        for shell in self.each_shell():
            self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/calls"')
            self.stub('ubus', 'exit 0')   # up: the hold stops it
            self.script(shell, KEYS_RUN, MU300_RUN=self.tmp / 'run')
            self.assertTrue(marker.exists())
            self.stub('ubus', 'exit 1')   # down: the hold starts it
            self.script(shell, KEYS_RUN, MU300_RUN=self.tmp / 'run')
            self.assertFalse(marker.exists())

    def test_first_wifi_press_while_asleep_only_wakes(self):
        self.stub('mu300-keys', 'cat "$STUBLOG/keys.in"')
        for name in ('mu300-led', 'mu300-wifi-band', 'ubus', 'wifi', 'logger', 'mu300-power'):
            self.stub(name, f'echo "{name} $*" >> "$STUBLOG/calls"')
        (self.tmp / 'run/mu300/power').mkdir(parents=True)
        for state, acts in (('idle', False), ('charging-boot', False), ('active', True)):
            (self.tmp / 'run/mu300/power/state').write_text(state + '\n')
            for shell in self.each_shell():
                for event in ('138 short', '138 long'):
                    (self.tmp / 'keys.in').write_text(event + '\n')
                    (self.tmp / 'calls').unlink(missing_ok=True)
                    r = self.script(shell, KEYS_RUN, MU300_RUN=self.tmp / 'run')
                    self.assertEqual(r.returncode, 0, r.stderr)
                    calls = (self.tmp / 'calls').read_text()
                    self.assertIn('mu300-power wake', calls, (state, event))
                    self.assertEqual(acts, ('mu300-wifi-band toggle' in calls or 'wifi up' in calls or 'get_status' in calls), (state, event))




class ThermalGuardCases:
    """the thermal guard's LED alarm (one round of mu300-sysd against a fake /: MU300_SYSROOT)."""
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        self.bin = self.tmp / 'bin'
        self.bin.mkdir()
        led = self.bin / 'mu300-led'
        led.write_text('#!/bin/sh\necho "mu300-led $*" >> "$STUBLOG/calls"\n')
        led.chmod(0o755)
        shutil.copy(BIN / 'mu300-device', self.bin / 'mu300-device')
        self.device('u30air')
        self.stub('logger', ':')
        self.stub('poweroff', 'echo poweroff >> "$STUBLOG/calls"')

    def device(self, name):
        (self.root / 'run/mu300').mkdir(parents=True, exist_ok=True)
        (self.root / 'run/mu300/device').write_text(name + '\n')

    def round(self, shell, soc, battery=None, trips=False):
        z = self.root / 'sys/class/thermal/thermal_zone0'
        z.mkdir(parents=True, exist_ok=True)
        (z / 'temp').write_text(f'{soc}\n')
        if trips:
            (z / 'trip_point_0_temp').write_text('95000\n')
        if battery is not None:
            b = self.root / 'sys/class/power_supply/battery'
            b.mkdir(parents=True, exist_ok=True)
            (b / 'type').write_text('Battery\n')
            (b / 'temp').write_text(f'{battery}\n')
        (self.tmp / 'calls').unlink(missing_ok=True)
        r = self.script(shell, ROUND, MU300_SYSROOT=self.root, MU300_BIN=self.bin)
        self.assertEqual(r.returncode, 0, r.stderr)
        return (self.tmp / 'calls').read_text().splitlines() if (self.tmp / 'calls').exists() else []

    def test_alarm_on_and_off(self):
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('u30air')
            self.assertEqual(self.round(shell, 60000, 300), [])
            self.assertEqual(self.round(shell, 86000, 300), ['mu300-led alarm on'])
            self.assertEqual(self.round(shell, 90000, 300), [])                # on already
            self.assertEqual(self.round(shell, 80000, 300), [])                # not cool enough yet
            self.assertEqual(self.round(shell, 74000, 300), ['mu300-led alarm off'])
            self.assertEqual(self.round(shell, 60000, 510), ['mu300-led alarm on'])   # the battery alone
            self.assertEqual(self.round(shell, 60000, 460), [])
            self.assertEqual(self.round(shell, 60000, 440), ['mu300-led alarm off'])

    def test_f50_battery_is_ignored(self):
        # the F50 has no battery, but its fuel gauge reports one at 75.0 C from an open NTC input: the alarm would go
        # on at every boot and never off
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('f50')
            self.assertEqual(self.round(shell, 46000, 750), [])
            self.assertEqual(self.round(shell, 86000, 750), ['mu300-led alarm on'])    # the SoC still counts
            self.assertEqual(self.round(shell, 74000, 750), ['mu300-led alarm off'])

    def kernel(self, release):
        (self.root / 'proc/sys/kernel').mkdir(parents=True, exist_ok=True)
        (self.root / 'proc/sys/kernel/osrelease').write_text(release + '\n')

    def test_no_bound_zone_caps_and_powers_off_at_crit(self):
        # without a thermal zone whose cooling devices are bound, this guard caps and powers off at CRIT itself
        self.stub('logger', 'echo "$*" >> "$STUBLOG/log"')
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            (self.tmp / 'log').unlink(missing_ok=True)
            self.device('u30air')
            self.kernel('7.2.10')
            self.assertEqual(self.round(shell, 110000, trips=True), ['poweroff', 'mu300-led alarm on'])
            self.assertIn('no kernel thermal zone throttles the CPUs (kernel 7.2.10)', self.log())

    # --- the stock virtual zone (mu300_thermal): the kernel throttles, this guard only watches
    def cooling(self, n, kind, state=0):
        d = self.root / f'sys/class/thermal/cooling_device{n}'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'type').write_text(kind + '\n')
        (d / 'cur_state').write_text(f'{state}\n')
        (d / 'max_state').write_text('9\n')
        return d

    def zone(self, n, kind, temp, trips=(), cdevs=(), mode='enabled'):
        """thermal_zoneN of type KIND with TRIPS [(type, temp)] and the cooling devices CDEVS bound (cdevK links)."""
        z = self.root / f'sys/class/thermal/thermal_zone{n}'
        z.mkdir(parents=True, exist_ok=True)
        (z / 'type').write_text(kind + '\n')
        (z / 'temp').write_text(f'{temp}\n')
        (z / 'mode').write_text(mode + '\n')
        for i, (tt, tv) in enumerate(trips):
            (z / f'trip_point_{i}_type').write_text(tt + '\n')
            (z / f'trip_point_{i}_temp').write_text(f'{tv}\n')
        for i, c in enumerate(cdevs):
            (z / f'cdev{i}').symlink_to(f'../cooling_device{c}')
        return z

    def soc_zone(self, temp, cdevs=(0, 1, 2), mode='enabled'):
        """the stock soc-thmzone as mu300_thermal brings it back: passive 70 and 85 C, critical 110 C, the three
        cpufreq cooling devices bound through the cooling maps (thermal_zone0..2 are physical zones without trips)"""
        for i, kind in enumerate(('lit0-thmzone', 'big7-thmzone', 'gpu-thmzone')):
            self.zone(i, kind, temp)
        for i, cpu in enumerate((0, 4, 7)):
            self.cooling(i, f'cpufreq-cpu{cpu}')
        return self.zone(3, 'soc-thmzone', temp, [('passive', 70000), ('passive', 85000), ('critical', 110000)],
                         cdevs, mode)

    def board(self, temp, **zone):
        shutil.rmtree(self.root, ignore_errors=True)
        self.device('f50')
        self.kernel('7.2.10')
        freqs = [614400, 936000, 1105000, 1404000, 2002000]
        self.pols = [self.policy(p, freqs, 2002000) for p in ('policy0', 'policy4', 'policy7')]
        if zone is not None:
            self.soc_zone(temp, **zone)

    def maxes(self):
        return [(p / 'scaling_max_freq').read_text().strip() for p in self.pols]

    def log(self):
        return (self.tmp / 'log').read_text() if (self.tmp / 'log').exists() else ''

    def test_kernel_cooling_bound_to_the_stock_zone_hands_frequency_control_over(self):
        self.stub('logger', 'echo "$*" >> "$STUBLOG/log"')
        for shell in self.each_shell():
            self.board(90000)
            (self.tmp / 'log').unlink(missing_ok=True)
            self.assertEqual(self.round(shell, 90000), ['mu300-led alarm on'])    # hot: the alarm, no capping
            self.assertEqual(self.maxes(), ['2002000'] * 3)
            self.assertIn('soc-thmzone', self.log())
            self.assertIn('watching', self.log())
            self.assertEqual(self.round(shell, 105000), ['poweroff'])             # our 105 C power-off stays
            self.assertEqual(self.maxes(), ['2002000'] * 3)

    def next_look(self, shell, soc, battery=None):
        """one round with every zone at SOC and the battery at BATTERY; the seconds the guard would wait for the next"""
        (self.root / 'sys/class/thermal/thermal_zone0').mkdir(parents=True, exist_ok=True)
        for z in (self.root / 'sys/class/thermal').glob('thermal_zone*'):
            (z / 'temp').write_text(f'{soc}\n')
        if battery is not None:
            b = self.root / 'sys/class/power_supply/battery'
            b.mkdir(parents=True, exist_ok=True)
            (b / 'type').write_text('Battery\n')
            (b / 'temp').write_text(f'{battery}\n')
        r = self.script(shell, ROUND, MU300_SYSROOT=self.root, MU300_BIN=self.bin)
        self.assertEqual(r.returncode, 0, r.stderr)
        return int(re.search(r'next look in (\d+) s', r.stderr).group(1))

    def test_the_interval_grows_with_the_distance_from_the_thresholds(self):
        """5 s near HOT, a second more per 2 C under it, at most 30 s while the kernel throttles itself
        and 10 s where this guard is the thermal policy; 5 s while its cap or the alarm is on"""
        for shell in self.each_shell():
            self.board(50000)                                       # the kernel's soc-thmzone is bound
            self.assertEqual(self.next_look(shell, 50000), 22)
            self.board(30000)
            self.assertEqual(self.next_look(shell, 30000), 30)
            self.board(80000)
            self.assertEqual(self.next_look(shell, 80000), 7)
            self.board(86000)
            self.assertEqual(self.next_look(shell, 86000), 5)          # the alarm is on
            self.assertEqual(self.next_look(shell, 80000), 5)          # and stays on until COOL
            self.assertEqual(self.next_look(shell, 50000), 22)         # off again
            # the battery counts too, a second per degree under BATT_HOT (the U30 Air: the F50 has no battery)
            self.board(50000)
            self.device('u30air')
            self.assertEqual(self.next_look(shell, 50000, battery=420), 13)
            # this guard is the thermal policy (no zone): at most 10 s, and 5 s while its cap is on
            self.board(50000)
            shutil.rmtree(self.root / 'sys/class/thermal/thermal_zone3')
            self.assertEqual(self.next_look(shell, 50000), 10)
            self.assertEqual(self.next_look(shell, 90000), 5)          # capped (and the alarm)
            self.assertTrue((self.root / 'run/mu300/thermal-capped').exists())
            (self.root / 'run/mu300/thermal-alarm').unlink()
            self.assertEqual(self.next_look(shell, 80000), 5)          # still capped

    def test_without_the_zone_caps_and_powers_off(self):
        for shell in self.each_shell():
            self.board(90000)
            shutil.rmtree(self.root / 'sys/class/thermal/thermal_zone3')
            self.assertEqual(self.round(shell, 90000), ['mu300-led alarm on'])
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.assertEqual(self.round(shell, 105000), ['poweroff'])

    def test_a_zone_whose_reading_cannot_be_trusted_keeps_this_guard_capping(self):
        for shell in self.each_shell():
            self.board(90000)                                                  # bound, but the zone does not read
            (self.root / 'sys/class/thermal/thermal_zone3/temp').unlink()
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.board(90000)                                                  # reads, but not a number
            (self.root / 'sys/class/thermal/thermal_zone3/temp').write_text('\n')
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.board(90000)                                                  # stale: far below the die sensors
            (self.root / 'sys/class/thermal/thermal_zone3/temp').write_text('40000\n')
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.board(90000)                                                  # within 15 C of the hottest: trusted
            (self.root / 'sys/class/thermal/thermal_zone3/temp').write_text('80000\n')
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['2002000'] * 3)
            self.board(90000)                                                  # passive trips above HOT: not ours
            z = self.root / 'sys/class/thermal/thermal_zone3'
            (z / 'trip_point_0_temp').write_text('95000\n')
            (z / 'trip_point_1_temp').write_text('100000\n')
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)

    def test_a_cluster_left_out_of_the_zone_keeps_this_guard_capping(self):
        for shell in self.each_shell():
            self.board(90000, cdevs=(0, 1))                                    # cpufreq-cpu7 not bound
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.board(90000, mode='disabled')                                 # the zone is off
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.board(90000)
            for i in range(3):                                                    # no cpufreq cooling device at all
                shutil.rmtree(self.root / f'sys/class/thermal/cooling_device{i}')
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)

    def test_handoff_lifts_the_caps_this_guard_set_and_the_zone_gone_caps_again(self):
        for shell in self.each_shell():
            self.board(90000, cdevs=())
            shutil.rmtree(self.root / 'sys/class/thermal/thermal_zone3')          # module not loaded yet
            (self.root / 'run/mu300/cpu-ceiling').write_text('policy0=1105000\n')
            self.round(shell, 90000)                                              # hot: one step down everywhere
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['1105000', '1105000', '1105000'])
            self.assertTrue((self.root / 'run/mu300/thermal-capped').exists())
            self.soc_zone(80000)                                                  # mu300_thermal loaded, zone bound
            self.round(shell, 80000)                                              # neither cool nor hot
            # our caps go (the kernel holds its own, as a QoS limit under scaling_max_freq), the profile's stays
            self.assertEqual(self.maxes(), ['1105000', '2002000', '2002000'])
            self.assertFalse((self.root / 'run/mu300/thermal-capped').exists())
            self.round(shell, 90000)                                              # hot under the kernel: untouched
            self.assertEqual(self.maxes(), ['1105000', '2002000', '2002000'])
            shutil.rmtree(self.root / 'sys/class/thermal/thermal_zone3')          # rmmod
            self.round(shell, 90000)
            self.assertEqual(self.maxes(), ['936000', '1404000', '1404000'])

    def test_a_restarted_guard_lifts_its_old_cap_on_the_handoff_too(self):
        # the marker outlives a restart: the cap of a run before the module loaded does not stick for ever
        for shell in self.each_shell():
            self.board(80000)
            for p in self.pols:
                (p / 'scaling_max_freq').write_text('1404000\n')
            (self.root / 'run/mu300/thermal-capped').write_text('')
            self.round(shell, 80000)
            self.assertEqual(self.maxes(), ['2002000'] * 3)
            self.board(80000)                                                  # no marker: another cap is not ours
            for p in self.pols:
                (p / 'scaling_max_freq').write_text('1404000\n')
            self.round(shell, 80000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)

    # --- the backstop: the kernel throttles, but the SoC climbs on (step_wise follows the sensors' noise: under a full
    # load the SoC reaches 98 C in three minutes with the states at 1-3 of 9)
    def test_backstop_caps_the_a76_clusters_on_top_of_the_kernel_from_92_c(self):
        self.stub('logger', 'echo "$*" >> "$STUBLOG/log"')
        for shell in self.each_shell():
            self.board(91000)
            (self.tmp / 'log').unlink(missing_ok=True)
            self.round(shell, 91000)                                              # under the backstop: watching
            self.assertEqual(self.maxes(), ['2002000'] * 3)
            self.board(93000)
            (self.tmp / 'log').unlink(missing_ok=True)
            self.assertEqual(self.round(shell, 93000), ['mu300-led alarm on'])
            self.assertEqual(self.maxes(), ['2002000', '1404000', '1404000'])     # policy0 (A55) left alone
            self.assertTrue((self.root / 'run/mu300/thermal-capped').exists())
            self.assertIn('soc-thmzone does not hold the SoC (93000 mC)', self.log())
            self.round(shell, 93000)                                              # a step a round, logged once
            self.assertEqual(self.maxes(), ['2002000', '1105000', '1105000'])
            self.assertEqual(self.log().count('does not hold'), 1)

    def test_backstop_caps_every_cluster_from_95_c_and_powers_off_at_crit_still(self):
        for shell in self.each_shell():
            self.board(96000)
            self.round(shell, 96000)
            self.assertEqual(self.maxes(), ['1404000'] * 3)
            self.assertEqual(self.round(shell, 105000), ['poweroff'])

    def heat(self, temp):
        """every zone at TEMP (round() sets thermal_zone0 only)"""
        for t in (self.root / 'sys/class/thermal').glob('thermal_zone*/temp'):
            t.write_text(f'{temp}\n')

    def test_backstop_lifts_its_caps_at_hot_a_step_a_round_never_above_the_ceiling(self):
        for shell in self.each_shell():
            self.board(96000)
            (self.root / 'run/mu300/cpu-ceiling').write_text('policy0=1404000\n')
            self.round(shell, 96000)
            self.round(shell, 96000)
            self.assertEqual(self.maxes(), ['1105000', '1105000', '1105000'])
            self.heat(88000); self.round(shell, 88000)            # between HOT and the backstop: held
            self.assertEqual(self.maxes(), ['1105000', '1105000', '1105000'])
            self.heat(85000); self.round(shell, 85000)
            self.assertEqual(self.maxes(), ['1404000', '1404000', '1404000'])
            self.assertTrue((self.root / 'run/mu300/thermal-capped').exists())
            self.heat(84000); self.round(shell, 84000)
            self.assertEqual(self.maxes(), ['1404000', '2002000', '2002000'])     # the profile's ceiling stays
            self.assertFalse((self.root / 'run/mu300/thermal-capped').exists())
            self.heat(84000); self.round(shell, 84000)            # no marker: nothing more
            self.assertEqual(self.maxes(), ['1404000', '2002000', '2002000'])

    def policy(self, name, freqs, cur_max):
        p = self.root / 'sys/devices/system/cpu/cpufreq' / name
        p.mkdir(parents=True, exist_ok=True)
        (p / 'scaling_available_frequencies').write_text(' '.join(str(f) for f in freqs) + ' \n')
        (p / 'cpuinfo_max_freq').write_text(f'{max(freqs)}\n')
        (p / 'scaling_max_freq').write_text(f'{cur_max}\n')
        return p

    def test_cooldown_does_not_raise_a_policy_above_the_mu300_cpu_ceiling(self):
        # mu300-cpu's "saving" profile caps a cluster and records the cap in /run/mu300/cpu-ceiling; on cool-down
        # the guard steps scaling_max up a notch at a time but never past that ceiling, so the profile stays.
        freqs = [614400, 768000, 936000, 1105000, 1228800, 1404000, 2002000]
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('u30air')
            p = self.policy('policy0', freqs, 936000)   # capped below hw max (2002000)
            (self.root / 'run/mu300/cpu-ceiling').write_text('policy0=1105000\n')
            self.round(shell, 60000)                     # cool: one step up, but clamped to the ceiling
            self.assertEqual((p / 'scaling_max_freq').read_text().strip(), '1105000')
            self.round(shell, 60000)                     # at the ceiling now: no further rise
            self.assertEqual((p / 'scaling_max_freq').read_text().strip(), '1105000')

    def test_a_round_without_a_reading_lifts_no_cap_and_keeps_the_alarm(self):
        # every zone failing (no number this round) is not a cool SoC: no cap goes and the alarm stays on until a real
        # reading is cool
        freqs = [614400, 936000, 1105000, 2002000]
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('u30air')
            p = self.policy('policy0', freqs, 936000)
            self.assertEqual(self.round(shell, 90000), ['mu300-led alarm on'])
            capped = (p / 'scaling_max_freq').read_text().strip()
            for bad in ('', 'error', '-'):
                (self.tmp / 'log').unlink(missing_ok=True)
                self.assertEqual(self.round(shell, bad), [], repr(bad))
                self.assertEqual((p / 'scaling_max_freq').read_text().strip(), capped, repr(bad))
                self.assertIn('no thermal zone gave a temperature', self.log())
            self.assertEqual(self.round(shell, 60000), ['mu300-led alarm off'])
            self.assertNotEqual((p / 'scaling_max_freq').read_text().strip(), capped)

    def test_cooldown_without_a_ceiling_rises_to_the_hardware_maximum(self):
        freqs = [614400, 936000, 1105000, 2002000]
        for shell in self.each_shell():
            shutil.rmtree(self.root, ignore_errors=True)
            self.device('u30air')
            p = self.policy('policy0', freqs, 936000)
            for _ in range(5):
                self.round(shell, 60000)
            self.assertEqual((p / 'scaling_max_freq').read_text().strip(), '2002000')


@unittest.skipUnless(OK, NEEDS)
class ButtonsUnderSysd(ButtonsCases, ShellTest):
    """the keys, with mu300-sysd reading them until its helper's output ends"""
    def setUp(self):
        super().setUp()
        self.shells = [sysd_shell(self.tmp)]


@unittest.skipUnless(OK, NEEDS)
class ThermalGuardUnderSysd(ThermalGuardCases, ShellTest):
    """one round of mu300-sysd each"""
    def setUp(self):
        super().setUp()
        self.shells = [sysd_shell(self.tmp)]


# ------------------------------------------------------------------ the daemon itself
class Clock(threading.Thread):
    """the fake /proc/uptime, running with the real clock from START (the LED files hold uptimes)"""
    def __init__(self, path, start):
        super().__init__(daemon=True)
        self.path, self.base, self.t0, self.stop = Path(path), start, time.monotonic(), False
        self.tick()

    def now(self):
        return self.base + time.monotonic() - self.t0

    def tick(self):
        tmp = self.path.with_suffix('.part')
        tmp.write_text(f'{self.now():.2f} 0.00\n')
        tmp.replace(self.path)

    def run(self):
        while not self.stop:
            self.tick()
            time.sleep(0.05)


@unittest.skipUnless(OK and LINUX, NEEDS)
class Daemon(ShellTest):
    """mu300-sysd running, its helper a stub that passes on what the test writes into a FIFO"""
    def setUp(self):
        super().setUp()
        self.procs, self.feed, self.clock = [], None, None
        self.fifo = self.tmp / 'keys.fifo'
        os.mkfifo(self.fifo)
        self.stub('mu300-keys', 'echo "mu300-keys $*" >> "$STUBLOG/helper"; exec cat "$STUBLOG/keys.fifo"')

    def tearDown(self):
        if self.clock:
            self.clock.stop = True
        if self.feed:
            self.feed.close()
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
            if os.environ.get('MU300_TEST_VERBOSE'):
                print('\n--- sysd stderr:\n' + p.stderr.read())
            p.stderr.close()
        super().tearDown()

    def start(self, feed=True, **env):
        p = subprocess.Popen(sysd_argv(), env=self.env(MU300_SYSD_LOG=self.tmp / 'sysd.log', **env),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        self.procs.append(p)
        if feed:   # the FIFO opens once the helper reads it
            fd = wait_for(lambda: self._open_fifo(), 8)
            self.assertTrue(fd, 'the helper never started')
            self.feed = os.fdopen(fd, 'w')
        return p

    def _open_fifo(self):
        try:
            return os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return None

    def send(self, line):
        self.feed.write(line + '\n')
        self.feed.flush()

    def calls(self):
        f = self.tmp / 'calls'
        return f.read_text().splitlines() if f.exists() else []

    def log(self):
        f = self.tmp / 'sysd.log'
        return f.read_text() if f.exists() else ''

    # --- the LED tick
    def u30air(self):
        root = self.tmp / 'root'
        for n in ('sc27xx:red', 'sc27xx:green', 'sc27xx:blue', 'zte-ldo0'):   # zte-ldo0: SIM 1's network LED
            d = root / 'sys/class/leds' / n
            d.mkdir(parents=True, exist_ok=True)
            (d / 'brightness').write_text('0\n')
            (d / 'max_brightness').write_text('255\n')
            (d / 'trigger').write_text('[none] timer\n')
        ps = root / 'sys/class/power_supply'
        for d, files in (('sc27xx-fgu', {'capacity': 80, 'status': 'Discharging'}), ('bq256xx-charger', {'online': 0})):
            (ps / d).mkdir(parents=True, exist_ok=True)
            for k, v in files.items():
                (ps / d / k).write_text(f'{v}\n')
        (root / 'proc').mkdir(parents=True, exist_ok=True)
        (root / 'run/mu300').mkdir(parents=True, exist_ok=True)
        (root / 'run/mu300/device').write_text('u30air\n')
        return root

    def test_the_led_tick_runs_on_events_and_on_time_only(self):
        """mu300-led tick starts when a uevent, the hint's due time or the end of a key's wake gives it something to
        do: never every 5 s, and not for a 1 % step of the battery that the LED does not show"""
        root = self.u30air()
        ps = root / 'sys/class/power_supply'
        (self.tmp / 'led.conf').write_text('LED_TIMEOUT=3\n')   # a 3 s timeout keeps the test short
        self.stub('mu300-led', f'echo "mu300-led $*" >> "$STUBLOG/calls"; exec "{BIN}/mu300-led" "$@"')
        self.stub('mu300-power', 'echo "mu300-power $*" >> "$STUBLOG/calls"')
        env = dict(MU300_RUN=self.tmp / 'run', MU300_SYSROOT=root, MU300_BIN=BIN, MU300_LED_CONF=self.tmp / 'led.conf',
                   MU300_SYSD_PARTS='keys led', MU300_SYSD_SAFETY=3600)
        self.clock = Clock(root / 'proc/uptime', 100)
        self.clock.start()
        r = subprocess.run(['sh', str(BIN / 'mu300-led'), 'data', 'on'], env=self.env(**env), capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

        def ticks():
            return self.calls().count('mu300-led tick')

        def lit():
            return (root / 'sys/class/leds/zte-ldo0/brightness').read_text().strip()

        self.assertEqual(lit(), '0')                               # no key yet: dark
        self.start(**env)
        self.send('0 tick')                                        # a look now: no hint yet
        self.assertTrue(wait_for(lambda: ticks() == 1), self.calls())
        # a 1 % step: the uevent is looked at, the LED shows the same, nothing starts
        (ps / 'sc27xx-fgu/capacity').write_text('79\n')
        self.send('uevent change power_supply sc27xx-fgu')
        time.sleep(1.5)
        self.assertEqual(ticks(), 1)
        # a key: its wake, no tick for it, and a tick when its timeout runs out (the hint, written while dark, is due
        # only in 10 minutes)
        self.send('116 short')
        self.assertTrue(wait_for(lambda: lit() == '255'), self.calls())
        self.assertIn('mu300-led wake', self.calls())
        time.sleep(0.5)
        self.assertEqual(ticks(), 1)
        self.assertTrue(wait_for(lambda: lit() == '0', 6), self.calls())
        self.assertEqual(ticks(), 2)
        # 59 %: the battery LED turns blue
        (ps / 'sc27xx-fgu/capacity').write_text('59\n')
        self.send('uevent change power_supply sc27xx-fgu')
        self.assertTrue(wait_for(lambda: (root / 'run/mu300/led/.battery').read_text().split()[0] == 'blue'))
        self.assertEqual(ticks(), 3)
        # plugged in: everything lit at once, and it stays lit
        (ps / 'bq256xx-charger/online').write_text('1\n')
        self.send('uevent change power_supply bq256xx-charger')
        self.assertTrue(wait_for(lambda: ticks() == 4 and lit() == '255'), self.calls())
        time.sleep(4)
        self.assertEqual((ticks(), lit()), (4, '255'))
        # unplugged: the timeout starts again, and runs out on time
        (ps / 'bq256xx-charger/online').write_text('0\n')
        self.send('uevent change power_supply bq256xx-charger')
        self.assertTrue(wait_for(lambda: ticks() == 5), self.calls())
        self.assertEqual(lit(), '255')
        self.assertTrue(wait_for(lambda: lit() == '0', 6), self.calls())
        self.assertEqual(ticks(), 6)
        # at rest nothing starts
        time.sleep(2.5)
        self.assertEqual(ticks(), 6)
        self.assertEqual(self.calls().count('mu300-led wake'), 1)

    def test_a_lost_uevent_is_caught_by_the_safety_look(self):
        root = self.u30air()
        self.stub('mu300-led', f'echo "mu300-led $*" >> "$STUBLOG/calls"; exec "{BIN}/mu300-led" "$@"')
        env = dict(MU300_RUN=self.tmp / 'run', MU300_SYSROOT=root, MU300_BIN=BIN, MU300_LED_CONF=self.tmp / 'none',
                   MU300_SYSD_PARTS='keys led', MU300_SYSD_SAFETY=1)
        self.clock = Clock(root / 'proc/uptime', 100)
        self.clock.start()
        self.start(**env)
        self.send('0 tick')
        self.assertTrue(wait_for(lambda: self.calls().count('mu300-led tick') == 1))
        time.sleep(2.5)                                            # looks, nothing changed
        self.assertEqual(self.calls().count('mu300-led tick'), 1)
        (root / 'sys/class/power_supply/sc27xx-fgu/capacity').write_text('5\n')   # no uevent
        battery = root / 'run/mu300/led/.battery'
        self.assertTrue(wait_for(lambda: battery.read_text().split() == ['red', 'steady', '1'], 4))
        self.assertEqual(self.calls().count('mu300-led tick'), 2)

    def led_daemon(self, online, status):
        """sysd's LED part on a U30 Air's supplies (the charger ONLINE, the gauge STATUS), the safety look 3600 s on
        the battery and 1 s on the charger, after its first tick"""
        root = self.u30air()
        ps = root / 'sys/class/power_supply'
        (ps / 'bq256xx-charger/online').write_text(f'{online}\n')
        (ps / 'sc27xx-fgu/status').write_text(f'{status}\n')
        self.stub('mu300-led', f'echo "mu300-led $*" >> "$STUBLOG/calls"; exec "{BIN}/mu300-led" "$@"')
        env = dict(MU300_RUN=self.tmp / 'run', MU300_SYSROOT=root, MU300_BIN=BIN, MU300_LED_CONF=self.tmp / 'none',
                   MU300_SYSD_PARTS='keys led', MU300_SYSD_SAFETY=3600, MU300_SYSD_SAFETY_PLUGGED=1)
        self.clock = Clock(root / 'proc/uptime', 100)
        self.clock.start()
        self.start(**env)
        self.send('0 tick')
        self.assertTrue(wait_for(lambda: self.calls().count('mu300-led tick') == 1))
        return root

    def test_on_the_charger_the_safety_look_comes_sooner(self):
        """the U30 Air's charger has no interrupt wired: charging -> full changes the battery LED without a uevent,
        so on the charger the hint is looked at every SAFETY_PLUGGED s (30)"""
        root = self.led_daemon(1, 'Charging')
        time.sleep(2.5)                                            # looks, nothing changed
        self.assertEqual(self.calls().count('mu300-led tick'), 1)
        (root / 'sys/class/power_supply/sc27xx-fgu/status').write_text('Full\n')    # no uevent
        self.assertTrue(wait_for(lambda: self.calls().count('mu300-led tick') == 2, 4))

    def test_on_the_battery_a_plug_is_an_extcon_uevent(self):
        """on the battery the look waits for SAFETY s (90; 3600 here) or a uevent: the plug's is extcon-gpio's"""
        root = self.led_daemon(0, 'Discharging')
        (root / 'sys/class/power_supply/bq256xx-charger/online').write_text('1\n')   # plugged, no uevent yet
        time.sleep(2.5)
        self.assertEqual(self.calls().count('mu300-led tick'), 1)
        self.send('uevent change extcon extcon0')
        self.assertTrue(wait_for(lambda: self.calls().count('mu300-led tick') == 2, 3))
        self.assertEqual((root / 'run/mu300/led/.plugged').read_text().strip(), '1')

    def test_the_helper_is_asked_for_power_supply_uevents_and_started_again(self):
        self.stub('mu300-keys', 'echo "mu300-keys $*" >> "$STUBLOG/helper"')   # ends at once
        self.start(feed=False, MU300_SYSD_PARTS='keys led', MU300_SYSD_SAFETY=3600)
        helper = self.tmp / 'helper'
        self.assertTrue(wait_for(lambda: helper.exists() and len(helper.read_text().splitlines()) >= 2, 8))
        self.assertEqual(helper.read_text().splitlines()[0], 'mu300-keys -l 3000 -u power_supply,extcon')
        self.assertIn('mu300-keys ended (0); again in', self.log())

    # --- thermal
    def test_the_kernel_zone_found_is_looked_at_again_every_round(self):
        """the zone found last round is looked at alone: one that is switched off or goes away (rmmod) makes the
        guard cap at the next round all the same"""
        root = self.tmp / 'root'
        th = root / 'sys/class/thermal'
        for i, cpu in enumerate((0, 4, 7)):
            d = th / f'cooling_device{i}'
            d.mkdir(parents=True)
            (d / 'type').write_text(f'cpufreq-cpu{cpu}\n')
        for i, kind in enumerate(('lit0-thmzone', 'soc-thmzone')):
            z = th / f'thermal_zone{i}'
            z.mkdir()
            (z / 'type').write_text(kind + '\n')
            (z / 'temp').write_text('88000\n')
            (z / 'mode').write_text('enabled\n')
        soc = th / 'thermal_zone1'
        for i, (tt, tv) in enumerate((('passive', 70000), ('passive', 85000), ('critical', 110000))):
            (soc / f'trip_point_{i}_type').write_text(tt + '\n')
            (soc / f'trip_point_{i}_temp').write_text(f'{tv}\n')
        for i in range(3):
            (soc / f'cdev{i}').symlink_to(f'../cooling_device{i}')
        p = root / 'sys/devices/system/cpu/cpufreq/policy0'
        p.mkdir(parents=True)
        (p / 'scaling_available_frequencies').write_text('614400 1105000 1404000 2002000 \n')
        (p / 'cpuinfo_max_freq').write_text('2002000\n')
        (p / 'scaling_max_freq').write_text('2002000\n')
        (root / 'run/mu300').mkdir(parents=True)
        (root / 'run/mu300/device').write_text('f50\n')
        b = self.tmp / 'bin'
        b.mkdir()
        shutil.copy(BIN / 'mu300-device', b / 'mu300-device')
        (b / 'mu300-led').write_text('#!/bin/sh\necho "mu300-led $*" >> "$STUBLOG/calls"\n')
        (b / 'mu300-led').chmod(0o755)

        def cap():
            return (p / 'scaling_max_freq').read_text().strip()

        self.start(feed=False, MU300_SYSD_PARTS='thermal', MU300_SYSROOT=root, MU300_BIN=b, MU300_THERMAL_INTERVAL=1)
        self.assertTrue(wait_for(lambda: 'soc-thmzone throttles the CPUs: watching only' in self.log()))
        time.sleep(2.5)                                            # hot, but the kernel throttles: no cap
        self.assertEqual(cap(), '2002000')
        (soc / 'mode').write_text('disabled\n')
        self.assertTrue(wait_for(lambda: cap() == '1404000', 4), self.log())
        self.assertIn('kernel thermal zone soc-thmzone gone: capping cpufreq again', self.log())
        (soc / 'mode').write_text('enabled\n')                    # back: the cap this guard set is lifted
        self.assertTrue(wait_for(lambda: cap() == '2002000', 4), self.log())
        shutil.rmtree(soc)                                          # rmmod
        self.assertTrue(wait_for(lambda: cap() == '1404000', 4), self.log())

    def test_thermal_rounds_run_on_their_own_timer(self):
        """the guard's rounds keep going as timers: the alarm and the caps come and go with the readings, the
        power-off at CRIT stays; nothing is started while nothing changes"""
        root = self.tmp / 'root'
        z = root / 'sys/class/thermal/thermal_zone0'
        z.mkdir(parents=True)
        (z / 'temp').write_text('60000\n')
        p = root / 'sys/devices/system/cpu/cpufreq/policy0'
        p.mkdir(parents=True)
        (p / 'scaling_available_frequencies').write_text('614400 1105000 2002000 \n')
        (p / 'cpuinfo_max_freq').write_text('2002000\n')
        (p / 'scaling_max_freq').write_text('2002000\n')
        (root / 'run/mu300').mkdir(parents=True)
        (root / 'run/mu300/device').write_text('f50\n')
        b = self.tmp / 'bin'
        b.mkdir()
        shutil.copy(BIN / 'mu300-device', b / 'mu300-device')
        (b / 'mu300-led').write_text('#!/bin/sh\necho "mu300-led $*" >> "$STUBLOG/calls"\n')
        (b / 'mu300-led').chmod(0o755)
        self.stub('poweroff', 'echo poweroff >> "$STUBLOG/calls"')
        self.start(feed=False, MU300_SYSD_PARTS='thermal', MU300_SYSROOT=root, MU300_BIN=b,
                   MU300_THERMAL_INTERVAL=1, MU300_THERMAL_MAX_INTERVAL_ALONE=1)
        self.assertTrue(wait_for(lambda: 'no kernel thermal zone throttles the CPUs' in self.log()))
        time.sleep(1.5)
        self.assertEqual(self.calls(), [])
        (z / 'temp').write_text('86000\n')
        self.assertTrue(wait_for(lambda: self.calls() == ['mu300-led alarm on'], 4), self.calls())
        self.assertTrue(wait_for(lambda: (p / 'scaling_max_freq').read_text().strip() == '614400', 4))
        self.assertTrue((root / 'run/mu300/thermal-capped').exists())
        (z / 'temp').write_text('74000\n')
        self.assertTrue(wait_for(lambda: self.calls()[-1:] == ['mu300-led alarm off'], 4), self.calls())
        self.assertTrue(wait_for(lambda: (p / 'scaling_max_freq').read_text().strip() == '2002000', 4))
        self.assertTrue(wait_for(lambda: not (root / 'run/mu300/thermal-capped').exists(), 4))
        (z / 'temp').write_text('106000\n')
        self.assertTrue(wait_for(lambda: 'poweroff' in self.calls(), 4), self.calls())
        self.assertIn('critical temperature 106000 mC, powering off', self.log())


# ------------------------------------------------------------------ --check and the init scripts
@unittest.skipUnless(OK, NEEDS)
class Init(ShellTest):
    """/etc/init.d/mu300-sysd runs the daemon, and nothing else runs the keys or the thermal guard"""
    HARNESS = 'procd_open_instance() { echo "instance $*"; }\nprocd_set_param() { echo "param $*"; }\n' \
              'procd_close_instance() { :; }\n'

    def start(self, name):
        r = self.sh(self.shells[0], self.HARNESS + (INIT / name).read_text() + '\nstart_service\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        return [l.split()[1] for l in r.stdout.splitlines() if l.startswith('instance ')], r.stdout

    def test_the_daemon_alone(self):
        names, out = self.start('mu300-sysd')
        self.assertEqual(names, ['sysd'])
        self.assertIn('param command /usr/bin/ucode /opt/mu300/bin/mu300-sysd\n', out)
        self.assertIn('param respawn 3600 5 0', out)


# ------------------------------------------------------------------ mu300-keys -u
@unittest.skipUnless(LINUX and CC, 'needs a C compiler on Linux')
class KeysUevents(unittest.TestCase):
    def test_usage_names_the_option(self):
        r = subprocess.run([str(KEYS), '-?'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn('usage: mu300-keys [-l MS] [-t SEC] [-u SUBSYSTEM[,SUBSYSTEM...]]', r.stderr)

    def test_without_keys_and_without_u_it_ends_as_before(self):
        if list(Path('/dev/input').glob('event*')):
            self.skipTest('this machine has input devices')
        r = subprocess.run([str(KEYS), '-t', '1'], capture_output=True, text=True, timeout=10)
        self.assertEqual((r.returncode, r.stdout), (1, ''))
        self.assertIn('no input device with keys', r.stderr)

    def listen(self, *subsystems):
        p = subprocess.Popen([str(KEYS), '-u', ','.join(subsystems)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(self.end, p)
        time.sleep(0.3)
        if p.poll() is not None:
            self.skipTest('no keys and no uevent socket here: ' + p.stderr.read().decode())
        return p

    def end(self, p):
        if p.poll() is None:
            p.kill()
        p.wait(5)
        for f in (p.stdout, p.stderr):
            f.close()

    def test_it_ends_when_its_reader_goes_away(self):
        p = self.listen('power_supply')
        p.stdout.close()
        self.assertEqual(p.wait(5), 0)

    def test_kernel_uevents_of_the_subsystems_asked_for_and_nobody_else_s(self):
        if os.geteuid() != 0 or not os.access('/sys/class/net/lo/uevent', os.W_OK):
            self.skipTest('needs root and a writable /sys/class/net/lo/uevent')
        p = self.listen('power_supply', 'net')

        def line(timeout):
            r, _, _ = select.select([p.stdout], [], [], timeout)
            return p.stdout.readline().decode() if r else None

        # a datagram to the uevent group from a process: not the kernel's, not reported
        s = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, 15)   # NETLINK_KOBJECT_UEVENT
        s.bind((0, 0))
        fake = b'change@/devices/virtual/net/lo\0ACTION=change\0DEVPATH=/devices/virtual/net/lo\0SUBSYSTEM=net\0'
        try:
            s.sendto(fake, (0, 1))
        except OSError as e:
            self.skipTest(f'cannot send to the uevent group: {e}')
        finally:
            s.close()
        self.assertIsNone(line(0.5))
        # the kernel's own: a synthetic "change" of the loopback device
        Path('/sys/class/net/lo/uevent').write_text('change\n')
        self.assertEqual(line(5), 'uevent change net lo\n')
