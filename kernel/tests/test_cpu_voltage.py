"""The CPU voltage offset (EXPERIMENTAL): the kernel's limits and encoding (the driver's header, compiled on the host)
and the initramfs' decision at every boot (kernel/initramfs/init, cpu-voltage: trial, good mark, the armed mark that
takes an offset back after a crash). The root filesystem's mu300-cpu holds the same firmware list and limits
(docs/contract.md); its own tests check it against the same values."""
import hashlib
import json
import re
import shutil
import subprocess
import unittest

from helpers import TOP, ShellTest

HEADER = TOP / 'kernel' / 'port' / 'drivers' / 'cpufreq' / 'sprd-cpufreq-v2-voltage.h'
DRIVER = TOP / 'kernel' / 'port' / 'drivers' / 'cpufreq' / 'sprd-cpufreq-v2-driver.c'
INIT = (TOP / 'kernel' / 'initramfs' / 'init').read_text()
CC = shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')

HARNESS = r'''
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "sprd-cpufreq-v2-voltage.h"
/* one request per line on stdin: "parse TEXT", "flag BASE A B C", "byte UV" */
int main(void)
{
	char line[256];
	while (fgets(line, sizeof line, stdin)) {
		int uv[3] = {0, 0, 0}, r;
		unsigned int f = 0, base;
		line[strcspn(line, "\n")] = 0;
		if (!strncmp(line, "parse ", 6)) {
			char *s = line + 6, buf[256];
			/* \n in the request stands for a newline */
			size_t i, j = 0;
			for (i = 0; s[i] && j < sizeof buf - 1; i++) {
				if (s[i] == '\\' && s[i + 1] == 'n') { buf[j++] = '\n'; i++; } else buf[j++] = s[i];
			}
			buf[j] = 0;
			r = sprd_volt_parse(buf, uv);
			printf("%d %d %d %d\n", r, uv[0], uv[1], uv[2]);
		} else if (sscanf(line, "flag %x %d %d %d", &base, &uv[0], &uv[1], &uv[2]) == 4) {
			r = sprd_volt_flag(base, uv, &f);
			printf("%d %08x\n", r, f);
		} else if (sscanf(line, "byte %d", &uv[0]) == 1) {
			unsigned int b = sprd_volt_encode_one(uv[0]);
			printf("%02x %d %d\n", b, sprd_volt_firmware_uv(b), sprd_volt_check_one(uv[0]));
		}
	}
	return 0;
}
'''


def defines():
    return {m.group(1): int(m.group(2)) for m in
            re.finditer(r'#define (SPRD_VOLT_\w+)\s+\(?(-?\d+)\)?', HEADER.read_text())}


