"""The release tags: vYYYY.MM.DD.N (and the older vYYYY.MM.DD), the same block in tools/releases.sh and mu300-update,
held to tests/fixtures/release-tags.tsv, and the regular expression the panel's programs check a tag with."""
import os
import re
import unittest

from helpers import TOP, ShellTest

TAGS = TOP / "openwrt" / "tests" / 'fixtures' / 'release-tags.tsv'
BIN_UPDATE = TOP / 'openwrt' / 'opt' / 'mu300' / 'bin' / 'mu300-update'
# the regular expression of a release tag (ERE), where a device program checks one with grep: the panel's adapters
TAG_ERE = r'v[0-9]{4}\.[0-9]{2}\.[0-9]{2}|v[0-9]{4}\.(0[1-9]|1[0-2])\.(0[1-9]|[12][0-9]|3[01])\.[1-9][0-9]?'


def tag_table():
    """(oks, bads, [(a, r, b)]) of tests/fixtures/release-tags.tsv: the table every language is held to"""
    oks, bads, cmps = [], [], []
    for line in TAGS.read_text().split('\n'):
        if not line or line.startswith('#'):
            continue
        f = line.split('\t')
        if f[0] == 'ok':
            oks.append(f[1])
        elif f[0] == 'bad':
            bads.append(f[1])
        else:
            cmps.append((f[1], f[2], f[3]))
    return oks, bads, cmps


def relation(newer, ok, a, b):
    if newer(a, b):
        return 'both' if newer(b, a) else '>'
    if newer(b, a):
        return '<'
    return '=' if ok(a) and ok(b) else '!'


class Regex(unittest.TestCase):
    def test_the_regular_expression_of_the_device_programs(self):
        # the same expression in each: the panel's rpcd (the arguments of update_act), the update adapter (the tag
        # it starts an update to) and dashboard-info (the note of a newer release on the home page)
        oks, bads, _ = tag_table()
        rx = re.compile(TAG_ERE)
        for t in oks:
            self.assertTrue(rx.fullmatch(t), t)
        for t in bads:
            self.assertFalse(rx.fullmatch(t), t)
        luci = TOP / 'openwrt' / 'luci-app-mu300' / 'root' / 'usr' / 'libexec'
        self.assertIn(f"allowed \"$tag\" '({TAG_ERE})?'", (luci / 'rpcd' / 'mu300dash').read_text())
        self.assertIn(f"LC_ALL=C grep -Eqx -- '{TAG_ERE}'", (luci / 'unisoc-modem' / 'update').read_text())
        # dashboard-info's snapshot is mu300.dash's (rpcd's mu300status reads it too)
        dash = TOP / 'openwrt' / 'luci-app-mu300' / 'root' / 'usr' / 'share' / 'ucode' / 'mu300' / 'dash.uc'
        self.assertIn(f"match(update_tag, /^({TAG_ERE})$/)", dash.read_text())


class ShellTags(ShellTest):
    """The table in sh: tools/releases.sh (make-release.sh) and its copy in mu300-update."""

    def block(self, text):
        m = re.search(r'\n# --- release-tag begin\n.*?\n# --- release-tag end\n', '\n' + text, re.S)
        self.assertIsNotNone(m)
        return m.group(0)

    def test_the_copies_are_the_same(self):
        lib = self.block((TOP / 'tools/releases.sh').read_text())
        self.assertEqual(self.block(BIN_UPDATE.read_text()), lib)

    def test_the_table(self):
        oks, bads, cmps = tag_table()
        # one run per shell and file: every line answered, then compared here
        lines = [f'tag_ok "{t}" && echo "ok[{t}]" || echo "no[{t}]"' for t in oks + bads]
        lines += [f'newer "{a}" "{b}" && echo "gt[{a}][{b}]"; newer "{b}" "{a}" && echo "gt[{b}][{a}]"' for a, _, b in cmps]
        code = '\n'.join(lines) + '\necho end'
        for shell in self.each_shell():
            for where, pre, env in (('releases.sh', f'. "{TOP}/tools/releases.sh"', {}),
                                    ('mu300-update', f'. "{BIN_UPDATE}"', {'MU300_LIB': '1'})):
                r = self.sh(shell, pre + '\n' + code, **env)
                out = set(r.stdout.split('\n'))
                self.assertIn('end', out, (where, r.stderr[-500:]))
                for t in oks:
                    self.assertIn(f'ok[{t}]', out, (where, t))
                for t in bads:
                    self.assertIn(f'no[{t}]', out, (where, t))
                for a, want, b in cmps:
                    got = relation(lambda x, y: f'gt[{x}][{y}]' in out, lambda x: f'ok[{x}]' in out, a, b)
                    self.assertEqual(got, want, (where, a, b))
                r = self.sh(shell, pre + '\ntag_key v2026.10.16.1; tag_key v2026.10.27; tag_key v2026.10.16.12; tag_key dev || echo none', **env)
                self.assertEqual(r.stdout.split(), ['22026101601', '12026102700', '22026101612', 'none'], where)


