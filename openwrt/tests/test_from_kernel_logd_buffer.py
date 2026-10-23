"""90-mu300 raises logd's buffer from a stock size (none, 64, 128 KiB) to 1 MiB and leaves a size the user set: the
vendor modules' routine lines are debug lines, and what is left of the kernel's and the system's lines fits in it."""
import unittest

from helpers import TOP, ShellTest

UCI_DEFAULTS = TOP / 'openwrt/overlay/etc/uci-defaults/90-mu300'


class LogdBuffer(ShellTest):
    def block(self):
        lines = [ln for ln in UCI_DEFAULTS.read_text().splitlines() if 'log_size' in ln and not ln.startswith('#')]
        self.assertEqual(len(lines), 1, lines)
        return lines[0]

    def test_stock_sizes_become_1024_and_a_chosen_one_stays(self):
        # uci: "get" answers $SIZE (nothing for an unset option: exit 1), "set" is recorded
        self.stub('uci', 'case "$*" in *" get "*|"get "*) [ -n "$SIZE" ] || exit 1; echo "$SIZE" ;;\n'
                         '*set*) echo "$*" >> "$STUBLOG/set" ;; esac')
        for shell in self.each_shell():
            for size, want in (('', '1024'), ('64', '1024'), ('128', '1024'), ('256', None), ('512', None),
                               ('1024', None), ('16', None)):
                (self.tmp / 'set').write_text('')
                r = self.sh(shell, self.block(), SIZE=size)
                self.assertEqual(r.returncode, 0, r.stderr)
                got = (self.tmp / 'set').read_text().strip()
                if want:
                    self.assertEqual(got, f"-q set system.@system[0].log_size={want}", size)
                else:
                    self.assertEqual(got, '', size)

    def test_set_before_the_commit_of_system(self):
        text = UCI_DEFAULTS.read_text()
        self.assertLess(text.index(self.block()), text.index('uci commit system'))


if __name__ == '__main__':
    unittest.main()
