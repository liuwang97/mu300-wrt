"""dashboard-info's network readouts (based on kanoqwq/mu300-linux@373e191 and @5c8c2f9, tests/test_dashboard_info.py):

- Wi-Fi clients: every associated station is one row with its bitrates and connected time (iw prints the signal
  before them), a station without metrics still counts, the DHCP name and address come from dnsmasq's lower-case
  leases file and its "*" is no name.
- DNS: the active servers of both data interfaces, from ubus's multi-line JSON, each once, never inactive ones.
- Connections: the kernel's nf_conntrack_count (mainline has no /proc/net/nf_conntrack), the table as fallback,
  null when neither can be read.

The SIM's own number (cell, the collector; home.js, the row; FINDINGS 49, after kanoqwq@0d93695): AT+CNUM on full
rounds only, the IMS public identity (AT+SPPAURI?) when the SIM has none, nothing when neither has one; never in
the fast tier, never on flash, never in a log."""
import json
import os
import shutil
import subprocess
import unittest

from helpers import ShellTest, TOP

INFO = TOP / 'openwrt' / 'luci-app-mu300' / 'root' / 'usr' / 'libexec' / 'unisoc-modem' / 'dashboard-info'
CELL = TOP / 'openwrt' / 'luci-app-mu300' / 'root' / 'usr' / 'libexec' / 'unisoc-modem' / 'cell'
HOME = TOP / 'openwrt' / 'luci-app-mu300' / 'htdocs' / 'luci-static' / 'resources' / 'view' / 'mu300' / 'home.js'

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


