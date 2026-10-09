"""The VPN is a module of its own (dikeckaan/mu300-linux-vpn, installed as the vpn extra): the images carry no
mu300-vpn, and every place of mu300-linux that works with it must do nothing harmful without it. mu300-power and
wifi-client have their own tests of this (test_power, test_wifi_client); here the toolkit's menu, mobile-data's
guard and the LuCI panel's VPN capability and action."""
import unittest

from helpers import BIN, TOP, ShellTest

TOOLKIT = BIN / 'mu300-toolkit'
ADAPTERS = TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem'


class Toolkit(ShellTest):
    """menu_vpn of mu300-toolkit, run on its own: the VPN menu with the module, how to get it without"""

    def setUp(self):
        super().setUp()
        src = TOOLKIT.read_text()
        self.assertLess(src.index('\nvpn_enabled() {'), src.index('\nmenu_signal() {'))
        self.block = src[src.index('\nvpn_enabled() {'):src.index('\nmenu_signal() {')]
        self.stub('mu300-extra', 'echo "mu300-extra $*" >> "$STUBLOG/calls"')

    def run_menu(self, shell, vpn, answers):
        pre = ('title() { echo "== $1"; }; pause() { :; }; state_color() { echo "$1"; }; svc() { echo inactive; }\n'
               'C=; N=; D=; B=; G=\nVPN_CONF=/nonexistent\n')
        return self.sh(shell, pre + self.block + '\nmenu_vpn; echo "rc=$?"', stdin=answers, MU300_VPN_CMD=vpn,
                       MU300_EXTRA_CMD=self.stubs / 'mu300-extra')

    def calls(self):
        f = self.tmp / 'calls'
        c = f.read_text() if f.exists() else ''
        f.unlink(missing_ok=True)
        return c

    def test_without_the_module_it_says_how_to_get_it(self):
        for shell in self.each_shell():
            r = self.run_menu(shell, self.tmp / 'no-such-mu300-vpn', '0\n')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertIn('https://github.com/dikeckaan/mu300-linux-vpn', r.stdout)
            self.assertIn('sudo mu300-extra install vpn', r.stdout)
            self.assertIn('--from', r.stdout)
            self.assertEqual(self.calls(), '')
            # 1: installs it there and then
            r = self.run_menu(shell, self.tmp / 'no-such-mu300-vpn', '1\n')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertEqual(self.calls(), 'mu300-extra install vpn\n')

    def test_with_the_module_it_is_the_vpn_menu(self):
        self.stub('mu300-vpn', 'echo "mu300-vpn $*" >> "$STUBLOG/calls"; [ "$1" != status ] || echo "state: off"')
        for shell in self.each_shell():
            r = self.run_menu(shell, self.stubs / 'mu300-vpn', '0\n')
            self.assertIn('rc=0', r.stdout, r.stderr)
            self.assertIn('state: off', r.stdout)
            self.assertNotIn('mu300-extra install vpn', r.stdout)
            self.assertIn('mu300-vpn status', self.calls())

    def test_the_menus_name_it_and_leave_its_service_out(self):
        src = TOOLKIT.read_text()
        self.assertIn('VPN (not installed: how to add it)', src)
        # the services list shows mu300-vpn only with the module
        self.assertIn('[ "$s" != mu300-vpn ] || vpn_present', src)


class Static(unittest.TestCase):
    def test_mobile_data_guard_is_optional(self):
        # a missing mu300-vpn must never stop mobile data from coming up
        src = (BIN / 'mobile-data').read_text()
        self.assertRegex(src, r'"\$\{MU300_VPN_CMD:-/opt/mu300/bin/mu300-vpn\}" guard >/dev/null 2>&1 \|\| true')

    def test_wifi_client_asks_only_a_present_mu300_vpn(self):
        self.assertIn('if [ -x "$VPN_CMD" ] && ! "$VPN_CMD" guard', (BIN / 'wifi-client').read_text())

    def test_luci_vpn_capability_is_the_module(self):
        info = (ADAPTERS / 'dashboard-info').read_text()
        self.assertIn('CAP_VPN=0; [ -x /opt/mu300/bin/mu300-vpn ] && [ -x /etc/init.d/mu300-vpn ] && CAP_VPN=1', info)
        act = (ADAPTERS / 'action').read_text()
        self.assertIn('if [ -x /opt/mu300/bin/mu300-vpn ] && [ -x /etc/init.d/mu300-vpn ]; then', act)


if __name__ == '__main__':
    unittest.main()
