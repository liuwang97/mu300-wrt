"""tools/smoke.py, the release smoke test, against fake boards: the config and its validation (hostile values
refused), the identity check before anything runs, the before/after comparison (the VPN's files by hash, never their
contents), the reboot's bounded waits, the dry run, the report."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP, shells

spec = importlib.util.spec_from_file_location('smoke', TOP / 'tools' / 'smoke.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

TAG = 'v2026.10.16.1'
OLD = 'v2026.10.15.1'


def key_line(host, seed):
    blob = b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + hashlib.sha256(seed.encode()).digest()
    return f'{host} ssh-ed25519 {base64.b64encode(blob).decode()}'


KEY = key_line('192.168.77.1', 'board-a')
FPR = smoke.ssh_key_fingerprint(KEY)
OTHER_KEY = key_line('192.168.77.1', 'another board')

VPN_ON = {'enabled': True, 'running': True, 'kill_switch': {'setting': True, 'active': False},
          'profile': {'id': 'office', 'name': 'Office'}, 'engine': {'name': 'sing-box'}, 'tunnel': {'up': True},
          'exit_ip': None}
HASHES = {'/etc/mu300/vpn/profiles/office.json': 'a' * 64, '/etc/mu300/vpn.conf': 'b' * 64,
          '/etc/mu300/vpn/profiles/my home.conf': 'c' * 64}
SERVICES = {'mu300-hotspot': 'enabled/running', 'mu300-mobile-data': 'enabled/running',
            'mu300-update-notice': 'enabled/stopped'}


def state_text(release=OLD, kernel='7.2.10', device='f50', hostname='mu300', default_boot='linux', boot_tag=None,
               services=None, ap=('wlan0',), wan=('sipa_eth0',), vpn=VPN_ON, hashes=None, complete=True):
    lines = [f'release={release}', f'kernel={kernel}', f'device={device}', f'hostname={hostname}',
             f'default_boot={default_boot}', f'boot_tag={release if boot_tag is None else boot_tag}']
    for k, v in (SERVICES if services is None else services).items():
        lines.append(f'svc {k} {v}')
    lines += [f'ap {a}' for a in ap] + [f'wan {w}' for w in wan]
    lines.append('vpn=' + (json.dumps(vpn) if vpn is not None else ''))
    for p, h in (HASHES if hashes is None else hashes).items():
        lines.append(f'vpnhash {h}  {p}')
    if complete:
        lines.append('end=1')
    return '\n'.join(lines) + '\n'


def board_dict(**over):
    d = {'name': 'f50-a', 'device': 'f50',
         'ssh': {'host': '192.168.77.1', 'port': 22, 'fingerprint': FPR, 'hostname': 'mu300'},
         'update': 'online'}
    for k, v in over.items():
        if k.startswith('ssh_'):
            d['ssh'][k[4:]] = v
        elif v is None:
            d.pop(k, None)
        else:
            d[k] = v
    return d


def boards(*dicts, timeouts=None):
    cfg = {'boards': list(dicts)}
    if timeouts is not None:
        cfg['timeouts'] = timeouts
    return smoke.parse_config(cfg)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeDevice:
    """One simulated board running OpenWrt over ssh. BEFORE/AFTER are the state script's output."""

    def __init__(self, clock, keys=(KEY,), hostname='mu300', device='f50', system='openwrt-luci', before=None,
                 after=None, apply_rc=0, boot_time=120, comes_back=True, goes_down=True):
        self.clock, self.keys, self.boot_time = clock, list(keys), boot_time
        self.identity = f'hostname={hostname}\ndevice=' + json.dumps({'mu300': 1, 'device': device, 'system': system}) + '\n'
        self.before = before if before is not None else state_text()
        self.after = after if after is not None else state_text(release=TAG)
        self.apply_rc, self.comes_back, self.goes_down = apply_rc, comes_back, goes_down
        self.updated = False
        self.down_from = None   # ssh is gone from this time ...
        self.up_at = None       # ... until this one (None: never)

    def linux_up(self):
        t = self.clock.now()
        if self.down_from is not None and t >= self.down_from:
            return self.up_at is not None and t >= self.up_at
        return True

    def reboot(self):
        t = self.clock.now()
        self.down_from = t if self.goes_down else None
        self.up_at = t + self.boot_time if self.comes_back else None


