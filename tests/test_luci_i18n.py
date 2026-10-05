"""The control panel's catalogs: complete in Turkish and Chinese, no Chinese outside them (spec, Translations)."""
import importlib.util, json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from helpers import TOP

TOOL = [sys.executable, str(TOP / 'tools' / 'luci-i18n.py')]
APP = TOP / 'openwrt' / 'luci-app-mu300'
CJK = '\u4e2d\u6587'        # written as escapes: this file is not one of the places Chinese is allowed

VIEW = 'htdocs/luci-static/resources/view/mu300/home.js'
DASH = 'root/usr/libexec/rpcd/mu300dash'


def run(*args):
    return subprocess.run(TOOL + list(args), capture_output=True, text=True, encoding='utf-8')


def po(header_lang, pairs):
    out = f'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n"Language: {header_lang}\\n"\n'
    for k, v in pairs:
        out += f'\nmsgid "{k}"\nmsgstr "{v}"\n'
    return out


# A small app with every kind of source; check passes on it, and each test breaks one thing.
MINI = {
    'root/usr/share/luci/menu.d/luci-app-mu300.json':
        '{\n "admin/home": {\n  "title": "Dashboard",\n  "order": 5\n }\n}\n',
    'root/usr/share/rpcd/acl.d/luci-app-mu300.json':
        '{\n "luci-app-mu300": {\n  "description": "MU300 control panel",\n  "read": {}\n }\n}\n',
    VIEW: (
        "'use strict';\n"
        "// _('Not a message: a comment') and /* _('nor this') */\n"
        "var s = \"_('nor a string')\", re = /_\\('nor a regex'\\)/;\n"
        "return L.view.extend({ render: function(res, carrier) {\n"
        "  var n = 3, a = n / 2 / 1;\n"
        "  E('p', {}, _('It\\'s \"quoted\" \\u0041')); x._('not the translate function');\n"
        "  E('p', {}, _(\"· %d messages\").format(n));\n"
        "  E('p', {}, `${_('In a template')}`);\n"
        "  E('p', {}, [ _(res.error), _( res.message ), _(carrier.name) ]);\n"
        "} });\n"),
    DASH: (
        "#!/bin/sh\n"
        "refuse() { printf '{\"ok\":0,\"error\":%s}\\n' \"$(json_str \"$1\")\"; }\n"
        "reply_obj() { json_obj \"$1\" || refuse \"$2\"; }\n"
        "m_a() { printf '{\"ok\":0,\"error\":\"Literal error\"}\\n'; }\n"
        "m_b() { [ -n \"$x\" ] || { refuse 'Refused here'; return; }; }\n"
        "m_c() { reply_obj \"$(foo)\" 'No valid reply'; }\n"
        "m_d() {\n"
        "    # i18n: Built elsewhere\n"
        "    printf '{\"ok\":0,\"error\":%s}\\n' \"$(json_str \"$msg\")\"\n"
        "}\n"),
    'root/usr/libexec/unisoc-modem/action': (
        "#!/bin/sh\n"
        "fail() { printf '{\"ok\":0,\"op\":%s,\"error\":%s}\\n' \"$(json_str \"$1\")\" \"$(json_str \"$2\")\"; }\n"
        "radio_on || fail \"radio on\" \"Radio did not come up\"\n"
        "printf '{\"ok\":1,\"message\":\"Done\"}\\n'\n"),
    'root/usr/libexec/unisoc-modem/device-usb': (
        "#!/bin/sh\n"
        "error() { printf '{\"ok\":0,\"error\":%s}\\n' \"$(json_str \"$1\")\"; exit 1; }\n"
        "[ -w \"$F\" ] || error 'USB role switch is unavailable'\n"),
}
MESSAGES = ['Dashboard', 'MU300 control panel', 'It\'s "quoted" A', '· %d messages', 'In a template',
            'Literal error', 'Refused here', 'No valid reply', 'Built elsewhere', 'Radio did not come up', 'Done',
            'USB role switch is unavailable']


