"""A kernel or module that fails to compile fails the build.

build-modules.sh shows make's output through tee, and in a pipeline the status is the last command's: a failed module
would report success and the previous build's .ko would be collected as new. pipefail makes make's status decide;
build.sh sends make's output to a log and stops on make's status, with the compiler's messages printed."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

SCRIPTS = ['kernel/build.sh', 'kernel/build-modules.sh']


class MakeStatus(unittest.TestCase):
    def test_pipefail(self):
        for s in SCRIPTS:
            self.assertIn('\nset -eo pipefail\n', (TOP / s).read_text(), s)

    def test_no_make_without_its_status(self):
        b = (TOP / 'kernel/build.sh').read_text()
        self.assertIn('make O="$O" -j"$(nproc)" Image > "$O/build.log" 2>&1 || {', b)
        m = (TOP / 'kernel/build-modules.sh').read_text()
        self.assertIn('modules 2>&1 | tee "$OUT/modules/$m.log"', m)

    def test_failed_make_exits_nonzero(self):
        # the module loop's make line, run under the script's own settings with a make that fails
        lines = (TOP / 'kernel/build-modules.sh').read_text().splitlines()
        line = next(l for l in lines if '| tee "$OUT/modules/$m.log"' in l)
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'modules').mkdir()
            for make, want in (('echo "foo.c:1: error: x"; exit 2', 2), ('echo ok', 0)):
                fake = Path(d) / 'make'
                fake.write_text(make + '\n')
                body = line.strip().replace('make ', f'sh {fake} ', 1)
                r = subprocess.run(['bash', '-c', f'set -eo pipefail\nOUT={d}; m=x\n{body}\necho done'],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, want, r.stdout + r.stderr)
                if want:
                    self.assertIn('error: x', r.stdout)
                    self.assertNotIn('done', r.stdout)


if __name__ == '__main__':
    unittest.main()