@unittest.skipIf(os.name == 'nt', 'make-release.sh runs on a Unix host')
class MakeReleaseTag(ShellTest):
    """tools/make-release.sh --next-tag and the tag it takes for a new release, with a stub gh
    ($STUBLOG/gh: its list of releases; $STUBLOG/gh.rc: its status) and a stub for the repository's own tags
    ($STUBLOG/tags, $STUBLOG/remote)."""

    def setUp(self):
        super().setUp()
        self.stub('gh', 'cat "$STUBLOG/gh" 2>/dev/null; exit "$(cat "$STUBLOG/gh.rc" 2>/dev/null || echo 0)"')
        self.stub('git', 'case "$*" in *" tag -l "*) cat "$STUBLOG/tags" 2>/dev/null ;; *ls-remote*) cat "$STUBLOG/remote" 2>/dev/null ;; '
                         '*) echo "unexpected: $*" >&2; exit 9 ;; esac')

    def run_mr(self, shell, *args, gh='', tags='', remote='', rc=0):
        (self.tmp / 'gh').write_text(gh)
        (self.tmp / 'gh.rc').write_text(str(rc))
        (self.tmp / 'tags').write_text(tags)
        (self.tmp / 'remote').write_text(remote)
        return self.script(shell, TOP / 'tools/make-release.sh', *args, MU300_TODAY='2026.10.16')

    def test_next_tag(self):
        for shell in self.each_shell():
            for gh, tags, remote, want in (
                    ('v2026.10.27\nv2026.10.26\ntest-issue-102\n', '', '', 'v2026.10.16.1'),
                    ('v2026.10.16.1\nv2026.10.27\n', '', '', 'v2026.10.16.2'),
                    # .9 then .10, not .10 then .2 (as numbers); a tag without a release counts; another day does not
                    ('v2026.10.16.9\nv2026.10.15.30\n', 'v2026.10.16.3\n', '', 'v2026.10.16.10'),
                    ('v2026.10.16.10\n', '', 'abc\trefs/tags/v2026.10.16.12\nabc\trefs/tags/v2026.10.16.12^{}\n', 'v2026.10.16.13'),
                    # not release tags: never counted
                    ('v2026.10.16.01\nv2026.10.16.0\nv2026.10.16.1x\n', '', '', 'v2026.10.16.1')):
                r = self.run_mr(shell, '--next-tag', gh=gh, tags=tags, remote=remote)
                self.assertEqual((r.returncode, r.stdout), (0, want + '\n'), (gh, tags, remote, r.stderr))
            # GitHub does not answer: no tag at all
            r = self.run_mr(shell, '--next-tag', rc=1)
            self.assertEqual((r.returncode, r.stdout), (1, ''))
            self.assertIn('no tag is guessed', r.stderr)
            r = self.run_mr(shell, '--next-tag', gh='v2026.10.16.99\n')
            self.assertEqual(r.returncode, 1)

    def test_the_tag_of_a_new_release(self):
        for shell in self.each_shell():
            r = self.run_mr(shell, '--check-tag', 'v2026.10.16.3')
            self.assertEqual((r.returncode, r.stdout), (0, 'v2026.10.16.3\n'), r.stderr)
            for tag, why in (('v2026.10.28', 'not a tag for a new release'), ('v2026.10.16', 'not a tag for a new release'),
                             ('v2026.10.16.0', 'not a release tag'), ('v2026.10.16.01', 'not a release tag'),
                             ('v2026.13.16.1', 'not a release tag'), ('v2026.10.15.1', 'not of today'),
                             ('v2026.10.17.1', 'not of today'), ('test-issue-102', 'not a tag for a new release')):
                r = self.run_mr(shell, '--check-tag', tag)
                self.assertEqual((r.returncode, r.stdout), (1, ''), tag)
                self.assertIn(why, r.stderr, tag)
            # another day only when said so; the form never
            r = self.run_mr(shell, '--check-tag', 'v2026.10.15.1', '--any-date')
            self.assertEqual(r.returncode, 0, r.stderr)
            r = self.run_mr(shell, '--check-tag', 'v2026.10.28', '--any-date')
            self.assertEqual(r.returncode, 1)
            # a build is refused before anything is looked at
            r = self.run_mr(shell, 'v2026.10.28', '--publish')
            self.assertEqual(r.returncode, 1)
            self.assertIn('not a tag for a new release', r.stderr)
            self.assertNotIn('kernel outputs', r.stderr)


if __name__ == '__main__':
    unittest.main()
