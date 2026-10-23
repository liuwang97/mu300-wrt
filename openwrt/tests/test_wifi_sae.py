"""The hotspot's WPA3-SAE (openwrt/hostapd): the SC2355 firmware does SAE and hands the PMK back in the association
IEs (dd 34 40:45:da:04 <PMK 32> <PMKID 16>); the rebuilt hostapd imports it and runs the four-way handshake.

* hostapd-unisoc-sae.h, compiled against stand-ins for hostapd (with ASan/UBSan where the compiler has them, always
  with MU300_TEST_SANITIZE=1): the import, every truncated record, a duplicate, PMF, PMKSA reuse, WPA2 after SAE,
  an interface without unisoc_sae;
* the AP options for the SC2355 (hostapd-unisoc.sh under each shell, hostapd-unisoc.uc under ucode): PMF for the mode,
  hunting-and-pecking, no RSN Override, nothing for another driver;
* patch-hostapd-sae.py on stand-ins of the sources it edits: idempotent, refuses a source it does not know, SAE only;
* wifi-sae-state.py: the build id and the check of an APK directory;
* the image installs the three APKs (openwrt/build-rootfs.sh) and the release audits them (tools/make-release.sh);
  nothing of the WPA2-only hotspot is left;
* with MU300_WRT_IMAGE and MU300_WIFI_SAE_APKS: the hostapd configuration the SAE build's wifi-scripts write.
"""
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import GIT_CHECKOUT, TOP, ShellTest, ucode

H = TOP / 'openwrt' / 'hostapd'
# wifi-sae-state.py, loaded as a module (no bytecode written into the build's directory)
_bytecode, sys.dont_write_bytecode = sys.dont_write_bytecode, True
_spec = importlib.util.spec_from_file_location('wifi_sae_state', H / 'wifi-sae-state.py')
state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state)
sys.dont_write_bytecode = _bytecode

