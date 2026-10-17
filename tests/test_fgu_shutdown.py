"""The fuel gauge's shutdown callback (upstream/patches/0018, FINDINGS 105; after kanoqwq's clean-tf-7.2 2af101f8):
the function as the patch adds it, compiled against stubs of the driver's helpers. It reads the capacity under the
driver's lock, keeps it and the boot mode, stops at the first error without writing what came after, and does nothing
on a PMIC other than UMP9620 or without a battery. Needs a C compiler (skipped without one)."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[1]
PATCH = TOP / 'upstream/patches/0018-power-sc27xx-fuel-gauge-UMP9620-capacity-kept-at-power-off.patch'

STUBS = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#define clamp(v, lo, hi) ((v) < (lo) ? (lo) : (v) > (hi) ? (hi) : (v))
#define SC27XX_FGU_NORMAIL_POWERTON 5
struct mutex { int held; };
struct device { int unused; };
static const int ump9620_info = 1, sc2731_info = 2;
struct sc27xx_fgu_data { const int *var; bool bat_present; struct mutex lock; struct device *dev; };
struct platform_device { struct sc27xx_fgu_data *drvdata; };
static void *platform_get_drvdata(struct platform_device *p) { return p->drvdata; }
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = 1; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = 0; }
static int live = 76, area = 18, mode = 0, reads, warned, err_read, err_cap, err_mode;
static int sc27xx_fgu_get_capacity(struct sc27xx_fgu_data *d, int *cap)
{ assert(d->lock.held); reads++; *cap = live; return err_read; }
static int sc27xx_fgu_save_last_cap(struct sc27xx_fgu_data *d, int cap)
{ assert(d->lock.held); if (err_cap) return err_cap; area = cap; return 0; }
static int sc27xx_fgu_save_boot_mode(struct sc27xx_fgu_data *d, int m)
{ assert(d->lock.held); if (err_mode) return err_mode; mode = m; return 0; }
#define dev_warn(dev, ...) (warned++)
'''

MAIN = r'''
static void reset(void) { live = 76; area = 18; mode = 0; reads = warned = err_read = err_cap = err_mode = 0; }
int main(void)
{
	struct device dev;
	struct sc27xx_fgu_data d = { .var = &ump9620_info, .bat_present = true, .dev = &dev };
	struct platform_device p = { .drvdata = &d }, none = { .drvdata = 0 };

	reset(); sc27xx_fgu_shutdown(&p);
	assert(area == 76 && mode == 5 && reads == 1 && !warned && !d.lock.held);
	reset(); live = 104; sc27xx_fgu_shutdown(&p);          /* kept within 0-100 */
	assert(area == 100);
	reset(); err_read = -5; sc27xx_fgu_shutdown(&p);       /* nothing written after a failed read */
	assert(area == 18 && mode == 0 && warned == 1 && !d.lock.held);
	reset(); err_cap = -5; sc27xx_fgu_shutdown(&p);        /* nor after a failed capacity write */
	assert(area == 18 && mode == 0 && warned == 1 && !d.lock.held);
	reset(); err_mode = -5; sc27xx_fgu_shutdown(&p);
	assert(area == 76 && warned == 1 && !d.lock.held);
	reset(); d.bat_present = false; sc27xx_fgu_shutdown(&p); /* the F50 and the V50: no battery */
	assert(!reads && area == 18 && mode == 0);
	reset(); d.bat_present = true; d.var = &sc2731_info; sc27xx_fgu_shutdown(&p);
	assert(!reads && area == 18);
	reset(); sc27xx_fgu_shutdown(&none);                   /* a probe that never finished */
	assert(!reads);
	puts("ok");
	return 0;
}
'''


def added_function(name):
    added = '\n'.join(l[1:] for l in PATCH.read_text().splitlines() if l.startswith('+') and not l.startswith('+++'))
    start = added.index(f'static void {name}(')
    i = added.index('{', start) + 1
    depth = 1
    while depth:
        depth += {'{': 1, '}': -1}.get(added[i], 0)
        i += 1
    return added[start:i]


@unittest.skipUnless(shutil.which('cc'), 'needs a C compiler')
class FguShutdown(unittest.TestCase):
    def test_capacity_kept_at_power_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 't.c'
            src.write_text(STUBS + added_function('sc27xx_fgu_shutdown') + MAIN)
            exe = Path(tmp) / 't'
            r = subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-variable', str(src), '-o', str(exe)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'ok'), r.stderr)


if __name__ == '__main__':
    unittest.main()