class FakeTransport:
    def __init__(self, clock, devices):
        self.clock, self.devices = clock, devices
        self.calls = []
        self.pinned = {}

    def ssh_host_keys(self, board, timeout=30):
        self.calls.append(('keyscan', board.name))
        d = self.devices[board.name]
        return list(d.keys) if d.linux_up() else []

    def pin(self, board, lines):
        self.calls.append(('pin', board.name))
        self.pinned[board.name] = lines

    def ssh(self, board, command, stdin=None, timeout=120):
        assert board.name in self.pinned, 'ssh before the host key was pinned'
        d = self.devices[board.name]
        kind = {smoke.IDENTITY_SCRIPT: 'identity', smoke.STATE_SCRIPT: 'state', smoke.REBOOT_SCRIPT: 'reboot',
                None: None}.get(stdin, 'other')
        self.calls.append(('ssh', board.name, command, kind))
        if not d.linux_up():
            return smoke.Result(255, '', 'ssh: connect to host: Connection refused')
        if kind == 'identity':
            return smoke.Result(0, d.identity)
        if kind == 'state':
            return smoke.Result(0, d.after if d.updated else d.before)
        if kind == 'reboot':
            boot = smoke.parse_state(d.after if d.updated else d.before)['default_boot']
            if boot not in ('', 'linux'):
                return smoke.Result(3, '', 'the default boot is not linux: not rebooting')
            d.reboot()
            return smoke.Result(0)
        if 'mu300-update apply' in command:
            if d.apply_rc == 0:
                d.updated = True
            return smoke.Result(d.apply_rc, 'ok\n', '' if d.apply_rc == 0 else 'ERROR: could not reach GitHub\n')
        return smoke.Result(0)

    def ssh_ok(self, board, timeout=30):
        self.calls.append(('ssh-ok', board.name))
        return self.devices[board.name].linux_up()

    def scp_put(self, board, files, remote_dir, timeout):
        self.calls.append(('scp', board.name, tuple(Path(f).name for f in files), remote_dir))
        return smoke.Result(0)

    def kinds(self, name):
        return [c[3] if c[0] == 'ssh' and c[3] not in (None, 'other') else c[2] if c[0] == 'ssh' else c[0]
                for c in self.calls if len(c) > 1 and c[1] == name]

    def changing_calls(self):
        """Every call that changes something on a board."""
        return [c for c in self.calls
                if (c[0] == 'ssh' and (c[3] == 'reboot' or 'mu300-update' in c[2] or 'mkdir' in c[2])) or c[0] == 'scp']


def harness(cfg, devices, release_dir=None, timeouts=None):
    clock = FakeClock()
    devs = {name: (d(clock) if callable(d) else d) for name, d in devices.items()}
    for d in devs.values():
        d.clock = clock
    tr = FakeTransport(clock, devs)
    bs, t = cfg
    if timeouts:
        t = dict(t, **timeouts)
    return smoke.Harness(bs, t, TAG, tr, clock=clock, release_dir=release_dir, log=lambda m: None), tr, clock


def by_name(checks):
    return {(c.board, c.name): c for c in checks}


