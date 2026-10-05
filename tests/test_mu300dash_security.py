"""The LuCI control panel's rpcd backend (luci-app-mu300's mu300dash) runs as root on input from the web UI: every
parameter is checked against an allow-list before anything runs, untrusted text reaches the adapters only as one exact
argument (or on stdin), and every reply is JSON whatever the adapters and the modem print.

`mu300dash call <method>` runs with the request on stdin, as rpcd runs it, under each shell. Every command it calls
is a stub that records its arguments (one file per call, NUL-separated) and prints what the test chose. jsonfilter is
an OpenWrt tool not found on the test machine: a stand-in in Python behaves as it does for what mu300dash asks of it
(an `@.key` / `@` path, a string printed raw, anything else as JSON, one value parsed and trailing text ignored,
raw control characters inside strings accepted), except that a NUL inside a string is printed instead of ending it,
which is the harder case for the backend."""
import json
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from helpers import TOP, ShellTest

APP = TOP / 'openwrt' / 'luci-app-mu300' / 'root'
DASH = APP / 'usr' / 'libexec' / 'rpcd' / 'mu300dash'
ADAPTERS = APP / 'usr' / 'libexec' / 'unisoc-modem'
ACL = APP / 'usr' / 'share' / 'rpcd' / 'acl.d' / 'luci-app-mu300.json'

JSONFILTER = r'''
import json, re, sys
args, src, exprs = sys.argv[1:], None, []
while args:
    opt, val, args = args[0], args[1], args[2:]
    if opt == '-s':
        src = val
    elif opt == '-i':
        src = open(val, 'rb').read().decode('utf-8', 'surrogateescape')
    elif opt == '-e':
        exprs.append(val)
    else:
        sys.exit(2)
if src is None:
    src = sys.stdin.buffer.read().decode('utf-8', 'surrogateescape')
try:
    doc = json.JSONDecoder(strict=False).raw_decode(src.lstrip())[0]
except ValueError:
    sys.stderr.write('Failed to parse json data\n')
    sys.exit(125)
found = []
for e in exprs:
    m = re.fullmatch(r'[@$]((?:\.[A-Za-z_][A-Za-z0-9_]*|\[(?:\*|\d+)\])*)', e)
    if not m:
        sys.exit(2)
    vals = [doc]
    for key, idx in re.findall(r'\.([A-Za-z_][A-Za-z0-9_]*)|\[(\*|\d+)\]', m.group(1)):
        nxt = []
        for v in vals:
            if key and isinstance(v, dict) and key in v:
                nxt.append(v[key])
            elif idx == '*' and isinstance(v, list):
                nxt.extend(v)
            elif idx and idx != '*' and isinstance(v, list) and int(idx) < len(v):
                nxt.append(v[int(idx)])
        vals = nxt
    found += vals
out = sys.stdout.buffer
for v in found:
    if isinstance(v, str):
        s = v
    elif v is None:
        s = 'null'
    elif isinstance(v, bool):
        s = 'true' if v else 'false'
    else:
        s = json.dumps(v, ensure_ascii=False)
    out.write(s.encode('utf-8', 'surrogateescape') + b'\n')
sys.exit(0 if found else 1)
'''

# A recording stub: one file per call in $STUB_CALLS (name and arguments, NUL-separated), what came on stdin for
# an SMS send, then the output the test put in $STUB_OUT/<name> (or the default given here).
RECORDER = r'''#!/bin/sh
f=$(mktemp "$STUB_CALLS/call.XXXXXX")
printf '%s\0' "{name}" "$@" > "$f"
[ "{name}" = sms ] && [ "${{1:-}}" = send ] && cat > "$f.stdin"
if [ "{name}" = cell ] && [ -f "$STUB_OUT/sig.json" ]; then cp "$STUB_OUT/sig.json" "$MU300_DASH_DIR/sig.json"; fi
if [ -f "$STUB_OUT/{name}" ]; then cat "$STUB_OUT/{name}"; else printf '%s\n' '{default}'; fi
exit 0
'''

