"""Broker-only SIM transactions: a switch is saved as the boot default only once the target has a bearer; a failed
switch (or one without data) preserves boot policy. Runs on macOS too: flock(1) and /proc/uptime are not needed."""
import json
import os
import subprocess
import time
import signal
from helpers import BIN, TOP, ShellTest

# flock(1) for a host without it (macOS): the lock is taken on the inherited descriptor, and BSD/Linux flock locks
# belong to the open file description, so it holds for the shell that opened it after this exits.
FLOCK = r'''
nb=0; while [ "$#" -gt 1 ]; do case $1 in -n) nb=1;; esac; shift; done
exec python3 -c 'import fcntl, sys
try: fcntl.flock(int(sys.argv[1]), fcntl.LOCK_EX | (fcntl.LOCK_NB if sys.argv[2] == "1" else 0))
except OSError: sys.exit(1)' "$1" "$nb"
'''
# netifd's wan: up with an address unless the test says T_WAN=0
UBUS = r'''
case "$*" in *network.interface.wan*)
    if [ "${T_WAN:-1}" = 1 ]; then printf '{ "up": true, "l3_device": "%s", "ipv4-address": [ { "address": "10.0.0.2" } ] }\n' "${T_BEARER:-sipa_eth$(( $(cat "$STUBLOG/run/mu300/sim-slot") * 8 ))}"
    else printf '{ "up": false, "ipv4-address": [ ] }\n'; fi ;;
esac
'''