class OwnNumber(ShellTest):
    """The collector's MSISDN: cell.json's msisdn / msisdn_src."""

    NUMBER = '+905551234567'
    SIM_CNUM = '+CNUM: "","+905551234567",145\nOK'
    IMS_URI = '+SPPAURI: "sip:+8613800000000@ims.mnc001.mcc460.3gppnetwork.org","tel:+8613800000000"\nOK'

    def setUp(self):
        super().setUp()
        self.stub('uci', 'exit 1')
        # the AT adapter: the replies a registered modem gives, the two number queries from the environment
        self.stub('fake-at', r"""printf '%s\n' "$*" >> "$STUBLOG/at-commands"
case "$*" in
  *AT+CFUN?*)     printf '+CFUN: 1\nOK\n' ;;
  *AT+CEREG?*)    printf '+CEREG: 2,1,"1A2B","01234567",7\nOK\n' ;;
  *AT+CESQ*)      printf '+CESQ: 99,99,255,255,20,50\nOK\n' ;;
  *AT+CIMI*)      printf '286016012345678\nOK\n' ;;
  *AT+CCID*)      printf '+CCID: 8990011234567890123\nOK\n' ;;
  *AT+CNUM*)      printf '%s\n' "$CNUM_REPLY" ;;
  *AT+SPPAURI?*)  printf '%s\n' "$IMS_REPLY" ;;
  *)              printf 'OK\n' ;;
esac""")
        self.log = self.tmp / 'at-commands'

    def collect(self, shell, mode='full', cnum='ERROR', ims='ERROR', tag='a'):
        """One collector round; returns the cache it wrote (cell.json for full, sig.json for fast)."""
        run = self.tmp / f'run-{tag}'
        ident = self.tmp / f'ident-{tag}'
        r = self.script(shell, CELL, mode, MU300_AT=self.stubs / 'fake-at', MU300_DASH_DIR=run,
                        MU300_DASH_IDENT_DIR=ident, MU300_DASH_POOL_DIR=self.tmp / f'pool-{tag}',
                        CNUM_REPLY=cnum, IMS_REPLY=ims)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout + r.stderr, '')     # a collector prints nothing: the number is not for a log
        return json.loads((run / ('cell.json' if mode == 'full' else 'sig.json')).read_text())

    def commands(self):
        return self.log.read_text() if self.log.exists() else ''

    def fresh(self, tag):
        self.log.unlink(missing_ok=True)
        for d in (f'run-{tag}', f'ident-{tag}', f'pool-{tag}'):
            shutil.rmtree(self.tmp / d, ignore_errors=True)

    def test_cnum_on_a_full_round(self):
        for i, shell in enumerate(self.each_shell()):
            self.fresh(i)
            c = self.collect(shell, 'full', cnum=self.SIM_CNUM, tag=i)
            self.assertEqual(c['msisdn'], self.NUMBER)
            self.assertEqual(c['msisdn_src'], 'sim')
            self.assertEqual(self.commands().count('AT+CNUM'), 1)
            self.assertNotIn('AT+SPPAURI?', self.commands())  # the SIM had one: IMS is not asked

    def test_the_ims_identity_when_the_sim_has_no_number(self):
        for i, shell in enumerate(self.each_shell()):
            self.fresh(i)
            c = self.collect(shell, 'full', cnum='+CNUM: "","",129\nOK', ims=self.IMS_URI, tag=i)
            self.assertEqual(c['msisdn'], '+8613800000000')
            self.assertEqual(c['msisdn_src'], 'ims')
            self.assertEqual(self.commands().count('AT+CNUM'), 1)
            self.assertEqual(self.commands().count('AT+SPPAURI?'), 1)

    def test_neither_has_one(self):
        for i, shell in enumerate(self.each_shell()):
            self.fresh(i)
            c = self.collect(shell, 'full', tag=i)
            self.assertIsNone(c['msisdn'])
            self.assertIsNone(c['msisdn_src'])
            # an empty answer is asked again on the next full round (IMS registers later than the SIM is read)
            c = self.collect(shell, 'full', ims=self.IMS_URI, tag=i)
            self.assertEqual(c['msisdn'], '+8613800000000')
            self.assertEqual(self.commands().count('AT+CNUM'), 2)
            self.assertEqual(self.commands().count('AT+SPPAURI?'), 2)

    def test_a_number_found_is_kept_for_the_boot(self):
        for i, shell in enumerate(self.each_shell()):
            self.fresh(i)
            self.collect(shell, 'full', cnum=self.SIM_CNUM, tag=i)
            c = self.collect(shell, 'full', cnum='ERROR', tag=i)   # not asked again, so the reply does not matter
            self.assertEqual(c['msisdn'], self.NUMBER)
            self.assertEqual(c['msisdn_src'], 'sim')
            self.assertEqual(self.commands().count('AT+CNUM'), 1)

    def test_never_in_the_fast_tier(self):
        for i, shell in enumerate(self.each_shell()):
            self.fresh(i)
            s = self.collect(shell, 'fast', cnum=self.SIM_CNUM, ims=self.IMS_URI, tag=i)
            self.assertNotIn('msisdn', s)
            self.assertNotIn('AT+CNUM', self.commands())
            self.assertNotIn('AT+SPPAURI', self.commands())

    def test_never_on_flash_never_in_a_log(self):
        for i, shell in enumerate(self.each_shell()):
            self.fresh(i)
            self.collect(shell, 'full', cnum=self.SIM_CNUM, tag=i)
            ident = self.tmp / f'ident-{i}'
            for f in ident.rglob('*'):                       # the 6 h identity cache (flash) has IMSI/ICCID, no number
                self.assertNotIn('5551234567', f.read_text(), f)
            run = self.tmp / f'run-{i}'
            holders = [f for f in run.rglob('*') if f.is_file() and '5551234567' in f.read_text()]
            self.assertTrue(holders)
            for f in holders:                                # the runtime caches are root's only (umask 077)
                self.assertEqual(f.stat().st_mode & 0o077, 0, f)
                self.assertIn(f.name, ('cell.json', 'own-number'), f)
            self.assertEqual(self.commands().count('5551234567'), 0)  # the stub logs the commands: none carries it
        dash = (TOP / 'openwrt' / 'luci-app-mu300' / 'root' / 'usr' / 'libexec' / 'rpcd' / 'mu300dash').read_text()
        self.assertIn('no IMSI or numbers', dash)           # the operations log's rule, unchanged

    def test_parsing(self):
        cases = [
            # (CNUM reply, IMS reply, number, source)
            ('+CNUM: "Line 1","05551234567",129\nOK', 'ERROR', '05551234567', 'sim'),        # national, as given
            ('+CNUM: "","905551234567",145\nOK', 'ERROR', '+905551234567', 'sim'),          # type 145: international
            ('+CNUM: "","+90 555 123-4567",145\nOK', 'ERROR', '+905551234567', 'sim'),      # spaces and dashes
            ('+CNUM: "","",129\nOK', '+SPPAURI: "tel:+905551234567"\nOK', '+905551234567', 'ims'),
            ('ERROR', '+SPPAURI: "sip:+905551234567@ims.mnc001.mcc286.3gppnetwork.org"\nOK', '+905551234567', 'ims'),
            ('ERROR', '+SPPAURI: "sip:286016012345678@ims.mnc001.mcc286.3gppnetwork.org"\nOK', None, None),  # an IMSI
            ('+CNUM: "","not a number",129\nOK', '+SPPAURI: "sip:x@y"\nOK', None, None),
            ('+CNUM: "","12345678901234567890",129\nOK', 'ERROR', None, None),              # longer than E.164
        ]
        for i, shell in enumerate(self.each_shell()):
            for j, (cnum, ims, number, source) in enumerate(cases):
                tag = f'{i}-{j}'
                self.fresh(tag)
                c = self.collect(shell, 'full', cnum=cnum, ims=ims, tag=tag)
                self.assertEqual((c['msisdn'], c['msisdn_src']), (number, source), (cnum, ims))