class Config(unittest.TestCase):
    def test_the_example_is_valid(self):
        bs, t = smoke.load_config(TOP / 'tools' / 'smoke.example.json')
        self.assertEqual([b.name for b in bs], ['u30-mirror', 'f50-from'])
        self.assertEqual(t, smoke.TIMEOUTS)
        u30, f50 = bs
        self.assertEqual((u30.device, u30.update, u30.expect_kernel), ('u30air', 'mirror', '7.2'))
        self.assertEqual((f50.device, f50.update, f50.port), ('f50', 'from', 2222))

    def test_the_local_config_is_ignored_by_git(self):
        self.assertIn('/tools/smoke.json', (TOP / '.gitignore').read_text().split())

    def test_defaults(self):
        b = boards(board_dict())[0][0]
        self.assertEqual((b.user, b.port, b.update, b.expect_kernel), ('root', 22, 'online', '7.2'))
        self.assertEqual(b.apply_args, [])

    def test_hostile_values_are_refused(self):
        cases = [
            dict(name='a;reboot'), dict(name='-x'), dict(name=''), dict(name='a' * 33),
            dict(device='f50; reboot'), dict(device='F50'), dict(device='x1'), dict(update='curl|sh'),
            dict(update='root'),
            dict(ssh_host='-oProxyCommand=touch /tmp/x'), dict(ssh_host='a b'), dict(ssh_host='host;id'),
            dict(ssh_host='host\nid'), dict(ssh_host='$(id)'), dict(ssh_host=''), dict(ssh_host=['x']),
            dict(ssh_port='22'), dict(ssh_port=0), dict(ssh_port=70000), dict(ssh_port=True), dict(ssh_port=22.0),
            dict(ssh_fingerprint='SHA256:short'), dict(ssh_fingerprint='MD5:aa:bb'), dict(ssh_fingerprint=None),
            dict(ssh_fingerprint=FPR + '\n'), dict(ssh_hostname='$(reboot)'), dict(ssh_hostname='a b'),
            dict(ssh_extra='x'), dict(ssh_user='root'), dict(adb_serial='MU300LINUX-1-2'),
            dict(update='mirror', mirror_url='http://pc:8000/download;reboot'),
            dict(update='mirror', mirror_url='http://pc:8000/../etc'), dict(update='mirror', mirror_url='ftp://pc/x'),
            dict(update='mirror', mirror_url='http://pc/a b'), dict(update='mirror', mirror_url="http://pc/$(id)"),
            dict(update='mirror', mirror_url='file:///srv/x?y'), dict(update='mirror'),
            dict(update='from', from_path='relative/dir'), dict(update='from', from_path='/srv/../etc'),
            dict(update='from', from_path='/srv/a b'), dict(update='from', from_path='/srv/$x'), dict(update='from'),
            dict(apply_args=['--from', '/x']), dict(apply_args=['; reboot']), dict(apply_args='--unsigned'),
            dict(apply_args=['openwrt-luci']),
            dict(expect_kernel='7.2; id'), dict(expect_kernel='latest'), dict(expect_kernel='7.3'),
            dict(expect_kernel='7.20'), dict(expect_kernel='7'), dict(expect_kernel=72),
            dict(system='openwrt-luci'), dict(sudo=True), dict(allow_android=False), dict(unknown_field=1),
        ]
        for over in cases:
            with self.subTest(over=over), self.assertRaises(smoke.ConfigError):
                boards(board_dict(**over))

    def test_good_values_are_taken(self):
        for over in (dict(ssh_host='mu300.local'), dict(ssh_host='fe80::1'), dict(ssh_host='192.168.77.1'),
                     dict(update='mirror', mirror_url='file:///srv/mirror/download'),
                     dict(update='mirror', mirror_url='https://example.org:8443/mu300/download/'),
                     dict(update='from', from_path='/mnt/mu300-disk/smoke-release'),
                     dict(apply_args=['--unsigned', '--no-rollback', '--downgrade', '--no-reinstall']),
                     dict(expect_kernel='7.2.10'), dict(expect_kernel='7.2'), dict(device='u30air'),
                     dict(_comment='anything; $(goes)')):
            with self.subTest(over=over):
                boards(board_dict(**over))

    def test_whole_config_is_checked(self):
        for cfg in ([], {'boards': []}, {'boards': {}}, {'boards': [board_dict()], 'extra': 1},
                    {'boards': [board_dict(), board_dict()]},
                    {'boards': [board_dict()], 'timeouts': {'boot': -1}},
                    {'boards': [board_dict()], 'timeouts': {'boot': '900'}},
                    {'boards': [board_dict()], 'timeouts': {'forever': 1}},
                    {'boards': [board_dict()], 'timeouts': {'install': 1}}):
            with self.subTest(cfg=cfg), self.assertRaises(smoke.ConfigError):
                smoke.parse_config(cfg)
        self.assertEqual(boards(board_dict(), timeouts={'boot': 30})[1]['boot'], 30)

    def test_json_errors_and_duplicate_keys(self):
        d = Path(tempfile.mkdtemp(prefix='mu300-test-'))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        f = d / 'smoke.json'
        f.write_text('{"boards": [')
        with self.assertRaises(smoke.ConfigError):
            smoke.load_config(f)
        f.write_text('{"boards": [{"name": "a", "name": "b"}]}')
        with self.assertRaisesRegex(smoke.ConfigError, 'duplicate'):
            smoke.load_config(f)
        with self.assertRaises(smoke.ConfigError):
            smoke.load_config(d / 'missing.json')

    def test_tags(self):
        for t in ('v2026.10.16.1', 'v2026.10.16.12', 'v2026.10.27'):
            self.assertTrue(smoke.valid_tag(t), t)
        for t in ('v2026.10.16.0', 'v2026.10.16.100', 'latest', '2026.10.16.1', 'v2026.10.16.1;id', 'v2026.10.16.1\n',
                  '../v2026.10.16.1', None):
            self.assertFalse(smoke.valid_tag(t), t)

    def test_commands_are_fixed_strings(self):
        mk = lambda **o: boards(board_dict(**o))[0][0]
        self.assertEqual(smoke.apply_command(mk(), TAG),
                         f'env MU300_RELEASE={TAG} /opt/mu300/bin/mu300-update apply')
        self.assertEqual(smoke.apply_command(mk(update='mirror', mirror_url='http://pc:8000/dl/'), TAG),
                         f'env MU300_RELEASE={TAG} MU300_RELEASE_URL=http://pc:8000/dl/{TAG} /opt/mu300/bin/mu300-update apply')
        self.assertEqual(smoke.apply_command(mk(update='from', from_path='/srv/r', apply_args=['--unsigned']), TAG),
                         f'env MU300_RELEASE={TAG} /opt/mu300/bin/mu300-update apply --from /srv/r --unsigned')

    def test_device_scripts_parse(self):
        for s in shells():
            for name in ('IDENTITY_SCRIPT', 'STATE_SCRIPT', 'REBOOT_SCRIPT'):
                with self.subTest(shell=s, script=name):
                    r = subprocess.run(s + ['-n'], input=getattr(smoke, name), capture_output=True, text=True, timeout=30)
                    self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_kernel_is_7_2(self):
        for k in ('7.2.0', '7.2.10', '7.2.10-mu300', '7.2'):
            self.assertTrue(smoke._kernel_matches(k, '7.2'), k)
        for k in ('7.20.1', '7.3.1', '', '17.2.1'):
            self.assertFalse(smoke._kernel_matches(k, '7.2'), k)