class HotSim(ShellTest):
    def setUp(self):
        super().setUp()
        self.run = self.tmp / 'run'
        self.etc = self.tmp / 'etc'
        self.etc.mkdir()
        (self.etc / 'openwrt_release').write_text('DISTRIB_ID=OpenWrt\n')
        (self.run / 'mu300').mkdir(parents=True)
        (self.run / 'mu300/sim-slot').write_text('1\n')
        (self.etc / 'uci-slot').write_text('1\n')
        (self.etc / 'uci-hot').write_text('1\n')
        (self.run / 'mu300-radio-freeze').touch()
        for n in ('mu300-at','mu300-at2','mu300-at4','mu300-at5'):
            d=self.run/n; (d/'owner').mkdir(parents=True)
            (d/'owner/pid').write_text(str(os.getpid())); os.mkfifo(d/'cmd')
        (self.tmp/'radio0').write_text('0'); (self.tmp/'radio1').write_text('1')
        self.stub('uci', r"""case "$*" in
 '-q get network.wan.sim_slot') cat "$STUBLOG/etc/uci-slot" ;;
 '-q get network.wan.sim_hot') cat "$STUBLOG/etc/uci-hot" ;;
 'set network.wan.sim_slot='*) printf '%s\n' "${2#*=}" > "$STUBLOG/etc/uci-slot" ;;
 'set network.wan.sim_hot='*) printf '%s\n' "${2#*=}" > "$STUBLOG/etc/uci-hot" ;;
 'commit network') : ;;
 *) exit 1;;
esac""")
        self.stub('ifdown' , 'echo down >> "$STUBLOG/net"')
        self.stub('ifup', 'echo up >> \"$STUBLOG/net\"; if [ \"${T_IFUP_FAIL_ONCE:-0}\" = 1 ] && [ ! -e \"$STUBLOG/ifup-failed\" ]; then touch \"$STUBLOG/ifup-failed\"; exit 1; fi')
        self.stub('mu300-device', 'echo "${T_DEVICE:-u30air}"')
        self.stub('setsid', "exec python3 -c 'import os, sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' \"$@\"")   # macOS has none; the process group is only for the interrupt
        self.stub('flock', FLOCK)
        self.stub('ubus', UBUS)
        self.stub('mu300-at', r'''
shift 2
case "$MU300_AT_DIR" in *at4|*at5) slot=1;; *) slot=0;; esac
echo "$slot $1" >> "$STUBLOG/at"
if [ "${T_SILENT:-}" = 1 ] && [ -e "$STUBLOG/mutated" ]; then exit 1; fi
case "$1" in
 'AT+CPIN?') if [ "$slot" = 0 ]; then echo "+CPIN: ${T_PIN:-READY}"; else echo '+CPIN: READY'; fi;;
 'AT+SFUN=4') [ -z "${T_DELAY:-}" ] || sleep "$T_DELAY"; touch "$STUBLOG/mutated"; echo 1 > "$STUBLOG/radio$slot";;
 'AT+SFUN=3') echo 0 > "$STUBLOG/radio$slot";;
 'AT+CFUN?') echo "+CFUN: $(cat "$STUBLOG/radio$slot")";;
 'AT+SPSWDATA')
   if [ "${T_FAIL_ONCE:-}" = 1 ] && [ ! -e "$STUBLOG/failed" ]; then touch "$STUBLOG/failed"; echo ERROR; exit 0; fi;;
esac
echo OK
''')
        self.stub('mobile-data', 'shift; ln -s "$$" "$T_RUN/mu300-radio-on.owner"; "$@"; r=$?; rm -f "$T_RUN/mu300-radio-on.owner"; exit "$r"')
        self.controller=self.tmp/'mu300-sim'
        self.controller.write_text((BIN/'mu300-sim-u30').read_text().replace('/run/',str(self.run)+'/').replace('/etc/',str(self.etc)+'/')
            .replace('/opt/mu300/bin/mu300-at','mu300-at').replace('/opt/mu300/bin/mobile-data','mobile-data')
            .replace('/opt/mu300/bin/mu300-device','mu300-device'))
        self.controller.chmod(0o755)

    def env(self, **extra):
        extra.setdefault('MU300_SIM_CONFIG_LIB', TOP / 'rootfs/overlay/opt/mu300/lib/sim-config.sh')
        return super().env(**extra)

    def ctl(self, *args, **env):
        env.setdefault('MU300_SIM_WAN_WAIT', '2')
        return self.script(['bash'],self.controller,*args,T_RUN=self.run, MU300_SIM_CONFIG_LIB=TOP / 'rootfs/overlay/opt/mu300/lib/sim-config.sh',**env)

    def status(self, **env):
        return json.loads(self.ctl('status', **env).stdout)

    def test_bidirectional_switch_persists_default(self):
        for public,slot in [('1','0'),('2','1')]:
            r=self.ctl('switch',public)
            self.assertEqual(r.returncode,0,r.stdout+r.stderr)
            self.assertEqual((self.etc/'uci-slot').read_text(),slot+'\n')
            self.assertEqual((self.run/'mu300/sim-slot').read_text(),slot+'\n')
            s=self.status()
            self.assertEqual(s['active'],int(public)); self.assertEqual(s['result'],'switched')
        calls=(self.tmp/'at').read_text()
        self.assertIn('0 AT+SPACTCARD=0;+SFUN=2',calls)
        self.assertIn('1 AT+SPACTCARD=1;+SFUN=5',calls)
        self.assertNotIn('SPTESTMODEM',calls)
        self.assertEqual((self.tmp/'net').read_text(),'down\nup\ndown\nup\n')

    def test_wrong_bearer_is_not_saved_as_success(self):
        result = self.ctl('switch', '1', T_BEARER='sipa_eth8')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status()['result'], 'no_data')
        self.assertEqual((self.etc / 'uci-slot').read_text(), '1\n')
        self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '1\n')

    def test_failed_ifup_rolls_back_and_keeps_preference(self):
        result = self.ctl('switch', '1', T_IFUP_FAIL_ONCE=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.status()['result'], 'no_data')
        self.assertEqual((self.etc / 'uci-slot').read_text(), '1\n')
        self.assertEqual((self.run / 'mu300/sim-slot').read_text(), '1\n')

    def test_same_active_card_without_data_does_not_replace_manual_default(self):
        (self.etc / 'uci-slot').write_text('0\n')
        result = self.ctl('switch', '2', T_WAN=0)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.etc / 'uci-slot').read_text(), '0\n')
        self.assertFalse((self.tmp / 'at').exists())

    def test_no_bearer_on_the_target_rolls_back_without_saving(self):
        # the one APN is shared: a card of another carrier switches "successfully" and then never gets a bearer
        r=self.ctl('switch','1',T_WAN='0')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('no data',r.stdout)
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')
        self.assertEqual((self.run/'mu300/sim-slot').read_text(),'1\n')
        s=self.status()
        self.assertEqual(s['active'],2); self.assertEqual(s['default'],2); self.assertEqual(s['result'],'no_data')
        # dialled the target, found nothing, went back and dialled the old card again
        self.assertEqual((self.tmp/'net').read_text(),'down\nup\ndown\nup\n')
        calls=(self.tmp/'at').read_text().splitlines()
        self.assertEqual(calls.count('0 AT+SFUN=4'),1); self.assertEqual(calls.count('1 AT+SFUN=4'),1)
        self.assertFalse((self.run/'mu300/sim-switching').exists())

    def test_same_card_sends_no_commands(self):
        self.assertEqual(self.ctl('switch','2').returncode,0)
        self.assertFalse((self.tmp/'at').exists())

    def test_target_pin_lock_rolls_back_and_preserves_default(self):
        self.assertNotEqual(self.ctl('switch','1',T_PIN='SIM PIN').returncode,0)
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')
        self.assertEqual((self.tmp/'net').read_text(),'down\nup\n')
        self.assertEqual(self.status()['result'],'rolled_back')

    def test_rolls_back_once_without_changing_boot_default(self):
        r=self.ctl('switch','1',T_FAIL_ONCE='1')
        self.assertNotEqual(r.returncode,0)
        s=self.status()
        self.assertEqual(s['active'],2); self.assertEqual(s['default'],2)
        self.assertEqual(s['result'],'rolled_back')
        self.assertEqual((self.tmp/'net').read_text(),'down\nup\n')

    def test_silent_cp_stops_and_requires_reboot(self):
        self.assertNotEqual(self.ctl('switch','1',T_SILENT='1').returncode,0)
        s=self.status()
        self.assertIsNone(s['active']); self.assertEqual(s['default'],2)
        self.assertEqual(s['result'],'reboot_required')
        self.assertEqual((self.tmp/'net').read_text(),'down\n')
        self.assertNotEqual(self.ctl('switch','1').returncode,0)

    def test_next_boot_selection_does_not_change_active(self):
        r=self.ctl('default','1')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        s=json.loads(r.stdout)
        self.assertEqual(s['active'],2); self.assertEqual(s['default'],1)
        self.assertEqual(s['busy'],0, 'the holder of the lock is not busy to itself')
        self.assertFalse((self.tmp/'at').exists())
        # and the next boot's choice needs neither hot switching nor the brokers
        (self.etc/'uci-hot').write_text('0\n')
        (self.run/'mu300-at4/owner/pid').unlink()
        self.assertEqual(self.ctl('default','2').returncode,0)
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')

    def test_fallback_of_the_last_boot_is_reported_until_the_user_chooses(self):
        (self.etc/'mu300-sim-fallback').write_text('cold-start-failed\n')
        self.assertEqual(self.status()['fallback'],'cold-start-failed')
        (self.etc/'mu300-sim-fallback').write_text('+CCID: 8990; rm -rf /\n')   # never modem output, never shell
        self.assertEqual(self.status()['fallback'],'8990rm-rf')
        self.assertEqual(self.ctl('default','2').returncode,0)
        self.assertFalse((self.etc/'mu300-sim-fallback').exists())
        self.assertIsNone(self.status()['fallback'])

    def test_other_devices_and_systems_refuse_everything(self):
        for env in ({'T_DEVICE': 'f50'}, {'T_DEVICE': 'u30pro'}):
            for args in (('status',), ('default','2'), ('hot','1'), ('switch','1')):
                r=self.ctl(*args, **env)
                self.assertNotEqual(r.returncode,0,args)
                self.assertEqual(json.loads(r.stdout)['supported'],0)
        (self.etc/'openwrt_release').unlink()   # Ubuntu
        r=self.ctl('default','2')
        self.assertNotEqual(r.returncode,0); self.assertEqual(json.loads(r.stdout)['ok'],0)
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')
        self.assertEqual((self.etc/'uci-hot').read_text(),'1\n')
        self.assertFalse((self.tmp/'at').exists())

    def test_disabled_or_missing_owner_sends_nothing(self):
        (self.etc/'uci-hot').write_text('0')
        self.assertNotEqual(self.ctl('switch','1').returncode,0)
        (self.etc/'uci-hot').write_text('1')
        (self.run/'mu300-at4/owner/pid').unlink()
        self.assertNotEqual(self.ctl('switch','1').returncode,0)
        self.assertFalse((self.tmp/'at').exists())

    def test_injection_and_internal_sequence_rejected(self):
        for verb,value in [('switch','1;reboot'),('hot','yes'),('default','0'),('--sequence','0')]:
            self.assertNotEqual(self.ctl(verb,value).returncode,0)
        self.assertFalse((self.tmp/'at').exists())

    def test_concurrent_request_cannot_change_default(self):
        lock=self.run/'mu300/sim-control.lock'
        with lock.open('w') as f:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX)
            self.assertNotEqual(self.ctl('default','1').returncode,0)
            self.assertEqual(self.status()['busy'],1)
        self.assertEqual((self.etc/'uci-slot').read_text(),'1\n')
        self.assertEqual(self.status()['busy'],0)

    def test_interrupted_switch_keeps_default_and_requires_reboot(self):
        env=self.env(T_RUN=self.run,T_DELAY='10')
        p=subprocess.Popen(['bash',str(self.controller),'switch','1'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+5
            while not (self.run/'mu300/sim-switching').exists():
                self.assertLess(time.monotonic(),deadline); time.sleep(.02)
            p.send_signal(signal.SIGTERM)
            p.communicate(timeout=3)
            s=self.status()
            self.assertIsNone(s['active']); self.assertEqual(s['default'],2)
            self.assertEqual(s['result'],'reboot_required')
        finally:
            if p.poll() is None: p.kill(); p.communicate()

    def test_interrupted_marker_never_allows_another_switch(self):
        (self.run/'mu300/sim-switching').touch()
        s=self.status()
        self.assertIsNone(s['active']); self.assertEqual(s['result'],'reboot_required')
        self.assertNotEqual(self.ctl('switch','1').returncode,0)
        self.assertFalse((self.tmp/'at').exists())
