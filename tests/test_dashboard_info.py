"""dashboard-info's network readouts (based on kanoqwq/mu300-linux@373e191 and @5c8c2f9, tests/test_dashboard_info.py):

- Wi-Fi clients: every associated station is one row with its bitrates and connected time (iw prints the signal
  before them), a station without metrics still counts, the DHCP name and address come from dnsmasq's lower-case
  leases file and its "*" is no name.
- DNS: the active servers of both data interfaces, from ubus's multi-line JSON, each once, never inactive ones.
- Connections: the kernel's nf_conntrack_count (mainline has no /proc/net/nf_conntrack), the table as fallback,
  null when neither can be read."""
import json

from helpers import ShellTest, TOP

INFO = TOP / 'openwrt' / 'luci-app-mu300' / 'root' / 'usr' / 'libexec' / 'unisoc-modem' / 'dashboard-info'

STATIONS = '''Station 3C:22:FB:00:00:01 (on wlan0)
\tinactive time:\t120 ms
\trx bytes:\t102400
\tsignal:  \t-48 [-48] dBm
\tsignal avg:\t-49 dBm
\ttx bitrate:\t866.7 MBit/s VHT-MCS 9 80MHz short GI VHT-NSS 2
\trx bitrate:\t390.0 MBit/s VHT-MCS 8 80MHz VHT-NSS 1
\tauthorized:\tyes
\tconnected time:\t725 seconds
Station 3c:22:fb:00:00:01 (on wlan0)
\tsignal:  \t-40 dBm
Station a4:83:e7:00:00:02 (on wlan0)
\tauthorized:\tyes
Station not-a-mac (on wlan0)
\tsignal:  \t-40 dBm
'''

# U30 Air (SC2355), `iw dev wlan0 station dump` and `iw dev wlan0 station get 3c:22:fb:00:00:01`
SC2355_DUMP = '''Station 3c:22:fb:00:00:01 (on wlan0)
\tcurrent time:\t1760178000123 ms
Station a4:83:e7:00:00:02 (on wlan0)
\tcurrent time:\t1760178000123 ms
'''
SC2355_GET = '''Station 3c:22:fb:00:00:01 (on wlan0)
\tsignal:  \t0 dBm
\ttx bitrate:\t(unknown)
\trx bitrate:\t144.4 MBit/s
\tcurrent time:\t1760178000125 ms
'''

LEASES = '''1999999999 3c:22:fb:00:00:01 192.168.0.10 laptop 01:3c:22:fb:00:00:01
1999999999 a4:83:e7:00:00:02 192.168.0.11 * *
'''

# what `ubus call network.interface.wan status` prints: the array over several lines, an inactive list too
WAN = '''{
\t"up": true,
\t"l3_device": "sipa_eth0",
\t"dns-server": [
\t\t"10.0.0.1",
\t\t"10.0.0.2"
\t],
\t"inactive": {
\t\t"dns-server": [
\t\t\t"9.9.9.9"
\t\t]
\t}
}'''
WAN6 = '''{
\t"up": true,
\t"dns-server": [
\t\t"2001:db8::53",
\t\t"10.0.0.2"
\t]
}'''

JSONFILTER = r'''
import json, re, sys
args = sys.argv[1:]
src = args[args.index('-s') + 1]
expr = args[args.index('-e') + 1]
m = re.fullmatch(r'@\["([^"]+)"\]\[\*\]', expr)
if not m:
    sys.exit(2)
v = json.loads(src).get(m.group(1))
if not isinstance(v, list):
    sys.exit(1)
for x in v:
    print(x)
'''


