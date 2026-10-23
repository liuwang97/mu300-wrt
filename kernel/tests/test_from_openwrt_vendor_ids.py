"""The initramfs' ueventd-perms.sh and its generator (moved here from the root filesystem tree's test_vendor_ids: they
read this tree only): the committed script is gen-ueventd-perms.py's output, and the generator's mapping of Android's
accounts and modes - the modem's nodes to the vendor account, nothing writable by everyone, root's alone where Android
gave a node to a login-facing group."""
import re
import subprocess
import sys

from helpers import TOP, ShellTest

GEN = TOP / 'kernel' / 'initramfs' / 'gen-ueventd-perms.py'
INPUTS = [TOP / 'kernel' / 'initramfs' / 'ueventd' / 'ueventd.rc', TOP / 'kernel' / 'initramfs' / 'ueventd' / 'init-perms.rc']
OUTPUT = TOP / 'kernel' / 'initramfs' / 'ueventd-perms.sh'
LINE = re.compile(r'for n in (.+?); do \[ -e "\$n" \] && (?:chown (\S+):(\S+) "\$n"(?: && chmod ([0-7]+) "\$n")?'
                  r'|chmod ([0-7]+) "\$n"); done')


def generate(*files):
    return subprocess.run([sys.executable, str(GEN)] + [str(f) for f in files],
                          capture_output=True, text=True, check=True).stdout


def rules(text):
    """[(path, owner, group, mode), ...] of every node line (owner/group None for a chmod alone)"""
    out = []
    for line in text.splitlines():
        m = LINE.fullmatch(line)
        if m:
            out.append((m.group(1), m.group(2), m.group(3), m.group(4) or m.group(5)))
    return out


class Generator(ShellTest):
    def test_the_committed_script_is_the_generators(self):
        self.assertEqual(OUTPUT.read_text(), generate(*INPUTS), f'{OUTPUT}: run gen-ueventd-perms.py again')

    def test_mapping(self):
        rc = self.tmp / 'ueventd.rc'
        rc.write_text('/dev/modem 0660 system radio\n/dev/spipe_pm* 0666 system system\n'
                      '/dev/mali0 0666 system graphics\n/dev/gsp 0660 system graphics\n/dev/audio_dsp* 666 audioserver audio\n'
                      '/dev/stty_nr* 0660 radio system\n/dev/block/by-name/miscdata 0660 root system\n'
                      '/dev/ttyM0 0660 bluetooth net_bt_admin\n/dev/input/event* 0660 system input\n'
                      '/dev/x 0646 system system   # a comment\n/dev/unknown 0660 nosuch system\n')
        init = self.tmp / 'init.rc'
        init.write_text('    chown system system /sys/power/a\n    chmod 0666 /sys/class/b\n'
                        '    chmod 0646 /dev/c\n    chown graphics system /sys/d\n    chown system /sys/e\n'
                        '    chown system system /sys/module/firmware_class/parameters/path\n'
                        '    chmod 660 /sys/module/firmware_class/parameters/path\n    write /dev/f 1\n')
        r = {p: (o, g, m) for p, o, g, m in rules(generate(rc, init)) if o is not None}
        self.assertEqual(r['/dev/modem'], ('$V', '$VG', '0660'))
        self.assertEqual(r['/dev/spipe_pm*'], ('$V', '$VG', '0660'))
        self.assertEqual(r['/dev/mali0'], ('0', '$GPU', '0660'))
        self.assertEqual(r['/dev/gsp'], ('$V', '0', '0660'))     # only everyone's GPU nodes go to video
        self.assertEqual(r['/dev/audio_dsp*'], ('0', '0', '0600'))       # root's alone: no group bits
        self.assertEqual(r['/dev/stty_nr*'], ('$V', '$VG', '0660'))
        self.assertEqual(r['/dev/block/by-name/miscdata'], ('0', '$VG', '0660'))
        self.assertEqual(r['/dev/ttyM0'], ('0', '0', '0600'))
        self.assertEqual(r['/dev/x'], ('$V', '$VG', '0640'))
        self.assertEqual(r['/dev/input/event*'], ('0', '0', '0600'))
        self.assertNotIn('/dev/unknown', r)
        out = generate(rc, init)
        self.assertIn('chown $V:$VG "$n"; done', out.splitlines()[[i for i, x in enumerate(out.splitlines())
                                                                   if '/sys/power/a' in x][0]])
        c = {p: m for p, o, g, m in rules(out) if o is None}
        self.assertEqual(c['/sys/class/b'], '0664')     # readable stays readable, written by nobody else
        self.assertEqual(c['/dev/c'], '0640')
        r2 = {p: (o, g) for p, o, g, m in rules(out) if o is not None}
        self.assertEqual(r2['/sys/d'], ('0', '$VG'))
        self.assertEqual(r2['/sys/e'], ('$V', '$VG'))
        self.assertIn('for n in /sys/module/firmware_class/parameters/path; do [ -e "$n" ] && chown 0:0 "$n" && chmod 0600', out)
        self.assertEqual(out.count('firmware_class'), 1)     # its chmod 660 is not taken over
        self.assertNotIn('/dev/f', out)
