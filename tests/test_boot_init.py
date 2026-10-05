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


class PickRoot(ShellTest):
    def functions(self):
        m = re.search(r'# --- pick-root begin\n(.*?)# --- pick-root end', INIT, re.S)
        self.assertIsNotNone(m, 'boot/init has no pick-root block')
        return m.group(1).replace('/disk', str(self.tmp / 'disk'))

    def system(self, name, init='sbin/init'):
        p = self.tmp / 'disk' / name / init
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('#!/bin/sh\n'); p.chmod(0o755)

    def pick(self, shell):
        return self.sh(shell, self.functions() + '\npick_root').stdout.strip()

    def test_only_the_third_system(self):
        self.system('openwrt-luci')
        for shell in self.each_shell():
            self.assertEqual(self.pick(shell), f'{self.tmp}/disk/openwrt-luci')

    def test_boot_os_chooses_it(self):
        self.system('ubuntu', 'lib/systemd/systemd'); self.system('openwrt'); self.system('openwrt-luci')
        (self.tmp / 'disk/.mu300').mkdir(); (self.tmp / 'disk/.mu300/boot-os').write_text('openwrt-luci\n')
        for shell in self.each_shell():
            self.assertEqual(self.pick(shell), f'{self.tmp}/disk/openwrt-luci')

    def test_never_boots_a_kept_copy(self):
        self.system('openwrt-luci.old')
        (self.tmp / 'disk/.mu300').mkdir(); (self.tmp / 'disk/.mu300/boot-os').write_text('openwrt-luci.old\n')
        for shell in self.each_shell():
            self.assertEqual(self.pick(shell), '')


class Rules(unittest.TestCase):
    def test_timer_stays_at_300(self):
        self.assertIn('(sleep 300\n', INIT)

    def test_wait_is_written_once(self):
        # the 8 seconds are one constant, behind the two conditions the RootSelect tests exercise
        body = INIT[INIT.index('# --- sd-root end'):]
        self.assertEqual(body.count('wait_sd_root 8'), 1)


class GadgetHarness(ShellTest):
    """setup_usb_gadget, run against a directory that stands in for /config."""

    @staticmethod
    def block(name):
        m = re.search(rf'# --- {name} begin\n(.*?)# --- {name} end', INIT, re.S)
        assert m, f'boot/init has no {name} block'
        return m.group(1)

    def build(self, shell, usbnet, then='', no_rndis=False):
        """setup_usb_gadget with MU300_USBNET=USBNET (None: unset), then the shell code THEN; NO_RNDIS: a kernel
        without f_rndis, whose configfs refuses to make rndis.rn0."""
        body = self.block('usb-gadget') + self.block('usb-net')
        for path, repl in (('/config/usb_gadget', f'{self.tmp}/cfg/usb_gadget'), ('/sys/class/udc', f'{self.tmp}/udc'),
                           ('/sys/class/net/', f'{self.tmp}/net/'), ('/run/', f'{self.tmp}/run/')):
            body = body.replace(path, repl)
        (self.tmp / 'run').mkdir(exist_ok=True)
        g = self.tmp / 'cfg' / 'usb_gadget' / 'linux'
        code = ('log() { echo "$*" >> "$T/log"; }; mount() { :; }; sleep() { :; }; persist() { :; }\n'
                # the early DHCP server: recorded, never run
                'udhcpd() { echo "udhcpd $*" >> "$T/log"; }; kill() { echo "kill $*" >> "$T/log"; }\n'
                'killall() { echo "killall $*" >> "$T/log"; }\n'
                # configfs makes these itself when their parent is made, and takes them away with it
                'mkdir() { for d; do case $d in */functions/rndis.rn0) [ -z "$NO_RNDIS" ] || return 1 ;; esac; done\n'
                '    command mkdir "$@" || return; for d; do case $d in */functions/rndis.rn0) command mkdir -p "$d/os_desc/interface.rndis" ;; esac; done; }\n'
                'rmdir() { for d; do case $d in */functions/*) command rm -rf "$d" ;; *) command rmdir "$d" ;; esac; done; }\n'
                'mkdir -p "%s/cfg/usb_gadget/linux/os_desc"\n' % self.tmp +
                'ifconfig() { echo "ifconfig $*" >> "$T/log"; }; ip() { echo "ip $*" >> "$T/log"; }\n'
                f'MAC=02:00:00:00:00 NET=192.168.77 T={self.tmp} NO_RNDIS={"1" if no_rndis else ""}\n' + (f'MU300_USBNET="{usbnet}"\n' if usbnet is not None else '')
                + body + '\nsetup_usb_gadget\n' + then)
        r = self.sh(shell, code)
        self.assertEqual(0, r.returncode, r.stderr)
        return g

    def configs(self, g):
        return sorted(p.name for p in (g / 'configs').iterdir())

    def links(self, g, c):
        return {p.name: p.resolve().name for p in (g / 'configs' / c).iterdir() if p.is_symlink()}