HOST = r'''
#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
typedef unsigned char u8;
#define CONFIG_SAE 1
#define SAE_PMK_LEN 32
#define PMKID_LEN 16
#define WPA_KEY_MGMT_SAE 8
#define WPA_KEY_MGMT_PSK 2
#define WPA_CAPABILITY_MFPC 128
#define WPA_DRIVER_FLAGS2_SAE_OFFLOAD_AP 1
#define WLAN_EID_RSN 48
#define WLAN_AUTH_OPEN 0
#define WLAN_AUTH_SAE 3
#define SAE_ACCEPTED 3
#define os_memcpy memcpy
static int alloc_fail, cache_fail, cache_valid, added, alg, pmkid_set;
static void *os_zalloc(size_t n){return alloc_fail?NULL:calloc(1,n);}
static void bin_clear_free(void *p,size_t n){memset(p,0,n);free(p);}
struct sae_data { u8 pmk[64],pmkid[16];size_t pmk_len;int akmp,state; };
struct wpa_ie_data { int key_mgmt,capabilities,num_pmkid;const u8 *pmkid; };
static struct wpa_ie_data parsed={8,128,0,NULL};
struct rsn_pmksa_cache_entry { int akmp; size_t pmk_len;u8 pmk[32],pmkid[16]; };
static struct rsn_pmksa_cache_entry cache;
struct configuration { int unisoc_sae,wpa_key_mgmt,disable_pmksa_caching; };
struct interface { int drv_flags2; };
struct hostapd_data { struct configuration *conf;struct interface *iface;void *wpa_auth; };
struct sta_info { u8 addr[6];struct sae_data *sae;int auth_alg,vlan_id;void *wpa_sm; };
static const u8 *get_ie(const u8 *p,size_t len,int id) {
 for(size_t i=0;i+2<=len;) {size_t n=p[i+1]+2;if(n>len-i)return NULL;if(p[i]==id)return p+i;i+=n;}return NULL;
}
static const u8 *get_vendor_ie(const u8 *p,size_t len,uint32_t id) {
 for(size_t i=0;i+2<=len;) {size_t n=p[i+1]+2;if(n>len-i)return NULL;
 if(p[i]==221 && n>=6 && (((uint32_t)p[i+2]<<24)|((uint32_t)p[i+3]<<16)|((uint32_t)p[i+4]<<8)|p[i+5])==id)return p+i;i+=n;}return NULL;
}
static int wpa_parse_wpa_ie_rsn(const u8 *p,size_t len,struct wpa_ie_data *out){*out=parsed;return 0;}
static struct rsn_pmksa_cache_entry *wpa_auth_pmksa_get(void *a,const u8 *s,const u8 *id){return cache_valid&&!memcmp(id,cache.pmkid,16)?&cache:NULL;}
static int wpa_auth_pmksa_add_sae(void *a,const u8 *s,const u8 *pmk,size_t len,const u8 *id,int akm,bool ml,int vlan) {
 if(cache_fail)return -1;assert(len==32 && akm==8 && !ml);memcpy(cache.pmk,pmk,32);memcpy(cache.pmkid,id,16);
 cache.pmk_len=len;cache.akmp=akm;cache_valid=1;added++;return 0;
}
static void sae_clear_data(struct sae_data *p){}
static void wpa_auth_set_auth_alg(void *s,int n){alg=n;}
static void wpa_auth_add_sae_pmkid(void *s,const u8 *id){pmkid_set++;}
#include "host.h"
static void clear(struct sta_info *s){if(s->sae){bin_clear_free(s->sae,sizeof(*s->sae));s->sae=NULL;}}
int main(void) {
 struct configuration c={1,8,0};struct interface f={1};struct hostapd_data h={&c,&f,NULL};struct sta_info sta={0};
 u8 ie[128]={48,0,221,52,0x40,0x45,0xda,4};for(int i=0;i<48;i++)ie[8+i]=(u8)(i+1);
 /* the firmware's record after the RSN element: PMK and PMKID taken, the station marked SAE */
 assert(unisoc_sae_import(&h,&sta,ie,56)==0 && sta.auth_alg==3 && alg==3 && added==1 && pmkid_set==1);
 assert(sta.sae->pmk_len==32 && !memcmp(sta.sae->pmk,ie+8,32) && !memcmp(sta.sae->pmkid,ie+40,16));
 /* a PMKSA reassociation without a record: only a cached SAE entry for that PMKID */
 parsed.num_pmkid=1;parsed.pmkid=ie+40;
 assert(unisoc_sae_import(&h,&sta,ie,2)==0);
 cache_valid=0;assert(unisoc_sae_import(&h,&sta,ie,2)<0);
 parsed.num_pmkid=0;clear(&sta);
 /* every truncation, a wrong length, two records */
 for(int n=0;n<54;n++)assert(unisoc_sae_import(&h,&sta,ie,2+n)<0);
 ie[3]=51;assert(unisoc_sae_import(&h,&sta,ie,56)<0);ie[3]=52;
 memcpy(ie+56,ie+2,54);assert(unisoc_sae_import(&h,&sta,ie,110)<0);
 /* SAE without PMF, a driver without SAE offload, no memory, a cache that refuses */
 parsed.capabilities=0;assert(unisoc_sae_import(&h,&sta,ie,56)<0);parsed.capabilities=128;
 f.drv_flags2=0;assert(unisoc_sae_import(&h,&sta,ie,56)<0);f.drv_flags2=1;
 alloc_fail=1;assert(unisoc_sae_import(&h,&sta,ie,56)<0);alloc_fail=0;
 cache_fail=1;assert(unisoc_sae_import(&h,&sta,ie,56)<0);cache_fail=0;
 c.disable_pmksa_caching=1;int before=added;
 assert(unisoc_sae_import(&h,&sta,ie,56)==0 && added==before);
 /* a record on a WPA2 association supplies no key, and clears the SAE state of an earlier association */
 parsed.key_mgmt=2;assert(unisoc_sae_import(&h,&sta,ie,56)==0 && !sta.sae && sta.auth_alg==0 && alg==0 && added==before);
 /* an interface without unisoc_sae ignores the record */
 parsed.key_mgmt=8;c.unisoc_sae=0;assert(unisoc_sae_import(&h,&sta,ie,56)==0 && !sta.sae);
 clear(&sta);
 return 0;
}
'''


def compiler():
    return shutil.which(os.environ.get('CC', 'cc')) or shutil.which('gcc') or shutil.which('clang')