DEFAULTS = {
    'dashboard-info': '{"ok":1,"host":"f50"}', 'cell': '{"ok":1}', 'action': '{"ok":1,"op":"x"}',
    'lock': '{"ok":1,"mode":{"label":"auto"}}', 'device-usb': '{"ok":1}', 'at': 'OK', 'sms': 'sent (12)',
}

# Hostile values: each is refused by every field that takes a fixed set, a number or a name.
HOSTILE = {
    'single quote': "'", 'double quote': '"', 'newline': 'x\ny', 'semicolon': ';reboot', 'subst': '$(reboot)',
    'backtick': '`reboot`', 'pipe': '|reboot', 'ampersand': '&reboot', 'leading dash': '-rf', 'nul': '\x00',
    'long': 'A' * 10000, 'non-ascii': 'ğüş中', 'object': {'a': 1}, 'array': [1],
}


def hostile_variants(valid):
    """The hostile values alone, and appended to a valid value (an exact pattern refuses both)."""
    out = dict(HOSTILE)
    for k, v in HOSTILE.items():
        if isinstance(v, str):
            out[k + ' after ' + valid] = valid + v
    out['trailing newline'] = valid + '\n'
    out['leading space'] = ' ' + valid
    return out


class Mu300Dash(ShellTest):
    def setUp(self):
        super().setUp()
        (self.stubs / 'jsonfilter.py').write_text(JSONFILTER)
        self.stub('jsonfilter', f'exec python3 "{self.stubs}/jsonfilter.py" "$@"')
        self.stub('uci', 'exit 1')
        self.stub('logger', 'exit 0')
        # setsid: record what would have been started detached, start nothing
        self.stub('setsid', 'f=$(mktemp "$STUB_CALLS/call.XXXXXX"); printf "%s\\0" setsid "$@" > "$f"')
        self.adapters = self.tmp / 'adapters'
        self.adapters.mkdir()
        for name, default in DEFAULTS.items():
            p = self.adapters / name
            p.write_text(RECORDER.format(name=name, default=default))
            p.chmod(0o755)
        self.out = self.tmp / 'out'
        self.out.mkdir()
        self.n = 0

    def output(self, name, text):
        """What the stub NAME prints from now on."""
        (self.out / name).write_text(text)

    def call(self, shell, method, params=None, raw=None):
        """Run `mu300dash call METHOD` with PARAMS (JSON) on stdin: (CompletedProcess, [[name, arg...], ...],
        {call index: stdin text})."""
        self.n += 1
        run = self.tmp / f'run{self.n}'
        calls = run / 'calls'
        calls.mkdir(parents=True)
        (run / 'dash').mkdir()
        for cache in ('cell.json', 'sig.json'):   # the collector's caches, as the stubs' output
            if (self.out / cache).exists():
                (run / 'dash' / cache).write_bytes((self.out / cache).read_bytes())
        stdin = raw if raw is not None else json.dumps(params or {}, ensure_ascii=False)
        env = self.env(STUB_CALLS=calls, STUB_OUT=self.out, MU300_DASH_BIN=self.adapters,
                       MU300_DASH_DIR=run / 'dash', MU300_AT=self.adapters / 'at', MU300_SMS_BIN=self.adapters / 'sms',
                       MU300_SMS_POOL=run / 'pool')
        r = subprocess.run(shell + [str(DASH), 'call', method], input=stdin.encode(), capture_output=True,
                           env=env, timeout=60)
        r.stdout, r.stderr = r.stdout.decode('utf-8', 'replace'), r.stderr.decode('utf-8', 'replace')
        recs, stdins = [], {}
        for f in sorted(calls.glob('call.*')):
            if f.suffix == '.stdin':
                continue
            rec = [a.decode('utf-8', 'surrogateescape') for a in f.read_bytes().split(b'\0')[:-1]]
            if rec[0] == 'setsid':
                rec = ['setsid', Path(rec[1]).name] + rec[2:]
            sin = f.with_name(f.name + '.stdin')
            if sin.exists():
                stdins[len(recs)] = sin.read_text()
            recs.append(rec)
        return r, recs, stdins

    def reply(self, r):
        """The reply: one JSON object, whatever happened."""
        try:
            obj = json.loads(r.stdout)
        except ValueError as e:
            self.fail(f'not JSON ({e}): {r.stdout!r} stderr={r.stderr!r}')
        self.assertIsInstance(obj, dict, r.stdout)
        return obj

    def calls_parallel(self, shell, jobs):
        """[(label, method, params)] -> [(label, CompletedProcess, calls, stdins)], several at a time."""
        with ThreadPoolExecutor(max_workers=8) as ex:
            res = list(ex.map(lambda j: (j[0],) + self.call(shell, j[1], j[2]), jobs))
        return res

    def assert_refused(self, shell, jobs):
        for label, r, recs, _ in self.calls_parallel(shell, jobs):
            with self.subTest(case=label):
                obj = self.reply(r)
                self.assertEqual(obj.get('ok'), 0, (label, r.stdout))
                self.assertTrue(obj.get('error'), r.stdout)
                self.assertEqual(recs, [], f'{label}: something ran')


