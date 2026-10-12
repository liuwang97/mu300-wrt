"""SIM2 boot gates with fake AT brokers; no modem devices or host /run writes. The cold start, its warm-up wrapper
(the fallback to SIM1 when it cannot be done) and init.d/mu300-atd's dual-SIM path, on macOS too."""
import os
import subprocess
import unittest

from helpers import BIN, TOP, ShellTest


class Sim2ColdStart(ShellTest):
    def setUp(self):
        super().setUp()
        self.run = self.tmp / 'run'
        self.etc = self.tmp / 'etc'
        self.etc.mkdir()
        (self.etc / 'openwrt_release').touch()
        (self.run / 'mu300').mkdir(parents=True)
        (self.run / 'mu300-radio-freeze').touch()
        for name in ('mu300-at', 'mu300-at2', 'mu300-at4', 'mu300-at5'):
            d = self.run / name
            (d / 'owner').mkdir(parents=True)
            (d / 'owner/pid').write_text(str(os.getpid()))
            os.mkfifo(d / 'cmd')
        urc = self.run / 'mu300-at/urc'
        urc.mkdir()
        (urc / 'stty_nr0.log').write_text('CP ready\n')
        self.stub('mu300-device', 'echo "${T_DEVICE-u30air}"')
        self.stub('uci', r'''case "$*" in
 '-q get network.wan.sim_slot') cat "$STUBLOG/etc/uci-slot";;
 '-q get network.wan.sim_hot') cat "$STUBLOG/etc/uci-hot";;
 'set network.wan.sim_slot='*) printf '%s\n' "${2#*=}" > "$STUBLOG/etc/uci-slot";;
 'set network.wan.sim_hot='*) printf '%s\n' "${2#*=}" > "$STUBLOG/etc/uci-hot";;
 'commit network') :;;
 *) exit 1;;
esac''')
        self.stub('sleep', ':')
        self.stub('logger', 'echo "$*" >> "$STUBLOG/logger"')
        self.stub('mu300-at', r'''
shift 2
echo "${MU300_AT_DIR##*/} $1" >> "$STUBLOG/at"
case $1 in
  'AT+CFUN?')
    if [ "${T_ALREADY_ON:-0}" = 1 ] || [ -e "$STUBLOG/radio-on" ]; then
      echo '+CFUN: 1'
    else echo '+CFUN: 0'; fi ;;
  'AT+CPIN?') echo "+CPIN: ${T_PIN:-READY}" ;;
  'AT+CCID') echo '+CCID: 00000000000000000001' ;;
  'AT+SFUN=4') [ "${T_FAIL_SFUN:-0}" = 1 ] && { echo ERROR; exit 0; }; touch "$STUBLOG/radio-on" ;;
  'AT+CEREG?') echo "+CEREG: 2,${T_REG:-1}" ;;
esac
echo OK
''')
        # mobile-data: radio-locked runs the command; radio-on (the SIM1 warm-up) is only recorded
        self.stub('mobile-data', 'case $1 in radio-locked) shift; exec "$@" ;; *) echo "$*" >> "$STUBLOG/md" ;; esac')
        self.start = self.copy_script('mu300-sim2-start')

    def env(self, **extra):
        extra.setdefault('MU300_SIM_ROOT', self.tmp)
        extra.setdefault('MU300_DEVICE_CMD', self.stubs / 'mu300-device')
        extra.setdefault('MU300_SIM_CONFIG_LIB', TOP / 'rootfs/overlay/opt/mu300/lib/sim-config.sh')
        return super().env(**extra)

    def copy_script(self, name):
        p = self.tmp / name
        p.write_text((BIN / name).read_text().replace('/run/', str(self.run) + '/').replace('/etc/', str(self.etc) + '/')
                     .replace('/opt/mu300/bin/mu300-at', 'mu300-at').replace('/opt/mu300/bin/mobile-data', 'mobile-data')
                     .replace('/opt/mu300/bin/mu300-sim2-start', str(self.tmp / 'mu300-sim2-start')))
        return p

    def boot(self, **env):
        return self.script(['bash'], self.start, '--cold-start', **env)

    def calls(self):
        p = self.tmp / 'at'
        return p.read_text().splitlines() if p.exists() else []

    def test_cold_helpers_refuse_unverified_devices_and_other_systems(self):
        for device in ('f50', 'u30pro', 'm3', ''):
            with self.subTest(device=device):
                self.assertNotEqual(self.boot(T_DEVICE=device).returncode, 0)
                self.assertFalse(self.calls())
        (self.etc / 'openwrt_release').unlink()
        self.assertNotEqual(self.boot().returncode, 0)
        self.assertFalse(self.calls())

    def test_success_and_zero_byte_marker(self):
        r = self.boot()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls(), [
            'mu300-at AT+SMMSWAP=0', 'mu300-at AT+CFUN?', 'mu300-at4 AT+CFUN?',
            'mu300-at4 AT+CPIN?', 'mu300-at4 AT+CCID',
            'mu300-at5 AT+SPTESTMODEM=134,134', 'mu300-at5 AT+SPSWDATA',
            'mu300-at5 AT+SFUN=4', 'mu300-at4 AT+CFUN?'])
        self.assertEqual((self.run / 'mu300/sim2-radio-ready').stat().st_size, 0)
        self.assertTrue((self.run / 'mu300/sim-radio-ready').exists())
        self.assertTrue((self.run / 'mu300/sim2-cold-start.sent').exists())
        self.assertNotIn('00000000000000000001', (self.run / 'mu300/sim2-cold-start.log').read_text())
        self.assertNotEqual(self.boot().returncode, 0)
        self.assertEqual(len(self.calls()), 9, 'second attempt must send no commands')

    def test_registration_is_not_a_boot_gate(self):
        # CFUN=1 is the radio ready, as for SIM1: a slow registration is netifd's to wait for, never a lost boot
        r = self.boot(T_REG='2')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.run / 'mu300/sim2-radio-ready').exists())
        self.assertFalse(any('CEREG' in c for c in self.calls()))

    def test_active_radio_is_not_restarted(self):
        self.assertNotEqual(self.boot(T_ALREADY_ON='1').returncode, 0)
        self.assertEqual(self.calls(), ['mu300-at AT+SMMSWAP=0', 'mu300-at AT+CFUN?'])
        self.assertFalse((self.run / 'mu300/sim2-cold-start.sent').exists())

    def test_wrong_card_stops_before_radio_on(self):
        self.assertNotEqual(self.boot(MU300_EXPECT_CCID='synthetic-mismatch').returncode, 0)
        self.assertFalse(any('SFUN' in c or 'SPTESTMODEM' in c for c in self.calls()))
        self.assertFalse((self.run / 'mu300/sim2-cold-start.sent').exists())

    def test_unready_card_stops_before_radio_on(self):
        self.assertNotEqual(self.boot(T_PIN='SIM PIN').returncode, 0)
        self.assertFalse(any('SFUN' in c or 'SPTESTMODEM' in c for c in self.calls()))

    def test_rejected_sfun_is_not_success(self):
        self.assertNotEqual(self.boot(T_FAIL_SFUN='1').returncode, 0)
        self.assertFalse((self.run / 'mu300/sim2-radio-ready').exists())
        self.assertTrue((self.run / 'mu300/sim2-cold-start.sent').exists())

    def test_missing_owner_does_not_open_tty(self):
        (self.run / 'mu300-at4/owner/pid').unlink()
        self.assertNotEqual(self.boot().returncode, 0)
        self.assertFalse(any(c.startswith('mu300-at4 ') for c in self.calls()))

    def test_missing_cp_output_sends_nothing(self):
        (self.run / 'mu300-at/urc/stty_nr0.log').write_text('')
        self.assertNotEqual(self.boot().returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_sim1_cold_path_uses_its_own_data_broker(self):
        r = self.boot(MU300_SIM_SLOT='0')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('mu300-at2 AT+SPSWDATA', self.calls())
        self.assertNotIn('mu300-at5 AT+SFUN=4', self.calls())
        self.assertTrue((self.run / 'mu300/sim-radio-ready').exists())

    # --- the warm-up wrapper: confirms the boot, or falls back to SIM1
    def warmup(self, **env):
        (self.run / 'mu300/sim-slot').write_text('1\n')
        (self.etc / 'uci-slot').write_text('1\n')
        (self.etc / 'uci-hot').write_text('1\n')
        (self.etc / 'mu300-sim-pending').write_text('')
        (self.run / 'mu300/sim-managed').touch()
        return self.script(['bash'], self.copy_script('mu300-sim2-warmup'), **env)

    def settings(self):
        return ((self.etc / 'uci-slot').read_text(), (self.etc / 'uci-hot').read_text(),
                (self.etc / 'mu300-sim-pending').exists(),
                (self.etc / 'mu300-sim-fallback').read_text() if (self.etc / 'mu300-sim-fallback').exists() else None)

    def test_charging_boot_sends_nothing(self):
        (self.run / 'mu300/charging-boot').touch()
        self.assertEqual(self.boot().returncode, 0)
        self.assertEqual(self.warmup().returncode, 0)
        self.assertEqual(self.calls(), [])
        self.assertTrue((self.etc / 'mu300-sim-pending').exists(), 'the real boot confirms')

    def test_warmup_confirms_the_boot(self):
        r = self.warmup()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.settings(), ('1\n', '1\n', False, None))
        self.assertTrue((self.run / 'mu300/sim2-radio-ready').exists())
        self.assertFalse((self.tmp / 'md').exists())

    def test_failed_cold_start_falls_back_to_sim1_at_the_next_boot(self):
        # the radio was told to start SIM2 (SPTESTMODEM went out): not undone in this boot, SIM1 from the next one
        r = self.warmup(T_FAIL_SFUN='1')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.settings(), ('0\n', '0\n', False, 'cold-start-failed\n'))
        self.assertFalse((self.tmp / 'md').exists(), 'no SIM1 bring-up on a radio that was told otherwise')
        self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '1\n')
        self.assertIn('reboot for SIM1', (self.tmp / 'logger').read_text())

    def test_cold_start_refused_before_any_command_falls_back_in_this_boot(self):
        (self.etc / 'mu300').mkdir()
        for case in ('pin', 'ccid'):
            with self.subTest(case=case):
                for p in ('at', 'md', 'logger', 'radio-on'):
                    (self.tmp / p).unlink(missing_ok=True)
                for p in ('mu300/sim2-cold-start.attempted', 'mu300/sim2-cold-start.sent', 'mu300/sim-managed',
                          'mu300-radio-freeze'):
                    (self.run / p).unlink(missing_ok=True)
                (self.run / 'mu300-radio-freeze').touch()
                env = {'T_PIN': 'SIM PIN'} if case == 'pin' else {}
                if case == 'ccid':   # the recorded card (private file) is not the one in the slot
                    (self.etc / 'mu300/sim2-iccid').write_text('synthetic-mismatch\n')
                r = self.warmup(**env)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.settings(), ('0\n', '0\n', False, 'cold-start-failed\n'))
                self.assertFalse(any('SFUN' in c or 'SPTESTMODEM' in c for c in self.calls()))
                # this boot: the pin becomes SIM1, the freeze goes, the usual warm-up runs
                self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '0\n')
                self.assertFalse((self.run / 'mu300-radio-freeze').exists())
                self.assertFalse((self.run / 'mu300/sim-managed').exists())
                self.assertEqual((self.tmp / 'md').read_text(), 'radio-on\n')

    def test_missing_brokers_fall_back_without_touching_the_radio(self):
        os.unlink(self.run / 'mu300-at5/cmd')
        r = self.warmup()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.settings(), ('0\n', '0\n', False, 'brokers-missing\n'))
        self.assertEqual(self.calls(), [])
        self.assertEqual((self.tmp / 'md').read_text(), 'radio-on\n')

    def test_mobile_data_uses_ready_marker_without_second_bringup(self):
        lib = self.copy_script('mobile-data')
        (self.run / 'mu300/sim2-radio-ready').touch()
        (self.run / 'mu300/sim-radio-ready').touch()
        r = self.sh(['bash'], f'MU300_LIB=1; . "{lib}"\n'
                    'at() { echo "$*" >> "$STUBLOG/radio-check"; printf "+CFUN: 1\\r\\nOK\\n"; }\n'
                    'radio_on_locked', MU300_SIM_SLOT='1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.tmp / 'radio-check').read_text(), 'AT+CFUN? 4\n')

    def test_sim2_suspend_refused_before_any_external_action(self):
        lib = self.copy_script('mobile-data')
        r = self.sh(['bash'], f'MU300_LIB=1; . "{lib}"\n'
                    'at() { echo AT >> "$STUBLOG/actions"; }\n'
                    'ifdown() { echo ifdown >> "$STUBLOG/actions"; }\n'
                    'do_suspend off', MU300_SIM_SLOT='1')
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.tmp / 'actions').exists())
        self.assertFalse((self.run / 'mu300-mobile-data.suspend').exists())

    def test_runtime_scripts_read_the_boot_pin_only(self):
        # the saved choice is init.d/mu300-atd's to read (on the U30 Air alone): a stray /etc/mu300-sim-slot on
        # another device or on Ubuntu, where no second broker exists, changes nothing for mu300-at and mobile-data
        for name in ('mu300-at', 'mobile-data', 'sipa-dele-start'):
            with self.subTest(name=name):
                text = (BIN / name).read_text()
                self.assertNotIn('cat /etc/mu300-sim-slot', text)
        for rel in ('openwrt/overlay/lib/netifd/proto/mu300cell.sh', 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/lock',
                    'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/cell', 'openwrt/luci-overlay/etc/init.d/mu300-atd-dash'):
            with self.subTest(rel=rel):
                self.assertNotIn('cat /etc/mu300-sim-slot', (TOP / rel).read_text())

    # --- init.d/mu300-atd
    def init_service(self, tail='start_service', device='u30air', **env):
        source = TOP / 'openwrt/overlay/etc/init.d/mu300-atd'
        text = source.read_text().replace('/run/', str(self.run) + '/')
        text = text.replace('/etc/mu300-sim-slot', str(self.etc / 'uci-slot'))
        text = text.replace('/etc/mu300-sim-hot', str(self.etc / 'uci-hot'))
        text = text.replace('/etc/mu300-sim-pending', str(self.tmp / 'pending'))
        text = text.replace('/etc/mu300-sim-fallback', str(self.tmp / 'fallback'))
        text = text.replace('/opt/mu300/bin/mu300-device', 'mu300-device')
        harness = f'''
procd_open_instance() {{ echo "instance $*"; }}
procd_set_param() {{ echo "param $*"; }}
procd_close_instance() {{ :; }}
mu300-device() {{ echo {device}; }}
sleep() {{ :; }}
'''
        return self.sh(['bash'], harness + text + '\n' + tail, **env)

    def instances(self, r):
        return [l.split()[1] for l in r.stdout.splitlines() if l.startswith('instance ')]

    def test_hot_sim1_boot_opens_both_groups_once(self):
        (self.etc / 'uci-slot').write_text('0\n')
        (self.etc / 'uci-hot').write_text('1\n')
        r = self.init_service()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count('MU300_AT_DISABLE_REOPEN=1'), 4)
        self.assertTrue((self.run / 'mu300/sim-managed').exists())
        self.assertNotIn('param respawn', r.stdout)
        self.assertTrue((self.tmp / 'pending').exists())

    def test_setting_is_pinned_until_reboot(self):
        (self.etc / 'uci-slot').write_text('0\n')
        r = self.init_service(f'start_service; echo 1 > "{self.tmp}/choice"; start_service')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '0\n')
        self.assertNotIn('instance atd4', r.stdout)

    def test_sim2_owners_are_persistent_and_not_respawned(self):
        (self.etc / 'uci-slot').write_text('1\n')
        r = self.init_service()
        self.assertEqual(r.returncode, 0, r.stderr)
        for channel in (1, 2, 4, 5):
            self.assertIn(f'MU300_AT_DEV=/dev/stty_nr{channel}', r.stdout)
        self.assertEqual(r.stdout.count('MU300_AT_DISABLE_REOPEN=1'), 4)
        self.assertNotIn('param respawn', r.stdout)
        self.assertEqual(self.instances(r), ['atd', 'atd2', 'atd4', 'atd5', 'sim2-cold-start'])
        self.assertTrue((self.tmp / 'pending').exists())

    def test_second_group_waits_for_the_sbuf_inside_its_instance(self):
        # the wait is the instance's, bounded, never start_service's: a boot always gets its brokers
        (self.etc / 'uci-slot').write_text('1\n')
        r = self.init_service()
        commands = [l for l in r.stdout.splitlines() if l.startswith('param command ')]
        self.assertEqual(len(commands), 5)
        for c in commands:
            if 'stty_nr4' in c or 'stty_nr5' in c:
                self.assertIn("sbuf_5_006, state: 1,", c); self.assertIn('/dev/sipc_sbuf', c); self.assertIn('-gt 240', c)
            else:
                self.assertNotIn('sipc_sbuf', c)
        self.assertNotIn('sipc_sbuf', r.stderr)

    def test_generated_broker_wait_reaches_exec_on_posix_shells(self):
        (self.etc / 'uci-slot').write_text('1\n')
        r = self.init_service()
        commands = [l.removeprefix('param command /bin/sh -c ') for l in r.stdout.splitlines()
                    if l.startswith('param command /bin/sh -c ')]
        self.assertEqual(len(commands), 4)
        for command in commands:
            # Execute the real generated command, with only the device/readiness and owner substituted.
            # In particular, a stray ]} is syntactically valid but makes the test loop forever.
            import re
            command = re.sub(r'\[ -c /dev/stty_nr[1245] \]', 'true', command)
            command = command.replace("[ -c /dev/sipc_sbuf ] && grep -q '^sbuf_5_006, state: 1,' /dev/sipc_sbuf", 'true')
            command = command.replace('exec /opt/mu300/bin/mu300-atd', 'echo owner-started')
            for shell in ('sh', 'bash'):
                result = subprocess.run([shell, '-c', 'sleep() { exit 97; }; ' + command], capture_output=True, text=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), 'owner-started')

    def test_other_devices_ignore_the_dual_sim_files(self):
        (self.etc / 'uci-slot').write_text('1\n')
        (self.etc / 'uci-hot').write_text('1\n')
        (self.run / 'mu300-radio-freeze').unlink()
        for device in ('f50', 'u30pro', ''):
            with self.subTest(device=device):
                r = self.init_service(device=device)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.instances(r), ['atd', 'atd2', 'radio-warmup'])
                self.assertFalse((self.tmp / 'pending').exists())
                self.assertFalse((self.run / 'mu300-radio-freeze').exists())
        self.assertEqual((self.etc / 'uci-slot').read_text(), '1\n', 'left alone, for a U30 Air that reads it')

    def test_early_runtime_reader_cannot_hide_an_unconfirmed_previous_boot(self):
        (self.etc / 'uci-slot').write_text('1\n')
        (self.etc / 'uci-hot').write_text('1\n')
        (self.run / 'mu300/sim-slot').write_text('1\n')
        (self.tmp / 'pending').touch()
        result = self.init_service()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.instances(result), ['atd', 'atd2', 'radio-warmup'])
        self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '0\n')
        self.assertEqual((self.etc / 'uci-slot').read_text(), '0\n')

    def test_unconfirmed_boot_falls_back_to_sim1(self):
        # the last boot's dual-SIM path never confirmed (a broker that never came up, a warm-up that died)
        (self.etc / 'uci-slot').write_text('1\n')
        (self.etc / 'uci-hot').write_text('1\n')
        (self.tmp / 'pending').write_text('')
        r = self.init_service()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.instances(r), ['atd', 'atd2', 'radio-warmup'])
        self.assertEqual((self.etc / 'uci-slot').read_text(), '0\n')
        self.assertEqual((self.etc / 'uci-hot').read_text(), '0\n')
        self.assertEqual((self.tmp / 'fallback').read_text(), 'unconfirmed\n')
        self.assertFalse((self.tmp / 'pending').exists())
        self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '0\n')
        self.assertIn('unconfirmed', r.stderr)

    def test_pending_marker_survives_a_restart_within_the_boot(self):
        (self.etc / 'uci-slot').write_text('1\n')
        (self.tmp / 'pending').write_text('')
        (self.run / 'mu300/sim-slot').write_text('1\n')
        (self.run / 'mu300/sim-boot-policy').touch()   # this boot's pin: the service is being restarted
        r = self.init_service()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('atd4', self.instances(r))
        self.assertEqual((self.etc / 'uci-slot').read_text(), '1\n')


if __name__ == '__main__':
    unittest.main()
