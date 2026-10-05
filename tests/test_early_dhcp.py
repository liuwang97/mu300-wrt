"""The USB host's early DHCP lease, from boot/init into the system (K5, K9, K11, K13, K25-K27): OpenWrt's preinit
server on the system's own subnet, the LAN hook that ends it, the first-boot defaults, and Ubuntu's lan-start. The
scripts run against a scratch directory and stub commands, under each shell (busybox ash on OpenWrt)."""
import shutil
import subprocess
import unittest

from helpers import BIN, TOP, ShellTest


OPENWRT = TOP / 'openwrt' / 'overlay'


class EarlyUsbOpenWrt(ShellTest):
    """The early DHCP lease of boot/init carried into OpenWrt (K9, K11, K13, K25-K27): preinit serves the system's
    own LAN on the gadget's netdev until netifd brings the LAN up, the LAN hook ends it, and the first-boot defaults
    give the host its pinned, broadcast lease and the early path its firewall zone."""

    def setUp(self):
        super().setUp()
        (self.tmp / 'run').mkdir()
        (self.tmp / 'net').mkdir()

    def netdevs(self, *names):
        for n in names:
            (self.tmp / 'net' / n).mkdir(parents=True, exist_ok=True)

    def text(self, rel, **paths):
        body = (OPENWRT / rel).read_text()
        for old, new in paths.items():
            body = body.replace(old, new)
        return body

    # --- preinit 06_mu300_early_usb (K11)
    def preinit(self, shell, lan):
        self.stub('uci', f'echo "uci $*" >> "$STUBLOG/uci.log"; [ "$*" = "-q get network.lan.ipaddr" ] && '
                         f'{"echo " + repr(lan) if lan else "exit 1"}')
        self.stub('mu300-lan-ip', 'echo 192.168.78.1')
        # (not named busybox: that would be the shell under test, first on PATH)
        self.stub('bb', 'echo "busybox $*" >> "$STUBLOG/bb.log"\n'
                             'case $1 in udhcpd) cp "$3" "$STUBLOG/udhcpd.conf" ;; esac')
        body = self.text('lib/preinit/06_mu300_early_usb', **{
            '/opt/mu300/bin/busybox': f'{self.stubs}/bb', '/opt/mu300/bin/': f'{self.stubs}/', '/run/': f'{self.tmp}/run/',
            '/sys/class/net/': f'{self.tmp}/net/'})
        r = self.sh(shell, 'boot_hook_add() { :; }\n' + body + '\nmu300_early_usb\nwait\n')
        self.assertEqual(0, r.returncode, r.stderr)
        conf = self.tmp / 'udhcpd.conf'
        return conf.read_text() if conf.exists() else None

    def test_preinit_uses_the_configured_lan(self):
        # a custom LAN address: the host is on the system's subnet from its first lease in the system on
        for shell in self.each_shell():
            for lan in ('10.1.2.1', '10.1.2.1/24'):
                self.netdevs('usb0')
                conf = self.preinit(shell, lan)
                for line in ('start 10.1.2.200', 'end 10.1.2.200', 'interface usb0', 'option router 10.1.2.1',
                             'option dns 10.1.2.1', 'option subnet 255.255.255.0', 'option lease 3600'):
                    self.assertIn(line + '\n', conf, lan)
                self.assertNotIn('192.168.77', conf)
                bb = (self.tmp / 'bb.log').read_text()
                self.assertIn('busybox ifconfig usb0 10.1.2.1 netmask 255.255.255.0 up', bb)
                self.assertTrue((self.tmp / 'run' / 'mu300-early-udhcpd.pid').read_text().strip().isdigit())
                self.tearDown(); self.setUp()

    def test_preinit_without_a_lan_address_takes_the_device_default(self):
        for shell in self.each_shell():
            self.netdevs('usb0')
            conf = self.preinit(shell, None)
            self.assertIn('start 192.168.78.200\n', conf)
            self.assertIn('option router 192.168.78.1\n', conf)
            self.tearDown(); self.setUp()

    def test_preinit_serves_rndis0(self):
        for shell in self.each_shell():
            self.netdevs('rndis0')
            conf = self.preinit(shell, '10.1.2.1')
            self.assertIn('interface rndis0\n', conf)
            self.assertIn('busybox ifconfig rndis0 10.1.2.1', (self.tmp / 'bb.log').read_text())
            self.tearDown(); self.setUp()

    def test_preinit_without_a_gadget_does_nothing(self):
        for shell in self.each_shell():
            self.assertIsNone(self.preinit(shell, '10.1.2.1'))
            self.assertFalse((self.tmp / 'run' / 'mu300-early-udhcpd.pid').exists())
            self.tearDown(); self.setUp()

    # --- hotplug iface/10-mu300-usb (K9; K10 rejected: the re-enumeration stays)
    def hotplug(self, shell, stamp_age=None):
        (self.tmp / 'run' / 'mu300-early-udhcpd.pid').write_text('4242\n')
        (self.tmp / 'uptime').write_text('100.50 90.00\n')
        (self.tmp / 'mounts').write_text(f'configfs {self.tmp}/cfg configfs rw 0 0\n')
        g = self.tmp / 'cfg' / 'usb_gadget' / 'linux'
        g.mkdir(parents=True, exist_ok=True)
        (g / 'UDC').write_text('25100000.dwc3\n')
        if stamp_age is not None:
            (self.tmp / 'stamp').write_text(f'{100 - stamp_age}\n')
        # usb0 kept the bridge's address; rndis0 is in the bridge without it
        self.stub('ip', 'echo "ip $*" >> "$STUBLOG/ip.log"\n'
                        'case "$*" in\n'
                        '"-4 -o addr show dev br-lan") echo "9: br-lan    inet 10.1.2.1/24 brd 10.1.2.255 scope global br-lan" ;;\n'
                        '"-o link show dev usb0"|"-o link show dev rndis0") echo "7: $5: <UP> mtu 1500 master br-lan state UP" ;;\n'
                        '"-4 -o addr show dev usb0") echo "7: usb0    inet 10.1.2.1/24 scope global usb0" ;;\n'
                        'esac')
        body = self.text('etc/hotplug.d/iface/10-mu300-usb', **{
            '/run/': f'{self.tmp}/run/', '/sys/class/net/': f'{self.tmp}/net/', '/tmp/mu300-usb-rebound': f'{self.tmp}/stamp',
            '/sys/kernel/config': f'{self.tmp}/cfg', '/proc/uptime': f'{self.tmp}/uptime', '/proc/mounts': f'{self.tmp}/mounts'})
        code = ('kill() { echo "kill $*" >> "$STUBLOG/kill.log"; }; sleep() { :; }; logger() { :; }; mount() { :; }\n'
                'ACTION=ifup INTERFACE=lan\n' + body)
        r = self.sh(shell, code)
        self.assertEqual('', r.stderr)
        return g

    def test_lan_up_ends_the_early_server(self):
        for shell in self.each_shell():
            self.netdevs('usb0', 'rndis0')
            g = self.hotplug(shell)
            self.assertEqual('kill 4242\n', (self.tmp / 'kill.log').read_text())
            self.assertFalse((self.tmp / 'run' / 'mu300-early-udhcpd.pid').exists())
            ip = (self.tmp / 'ip.log').read_text()
            self.assertIn('ip addr del 10.1.2.1/24 dev usb0\n', ip)
            self.assertNotIn('dev rndis0\nip addr del', ip)
            self.assertEqual(1, ip.count('ip addr del'))
            self.assertIn('ip link set rndis0 master br-lan\n', ip)
            # the macOS re-enumeration still runs (K10 rejected until its gate)
            self.assertEqual('100', (self.tmp / 'stamp').read_text().strip())
            self.assertEqual('25100000.dwc3', (g / 'UDC').read_text().strip())
            self.tearDown(); self.setUp()

    def test_without_rndis0_nothing_is_reattached(self):
        for shell in self.each_shell():
            self.netdevs('usb0')
            self.hotplug(shell)
            self.assertNotIn('link set rndis0', (self.tmp / 'ip.log').read_text())
            self.tearDown(); self.setUp()

    def test_handoff_even_when_the_re_enumeration_waits(self):
        # a second LAN ifup within 20 s skips the re-enumeration, never the handoff
        for shell in self.each_shell():
            self.netdevs('usb0')
            self.hotplug(shell, stamp_age=5)
            self.assertEqual('kill 4242\n', (self.tmp / 'kill.log').read_text())
            self.assertIn('ip addr del 10.1.2.1/24 dev usb0', (self.tmp / 'ip.log').read_text())
            self.assertEqual('95', (self.tmp / 'stamp').read_text().strip())
            self.tearDown(); self.setUp()

    def test_other_events_do_nothing(self):
        body = self.text('etc/hotplug.d/iface/10-mu300-usb', **{'/run/': f'{self.tmp}/run/'})
        (self.tmp / 'run' / 'mu300-early-udhcpd.pid').write_text('4242\n')
        for shell in self.each_shell():
            for env in ('ACTION=ifdown INTERFACE=lan', 'ACTION=ifup INTERFACE=wan'):
                r = self.sh(shell, 'kill() { echo "kill $*" >> "$STUBLOG/kill.log"; }\n' + env + '\n' + body)
                self.assertEqual(0, r.returncode)
                self.assertFalse((self.tmp / 'kill.log').exists(), env)

    # --- uci-defaults 90-mu300 (K25, K26, K27)
    def defaults(self, shell, runs=1, mac='02:50:aa:bb:77:02'):
        (self.tmp / 'inittab').write_text('')
        if mac:
            (self.tmp / 'run' / 'mu300-usb-host-mac').write_text(mac + '\n')
        self.stub('mu300-lan-ip', 'echo 192.168.77.1')
        # a uci that remembers what was added: `show firewall` lists the earlyusb zone once one was named so
        self.stub('uci', 'echo "uci $*" >> "$STUBLOG/uci.log"\n'
                         '[ "$1" = -q ] && shift\n'
                         'case "$1 $2" in\n'
                         '"show firewall") echo "firewall.@zone[1].name=\'wan\'"\n'
                         '  grep -q "^uci set firewall\\.cfg0e1.name=earlyusb$" "$STUBLOG/uci.log" && echo "firewall.@zone[2].name=\'earlyusb\'" ;;\n'
                         '"show network") echo "network.@device[0].name=\'br-lan\'" ;;\n'
                         '"add firewall") echo cfg0e1 ;;\n'
                         '"batch ") sed "s/^/batch: /" >> "$STUBLOG/uci.log" ;;\n'
                         'esac\nexit 0')
        body = self.text('etc/uci-defaults/90-mu300', **{
            '/opt/mu300/bin/': f'{self.stubs}/', '/run/': f'{self.tmp}/run/', '/sys/': f'{self.tmp}/sys/',
            '/etc/inittab': f'{self.tmp}/inittab'})
        for _ in range(runs):
            r = self.sh(shell, body)
            self.assertEqual(0, r.returncode, r.stderr)
        return (self.tmp / 'uci.log').read_text()

    def test_defaults_pin_the_usb_host_with_broadcast(self):
        for shell in self.each_shell():
            log = self.defaults(shell)
            for line in ("batch: set dhcp.mu300_usb=host", "batch: set dhcp.mu300_usb.mac='02:50:aa:bb:77:02'",
                         "batch: set dhcp.mu300_usb.ip='192.168.77.200'", "batch: set dhcp.mu300_usb.broadcast='1'",
                         'uci commit dhcp'):
                self.assertIn(line + '\n', log)
            # Task 30's offloading stays
            self.assertIn("uci -q set firewall.@defaults[0].flow_offloading=1\n", log)
            self.tearDown(); self.setUp()

    def test_defaults_without_a_known_host_mac_pin_nothing(self):
        for shell in self.each_shell():
            log = self.defaults(shell, mac=None)
            self.assertNotIn('mu300_usb', log)
            self.tearDown(); self.setUp()

    def test_defaults_add_the_earlyusb_zone_once(self):
        for shell in self.each_shell():
            log = self.defaults(shell, runs=2)
            self.assertEqual(1, log.count('uci add firewall zone\n'))
            for line in ('uci set firewall.cfg0e1.name=earlyusb', 'uci add_list firewall.cfg0e1.device=usb0',
                         'uci add_list firewall.cfg0e1.device=rndis0', 'uci set firewall.cfg0e1.input=ACCEPT',
                         'uci set firewall.cfg0e1.forward=REJECT', 'uci commit firewall'):
                self.assertIn(line + '\n', log)
            self.tearDown(); self.setUp()

    def test_defaults_bridge_rndis0_only_when_it_exists(self):
        for shell in self.each_shell():
            for present in (False, True):
                if present:
                    (self.tmp / 'sys' / 'class' / 'net' / 'rndis0').mkdir(parents=True)
                log = self.defaults(shell)
                self.assertIn("uci add_list network.@device[0].ports=usb0\n", log)
                self.assertEqual(present, "uci add_list network.@device[0].ports=rndis0\n" in log)
                self.assertIn("uci set network.@device[0].bridge_empty=1\n", log)
                self.tearDown(); self.setUp()

    # --- init.d/mu300-post (K13)
    def test_post_bridges_usb1(self):
        text = (OPENWRT / 'etc' / 'init.d' / 'mu300-post').read_text()
        self.assertIn('[ -e /sys/class/net/usb1 ] && ip link set usb1 master br-lan', text)
        # K12 is Task 26's gate: the lease check stays for now
        self.assertIn('mu300-usb-reset --if-no-lease 25', text)

    def test_overlay_scripts_are_executable(self):
        # the fork's mu300-post and mu300-usb-reset (Task 26) run the LAN hook directly
        for rel in ('lib/preinit/06_mu300_early_usb', 'etc/hotplug.d/iface/10-mu300-usb', 'etc/uci-defaults/90-mu300'):
            self.assertTrue((OPENWRT / rel).stat().st_mode & 0o111, rel)


