"""Call audio (FINDINGS 24c): no phone number reaches a log in full, and mu300-hfp only talks to a bonded,
authenticated headset through a strict AT allow-list."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

VOICE = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-voice'
HFP = TOP / 'tools' / 'hfp' / 'mu300-hfp.c'


def shell_function(src, name):
    m = re.search(r'(?ms)^%s\(\) \{\n.*?\n\}\n' % re.escape(name), src)
    if not m:
        m = re.search(r'(?m)^%s\(\) \{.*\}$' % re.escape(name), src)
    assert m, name
    return m.group(0).rstrip('\n') + '\n'


class Masking(unittest.TestCase):
    NUMBERS = ['+905321234540', '05321234540', '5321234540', '+441632960961', '112233']

    def run_log(self, *lines):
        src = VOICE.read_text()
        script = shell_function(src, 'mask') + shell_function(src, 'log') + ''.join(
            'log %s\n' % repr(l).replace("'", "'") for l in lines)
        return subprocess.run(['sh', '-c', script], capture_output=True, text=True, check=True).stdout

    def test_numbers_masked(self):
        out = self.run_log('call state: 4 (+905321234540)',
                           '+CLCC: 1,1,4,0,0,"05321234540",129',
                           'RING +CLIP: "+441632960961",145,,,"",0',
                           'dial 5321234540 now', 'short 112233')
        for n in self.NUMBERS:
            self.assertNotIn(n, out)
        self.assertIn('+90********40', out)
        self.assertIn('"*********40"', out)
        self.assertIn('+44********61', out)
        self.assertIn('****33', out)

    def test_small_values_untouched(self):
        # card numbers, modes, rates and volumes are not phone numbers
        out = self.run_log('profiles: mode 28 (band 1) = 0x1c1c0005, VBC_VOLUME 7', 'hw:1,5 8000 Hz')
        self.assertIn('mode 28', out)
        self.assertIn('8000 Hz', out)
        self.assertIn('VBC_VOLUME 7', out)

    def test_every_number_path_masked(self):
        src = VOICE.read_text()
        self.assertIn('log() { echo "mu300-voice: $*" | mask; }', src)
        self.assertIn('echo "incoming: $line" | mask', src)
        self.assertRegex(src, r"at 'AT\+CLCC' 8 \| tr '\\n' ' ' \| mask")
        # the number only goes to the headset (ring), never to stdout unmasked
        for line in src.splitlines():
            if '$number' in line and 'log ' not in line:
                self.assertRegex(line, r'number=|hfp "ring \$number"')
        self.assertIn('umask 077', src)


class RunDirectory(unittest.TestCase):
    """/run is world-writable on the images, so /run/mu300-voice may be a directory somebody else made first."""

    def ours(self, path, uid):
        fn = shell_function(VOICE.read_text(), 'run_dir_ours')
        return subprocess.run(['sh', '-c', fn + 'run_dir_ours "$1" "$2"', 'sh', str(path), str(uid)]).returncode == 0

    def test_only_the_owners_own_directory(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            mine = d / 'mine'
            mine.mkdir(mode=0o700)
            self.assertTrue(self.ours(mine, os.getuid()))
            mine.chmod(0o710)                      # the voice group may enter
            self.assertTrue(self.ours(mine, os.getuid()))
            self.assertFalse(self.ours(mine, os.getuid() + 1))        # somebody else's
            for mode in (0o770, 0o707, 0o777, 0o1777):
                mine.chmod(mode)
                self.assertFalse(self.ours(mine, os.getuid()), oct(mode))
            mine.chmod(0o700)
            (d / 'link').symlink_to(mine)
            self.assertFalse(self.ours(d / 'link', os.getuid()))
            (d / 'file').write_text('')
            self.assertFalse(self.ours(d / 'file', os.getuid()))
            self.assertFalse(self.ours(d / 'missing', os.getuid()))

    def test_checked_before_anything_is_written_there(self):
        src = VOICE.read_text()
        check = src.index('run_dir_ours "$TMP"')
        for first_use in ('SAVED=$TMP/saved', 'chmod 700 "$TMP"', 'SCENE_PID=$TMP/scene.pid'):
            self.assertLess(check, src.index(first_use), first_use)
        # the headset's control fifo is inside it, not in /run itself
        self.assertIn('HFP_CTL=$TMP/hfp.ctl', src)


class Hfp(unittest.TestCase):
    def test_security_in_source(self):
        c = HFP.read_text()
        self.assertIn('BT_SECURITY_MEDIUM', c)
        # security is set before the connect, and checked after it together with the peer address
        sec = c.index('setsockopt(rc, SOL_BLUETOOTH, BT_SECURITY')
        self.assertLess(sec, c.index('connect(rc,'))
        self.assertIn('getpeername(rc', c)
        self.assertIn('sec.level < BT_SECURITY_MEDIUM', c)
        # only bonded devices, never a listening socket
        self.assertIn('[LinkKey]', c.replace('\\\\', ''))
        self.assertNotRegex(c, r'\blisten\(|\baccept\(')
        self.assertIn('mkfifo(fifo, 0600)', c)
        # refused lines are not logged verbatim
        self.assertIn('refused (%zu bytes)', c)
        # the answer and hang-up commands inherit none of the headset's sockets
        child = c[c.index('static void run_bg'):c.index('static void ciev')]
        self.assertLess(child.index('close(fd)'), child.index('execl('))

    @unittest.skipUnless(shutil.which('cc'), 'no C compiler')
    def test_allow_list(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / 'hfptest'
            subprocess.run(['cc', '-DMU300_HFP_TEST', '-o', str(exe), str(HFP)], check=True, capture_output=True)
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn(', 0 failed', r.stdout)


if __name__ == '__main__':
    unittest.main()
