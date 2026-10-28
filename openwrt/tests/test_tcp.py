"""mu300-tcp (kanoqwq's b9f71e72): the device's own TCP congestion control - only what the kernel offers, the kernel's
own pair read once per boot and put back by "default", the old pair back when the kernel refuses, nothing touched
without a saved setting, and the boot replay from mu300-toolkit apply."""
import os
import unittest

from helpers import BIN, TOP, ShellTest

CC = 'net/ipv4/tcp_congestion_control'
QD = 'net/core/default_qdisc'
AV = 'net/ipv4/tcp_available_congestion_control'


class Tcp(ShellTest):
    def setUp(self):
        super().setUp()
        self.stub('id', 'echo 0')
        self.stub('modprobe', 'echo "$*" >> "$STUBLOG/modprobe.log"; exit 0')
        self.sys = self.tmp / 'sys'
        self.conf = self.tmp / 'etc' / 'tcp.conf'
        self.base = self.tmp / 'run' / 'tcp-default'

    def kernel(self, available='reno cubic bbr', cc='cubic', qd='fq_codel', boot='b1'):
        for k, v in ((CC, cc), (QD, qd), (AV, available), ('kernel/random/boot_id', boot)):
            p = self.sys / k
            p.parent.mkdir(parents=True, exist_ok=True)
            p.chmod(0o644) if p.exists() else None
            p.write_text(v + '\n')

    def reset(self, **kw):
        self.conf.unlink(missing_ok=True)
        self.base.unlink(missing_ok=True)
        self.kernel(**kw)

    def tcp(self, shell, *args):
        return self.script(shell, BIN / 'mu300-tcp', *args, MU300_TCP_CONF=self.conf, MU300_TCP_SYS=self.sys,
                           MU300_TCP_BASE=self.base)

    def now(self):
        return (self.sys / CC).read_text().strip(), (self.sys / QD).read_text().strip()

    def state(self, shell):
        r = self.tcp(shell, 'state')
        self.assertEqual(r.returncode, 0, r.stderr)
        return dict(l.split('=', 1) for l in r.stdout.splitlines())

    def test_bbr_sets_fq_and_default_puts_the_kernels_pair_back(self):
        for shell in self.each_shell():
            self.reset()
            r = self.tcp(shell, 'set', 'bbr')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.now(), ('bbr', 'fq'))
            self.assertEqual(self.conf.read_text(), 'ALGO=bbr\n')
            st = self.state(shell)
            self.assertEqual((st['SAVED'], st['CURRENT'], st['QDISC'], st['OFFERED'], st['DEFAULT'], st['SUPPORTED']),
                             ('bbr', 'bbr', 'fq', 'cubic bbr', 'cubic', '1'))
            # cubic keeps the kernel's own queue, not fq
            self.assertEqual(self.tcp(shell, 'set', 'cubic').returncode, 0)
            self.assertEqual(self.now(), ('cubic', 'fq_codel'))
            self.assertEqual(self.tcp(shell, 'set', 'bbr').returncode, 0)
            r = self.tcp(shell, 'set', 'default')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.now(), ('cubic', 'fq_codel'))
            self.assertFalse(self.conf.exists())

    def test_the_default_is_read_once_per_boot(self):
        for shell in self.each_shell():
            self.reset()
            self.assertEqual(self.tcp(shell, 'set', 'bbr').returncode, 0)
            # the same boot: what was there before the first write is the default, not what is there now
            self.assertEqual(self.state(shell)['DEFAULT'], 'cubic')
            # a new boot (another boot_id): the boot replay reads the kernel's pair first, then applies the saved one
            self.kernel(cc='reno', qd='pfifo_fast', boot='b2')
            r = self.tcp(shell, 'apply')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.now(), ('bbr', 'fq'))
            self.assertEqual(self.tcp(shell, 'set', 'default').returncode, 0)
            self.assertEqual(self.now(), ('reno', 'pfifo_fast'))

    def test_nothing_saved_touches_nothing(self):
        for shell in self.each_shell():
            self.reset(cc='reno', qd='pfifo_fast')
            before = {p: p.stat().st_mtime_ns for p in (self.sys / CC, self.sys / QD)}
            r = self.tcp(shell, 'apply')
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, '', ''))
            self.assertEqual({p: p.stat().st_mtime_ns for p in before}, before)
            self.assertFalse(self.conf.exists())
            self.assertEqual(self.state(shell)['SAVED'], '')

    def test_a_kernel_without_bbr_offers_and_takes_no_bbr(self):
        # a kernel built without TCP_CONG_BBR
        for shell in self.each_shell():
            self.reset(available='reno cubic')
            self.assertEqual(self.state(shell)['OFFERED'], 'cubic')
            r = self.tcp(shell, 'set', 'bbr')
            self.assertEqual(r.returncode, 1)
            self.assertIn('no bbr', r.stderr)
            self.assertEqual(self.now(), ('cubic', 'fq_codel'))
            self.assertFalse(self.conf.exists())
            # a saved bbr from another kernel is not forced on this one at boot
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('ALGO=bbr\n')
            self.assertEqual(self.tcp(shell, 'apply').returncode, 1)
            self.assertEqual(self.now(), ('cubic', 'fq_codel'))

    def test_a_refused_write_puts_the_old_pair_back(self):
        if getattr(os, 'geteuid', lambda: 1)() == 0:
            self.skipTest('root writes a read-only file')
        for shell in self.each_shell():
            self.reset()
            # the kernel takes the queue but not the congestion control: the queue goes back
            (self.sys / CC).chmod(0o444)
            r = self.tcp(shell, 'set', 'bbr')
            (self.sys / CC).chmod(0o644)
            self.assertEqual(r.returncode, 1)
            self.assertEqual(self.now(), ('cubic', 'fq_codel'))
            self.assertFalse(self.conf.exists())

    def test_bad_values_and_root(self):
        for shell in self.each_shell():
            self.reset()
            for bad in ('', 'reno', 'BBR', '-x', 'bbr;id'):
                r = self.tcp(shell, 'set', bad)
                self.assertEqual(r.returncode, 2, bad)
            self.assertEqual(self.now(), ('cubic', 'fq_codel'))
        self.stub('id', 'echo 1000')
        for shell in self.each_shell():
            self.assertEqual(self.tcp(shell, 'set', 'bbr').returncode, 1)
            self.assertEqual(self.tcp(shell, 'status').returncode, 0)

    def test_boot_replay_is_wired(self):
        kit = (BIN / 'mu300-toolkit').read_text()
        self.assertIn('/opt/mu300/bin/mu300-tcp apply', kit)
        init = (TOP / 'openwrt/overlay/etc/init.d/mu300-toolkit').read_text()
        self.assertIn('[ -r /etc/mu300/tcp.conf ]', init)
        self.assertIn('mu300-tcp', (TOP / 'openwrt/opt/mu300/lib/path-commands').read_text().split())
        # only the two sysctls: no firewall, no interface, no VPN
        src = (BIN / 'mu300-tcp').read_text()
        for word in ('nft ', 'iptables', 'uci ', 'fw4', 'mu300-vpn', 'ip link', 'tc qdisc'):
            self.assertNotIn(word, src)


if __name__ == '__main__':
    unittest.main()