class Fingerprint(unittest.TestCase):
    def test_as_ssh_keygen_prints_it(self):
        if not shutil.which('ssh-keygen'):
            self.skipTest('no ssh-keygen')
        d = Path(tempfile.mkdtemp(prefix='mu300-test-'))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(d / 'k')], check=True, timeout=30)
        pub = (d / 'k.pub').read_text().strip()
        want = subprocess.run(['ssh-keygen', '-lf', str(d / 'k.pub')], capture_output=True, text=True,
                              timeout=30).stdout.split()[1]
        self.assertEqual(smoke.ssh_key_fingerprint('host ' + pub), want)

    def test_not_a_key(self):
        for line in ('', '# comment', 'host ssh-ed25519', 'host ssh-ed25519 !!!notbase64'):
            self.assertIsNone(smoke.ssh_key_fingerprint(line))


class Identity(unittest.TestCase):
    def two_boards(self, **dev_b):
        cfg = boards(board_dict(), board_dict(name='u30-b', device='u30air', ssh_host='192.168.78.1',
                                              ssh_hostname='OpenWrt'))
        k2 = key_line('192.168.78.1', 'u30')
        cfg[0][1].fingerprint = smoke.ssh_key_fingerprint(k2)
        opts = dict(keys=[k2], hostname='OpenWrt', device='u30air')
        opts.update(dev_b)
        return harness(cfg, {'f50-a': lambda c: FakeDevice(c),
                             'u30-b': lambda c: FakeDevice(c, **opts)})

    def assertNothingRan(self, tr):
        self.assertEqual(tr.changing_calls(), [])
        self.assertFalse([c for c in tr.calls if c[0] == 'ssh' and c[3] == 'state'])

    def test_all_match(self):
        h, tr, _ = self.two_boards()
        self.assertEqual(h.preflight(), {})

    def test_host_key_mismatch_refuses_before_anything(self):
        for name, opts in (('key', dict(keys=[OTHER_KEY])), ('unreachable', dict(keys=[])),
                           ('hostname', dict(hostname='OpenWrt-2')), ('device', dict(device='f50')),
                           ('system', dict(system='openwrt'))):
            with self.subTest(name):
                h, tr, _ = self.two_boards(**opts)
                checks, refused = h.run()
                self.assertEqual(checks, [])
                self.assertEqual(list(refused), ['u30-b'])
                self.assertNothingRan(tr)     # not even the first board, which matched
                text = smoke.report(TAG, checks, refused)
                self.assertIn('- REFUSED u30-b identity:', text)
                self.assertIn('Nothing was run', text)
                self.assertEqual(smoke.exit_code(checks, refused), 2)

    def test_no_ssh_command_without_the_pinned_key(self):
        h, tr, _ = self.two_boards(keys=[OTHER_KEY])
        h.preflight()
        self.assertNotIn('u30-b', tr.pinned)
        self.assertFalse([c for c in tr.calls if c[0] == 'ssh' and c[1] == 'u30-b'])

    def test_a_device_that_prints_its_type_alone(self):
        cfg = boards(board_dict())
        h, tr, _ = harness(cfg, {'f50-a': lambda c: FakeDevice(c)})
        tr.devices['f50-a'].identity = 'hostname=mu300\ndevice=f50\n'
        self.assertEqual(h.preflight(), {})
        tr.devices['f50-a'].identity = 'hostname=mu300\ndevice=u30air\n'
        self.assertIn('device is u30air', h.preflight()['f50-a'][0])


