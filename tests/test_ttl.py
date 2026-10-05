"""mu300-ttl and fw4's software flowtable: while a TTL is set, OpenWrt's flow offloading is off (an offloaded flow
skips postrouting, where the TTL rule is); without fw4 (Ubuntu) nothing about the firewall is touched."""
import unittest

from helpers import BIN, ShellTest

KEY = 'firewall.@defaults[0].flow_offloading'


class TtlOffload(ShellTest):
    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'etc' / 'ttl.conf'
        self.stub('id', 'echo 0')
        self.stub('nft', 'echo "$*" >> "$STUBLOG/nft.args"; [ "$1" = -f ] && cat >> "$STUBLOG/nft.in"; '
                         '[ "$1" = list ] && exit 1; exit 0')

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
        return self.script(shell, BIN / 'mu300-ttl', *args, MU300_TTL_CONF=self.conf)

    def reset(self, offloading='1'):
        for f in ('nft.args', 'nft.in', 'uci.log', 'fw4.log'):
            (self.tmp / f).unlink(missing_ok=True)
        self.conf.unlink(missing_ok=True)
        if hasattr(self, 'store'):
            self.store.write_text(offloading + '\n')

    def log(self, name):
        p = self.tmp / name
        return p.read_text() if p.exists() else ''

    def offloading(self):
        return self.store.read_text().strip()

    def test_set_turns_offloading_off_and_off_turns_it_back_on(self):
        self.openwrt()
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('ip ttl set 64', self.log('nft.in'))
            self.assertEqual(self.offloading(), '0')
            self.assertIn('commit firewall', self.log('uci.log'))
            self.assertEqual(self.log('fw4.log'), '-q reload\n')
            # a new value with offloading already off: no second reload
            (self.tmp / 'fw4.log').unlink()
            self.assertEqual(self.ttl(shell, 'set', '128').returncode, 0)
            self.assertEqual(self.offloading(), '0')
            self.assertEqual(self.log('fw4.log'), '')
            r = self.ttl(shell, 'off')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(self.conf.exists())
            self.assertEqual(self.offloading(), '1')
            self.assertEqual(self.log('fw4.log'), '-q reload\n')
            self.assertEqual(r.stderr, '')

    def test_boot_apply_keeps_offloading_off_for_a_saved_ttl(self):
        # after an update uci-defaults (90-mu300) sets flow_offloading=1; mu300-toolkit apply (S96) runs this
        self.openwrt()
        for shell in self.each_shell():
            self.reset('1')
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('TTL=64\n')
            r = self.ttl(shell, 'apply')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('ip ttl set 64', self.log('nft.in'))
            self.assertEqual(self.offloading(), '0')
            self.assertEqual(self.log('fw4.log'), '-q reload\n')
            # the next boot: already off, the firewall is not reloaded again
            self.reset('0')
            self.conf.write_text('TTL=64\n')
            self.assertEqual(self.ttl(shell, 'apply').returncode, 0)
            self.assertEqual(self.offloading(), '0')
            self.assertEqual(self.log('fw4.log'), '')

    def test_no_ttl_leaves_offloading_as_it_is(self):
        # the default (no TTL): offloading stays on; a choice made in LuCI (off) is not undone at boot
        self.openwrt()
        for shell in self.each_shell():
            for value in ('1', '0'):
                self.reset(value)
                r = self.ttl(shell, 'apply')
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.offloading(), value)
                self.assertNotIn('set', self.log('uci.log'))
                self.assertEqual(self.log('fw4.log'), '')
                self.assertEqual(self.ttl(shell).returncode, 0)  # status touches nothing either
                self.assertEqual(self.log('uci.log').count('set'), 0)

    def test_invalid_value_touches_no_firewall(self):
        self.openwrt()
        for shell in self.each_shell():
            self.reset()
            self.assertEqual(self.ttl(shell, 'set', '0').returncode, 2)
            self.assertEqual(self.offloading(), '1')
            self.assertEqual(self.log('uci.log'), '')
            self.assertEqual(self.log('fw4.log'), '')

    def test_failed_rule_leaves_offloading_on(self):
        self.openwrt()
        self.stub('nft', '[ "$1" = -f ] && { cat > /dev/null; exit 1; }; exit 0')
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 1)
            self.assertIn('could not be set', r.stderr)
            self.assertEqual(self.offloading(), '1')
            self.assertEqual(self.log('fw4.log'), '')

    def test_failed_uci_is_reported_and_the_rule_stays(self):
        self.openwrt()
        self.stub('uci', 'exit 1')
        for shell in self.each_shell():
            self.reset()
            r = self.ttl(shell, 'set', '64')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('ip ttl set 64', self.log('nft.in'))
            self.assertIn('flow offloading could not be turned off', r.stderr)
            self.assertEqual(self.log('fw4.log'), '')

    def test_ubuntu_without_fw4_is_unchanged(self):
        # no fw4 on PATH: no uci call, whatever uci there is
        self.stub('uci', 'echo "$*" >> "$STUBLOG/uci.log"')
        for shell in self.each_shell():
            self.reset()
            self.assertEqual(self.ttl(shell, 'set', '64').returncode, 0)
            self.assertIn('ip ttl set 64', self.log('nft.in'))
            r = self.ttl(shell, 'off')
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stderr, '')
            self.assertEqual(self.log('uci.log'), '')


if __name__ == '__main__':
    unittest.main()