class Inventory(Mu300Dash):
    METHODS = {'sysinfo', 'status', 'signal', 'act', 'at', 'at_history', 'lock_get', 'lock_set', 'sms_list',
               'sms_show', 'sms_send', 'sms_delete', 'sms_sync', 'usb_get', 'usb_set', 'usb_net_list', 'usb_net_add'}

    def test_list_declares_every_method(self):
        for shell in self.each_shell():
            r = self.script(shell, DASH, 'list')
            self.assertEqual(set(json.loads(r.stdout)), self.METHODS)

    def test_an_unknown_method_runs_nothing(self):
        for shell in self.each_shell():
            r, recs, _ = self.call(shell, 'rm -rf /', {})
            self.assertEqual(self.reply(r)['ok'], 0)
            self.assertEqual(recs, [])

    def test_a_request_too_large_is_refused(self):
        for shell in self.each_shell():
            r, recs, _ = self.call(shell, 'sms_send', {'num': '123', 'text': 'x' * 20000})
            self.assertEqual(self.reply(r)['ok'], 0)
            self.assertEqual(recs, [])

    def test_the_changed_scripts_parse(self):
        for shell in self.each_shell():
            for p in [DASH] + [ADAPTERS / n for n in ('action', 'at', 'device-usb', 'lock')]:
                with self.subTest(p=p.name):
                    r = subprocess.run(shell + ['-n', str(p)], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, r.stderr)


