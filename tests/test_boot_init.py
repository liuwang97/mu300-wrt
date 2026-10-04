"""boot/init: where the Linux filesystem is looked for. The card functions are cut out of init between their
markers and run against files that stand in for block devices."""
import re
import unittest

from helpers import TOP, ShellTest

INIT = (TOP / 'boot' / 'init').read_text()


def fake_ext4(path, label, magic=b'\x53\xef'):
    data = bytearray(4096)
    data[1080:1082] = magic
    data[1144:1144 + len(label)] = label.encode()
    path.write_bytes(bytes(data))


class SdRoot(ShellTest):
    def functions(self):
        m = re.search(r'# --- sd-root begin\n(.*?)# --- sd-root end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no sd-root block')
        return m.group(1)

    def run_fn(self, shell, call, glob):
        code = 'log() { :; }; mdev() { :; }\n' + self.functions() + f'\nMU300_SD_GLOB="{glob}"\n{call}\necho "rc=$?"'
        return self.sh(shell, code).stdout

    def test_label(self):
        fake_ext4(self.tmp / 'a', 'mu300sd')
        fake_ext4(self.tmp / 'b', 'mu300sd', magic=b'\x00\x00')
        for shell in self.each_shell():
            self.assertIn('mu300sd\nrc=0', self.run_fn(shell, f'ext4_label {self.tmp}/a', ''))
            self.assertEqual('rc=1', self.run_fn(shell, f'ext4_label {self.tmp}/b', '').strip())

    def test_partition_before_whole_card(self):
        fake_ext4(self.tmp / 'mmcblk1', 'mu300sd')
        fake_ext4(self.tmp / 'mmcblk1p1', 'mu300sd')
        for shell in self.each_shell():
            out = self.run_fn(shell, 'find_sd_root', f'{self.tmp}/mmcblk[1-9]p1 {self.tmp}/mmcblk[1-9]')
            self.assertIn(f'{self.tmp}/mmcblk1p1\nrc=0', out)

    def test_finds_card_under_another_number(self):
        fake_ext4(self.tmp / 'mmcblk2p1', 'mu300sd')
        for shell in self.each_shell():
            out = self.run_fn(shell, 'find_sd_root', f'{self.tmp}/mmcblk[1-9]p1 {self.tmp}/mmcblk[1-9]')
            self.assertIn('mmcblk2p1\nrc=0', out)

    def test_foreign_and_missing(self):
        fake_ext4(self.tmp / 'mmcblk1p1', 'photos')
        for shell in self.each_shell():
            self.assertEqual('rc=1', self.run_fn(shell, 'find_sd_root', f'{self.tmp}/mmcblk[1-9]p1').strip())
            self.assertEqual('rc=1', self.run_fn(shell, 'find_sd_root', f'{self.tmp}/none[1-9]').strip())

    def test_wait_gives_up(self):
        for shell in self.each_shell():
            out = self.run_fn(shell, 'sleep() { :; }; wait_sd_root 3', f'{self.tmp}/none[1-9]')
            self.assertEqual('rc=1', out.strip())


class Rules(unittest.TestCase):
    def test_timer_stays_at_300(self):
        self.assertIn('(sleep 300\n', INIT)

    def test_card_before_internal_and_wait_is_conditional(self):
        body = INIT[INIT.index('# --- sd-root end'):]
        self.assertLess(body.index('sd=$(find_sd_root)'), body.index('elif mount_internal'))
        # the wait must sit behind the two conditions, never on the plain path
        self.assertRegex(body, r'root-on-sd')
        self.assertEqual(body.count('wait_sd_root 8'), 1)

    def test_empty_card_falls_back(self):
        # a card with the label but no system inside must not end in standalone mode while an internal system exists
        self.assertIn('stage=sd-root-empty', INIT)


if __name__ == '__main__':
    unittest.main()
