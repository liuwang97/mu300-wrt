"""mu300-ttl: the TTL is rewritten by tc on clsact egress of every sipa_eth*, which an offloaded flow still passes
(the flowtable transmits with dev_queue_xmit), so fw4's flow offloading is never touched."""
import unittest

from helpers import BIN, ShellTest

KEY = 'firewall.@defaults[0].flow_offloading'


class TtlBase(ShellTest):
    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'etc' / 'ttl.conf'
        self.stub('id', 'echo 0')

    def openwrt(self, offloading='1'):
        """Fake uci (flow_offloading in a file) and fw4 that log their calls."""
        self.store = self.tmp / 'offloading'
        self.store.write_text(offloading + '\n')
        self.stub('uci', f'''echo "$*" >> "$STUBLOG/uci.log"
[ "$1" = -q ] && shift
case $1 in
    get) [ "$2" = '{KEY}' ] || exit 1; cat "$STUBLOG/offloading" ;;
    set) case $2 in '{KEY}='*) echo "${{2#*=}}" > "$STUBLOG/offloading" ;; *) exit 1 ;; esac ;;
    commit) [ "$2" = firewall ] || exit 1 ;;
    *) exit 1 ;;
esac''')
        self.stub('fw4', 'echo "$*" >> "$STUBLOG/fw4.log"')

    def ttl(self, shell, *args):
        return self.script(shell, BIN / 'mu300-ttl', *args, MU300_TTL_CONF=self.conf,
                           MU300_TTL_NET=self.tmp / 'net')

    def reset(self, offloading='1'):
        for f in ('uci.log', 'fw4.log', 'tc.log', 'modprobe.log'):
            (self.tmp / f).unlink(missing_ok=True)
        self.conf.unlink(missing_ok=True)
        if hasattr(self, 'store'):
            self.store.write_text(offloading + '\n')

    def log(self, name):
        p = self.tmp / name
        return p.read_text() if p.exists() else ''

    def offloading(self):
        return self.store.read_text().strip()


