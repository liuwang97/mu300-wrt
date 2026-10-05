"""The control panel's catalogs: complete in Turkish and Chinese, no Chinese outside them (spec, Translations)."""
import shutil, subprocess, sys, tempfile, unittest
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
            ('placeholder', both(append(VIEW, "\nE('p', {}, _('%d new messages').format(n));\n"),
                                 tr_entry('%d new messages', '%s yeni mesaj'))),
            ('stale', tr_entry('A message no source uses', 'Kimse kullanmiyor')),
            ('dynamic', append(VIEW, "\nE('p', {}, _(someVariable));\n")),
            ('dynamic', append(VIEW, "\nE('p', {}, _('Half ' + 'a sentence'));\n")),
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


if __name__ == '__main__':
    unittest.main()