class Refusals(Mu300Dash):
    """Every hostile value in every field is refused with ok:0 before anything runs."""

    FIELDS = [
        # (method, valid params, field, a valid value of the field)
        ('act', {'op': 'radio', 'arg': 'on'}, 'op', 'radio'),
        ('act', {'op': 'radio', 'arg': 'on'}, 'arg', 'on'),
        ('act', {'op': 'data', 'arg': 'up'}, 'arg', 'up'),
        ('act', {'op': 'wifi', 'arg': 'off'}, 'arg', 'off'),
        ('act', {'op': 'vpn', 'arg': 'start'}, 'arg', 'start'),
        ('act', {'op': 'os', 'arg': 'android'}, 'arg', 'android'),
        ('act', {'op': 'reboot'}, 'arg', ''),
        ('act', {'op': 'modem-reset'}, 'arg', ''),
        ('lock_get', {}, 'fresh', '1'),
        ('lock_set', {'kind': 'endc', 'val': 'on'}, 'kind', 'endc'),
        ('lock_set', {'kind': 'mode', 'val': 'sa'}, 'val', 'sa'),
        ('lock_set', {'kind': 'endc', 'val': 'on'}, 'val', 'on'),
        ('lock_set', {'kind': 'auto_apply', 'val': 'off'}, 'val', 'off'),
        ('lock_set', {'kind': 'lte', 'val': '1,3'}, 'val', '1,3'),
        ('lock_set', {'kind': 'nr', 'val': '78'}, 'val', '78'),
        ('lock_set', {'kind': 'cell', 'val': 'nr:627264,393'}, 'val', 'nr:627264,393'),
        ('lock_set', {'kind': 'cell', 'val': 'off-lte'}, 'val', 'off-lte'),
        ('sms_list', {}, 'page', '2'),
        ('sms_show', {}, 'id', '12'),
        ('sms_send', {'num': '+905551112233', 'text': 'hi'}, 'num', '+905551112233'),
        ('sms_delete', {'id': '3'}, 'id', '3'),
        ('sms_delete', {'id': '3'}, 'sim', '1'),
        ('usb_set', {'kind': 'role', 'value': 'host', 'scope': '', 'auto': '0'}, 'kind', 'role'),
        ('usb_set', {'kind': 'role', 'value': 'host', 'scope': '', 'auto': '0'}, 'value', 'host'),
        ('usb_set', {'kind': 'role', 'value': 'host', 'scope': '', 'auto': '0'}, 'auto', '0'),
        ('usb_set', {'kind': 'role', 'value': 'host', 'scope': '', 'auto': '0'}, 'scope', ''),
        ('usb_set', {'kind': 'net', 'value': 'ncm', 'scope': 'once', 'auto': '1'}, 'value', 'ncm'),
        ('usb_set', {'kind': 'net', 'value': 'ncm', 'scope': 'once', 'auto': '1'}, 'scope', 'once'),
        ('usb_set', {'kind': 'net', 'value': 'ncm', 'scope': 'once', 'auto': '1'}, 'auto', '1'),
        ('usb_net_add', {}, 'iface', 'eth1'),
    ]

    def test_hostile_values_are_refused(self):
        jobs = []
        for method, base, field, valid in self.FIELDS:
            for name, bad in hostile_variants(valid).items():
                if name.startswith('leading space') and valid == '':
                    continue
                if field == 'iface' and name == 'leading dash after eth1':
                    continue   # eth1-rf is a valid interface name, and not an option
                jobs.append((f'{method}.{field}={name}', method, dict(base, **{field: bad})))
        for shell in self.each_shell():
            self.assert_refused(shell, jobs)

    def test_values_out_of_range_are_refused(self):
        jobs = [
            ('act op unknown', 'act', {'op': 'shell', 'arg': 'on'}),
            ('lock val mode', 'lock_set', {'kind': 'mode', 'val': '5g'}),
            ('lock lte too many', 'lock_set', {'kind': 'lte', 'val': ','.join(['1'] * 33)}),
            ('lock lte four digits', 'lock_set', {'kind': 'lte', 'val': '1000'}),
            ('lock lte empty item', 'lock_set', {'kind': 'lte', 'val': '1,,3'}),
            ('lock cell arfcn', 'lock_set', {'kind': 'cell', 'val': 'nr:12345678,1'}),
            ('lock cell pci', 'lock_set', {'kind': 'cell', 'val': 'lte:1650,12345'}),
            ('lock cell two pairs', 'lock_set', {'kind': 'cell', 'val': 'nr:1,2,3'}),
            ('lock cell rat', 'lock_set', {'kind': 'cell', 'val': 'gsm:1,2'}),
            ('sms id seven digits', 'sms_show', {'id': '1234567'}),
            ('sms id empty', 'sms_show', {'id': ''}),
            ('sms delete id', 'sms_delete', {'id': 'all2'}),
            ('sms num too long', 'sms_send', {'num': '1' * 21, 'text': 'hi'}),
            ('sms num plus only', 'sms_send', {'num': '+', 'text': 'hi'}),
            ('sms num inner plus', 'sms_send', {'num': '12+3', 'text': 'hi'}),
            ('sms page', 'sms_list', {'page': '1234567'}),
            ('usb role mode', 'usb_set', {'kind': 'role', 'value': 'ncm', 'scope': '', 'auto': '0'}),
            ('usb net role', 'usb_set', {'kind': 'net', 'value': 'host', 'scope': 'once', 'auto': '0'}),
            ('usb iface dots', 'usb_net_add', {'iface': '..'}),
            ('usb iface slash', 'usb_net_add', {'iface': 'eth1/../x'}),
            ('usb iface 16', 'usb_net_add', {'iface': 'e' * 16}),
            ('usb iface empty', 'usb_net_add', {'iface': ''}),
        ]
        for shell in self.each_shell():
            self.assert_refused(shell, jobs)