class UsbGadget(GadgetHarness):
    def test_rndis_one_configuration_with_console(self):
        for shell in self.each_shell():
            g = self.build(shell, 'rndis')
            self.assertEqual(['c.1'], self.configs(g))
            self.assertEqual({'f1': 'rndis.rn0', 'f2': 'GS0'}, {k: v.replace('acm.', '') for k, v in self.links(g, 'c.1').items()})
            self.assertEqual('0x0302', (g / 'bcdDevice').read_text().strip())
            self.assertEqual('1', (g / 'os_desc' / 'use').read_text().strip())
            self.assertEqual('c.1', (g / 'os_desc' / 'c.1').resolve().name)
            self.assertFalse((g / 'functions' / 'ncm.usb0').exists())
            self.assertIn('RNDIS', (g / 'configs' / 'c.1' / 'strings' / '0x409' / 'configuration').read_text())
            self.assertIn('ifconfig rndis0 up', (self.tmp / 'log').read_text())
            self.tearDown(); self.setUp()

    def test_rndis_wins_whatever_the_order(self):
        for shell in self.each_shell():
            for usbnet in ('rndis ncm', 'ncm rndis'):
                g = self.build(shell, usbnet)
                self.assertEqual(['c.1'], self.configs(g), usbnet)
                self.assertEqual(['acm.GS0', 'rndis.rn0'], sorted(p.name for p in (g / 'functions').iterdir()), usbnet)
                self.assertEqual('0x0302', (g / 'bcdDevice').read_text().strip())
                self.tearDown(); self.setUp()

    def test_ncm_ecm_unchanged(self):
        for shell in self.each_shell():
            for usbnet in ('ncm ecm', None):
                g = self.build(shell, usbnet)
                self.assertEqual(['c.1'], self.configs(g))
                self.assertEqual({'f1': 'ncm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
                self.assertEqual('0x0301', (g / 'bcdDevice').read_text().strip())
                self.assertFalse((g / 'os_desc' / 'use').exists())
                self.assertFalse((g / 'functions' / 'rndis.rn0').exists())
                self.tearDown(); self.setUp()


class UsbNetPolicy(ShellTest):
    """usb_net_policy ROOTDIR: the gadget functions etc/mu300/usb-net of the chosen system asks for (D12, K7)."""

    def policy(self, shell, content, usbnet=None):
        f = self.tmp / 'root' / 'etc' / 'mu300' / 'usb-net'
        f.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            f.unlink(missing_ok=True)
        else:
            f.write_text(content)
        env = {} if usbnet is None else {'MU300_USBNET': usbnet}
        r = self.sh(shell, GadgetHarness.block('usb-net') + f'\nusb_net_policy "{self.tmp}/root"; echo "rc=$?"', **env)
        self.assertEqual('', r.stderr)
        return r.stdout

    def test_each_value(self):
        for shell in self.each_shell():
            for value, functions in (('ncm', 'ncm ecm'), ('ecm', 'ecm ncm'), ('rndis', 'rndis ncm ecm'),
                                     ('ecm\n', 'ecm ncm')):
                self.assertEqual(f'{functions}\nrc=0\n', self.policy(shell, value), value)

    def test_garbage_and_missing_say_nothing(self):
        for shell in self.each_shell():
            for value in ('', 'RNDIS', 'ncm ecm', 'mode=ecm', 'ecm; reboot', '../x', None):
                self.assertEqual('rc=0\n', self.policy(shell, value), value)

    def test_command_line_wins(self):
        for shell in self.each_shell():
            for value in ('ncm', 'ecm', 'rndis'):
                self.assertEqual('rc=0\n', self.policy(shell, value, usbnet='ncm ecm'), value)
                self.assertEqual('rc=0\n', self.policy(shell, value, usbnet='rndis'), value)


class UsbNetRebind(GadgetHarness):
    """apply_usb_net ROOTDIR after pick_root: the gadget built at boot, rebuilt once when the system asks for other
    functions (D12, K7), and the applied marker (K8)."""

    def apply(self, shell, value, usbnet=None, no_rndis=False):
        (self.tmp / 'udc' / '25100000.dwc3').mkdir(parents=True, exist_ok=True)
        f = self.tmp / 'root' / 'etc' / 'mu300' / 'usb-net'
        f.parent.mkdir(parents=True, exist_ok=True)
        if value is not None:
            f.write_text(value + '\n')
        return self.build(shell, usbnet, f'apply_usb_net "{self.tmp}/root"', no_rndis=no_rndis)

    def binds(self):
        return (self.tmp / 'log').read_text().count('stage=usb-bind-start')

    def marker(self):
        m = self.tmp / 'run' / 'mu300' / 'usb-net-applied'
        return m.read_text().strip() if m.exists() else None

    def test_rndis_rebuilds_the_single_configuration_gadget(self):
        for shell in self.each_shell():
            g = self.apply(shell, 'rndis')
            self.assertEqual(2, self.binds())
            self.assertEqual(['c.1'], self.configs(g))
            self.assertEqual({'f1': 'rndis.rn0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
            self.assertEqual(['acm.GS0', 'rndis.rn0'], sorted(p.name for p in (g / 'functions').iterdir()))
            self.assertEqual('0x0302', (g / 'bcdDevice').read_text().strip())
            self.assertEqual('1', (g / 'os_desc' / 'use').read_text().strip())
            self.assertEqual('25100000.dwc3', (g / 'UDC').read_text().strip())
            self.assertIn('ifconfig rndis0 up', (self.tmp / 'log').read_text())
            self.assertEqual('rndis', self.marker())
            self.tearDown(); self.setUp()

    def test_rndis_on_a_kernel_without_it_falls_back_to_ncm(self):
        # the panel saved rndis, permanently: a kernel without f_rndis must still get a network function, every boot
        for shell in self.each_shell():
            g = self.apply(shell, 'rndis', no_rndis=True)
            self.assertEqual({'f1': 'ncm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
            self.assertEqual(['acm.GS0', 'ncm.usb0'], sorted(p.name for p in (g / 'functions').iterdir()))
            self.assertEqual('0x0301', (g / 'bcdDevice').read_text().strip())
            self.assertFalse((g / 'os_desc' / 'use').exists())
            self.assertEqual('25100000.dwc3', (g / 'UDC').read_text().strip())
            self.assertNotIn('usb-no-net-function', (self.tmp / 'log').read_text())
            self.assertIn('ifconfig usb0', (self.tmp / 'log').read_text())
            self.assertEqual('ncm', self.marker())   # what was made, not what was asked for
            self.tearDown(); self.setUp()

    def test_ecm_rebuilds_with_ecm(self):
        for shell in self.each_shell():
            g = self.apply(shell, 'ecm')
            self.assertEqual(2, self.binds())
            self.assertEqual({'f1': 'ecm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
            self.assertEqual(['acm.GS0', 'ecm.usb0'], sorted(p.name for p in (g / 'functions').iterdir()))
            self.assertEqual('0x0301', (g / 'bcdDevice').read_text().strip())
            self.assertIn('stage=usb-net-policy ecm', (self.tmp / 'log').read_text())
            self.assertEqual('ecm', self.marker())
            self.tearDown(); self.setUp()

    def test_ncm_is_what_was_bound_no_rebind(self):
        for shell in self.each_shell():
            g = self.apply(shell, 'ncm')
            self.assertEqual(1, self.binds())
            self.assertEqual({'f1': 'ncm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
            # the setting is in effect: a one-shot choice is consumed all the same
            self.assertEqual('ncm', self.marker())
            self.tearDown(); self.setUp()

    def test_garbage_or_no_file_leaves_the_gadget(self):
        for shell in self.each_shell():
            for value in ('bogus', None):
                g = self.apply(shell, value)
                self.assertEqual(1, self.binds(), value)
                self.assertEqual({'f1': 'ncm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
                self.assertIsNone(self.marker())
                self.tearDown(); self.setUp()

    def test_no_controller_no_second_wait(self):
        for shell in self.each_shell():
            (self.tmp / 'root' / 'etc' / 'mu300').mkdir(parents=True)
            (self.tmp / 'root' / 'etc' / 'mu300' / 'usb-net').write_text('ecm\n')
            g = self.build(shell, None, f'apply_usb_net "{self.tmp}/root"')
            log = (self.tmp / 'log').read_text()
            self.assertEqual(1, log.count('stage=usb-wait-udc'))
            self.assertIn('skipped (no udc)', log)
            self.assertEqual({'f1': 'ncm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
            self.assertIsNone(self.marker())
            self.tearDown(); self.setUp()

    def test_command_line_wins(self):
        for shell in self.each_shell():
            g = self.apply(shell, 'rndis', usbnet='ncm ecm')
            self.assertEqual(1, self.binds())
            self.assertEqual({'f1': 'ncm.usb0', 'f2': 'acm.GS0'}, self.links(g, 'c.1'))
            self.assertIsNone(self.marker())
            self.tearDown(); self.setUp()


class EarlyDhcp(GadgetHarness):
    """K5: the host gets a lease the moment the gadget is bound, from a server on the gadget's netdev, on init's own
    subnet ($NET) and for 120 s only - the system may use another subnet, and its server takes over at the renewal."""

    def netdevs(self, *names):
        for n in names:
            (self.tmp / 'net' / n).mkdir(parents=True, exist_ok=True)

    def conf(self):
        return (self.tmp / 'run' / 'udhcpd-usb0.conf').read_text()

    def starts(self):
        return [l for l in (self.tmp / 'log').read_text().splitlines() if l.startswith('udhcpd ')]

    def test_ncm_serves_usb0(self):
        for shell in self.each_shell():
            self.netdevs('usb0')
            self.build(shell, None)
            conf = self.conf()
            for line in ('start 192.168.77.200', 'end 192.168.77.200', 'interface usb0', 'option router 192.168.77.1',
                         'option subnet 255.255.255.0', 'option lease 120'):
                self.assertIn(line + '\n', conf)
            self.assertNotIn('3600', conf)
            self.assertIn('ifconfig usb0 192.168.77.1 netmask 255.255.255.0 up', (self.tmp / 'log').read_text())
            self.assertEqual(['udhcpd -f ' + str(self.tmp / 'run' / 'udhcpd-usb0.conf')], self.starts())
            self.assertEqual('02:00:00:00:00:02', (self.tmp / 'run' / 'mu300-usb-host-mac').read_text().strip())
            self.assertTrue((self.tmp / 'run' / 'udhcpd-usb0.pid').read_text().strip().isdigit())
            self.tearDown(); self.setUp()

    def test_rndis_serves_rndis0(self):
        for shell in self.each_shell():
            self.netdevs('rndis0')
            self.build(shell, 'rndis')
            self.assertIn('interface rndis0\n', self.conf())
            self.assertIn('ifconfig rndis0 192.168.77.1 netmask 255.255.255.0 up', (self.tmp / 'log').read_text())
            self.assertEqual('02:00:00:00:00:04', (self.tmp / 'run' / 'mu300-usb-host-mac').read_text().strip())
            self.tearDown(); self.setUp()

    def test_no_netdev_no_server(self):
        for shell in self.each_shell():
            self.build(shell, None)
            self.assertEqual([], self.starts())
            self.assertFalse((self.tmp / 'run' / 'udhcpd-usb0.conf').exists())
            self.tearDown(); self.setUp()

    def test_a_rebuilt_gadget_replaces_the_server(self):
        # apply_usb_net builds the gadget a second time (D12): one server at a time, on the new netdev
        for shell in self.each_shell():
            self.netdevs('usb0', 'rndis0')
            (self.tmp / 'udc' / '25100000.dwc3').mkdir(parents=True)
            (self.tmp / 'root' / 'etc' / 'mu300').mkdir(parents=True)
            (self.tmp / 'root' / 'etc' / 'mu300' / 'usb-net').write_text('rndis\n')
            self.build(shell, None, f'apply_usb_net "{self.tmp}/root"')
            log = (self.tmp / 'log').read_text().splitlines()
            starts = [i for i, l in enumerate(log) if l.startswith('udhcpd ')]
            kills = [i for i, l in enumerate(log) if l.startswith('kill ')]
            self.assertEqual(2, len(starts))
            self.assertEqual(1, len(kills), log)
            self.assertLess(starts[0], kills[0])
            self.assertLess(kills[0], starts[1])
            self.assertIn('interface rndis0\n', self.conf())
            self.assertEqual('02:00:00:00:00:04', (self.tmp / 'run' / 'mu300-usb-host-mac').read_text().strip())
            self.tearDown(); self.setUp()

    def test_place_in_init(self):
        block = self.block('usb-gadget')
        self.assertIn('start $NET.200', block)
        self.assertIn('option lease 120', block)
        # gone before the system starts: its own server answers (Ubuntu's dnsmasq, OpenWrt's preinit then dnsmasq)
        self.assertEqual(1, INIT.count('killall udhcpd 2>/dev/null\n    mkdir -p /newroot/dev'))
        self.assertLess(INIT.index('killall udhcpd'), INIT.index('exec switch_root'))
        # the standalone fallback starts its own server on usb0 in place of the early one
        tail = INIT[INIT.index('stage=rootfs-unavailable'):]
        self.assertLess(tail.index('killall udhcpd'), tail.index('udhcpd /etc/udhcpd.conf'))
        self.assertIn('(sleep 300\n', INIT)


class UsbNetPlace(unittest.TestCase):
    def test_after_pick_root_before_switch_root(self):
        call = 'apply_usb_net "$rootdir"'
        self.assertEqual(1, INIT.count(call))
        self.assertLess(INIT.index('rootdir=$(pick_root)'), INIT.index(call))
        self.assertLess(INIT.index(call), INIT.index('exec switch_root'))
        # the gadget is still built before the root is looked for (D12: not the fork's order)
        self.assertLess(INIT.index('\nsetup_usb_gadget\n'), INIT.index('# --- root-select begin'))

    def test_one_gadget_builder(self):
        self.assertEqual(1, INIT.count('echo 0x0302 > "$g/bcdDevice"'))
        self.assertNotIn('cdc=', INIT)


if __name__ == '__main__':
    unittest.main()
