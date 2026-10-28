"""kanoqwq's charger and NCM kernel patches (kernel/patches/0024-0026): the U30 Air's battery
CV (in the stock driver's encoding) and stock safety-timer policy on, its 3 A and the NCM busy-flush retry off unless
asked for."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PATCHES = Path(__file__).resolve().parents[2] / 'kernel/patches'


def patch(prefix):
    found = sorted(PATCHES.glob(prefix + '-*.patch'))
    assert len(found) == 1, found
    return found[0].read_text()


def added(text):
    return '\n'.join(l[1:] for l in text.splitlines() if l.startswith('+') and not l.startswith('+++'))


def after(text):
    """the hunks as they read once applied: context and added lines"""
    return '\n'.join(l[1:] for l in text.splitlines()
                     if (l.startswith('+') and not l.startswith('+++')) or l.startswith(' '))


def removed(text):
    return '\n'.join(l[1:] for l in text.splitlines() if l.startswith('-') and not l.startswith('---'))


class ChargerCV(unittest.TestCase):
    def setUp(self):
        self.text = patch('0024')

    def test_kano_is_the_author(self):
        self.assertTrue(self.text.startswith('From: kanoqwq <kanoqwq@qq.com>\n'))
        self.assertIn('c031a58a', self.text)
        self.assertIn('b4748534', self.text)

    def test_the_battery_cv_bounded_by_the_chip_and_only_on_the_vendor_node(self):
        self.assertIn('BQ2560X_CHG_VBATREG_MAX_uV', removed(self.text))          # the 4.208 V snapshot cap goes
        plus = added(self.text)
        self.assertNotIn('4208000', plus)
        # the battery's declared CV, only when the device tree gave one, in the stock encoding, bounded by the chip
        self.assertIn('bq->init_data.vbatreg_max = !ret && bat_info->constant_charge_voltage_max_uv > 0 ?\n'
                      '\t\t\tbq2560x_chg_stock_cv(bat_info->constant_charge_voltage_max_uv,\n'
                      '\t\t\t\t\t     bq->chip_info->bq256xx_max_vbatreg) :', plus)
        # code 0xf read back as the chip's special 4.352 V, on the vendor node only
        self.assertIn('if (bq->bq2560x_chg && vbatreg_reg_code == BQ2560X_CHG_VBATREG_SPECIAL)', plus)
        self.assertIn('bq->chip_info->bq256xx_def_vbatreg;', plus)                 # no valid node: the chip default
        # what probe writes: the vendor node its bounded CV, every other board the chip default as before
        self.assertIn('bq->bq2560x_chg ? bq->init_data.vbatreg_max :\n\t\t\tbq->chip_info->bq256xx_def_vbatreg);',
                      plus)

    def test_the_safety_timer_off_and_termination_kept(self):
        plus = added(self.text)
        self.assertIn('#define BQ2560X_CHG_EN_TIMER\t\tBIT(3)', plus)
        # mask both bits, set only EN_TERM: the timer bit cleared, termination on, nothing else touched
        self.assertIn('BQ256XX_EN_TERM | BQ2560X_CHG_EN_TIMER,\n\t\t\t\t\t BQ256XX_EN_TERM);', after(self.text))

    def test_the_current_budget_is_not_this_patch(self):
        self.assertNotIn('ICHG', added(self.text).replace('ICHG_MAX_uA', ''))


    @unittest.skipUnless(shutil.which('cc'), 'needs a C compiler')
    def test_the_stock_encoding(self):
        # ZTE's sqc_mp_set_cv: floor((mV - 3856) / 32) + 1, at most the chip's maximum
        plus = added(self.text)
        m = re.search(r'\nstatic int bq2560x_chg_stock_cv\(.*?\n}\n', after(self.text), re.S)
        self.assertTrue(m)
        prog = (
            '#include <stdio.h>\n#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))\n'
            '#define BQ2560X_VBATREG_STEP_uV 32000\n#define BQ2560X_VBATREG_OFFSET_uV 3856000\n'
            '#define BQ2560X_VBATREG_MIN_uV 3856000\n'
            + '\n'.join(l for l in plus.splitlines() if l.startswith('#define BQ2560X_CHG_VBATREG_SPECIAL'))
            + m.group(0) +
            'int main(void) { int v[] = { 4300000, 4272000, 4304000, 4400000, 4624000, 5000000, 3000000 };'
            ' for (unsigned i = 0; i < sizeof(v) / sizeof(v[0]); i++)'
            ' printf("%d\\n", bq2560x_chg_stock_cv(v[i], 4624000)); return 0; }\n')
        with tempfile.TemporaryDirectory() as d:
            src = f'{d}/t.c'
            with open(src, 'w') as f:
                f.write(prog)
            r = subprocess.run(['cc', '-o', f'{d}/t', src], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            got = [int(x) for x in subprocess.run([f'{d}/t'], capture_output=True, text=True).stdout.split()]
        # 4.3 V: code 14, REG04 0x70, 4.304 V (stock Android reads 0x70 on the same unit); not the round-down's 0x68
        self.assertEqual(got[0], 4304000)
        self.assertEqual((got[0] - 3856000) // 32000 << 3, 0x70)
        self.assertEqual(got[1], 4304000)               # 4.272 V, on the grid: one step up all the same
        self.assertEqual(got[2], 4352000)               # code 0xf: the chip's special 4.352 V, not 4.336 V
        self.assertEqual(got[3], 4432000)
        self.assertEqual(got[4:6], [4624000, 4624000])  # the chip's maximum bounds it
        self.assertEqual(got[6], 3856000)


class Charger3A(unittest.TestCase):
    def setUp(self):
        self.text = patch('0025')
        self.plus = added(self.text)

    def test_off_by_default(self):
        self.assertIn('\nstatic bool u30air_3a;\n', self.plus)
        self.assertIn('module_param(u30air_3a, bool, 0444);', self.plus)

    def test_every_change_behind_the_switch(self):
        self.assertIn('return u30air_3a ? 3000000 : 2000000;', self.plus)          # a 3 A Rp: 2 A as before
        self.assertIn('return 2000000;', removed(self.text))
        self.assertIn('bq->bq2560x_chg && u30air_3a ? bq->init_data.ichg_max :\n'
                      '\t\t\t\tbq->chip_info->bq256xx_def_ichg);', after(self.text))       # the initial ICHG
        m = re.search(r'if \(u30air_3a\)\n(.*?)\n\t\telse\n\t\t\tbq->init_data.ichg_max = '
                      r'min_t\(u32, bq->init_data.ichg_max, BQ2560X_CHG_ICHG_MAX_uA\);', self.plus, re.S)
        self.assertTrue(m, 'the old 1.98 A cap stays the default')
        # on: the battery node's current, at most 3 A and the chip's; 1.98 A without a valid node
        self.assertIn('BQ2560X_CHG_ICHG_MAX_uA :', m.group(1))
        self.assertIn('min_t(u32, BQ2560X_CHG_ICHG_3A_uA, bq->chip_info->bq256xx_max_ichg)', m.group(1))
        self.assertIn('#define BQ2560X_CHG_ICHG_3A_uA\t\t3000000', self.plus)

    def test_nothing_else_is_raised(self):
        # the 1.98 A default is not edited; only the lines above change
        self.assertNotIn('BQ2560X_CHG_ICHG_MAX_uA\t\t', removed(self.text))


class NcmRetry(unittest.TestCase):
    def setUp(self):
        self.text = patch('0026')
        self.plus = added(self.text)

    def test_off_by_default_and_switchable(self):
        self.assertIn('\nstatic bool tx_busy_retry;\n', self.plus)
        self.assertIn('module_param(tx_busy_retry, bool, 0644);', self.plus)

    def test_retry_only_when_busy_and_asked_for_with_the_deadline_moved_first(self):
        body = self.plus[self.plus.index('/* A timer flush has no qdisc'):]
        self.assertIn('== NETDEV_TX_BUSY &&\n\t\t    READ_ONCE(tx_busy_retry)) {', body)
        self.assertLess(body.index('hrtimer_forward_now(data, TX_TIMEOUT_NSECS);'), body.index('return HRTIMER_RESTART;'))
        self.assertIn('return HRTIMER_NORESTART;', self.text)                     # every other path as before

    def test_the_source(self):
        self.assertIn('20260817192638.437798-1-cosmin-gabriel.tanislav.xa@renesas.com', self.text)


if __name__ == '__main__':
    unittest.main()