class Passthrough(Mu300Dash):
    """A valid value reaches the adapter as one exact argument."""

    CASES = [
        # (method, params, the calls wanted)
        ('act', {'op': 'radio', 'arg': 'on'}, [['setsid', 'action', 'radio', 'on']]),
        ('act', {'op': 'reboot', 'arg': None}, [['setsid', 'action', 'reboot', '']]),
        ('act', {'op': 'vpn', 'arg': 'stop'}, [['setsid', 'action', 'vpn', 'stop']]),
        ('act', {'op': 'wifi', 'arg': 'off'}, [['action', 'wifi', 'off']]),
        ('act', {'op': 'os', 'arg': 'android'}, [['action', 'os', 'android']]),
        ('lock_get', {}, [['lock', 'get']]),
        ('lock_get', {'fresh': '1'}, [['lock', 'get', 'fresh']]),
        ('lock_set', {'kind': 'lte', 'val': '1,3,41'}, [['setsid', 'lock', 'apply', 'lte', '1,3,41']]),
        ('lock_set', {'kind': 'nr', 'val': ''}, [['setsid', 'lock', 'apply', 'nr', '']]),
        ('lock_set', {'kind': 'cell', 'val': 'lte:1650,211'}, [['setsid', 'lock', 'apply', 'cell', 'lte:1650,211']]),
        ('lock_set', {'kind': 'cell', 'val': 'auto'}, [['setsid', 'lock', 'apply', 'cell', 'auto']]),
        ('lock_set', {'kind': 'mode', 'val': '4g'}, [['setsid', 'lock', 'apply', 'mode', '4g']]),
        ('sms_list', {'page': 2}, [['sms', 'list', '2']]),
        ('sms_list', {}, [['sms', 'list', '1']]),
        ('sms_show', {'id': '12'}, [['sms', 'show', '12']]),
        ('sms_delete', {'id': 'all', 'sim': True}, [['sms', 'delete', 'all', '--sim']]),
        ('sms_delete', {'id': '7', 'sim': False}, [['sms', 'delete', '7']]),
        ('sms_delete', {'id': '7', 'sim': '1'}, [['sms', 'delete', '7', '--sim']]),
        ('sms_sync', {}, [['setsid', 'sms', 'sync']]),
        ('usb_get', {}, [['device-usb', 'get']]),
        ('usb_set', {'kind': 'role', 'value': 'device', 'scope': '', 'auto': '0'},
         [['device-usb', 'set-role', 'device', '0']]),
        ('usb_set', {'kind': 'net', 'value': 'rndis', 'scope': 'permanent', 'auto': '1'},
         [['device-usb', 'set-net', 'rndis', 'permanent', '1']]),
        ('usb_net_list', {}, [['device-usb', 'net-list']]),
        ('usb_net_add', {'iface': 'eth1'}, [['device-usb', 'net-add', 'eth1']]),
        ('usb_net_add', {'iface': 'enx00e04c680001'}, [['device-usb', 'net-add', 'enx00e04c680001']]),
    ]

    def test_valid_values_reach_the_adapter(self):
        jobs = [(f'{m} {p}', m, p) for m, p, _ in self.CASES]
        for shell in self.each_shell():
            for (label, r, recs, _), (_, _, want) in zip(self.calls_parallel(shell, jobs), self.CASES):
                with self.subTest(case=label):
                    self.reply(r)
                    self.assertEqual(recs, want, r.stdout)

    def test_at_commands_pass_as_one_exact_argument(self):
        ok = ['AT', 'at+cgsn', 'AT+SP5GCMDS="get nr support_band"', "AT+X='a'", 'AT$(reboot)', 'AT`reboot`',
              'AT|reboot', 'AT&F', 'AT -rf', 'AT+' + 'A' * 509]
        bad = ['', 'ATI;reboot', 'AT\nreboot', 'AT\rAT+CFUN=0', 'AT\x00x', 'AT' + 'A' * 10000, 'AT+' + 'A' * 510,
               'ATğ', '-tAT', ' AT', 'reboot', 'AT+SPENGMD=0,1,0', 'at+spengmd=0,1,0', 'AT+SPENGMD = 0,1,0', 'AT\t',
               {'a': 1}]
        for shell in self.each_shell():
            res = self.calls_parallel(shell, [(repr(c), 'at', {'cmd': c}) for c in ok])
            for (label, r, recs, _), c in zip(res, ok):
                with self.subTest(cmd=label):
                    self.assertEqual(self.reply(r), {'ok': 1, 'cmd': c, 'reply': 'OK'})
                    self.assertEqual(recs, [['at', '-t', '8', c]])
            self.assert_refused(shell, [(repr(c), 'at', {'cmd': c}) for c in bad])

    def test_sms_text_goes_on_stdin(self):
        texts = ["-n it's \"quoted\" $(reboot) `id` | & ; \\ end", 'two\nlines\tand tab', 'ğüşçöı 中文 😀',
                 '-', 'ğ' * 480]
        for shell in self.each_shell():
            for t in texts:
                with self.subTest(text=t[:20]):
                    r, recs, stdins = self.call(shell, 'sms_send', {'num': '+905551112233', 'text': t})
                    self.assertEqual(self.reply(r)['ok'], 1, r.stdout)
                    self.assertEqual(recs, [['sms', 'send', '--stdin', '+905551112233']])
                    self.assertEqual(stdins, {0: t})
            self.assert_refused(shell, [
                ('empty', 'sms_send', {'num': '123', 'text': ''}),
                ('nul', 'sms_send', {'num': '123', 'text': 'a\x00b'}),
                ('bell', 'sms_send', {'num': '123', 'text': 'a\x07b'}),
                ('cr', 'sms_send', {'num': '123', 'text': 'a\rb'}),
                ('481', 'sms_send', {'num': '123', 'text': 'a' * 481}),
                ('10000', 'sms_send', {'num': '123', 'text': 'a' * 10000}),
            ])
            # a JSON object where the text belongs is only text: it travels on stdin like any other
            r, recs, stdins = self.call(shell, 'sms_send', {'num': '123', 'text': {'a': '$(id)'}})
            self.assertEqual(recs, [['sms', 'send', '--stdin', '123']])
            self.assertEqual(json.loads(stdins[0]), {'a': '$(id)'})


