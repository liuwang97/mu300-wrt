"""The SC2355 driver's side of a WPA2-PSK-only hotspot (moved here from the root filesystem tree\'s test_wpa2_only: they read this tree only): the firmware's PMK record never reaches cfg80211 - the strip function compiled from the source
(with UBSan when MU300_TEST_UBSAN=1) against records, malformed lists and random input; the vendor command that gave
the firmware the SAE password is refused; the driver never logs the passphrase, a station key or raw payloads."""
import os
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

WLAN = TOP / 'kernel/modules/sprd_wlan_combo'
RECORD_PREFIX = bytes([0xdd, 0x34, 0x40, 0x45, 0xda, 0x04])


def c_function(source, name):
    """the text of C function NAME in SOURCE, from its 'static' line to its closing brace"""
    start = source.index('static u16 ' + name + '(')
    i = source.index('{', start)
    depth = 0
    while True:
        if source[i] == '{':
            depth += 1
        elif source[i] == '}':
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
        i += 1


def added_lines(patch, path):
    """the '+' lines of the hunks of PATH in a unified diff"""
    out, on = [], False
    for line in patch.splitlines():
        if line.startswith('+++ '):
            on = line[4:].strip() == 'b/' + path
            continue
        if line.startswith('--- '):
            continue
        if on and line.startswith('+'):
            out.append(line[1:])
    return '\n'.join(out) + '\n'


def strip_model(ies):
    """what the driver must leave: the well-formed elements, in order, without any dd ..40:45:da:04 element"""
    out, i = b'', 0
    while i + 2 <= len(ies):
        n = ies[i + 1]
        if i + 2 + n > len(ies):
            break
        e = ies[i:i + 2 + n]
        if not (e[0] == 221 and n >= 4 and e[2:6] == RECORD_PREFIX[2:]):
            out += e
        i += 2 + n
    return out