class Run(unittest.TestCase):
    def run_one(self, cfg=None, release_dir=None, timeouts=None, **dev):
        cfg = cfg or boards(board_dict())
        h, tr, clock = harness(cfg, {'f50-a': lambda c: FakeDevice(c, **dev)}, release_dir=release_dir,
                               timeouts=timeouts)
        checks, refused = h.run()
        self.assertEqual(refused, {})
        return by_name(checks), checks, tr, clock

    def test_a_clean_update(self):
        c, checks, tr, clock = self.run_one()
        self.assertEqual([x.name for x in checks], list(smoke.CHECKS))
        for x in checks:
            self.assertEqual(x.status, 'PASS', x.line())
        self.assertEqual(c['f50-a', 'release'].detail, f'{TAG} (was {OLD})')
        self.assertEqual(c['f50-a', 'vpn-files'].detail, 'unchanged (3 files)')
        self.assertEqual(c['f50-a', 'vpn-state'].detail, 'unchanged (running)')
        self.assertEqual(smoke.exit_code(checks), 0)
        # the order on the board: identity, state, update, reboot, back, identity, state
        seq = [k for k in tr.kinds('f50-a') if k not in ('ssh-ok', 'keyscan', 'pin')]
        self.assertEqual(seq, ['identity', 'state', f'env MU300_RELEASE={TAG} /opt/mu300/bin/mu300-update apply',
                               'reboot', 'identity', 'state'])
        self.assertGreaterEqual(clock.t, 120 + 60)   # the boot, then the settle time

    def test_vpn_files_changed_names_without_contents_or_hashes(self):
        after = dict(HASHES)
        after['/etc/mu300/vpn/profiles/office.json'] = 'd' * 64
        del after['/etc/mu300/vpn.conf']
        after['/etc/mu300/vpn/profiles/new.json'] = 'e' * 64
        c, checks, tr, _ = self.run_one(after=state_text(release=TAG, hashes=after))
        v = c['f50-a', 'vpn-files']
        self.assertEqual(v.status, 'FAIL')
        self.assertEqual(v.detail, 'changed: 1 files: /etc/mu300/vpn/profiles/office.json; '
                                   'gone: 1 files: /etc/mu300/vpn.conf; new: 1 files: /etc/mu300/vpn/profiles/new.json')
        text = smoke.report(TAG, checks)
        for h in list(HASHES.values()) + list(after.values()):
            self.assertNotIn(h, text)
            self.assertNotIn(h[:12], text)
        self.assertEqual(smoke.exit_code(checks), 1)

    def test_a_file_name_with_a_space(self):
        s = smoke.parse_state(state_text())
        self.assertEqual(s['vpn_hashes']['/etc/mu300/vpn/profiles/my home.conf'], 'c' * 64)

    def test_the_state_keeps_no_contents(self):
        # mu300-vpn status --json: only the compared fields are kept (no profile name, no exit IP, no address)
        s = smoke.parse_state(state_text(vpn=dict(VPN_ON, exit_ip='203.0.113.9', tunnel={'up': True, 'addresses': ['10.0.0.2/32']})))
        self.assertEqual(s['vpn'], {'enabled': True, 'running': True, 'kill_switch.setting': True, 'profile.id': 'office',
                                    'engine.name': 'sing-box', 'tunnel.up': True})
        self.assertNotIn('203.0.113.9', repr(s))
        self.assertNotIn('Office', repr(s))

    def test_version_mismatch_fails(self):
        c, checks, _, _ = self.run_one(after=state_text(release=OLD))
        self.assertEqual(c['f50-a', 'release'].status, 'FAIL')
        self.assertEqual(c['f50-a', 'release'].detail, f'{OLD}, expected {TAG} (was {OLD})')
        self.assertEqual(c['f50-a', 'boot-image'].status, 'FAIL')
        self.assertEqual(smoke.exit_code(checks), 1)

    def test_vpn_state_changed(self):
        c, _, _, _ = self.run_one(after=state_text(release=TAG, vpn=dict(VPN_ON, running=False, tunnel={'up': False})))
        self.assertEqual(c['f50-a', 'vpn-state'].status, 'FAIL')
        self.assertEqual(c['f50-a', 'vpn-state'].detail, 'changed: running, tunnel.up')
        c, _, _, _ = self.run_one(after=state_text(release=TAG, vpn=None))
        self.assertEqual(c['f50-a', 'vpn-state'].detail, 'the VPN module is gone')
        c, _, _, _ = self.run_one(before=state_text(vpn=None, hashes={}), after=state_text(release=TAG, vpn=None, hashes={}))
        self.assertEqual((c['f50-a', 'vpn-state'].status, c['f50-a', 'vpn-files'].status), ('SKIP', 'SKIP'))

    def test_services_hotspot_mobile_data(self):
        svc = dict(SERVICES, **{'mu300-hotspot': 'enabled/stopped'})
        del svc['mu300-mobile-data']
        c, _, _, _ = self.run_one(after=state_text(release=TAG, services=svc, ap=(), wan=()))
        self.assertEqual(c['f50-a', 'services'].status, 'FAIL')
        self.assertEqual(c['f50-a', 'services'].detail, 'no longer running: mu300-hotspot, mu300-mobile-data')
        self.assertEqual(c['f50-a', 'hotspot'].detail, 'down, was up (wlan0)')
        self.assertEqual(c['f50-a', 'mobile-data'].status, 'FAIL')
        self.assertEqual(c['f50-a', 'mobile-data'].detail, 'down, was up (sipa_eth0)')
        # a service that was stopped before may stay stopped; no mobile data before, none after
        before = state_text(services={'mu300-hotspot': 'enabled/running', 'mu300-post': 'enabled/stopped'}, wan=())
        after = state_text(release=TAG, services={'mu300-hotspot': 'enabled/running', 'mu300-post': 'disabled/stopped'}, wan=())
        c, _, _, _ = self.run_one(before=before, after=after)
        self.assertEqual(c['f50-a', 'services'].status, 'PASS')
        self.assertEqual(c['f50-a', 'services'].detail, '1 running, none lost')
        self.assertEqual(c['f50-a', 'mobile-data'].detail, 'down, and was down before')
        self.assertEqual(c['f50-a', 'mobile-data'].status, 'SKIP')

    def test_kernel(self):
        c, _, _, _ = self.run_one(after=state_text(release=TAG, kernel='7.2.11'), before=state_text(kernel='7.2.9'))
        self.assertEqual((c['f50-a', 'kernel'].status, c['f50-a', 'kernel'].detail), ('PASS', '7.2.11 (was 7.2.9)'))
        c, _, _, _ = self.run_one(after=state_text(release=TAG, kernel='7.3.1'))
        self.assertEqual(c['f50-a', 'kernel'].status, 'FAIL')
        self.assertEqual(c['f50-a', 'kernel'].detail, '7.3.1 (was 7.2.10), expected 7.2')
        c, _, _, _ = self.run_one(after=state_text(release=TAG, kernel='7.20.1'))
        self.assertEqual(c['f50-a', 'kernel'].status, 'FAIL')
        # a board that names the 7.2 release it expects
        cfg = boards(board_dict(expect_kernel='7.2.10'))
        c, _, _, _ = self.run_one(cfg=cfg)
        self.assertEqual(c['f50-a', 'kernel'].status, 'PASS')
        c, _, _, _ = self.run_one(cfg=cfg, after=state_text(release=TAG, kernel='7.2.11'))
        self.assertEqual(c['f50-a', 'kernel'].status, 'FAIL')

    def test_a_failed_update_does_not_reboot(self):
        c, checks, tr, _ = self.run_one(apply_rc=1)
        self.assertEqual(c['f50-a', 'update'].status, 'FAIL')
        self.assertIn('ERROR: could not reach GitHub', c['f50-a', 'update'].detail)
        self.assertNotIn('reboot', tr.kinds('f50-a'))
        self.assertEqual({x.status for x in checks[2:]}, {'SKIP'})
        self.assertEqual(len(checks), len(smoke.CHECKS))

    def test_never_rebooted_into_another_default_boot(self):
        c, _, tr, _ = self.run_one(before=state_text(default_boot='recovery'))
        self.assertEqual(c['f50-a', 'update'].status, 'FAIL')
        self.assertIn('not linux', c['f50-a', 'update'].detail)
        self.assertEqual(tr.changing_calls(), [])
        c, _, tr, _ = self.run_one(after=state_text(release=TAG, default_boot='recovery'))
        self.assertEqual(c['f50-a', 'reboot'].status, 'FAIL')
        self.assertIn('not rebooted', c['f50-a', 'reboot'].detail)
        # a device without the file has no other default boot
        c, checks, _, _ = self.run_one(before=state_text(default_boot=''), after=state_text(release=TAG, default_boot=''))
        self.assertEqual(smoke.exit_code(checks), 0)

    def test_reboot_wait_is_bounded(self):
        c, checks, tr, clock = self.run_one(comes_back=False)
        self.assertEqual(c['f50-a', 'reboot'].status, 'FAIL')
        self.assertEqual(c['f50-a', 'reboot'].detail, 'ssh did not come back within 900 s (no answer)')
        self.assertLess(clock.t, 900 + 300 + 60)
        self.assertEqual(c['f50-a', 'release'].detail, 'not reached')
        c, _, _, clock = self.run_one(goes_down=False)
        self.assertEqual(c['f50-a', 'reboot'].detail, 'ssh still answers 300 s after the reboot command')
        c, _, _, clock = self.run_one(timeouts={'boot': 60}, boot_time=200)
        self.assertEqual(c['f50-a', 'reboot'].detail, 'ssh did not come back within 60 s (no answer)')

    def test_another_host_key_after_the_reboot(self):
        cfg = boards(board_dict())
        h, tr, clock = harness(cfg, {'f50-a': lambda c: FakeDevice(c)})
        orig = tr.ssh

        def ssh(board, command, stdin=None, timeout=120):
            r = orig(board, command, stdin, timeout)
            if stdin == smoke.REBOOT_SCRIPT:
                tr.devices['f50-a'].keys = [OTHER_KEY]
                tr.devices['f50-a'].comes_back = False
                tr.devices['f50-a'].up_at = None
            return r
        tr.ssh = ssh
        c = by_name(h.run()[0])
        self.assertIn('ssh did not come back', c['f50-a', 'reboot'].detail)

    def test_identity_after_the_reboot(self):
        cfg = boards(board_dict())
        h, tr, clock = harness(cfg, {'f50-a': lambda c: FakeDevice(c)})
        orig = tr.ssh

        def ssh(board, command, stdin=None, timeout=120):
            r = orig(board, command, stdin, timeout)
            if stdin == smoke.REBOOT_SCRIPT:
                tr.devices['f50-a'].identity = 'hostname=someone-else\ndevice=f50\n'
            return r
        tr.ssh = ssh
        checks = h.run()[0]
        c = by_name(checks)
        self.assertEqual(c['f50-a', 'identity-after'].status, 'FAIL')
        self.assertIn('hostname is someone-else', c['f50-a', 'identity-after'].detail)
        self.assertEqual([x for x in tr.kinds('f50-a') if x == 'state'], ['state'])   # no state read after it