NASTY = 'he said "hi" \\ back\\slash\nnew line\ttab\rcr \x01 \x1f end'
NASTY_JSON = json.dumps({'ok': 1, 'op': NASTY, 'name': NASTY, 'nested': {'k': [NASTY]}}, ensure_ascii=False)


class Replies(Mu300Dash):
    """Whatever an adapter, the modem or the SMS pool prints, the reply is one JSON object."""

    VALID = [
        ('sysinfo', {}), ('status', {}), ('signal', {}), ('act', {'op': 'wifi', 'arg': 'on'}),
        ('act', {'op': 'radio', 'arg': 'off'}), ('at', {'cmd': 'ATI'}), ('at_history', {}), ('lock_get', {}),
        ('lock_get', {'fresh': '1'}), ('lock_set', {'kind': 'lte', 'val': '1'}), ('sms_list', {}),
        ('sms_show', {'id': '1'}), ('sms_send', {'num': '123', 'text': 'x'}), ('sms_delete', {'id': '1'}),
        ('sms_sync', {}), ('usb_get', {}), ('usb_set', {'kind': 'role', 'value': 'device', 'auto': '0'}),
        ('usb_net_list', {}), ('usb_net_add', {'iface': 'eth1'}),
    ]
    OUTPUTS = {
        'garbage': NASTY,
        'json with nasty strings': NASTY_JSON,
        'json then garbage': '{"ok":1} "}, "x": "',
        'json injection': '{"ok":1,"error":"x"}","admin":true,"y":"',
        'empty': '',
        'array': '[1,2]',
    }

    def test_every_reply_is_json(self):
        for shell in self.each_shell():
            for oname, text in self.OUTPUTS.items():
                for name in list(DEFAULTS) + ['cell.json', 'sig.json']:
                    self.output(name, text)
                res = self.calls_parallel(shell, [(f'{oname}: {m}', m, p) for m, p in self.VALID])
                for label, r, _, _ in res:
                    with self.subTest(case=label):
                        self.reply(r)

    def test_adapter_json_is_passed_through_as_json(self):
        for name in DEFAULTS:
            self.output(name, NASTY_JSON)
        want = json.loads(NASTY_JSON)
        for shell in self.each_shell():
            for m, p in [('sysinfo', {}), ('act', {'op': 'wifi', 'arg': 'on'}), ('lock_get', {}), ('usb_get', {}),
                         ('usb_net_list', {})]:
                with self.subTest(method=m):
                    r, _, _ = self.call(shell, m, p)
                    self.assertEqual(self.reply(r), want)
            r, _, _ = self.call(shell, 'status', {})
            self.assertEqual(self.reply(r)['info'], want)

    def test_modem_and_sms_text_is_escaped(self):
        text = NASTY.replace('\r', '')
        for shell in self.each_shell():
            self.output('at', text)
            r, _, _ = self.call(shell, 'at', {'cmd': 'AT+COPS?'})
            self.assertEqual(self.reply(r)['reply'], text)
            self.output('sms', text)
            r, _, _ = self.call(shell, 'sms_show', {'id': '4'})
            self.assertEqual(self.reply(r)['text'], text)
            r, _, _ = self.call(shell, 'sms_send', {'num': '123', 'text': 'sent'})
            self.assertEqual(self.reply(r)['ok'], 0)

    def test_sms_list_fields_are_escaped(self):
        row = '%-7s %-7s %-3s %-18s %-21s %s' % ('3', 'unread', 'mt', 'A"B\\C', '26/10/05,12:00:00',
                                                   'say "hi" \\o/ \x01 end')
        self.output('sms', 'pool: 1 message(s), 1 unread - page 1/1 (10 per page)\n' + row + '\n')
        for shell in self.each_shell():
            r, _, _ = self.call(shell, 'sms_list', {'page': '1'})
            obj = self.reply(r)
            self.assertEqual(obj['total'], 1)
            self.assertEqual(obj['msgs'][0]['peer'], 'A"B\\C')
            self.assertEqual(obj['msgs'][0]['preview'], 'say "hi" \\o/ \x01 end')

    def test_at_history_is_escaped(self):
        for shell in self.each_shell():
            r, _, _ = self.call(shell, 'at', {'cmd': 'AT+X="a\\b"'})
            self.reply(r)
            hist = self.tmp / f'run{self.n}' / 'dash' / 'at-history'
            hist.write_text(hist.read_text() + 'x "quoted" \\ \x02\n')
            r = subprocess.run(shell + [str(DASH), 'call', 'at_history'], input='{}', capture_output=True, text=True,
                               env=self.env(MU300_DASH_DIR=hist.parent), timeout=60)
            obj = self.reply(r)
            self.assertIn('AT+X="a\\b"', obj['history'])
            self.assertIn('x "quoted" \\ \x02', obj['history'])