class TtlTc(TtlBase):
    """act_pedit, act_csum, cls_matchall: a matchall filter on clsact egress of every sipa_eth*."""

    V4 = 'egress pref 10 protocol ip matchall action pedit ex munge ip ttl set {} pipe action csum ip'
    V6 = 'egress pref 11 protocol ipv6 matchall action pedit ex munge ip6 hoplimit set {}'

    def setUp(self):
        super().setUp()
        self.stub('modprobe', 'echo "$*" >> "$STUBLOG/modprobe.log"')
        # tc logs its arguments; "filter show" prints $STUBLOG/tc.show; an add fails when $STUBLOG/tc.fail exists
        self.stub('tc', 'echo "$*" >> "$STUBLOG/tc.log"\n'
                        'case "$*" in *"filter show"*) cat "$STUBLOG/tc.show" 2>/dev/null ;;\n'
                        '    *"filter add"*) [ -e "$STUBLOG/tc.fail" ] && exit 2 ;; esac\nexit 0')
        net = self.tmp / 'net'
        for i in ('sipa_eth0', 'sipa_eth1', 'lo', 'br-lan'):
            (net / i).mkdir(parents=True, exist_ok=True)

    def tc_log(self):
        return self.log('tc.log').splitlines()

    def test_set_uses_tc_on_every_sipa_and_leaves_offloading_alone(self):
        self.openwrt()
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.log('modprobe.log').split(), ['act_pedit', 'act_csum', 'cls_matchall'])
            tc = self.tc_log()
            for i in ('sipa_eth0', 'sipa_eth1'):
                self.assertIn(f'qdisc add dev {i} clsact', tc)
                self.assertIn(f'filter add dev {i} ' + self.V4.format(64), tc)
                self.assertIn(f'filter add dev {i} ' + self.V6.format(64), tc)
            self.assertFalse([c for c in tc if ' lo ' in c + ' ' or 'br-lan' in c], tc)
            self.assertEqual(self.offloading(), '1')
            self.assertNotIn('set', self.log('uci.log'))
            self.assertEqual(self.log('fw4.log'), '')

    def test_apply_is_idempotent(self):
        # mobile-data calls apply on every bring-up: matchall has no replace (EEXIST, seen on the U30 Air), so each
        # filter is deleted before it is added again - never a second one, never a failed add
        for shell in self.each_shell():
            self.reset()
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('TTL=65\n')
            self.assertEqual(self.ttl(shell, 'apply').returncode, 0)
            self.assertEqual(self.ttl(shell, 'apply').returncode, 0)
            tc = self.tc_log()
            self.assertFalse([c for c in tc if 'replace' in c], tc)
            add = 'filter add dev sipa_eth0 ' + self.V4.format(65)
            self.assertEqual(tc.count(add), 2)
            # the second add comes after a delete of the first
            last_add = max(i for i, x in enumerate(tc) if x == add)
            self.assertIn('filter del dev sipa_eth0 egress pref 10', tc[tc.index(add) + 1:last_add])

    def test_tc_failure_fails_and_leaves_no_filter(self):
        self.openwrt()
        (self.tmp / 'tc.fail').write_text('')
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 1)
            self.assertIn('could not be set', r.stderr)
            tc = self.tc_log()
            last_add = max(i for i, x in enumerate(tc) if 'filter add' in x)
            self.assertIn('filter del dev sipa_eth0 egress pref 10', tc[last_add:])   # half-set filters removed
            self.assertEqual(self.offloading(), '1')
            self.assertEqual(self.log('fw4.log'), '')
        (self.tmp / 'tc.fail').unlink()

    def test_a_kernel_without_the_modules_fails(self):
        self.openwrt()
        self.stub('modprobe', 'exit 1')
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 1)
            self.assertIn('no tc TTL rewrite', r.stderr)
            self.assertFalse([c for c in self.tc_log() if 'filter add' in c])
            self.assertEqual(self.offloading(), '1')

    def test_off_removes_both_and_leaves_offloading_alone(self):
        self.openwrt()
        for shell in self.each_shell():
            self.reset('0')                     # turned off in LuCI by the user: not ours to turn back on
            self.assertEqual(self.ttl(shell, 'set', '128').returncode, 0)
            (self.tmp / 'tc.log').unlink()
            r = self.ttl(shell, 'off')
            self.assertEqual(r.returncode, 0, r.stderr)
            tc = self.tc_log()
            for i in ('sipa_eth0', 'sipa_eth1'):
                for pref in ('10', '11'):
                    self.assertIn(f'filter del dev {i} egress pref {pref}', tc)
            self.assertFalse([c for c in tc if 'filter add' in c], tc)
            self.assertEqual(self.offloading(), '0')
            self.assertEqual(self.log('fw4.log'), '')

    def test_no_sipa_yet_sets_nothing_and_fails_nothing(self):
        # at boot before the modem: the rule comes with mobile data (mobile-data up calls apply)
        for shell in self.each_shell():
            self.reset()
            for i in ('sipa_eth0', 'sipa_eth1'):
                (self.tmp / 'net' / i).rmdir()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse([c for c in self.tc_log() if 'filter add' in c])
            for i in ('sipa_eth0', 'sipa_eth1'):
                (self.tmp / 'net' / i).mkdir()

    SHOW = """filter protocol ip pref 10 matchall chain 0 
filter protocol ip pref 10 matchall chain 0 handle 0x1 
  not_in_hw (rule hit 42)
\taction order 1:  pedit action pipe keys 1
\tAction statistics:
\tSent 4200 bytes 42 pkt (dropped 0, overlimits 0 requeues 0) 
\taction order 2: csum (iph) action pass
\tSent 4200 bytes 42 pkt (dropped 0, overlimits 0 requeues 0) 
filter protocol ipv6 pref 11 matchall chain 0 
filter protocol ipv6 pref 11 matchall chain 0 handle 0x1 
  not_in_hw (rule hit 7)
\tSent 700 bytes 7 pkt (dropped 0, overlimits 0 requeues 0) 
"""

    def test_status_names_the_backend(self):
        self.openwrt()
        for shell in self.each_shell():
            self.reset()
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('TTL=64\n')
            (self.tmp / 'tc.show').write_text(self.SHOW)
            r = self.ttl(shell)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('tc on sipa_eth0', r.stdout)
            self.assertIn('flow offload untouched', r.stdout)
            self.assertIn('IPv4 TTL 64 (84 packets so far)', r.stdout)      # both interfaces summed
            self.assertIn('IPv6 hop limit 64 (14 packets so far)', r.stdout)
            r = self.ttl(shell, 'state')
            self.assertEqual(r.stdout, 'TTL=64\nBACKEND=tc\nIFACES=sipa_eth0 sipa_eth1\nOFFLOAD=1\n')
            (self.tmp / 'tc.show').unlink()
            self.store.write_text('0\n')
            self.conf.unlink()
            r = self.ttl(shell, 'state')
            self.assertEqual(r.stdout, 'TTL=\nBACKEND=none\nIFACES=\nOFFLOAD=0\n')

    def test_no_fw4_no_offload_state(self):
        self.stub('uci', 'echo "$*" >> "$STUBLOG/uci.log"')
        for shell in self.each_shell():
            self.reset()
            self.assertEqual(self.ttl(shell, 'set', '64').returncode, 0)
            self.assertIn('filter add dev sipa_eth0 ' + self.V4.format(64), self.tc_log())
            r = self.ttl(shell, 'off')
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stderr, '')
            self.assertEqual(self.log('uci.log'), '')
            r = self.ttl(shell, 'state')
            self.assertIn('OFFLOAD=\n', r.stdout)


if __name__ == '__main__':
    unittest.main()