@unittest.skipUnless(compiler(), 'no C compiler')
class FirmwarePmkImport(unittest.TestCase):
    def test_import(self):
        cc = compiler()
        with tempfile.TemporaryDirectory(prefix='mu300-sae-') as d:
            d = Path(d)
            (d / 'host.h').write_bytes((H / 'hostapd-unisoc-sae.h').read_bytes())
            (d / 'pmksa_cache_auth.h').write_text('/* the cache API is stood in for in host.c */\n')
            (d / 'host.c').write_text(HOST)
            base = [cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-Wno-misleading-indentation', '-g', str(d / 'host.c'), '-o', str(d / 'host')]
            sanitized = subprocess.run(base + ['-fsanitize=address,undefined', '-fno-sanitize-recover=all'],
                                       capture_output=True, text=True)
            if sanitized.returncode:
                # MU300_TEST_SANITIZE=1 (CI): the sanitizers or nothing
                self.assertFalse(os.environ.get('MU300_TEST_SANITIZE'), sanitized.stderr)
                r = subprocess.run(base, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
            r = subprocess.run([str(d / 'host')], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


POLICY_SH = r'''set -eu
. "$1"
auth_type="$2"; ieee80211w="$3"; sae_pwe="$4"; test_module="$5"; rsn_override=1
phy=phy0; bss_conf=; N='|'
readlink() { printf '%s\n' "$test_module"; }
append() { bss_conf="${bss_conf}${2}|"; }
set_default() { eval "case \"\${$1-}\" in '') $1=\"\$2\" ;; esac"; }
unisoc_sae_bss_options
printf '%s/%s/%s/%s' "$ieee80211w" "$sae_pwe" "$rsn_override" "$bss_conf"
'''


class ShellPolicy(ShellTest):
    """hostapd_set_bss_options() of the shell generator calls unisoc_sae_bss_options"""

    def test_cases(self):
        (self.tmp / 'policy.sh').write_text(POLICY_SH)
        cases = [('sae', '0', '', 'sprd_wlan_combo', '2/0/0/unisoc_sae=1|'),
                 ('sae', '1', '', 'sprd_wlan_combo', '2/0/0/unisoc_sae=1|'),
                 ('sae', '', '', 'sprd_wlan_combo', '/0/0/unisoc_sae=1|'),
                 ('psk-sae', '0', '', 'sprd_wlan_combo', '1/0/0/unisoc_sae=1|'),
                 ('psk-sae', '2', '1', 'sprd_wlan_combo', '2/1/0/unisoc_sae=1|'),
                 ('psk', '0', '', 'sprd_wlan_combo', '0//1/'),
                 ('sae-missing', '', '', 'sprd_wlan_combo', '//1/'),
                 ('sae', '0', '', 'mt76', '2//1/'),
                 ('psk-sae', '0', '', 'mt76', '1//1/')]
        for shell in self.each_shell():
            for auth, pmf, pwe, module, want in cases:
                r = subprocess.run(shell + [str(self.tmp / 'policy.sh'), str(H / 'hostapd-unisoc.sh'), auth, pmf, pwe,
                                            '/sys/bus/pci/drivers/' + module], capture_output=True, text=True,
                                   env=self.env(), timeout=30)
                self.assertEqual((r.returncode, r.stdout), (0, want), (auth, pmf, module, r.stderr))


POLICY_UC = r'''
import { configure } from './hostapd-unisoc.uc';

function check(c, module, pmf, pwe, enabled) {
	configure(c, module);
	if (c.ieee80211w != pmf || c.sae_pwe != pwe || c.unisoc_sae != enabled)
		die('SAE policy mismatch: ' + sprintf('%J', c));
	if (enabled && (c.rsn_override != 0 || c.rsn_override_pairwise != null || c.rsn_override_key_mgmt != null))
		die('RSN Override advertised: ' + sprintf('%J', c));
}
const native = '/sys/module/sprd_wlan_combo';
check({auth_type: 'sae', ieee80211w: 0}, native, 2, 0, 1);
check({auth_type: 'sae', ieee80211w: 1}, native, 2, 0, 1);
check({auth_type: 'sae'}, native, 2, 0, 1);
check({auth_type: 'psk-sae', ieee80211w: 0}, native, 1, 0, 1);
check({auth_type: 'psk-sae', rsn_override: 1, rsn_override_pairwise: 'GCMP-256'}, native, 1, 0, 1);
check({auth_type: 'psk-sae', ieee80211w: 2, sae_pwe: 1}, native, 2, 1, 1);
check({auth_type: 'psk', ieee80211w: 0}, native, 0, null, null);
check({auth_type: 'sae', ieee80211w: 0}, '/sys/module/mt76', 2, null, null);
check({auth_type: 'psk-sae', ieee80211w: 0}, null, 1, null, null);
print('PASS\n');
'''


class UcodePolicy(unittest.TestCase):
    """the ucode AP generator (ap.uc) calls configure() of hostapd-unisoc.uc (installed as wifi/unisoc-sae.uc)"""

    def test_cases(self):
        u = ucode()
        if not u:
            self.skipTest('no ucode interpreter (MU300_TEST_UCODE)')
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(H / 'hostapd-unisoc.uc', d)
            (Path(d) / 'policy.uc').write_text(POLICY_UC)
            r = subprocess.run(u + [str(Path(d) / 'policy.uc')], capture_output=True, text=True, timeout=30)
            self.assertEqual((r.returncode, r.stdout), (0, 'PASS\n'), r.stderr)

    def test_module_path(self):
        uc = (H / 'hostapd-unisoc.uc').read_text()
        self.assertIn("if (module != '/sys/module/sprd_wlan_combo')", uc)
        self.assertIn("config.sae_pwe ??= 0;", uc)


# stand-ins of the files patch-hostapd-sae.py edits: the anchor it looks for, once each
SOURCES = {
    'src/ap/ap_config.h': 'struct hostapd_bss_config {\n\tint sae_require_mfp;\n};\n',
    'hostapd/config_file.c': 'if (0) {\n\t} else if (os_strcmp(buf, "sae_require_mfp") == 0) {\n}\n',
    'src/ap/drv_callbacks.c': '#include "nan_usd_ap.h"\n\nvoid f(void) {\n'
                              '\t\twpa_auth_set_rsn_selection(sta->wpa_sm, elems.rsn_selection,\n}\n',
    'src/drivers/driver_nl80211_event.c': 'wpa_hexdump(MSG_DEBUG, "nl80211: Assoc Req IEs", a, b);\n',
    'src/ap/beacon.c': 'int g(void) {\n\t/* If SAE offload is enabled, provide password to lower layer for\n}\n',
}
WIFI = {
    'files/lib/netifd/hostapd.sh': 'x() {\n}\n\nhostapd_set_bss_options() {\n\tset_default uapsd 1\n'
                                   '\t[ -n "$spp_amsdu" ] && append bss_conf "spp_amsdu=$spp_amsdu" "$N"\n}\n',
    'files-ucode/usr/share/ucode/wifi/ap.uc': "import * as fs from 'fs';\nimport * as iface from 'wifi.iface';\n\n"
                                              "export function generate(interface, data, config) {\n"
                                              "\tconfig.wmm_enabled = 1;\n\tiface_auth_type(config);\n}\n",
}
RELOAD = {'hostapd.uc': 'function bss_reload() {\n\tif (is_equal(old_config.bss, config.bss))\n\t\treturn true;\n}\n'}


class PatchScript(unittest.TestCase):
    def run_patch(self, kind, root):
        return subprocess.run([sys.executable, str(H / 'patch-hostapd-sae.py'), kind, str(root)],
                              capture_output=True, text=True, timeout=60)

    def tree(self, d, files):
        for name, text in files.items():
            p = Path(d) / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

    def snapshot(self, d):
        return {str(p.relative_to(d)): p.read_bytes() for p in sorted(Path(d).rglob('*')) if p.is_file()}

    def test_source(self):
        with tempfile.TemporaryDirectory() as d:
            self.tree(d, SOURCES)
            r = self.run_patch('source', d)
            self.assertEqual(r.returncode, 0, r.stderr)
            once = self.snapshot(d)
            # idempotent: a second run changes nothing
            self.assertEqual(self.run_patch('source', d).returncode, 0)
            self.assertEqual(self.snapshot(d), once)
            d = Path(d)
            self.assertEqual((d / 'src/ap/unisoc_sae.h').read_bytes(), (H / 'hostapd-unisoc-sae.h').read_bytes())
            self.assertIn('#include "unisoc_sae.h"', (d / 'src/ap/drv_callbacks.c').read_text())
            cb = (d / 'src/ap/drv_callbacks.c').read_text()
            self.assertLess(cb.index('unisoc_sae_import(hapd, sta, req_ies, req_ies_len) < 0'),
                            cb.index('wpa_auth_set_rsn_selection'))
            self.assertIn('bss->unisoc_sae = atoi(pos);', (d / 'hostapd/config_file.c').read_text())
            # the association IEs carry the PMK: never printed in the clear
            self.assertIn('wpa_hexdump_key(MSG_DEBUG, "nl80211: Assoc Req IEs",',
                          (d / 'src/drivers/driver_nl80211_event.c').read_text())
            beacon = (d / 'src/ap/beacon.c').read_text()
            self.assertIn('SAE_PWE_HUNT_AND_PECK', beacon)
            self.assertIn('sae_groups[0] != 19', beacon)

    def test_wifi_scripts(self):
        with tempfile.TemporaryDirectory() as d:
            self.tree(d, WIFI)
            r = self.run_patch('wifi', Path(d) / 'files')
            self.assertEqual(r.returncode, 0, r.stderr)
            once = self.snapshot(d)
            self.assertEqual(self.run_patch('wifi', Path(d) / 'files').returncode, 0)
            self.assertEqual(self.snapshot(d), once)
            d = Path(d)
            sh = (d / 'files/lib/netifd/hostapd.sh').read_text()
            self.assertIn('. /lib/netifd/hostapd-unisoc.sh\n\nhostapd_set_bss_options() {', sh)
            self.assertIn('\n\tunisoc_sae_bss_options\n', sh)
            ap = (d / 'files-ucode/usr/share/ucode/wifi/ap.uc').read_text()
            self.assertIn("import { configure as unisoc_sae_config } from 'wifi.unisoc-sae';", ap)
            self.assertLess(ap.index('unisoc_sae_config(config, fs.realpath('), ap.index('\tiface_auth_type(config);'))
            # SAE only: U-APSD and WMM as OpenWrt has them
            self.assertIn('\tset_default uapsd 1\n', sh)
            self.assertNotIn('uapsd', ap)
            self.assertEqual((d / 'files/lib/netifd/hostapd-unisoc.sh').read_bytes(),
                             (H / 'hostapd-unisoc.sh').read_bytes())
            self.assertEqual((d / 'files-ucode/usr/share/ucode/wifi/unisoc-sae.uc').read_bytes(),
                             (H / 'hostapd-unisoc.uc').read_bytes())

    def test_reload(self):
        with tempfile.TemporaryDirectory() as d:
            self.tree(d, RELOAD)
            self.assertEqual(self.run_patch('reload', d).returncode, 0)
            self.assertEqual(self.run_patch('reload', d).returncode, 0)
            text = (Path(d) / 'hostapd.uc').read_text()
            self.assertEqual(text.count("if (line == 'unisoc_sae=1')"), 1)

    def test_an_unknown_source_is_refused(self):
        for kind, files, root in (('source', SOURCES, ''), ('wifi', WIFI, 'files'), ('reload', RELOAD, '')):
            for name in files:
                with self.subTest(kind=kind, file=name), tempfile.TemporaryDirectory() as d:
                    self.tree(d, {**files, name: files[name].replace('\t', '    ').replace('(', '( ')})
                    r = self.run_patch(kind, Path(d) / root)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn('Unexpected hostapd source', r.stderr)


class ApkState(unittest.TestCase):
    def apks(self, d, extra=None):
        d = Path(d)
        names = ['wpad-basic-mbedtls-2025.08.26~ca266cc2-r1001.apk', 'hostapd-common-2025.08.26~ca266cc2-r1001.apk',
                 'wifi-scripts-1.0-r1001.apk'] + (extra or [])
        for n in names:
            (d / n).write_bytes(n.encode())
        (d / 'SHA256SUMS').write_text(''.join(f'{hashlib.sha256(n.encode()).hexdigest()}  ./{n}\n' for n in names))
        (d / 'build-id').write_text(state.build_id() + '\n')

    def check(self, d):
        return subprocess.run([sys.executable, str(H / 'wifi-sae-state.py'), str(d)], capture_output=True, text=True)

    def test_build_id(self):
        r = subprocess.run([sys.executable, str(H / 'wifi-sae-state.py'), '--build-id'], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), state.build_id())
        want = hashlib.sha256(b''.join((H / n).read_bytes() for n in state.SOURCES)).hexdigest()
        self.assertEqual(state.build_id(), want)
        # every file of the build is in the id, and nothing else is there
        self.assertEqual(set(state.SOURCES), {p.name for p in H.iterdir() if p.is_file()})

    def test_check(self):
        with tempfile.TemporaryDirectory() as d:
            self.apks(d)
            r = self.check(d)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, state.build_id()), r.stderr)
            (Path(d) / 'build-id').write_text('0' * 64 + '\n')
            self.assertIn('stale', self.check(d).stderr)
        with tempfile.TemporaryDirectory() as d:
            self.apks(d)
            (Path(d) / 'wifi-scripts-1.0-r1001.apk').write_bytes(b'changed')
            self.assertIn('checksum mismatch', self.check(d).stderr)
        with tempfile.TemporaryDirectory() as d:
            self.apks(d)
            (Path(d) / 'wpad-basic-openssl-1-r1.apk').write_bytes(b'x')
            self.assertIn('unexpected extra APKs', self.check(d).stderr)
        with tempfile.TemporaryDirectory() as d:
            self.apks(d, ['wifi-scripts-1.0-r1.apk'])
            self.assertIn('unexpected APK', self.check(d).stderr)

    @unittest.skipUnless(os.environ.get('MU300_WIFI_SAE_APKS'), 'MU300_WIFI_SAE_APKS (openwrt/hostapd) not set')
    def test_built_apks(self):
        r = self.check(os.environ['MU300_WIFI_SAE_APKS'])
        self.assertEqual(r.returncode, 0, r.stderr)


