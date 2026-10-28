"""The hotspot's on/off on OpenWrt (/opt/mu300/lib/hotspot-ap.sh, kanoqwq's db4e5d2 hotspot.uc): the access point's own
wifi-iface, the radio only when it was off and nothing else would come up with it, never while the wireless
configuration has unsaved changes (uci's or a LuCI session's), one toggle at a time, "pending" since netifd applies it
later, never a key in the output. The panel's Wi-Fi button (unisoc-modem/action wifi) and the Wi-Fi key held
(mu300-sysd) both go through it."""
import json
import unittest

from helpers import BIN, TOP, ShellTest

LIB = TOP / 'openwrt/opt/mu300/lib/hotspot-ap.sh'
ACTION = TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/action'

# uci over a JSON file: "committed" (option -> value, section -> type) and "staged" (uci's own deltas)
FAKE_UCI = r'''
import json, os, sys
db_path = os.path.join(os.environ['STUBLOG'], 'uci.json')
db = json.load(open(db_path))
args = [a for a in sys.argv[1:] if a != '-q']
with open(os.path.join(os.environ['STUBLOG'], 'uci.log'), 'a') as f:
    f.write(' '.join(args) + '\n')
cmd, rest = args[0], args[1:]
view = dict(db['committed']); view.update(db['staged'])
def save():
    json.dump(db, open(db_path, 'w'))
if cmd == 'get':
    if rest[0] in view:
        print(view[rest[0]]); sys.exit(0)
    sys.exit(1)
if cmd == 'show':
    pre = rest[0] + '.'
    for k, v in view.items():
        if k.startswith(pre):
            print(f"{k}={v}" if k.count('.') == 1 else f"{k}='{v}'")
    sys.exit(0)
if cmd == 'set':
    if os.path.exists(os.path.join(os.environ['STUBLOG'], 'uci.setfail')):
        sys.exit(1)
    k, v = rest[0].split('=', 1)
    db['staged'][k] = v; save(); sys.exit(0)
if cmd == 'changes':
    for k, v in db['staged'].items():
        if k.startswith(rest[0] + '.'):
            print(f"{k}='{v}'")
    sys.exit(0)
if cmd == 'commit':
    for k in [k for k in db['staged'] if k.startswith(rest[0] + '.')]:
        db['committed'][k] = db['staged'].pop(k)
    save(); sys.exit(0)
if cmd == 'revert':
    for k in [k for k in db['staged'] if k.startswith(rest[0] + '.')]:
        db['staged'].pop(k)
    save(); sys.exit(0)
sys.exit(1)
'''


def wireless(**extra):
    w = {'wireless.radio0': 'wifi-device', 'wireless.radio0.band': '5g',
         'wireless.ap0': 'wifi-iface', 'wireless.ap0.device': 'radio0', 'wireless.ap0.mode': 'ap',
         'wireless.ap0.ifname': 'wlan0', 'wireless.ap0.ssid': 'MU300', 'wireless.ap0.key': 'secret-psk-123'}
    w.update(extra)
    return {k: v for k, v in w.items() if v is not None}


