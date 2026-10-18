"""mu300-audio-dsp install --download: the owner's published AGDSP image, fetched and installed only if its sha256 is
exactly the pinned one. curl and sha256sum are stand-ins; nothing goes to the network."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

DSP = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-audio-dsp'
PINNED = '378ceea78dbf013e10625e71efd6fab487d727e371884ae8daf078da2828b937'
URL = 'https://github.com/dikeckaan/mu300-audio-firmware/releases/download/v1.0/agdsp.bin'


class Download(unittest.TestCase):
    def run_install(self, fake_sum=None):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            bin_ = d / 'bin'; bin_.mkdir()
            image = d / 'served.bin'
            image.write_bytes(b'SharkL5_AUDCP_2022Y_VER_2548\0' + bytes(1048576))
            (bin_ / 'curl').write_text('#!/bin/sh\nwhile [ $# -gt 1 ]; do [ "$1" = -o ] && out=$2; shift; done\n'
                                       'echo "$1" > "%s/url"\ncp "%s" "$out"\n' % (d, image))
            (bin_ / 'curl').chmod(0o755)
            if fake_sum:
                (bin_ / 'sha256sum').write_text('#!/bin/sh\necho "%s  $1"\n' % fake_sum)
                (bin_ / 'sha256sum').chmod(0o755)
            fw = d / 'fw' / 'agdsp.bin'
            env = dict(os.environ, PATH='%s:%s' % (bin_, os.environ['PATH']), MU300_AGDSP_FW=str(fw),
                       MU300_AGDSP_SUM=str(d / 'etc' / 'agdsp.sha256'))
            r = subprocess.run(['sh', str(DSP), 'install', '--download'], capture_output=True, text=True, env=env)
            url = (d / 'url').read_text().strip() if (d / 'url').exists() else None
            return r, fw.exists(), url

    def test_mismatch_refused_and_nothing_installed(self):
        r, installed, url = self.run_install()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('refused', r.stderr)
        self.assertFalse(installed)
        self.assertEqual(url, URL)

    def test_pinned_hash_installed(self):
        r, installed, url = self.run_install(fake_sum=PINNED)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(installed)
        self.assertIn('measured on an F50', r.stdout)

    def test_pin_and_url_in_the_script(self):
        src = DSP.read_text()
        self.assertIn('FW_URL=' + URL, src)
        self.assertIn('FW_URL_SHA256=' + PINNED, src)
        self.assertIn("--proto '=https'", src)


if __name__ == '__main__':
    unittest.main()
