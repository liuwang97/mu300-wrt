"""Compile the actual metadata parser from the hostapd patch, with malformed IE input."""
import json
import random
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP, ShellTest

PATCH = TOP / 'openwrt/patches/hostapd/990-u30-sae-offload.patch'


class SaeMetadata(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shutil.which('cc')
        if not cc:
            raise unittest.SkipTest('no native C compiler')
        cls.work = tempfile.TemporaryDirectory(prefix='u30-sae-parser-')
        work = Path(cls.work.name)
        patch = PATCH.read_text()
        added = patch[patch.index('+++ b/src/ap/u30_sae_offload.h'):].splitlines()[2:]
        lines = []
        for line in added:
            if line.startswith('--- '): break
            if line.startswith('+'): lines.append(line[1:])
        (work / 'u30_sae_offload.h').write_text('\n'.join(lines) + '\n')
        (work / 'check.c').write_text(r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint8_t u8;
#define WLAN_EID_VENDOR_SPECIFIC 221
#define os_memcmp memcmp
#include "u30_sae_offload.h"
int main(void) {
    u8 bytes[4];
    if (u30_sae_get_key(NULL, 0)) return 2;
    while (fread(bytes, 1, 4, stdin) == 4) {
        size_t len = bytes[0] | ((uint32_t) bytes[1] << 8) |
            ((uint32_t) bytes[2] << 16) | ((uint32_t) bytes[3] << 24);
        u8 *buf;
        const u8 *key;
        if (len > 65535) return 3;
        buf = malloc(len ? len : 1);
        if (!buf || fread(buf, 1, len, stdin) != len) return 4;
        key = u30_sae_get_key(buf, len);
        printf("%ld\n", key ? (long) (key - buf) : -1L);
        free(buf);
    }
    return 0;
}
''')
        cls.exe = work / 'check'
        # Sanitizers are optional on desktop hosts whose compiler has no runtime library.
        # CI can require them with MU300_TEST_UBSAN=1.
        probe = subprocess.run([cc, '-fsanitize=undefined', '-x', 'c', '-', '-o', str(work / 'probe')],
                               input='int main(void) { return 0; }', capture_output=True, text=True)
        if os.environ.get('MU300_TEST_UBSAN') == '1' and probe.returncode:
            raise AssertionError('UBSan was required but its compiler/runtime is unavailable: ' + probe.stderr)
        flags = ['-fsanitize=undefined', '-fno-sanitize-recover=all'] if not probe.returncode else []
        cls.sanitizer_flags = flags
        r = subprocess.run([cc, '-std=c99', '-Wall', '-Wextra', '-Werror'] + flags + [
                            str(work / 'check.c'), '-o', str(cls.exe)], capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def parse(self, records):
        data = b''.join(struct.pack('<I', len(x)) + x for x in records)
        r = subprocess.run([str(self.exe)], input=data, capture_output=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        return [int(x) for x in r.stdout.splitlines()]

    def test_only_one_complete_trailing_private_element(self):
        key = bytes([221, 52]) + bytes.fromhex('4045da04') + bytes(range(48))
        prefix = bytes([0, 3]) + b'abc'
        records = [b'', b'\xdd', key, prefix + key, key[:-1], key + b'\x00',
                   key + prefix, key + key, key[:5] + b'\x05' + key[6:],
                   b'\x00\xff' + key, prefix]
        self.assertEqual(self.parse(records), [-1, -1, 6, 11, -1, -1, -1, -1, -1, -1, -1])

    def test_client_marker_of_any_length_blocks_the_trailer(self):
        # A client copy of the private OUI/type must not hide beside the firmware trailer
        # by using a length the key check would skip.
        key = bytes([221, 52]) + bytes.fromhex('4045da04') + bytes(range(48))
        short = bytes([221, 4]) + bytes.fromhex('4045da04')
        other_type = bytes([221, 4]) + bytes.fromhex('4045da05')
        records = [short + key, key + short, bytes([221, 10]) + bytes.fromhex('4045da04') + bytes(6) + key,
                   bytes([221, 60]) + bytes.fromhex('4045da04') + bytes(56) + key, other_type + key,
                   bytes([221, 3]) + bytes.fromhex('4045da') + key]
        self.assertEqual(self.parse(records), [-1, -1, -1, -1, 12, 11])

    def test_every_truncation_and_random_malformed_inputs(self):
        key = bytes([221, 52]) + bytes.fromhex('4045da04') + bytes(range(48))
        rng = random.Random(2355)
        records = [key[:n] for n in range(54)]
        records += [rng.randbytes(rng.randrange(0, 2048)) for _ in range(500)]
        self.assertEqual(self.parse(records), [-1] * len(records))

    def test_configuration_rejects_multiple_bss_before_driver_setup(self):
        added = '\n'.join(line[1:] for line in PATCH.read_text().splitlines()
                          if line.startswith('+') and not line.startswith('+++'))
        start = added.index('static int hostapd_config_check_u30_sae(')
        helper = added[start:added.index('\n}\n', start) + 3]
        source = Path(self.work.name) / 'config.c'
        source.write_text(r"""
#include <stddef.h>
#include <assert.h>
#define WPA_KEY_MGMT_SAE 1024
#define MGMT_FRAME_PROTECTION_REQUIRED 2
#define MSG_ERROR 4
#define wpa_printf(...) ((void) 0)
struct hostapd_bss_config {
    int u30_sae_offload, wpa_key_mgmt, ieee80211w, sae_pwe, disable_pmksa_caching;
    void *sae_passwords;
};
struct hostapd_config { size_t num_bss; struct hostapd_bss_config **bss; };
""" + helper + r"""
int main(void) {
    struct hostapd_bss_config first = {0}, second = {0};
    struct hostapd_bss_config *bss[] = { &first, &second };
    struct hostapd_config conf = { 2, bss };
    /* The untouched option-off path permits the existing configuration. */
    assert(hostapd_config_check_u30_sae(&conf, 1) == 0);
    /* Either BSS opting in rejects the whole unsupported configuration. */
    first.u30_sae_offload = 1;
    assert(hostapd_config_check_u30_sae(&conf, 1) < 0);
    assert(hostapd_config_check_u30_sae(&conf, 0) < 0);
    first.u30_sae_offload = 0; second.u30_sae_offload = 1;
    assert(hostapd_config_check_u30_sae(&conf, 1) < 0);
    assert(hostapd_config_check_u30_sae(&conf, 0) < 0);
    conf.num_bss = 1; first.u30_sae_offload = 1;
    first.wpa_key_mgmt = WPA_KEY_MGMT_SAE; first.ieee80211w = 2;
    assert(hostapd_config_check_u30_sae(&conf, 1) == 0);
    first.wpa_key_mgmt |= 1; assert(hostapd_config_check_u30_sae(&conf, 1) < 0);
    first.wpa_key_mgmt = WPA_KEY_MGMT_SAE;
    first.ieee80211w = 1; assert(hostapd_config_check_u30_sae(&conf, 1) < 0);
    first.ieee80211w = 2;
    first.sae_pwe = 2; assert(hostapd_config_check_u30_sae(&conf, 1) < 0);
    first.sae_pwe = 0;
    first.disable_pmksa_caching = 1;
    assert(hostapd_config_check_u30_sae(&conf, 1) < 0);
    first.disable_pmksa_caching = 0;
    first.sae_passwords = &first;
    assert(hostapd_config_check_u30_sae(&conf, 1) < 0);
    return 0;
}
""")
        exe = source.with_suffix('')
        result = subprocess.run([shutil.which('cc'), '-std=c99', '-Wall', '-Wextra',
                                 '-Werror'] + self.sanitizer_flags + [
                                 str(source), '-o', str(exe)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_firmware_request_gates_and_vendor_contract(self):
        # Run the actual firmware helper with fake driver/crypto calls; all credentials are synthetic.
        added = '\n'.join(line[1:] for line in PATCH.read_text().splitlines()
                          if line.startswith('+') and not line.startswith('+++'))
        start = added.index('static int u30_sae_configure_fw(')
        helper = added[start:added.index('\n}\n', start) + 3]
        source = Path(self.work.name) / 'firmware.c'
        source.write_text(r'''
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <assert.h>
typedef uint8_t u8;
#define WPA_KEY_MGMT_SAE 1024
#define MGMT_FRAME_PROTECTION_REQUIRED 2
#define WPA_DRIVER_FLAGS2_AP_SME 1
#define NESTED_ATTR_USED 1
#define MSG_INFO 3
#define MSG_ERROR 4
#define os_strlen strlen
#define os_memcmp_const memcmp
#define os_memcpy memcpy
#define os_memset memset
#define wpa_printf(...) ((void) 0)
struct conf {
    struct { const char *wpa_passphrase; } ssid;
    int u30_sae_offload, wpa_key_mgmt, ieee80211w, sae_pwe, disable_pmksa_caching;
    void *sae_passwords;
};
struct iface { unsigned long drv_flags2; };
struct hostapd_data {
    struct conf *conf;
    struct iface *iface;
    int u30_sae_configured;
    u8 u30_sae_key_fingerprint[32];
};
static u8 saved[84];
static size_t saved_len;
static int calls, result;
static void put(u8 *p, uint32_t n, size_t len) {
    size_t i; for (i=0; i<len; i++) p[i] = n >> (8*i);
}
#define WPA_PUT_LE16(p,n) put((p),(n),2)
#define WPA_PUT_LE32(p,n) put((p),(n),4)
static void forced_memzero(void *p, size_t n) { memset(p, 0, n); }
static int sha256_vector(size_t n, const u8 **p, const size_t *len, u8 *out) {
    size_t i; (void) n; memset(out, 0, 32);
    for (i=0; i<len[0]; i++) out[i%32] ^= p[0][i];
    return 0;
}
static int hostapd_drv_vendor_cmd(struct hostapd_data *h, unsigned int oui,
    unsigned int cmd, u8 *data, size_t len, int nested, void *reply) {
    (void) h; (void) reply;
    assert(oui == 0x001374 && cmd == 43 && nested == 1);
    assert(len <= sizeof(saved));
    memcpy(saved, data, len); saved_len=len; calls++;
    return result;
}
''' + helper + r'''
int main(void) {
    struct conf c = {0}; struct iface i = {0};
    struct hostapd_data h = { .conf=&c, .iface=&i };
    size_t n, group;
    assert(u30_sae_configure_fw(&h) == 0 && calls == 0);
    c.u30_sae_offload=1; c.wpa_key_mgmt=WPA_KEY_MGMT_SAE;
    c.ieee80211w=2; c.ssid.wpa_passphrase="synthetic-test-password";
    assert(u30_sae_configure_fw(&h) == 0 && calls == 1 && h.u30_sae_configured);
    n=strlen(c.ssid.wpa_passphrase); group=(n+7)&~(size_t)3;
    assert(saved[0] == n+4 && saved[1] == 0 && saved[2] == 8 && saved[3] == 0);
    assert(memcmp(saved+4, c.ssid.wpa_passphrase, n) == 0);
    assert(saved[group] == 8 && saved[group+2] == 6 && saved[group+4] == 19);
    assert(saved[group+8] == 8 && saved[group+10] == 7);
    assert(memcmp(saved+group+12, "\xff\xff\xff\xff", 4) == 0 && saved_len == group+16);
    assert(u30_sae_configure_fw(&h) == 0 && calls == 1);
    c.disable_pmksa_caching=1; assert(u30_sae_configure_fw(&h) < 0 && calls == 1); c.disable_pmksa_caching=0;
    c.sae_pwe=2; assert(u30_sae_configure_fw(&h) < 0 && calls == 1); c.sae_pwe=0;
    c.ieee80211w=1; assert(u30_sae_configure_fw(&h) < 0 && calls == 1); c.ieee80211w=2;
    c.wpa_key_mgmt |= 1; assert(u30_sae_configure_fw(&h) < 0 && calls == 1); c.wpa_key_mgmt=WPA_KEY_MGMT_SAE;
    i.drv_flags2=1; assert(u30_sae_configure_fw(&h) < 0 && calls == 1); i.drv_flags2=0;
    c.ssid.wpa_passphrase="short"; assert(u30_sae_configure_fw(&h) < 0 && calls == 1);
    c.ssid.wpa_passphrase="another-synthetic-password"; result=-1;
    assert(u30_sae_configure_fw(&h) < 0 && calls == 2 && !h.u30_sae_configured);
    result=0; assert(u30_sae_configure_fw(&h) == 0 && calls == 3 && h.u30_sae_configured);
    return 0;
}
''')
        exe = source.with_suffix('')
        r = subprocess.run([shutil.which('cc'), '-std=c99', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(exe)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


UCI = r"""
[ "$1" = -q ] && shift
f=$STUBLOG/uci.state
case "$1" in
    show) cat "$f" ;;
    get) v=$(sed -n "s/^$2='\(.*\)'$/\1/p" "$f" | head -n1); [ -n "$v" ] || exit 1; echo "$v"; exit 0 ;;
    set) k=${2%%=*}; grep -v "^$k=" "$f" > "$f.n"; echo "$k='${2#*=}'" >> "$f.n"; mv "$f.n" "$f" ;;
    delete) grep -v "^$2=" "$f" > "$f.n"; mv "$f.n" "$f" ;;
    del_list) k=${2%%=*}; sed -e "/^$k=/s/ *'${2#*=}'//" -e "/^$k=\$/d" "$f" > "$f.n"; mv "$f.n" "$f" ;;
    commit) ;;
esac
[ "$1" = show ] || echo "$*" >> "$STUBLOG/uci.log"
exit 0
"""
# what `uci show wireless` prints for the documented opt-in (docs/U30AIR-SAE.md), with a synthetic passphrase
SAE_STATE = """wireless.radio0=wifi-device
wireless.radio0.band='5g'
wireless.ap0=wifi-iface
wireless.ap0.device='radio0'
wireless.ap0.mode='ap'
wireless.ap0.ifname='wlan0'
wireless.ap0.ssid='MU300-TEST'
wireless.ap0.encryption='sae'
wireless.ap0.key='synthetic-test-password'
wireless.ap0.ieee80211w='2'
wireless.ap0.sae_pwe='0'
wireless.ap0.hostapd_bss_options='max_listen_interval=10' 'u30_sae_offload=1'
"""
WPA2_STATE = """wireless.radio0=wifi-device
wireless.ap0=wifi-iface
wireless.ap0.device='radio0'
wireless.ap0.mode='ap'
wireless.ap0.ifname='wlan0'
wireless.ap0.encryption='psk2'
wireless.ap0.key='synthetic-test-password'
"""


class SaeGuard(ShellTest):
    """openwrt-wifi-sae-guard: a hotspot that cannot start with the offload goes back to WPA2; nothing else moves."""
    GUARD = BIN / 'openwrt-wifi-sae-guard'

    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        for d in ('etc/mu300', 'etc/config', 'etc/init.d', 'usr/sbin', 'run/mu300/power'):
            (self.root / d).mkdir(parents=True)
        (self.root / 'etc/openwrt_release').write_text('DISTRIB_ID=OpenWrt\n')
        (self.root / 'etc/config/wireless').write_text('the file as it was\n')
        wpad = self.root / 'etc/init.d/wpad'
        wpad.write_text('#!/bin/sh\necho "wpad $*" >> "$STUBLOG/wifi.log"\n')
        wpad.chmod(0o755)
        self.hostapd(patched=True)
        self.stub('uci', UCI)
        # the access point is up from the Nth question on ($STUBLOG/ap-after), never without that file
        self.stub('ubus', 'echo "$*" >> "$STUBLOG/ubus.log"\n'
                          '[ "$(wc -l < "$STUBLOG/ubus.log")" -ge "$(cat "$STUBLOG/ap-after" 2>/dev/null || echo 9999)" ]'
                          ' || exit 4\nprintf \'{\\n\\t"status": "ENABLED",\\n\\t"freq": 5180\\n}\\n\'')
        self.stub('wifi', 'echo "wifi $*" >> "$STUBLOG/wifi.log"')
        self.stub('logger', 'echo "$*" >> "$STUBLOG/logger.log"')

    def hostapd(self, patched):
        # the option's name is in the binary that parses it; the stock wpad has no such string
        (self.root / 'usr/sbin/hostapd').write_bytes(
            b'\x7fELF\x00sae_pwe\x00' + (b'u30_sae_offload\x00' if patched else b'') + b'sae_confirm_immediate\x00')

    def run_guard(self, shell, mode, state, up_after=None):
        (self.tmp / 'uci.state').write_text(state)
        for f in ('uci.log', 'ubus.log', 'wifi.log', 'logger.log', 'ap-after'):
            (self.tmp / f).unlink(missing_ok=True)
        (self.root / 'etc/mu300/wireless.sae-offload').unlink(missing_ok=True)
        if up_after:
            (self.tmp / 'ap-after').write_text(str(up_after))
        # two more looks, a second apart (busybox ash runs its own sleep, so a stub would not be asked)
        r = self.script(shell, self.GUARD, mode, MU300_SYSROOT=self.root, MU300_SAE_WAIT=2, MU300_SAE_STEP=1)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def log(self, name):
        p = self.tmp / name
        return p.read_text().splitlines() if p.exists() else []

    def assertUntouched(self, r, state):
        self.assertEqual(r.stderr, '')
        self.assertEqual((self.tmp / 'uci.state').read_text(), state)
        self.assertEqual([c for c in self.log('uci.log') if not c.startswith('get ')], [])
        self.assertEqual(self.log('wifi.log') + self.log('logger.log'), [])
        self.assertFalse((self.root / 'etc/mu300/wireless.sae-offload').exists())

    def assertWpa2(self, r, reason):
        state = (self.tmp / 'uci.state').read_text()
        self.assertIn("wireless.ap0.encryption='psk2'\n", state)
        self.assertIn("wireless.ap0.key='synthetic-test-password'\n", state)
        # only this option leaves the list
        self.assertIn("wireless.ap0.hostapd_bss_options='max_listen_interval=10'\n", state)
        for gone in ('u30_sae_offload', 'ieee80211w', 'sae_pwe'):
            self.assertNotIn(gone, state)
        self.assertEqual(self.log('uci.log')[-1], 'commit wireless')
        self.assertIn(reason, r.stderr)
        self.assertIn('back on WPA2 with the same password', r.stderr)
        self.assertNotIn('synthetic-test-password', r.stderr + '\n'.join(self.log('logger.log')))
        self.assertEqual(len(self.log('logger.log')), 1)
        self.assertIn('-p daemon.err', self.log('logger.log')[0])
        saved = self.root / 'etc/mu300/wireless.sae-offload'
        self.assertEqual(saved.read_text(), 'the file as it was\n')
        self.assertEqual(saved.stat().st_mode & 0o077, 0)

    def test_without_the_option_nothing_is_asked_or_changed(self):
        for shell in self.each_shell():
            for mode in ('precheck', 'verify'):
                self.hostapd(patched=False)
                r = self.run_guard(shell, mode, WPA2_STATE)
                self.assertUntouched(r, WPA2_STATE)
                self.assertEqual(self.log('uci.log') + self.log('ubus.log'), [])

    def test_not_openwrt(self):
        (self.root / 'etc/openwrt_release').unlink()
        self.hostapd(patched=False)
        for shell in self.each_shell():
            self.assertUntouched(self.run_guard(shell, 'precheck', SAE_STATE), SAE_STATE)

    def test_precheck_keeps_a_hostapd_that_has_the_option(self):
        for shell in self.each_shell():
            self.assertUntouched(self.run_guard(shell, 'precheck', SAE_STATE), SAE_STATE)

    def test_precheck_stock_hostapd_after_an_update(self):
        self.hostapd(patched=False)
        for shell in self.each_shell():
            r = self.run_guard(shell, 'precheck', SAE_STATE)
            self.assertWpa2(r, 'the installed hostapd does not have it')
            # mu300-hw brings Wi-Fi up right after; the client mode's disabled access point is repaired too
            self.assertEqual(self.log('wifi.log'), [])
            r = self.run_guard(shell, 'precheck', SAE_STATE + "wireless.ap0.disabled='1'\n")
            self.assertWpa2(r, 'the installed hostapd does not have it')

    def test_verify_access_point_up(self):
        for shell in self.each_shell():
            self.assertUntouched(self.run_guard(shell, 'verify', SAE_STATE, up_after=1), SAE_STATE)
            self.assertEqual(self.log('ubus.log'), ['-t 5 call hostapd.wlan0 get_status'])
            # automatic channel selection takes half a minute: up on a later look is up
            self.assertUntouched(self.run_guard(shell, 'verify', SAE_STATE, up_after=3), SAE_STATE)
            self.assertEqual(len(self.log('ubus.log')), 3)

    def test_verify_access_point_never_up(self):
        for shell in self.each_shell():
            r = self.run_guard(shell, 'verify', SAE_STATE)
            self.assertWpa2(r, 'the access point did not start')
            self.assertEqual(len(self.log('ubus.log')), 3)
            self.assertEqual(self.log('wifi.log'), ['wifi down', 'wpad restart', 'wifi up'])

    def test_verify_leaves_a_hotspot_that_is_off_on_purpose(self):
        for shell in self.each_shell():
            for state in (SAE_STATE + "wireless.ap0.disabled='1'\n", SAE_STATE + "wireless.radio0.disabled='1'\n",
                          SAE_STATE.replace("mode='ap'", "mode='sta'")):
                self.assertUntouched(self.run_guard(shell, 'verify', state), state)
            for marker in ('run/mu300-wifi-client.active', 'run/mu300-wifi-scan-mode', 'run/mu300/power/hotspot-off'):
                (self.root / marker).write_text('')
                self.assertUntouched(self.run_guard(shell, 'verify', SAE_STATE), SAE_STATE)
                (self.root / marker).unlink()

    def test_boot_calls_it_before_wifi_up_and_after_the_retry(self):
        hw = (TOP / 'openwrt/overlay/etc/init.d/mu300-hw').read_text()
        self.assertLess(hw.index('openwrt-wifi-config'), hw.index('openwrt-wifi-sae-guard precheck'))
        self.assertLess(hw.index('openwrt-wifi-sae-guard precheck'), hw.index('wifi down'))
        post = (TOP / 'openwrt/overlay/etc/init.d/mu300-post').read_text()
        self.assertLess(post.index("'AP not up, retrying'; wifi up"), post.index('openwrt-wifi-sae-guard verify'))


@unittest.skipUnless(os.environ.get('MU300_WRT_IMAGE') and shutil.which('docker'),
                     'MU300_WRT_IMAGE (an OpenWrt 25.12 arm64 docker image) not set')
class SaeWifiScripts(unittest.TestCase):
    """OpenWrt's wifi-scripts for a fake wiphy (as test_wifi_channels): the hostapd configuration of the documented
    opt-in, and of what openwrt-wifi-sae-guard leaves behind."""

    def test_hostapd_config_of_the_opt_in_and_of_the_fallback(self):
        import test_wifi_channels as channels
        fake = channels.WifiScriptsInOpenWrt
        opt_in = {'encryption': 'sae', 'ieee80211w': '2', 'sae_pwe': '0',
                  'hostapd_bss_options': ['u30_sae_offload=1']}
        cases = {'stock': {}, 'opt-in': opt_in, 'fallback': {'encryption': 'psk2', 'hostapd_bss_options': []}}
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'cases').mkdir()
            (d / 'out').mkdir()
            for name, iface in cases.items():
                data = fake.data(36, 'VHT80')
                data['interfaces']['ap0']['config'].update(iface)
                (d / 'cases' / f'{name}.wiphy.json').write_text(json.dumps(fake.wiphy(False, {})))
                (d / 'cases' / f'{name}.data.json').write_text(json.dumps(data))
            script = r"""
mkdir -p /var/lock /var/run /tmp /fake/wifi
apk update >/dev/null && apk add wifi-scripts >/dev/null 2>&1 || exit 1
sed -e "s/^export function wiphy_info(phy) {/function nl_wiphy_info(phy) {/" /usr/share/ucode/wifi/common.uc > /fake/wifi/common.uc
grep -q "^function nl_wiphy_info" /fake/wifi/common.uc || exit 1
echo "export function wiphy_info(phy) { return json(fs.readfile(getenv(\"FAKE_WIPHY\"))); };" >> /fake/wifi/common.uc
for f in /t/cases/*.data.json; do
    n=$(basename "$f" .data.json)
    FAKE_DATA=$f FAKE_WIPHY=/t/cases/$n.wiphy.json FAKE_CC=CN \
        ucode -L "/fake/*.uc" -L "/usr/share/ucode/*.uc" /fx/run.uc > /t/out/$n.conf 2>&1 || echo "rc=$?" >> /t/out/$n.conf
done
"""
            r = subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/arm64',
                                '-v', f'{channels.FIXTURES}:/fx:ro', '-v', f'{d}:/t',
                                os.environ['MU300_WRT_IMAGE'], '/bin/sh', '-c', script],
                               capture_output=True, text=True, timeout=900)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

            def conf(name):
                text = (d / 'out' / f'{name}.conf').read_text()
                self.assertNotIn('rc=', text, name)
                return dict(l.split('=', 1) for l in text.splitlines() if '=' in l and not l.startswith('#'))

            c = conf('opt-in')
            self.assertEqual((c['wpa_key_mgmt'], c['ieee80211w'], c['sae_pwe'], c['u30_sae_offload']),
                             ('SAE', '2', '0', '1'))
            # the patch reads the passphrase from wpa_passphrase and refuses a BSS with sae_password entries
            self.assertEqual(c['wpa_passphrase'], '12345678')
            self.assertNotIn('sae_password', c)
            # the hotspot of the images, and the guard's fallback: the same WPA2 configuration, no key of the offload
            stock, back = conf('stock'), conf('fallback')
            self.assertEqual(stock, back)
            self.assertEqual(back['wpa_key_mgmt'], 'WPA-PSK')
            self.assertNotIn('u30_sae_offload', back)
            self.assertNotIn('sae_pwe', back)


class DriverPrivacy(unittest.TestCase):
    def body(self, path, name):
        source = (TOP / 'upstream/modules/sprd_wlan_combo' / path).read_text()
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
        source = (TOP / 'upstream/modules/sprd_wlan_combo/common/cfg80211.c').read_text()
        self.assertNotIn('"PSK %s', source)
        self.assertIn('memcpy(con.psk, sme->key, sme->key_len)', source)


class SaeSdkBuild(ShellTest):
    def setUp(self):
        super().setUp()
        self.sdk = self.tmp / 'sdk'
        self.package = self.sdk / 'package/network/services/hostapd'
        self.package.mkdir(parents=True)
        (self.package / 'Makefile').write_text(
            'PKG_SOURCE_VERSION:=ca266cc24d8705eb1a2a0857ad326e48b1408b20\nPKG_RELEASE:=5\n')
        self.stub('make', '''echo "$*" >> "$STUBLOG/calls"
case "$*" in *hostapd/compile*)
    mkdir -p bin/packages/aarch64_generic/base
    for name in hostapd-common wpad-basic-openssl; do
        echo fixture > "bin/packages/aarch64_generic/base/$name-fixture-r9.apk"
    done ;;
esac''')

    def build(self):
        return self.script(['sh'], TOP / 'openwrt/build-u30-sae.sh', self.sdk, self.tmp / 'out')

    def test_recipe_revision_and_matching_package_pair(self):
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('PKG_RELEASE:=9', (self.package / 'Makefile').read_text())
        self.assertEqual((self.package / 'patches' / PATCH.name).read_bytes(), PATCH.read_bytes())
        self.assertEqual(sorted(p.name for p in (self.tmp / 'out').glob('*.apk')),
                         ['hostapd-common-fixture-r9.apk', 'wpad-basic-openssl-fixture-r9.apk'])
        self.assertEqual((self.tmp / 'calls').read_text().splitlines(),
                         ['defconfig', 'package/network/services/hostapd/clean',
                          '-j4 package/network/services/hostapd/compile V=s'])
        self.assertEqual(self.build().returncode, 0)

    def test_different_source_is_rejected_before_mutation(self):
        recipe = self.package / 'Makefile'
        recipe.write_text('PKG_SOURCE_VERSION:=unsupported\nPKG_RELEASE:=5\n')
        self.assertNotEqual(self.build().returncode, 0)
        self.assertFalse((self.package / 'patches').exists())
        self.assertFalse((self.tmp / 'calls').exists())
        self.assertFalse((self.sdk / '.config').exists())

    def test_official_sdk_feed_layout_and_revision(self):
        feed = self.sdk / 'feeds/base/network/services/hostapd'
        feed.parent.mkdir(parents=True)
        self.package.rename(feed)
        recipe = feed / 'Makefile'
        recipe.write_text(recipe.read_text().replace('PKG_RELEASE:=5', 'PKG_RELEASE:=1'))
        link = self.sdk / 'package/feeds/base/hostapd'
        link.parent.mkdir(parents=True)
        link.symlink_to(feed, target_is_directory=True)
        r = self.build()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('PKG_RELEASE:=9', recipe.read_text())
        self.assertEqual((feed / 'patches' / PATCH.name).read_bytes(), PATCH.read_bytes())
        self.assertIn('package/feeds/base/hostapd/compile', (self.tmp / 'calls').read_text())

    def test_missing_feed_gives_setup_instructions_without_mutation(self):
        (self.package / 'Makefile').unlink()
        r = self.build()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('scripts/feeds install -p base hostapd', r.stderr)
        self.assertFalse((self.sdk / '.config').exists())
        self.assertFalse((self.tmp / 'calls').exists())

    def test_unrecorded_revision_is_rejected(self):
        recipe = self.package / 'Makefile'
        original = recipe.read_text().replace('PKG_RELEASE:=5', 'PKG_RELEASE:=2')
        recipe.write_text(original)
        self.assertNotEqual(self.build().returncode, 0)
        self.assertEqual(recipe.read_text(), original)
        self.assertFalse((self.sdk / '.config').exists())

    def test_conflicting_local_patch_is_not_overwritten(self):
        target = self.package / 'patches' / PATCH.name
        target.parent.mkdir()
        target.write_text('existing private changes')
        self.assertNotEqual(self.build().returncode, 0)
        self.assertEqual(target.read_text(), 'existing private changes')
        self.assertFalse((self.sdk / '.config').exists())

    def test_old_package_artifacts_are_not_delivered_as_r9(self):
        packages = self.sdk / 'bin/packages/aarch64_generic/base'
        packages.mkdir(parents=True)
        for name in ('hostapd-common', 'wpad-basic-openssl'):
            for revision in (6, 7, 8):
                (packages / (name + '-fixture-r' + str(revision) + '.apk')).write_text('old build')
        result = self.build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sorted(p.name for p in (self.tmp / 'out').glob('*.apk')),
                         ['hostapd-common-fixture-r9.apk', 'wpad-basic-openssl-fixture-r9.apk'])

    def test_previous_local_revision_requires_a_clean_sdk(self):
        recipe = self.package / 'Makefile'
        base = recipe.read_text()
        target = self.package / 'patches' / PATCH.name
        target.parent.mkdir()
        target.write_bytes(PATCH.read_bytes())
        for revision in (6, 7, 8):
            with self.subTest(revision=revision):
                recipe.write_text(base.replace('PKG_RELEASE:=5', 'PKG_RELEASE:=' + str(revision)))
                original = recipe.read_bytes()
                result = self.build()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(recipe.read_bytes(), original)
                self.assertFalse((self.sdk / '.config').exists())
                self.assertFalse((self.tmp / 'calls').exists())


if __name__ == '__main__':
    unittest.main()