class Info(ShellTest):
    def setUp(self):
        super().setUp()
        self.stub('busybox', 'shift 4; exec "$@"')   # busybox timeout -s KILL N CMD...
        for name in ('ip', 'uci'):
            self.stub(name, 'exit 1')
        (self.tmp / 'stations').write_text(STATIONS)
        # iw dev IF station dump | iw dev IF station get MAC (a file per MAC; none: the station is unknown)
        self.stub('iw', 'case "$4" in dump) cat "$STUBLOG/stations" ;; '
                        'get) echo "$5" >> "$STUBLOG/gets"; cat "$STUBLOG/get.$5" 2>/dev/null || exit 237 ;; esac')
        (self.tmp / 'wan.json').write_text(WAN)
        (self.tmp / 'wan6.json').write_text(WAN6)
        self.stub('ubus', 'case "$*" in *interface.wan6*) cat "$STUBLOG/wan6.json" ;; '
                          '*interface.wan*) cat "$STUBLOG/wan.json" ;; *) exit 1 ;; esac')
        (self.stubs / 'jsonfilter.py').write_text(JSONFILTER)
        self.stub('jsonfilter', f'exec python3 "{self.stubs}/jsonfilter.py" "$@"')
        self.leases = self.tmp / 'dhcp.leases'
        self.leases.write_text(LEASES)

    def info(self, shell, **env):
        base = dict(MU300_DHCP_LEASES=self.leases, MU300_POWER_SUPPLY_DIR=self.tmp / 'psy',
                    MU300_CONNTRACK_COUNT=self.tmp / 'nf_conntrack_count',
                    MU300_CONNTRACK_TABLE=self.tmp / 'nf_conntrack')
        base.update(env)
        r = self.script(shell, INFO, **base)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_wifi_clients(self):
        for shell in self.each_shell():
            wifi = self.info(shell)['wifi']
            self.assertEqual(wifi['clients_n'], 2)
            first, second = wifi['clients']
            self.assertEqual(first, {'mac': '3c:22:fb:00:00:01', 'host': 'laptop', 'ip': '192.168.0.10',
                                     'signal': -48, 'tx': '866.7 Mbit/s', 'rx': '390.0 Mbit/s', 'conn': '12 min'})
            self.assertEqual(second, {'mac': 'a4:83:e7:00:00:02', 'host': None, 'ip': '192.168.0.11',
                                      'signal': None, 'tx': None, 'rx': None, 'conn': None})

    def test_sc2355_metrics_come_from_station_get(self):
        """Captured on the U30 Air (SC2355): the dump has only the MAC and the time, station get has the rx rate and a
        signal of 0 (unknown), tx is "(unknown)" and there is no connected time: those are null, never 0 dBm."""
        (self.tmp / 'stations').write_text(SC2355_DUMP)
        (self.tmp / 'get.3c:22:fb:00:00:01').write_text(SC2355_GET)
        for shell in self.each_shell():
            (self.tmp / 'gets').unlink(missing_ok=True)
            wifi = self.info(shell)['wifi']
            self.assertEqual(wifi['clients_n'], 2)
            first, second = wifi['clients']
            self.assertEqual(first, {'mac': '3c:22:fb:00:00:01', 'host': 'laptop', 'ip': '192.168.0.10',
                                     'signal': None, 'tx': None, 'rx': '144.4 Mbit/s', 'conn': None})
            self.assertEqual(second['mac'], 'a4:83:e7:00:00:02')
            self.assertEqual((second['signal'], second['rx']), (None, None))
            self.assertEqual((self.tmp / 'gets').read_text().split(), ['3c:22:fb:00:00:01', 'a4:83:e7:00:00:02'])

    def test_station_get_is_bounded(self):
        """At most 16 station get calls per round, however many clients the dump lists."""
        (self.tmp / 'stations').write_text(''.join(f'Station 02:00:00:00:00:{i:02x} (on wlan0)\n' for i in range(40)))
        for shell in self.each_shell():
            (self.tmp / 'gets').unlink(missing_ok=True)
            self.assertEqual(self.info(shell)['wifi']['clients_n'], 40)
            self.assertEqual(len((self.tmp / 'gets').read_text().split()), 16)

    def test_dns_from_multiline_status(self):
        for shell in self.each_shell():
            self.assertEqual(self.info(shell)['wan']['dns'], '10.0.0.1, 10.0.0.2, 2001:db8::53')

    def test_connections(self):
        count, table = self.tmp / 'nf_conntrack_count', self.tmp / 'nf_conntrack'
        for shell in self.each_shell():
            count.unlink(missing_ok=True)
            table.unlink(missing_ok=True)
            self.assertIsNone(self.info(shell)['conns'])
            table.write_text('a\nb\nc\n')
            self.assertEqual(self.info(shell)['conns'], 3)
            count.write_text('42\n')
            self.assertEqual(self.info(shell)['conns'], 42)
            count.write_text('x\n')
            self.assertEqual(self.info(shell)['conns'], 3)


if __name__ == '__main__':
    import unittest
    unittest.main()
