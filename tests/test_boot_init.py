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

    # The wait beyond the first seconds: a fake /sys with the hosts and card devices of a scenario; sleep counts.
    def fake_sys(self, hosts=(), card=None):
        s = self.tmp / 'sys'
        for h in hosts:
            (s / 'class' / 'mmc_host' / h).mkdir(parents=True, exist_ok=True)
        (s / 'bus' / 'mmc' / 'devices').mkdir(parents=True, exist_ok=True)
        if card:  # card = (type, bound)
            c = s / 'bus' / 'mmc' / 'devices' / 'mmc1:aaaa'
            c.mkdir(parents=True, exist_ok=True)
            (c / 'type').write_text(card[0] + '\n')
            if card[1]:
                (c / 'driver').mkdir(exist_ok=True)
        return s

    def waited(self, shell, kernel, sys, extra=''):
        call = (f'n_sleep=0; sleep() {{ n_sleep=$((n_sleep + 1)); {extra} }}; uname() {{ echo {kernel}; }}; '
                f'MU300_SYS={sys}; wait_sd_root 3 6; r=$?; echo "slept=$n_sleep"; (exit $r)')
        return self.run_fn(shell, call, f'{self.tmp}/mmcblk[1-9]p1')

    def test_mainline_empty_slot_keeps_the_short_wait(self):
        # 6.18/7.2 poll the slot and find a card in about 2.5 s: a host without a card is no reason to wait longer
        sys = self.fake_sys(hosts=('mmc0', 'mmc1'))
        for shell in self.each_shell():
            self.assertEqual('slept=3\nrc=1', self.waited(shell, '6.18.55', sys).strip())

    def test_card_found_but_not_set_up_yet_extends_the_wait(self):
        sys = self.fake_sys(hosts=('mmc0', 'mmc1'), card=('SD', False))
        for shell in self.each_shell():
            self.assertEqual('slept=6\nrc=1', self.waited(shell, '6.18.55', sys).strip())

    def test_card_that_is_set_up_and_foreign_does_not_extend(self):
        sys = self.fake_sys(hosts=('mmc0', 'mmc1'), card=('SD', True))
        for shell in self.each_shell():
            self.assertEqual('slept=3\nrc=1', self.waited(shell, '5.4.254-gb50db5b6224c', sys).strip())

    def test_vendor_kernel_with_a_card_host_extends_the_wait(self):
        # 5.4 has a card-detect line and no poll, and the card was once seen only at 23.75 s (FINDINGS 31j)
        sys = self.fake_sys(hosts=('mmc0', 'mmc1', 'mmc2'))
        for shell in self.each_shell():
            self.assertEqual('slept=6\nrc=1', self.waited(shell, '5.4.254-gb50db5b6224c', sys).strip())

    def test_vendor_kernel_without_a_card_host_keeps_the_short_wait(self):
        # the U30 Air: the eMMC is its only host
        sys = self.fake_sys(hosts=('mmc0',))
        for shell in self.each_shell():
            self.assertEqual('slept=3\nrc=1', self.waited(shell, '5.4.254-gb50db5b6224c', sys).strip())

    def test_late_card_found_during_the_long_wait(self):
        sys = self.fake_sys(hosts=('mmc0', 'mmc1', 'mmc2'))
        src = self.tmp / 'card'
        fake_ext4(src, 'mu300sd')
        appear = f'[ $n_sleep = 5 ] && cp {src} {self.tmp}/mmcblk1p1;'
        for shell in self.each_shell():
            (self.tmp / 'mmcblk1p1').unlink(missing_ok=True)
            out = self.waited(shell, '5.4.254-gb50db5b6224c', sys, appear)
            self.assertEqual(f'{self.tmp}/mmcblk1p1\nslept=5\nrc=0', out.strip())