@unittest.skipUnless(CC, 'no C compiler')
class KernelLimits(unittest.TestCase):
    """The header the driver uses, compiled as it is: what voltage_offset_uv accepts and what DVFS_INIT gets."""
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.dir = tempfile.mkdtemp(prefix='mu300-cpuv-')
        src = f'{cls.dir}/h.c'
        with open(src, 'w') as f:
            f.write(HARNESS)
        r = subprocess.run([CC, '-std=c99', '-Wall', '-Wextra', '-Werror', '-I', str(HEADER.parent), '-o',
                            f'{cls.dir}/h', src], capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(r.stderr)
        cls.bin = f'{cls.dir}/h'

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def ask(self, *lines):
        r = subprocess.run([self.bin], input=''.join(l + '\n' for l in lines), capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_limits_are_conservative(self):
        d = defines()
        self.assertEqual((d['SPRD_VOLT_MIN_UV'], d['SPRD_VOLT_MAX_UV'], d['SPRD_VOLT_STEP_UV']), (-25000, 12500, 3125))
        self.assertEqual(d['SPRD_VOLT_DOMAINS'], 3)
        # tighter than kano's -50..+25 mV, and within what the 7-bit field and the firmware's selector can express
        self.assertGreaterEqual(d['SPRD_VOLT_MIN_UV'], -50000)
        self.assertLessEqual(d['SPRD_VOLT_MAX_UV'], 25000)

    def test_every_step_round_trips_through_the_firmware(self):
        # floor(mV) encoding, and the firmware's ceil(mV * 1000 / 3125) gives back exactly the steps asked for
        out = self.ask(*[f'byte {k * 3125}' for k in range(-8, 5)])
        for k, line in zip(range(-8, 5), out):
            b, fw, chk = line.split()
            self.assertEqual(int(fw), k * 3125, line)
            self.assertEqual(int(chk), 0, line)
            self.assertEqual(int(b, 16) >> 7, 1 if k < 0 else 0)
        # the whole 7-bit range of the firmware: floor encoding is exact for every whole step up to 127 mV
        for k in range(0, 41):
            mv = (k * 3125) // 1000
            self.assertEqual(-(-mv * 1000 // 3125), k)

    def test_outside_or_between_steps_is_refused(self):
        out = self.ask('byte -28125', 'byte 15625', 'byte 1000', 'byte -3124', 'byte 3126')
        for line in out:
            self.assertNotEqual(int(line.split()[2]), 0, line)

    def test_flag(self):
        out = self.ask('flag 0 0 0 0', 'flag 5a 0 0 0', 'flag 12340000 0 0 0',     # zero: the base, unchanged
                       'flag 0 -3125 0 3125',                                       # domain 0 down 3 mV, 2 up 3 mV
                       'flag 0 -25000 -25000 -25000', 'flag 0 12500 12500 12500',
                       'flag 12340000 -3125 0 0',                                   # vendor upper bits: refused
                       'flag 0 -3125 0 99999', 'flag 0 1 0 0')                      # one bad value: none applied
        self.assertEqual(out[0], '0 00000000')
        self.assertEqual(out[1], '0 0000005a')
        self.assertEqual(out[2], '0 12340000')
        self.assertEqual(out[3], '0 03008300')
        self.assertEqual(out[4], '0 99999900')
        self.assertEqual(out[5], '0 0c0c0c00')
        self.assertTrue(out[6].startswith('-') and out[6].split()[0] != '0')
        self.assertNotEqual(out[7].split()[0], '0')
        self.assertNotEqual(out[8].split()[0], '0')

    def test_parse(self):
        ok = {'0,0,0': (0, 0, 0), '-3125,0,3125': (-3125, 0, 3125), '-3125 0 3125': (-3125, 0, 3125),
              '-3125, 0 ,3125\\n': (-3125, 0, 3125), '+3125,0,0': (3125, 0, 0), ' 0,0,0 ': (0, 0, 0)}
        bad = ['', '0,0', '0,0,0,0', '0,,0', '0x10,0,0', '1.5,0,0', '0,0,0x', '1234567,0,0', '-,0,0', '0;0;0',
               '0,0,0\\n\\n', 'a,b,c']
        out = self.ask(*[f'parse {s}' for s in list(ok) + bad])
        for s, line in zip(ok, out):
            r, a, b, c = map(int, line.split())
            self.assertEqual((r, a, b, c), (0,) + ok[s], s)
        for s, line in zip(bad, out[len(ok):]):
            self.assertNotEqual(int(line.split()[0]), 0, s)


class KernelDriver(unittest.TestCase):
    """What the driver must keep (read, not run: the device test does that)."""
    def test_built_in_and_no_export_patch(self):
        cfg = (TOP / 'kernel' / 'mu300-mainline.config').read_text()
        self.assertIn('CONFIG_ARM_SPRD_CPUFREQ_V2=y\n', cfg)
        port = (TOP / 'kernel' / 'port' / 'install.py').read_text()
        self.assertIn('bool "Unisoc UMS9620 cpufreq (v2, ATF DVFS)"', port)
        self.assertNotIn('topology_set_freq_scale', port)

    def test_one_dvfs_init_through_the_checked_flag(self):
        src = DRIVER.read_text()
        self.assertEqual(src.count('->dvfs_init('), 1)
        self.assertIn('ret = pclusters->dvfs_init(flag);', src)
        # the work is the only caller of the start, and it builds the flag with the checked helper
        self.assertEqual(len(re.findall(r'\bsprd_dvfs_start\(', src)), 2)
        self.assertIn('ret = sprd_volt_flag(sprd_dvfs_base_flag, sprd_volt_req, &flag);', src)
        self.assertIn('.suppress_bind_attrs = true', src)
        # never from the command line, root only, and the stock call by itself when nobody writes
        self.assertIn('system_state != SYSTEM_RUNNING || !capable(CAP_SYS_ADMIN)', src)
        self.assertIn('schedule_delayed_work(&sprd_volt_work, SPRD_VOLT_WAIT_S * HZ);', src)
        self.assertIn('module_param_cb(voltage_offset_uv, &sprd_volt_offset_ops, NULL, 0644);', src)
        # a raise needs the DCDC maximum from the device tree, and only UMS9620
        self.assertIn('if (uv[i] > 0 && !sprd_volt_dcdc_max_uv)', src)
        self.assertIn('of_machine_is_compatible("sprd,ums9620")', src)

    def test_wait_outlasts_the_longest_root_wait(self):
        # init waits up to 8 s for a card, 30 s while one is coming (wait_sd_root 8 30), after the modules: the driver
        # must not give up first in a normal boot, and must not hold DVFS for long in a broken one
        self.assertIn('sd_wait() { wait_sd_root 8 30; }', INIT)
        self.assertGreater(defines()['SPRD_VOLT_WAIT_S'], 30)
        self.assertLessEqual(defines()['SPRD_VOLT_WAIT_S'], 60)


def init_block():
    m = re.search(r'# --- cpu-voltage begin\n(.*?)# --- cpu-voltage end', INIT, re.S)
    assert m, 'kernel/initramfs/init has no cpu-voltage block'
    return m.group(1)


SML = b'firmware of a known build\n' * 1000


class InitDecision(ShellTest):
    """kernel/initramfs/init, cpu-voltage: a fake driver (its parameters as files; it 'applies' on the first wait) and a fake disk."""
    def setUp(self):
        super().setUp()
        self.sys = self.tmp / 'params'
        self.sys.mkdir()
        (self.sys / 'voltage_offset_uv').write_text('0,0,0\n')
        (self.sys / 'voltage_state').write_text('waiting\n')
        self.d = self.tmp / 'disk' / '.mu300'
        self.d.mkdir(parents=True)
        for s in 'ab':
            (self.tmp / f'sml_{s}').write_bytes(SML)
        self.known = hashlib.sha256(SML).hexdigest()

    def boot(self, shell, extra='', known=None):
        code = ('log() { echo "LOG $*"; }\n'
                f'emmc_part() {{ echo "{self.tmp}/$1"; }}\n'
                f'usleep() {{ echo applied > "{self.sys}/voltage_state"; }}\n'
                + init_block() + extra + f'\ncpu_voltage_boot "{self.d}"\necho "rc=$?"')
        return self.sh(shell, code, MU300_CPUV_SYS=self.sys,
                       MU300_CPUV_SML_KNOWN=known if known is not None else self.known)

    def f(self, name):
        p = self.d / name
        return p.read_text().strip() if p.exists() else None

    def reset(self, state='waiting', written='0,0,0'):
        (self.sys / 'voltage_state').write_text(state + '\n')
        (self.sys / 'voltage_offset_uv').write_text(written + '\n')

    def written(self):
        return (self.sys / 'voltage_offset_uv').read_text().strip()

    def test_nothing_asked_for_is_the_stock_call(self):
        for shell in self.each_shell():
            self.reset()
            out = self.boot(shell).stdout
            self.assertEqual(self.written(), '0,0,0')
            self.assertIn('LOG stage=cpu-voltage 0,0,0', out)
            self.assertIsNone(self.f('cpu-voltage-armed'))
            # handed over, not waited for (the fake driver 'applies' only on a wait): no boot pays for DVFS starting
            self.assertEqual((self.sys / 'voltage_state').read_text().strip(), 'waiting')

    def test_a_new_value_gets_one_trial_boot_then_the_good_mark_keeps_it(self):
        for shell in self.each_shell():
            for n in ('cpu-voltage-trial', 'cpu-voltage-good', 'cpu-voltage-armed', 'cpu-voltage-rejected'):
                (self.d / n).unlink(missing_ok=True)
            (self.d / 'cpu-voltage').write_text('-6250,-3125,0\n')
            self.reset()
            self.boot(shell)
            self.assertEqual(self.written(), '-6250,-3125,0')
            self.assertEqual(self.f('cpu-voltage-trial'), '-6250,-3125,0')
            self.assertEqual(self.f('cpu-voltage-armed'), '-6250,-3125,0')
            # a clean shutdown (mu300-cpu voltage shutdown) and the good mark (10 minutes up)
            (self.d / 'cpu-voltage-armed').unlink()
            (self.d / 'cpu-voltage-good').write_text('-6250,-3125,0\n')
            (self.d / 'cpu-voltage-trial').unlink()
            self.reset()
            self.boot(shell)
            self.assertEqual(self.written(), '-6250,-3125,0')
            self.assertEqual(self.f('cpu-voltage-armed'), '-6250,-3125,0')
            self.assertIsNone(self.f('cpu-voltage-rejected'))

    def test_a_boot_that_did_not_shut_down_cleanly_drops_the_offset(self):
        for shell in self.each_shell():
            (self.d / 'cpu-voltage').write_text('-6250,-3125,0\n')
            (self.d / 'cpu-voltage-good').write_text('-6250,-3125,0\n')
            (self.d / 'cpu-voltage-armed').write_text('-6250,-3125,0\n')     # crash, watchdog reset, power cut
            self.reset()
            out = self.boot(shell).stdout
            self.assertEqual(self.written(), '0,0,0')
            self.assertEqual(self.f('cpu-voltage'), '0,0,0')
            self.assertIsNone(self.f('cpu-voltage-armed'))
            self.assertIsNone(self.f('cpu-voltage-good'))
            self.assertEqual(self.f('cpu-voltage-rejected'), '-6250,-3125,0 unclean-end')
            self.assertIn('stage=cpu-voltage-rejected', out)

    def test_a_trial_boot_that_ended_before_its_good_mark_gets_no_second_try(self):
        for shell in self.each_shell():
            (self.d / 'cpu-voltage').write_text('-3125,0,0\n')
            (self.d / 'cpu-voltage-trial').write_text('-3125,0,0\n')
            (self.d / 'cpu-voltage-good').unlink(missing_ok=True)
            self.reset()
            self.boot(shell)
            self.assertEqual(self.written(), '0,0,0')
            self.assertEqual(self.f('cpu-voltage-rejected'), '-3125,0,0 no-good-mark')
            self.assertEqual(self.f('cpu-voltage'), '0,0,0')

    def test_an_invalid_request_or_unknown_firmware_is_never_written(self):
        for shell in self.each_shell():
            for value, known, why in (('-50000,0,0', None, 'invalid'), ('-3125,0', None, 'invalid'),
                                      ('03125,0,0', None, 'invalid'), ('-0,0,0', None, 'invalid'),
                                      ('$(reboot),0,0', None, 'invalid'), ('-3125,0,0', '0' * 64, 'unknown-firmware')):
                for n in ('cpu-voltage-trial', 'cpu-voltage-good', 'cpu-voltage-armed', 'cpu-voltage-rejected'):
                    (self.d / n).unlink(missing_ok=True)
                (self.d / 'cpu-voltage').write_text(value + '\n')
                self.reset()
                self.boot(shell, known=known)
                self.assertEqual(self.written(), '0,0,0', value)
                self.assertEqual(self.f('cpu-voltage-rejected'), f'{value} {why}', value)
                self.assertIsNone(self.f('cpu-voltage-armed'), value)

    def test_one_sml_copy_unknown_is_unknown(self):
        (self.tmp / 'sml_b').write_bytes(SML + b'x')
        for shell in self.each_shell():
            (self.d / 'cpu-voltage').write_text('-3125,0,0\n')
            for n in ('cpu-voltage-trial', 'cpu-voltage-rejected'):
                (self.d / n).unlink(missing_ok=True)
            self.reset()
            self.boot(shell)
            self.assertEqual(self.written(), '0,0,0')
            self.assertEqual(self.f('cpu-voltage-rejected'), '-3125,0,0 unknown-firmware')

    def test_kernel_refusal_falls_back_to_the_stock_call(self):
        # the driver refuses the value (a raise without a DCDC maximum): the request is dropped, DVFS starts at stock
        fake = (f'\ncpuv_write() {{ [ "$1" = 0,0,0 ] || return 1; echo "$1" > "{self.sys}/voltage_offset_uv"; '
                f'echo applied > "{self.sys}/voltage_state"; }}\n')
        for shell in self.each_shell():
            (self.d / 'cpu-voltage').write_text('3125,0,0\n')
            for n in ('cpu-voltage-trial', 'cpu-voltage-rejected', 'cpu-voltage-armed'):
                (self.d / n).unlink(missing_ok=True)
            self.reset()
            self.boot(shell, extra=fake)
            self.assertEqual(self.written(), '0,0,0')
            self.assertEqual(self.f('cpu-voltage-rejected'), '3125,0,0 kernel-refused')
            self.assertIsNone(self.f('cpu-voltage-armed'))

    def test_left_alone_without_the_driver_or_once_dvfs_runs(self):
        for shell in self.each_shell():
            (self.d / 'cpu-voltage').write_text('-3125,0,0\n')
            for n in ('cpu-voltage-trial', 'cpu-voltage-rejected', 'cpu-voltage-armed'):
                (self.d / n).unlink(missing_ok=True)
            self.reset(state='timeout', written='0,0,0')
            self.boot(shell)
            self.assertIsNone(self.f('cpu-voltage-trial'))
            self.assertEqual(self.f('cpu-voltage'), '-3125,0,0')
            (self.sys / 'voltage_offset_uv').unlink()
            self.reset(state='waiting', written='0,0,0')
            (self.sys / 'voltage_offset_uv').unlink()
            self.assertIn('rc=0', self.boot(shell).stdout)
            self.assertIsNone(self.f('cpu-voltage-trial'))
            (self.sys / 'voltage_offset_uv').write_text('0,0,0\n')

    def test_place_in_init(self):
        # after the boot-tries backstop (which may send the boot to Android) and before switch_root; the rescue path
        # starts DVFS at the stock voltages rather than after the driver's own wait
        i = INIT.index('    cpu_voltage_boot /disk/.mu300\n')
        self.assertLess(INIT.index('if boot_tries_exceeded "$rootdir" /disk; then'), i)
        self.assertLess(i, INIT.index('exec switch_root /newroot $init'))
        r = INIT.index('log "stage=rootfs-unavailable $(cat /run/rootmount.err)"\ncpu_voltage_stock\n')
        self.assertGreater(r, i)


class Consistency(unittest.TestCase):
    def test_firmware_list_and_limits(self):
        sml = re.search(r'CPUV_SML_KNOWN=\$\{MU300_CPUV_SML_KNOWN:-([0-9a-f ]+)\}', INIT).group(1)
        # the F50's build and the U30 Air's (kanoqwq read them; its payload is the F50's byte for byte); mu300-cpu on
        # the root filesystem holds the same list
        self.assertEqual(sml.split(), ['ff4782690fd805ff54fe34853ef8ee5c7e51c3259e20077c55373b50a4b259c2',
                                       'f27ac16f44e6cdc87a8ee00a9b53c499c8dde0c3b92c771388e256e84ef12e70'])
        valid_init = re.search(r'cpuv_valid\(\) \{.*?\n(.*?)\n\}', INIT, re.S).group(1)
        d = defines()
        self.assertIn(f'-ge {d["SPRD_VOLT_MIN_UV"]} ] && [ "$_cv" -le {d["SPRD_VOLT_MAX_UV"]} ]', valid_init)
        self.assertIn(f'$((_cv % {d["SPRD_VOLT_STEP_UV"]}))', valid_init)


if __name__ == '__main__':
    unittest.main()