def po_str(m):
    return m.replace('\\', '\\\\').replace('"', '\\"')


class Catalogs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mu300-test-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mini(self):
        root = self.tmp / 'mini'
        for rel, text in MINI.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding='utf-8')
        for lang, mark in (('tr', 'TR'), ('zh_Hans', CJK)):
            (root / 'po' / lang).mkdir(parents=True)
            (root / 'po' / lang / 'mu300.po').write_text(
                po(lang, [(po_str(m), po_str(m.replace('messages', 'x') + ' ' + mark)) for m in MESSAGES]),
                encoding='utf-8')
        return root

    @unittest.expectedFailure          # the fork's state; Tasks 13-16 make it clean, Task 16 removes this line
    def test_check_is_clean(self):
        r = run('check')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_default_check_scans_the_repository(self):
        # until Task 16 makes test_check_is_clean pass: the default run works, and finds the fork's Chinese
        r = run('check')
        self.assertNotIn('Traceback', r.stderr)
        self.assertIn('cjk: openwrt/luci-app-mu300/', r.stdout)
        self.assertNotIn('tools/i18n.sh', r.stdout)

    def test_extract_finds_every_kind_of_message_and_nothing_else(self):
        r = run('extract', '--root', str(self.mini()))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), MESSAGES)

    def test_minimal_app_is_clean(self):
        r = run('check', '--root', str(self.mini()))
        self.assertEqual((r.returncode, r.stdout), (0, ''), r.stderr)

    def test_update_adds_missing_and_removes_stale_in_order_of_first_use(self):
        root = self.mini()
        tr = root / 'po' / 'tr' / 'mu300.po'
        tr.write_text(po('tr', [('Done', 'Tamam'), ('Gone', 'Yok')]), encoding='utf-8')
        shutil.rmtree(root / 'po' / 'zh_Hans')
        r = run('update', '--root', str(root))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        want = po('tr', [(po_str(m), 'Tamam' if m == 'Done' else '') for m in MESSAGES])
        self.assertEqual(tr.read_text(encoding='utf-8'), want)
        zh = (root / 'po' / 'zh_Hans' / 'mu300.po').read_text(encoding='utf-8')
        self.assertIn('"Language: zh_Hans\\n"', zh)
        self.assertEqual(zh.count('msgstr ""'), len(MESSAGES) + 1)
        r = run('check', '--root', str(root))       # still failing: the new entries have no translation yet
        self.assertEqual(r.returncode, 1)
        self.assertIn('empty msgstr', r.stdout)
        self.assertNotIn('stale', r.stdout)

    def test_checker_catches_each_rule(self):
        # each rule against a temporary copy of the app: the problems the defect adds must name the rule
        def append(rel, text):
            def apply(root):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                with open(root / rel, 'a', encoding='utf-8') as f:
                    f.write(text)
            return apply

        def tr_entry(msgid, msgstr):
            return append('po/tr/mu300.po', f'\nmsgid "{msgid}"\nmsgstr "{msgstr}"\n')

        def both(*steps):
            def apply(root):
                for s in steps:
                    s(root)
            return apply

        defects = [
            ('cjk', append(VIEW, f"\nvar label = '{CJK}';\n")),
            ('cjk', append('root/etc/init.d/unisoc-modem-ui', f'\n# {CJK}\n')),
            ('missing', append(VIEW, "\nE('p', {}, _('A message nobody translated'));\n")),
            ('missing', append(DASH, "\nm_z() { refuse 'A refusal nobody translated'; }\n")),
            ('missing', append(DASH, "\nm_y() { printf '{\"ok\":0,\"error\":\"A printf nobody translated\"}\\n'; }\n")),
            ('missing', append(DASH, '\n# i18n: A comment message nobody translated\n')),
            ('missing', both(append(VIEW, "\nE('p', {}, _('Empty in tr'));\n"), tr_entry('Empty in tr', ''))),
            ('missing', append(DASH, '\nm_x() { awk \'END { print "{\\"error\\":\\"Escaped nobody translated\\"}" }\'; }\n')),
            ('placeholder', both(append(VIEW, "\nE('p', {}, _('%d new messages').format(n));\n"),
                                 tr_entry('%d new messages', '%s yeni mesaj'))),
            ('stale', tr_entry('A message no source uses', 'Kimse kullanmiyor')),
            ('dynamic', append(VIEW, "\nE('p', {}, _(someVariable));\n")),
            ('dynamic', append(VIEW, "\nE('p', {}, _('Half ' + 'a sentence'));\n")),
            ('dynamic', append(DASH, '\nm_w() { printf \'{"ok":0,"error":%s}\\n\' "$(json_str "$msg")"; }\n')),
            ('dynamic', append(DASH, '\nm_v() { printf \'{"ok":0,"error":"%s"}\\n\' "$msg"; }\n')),
            ('plural', append(VIEW, "\nE('p', {}, N_(n, 'One item', '%d items').format(n));\n")),
        ]
        for base_name, base in (('the app', APP), ('a clean app', self.mini())):
            before = run('check', '--root', str(base))
            for n, (rule, defect) in enumerate(defects):
                with self.subTest(base=base_name, rule=rule, n=n):
                    root = self.tmp / f'{base.name}-{n}'
                    shutil.copytree(base, root)
                    defect(root)
                    after = run('check', '--root', str(root))
                    self.assertEqual(after.returncode, 1, after.stdout + after.stderr)
                    old = set(before.stdout.splitlines())      # paths are printed relative to --root
                    new = [line for line in after.stdout.splitlines() if line not in old]
                    self.assertTrue(any(line.startswith(rule + ': ') for line in new),
                                    f'{rule}: not among the new problems:\n' + '\n'.join(new))
                    shutil.rmtree(root)