class FromDirectory(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='mu300-test-'))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        for f in smoke.RELEASE_FILES + ('bad name;x',):
            (self.dir / f).write_text('x')
        self.cfg = boards(board_dict(update='from', from_path='/mnt/mu300-disk/smoke'))

    def test_the_release_files_are_those_of_the_contract(self):
        self.assertEqual(set(smoke.RELEASE_FILES),
                         {'SHA256SUMS', 'mu300-kernel-7.2.tar.gz', 'mu300-openwrt-luci-rootfs.tar.gz'})

    def test_uploaded_then_applied_from(self):
        h, tr, _ = harness(self.cfg, {'f50-a': lambda c: FakeDevice(c)}, release_dir=self.dir)
        self.assertEqual(h.local_problems(), [])
        checks, refused = h.run()
        self.assertEqual((refused, smoke.exit_code(checks)), ({}, 0))
        scp = [x for x in tr.calls if x[0] == 'scp']
        self.assertEqual(scp, [('scp', 'f50-a', tuple(sorted(smoke.RELEASE_FILES)), '/mnt/mu300-disk/smoke')])
        self.assertIn(f'env MU300_RELEASE={TAG} /opt/mu300/bin/mu300-update apply --from /mnt/mu300-disk/smoke',
                      tr.kinds('f50-a'))
        order = [c[3] if c[0] == 'ssh' else c[0] for c in tr.calls if c[0] in ('ssh', 'scp')]
        self.assertLess(order.index('state'), order.index('scp'))   # the state is read before anything is uploaded

    def test_without_a_release_directory_nothing_is_uploaded(self):
        h, tr, _ = harness(self.cfg, {'f50-a': lambda c: FakeDevice(c)})
        self.assertEqual(h.local_problems(), [])
        self.assertEqual(smoke.exit_code(h.run()[0]), 0)
        self.assertFalse([x for x in tr.calls if x[0] == 'scp'])

    def test_the_release_files_must_be_there(self):
        (self.dir / 'SHA256SUMS').unlink()
        h, tr, _ = harness(self.cfg, {'f50-a': lambda c: FakeDevice(c)}, release_dir=self.dir)
        self.assertIn('SHA256SUMS is not in', h.local_problems()[0])
        # a board that does not take the directory does not need it
        h, tr, _ = harness(boards(board_dict()), {'f50-a': lambda c: FakeDevice(c)}, release_dir=self.dir)
        self.assertEqual(h.local_problems(), [])