# Stand-ins for what root selection touches on the device. One card (mmcblk1p1) and one internal region; the
# scenario says which of them are there, when the card shows up and what is on it. mount and umount keep track
# of what is on /disk in $ON, and pick_root answers from that.
STUBS = r'''
log() { echo "$*" >> "$T/log"; }
find_sd_root() { [ "$CARD_NOW" = 1 ] && { echo /dev/mmcblk1p1; return 0; }; return 1; }
wait_sd_root() { echo "wait $*" >> "$T/log"; [ "$CARD_LATE" = 1 ] && { echo /dev/mmcblk1p1; return 0; }; return 1; }
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
            self.assertIn('wait 8 30\ndetach /dev/loop0\nstage=sd-root dev=/dev/mmcblk1p1', log)

    def test_marker_and_no_card(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, marker=True, INTERNAL=1)
            self.assertEqual('mounted=1 on=internal disk-dev=/dev/loop0', out)
            self.assertEqual('mmcblk0@4096', dev)
            self.assertIn('wait 8 30\nstage=sd-root-missing', log)

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
            self.assertIn('stage=sd-wait (no internal system)\nwait 8 30', log)

    def test_no_internal_and_late_card(self):
        for shell in self.each_shell():
            out, dev, log = self.select(shell, INTERNAL=0, CARD_LATE=1)
            self.assertEqual('mounted=1 on=card disk-dev=/dev/mmcblk1p1', out)
            self.assertEqual('/dev/mmcblk1p1', dev)
            self.assertIn('wait 8 30', log)


class UsbId(ShellTest):
    """The gadget's MACs and USB serial: the device's serial number and its eMMC's, so that two devices restored from
    one backup (the same androidboot.serialno) still differ on one computer."""
    def functions(self):
        m = re.search(r'# --- usb-id begin\n(.*?)# --- usb-id end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no usb-id block')
        return m.group(1)

    def ident(self, shell, bootargs, cids=(), net='192.168.77'):
        b = self.tmp / 'bootargs'
        b.write_bytes(bootargs.encode() + b'\0')
        s = self.tmp / 'sys'
        (s / 'bus' / 'mmc' / 'devices').mkdir(parents=True, exist_ok=True)
        for i, (kind, cid) in enumerate(cids):
            d = s / 'bus' / 'mmc' / 'devices' / f'mmc{i}:000{i}'
            d.mkdir(exist_ok=True)
            (d / 'type').write_text(kind + '\n')
            (d / 'cid').write_text(cid + '\n')
        code = f'NET={net}; MU300_SYS={s}\n' + self.functions() + f'\nusb_id {self.tmp}/none {b}\necho "$USBID $MAC"'
        return self.sh(shell, code).stdout.strip()

    @staticmethod
    def mac(text, last='77'):
        import hashlib
        h = hashlib.md5((text + '\n').encode()).hexdigest()
        return f'02:50:{h[0:2]}:{h[2:4]}:{last}'

    def test_serial_and_emmc(self):
        for shell in self.each_shell():
            out = self.ident(shell, 'console=ttyS1 androidboot.serialno=324950664950 androidboot.emmcid=2128e853 x=1')
            self.assertEqual(out, '324950664950-2128e853 ' + self.mac('324950664950-2128e853'))

    def test_clones_differ(self):
        for shell in self.each_shell():
            a = self.ident(shell, 'androidboot.serialno=324950664950 androidboot.emmcid=2128e853')
            b = self.ident(shell, 'androidboot.serialno=324950664950 androidboot.emmcid=7f0011aa')
            self.assertNotEqual(a.split()[1], b.split()[1])

    def test_no_emmcid_is_the_serial_alone(self):
        # not the eMMC's CID from /sys: init runs before the modules that find the eMMC (and on mainline it may or
        # may not be there yet), so the identity would change from boot to boot
        cids = (('MMC', 'ea010e325931384347102128e8539c00'),)
        for shell in self.each_shell():
            out = self.ident(shell, 'androidboot.serialno=324950664950', cids)
            self.assertEqual(out, '324950664950 ' + self.mac('324950664950'))

    def test_emmcid_without_serial(self):
        for shell in self.each_shell():
            out = self.ident(shell, 'androidboot.emmcid=2128e853')
            self.assertEqual(out, '2128e853 ' + self.mac('2128e853'))

    def test_u30air_subnet_byte(self):
        for shell in self.each_shell():
            out = self.ident(shell, 'androidboot.serialno=323960377386 androidboot.emmcid=203edc81', net='192.168.78')
            self.assertEqual(out.split()[1], self.mac('323960377386-203edc81', '78'))

    def test_nothing_known_is_the_old_identity(self):
        for shell in self.each_shell():
            self.assertEqual(self.ident(shell, 'console=ttyS1'), self.mac(''))
            self.assertEqual(self.ident(shell, 'androidboot.serialno=1234'), '1234 ' + self.mac('1234'))

    def test_serial_string_uses_it(self):
        self.assertIn('echo "MU300LINUX${USBID:+-$USBID}" > "$g/strings/0x409/serialnumber"', INIT)
        self.assertIn('usb_id /proc/cmdline /proc/device-tree/chosen/bootargs', INIT)


class Rules(unittest.TestCase):
    def test_timer_stays_at_300(self):
        self.assertIn('(sleep 300\n', INIT)

    def test_wait_is_written_once(self):
        # the 8 seconds are one constant, behind the two conditions the RootSelect tests exercise
        body = INIT[INIT.index('# --- sd-root end'):]
        self.assertEqual(body.count('wait_sd_root 8 30'), 1)


if __name__ == '__main__':
    unittest.main()