COMMON = APP / 'htdocs/luci-static/resources/mu300/common.js'
BUILD = TOP / 'openwrt' / 'build-rootfs.sh'


def catalog(lang):
    """{msgid: msgstr} of po/<lang>/mu300.po, read as the tool reads it."""
    spec = importlib.util.spec_from_file_location('luci_i18n', TOP / 'tools' / 'luci-i18n.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return {e['msgid']: e['msgstr'] for e in tool.read_po(APP / 'po' / lang / 'mu300.po')[1]}


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed for the LuCI JS tests')
class Languages(unittest.TestCase):
    """common.js under Node, with LuCI's _() and String.prototype.format stubbed: _ looks a message up in the
    catalog LuCI would load for the browser's language (none for English or a language the app does not have).
    The Chinese expectations are written as escapes, as everywhere in this file."""

    def run_js(self, lang, body):
        """Run body (an async function body) with M = common.js loaded for lang; returns {result: body's value,
        used: every _() argument, in order}. The stubs: one DOM element per createElement, every getElementById the
        toast container (notes collects what it is given), window.setInterval keeps its callback as tick, rpc calls
        answer rpcReply, and setTimeout does nothing."""
        cat = catalog(lang) if lang in ('tr', 'zh_Hans') else {}
        harness = r'''
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const CATALOG = %s;
String.prototype.format = function() {
    const args = arguments; let i = 0;
    return this.replace(/%%(%%|s|d)/g, (m, c) => c === '%%' ? '%%' : c === 'd' ? String(Math.trunc(args[i++])) : String(args[i++]));
};
const used = [];
const _ = (s) => { used.push(s); return Object.prototype.hasOwnProperty.call(CATALOG, s) ? CATALOG[s] : s; };
let aurora = '', selected, tick, rpcReply = {};
const notes = [];
const toasts = { appendChild: (t) => notes.push(t) };
const element = () => { const parts = {}; return { classList: { add: () => {} }, addEventListener: () => {},
    remove: () => {}, querySelector: (sel) => parts[sel] || (parts[sel] = {}) }; };
const root = { classList: { remove: () => {}, toggle: (name, value) => { selected = value; } } };
const document = { documentElement: root, body: { appendChild: () => {} }, getElementById: () => toasts,
    createElement: element, head: { appendChild: () => {} } };
const style = () => ({ getPropertyValue: (key) => key === '--surface' ? aurora : key === '--background-color-high' ? '#fff' : '' });
const window = { setInterval: (fn) => { tick = fn; } };
const flush = () => new Promise((r) => setImmediate(r));
const M = new Function('rpc', 'baseclass', '_', 'document', 'getComputedStyle', 'window', 'setTimeout', src)(
    { declare: () => () => rpcReply }, { extend: (obj) => obj }, _, document, style, window, () => 0);
(async function() { %s })().then((result) => process.stdout.write(JSON.stringify({ result: result, used: used })),
    (e) => { console.error(e); process.exit(1); });
''' % (json.dumps(cat, ensure_ascii=True), body)
        r = subprocess.run(['node', '-', str(COMMON)], input=harness, capture_output=True, text=True,
                           encoding='utf-8')
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    RENDER = '''
const sms = (id, peer) => ({ id: String(id), dir: 'mt', peer: peer, preview: 'text ' + id });
M.watchSms();
rpcReply = { msgs: [ sms(1, '10086') ] }; tick(); await flush();          // the first read only sets the baseline
rpcReply = { msgs: [ sms(3, ''), sms(2, '10086'), sms(1, '10086') ] }; tick(); await flush();
return {
    quality: [ M.qLabel(-85), M.qLabel(-95), M.qLabel(-105), M.qLabel(-115), M.qLabel() ],
    uptime: [ M.fmtUptime(90061), M.fmtUptime(3720), M.fmtUptime(300) ],
    neighbours: M.neighborRows({}),
    locks: M.neighborRows({ neigh: [ { rat: 'nr', band: 78, pci: 1, arfcn: 2, rsrp: -80 },
                                     { rat: 'lte', band: 3, pci: 4, arfcn: 5, rsrp: -90 } ] }, 'nr:2,1'),
    colour: M.qCol(M.qLabel(-115)),
    sms: notes.map((n) => n.querySelector('b').textContent)
};'''

    def test_three_languages(self):
        # The fork's case was a sentence assembled from fragments ('Link & traffic' + ' · ' + 'No response', a pair
        # that exists only in its test). common.js's own assembled sentence was the new-SMS banner, ('new SMS · ' in
        # Chinese) + sender; it is now one message with a placeholder, rendered whole in each language, with the unknown-
        # sender fallback translated inside it.
        want = {
            None: (['Excellent', 'Good', 'Fair', 'Poor', 'Unknown'], ['1 d 1 h', '1 h 2 min', '5 min'],
                   'No neighbor-cell data', 'Locked', 'Lock', ['New SMS · Unknown number', 'New SMS · 10086']),
            'tr': (['Mükemmel', 'İyi', 'Orta', 'Zayıf', 'Bilinmiyor'],
                   ['1 gün 1 sa', '1 sa 2 dk', '5 dk'], 'Komşu hücre verisi yok', 'Kilitli', 'Kilitle',
                   ['Yeni SMS · Bilinmeyen numara', 'Yeni SMS · 10086']),
            'zh_Hans': (['\u4f18\u79c0', '\u826f\u597d', '\u4e00\u822c', '\u8f83\u5dee', '\u672a\u77e5'],
                        ['1 \u5929 1 \u5c0f\u65f6', '1 \u5c0f\u65f6 2 \u5206', '5 \u5206'],
                        '\u6682\u65e0\u90bb\u533a\u6570\u636e', '\u5df2\u9501\u5b9a', '\u9501\u5b9a',
                        ['\u65b0\u77ed\u4fe1 \xb7 \u672a\u77e5\u53f7\u7801', '\u65b0\u77ed\u4fe1 \xb7 10086']),
        }
        for lang, (quality, uptime, empty, locked, lock, sms) in want.items():
            with self.subTest(lang=lang):
                r = self.run_js(lang, self.RENDER)['result']
                self.assertEqual(r['quality'], quality)
                self.assertEqual(r['uptime'], uptime)
                self.assertIn('>%s</td>' % empty, r['neighbours'])
                self.assertIn('>%s</button>' % locked, r['locks'])
                self.assertIn('>%s</button>' % lock, r['locks'])
                self.assertEqual(r['colour'], 'var(--danger, #E25555)')   # the colour follows the translated label
                self.assertEqual(r['sms'], sms)

    def test_carrier_names_follow_locale(self):
        # the fork's cases (a COPS name, a PLMN from the table) with the name COPS gives in English
        body = '''return [ M.carrierName({ name: 'China Unicom' }), M.carrierName({ plmn: '46001' }),
                          M.carrierName({ plmn: '46000' }), M.carrierName({ name: 'Turkcell' }),
                          M.carrierName({ plmn: '28601' }), M.carrierName(null) ];'''
        unicom, mobile = '\u4e2d\u56fd\u8054\u901a', '\u4e2d\u56fd\u79fb\u52a8'
        for lang, want in ((None, ['China Unicom', 'China Unicom', 'China Mobile']),
                           ('tr', ['China Unicom', 'China Unicom', 'China Mobile']),
                           ('zh_Hans', [unicom, unicom, mobile])):
            with self.subTest(lang=lang):
                # a name the catalogs do not know, a PLMN outside the table and no operator pass through
                self.assertEqual(self.run_js(lang, body)['result'], want + ['Turkcell', '28601', '--'])

    def test_unknown_language_is_english(self):
        # German has no catalog: every _() returns its msgid, and every msgid rendered is one the catalogs carry
        out = self.run_js('de', '''
M.injectCss();
if (selected !== true) throw Error('the Bootstrap token bridge must survive the conversion');''' + self.RENDER)
        r, used = out['result'], set(out['used'])
        self.assertEqual(r['quality'], ['Excellent', 'Good', 'Fair', 'Poor', 'Unknown'])
        self.assertEqual(r['uptime'], ['1 d 1 h', '1 h 2 min', '5 min'])
        self.assertIn('>No neighbor-cell data</td>', r['neighbours'])
        self.assertIn('>Locked</button>', r['locks'])
        self.assertEqual(r['sms'], ['New SMS · Unknown number', 'New SMS · 10086'])
        extracted = set(subprocess.run(TOOL + ['extract'], capture_output=True, text=True, encoding='utf-8',
                                       check=True).stdout.splitlines())
        self.assertLessEqual(used, extracted)
        for lang in ('tr', 'zh_Hans'):
            self.assertLessEqual(used, set(catalog(lang)), lang)

    def test_common_menu_and_acl_have_no_chinese_and_are_in_both_catalogs(self):
        # Task 13's files: the catalog check finds no problem in them (the views and backend are Tasks 14-16)
        r = run('check')
        mine = ('mu300/common.js', 'menu.d/luci-app-mu300.json', 'acl.d/luci-app-mu300.json')
        self.assertEqual([l for l in r.stdout.splitlines() if any(m in l for m in mine)], [])
        self.assertEqual([l for l in r.stdout.splitlines() if l.startswith(('stale', 'po:', 'placeholder'))], [])
        self.assertFalse((APP / 'po' / 'en').exists(), 'English is the source: no po/en catalog')

    def test_build_requires_the_chinese_catalog(self):
        # once po/zh_Hans exists, an image without mu300.zh-cn.lmo is a Chinese panel in English: the build stops
        self.assertTrue((APP / 'po' / 'zh_Hans' / 'mu300.po').is_file())
        self.assertTrue('[ -s "$CAT/mu300.zh_Hans.lmo" ] ||' in BUILD.read_text(encoding='utf-8'),
                        'build-rootfs.sh does not stop without the Chinese panel catalog')




if __name__ == '__main__':
    unittest.main()