class Wiring(unittest.TestCase):
    def test_build_scripts(self):
        for f in ('build-hostapd.sh', 'build-hostapd-inner.sh', 'hostapd-unisoc.sh'):
            r = subprocess.run(['sh', '-n', str(H / f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f + r.stderr)
        b = (H / 'build-hostapd.sh').read_text()
        self.assertIn('stamp=$(python3 "$H/wifi-sae-state.py" --build-id)', b)
        self.assertIn('${MU300_HOSTAPD_CACHE:-mu300-openwrt-src}:/build', b)
        inner = (H / 'build-hostapd-inner.sh').read_text()
        self.assertIn("sed -i 's/^PKG_RELEASE:=.*/PKG_RELEASE:=1001/'", inner)
        self.assertIn("'CONFIG_PACKAGE_wpad-basic-mbedtls=m' 'CONFIG_PACKAGE_hostapd-common=m'", inner)
        # the source patch is a patch of the package (a compile that prepares the source again keeps it), and what
        # was built is checked for it
        self.assertIn('SAEPATCH=$H/patches/990-mu300-unisoc-sae.patch', inner)
        self.assertLess(inner.index('> "$SAEPATCH"'), inner.index('hostapd_make -j"$JOBS" "$H/compile" V=s'))
        self.assertGreater(inner.index("grep -aq unisoc_sae \"$wpad\""), inner.index('"$H/compile"'))
        # the build id goes last, after every APK and SHA256SUMS
        self.assertGreater(inner.index('mv "$stage/build-id" /out/build-id'), inner.index('cp "$stage/"*.apk'))

    def test_image_installs_the_sae_build(self):
        build = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.assertIn('HOSTAPD_BUILD=$TOP/openwrt/hostapd/build-hostapd.sh', build)
        self.assertIn('MU300_WRT_VER="$VER" sh "$HOSTAPD_BUILD" "$WIFI_APKS"', build)
        # after the checks that need no network, before the image
        self.assertLess(build.index('theme package missing'), build.index('sh "$HOSTAPD_BUILD"'))
        self.assertLess(build.index('sh "$HOSTAPD_BUILD"'), build.index('docker run --rm --platform linux/arm64 \\\n'))
        self.assertIn('-v "$WIFI_APKS":/in/wifi-sae:ro', build)
        add = 'apk add --allow-untrusted /in/wifi-sae/*.apk >/dev/null'
        self.assertIn(add, build)
        # first, so no package of the feed brings its own wpad or wifi-scripts; the feed's never asked for by name
        self.assertLess(build.index(add), build.index('apk add iwinfo wireless-regdb'))
        self.assertGreater(build.index(add), build.index('apk upgrade >/dev/null'))
        self.assertNotRegex(build, r'apk add [^\n]*\bwpad-basic-mbedtls\b')
        self.assertNotRegex(build, r'apk add [^\n]*\bwifi-scripts\b')
        self.assertIn('grep -q -- "-r1001 "', build)
        self.assertIn('cp /in/wifi-sae/build-id $R/etc/mu300/wifi-sae.build-id', build)
        self.assertIn('grep -q "unisoc_sae_config(config" $R/usr/share/ucode/wifi/ap.uc', build)

    def test_release_audits_it(self):
        mr = (TOP / 'tools' / 'make-release.sh').read_text()
        for f in ('usr/share/ucode/wifi/unisoc-sae.uc', 'lib/netifd/hostapd-unisoc.sh', 'etc/mu300/wifi-sae.build-id'):
            self.assertIn(f, mr)
        self.assertIn('python3 "$TOP/openwrt/hostapd/wifi-sae-state.py" --build-id', mr)
        self.assertIn('grep -q "^$p-[^ ]*-r1001 "', mr)

    @unittest.skipUnless(GIT_CHECKOUT, 'not a git checkout')
    def test_nothing_of_wpa2_only_is_left(self):
        pattern = 'wpa2-only|wpa2_only|wpa3-disabled|sae_refused|05_mu300_wpa2|wpa2-notice|u30_sae'
        r = subprocess.run(['git', '-C', str(TOP), 'grep', '-lE', pattern, '--', 'openwrt', 'tools', '.github',
                            ':!openwrt/tests/test_wifi_sae.py'], capture_output=True, text=True)
        self.assertIn(r.returncode, (0, 1), r.stderr)
        # only files git tracks are searched: a removed file still in the index shows here until it is committed
        self.assertEqual([f for f in r.stdout.split() if (TOP / f).exists()], [])
        for gone in ('openwrt/patches/wifi-scripts-wpa2-only.patch', 'openwrt/opt/mu300/bin/openwrt-wifi-wpa2-only',
                     'openwrt/overlay/www/luci-static/resources/view/status/include/05_mu300_wpa2.js',
                     'openwrt/overlay/usr/share/rpcd/acl.d/mu300-wpa2-notice.json'):
            self.assertFalse((TOP / gone).exists(), gone)


@unittest.skipUnless(os.environ.get('MU300_WRT_IMAGE') and os.environ.get('MU300_WIFI_SAE_APKS') and
                     shutil.which('docker'), 'MU300_WRT_IMAGE and MU300_WIFI_SAE_APKS not set')
class SaeWifiScriptsInOpenWrt(unittest.TestCase):
    """The SAE build's wifi-scripts for a fake SC2355 (and another driver): the hostapd configuration they write."""

    def test_hostapd_config(self):
        import test_wifi_channels as channels
        fake = channels.WifiScriptsInOpenWrt
        cases = {}
        for name, iface in (('psk2', {'encryption': 'psk2+ccmp'}), ('sae', {'encryption': 'sae'}),
                            ('sae-mixed', {'encryption': 'sae-mixed'}),
                            ('sae-pmf0', {'encryption': 'sae', 'ieee80211w': '0'}),
                            ('mixed-pmf0', {'encryption': 'sae-mixed', 'ieee80211w': '0'})):
            data = fake.data(36, 'VHT80')
            data['interfaces']['ap0']['config'].update(iface)
            cases[name] = (fake.wiphy(False, {}), data)
        apks = os.environ['MU300_WIFI_SAE_APKS']
        ours = fake.generate(cases, apks, 'sprd_wlan_combo')
        other = fake.generate(cases, apks, 'mt76')

        def conf(out, n):
            self.assertNotIn('rc=', out[n], n + ': ' + out[n])
            return fake.conf(out[n])

        c = conf(ours, 'psk2')
        self.assertEqual((c['wpa_key_mgmt'], c.get('ieee80211w', '0')), ('WPA-PSK', '0'))
        self.assertNotIn('unisoc_sae', c)
        # mixed: three AKMs, which the driver takes (max_num_akm_suites 3) and hands the firmware with SAE among them
        mixed = {'WPA-PSK', 'WPA-PSK-SHA256', 'SAE'}
        for n, akm, pmf in (('sae', {'SAE'}, '2'), ('sae-pmf0', {'SAE'}, '2'),
                            ('sae-mixed', mixed, '1'), ('mixed-pmf0', mixed, '1')):
            c = conf(ours, n)
            self.assertEqual((c['unisoc_sae'], c['ieee80211w'], c['sae_pwe']), ('1', pmf, '0'), n)
            self.assertEqual(set(c['wpa_key_mgmt'].split()), akm, n)
            self.assertNotIn('rsn_override_key_mgmt', c, n)
            self.assertNotIn('sae_groups', c, n)
            # another driver: OpenWrt's own SAE
            c = conf(other, n)
            self.assertNotIn('unisoc_sae', c, n)


if __name__ == '__main__':
    unittest.main()
