"""The SC2355 driver's side of the WPA3-SAE hotspot: the firmware runs SAE (sc2355/sae.h hands it nl80211's SAE
password at START_AP, compiled here against stubs, with UBSan when MU300_TEST_UBSAN=1), the wiphy advertises the
offload, the firmware's PMK record reaches cfg80211 in the station's elements for hostapd, and the driver never logs
the passphrase, a station key or raw payloads."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

WLAN = TOP / 'kernel/modules/sprd_wlan_combo'

STUBS = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32; typedef uint16_t __le16;
#define __packed __attribute__((packed))
#define cpu_to_le16(x) ((u16)(x))
#define EOPNOTSUPP 95
#define EINVAL 22
#define ENOMEM 12
#define BIT(n) (1u << (n))
#define SPRD_EXTEND_SOATAP_WPA3 BIT(6)
#define SPRD_CAPA_AP_SME BIT(3)
#define SPRD_STD_11W BIT(6)
#define SPRD_SAE_PASSWORD_ENTRY 2
#define CMD_SET_SAE_PARAM 85
#define WLAN_AKM_SUITE_PSK 0x000FAC02
#define WLAN_AKM_SUITE_SAE 0x000FAC08
#define WLAN_AKM_SUITE_FT_OVER_SAE 0x000FAC09
enum { NL80211_SAE_PWE_UNSPECIFIED, NL80211_SAE_PWE_HUNT_AND_PECK, NL80211_SAE_PWE_HASH_TO_ELEMENT, NL80211_SAE_PWE_BOTH };
struct cfg80211_crypto_settings { int n_akm_suites; u32 akm_suites[10]; const u8 *sae_pwd; u8 sae_pwd_len; int sae_pwe; };
struct cfg80211_ap_settings { struct cfg80211_crypto_settings crypto; };
struct sprd_priv { u32 extend_feature, fw_capa, fw_std; };
struct sprd_vif { int dummy; };
struct sprd_msg { u8 data[256]; size_t len; };
static struct sprd_msg sent; static int nsent;
static struct sprd_msg *get_cmdbuf(struct sprd_priv *p, struct sprd_vif *v, size_t len, int cmd)
{ (void)p; (void)v; if (cmd != CMD_SET_SAE_PARAM || len > sizeof(sent.data)) abort(); memset(&sent, 0xaa, sizeof(sent)); sent.len = len; return &sent; }
static int send_cmd_recv_rsp(struct sprd_priv *p, struct sprd_msg *m, void *r, void *l)
{ (void)p; (void)m; (void)r; (void)l; nsent++; return 0; }
'''

MAIN = r'''
int main(void)
{
	char line[512];
	while (fgets(line, sizeof(line), stdin)) {
		unsigned ext, capa, std, pwe; int n; char akms[64], pwd[128];
		struct sprd_priv priv; struct sprd_vif vif; struct cfg80211_ap_settings s;
		if (sscanf(line, "%x %x %x %u %63s %127[^\n]", &ext, &capa, &std, &pwe, akms, pwd) < 5) continue;
		if (!strcmp(pwd, "-")) pwd[0] = 0;
		memset(&s, 0, sizeof(s));
		priv.extend_feature = ext; priv.fw_capa = capa; priv.fw_std = std;
		for (char *a = strtok(akms, ","); a; a = strtok(NULL, ","))
			s.crypto.akm_suites[s.crypto.n_akm_suites++] = (u32)strtoul(a, NULL, 16);
		s.crypto.sae_pwd = (const u8 *)pwd; s.crypto.sae_pwd_len = (u8)strlen(pwd); s.crypto.sae_pwe = (int)pwe;
		nsent = 0;
		n = sc2355_prepare_sae(&priv, &vif, &s);
		printf("%d %d", n, nsent);
		if (nsent) { printf(" "); for (size_t i = 0; i < sent.len; i++) printf("%02x", sent.data[i]); }
		printf("\n");
	}
	return 0;
}
'''

SAE, PSK, FT_SAE = '000fac08', '000fac02', '000fac09'
ALL = (0x40, 0x08, 0x40)     # SOFTAP_WPA3, AP_SME, 11W


class FirmwareSaePassword(unittest.TestCase):
    """sc2355/sae.h: the password goes to the firmware as Android's command 43 did, only when the firmware can do SAE"""

    def run_cases(self, cases):
        cc = shutil.which('cc')
        if not cc:
            self.skipTest('no native C compiler')
        work = Path(tempfile.mkdtemp(prefix='mu300-sae-'))
        self.addCleanup(shutil.rmtree, work, True)
        (work / 't.c').write_text(STUBS + (WLAN / 'sc2355/sae.h').read_text() + MAIN)
        flags = ['-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-O1']
        if os.environ.get('MU300_TEST_UBSAN') == '1':
            flags += ['-fsanitize=undefined,address', '-fno-sanitize-recover=all']
        r = subprocess.run([cc] + flags + [str(work / 't.c'), '-o', str(work / 't')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        stdin = ''.join('%x %x %x %d %s %s\n' % (*caps, pwe, ','.join(akms), pwd or '-') for caps, pwe, akms, pwd in cases)
        r = subprocess.run([str(work / 't')], input=stdin, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split('\n')[:len(cases)]

    def test_password_entry(self):
        pwd = 'correct horse'
        want = ('0200' '0000' '0000' '0700' + '%02x00' % len(pwd) + pwd.encode().hex() + 'ff000000')  # END: type 255, length 0
        out = self.run_cases([(ALL, 0, [SAE], pwd), (ALL, 1, [PSK, SAE], pwd), (ALL, 0, [SAE], 'x' * 63)])
        self.assertEqual(out[0], '0 1 ' + want)
        self.assertEqual(out[1], '0 1 ' + want)
        self.assertTrue(out[2].startswith('0 1 0200000000000700' + '3f00' + '78' * 63 + 'ff000000'))

    def test_refusals(self):
        out = self.run_cases([
            (ALL, 0, [PSK], 'secret12'),               # WPA2: nothing to do
            ((0, 0x08, 0x40), 0, [SAE], 'secret12'),   # no soft-AP WPA3 in the firmware
            ((0x40, 0, 0x40), 0, [SAE], 'secret12'),   # no AP SME
            ((0x40, 0x08, 0), 0, [SAE], 'secret12'),   # no PMF
            (ALL, 2, [SAE], 'secret12'),               # H2E
            (ALL, 3, [SAE], 'secret12'),               # H2E or hunting-and-pecking
            (ALL, 0, [FT_SAE, SAE], 'secret12'),       # FT over SAE
            (ALL, 0, [SAE], ''),                       # no password
            (ALL, 0, [SAE], 'x' * 64),                 # longer than 63
        ])
        self.assertEqual(out, ['0 0'] + ['-95 0'] * 6 + ['-22 0'] * 2)


class OffloadWiring(unittest.TestCase):
    def test_wiphy_start_ap_and_report(self):
        init = (WLAN / 'sc2355/init.c').read_text()
        self.assertIn('wiphy_ext_feature_set(wiphy, NL80211_EXT_FEATURE_SAE_OFFLOAD_AP);', init)
        self.assertIn('wiphy->max_num_akm_suites = 3;', init)
        cmd = (WLAN / 'sc2355/cmdevt.c').read_text()
        start_ap = cmd[cmd.index('int sc2355_start_ap('):]
        self.assertLess(start_ap.index('sc2355_prepare_sae(priv, vif, settings)'), start_ap.index('CMD_START_AP'))
        # a refused password fails START_AP
        self.assertIn('if (cmd_id == CMD_SET_SAE_PARAM &&', cmd)
        # hostapd never takes AUTH frames from the firmware's SME
        cfg = (WLAN / 'common/cfg80211.c').read_text()
        self.assertIn('frame_type == IEEE80211_STYPE_AUTH &&', cfg)
        # the firmware's PMK record reaches cfg80211 unchanged, and the vendor password command stays closed
        report = (WLAN / 'common/report.c').read_text()
        self.assertNotIn('40, 0x45, 0xda', report)
        vendor = (WLAN / 'sc2355/vendor.c').read_text()
        fn = vendor[vendor.index('static int vendor_set_sae_password('):]
        self.assertLess(fn.index('return -EOPNOTSUPP;'), fn.index('nla_for_each_attr'))


class DriverPrivacy(unittest.TestCase):
    """the driver never logs the passphrase, a station key or raw command/event payloads """

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
