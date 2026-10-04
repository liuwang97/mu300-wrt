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


# Stand-ins for what root selection touches on the device. One card (mmcblk1p1) and one internal region; the
# scenario says which of them are there, when the card shows up and what is on it. mount and umount keep track
# of what is on /disk in $ON, and pick_root answers from that.
STUBS = r'''
log() { echo "$*" >> "$T/log"; }
find_sd_root() { [ "$CARD_NOW" = 1 ] && { echo /dev/mmcblk1p1; return 0; }; return 1; }
wait_sd_root() { echo "wait $1" >> "$T/log"; [ "$CARD_LATE" = 1 ] && { echo /dev/mmcblk1p1; return 0; }; return 1; }
find_root_offset() { [ "$INTERNAL" = 1 ] && { echo 4096; return 0; }; return 1; }
losetup() { case $1 in -f) echo /dev/loop0 ;; -d) echo "detach $2" >> "$T/log" ;; esac; }
cat() { case $1 in /sys/*) echo "$ROOT_OFFSET" ;; *) command cat "$@" ;; esac; }
dd() { [ "$INTERNAL" = 1 ] && printf '\123\357'; }
mount() {
    case $5 in
        /dev/loop*) [ "$INTERNAL" = 1 ] || return 1; ON=internal ;;
        *) [ "$CARD_MOUNTS" = 0 ] && return 1; ON=card ;;
    esac
}
umount() { ON=; }
pick_root() { case $ON in internal) echo /disk/ubuntu ;; card) [ "$CARD_EMPTY" = 1 ] || echo /disk/openwrt ;; esac; }
ON=
ROOT_OFFSET=27762098176
'''


class RootSelect(ShellTest):
    """Which filesystem ends up on /disk: the root-select block of init run against stubs."""

    def region(self):
        m = re.search(r'# --- root-select begin\n(.*?)# --- root-select end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no root-select block')
        code = m.group(1)
        # the device paths are only redirected here, never changed in init
        for path, repl in (('/run/mu300-root-dev', '$T/root-dev'), ('/run/rootmount.err', '$T/err'),
                           ('/disk/.mu300/root-on-sd', '$T/root-on-sd')):
            self.assertIn(path, code)
            code = code.replace(path, repl)
        return code

    def select(self, shell, marker=False, **scenario):
        for f in ('log', 'root-dev', 'root-on-sd'):
            (self.tmp / f).unlink(missing_ok=True)
        (self.tmp / 'log').touch()
        if marker:
            (self.tmp / 'root-on-sd').touch()
        code = STUBS + self.region() + '\necho "mounted=$root_mounted on=$ON disk-dev=$diskdev"'
        r = self.sh(shell, code, T=self.tmp, **scenario)
        self.assertEqual('', r.stderr)
        dev = self.tmp / 'root-dev'
        return r.stdout.strip(), dev.read_text().strip() if dev.exists() else '', (self.tmp / 'log').read_text()

    def test_card_only(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, CARD_NOW=1, INTERNAL=0)
            self.assertEqual('mounted=1 on=card disk-dev=/dev/mmcblk1p1', out)
            self.assertEqual('/dev/mmcblk1p1', dev)
            self.assertIn('stage=sd-root dev=/dev/mmcblk1p1', log)
            self.assertNotIn('wait', log)

    def test_card_wins_over_internal(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, CARD_NOW=1, INTERNAL=1)
            self.assertEqual('mounted=1 on=card disk-dev=/dev/mmcblk1p1', out)
            self.assertEqual('/dev/mmcblk1p1', dev)
            self.assertNotIn('root-offset', log)

    def test_internal_without_marker_never_waits(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, INTERNAL=1)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertNotIn('wait', log)

    def test_marker_and_late_card(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, marker=True, INTERNAL=1, CARD_LATE=1)
            self.assertEqual('mounted=1 on=card disk-dev=/dev/mmcblk1p1', out)
            self.assertEqual('/dev/mmcblk1p1', dev)
            self.assertIn('wait 8\ndetach /dev/loop0\nstage=sd-root dev=/dev/mmcblk1p1', log)

    def test_marker_and_no_card(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, marker=True, INTERNAL=1)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertIn('wait 8\nstage=sd-root-missing', log)

    def test_marker_and_late_card_that_is_empty(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, marker=True, INTERNAL=1, CARD_LATE=1, CARD_EMPTY=1)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertIn('stage=sd-root-empty dev=/dev/mmcblk1p1\nstage=sd-root-failed', log)

    def test_empty_card_falls_back_to_internal(self):
        # a card with the label but no system inside must not end in standalone mode while an internal system exists
        for shell in self.each_shell():
            out, dev, log = self.select(shell, CARD_NOW=1, CARD_EMPTY=1, INTERNAL=1)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertIn('stage=sd-root-empty dev=/dev/mmcblk1p1', log)
            self.assertNotIn('wait', log)

    def test_card_that_does_not_mount_falls_back_to_internal(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, CARD_NOW=1, CARD_MOUNTS=0, INTERNAL=1)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertNotIn('wait', log)

    def test_nothing_at_all_waits_then_standalone(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, INTERNAL=0)
            self.assertEqual('mounted=0 on= disk-dev=', out)
            self.assertEqual('', dev)
            self.assertIn('stage=sd-wait (no internal system)\nwait 8', log)

    def test_no_internal_and_late_card(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, INTERNAL=0, CARD_LATE=1)
            self.assertEqual('mounted=1 on=card disk-dev=/dev/mmcblk1p1', out)
            self.assertEqual('/dev/mmcblk1p1', dev)
            self.assertIn('wait 8', log)


class Rules(unittest.TestCase):
    def test_timer_stays_at_300(self):
        self.assertIn('(sleep 300\n', INIT)

    def test_wait_is_written_once(self):
        # the 8 seconds are one constant, behind the two conditions the RootSelect tests exercise
        body = INIT[INIT.index('# --- sd-root end'):]
        self.assertEqual(body.count('wait_sd_root 8'), 1)


if __name__ == '__main__':
    unittest.main()