class LanStartHandoff(ShellTest):
    """Ubuntu: the early lease of boot/init ends at lan-start, which takes the early address off whichever gadget
    netdev init put it on (usb0, or rndis0 with RNDIS), so br-lan alone holds the subnet and dnsmasq answers."""

    def test_flushes_both_gadget_netdevs(self):
        if not shutil.which('bash'):
            self.skipTest('no bash')
        self.stub('ip', 'echo "ip $*" >> "$STUBLOG/ip.log"; exit 0')
        self.stub('dnsmasq', 'echo "dnsmasq $*" >> "$STUBLOG/dnsmasq.log"')
        r = subprocess.run(['bash', str(BIN / 'lan-start')], capture_output=True, text=True,
                           env=self.env(MU300_LAN_IP='10.1.2.1'), timeout=30)
        self.assertEqual(0, r.returncode, r.stderr)
        ip = (self.tmp / 'ip.log').read_text()
        self.assertIn('ip addr flush dev usb0\n', ip)
        self.assertIn('ip addr flush dev rndis0\n', ip)
        self.assertLess(ip.index('ip addr flush dev rndis0'), ip.index('ip addr replace 10.1.2.1/24 dev br-lan'))
        # the early host address (.200) is in the pool: a renewal is answered (NAK, then a fresh lease)
        self.assertIn('--dhcp-range=10.1.2.2,10.1.2.200,', (self.tmp / 'dnsmasq.log').read_text())


if __name__ == '__main__':
    unittest.main()