class Exploding:
    """A transport that must not be used: every call is recorded and fails the test."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def f(*a, **k):
            self.calls.append(name)
            raise AssertionError(f'{name} called in a dry run')
        return f


class Main(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='mu300-test-'))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.config = self.dir / 'smoke.json'
        self.config.write_text(json.dumps({'boards': [board_dict(), board_dict(
            name='u30-b', device='u30air', ssh_host='192.168.78.1',
            update='mirror', mirror_url='file:///srv/mirror')]}))

    def main(self, *argv, transport=None, clock=None):
        out = io.StringIO()
        rc = smoke.main(list(argv), transport=transport, clock=clock, out=out)
        return rc, out.getvalue()

    def test_dry_run_connects_to_nothing(self):
        tr = Exploding()
        rc, out = self.main('--config', str(self.config), '--dry-run', TAG, transport=tr)
        self.assertEqual(rc, 0)
        self.assertEqual(tr.calls, [])
        self.assertIn(f'ssh -p 22 root@192.168.77.1 env MU300_RELEASE={TAG} /opt/mu300/bin/mu300-update apply', out)
        self.assertIn(f'MU300_RELEASE_URL=file:///srv/mirror/{TAG}', out)
        self.assertIn(f'ssh-keyscan -p 22 192.168.78.1 offers {FPR}', out)
        rc, out = self.main('--config', str(self.config), '--dry-run', '--board', 'u30-b', TAG, transport=tr)
        self.assertNotIn('192.168.77.1', out)
        self.assertEqual(tr.calls, [])

    def test_usage_errors_exit_2(self):
        for argv in (('--config', str(self.config), 'latest'), ('--config', str(self.config)),
                     ('--config', str(self.dir / 'none.json'), TAG), ('--config', str(self.config), '--board', 'x', TAG),
                     ('--config', str(self.config), '--tag', 'v2026.10.16.2', TAG)):
            with self.subTest(argv=argv):
                self.assertEqual(self.main(*argv, transport=Exploding())[0], 2)

    def test_a_release_directory_names_the_tag(self):
        rd = self.dir / TAG
        rd.mkdir()
        rc, out = self.main('--config', str(self.config), '--dry-run', str(rd), transport=Exploding())
        self.assertEqual(rc, 0)
        self.assertIn(f'smoke test of {TAG}', out)
        bad = self.dir / 'not-a-tag'
        bad.mkdir()
        self.assertEqual(self.main('--config', str(self.config), '--dry-run', str(bad), transport=Exploding())[0], 2)

    def test_help_works(self):
        with self.assertRaises(SystemExit) as e, contextlib.redirect_stdout(io.StringIO()) as out:
            smoke.main(['--help'])
        self.assertEqual(e.exception.code, 0)
        self.assertIn('--dry-run', out.getvalue())

    def test_report_and_exit_codes(self):
        clock = FakeClock()
        k2 = key_line('192.168.78.1', 'u30')
        cfg = json.loads(self.config.read_text())
        cfg['boards'][1]['ssh']['fingerprint'] = smoke.ssh_key_fingerprint(k2)
        self.config.write_text(json.dumps(cfg))
        devs = {'f50-a': FakeDevice(clock),
                'u30-b': FakeDevice(clock, keys=[k2], device='u30air', after=state_text(release=OLD))}
        tr = FakeTransport(clock, devs)
        report = self.dir / 'report.md'
        rc, out = self.main('--config', str(self.config), '--report', str(report), TAG, transport=tr, clock=clock)
        self.assertEqual(rc, 1)
        self.assertEqual(report.read_text(), out)
        lines = out.splitlines()
        self.assertEqual(lines[0], f'# Release smoke test: {TAG}')
        body = [l for l in lines if l.startswith('- ')]
        self.assertEqual(len(body), 2 * len(smoke.CHECKS))
        for l in body:
            self.assertRegex(l, r'^- (PASS|FAIL|SKIP) (f50-a|u30-b) [a-z-]+(: .*)?$')
        self.assertIn(f'- FAIL u30-b release: {OLD}, expected {TAG} (was {OLD})', lines)
        self.assertIn(f'- PASS f50-a release: {TAG} (was {OLD})', lines)
        self.assertRegex(lines[-1], r'^\d+ passed, 2 failed, \d+ skipped$')
        # all well: 0
        devs['u30-b'].after = state_text(release=TAG)
        for d in devs.values():
            d.updated = False
            d.down_from = d.up_at = None
        tr.calls.clear()
        rc, out = self.main('--config', str(self.config), TAG, transport=tr, clock=clock)
        self.assertEqual(rc, 0, out)

    def test_identity_refusal_exits_2(self):
        clock = FakeClock()
        devs = {'f50-a': FakeDevice(clock), 'u30-b': FakeDevice(clock, keys=[OTHER_KEY])}
        tr = FakeTransport(clock, devs)
        rc, out = self.main('--config', str(self.config), TAG, transport=tr, clock=clock)
        self.assertEqual(rc, 2)
        self.assertIn('- REFUSED u30-b identity: ssh host key of 192.168.78.1:22', out)
        self.assertEqual(tr.changing_calls(), [])


if __name__ == '__main__':
    unittest.main()