class HotspotAp(ShellTest):
    def setUp(self):
        super().setUp()
        (self.stubs / 'uci.py').write_text(FAKE_UCI)
        self.stub('uci', f'exec python3 "{self.stubs}/uci.py" "$@"')
        self.stub('ubus', 'echo "$*" >> "$STUBLOG/ubus.log"; [ ! -e "$STUBLOG/ubus.fail" ]')
        self.rpcd = self.tmp / 'rpcd'
        self.rpcd.mkdir()
        self.run_dir = self.tmp / 'run'
        self.run_dir.mkdir()

    def db(self, committed, staged=None):
        (self.tmp / 'uci.json').write_text(json.dumps({'committed': committed, 'staged': staged or {}}))
        for f in ('ubus.log', 'uci.log'):
            (self.tmp / f).unlink(missing_ok=True)

    def committed(self):
        return json.loads((self.tmp / 'uci.json').read_text())['committed']

    def env_(self):
        return dict(MU300_HOTSPOT_RPCD=self.rpcd, MU300_HOTSPOT_LOCK=self.tmp / 'lock', MU300_RUN=self.run_dir)

    def set_(self, shell, state):
        r = self.sh(shell, f'. "{LIB}"; hotspot_ap_set {state}; echo "rc=$?"', **self.env_())
        self.assertEqual(r.stderr, '')
        word, rc = r.stdout.split()
        return word, rc

    def reloads(self):
        p = self.tmp / 'ubus.log'
        return p.read_text().count('call network reload') if p.exists() else 0

    def test_off_and_on_toggle_the_access_point_not_the_radio(self):
        for shell in self.each_shell():
            self.db(wireless())
            self.assertEqual(self.set_(shell, 'off'), ('pending', 'rc=0'))
            c = self.committed()
            self.assertEqual(c['wireless.ap0.disabled'], '1')
            self.assertNotIn('wireless.radio0.disabled', c)
            self.assertEqual(self.reloads(), 1)
            # off again: nothing written, nothing reloaded
            self.assertEqual(self.set_(shell, 'off'), ('unchanged', 'rc=0'))
            self.assertEqual(self.reloads(), 1)
            self.assertEqual(self.set_(shell, 'on'), ('pending', 'rc=0'))
            self.assertEqual(self.committed()['wireless.ap0.disabled'], '0')

    def test_on_brings_back_a_radio_the_old_button_switched_off(self):
        # the old button's flag: wireless.radio0.disabled=1, the access point itself not disabled
        for shell in self.each_shell():
            self.db(wireless(**{'wireless.radio0.disabled': '1'}))
            self.assertEqual(self.set_(shell, 'on'), ('pending', 'rc=0'))
            c = self.committed()
            self.assertEqual((c['wireless.radio0.disabled'], c['wireless.ap0.disabled']), ('0', '0'))

    def test_a_radio_another_interface_would_come_up_on_is_not_turned_on(self):
        other = {'wireless.radio0.disabled': '1', 'wireless.sta0': 'wifi-iface', 'wireless.sta0.device': 'radio0',
                 'wireless.sta0.mode': 'sta'}
        for shell in self.each_shell():
            self.db(wireless(**other))
            self.assertEqual(self.set_(shell, 'on'), ('conflict', 'rc=1'))
            self.assertEqual(self.committed()['wireless.radio0.disabled'], '1')
            self.assertEqual(self.reloads(), 0)
            # that other interface disabled itself: fine
            self.db(wireless(**other, **{'wireless.sta0.disabled': '1'}))
            self.assertEqual(self.set_(shell, 'on'), ('pending', 'rc=0'))

    def test_unsaved_wireless_changes_are_neither_committed_nor_lost(self):
        for shell in self.each_shell():
            # uci's own staged change
            self.db(wireless(), staged={'wireless.radio0.channel': '36'})
            self.assertEqual(self.set_(shell, 'off'), ('staged', 'rc=1'))
            self.assertNotIn('wireless.ap0.disabled', self.committed())
            self.assertEqual(json.loads((self.tmp / 'uci.json').read_text())['staged'], {'wireless.radio0.channel': '36'})
            # a LuCI session's (rpcd keeps them in a directory of its own)
            self.db(wireless())
            sess = self.rpcd / 'uci-0123abcd'
            sess.mkdir(exist_ok=True)
            (sess / 'wireless').write_text("set wireless.ap0.ssid='new'\n")
            self.assertEqual(self.set_(shell, 'off'), ('staged', 'rc=1'))
            (sess / 'wireless').write_text('')
            self.assertEqual(self.set_(shell, 'off'), ('pending', 'rc=0'))
            (sess / 'wireless').unlink()

    def test_which_access_point(self):
        two = wireless(**{'wireless.guest': 'wifi-iface', 'wireless.guest.device': 'radio0', 'wireless.guest.mode': 'ap',
                          'wireless.guest.ifname': 'wlan0-1'})
        for shell in self.each_shell():
            # two APs: the one on the Wi-Fi device (wlan0)
            self.db(two)
            self.assertEqual(self.set_(shell, 'off'), ('pending', 'rc=0'))
            c = self.committed()
            self.assertEqual(c['wireless.ap0.disabled'], '1')
            self.assertNotIn('wireless.guest.disabled', c)
            # named in unisoc_modem.main.wifi_iface
            self.db(dict(two, **{'unisoc_modem.main.wifi_iface': 'guest'}))
            self.assertEqual(self.set_(shell, 'off'), ('pending', 'rc=0'))
            self.assertEqual(self.committed()['wireless.guest.disabled'], '1')
            # named, but not an access point; two and neither on wlan0; none at all
            self.db(dict(two, **{'unisoc_modem.main.wifi_iface': 'radio0'}))
            self.assertEqual(self.set_(shell, 'off'), ('noap', 'rc=1'))
            self.db(dict(two, **{'wireless.ap0.ifname': 'wlan9'}))
            self.assertEqual(self.set_(shell, 'off'), ('noap', 'rc=1'))
            self.db({'wireless.radio0': 'wifi-device'})
            self.assertEqual(self.set_(shell, 'on'), ('noap', 'rc=1'))
            # an access point on a radio that is not there
            self.db(wireless(**{'wireless.ap0.device': 'radio7'}))
            self.assertEqual(self.set_(shell, 'on'), ('noap', 'rc=1'))

    def test_client_mode_keeps_the_radio_and_failures_say_so(self):
        for shell in self.each_shell():
            self.db(wireless(**{'wireless.ap0.disabled': '1'}))
            (self.run_dir / 'mu300-wifi-client.active').write_text('')
            self.assertEqual(self.set_(shell, 'on'), ('client', 'rc=1'))
            self.assertEqual(self.set_(shell, 'off'), ('unchanged', 'rc=0'))
            (self.run_dir / 'mu300-wifi-client.active').unlink()
            # uci refuses: nothing left staged
            (self.tmp / 'uci.setfail').write_text('')
            self.assertEqual(self.set_(shell, 'on'), ('failed', 'rc=1'))
            (self.tmp / 'uci.setfail').unlink()
            self.assertEqual(json.loads((self.tmp / 'uci.json').read_text())['staged'], {})
            # netifd's reload fails: saved, said so
            (self.tmp / 'ubus.fail').write_text('')
            self.assertEqual(self.set_(shell, 'on'), ('saved', 'rc=0'))
            (self.tmp / 'ubus.fail').unlink()

    def test_one_toggle_at_a_time(self):
        self.stub('flock', '[ ! -e "$STUBLOG/held" ]')
        for shell in self.each_shell():
            self.db(wireless())
            (self.tmp / 'held').write_text('')
            self.assertEqual(self.set_(shell, 'off'), ('busy', 'rc=1'))
            self.assertNotIn('wireless.ap0.disabled', self.committed())
            (self.tmp / 'held').unlink()
            self.assertEqual(self.set_(shell, 'off'), ('pending', 'rc=0'))

    def test_the_panel_button_goes_through_it_and_never_shows_the_key(self):
        for shell in self.each_shell():
            self.db(wireless())
            r = self.script(shell, ACTION, 'wifi', 'off', MU300_HOTSPOT_AP_LIB=LIB, **self.env_())
            self.assertEqual(json.loads(r.stdout), {'ok': 1, 'op': 'wifi off', 'pending': 1})
            self.assertNotIn('secret-psk-123', r.stdout + r.stderr)
            self.assertEqual(self.committed()['wireless.ap0.disabled'], '1')
            (self.tmp / 'uci.json').write_text(json.dumps({'committed': wireless(), 'staged': {'wireless.x.y': '1'}}))
            r = self.script(shell, ACTION, 'wifi', 'on', MU300_HOTSPOT_AP_LIB=LIB, **self.env_())
            o = json.loads(r.stdout)
            self.assertEqual(o['ok'], 0)
            self.assertIn('unsaved changes', o['error'])
            r = self.script(shell, ACTION, 'wifi', 'on', MU300_HOTSPOT_AP_LIB=self.tmp / 'none', **self.env_())
            self.assertEqual(json.loads(r.stdout)['ok'], 0)

    def test_the_wifi_key_takes_the_same_path(self):
        src = (BIN / 'mu300-sysd').read_text()
        self.assertIn("'/opt/mu300/lib/hotspot-ap.sh'", src)
        self.assertIn('hotspot_ap_set %s', src)
        self.assertNotIn('wifi down', src)
        self.assertNotIn('wifi up', src)
        self.assertNotIn('wifi-device[0].disabled', ACTION.read_text())


if __name__ == '__main__':
    unittest.main()
