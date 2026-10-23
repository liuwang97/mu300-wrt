"""mu300-cpu voltage (the CPU voltage offset, EXPERIMENTAL), against a fake sysroot. The initramfs makes the boot's
decision with the same firmware list and limits (docs/contract.md); the kernel tree's tests hold it to the values
here."""
import hashlib
import json
import re
import shutil
import unittest

from helpers import BIN, TOP, ShellTest

CPU = BIN / 'mu300-cpu'
WARNING = TOP / 'openwrt' / 'opt' / 'mu300' / 'lib' / 'cpu-voltage-warning.txt'
SML = b'firmware of a known build\n' * 1000
# the SML builds whose DVFS_INIT encoding is known: the F50's and the U30 Air's
KNOWN_SML = ['ff4782690fd805ff54fe34853ef8ee5c7e51c3259e20077c55373b50a4b259c2',
             'f27ac16f44e6cdc87a8ee00a9b53c499c8dde0c3b92c771388e256e84ef12e70']
# the cpufreq driver's limits: -25 mV .. +12.5 mV in 3.125 mV steps
MIN_UV, MAX_UV, STEP_UV = -25000, 12500, 3125


class Cli(ShellTest):
    """mu300-cpu voltage against a fake sysroot: the driver's parameters and cpufreq attributes, the eMMC's SML
    partitions, the Linux disk's .mu300."""
    def setUp(self):
        super().setUp()
        self.stub('logger', ':')
        self.stub('sync', ':')
        self.root = self.tmp / 'root'
        self.run_dir = self.tmp / 'run'
        (self.run_dir / 'mu300').mkdir(parents=True)
        self.p = self.root / 'sys/module/sprd_cpufreq_v2_driver/parameters'
        self.p.mkdir(parents=True)
        self.kernel('applied', '0,0,0')
        (self.p / 'voltage_limits').write_text('min=-25000 max=12500 step=3125 dcdc_max=1100000 wait=45\n')
        self.disk = self.tmp / 'disk'
        (self.disk / '.mu300').mkdir(parents=True)
        (self.root / 'proc').mkdir()
        (self.root / 'proc' / 'uptime').write_text('700.12 100.00\n')
        # the eMMC (type MMC) with sml_a and sml_b, and a card that also has an sml_a
        self.emmc_sml(SML, SML)
        cf = self.root / 'sys/devices/system/cpu/cpufreq'
        for dom, (name, cpus) in enumerate((('policy0', '0 1 2 3'), ('policy4', '4 5 6'), ('policy7', '7'))):
            d = cf / name
            d.mkdir(parents=True)
            (d / 'related_cpus').write_text(cpus + '\n')
            (d / 'scaling_voltage_domain').write_text(f'{dom}\n')
            (d / 'scaling_voltage_offset').write_text('0\n')
            (d / 'scaling_voltage_table').write_text('614400 553125\n1228800 650000\n2002000 853125\n')
        self.known = hashlib.sha256(SML).hexdigest()

    def emmc_sml(self, a, b):
        for disk, typ, parts in (('mmcblk0', 'MMC', (('mmcblk0p5', 'sml_a', a), ('mmcblk0p6', 'sml_b', b))),
                                 ('mmcblk1', 'SD', (('mmcblk1p1', 'sml_a', b'card'),))):
            dd = self.root / 'sys/block' / disk
            (dd / 'device').mkdir(parents=True, exist_ok=True)
            (dd / 'device' / 'type').write_text(typ + '\n')
            for part, name, data in parts:
                (dd / part).mkdir(exist_ok=True)
                (dd / part / 'uevent').write_text(f'MAJOR=179\nDEVNAME={part}\nPARTNAME={name}\n')
                (self.root / 'dev').mkdir(exist_ok=True)
                (self.root / 'dev' / part).write_bytes(data)
        (self.run_dir / 'mu300' / 'cpu-voltage-firmware').unlink(missing_ok=True)

    def kernel(self, state, applied):
        (self.p / 'voltage_state').write_text(state + '\n')
        (self.p / 'voltage_offset_uv').write_text(applied + '\n')

    def cpu(self, shell, args, **env):
        e = dict(MU300_SYSROOT=self.root, MU300_RUN=self.run_dir, MU300_CPU_CONF=self.tmp / 'cpu.conf',
                 MU300_DISK=self.disk, MU300_CPUV_SML_KNOWN=self.known, MU300_CPUV_WARNING=WARNING)
        e.update(env)
        return self.sh(shell, f'"{CPU}" voltage {args}', **e)

    def f(self, name):
        p = self.disk / '.mu300' / name
        return p.read_text().strip() if p.exists() else None

    def clear(self):
        for p in (self.disk / '.mu300').iterdir():
            p.unlink()

    def test_status_json(self):
        for shell in self.each_shell():
            self.clear()
            r = self.cpu(shell, 'status --json')
            s = json.loads(r.stdout)
            self.assertEqual(s['kernel'], 1)
            self.assertEqual(s['phase'], 'off')
            self.assertEqual(s['applied'], [0, 0, 0])
            self.assertEqual(s['limits'], {'min': -25000, 'max': 12500, 'step': 3125, 'dcdc_max': 1100000})
            self.assertEqual(s['firmware'], 'known')
            self.assertEqual(s['confirm'], 'EXPERIMENTAL')
            self.assertEqual([d['cpus'] for d in s['domains']], ['0-3', '4-6', '7-7'])
            self.assertEqual(s['domains'][2]['table'][-1], [2002000, 853125])
            self.assertIsNone(s['rejected'])

    def test_status_phases_and_rejection(self):
        for shell in self.each_shell():
            self.clear()
            (self.disk / '.mu300' / 'cpu-voltage').write_text('-6250,0,0\n')
            self.assertEqual(json.loads(self.cpu(shell, 'status --json').stdout)['phase'], 'pending')
            self.kernel('applied', '-6250,0,0')
            (self.disk / '.mu300' / 'cpu-voltage-armed').write_text('-6250,0,0\n')
            s = json.loads(self.cpu(shell, 'status --json').stdout)
            self.assertEqual((s['phase'], s['armed'], s['applied']), ('trial', 1, [-6250, 0, 0]))
            (self.disk / '.mu300' / 'cpu-voltage-good').write_text('-6250,0,0\n')
            self.assertEqual(json.loads(self.cpu(shell, 'status --json').stdout)['phase'], 'good')
            (self.disk / '.mu300' / 'cpu-voltage-rejected').write_text('-6250,0,0 unclean-end\n')
            s = json.loads(self.cpu(shell, 'status --json').stdout)
            self.assertEqual(s['rejected'], {'offsets': [-6250, 0, 0], 'reason': 'unclean-end'})
            self.kernel('applied', '0,0,0')

    def test_set_needs_the_confirmation_word(self):
        for shell in self.each_shell():
            self.clear()
            r = self.cpu(shell, 'set -6250,-3125,0')
            self.assertEqual(r.returncode, 2)
            self.assertIn('--confirm EXPERIMENTAL', r.stderr)
            self.assertIn('PERMANENTLY DAMAGE', r.stderr)
            self.assertIsNone(self.f('cpu-voltage'))
            r = self.cpu(shell, 'set -6250,-3125,0 --confirm experimental')
            self.assertEqual(r.returncode, 2)
            self.assertIsNone(self.f('cpu-voltage'))
            (self.disk / '.mu300' / 'cpu-voltage-good').write_text('-3125,0,0\n')
            (self.disk / '.mu300' / 'cpu-voltage-trial').write_text('-3125,0,0\n')
            r = self.cpu(shell, '"set" "-6250 -3125 0" --confirm EXPERIMENTAL')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.f('cpu-voltage'), '-6250,-3125,0')
            self.assertIsNone(self.f('cpu-voltage-good'))     # a new value starts over: one trial boot
            self.assertIsNone(self.f('cpu-voltage-trial'))
            self.assertIn('next reboot', r.stdout)

    def test_set_refuses(self):
        for shell in self.each_shell():
            self.clear()
            for value in ('-28125,0,0', '1000,0,0', '-3125,0', '0x10,0,0', '-3125,0,0;reboot', '15625,0,0'):
                r = self.cpu(shell, f'set "{value}" --confirm EXPERIMENTAL')
                self.assertEqual(r.returncode, 2, value)
                self.assertIsNone(self.f('cpu-voltage'), value)
            # a raise without the DCDC maximum
            (self.p / 'voltage_limits').write_text('min=-25000 max=12500 step=3125 dcdc_max=0 wait=45\n')
            r = self.cpu(shell, 'set 3125,0,0 --confirm EXPERIMENTAL')
            self.assertEqual(r.returncode, 2)
            self.assertIn('DCDC', r.stderr)
            self.assertEqual(self.cpu(shell, 'set -3125,0,0 --confirm EXPERIMENTAL').returncode, 0)
            (self.p / 'voltage_limits').write_text('min=-25000 max=12500 step=3125 dcdc_max=1100000 wait=45\n')
            self.clear()
            # DVFS not running normally
            self.kernel('timeout', '0,0,0')
            self.assertEqual(self.cpu(shell, 'set -3125,0,0 --confirm EXPERIMENTAL').returncode, 0)
            for st in ('disabled', 'failed', 'probing'):
                self.clear()
                self.kernel(st, '0,0,0')
                self.assertEqual(self.cpu(shell, 'set -3125,0,0 --confirm EXPERIMENTAL').returncode, 1, st)
            self.kernel('applied', '0,0,0')

    def test_unknown_firmware_or_no_kernel_support_is_refused(self):
        for shell in self.each_shell():
            self.clear()
            self.emmc_sml(SML, b'another build')
            r = self.cpu(shell, 'set -3125,0,0 --confirm EXPERIMENTAL')
            self.assertEqual(r.returncode, 1)
            self.assertIn('unknown', r.stderr)
            self.assertEqual(json.loads(self.cpu(shell, 'status --json').stdout)['firmware'], 'unknown')
            self.emmc_sml(SML, SML)
            shutil.move(self.p / 'voltage_state', self.tmp / 'state')
            r = self.cpu(shell, 'set -3125,0,0 --confirm EXPERIMENTAL')
            self.assertEqual(r.returncode, 1)
            self.assertEqual(json.loads(self.cpu(shell, 'status --json').stdout)['phase'], 'unsupported')
            shutil.move(self.tmp / 'state', self.p / 'voltage_state')
            self.assertIsNone(self.f('cpu-voltage'))

    def test_status_without_the_driver_is_quiet(self):
        """A kernel without the offset (5.4, an older mainline: no parameters at all) is "unsupported", with nothing on
        stderr: on F50-C's 6.18.56 status --json printed "can't open .../voltage_limits" four times (the "<" failed
        before the 2>/dev/null after it applied)."""
        shutil.rmtree(self.root / 'sys/module')
        for shell in self.each_shell():
            r = self.cpu(shell, 'status --json')
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stderr, '')
            s = json.loads(r.stdout)
            self.assertEqual((s['kernel'], s['phase']), (0, 'unsupported'))
            self.assertEqual(s['limits'], {'min': 0, 'max': 0, 'step': 0, 'dcdc_max': 0})

    def test_off_needs_no_word(self):
        for shell in self.each_shell():
            self.clear()
            (self.disk / '.mu300' / 'cpu-voltage').write_text('-6250,0,0\n')
            (self.disk / '.mu300' / 'cpu-voltage-good').write_text('-6250,0,0\n')
            self.kernel('applied', '-6250,0,0')
            r = self.cpu(shell, 'off')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.f('cpu-voltage'), '0,0,0')
            self.assertIsNone(self.f('cpu-voltage-good'))
            self.assertIn('until you reboot', r.stdout)
            self.assertEqual(self.cpu(shell, 'set 0,0,0').returncode, 0)
            self.kernel('applied', '0,0,0')

    def test_mark_good(self):
        for shell in self.each_shell():
            self.clear()
            (self.disk / '.mu300' / 'cpu-voltage').write_text('-6250,0,0\n')
            (self.disk / '.mu300' / 'cpu-voltage-trial').write_text('-6250,0,0\n')
            (self.disk / '.mu300' / 'cpu-voltage-armed').write_text('-6250,0,0\n')
            self.kernel('applied', '-6250,0,0')
            (self.root / 'proc' / 'uptime').write_text('300.5 1.0\n')
            self.assertNotEqual(self.cpu(shell, 'mark-good').returncode, 0)        # too early
            self.assertIsNone(self.f('cpu-voltage-good'))
            (self.root / 'proc' / 'uptime').write_text('601.5 1.0\n')
            self.assertEqual(self.cpu(shell, 'mark-good').returncode, 0)
            self.assertEqual(self.f('cpu-voltage-good'), '-6250,0,0')
            self.assertIsNone(self.f('cpu-voltage-trial'))
            # the kernel does not run what is armed: no mark
            (self.disk / '.mu300' / 'cpu-voltage-good').unlink()
            self.kernel('applied', '0,0,0')
            self.cpu(shell, 'mark-good')
            self.assertIsNone(self.f('cpu-voltage-good'))

    def test_shutdown_removes_the_armed_mark(self):
        for shell in self.each_shell():
            self.clear()
            (self.disk / '.mu300' / 'cpu-voltage-armed').write_text('-6250,0,0\n')
            self.assertEqual(self.cpu(shell, 'shutdown').returncode, 0)
            self.assertIsNone(self.f('cpu-voltage-armed'))

    def test_systemd_stop_is_not_a_shutdown(self):
        # outside a sysroot the systemd check runs: "running" means somebody stopped the service by hand
        self.stub('systemctl', 'echo running')
        self.stub('id', 'echo 0')
        for shell in self.each_shell():
            self.clear()
            (self.disk / '.mu300' / 'cpu-voltage-armed').write_text('-6250,0,0\n')
            r = self.sh(shell, f'"{CPU}" voltage shutdown', MU300_DISK=self.disk, MU300_RUN=self.run_dir)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(self.f('cpu-voltage-armed'), '-6250,0,0')
            self.stub('systemctl', 'echo stopping')
            r = self.sh(shell, f'"{CPU}" voltage shutdown', MU300_DISK=self.disk, MU300_RUN=self.run_dir)
            self.assertIsNone(self.f('cpu-voltage-armed'))
            self.stub('systemctl', 'echo running')

    def test_root_only(self):
        self.stub('id', 'echo 1000')
        for shell in self.each_shell():
            self.clear()
            for args in ('set -3125,0,0 --confirm EXPERIMENTAL', 'off', 'stress 1'):
                r = self.sh(shell, f'"{CPU}" voltage {args}', MU300_DISK=self.disk, MU300_RUN=self.run_dir)
                self.assertNotEqual(r.returncode, 0, args)
            self.assertIsNone(self.f('cpu-voltage'))

    def test_stress_minutes(self):
        self.stub('setsid', 'shift; echo "$@" > "$STUBLOG/stress"; sleep 0')
        self.stub('fake-toolkit', 'echo "$@" > "$STUBLOG/stress-fg"')
        for shell in self.each_shell():
            for bad in ('0', '121', 'x', '-1', '1;reboot'):
                self.assertEqual(self.cpu(shell, f'stress "{bad}"', MU300_TOOLKIT='fake-toolkit').returncode, 2, bad)
            r = self.cpu(shell, 'stress 7', MU300_TOOLKIT=self.stubs / 'fake-toolkit')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.tmp / 'stress-fg').read_text().strip(), 'stress cpu 7')

    def test_warning_in_three_languages(self):
        text = WARNING.read_text(encoding='utf-8')
        self.assertIn('PERMANENTLY DAMAGE', text)
        self.assertIn('kalıcı zarar verebilirsiniz', text)            # the owner's words
        self.assertIn('永久性损坏', text)                   # permanent damage
        for shell in self.each_shell():
            self.assertEqual(self.cpu(shell, 'warning').stdout, text)


class Consistency(unittest.TestCase):
    def test_the_contract_s_firmware_list_and_limits(self):
        cli = CPU.read_text()
        sml = re.search(r'CPUV_SML_KNOWN=\$\{MU300_CPUV_SML_KNOWN:-([0-9a-f ]+)\}', cli).group(1)
        self.assertEqual(sml.split(), KNOWN_SML)
        valid = re.search(r'cpuv_valid\(\) \{.*?\n(.*?)\n\}', cli, re.S).group(1)
        self.assertIn(f'-ge {MIN_UV} ] && [ "$_cv" -le {MAX_UV} ]', valid)
        self.assertIn(f'$((_cv % {STEP_UV}))', valid)


if __name__ == '__main__':
    unittest.main()