class DriverStripsTheRecord(unittest.TestCase):
    """common/report.c: mu300_strip_sae_pmk_record before cfg80211_new_sta, compiled from the source."""

    def test_softap_report_strips_before_cfg80211(self):
        src = (WLAN / 'common/report.c').read_text()
        body = src[src.index('void sprd_report_softap('):src.index('EXPORT_SYMBOL(sprd_report_softap)')]
        self.assertLess(body.index('mu300_strip_sae_pmk_record(req_ie, req_ie_len)'), body.index('sinfo.assoc_req_ies = req_ie'))
        self.assertLess(body.index('sinfo.assoc_req_ies = req_ie'), body.index('cfg80211_new_sta('))
        for text in (src,):
            # the bytes of the record are never printed
            self.assertNotIn('hex_dump', body)
            self.assertIn('0x40, 0x45, 0xda, 0x04', text)

    def test_firmware_never_gets_an_sae_password(self):
        for text in ((WLAN / 'sc2355/vendor.c').read_text(),):
            fn = text[text.index('static int vendor_set_sae_password('):] if 'static int vendor_set_sae_password(' in text else text
            ret = fn.index('return -EOPNOTSUPP;')
            self.assertNotIn('nla_for_each_attr', fn[:ret])
            self.assertNotIn('send_cmd_recv_rsp', fn[:ret])

    def compile(self):
        cc = shutil.which('cc')
        if not cc:
            self.skipTest('no native C compiler')
        work = Path(tempfile.mkdtemp(prefix='mu300-strip-'))
        self.addCleanup(shutil.rmtree, work, True)
        fn = c_function((WLAN / 'common/report.c').read_text(), 'mu300_strip_sae_pmk_record')
        (work / 't.c').write_text('''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char u8;
typedef unsigned short u16;
''' + fn + '''
int main(int argc, char **argv)
{
	static u8 buf[65536];
	size_t n = fread(buf, 1, sizeof(buf), stdin);
	u16 len = (u16)n, out = mu300_strip_sae_pmk_record(buf, len);
	(void)argc; (void)argv;
	fwrite(buf, 1, out, stdout);
	return 0;
}
''')
        flags = ['-std=c99', '-Wall', '-Wextra', '-Werror', '-O1']
        if os.environ.get('MU300_TEST_UBSAN') == '1':
            flags += ['-fsanitize=undefined,address', '-fno-sanitize-recover=all']
        r = subprocess.run([cc] + flags + [str(work / 't.c'), '-o', str(work / 't')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return work / 't'

    def test_strip(self):
        exe = self.compile()

        def run(ies):
            r = subprocess.run([str(exe)], input=ies, capture_output=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout

        ssid = bytes([0, 4]) + b'MU30'
        rsn = bytes([48, 20, 1, 0, 0, 0x0f, 0xac, 4, 1, 0, 0, 0x0f, 0xac, 4, 1, 0, 0, 0x0f, 0xac, 2, 0, 0])
        wmm = bytes([221, 7, 0, 0x50, 0xf2, 2, 0, 1, 0])
        record = RECORD_PREFIX + bytes(range(48))
        other_vendor = bytes([221, 6, 0x40, 0x45, 0xda, 0x05, 1, 2])
        cases = {
            'no record': (ssid + rsn + wmm, ssid + rsn + wmm),
            'empty': (b'', b''),
            'firmware record at the end': (ssid + rsn + record, ssid + rsn),
            'a client record in the middle': (ssid + record + rsn + wmm, ssid + rsn + wmm),
            'two records': (record + ssid + record, ssid),
            'prefix only': (ssid + bytes([221, 4, 0x40, 0x45, 0xda, 0x04]), ssid),
            'any length with the prefix': (bytes([221, 10, 0x40, 0x45, 0xda, 0x04]) + bytes(6) + rsn, rsn),
            'another vendor type stays': (ssid + other_vendor, ssid + other_vendor),
            'too short for the prefix stays': (bytes([221, 3, 0x40, 0x45, 0xda]), bytes([221, 3, 0x40, 0x45, 0xda])),
            'a truncated record': (ssid + record[:30], ssid),
            'a record behind a malformed element': (ssid + bytes([7, 200]) + record, ssid),
            'one stray byte': (ssid + b'\xdd', ssid),
        }
        for name, (ies, want) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(run(ies), want)
                self.assertEqual(strip_model(ies), want)
        rnd = random.Random(80)
        for _ in range(400):
            parts = []
            for _ in range(rnd.randrange(0, 12)):
                k = rnd.randrange(5)
                if k == 0:
                    parts.append(record)
                elif k == 1:
                    parts.append(bytes([221, 4 + rnd.randrange(60)]) + RECORD_PREFIX[2:] + bytes(rnd.randrange(256) for _ in range(60)))
                else:
                    n = rnd.randrange(40)
                    parts.append(bytes([rnd.randrange(256), n]) + bytes(rnd.randrange(256) for _ in range(n)))
            ies = b''.join(parts)
            if rnd.random() < 0.3:
                ies = ies[:rnd.randrange(len(ies) + 1)]
            out = run(ies)
            self.assertEqual(out, strip_model(ies))
            self.assertEqual(strip_model(out), out)       # well-formed, nothing left to strip


class DriverPrivacy(unittest.TestCase):
    """the driver never logs the passphrase, a station key or raw command/event payloads (kept from the offload work)"""

    def body(self, path, name):
        source = (TOP / 'kernel/modules/sprd_wlan_combo' / path).read_text()
        start = source.index('{', source.index(name + '('))
        depth = 1
        end = start + 1
        while depth:
            if source[end] == '{': depth += 1
            if source[end] == '}': depth -= 1
            end += 1
        return source[start:end]

    def test_transport_command_payloads_never_reach_hex_dump(self):
        for path, name in [('sc2355/pcie.c', 'pcie_tx_one'),
                           ('sc2355/sdio.c', 'sdio_tx_one'),
                           ('sc2355/cmdevt.c', 'cmdevt_send_cmd'),
                           ('sc2355/cmdevt.c', 'sc2355_rx_rsp_process')]:
            with self.subTest(path=path, name=name):
                self.assertNotIn('hex_dump', self.body(path, name))

    def test_transport_rx_cannot_dump_a_new_station_pmk(self):
        for path, name in [('sc2355/pcie.c', 'sc2355_pcie_rx_work_queue'),
                           ('sc2355/sdio.c', 'sc2355_rx_work_queue')]:
            with self.subTest(path=path):
                self.assertNotIn('hex_dump', self.body(path, name))

    def test_station_key_is_not_logged_as_an_unbounded_string(self):
        source = (TOP / 'kernel/modules/sprd_wlan_combo/common/cfg80211.c').read_text()
        self.assertNotIn('"PSK %s', source)
        self.assertIn('memcpy(con.psk, sme->key, sme->key_len)', source)


if __name__ == '__main__':
    unittest.main()