class OperatorNameHex(ShellTest):
    """Issue #174: a name the modem gives as UCS2 hex while its character set is not UCS2 (the F50's is "HEX") is
    decoded when the text cannot be a name; everything else stays the text the modem gave."""

    def setUp(self):
        super().setUp()
        self.stub('uci', 'exit 1')
        self.stub('fake-at', r"""printf '%s\n' "$*" >> "$STUBLOG/at-commands"
case "$*" in
  *'AT+CSCS?;+COPS=3,0;+COPS?;+COPS=3,2'*) printf '%s\n%s\nOK\n' "$CSCS_REPLY" "$NAMED_REPLY" ;;
  *AT+CFUN?*)  printf '+CFUN: 1\nOK\n' ;;
  *AT+COPS?*)  printf '%s\nOK\n' "$COPS_REPLY" ;;
  *AT+CSCS?*)  printf '%s\nOK\n' "$CSCS_REPLY" ;;
  *)           printf 'OK\n' ;;
esac""")

    def operator(self, shell, tag, cops, named='', cscs='HEX'):
        run = self.tmp / f'run-{tag}'
        r = self.script(shell, CELL, 'full', MU300_AT=self.stubs / 'fake-at', MU300_DASH_DIR=run,
                        MU300_DASH_IDENT_DIR=self.tmp / f'ident-{tag}', MU300_DASH_POOL_DIR=self.tmp / f'pool-{tag}',
                        COPS_REPLY=cops, NAMED_REPLY=named,
                        CSCS_REPLY='ERROR' if cscs is None else f'+CSCS: "{cscs}"')
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((run / 'cell.json').read_text())['operator']

    def test_the_reported_case(self):
        # PLMN 46001 in the numeric format, the long name as UCS2 hex, the character set "HEX"
        for i, shell in enumerate(self.each_shell()):
            for _ in range(2):      # the second round reads the kept line: the same name, nothing decoded is cached
                op = self.operator(shell, i, '+COPS: 0,2,"46001",7', '+COPS: 0,0,"4E2D56FD8054901A",7')
                self.assertEqual((op['name'], op['plmn'], op['act']), ('中国联通', '46001', 7))
            kept = (self.tmp / f'run-{i}' / 'operator').read_text()
            self.assertIn('4E2D56FD8054901A', kept)
            self.assertNotIn('中国联通', kept)
            asked = (self.tmp / 'at-commands').read_text().count('AT+CSCS?;+COPS=3,0;+COPS?;+COPS=3,2')
            self.assertEqual(asked, 1 + i)      # once per PLMN, whatever the number of rounds

    def test_decoded_whatever_the_character_set_says(self):
        cases = [
            # (COPS?, the long-name answer, CSCS (None: the modem did not say), name)
            ('+COPS: 0,2,"46000",7', '+COPS: 0,0,"4E2D56FD79FB52A8",7', 'GSM', '中国移动'),
            ('+COPS: 0,2,"46001",7', '+COPS: 0,0,"4E2D56FD8054901A",7', None, '中国联通'),
            ('+COPS: 0,0,"4E2D56FD75354FE1",7', '', 'IRA', '中国电信'),              # the name format, no named line
            ('+COPS: 0,0,"4e2d56fd8054901a",7', '', 'HEX', '中国联通'),              # lower case
            ('+COPS: 0,0,"0054002D004D006F00620069006C0065",7', '', 'IRA', 'T-Mobile'),
            ('+COPS: 0,0,"D83DDCF10041",7', '', 'HEX', '\U0001f4f1A'),               # a surrogate pair is one character
            ('+COPS: 0,0,"4E2D56FD8054901A",7', '', 'UCS2', '中国联通'),             # as before
            ('+COPS: 0,0,"00540054",7', '', 'UCS2', 'TT'),                           # as before: UCS2 said so
        ]
        for i, shell in enumerate(self.each_shell()):
            for j, (cops, named, cscs, name) in enumerate(cases):
                op = self.operator(shell, f'{i}-{j}', cops, named, cscs)
                self.assertEqual(op['name'], name, (cops, named, cscs))

    def test_an_ordinary_name_stays_what_it_is(self):
        names = [
            'vodafone TR', 'China Unicom', 'CAFE', 'BEEF', 'FACE',   # one code unit of hex is a word, not UCS2
            '12345678', '00540054', '4600146001460011',              # a name of digits stays a name
            'DEADBEEF',                                              # a low surrogate with no high one before it
            'D83D0041',                                              # a high surrogate with no low one after it
            '000A0041', '0041007F',                                  # control characters
            'E000E001', 'F8FF0041',                                  # private use
            'FFFE0041', '0041FFFF', 'FDD00041',                      # not characters
            '4E2D56FD80', '4E2D56FD805',                             # not whole code units
            '4E2D 56FD', '4E2D56FG',                                 # not hex
        ]
        for i, shell in enumerate(self.each_shell()):
            for j, name in enumerate(names):
                for cscs in ('HEX', 'GSM', None):
                    op = self.operator(shell, f'{i}-{j}-{cscs}', f'+COPS: 0,0,"{name}",7', '', cscs)
                    self.assertEqual(op['name'], name, cscs)
                # and as the long name of a numeric answer
                op = self.operator(shell, f'{i}-{j}-n', '+COPS: 0,2,"28602",13', f'+COPS: 0,0,"{name}",13')
                self.assertEqual((op['name'], op['plmn']), (name, '28602'))

    def test_no_name_leaves_the_plmn(self):
        # the page then names the operator from the PLMN (common.js carrierName)
        for i, shell in enumerate(self.each_shell()):
            op = self.operator(shell, i, '+COPS: 0,2,"46001",7', 'ERROR')
            self.assertEqual((op['name'], op['plmn']), (None, '46001'))


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed for the LuCI JS tests')
class OwnNumberRow(unittest.TestCase):
    """home.js's paintIdent alone: the row is there only with a number, masked like the IMSI until revealed."""

    def paint(self, cell, shown=False):
        src = HOME.read_text(encoding='utf-8')
        start = src.index('paintIdent: function(cell) {')
        start = src.index('function', start)
        end = src.index('\n\t},', start)
        js = r'''
const _ = (s) => s;
const out = {}, row = { style: { display: 'none' } };
const M = { set: (id, t) => { out[id] = t; }, v: (id) => id === 'msisdn-r' ? row : null };
const self = { identShown: ''' + json.dumps(shown) + r''', paintIdent: ''' + src[start:end] + '\n\t}' + r''' };
self.paintIdent(''' + json.dumps(cell) + r''');
process.stdout.write(JSON.stringify({ shown: row.style.display !== 'none', value: out.msisdn, imsi: out.imsi }));
'''
        r = subprocess.run(['node', '-'], input=js, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_the_row_is_in_the_page(self):
        src = HOME.read_text(encoding='utf-8')
        self.assertIn('id="mud-msisdn-r"', src)
        self.assertIn("_('Own number')", src)

    def test_with_a_number(self):
        t = self.paint({'ident': {'imsi': '286016012345678'}, 'msisdn': '+905551234567', 'msisdn_src': 'sim'})
        self.assertTrue(t['shown'])
        self.assertEqual(t['value'], '+905****567')
        self.assertEqual(t['imsi'], '2860****678')
        t = self.paint({'ident': {}, 'msisdn': '+905551234567'}, shown=True)
        self.assertEqual(t['value'], '+905551234567')

    def test_without_a_number_the_row_is_hidden(self):
        for cell in ({'ident': {'imsi': '286016012345678'}, 'msisdn': None, 'msisdn_src': None},
                     {'ident': {'imsi': '286016012345678'}}, None, {'error': 'AT backend did not answer'}):
            t = self.paint(cell)
            self.assertFalse(t['shown'], cell)
            self.assertEqual(t['value'], '--')


if __name__ == '__main__':
    unittest.main()