class Adapters(Mu300Dash):
    """The adapters mu300dash starts guard their own input and escape what the modem and sysfs say."""

    def adapter(self, shell, name, *args, **env):
        calls = self.tmp / f'adapter{self.n}'
        self.n += 1
        calls.mkdir()
        e = dict(STUB_CALLS=calls, STUB_OUT=self.out, MU300_AT=self.adapters / 'at', MU300_DASH_DIR=self.tmp / 'run',
                 UNISOC_APPLY_DIR=self.tmp / 'apply')
        e.update(env)
        r = self.script(shell, ADAPTERS / name, *args, **e)
        recs = [f.read_bytes().split(b'\0')[:-1] for f in calls.glob('call.*')]
        return r, recs

    def test_lock_apply_refuses_what_it_does_not_know(self):
        bad = [('mode', '5g'), ('mode', 'sa;reboot'), ('endc', 'yes'), ('auto_apply', 'on\n'), ('lte', '1,3;x'),
               ('lte', '-1'), ('nr', '78,'), ('nr', ',78'), ('nr', '1,,3'), ('lte', '1' * 128),
               ('cell', 'nr:627264,393;x'), ('cell', 'nr:627264'), ('cell', 'gsm:1,2'), ('cell', 'nr:12345678,1'),
               ('cell', 'lte:1,2,3'), ('cell', 'lte:$(id),1'), ('shell', 'on')]
        for shell in self.each_shell():
            for kind, val in bad:
                with self.subTest(kind=kind, val=val):
                    r, recs = self.adapter(shell, 'lock', 'apply', kind, val)
                    self.assertEqual(r.returncode, 2, r.stderr)
                    self.assertEqual(recs, [])

    def test_lock_get_escapes_the_modem(self):
        self.output('at', '+SPTESTMODE: 1"2\\,134,0\n+SPLBAND: 0,"x\\\x01\n+SP5GRAN: 1\n+ENDC: 1\nOK')
        for shell in self.each_shell():
            r, _ = self.adapter(shell, 'lock', 'get', 'fresh')
            obj = json.loads(r.stdout)
            self.assertEqual(obj['mode']['work'], '1"2\\')
            self.assertEqual(obj['lte']['raw'], '0,"x\\\x01')

    def test_device_usb_escapes_sysfs_and_refuses_odd_names(self):
        role = self.tmp / 'role'
        role.write_text('ho"st\\\n')
        for shell in self.each_shell():
            r, _ = self.adapter(shell, 'device-usb', 'get', MU300_USB_ROLE_FILE=role)
            self.assertEqual(json.loads(r.stdout)['role'], 'ho"st\\')
            role.write_text('host\n')
            for name in ['-x', '..', '.', 'e' * 16, 'a/b', 'a b', 'a"b']:
                with self.subTest(name=name):
                    (self.tmp / 'net' / name.replace('/', '_')).mkdir(parents=True, exist_ok=True)
                    r, recs = self.adapter(shell, 'device-usb', 'net-add', name, MU300_USB_ROLE_FILE=role,
                                           MU300_USB_NET_CLASS=self.tmp / 'net', MU300_USB_IP_BIN=self.adapters / 'at')
                    self.assertEqual(json.loads(r.stdout), {'ok': 0, 'error': 'Not a USB network interface'})
                    self.assertEqual(recs, [])
            role.write_text('ho"st\\\n')

    def test_action_replies_are_json(self):
        self.stub('wifi', 'exit 0')
        for shell in self.each_shell():
            for args in [('wifi', 'on'), ('vpn', 'start'), ('os', 'android'), ('nonsense', '"\\')]:
                with self.subTest(args=args):
                    r, _ = self.adapter(shell, 'action', *args)
                    self.assertIsInstance(json.loads(r.stdout.splitlines()[-1]), dict)

    def test_at_adapter_sends_one_command(self):
        for shell in self.each_shell():
            for cmd in ['AT\rAT+CFUN=0', 'AT\nAT+CFUN=0']:
                r, _ = self.adapter(shell, 'at', '-t', '8', cmd)
                self.assertEqual(r.returncode, 2, r.stderr)


class Acl(unittest.TestCase):
    READ = {'sysinfo', 'status', 'signal', 'at_history', 'lock_get', 'sms_list', 'sms_show', 'usb_get',
            'usb_net_list'}
    WRITE = {'act', 'at', 'lock_set', 'sms_send', 'sms_delete', 'sms_sync', 'usb_set', 'usb_net_add'}

    def test_actions_need_write_access(self):
        acl = json.loads(ACL.read_text())['luci-app-mu300']
        read = set(acl['read']['ubus']['mu300dash'])
        write = set(acl['write']['ubus']['mu300dash'])
        self.assertEqual(read, self.READ)
        self.assertEqual(write, self.WRITE)
        self.assertEqual(read | write, Inventory.METHODS)


if __name__ == '__main__':
    unittest.main()
