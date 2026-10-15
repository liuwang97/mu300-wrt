"""Which release the installers take: tools/release_pick.py (the decision, for install.sh, install.ps1 and the
graphical installer) against fake answers of GitHub's API, and tools/releases.sh's release_resolve - the beta is
asked about only when there is one newer than the newest full release, never with MU300_RELEASE set, and a GitHub
that does not answer the list leaves the newest release, as before."""
import importlib.util
import json
import os
import subprocess
import sys
import unittest

from helpers import TOP, ShellTest

PICK = TOP / 'tools' / 'release_pick.py'
spec = importlib.util.spec_from_file_location('release_pick', PICK)
release_pick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_pick)


def rel(tag, when, prerelease=False, draft=False, files=None):
    return {'tag_name': tag, 'published_at': when, 'created_at': when, 'prerelease': prerelease, 'draft': draft,
            'assets': [{'name': n, 'size': s} for n, s in (files or {}).items()]}


FULL = rel('v2026.10.23', '2026-10-12T17:55:05Z', files={'mu300-kernel.tar.gz': 26971198, 'SHA256SUMS': 1011})
OLDER = rel('v2026.10.22', '2026-10-12T09:41:29Z')
OLD_BETA = rel('v2026.10.19', '2026-10-11T16:18:01Z', prerelease=True)
NEW_BETA = rel('v2026.10.25', '2026-10-14T08:00:00Z', prerelease=True, files={'mu300-kernel.tar.gz': 27000000})
NEWER_BETA = rel('v2026.10.26', '2026-10-15T08:00:00Z', prerelease=True)
RATE_LIMIT = {'message': "API rate limit exceeded for 203.0.113.7.", 'documentation_url': 'https://docs.github.com/'}


class Pick(unittest.TestCase):
    def test_no_prerelease(self):
        r = release_pick.pick([FULL, OLDER])
        self.assertEqual((r['latest'], r['beta']), ('v2026.10.23', None))

    def test_a_prerelease_the_full_release_has_overtaken_is_not_offered(self):
        r = release_pick.pick([FULL, OLDER, OLD_BETA])
        self.assertEqual((r['latest'], r['beta']), ('v2026.10.23', None))

    def test_a_newer_prerelease_is_the_beta(self):
        # in any order: the API's own order is not relied on
        for order in ([NEW_BETA, FULL, OLDER, OLD_BETA], [OLD_BETA, OLDER, FULL, NEW_BETA]):
            r = release_pick.pick(order)
            self.assertEqual((r['latest'], r['beta']), ('v2026.10.23', 'v2026.10.25'))
        self.assertEqual(r['files']['v2026.10.25'], {'mu300-kernel.tar.gz': 27000000})
        self.assertEqual(r['files']['v2026.10.23']['SHA256SUMS'], 1011)

    def test_the_newest_of_two_betas(self):
        self.assertEqual(release_pick.pick([NEW_BETA, NEWER_BETA, FULL])['beta'], 'v2026.10.26')

    def test_drafts_are_nobodys_release(self):
        draft_full = rel('v2026.11.01', '2026-11-01T00:00:00Z', draft=True)
        draft_beta = rel('v2026.11.02', '2026-11-02T00:00:00Z', prerelease=True, draft=True)
        r = release_pick.pick([draft_full, draft_beta, FULL])
        self.assertEqual((r['latest'], r['beta']), ('v2026.10.23', None))

    def test_only_prereleases_is_no_release_and_no_beta(self):
        # "newer than the newest full release" cannot be said without one
        r = release_pick.pick([NEW_BETA])
        self.assertEqual((r['latest'], r['beta']), (None, None))

    def test_what_is_not_a_list_of_releases(self):
        for answer in (RATE_LIMIT, None, [], {}, 'x', 7, [7, 'x', None, {}], [{'tag_name': 5}], [{'tag_name': ''}]):
            r = release_pick.pick(answer)
            self.assertEqual((r['latest'], r['beta']), (None, None), answer)

    def test_text(self):
        self.assertEqual(release_pick.pick_text(json.dumps([FULL, NEW_BETA]))['beta'], 'v2026.10.25')
        for text in ('', '<html>blocked</html>', '{"message": "Not Found"}', '[{"tag_name": '):
            self.assertEqual(release_pick.pick_text(text)['latest'], None, text)

    def run_tool(self, text, *args):
        return subprocess.run([sys.executable, str(PICK)] + list(args), input=text.encode(), capture_output=True)

    def test_command_line(self):
        r = self.run_tool(json.dumps([FULL, NEW_BETA, OLD_BETA]))
        self.assertEqual(r.stdout.decode().split(), ['LATEST=v2026.10.23', 'BETA=v2026.10.25'])
        r = self.run_tool(json.dumps([FULL, OLD_BETA]))
        self.assertEqual(r.stdout.decode().split(), ['LATEST=v2026.10.23'])
        for text in (json.dumps(RATE_LIMIT), '', 'not json'):
            r = self.run_tool(text)
            self.assertEqual((r.returncode, r.stdout), (0, b''), text)
        r = self.run_tool(json.dumps([FULL, NEW_BETA]), '--json')
        self.assertEqual(json.loads(r.stdout.decode())['beta'], 'v2026.10.25')

    def test_a_tag_that_is_not_plain_is_not_passed_on(self):
        # the tag goes into a URL and a directory name of the installers
        evil = rel('v1; rm -rf ~', '2026-10-20T00:00:00Z', prerelease=True)
        r = self.run_tool(json.dumps([FULL, evil]))
        self.assertEqual(r.stdout.decode().split(), ['LATEST=v2026.10.23'])
        r = self.run_tool(json.dumps([rel('../../x', '2026-10-20T00:00:00Z'), NEW_BETA]))
        self.assertEqual(r.stdout, b'')
        r = self.run_tool(json.dumps([FULL, evil]), '--json')
        self.assertEqual(json.loads(r.stdout.decode()), {'latest': 'v2026.10.23', 'beta': None,
                                                         'files': {'v2026.10.23': release_pick._files(FULL)}})


@unittest.skipIf(os.name == 'nt', 'the stub curl is a shell script; install.ps1 has its own lookup, tested in installer.Tests.ps1')
class Resolve(ShellTest):
    """release_resolve with a stub curl: $STUBLOG/list is the answer to the list of releases, $STUBLOG/latest the
    one to releases/latest; a missing file is a failed request (curl -f)."""

    def setUp(self):
        super().setUp()
        self.stub('curl', 'for a in "$@"; do u=$a; done; echo "$u" >> "$STUBLOG/curl.log"\n'
                          'case $u in */releases\\?per_page=*) f=list ;; */releases/latest) f=latest ;; *) exit 22 ;; esac\n'
                          '[ -f "$STUBLOG/$f" ] || exit 22\ncat "$STUBLOG/$f"')

    def resolve(self, shell, listing=None, latest=None, answer='', pre=''):
        for name, value in (('list', listing), ('latest', latest)):
            p = self.tmp / name
            if value is None:
                p.unlink(missing_ok=True)
            else:
                p.write_text(value if isinstance(value, str) else json.dumps(value))
        (self.tmp / 'curl.log').write_text('')
        code = (f'TOP="{TOP}"; . "$TOP/tools/i18n.sh"; MU300_LANG=en; REPO=dikeckaan/mu300-linux; RELEASE=; '
                'die() { echo "DIE $*"; exit 1; }; '
                'ask() { printf "ASKED[%s|%s|%s] " "$2" "$3" "${4:-}"; read -r _a; [ -n "$_a" ] || _a=$3; eval "$1=\\$_a"; }; '
                f'{pre} . "$TOP/tools/releases.sh"; release_resolve; echo "release=$RELEASE offered=$BETA_OFFERED"')
        r = self.sh(shell, code, stdin=answer + '\n')
        return r.stdout, (self.tmp / 'curl.log').read_text()

    def test_no_beta_asks_nothing(self):
        for shell in self.each_shell():
            out, _ = self.resolve(shell, [FULL, OLDER, OLD_BETA])
            self.assertNotIn('ASKED', out)
            self.assertIn('release=v2026.10.23 offered=\n', out)

    def test_a_beta_is_asked_about_and_the_default_is_no(self):
        for shell in self.each_shell():
            out, _ = self.resolve(shell, [NEW_BETA, FULL])
            self.assertIn('ASKED[Install the beta v2026.10.25 instead of v2026.10.23? (yes/no)|no|MU300_ANS_BETA]', out)
            self.assertIn('A beta is out: v2026.10.25,', out)
            self.assertIn('release=v2026.10.23 offered=v2026.10.25', out)
            out, _ = self.resolve(shell, [NEW_BETA, FULL], answer='yes')
            self.assertIn('release=v2026.10.25 offered=v2026.10.25', out)
            out, _ = self.resolve(shell, [NEW_BETA, FULL], answer='no')
            self.assertIn('release=v2026.10.23', out)

    def test_a_release_given_asks_nothing_and_asks_github_nothing(self):
        for shell in self.each_shell():
            out, log = self.resolve(shell, [NEW_BETA, FULL], pre='RELEASE=v2026.10.20;')
            self.assertNotIn('ASKED', out)
            self.assertIn('release=v2026.10.20 offered=\n', out)
            self.assertEqual(log, '')
            out, log = self.resolve(shell, [NEW_BETA, FULL], pre='MU300_RELEASE_URL=http://127.0.0.1:8000;')
            self.assertNotIn('ASKED', out)
            self.assertIn('release=custom', out)
            self.assertEqual(log, '')

    def test_without_the_list_the_newest_release_and_no_question(self):
        latest = '{\n  "url": "x",\n  "tag_name": "v2026.10.23",\n  "prerelease": false\n}\n'
        for shell in self.each_shell():
            # the request failed (offline on that path, HTTP 403 of the rate limit: curl -f prints nothing)
            out, log = self.resolve(shell, None, latest)
            self.assertNotIn('ASKED', out)
            self.assertIn('release=v2026.10.23 offered=\n', out)
            self.assertEqual(len(log.splitlines()), 2)
            # the rate limit's error object, a proxy's page
            for listing in (RATE_LIMIT, '<html>captive portal</html>', ''):
                out, _ = self.resolve(shell, listing, latest)
                self.assertNotIn('ASKED', out)
                self.assertIn('release=v2026.10.23', out)

    def test_github_gone_altogether_stops_as_before(self):
        for shell in self.each_shell():
            out, _ = self.resolve(shell, None, None)
            self.assertIn('DIE cannot find the newest release of dikeckaan/mu300-linux (set MU300_RELEASE=<tag>', out)
            self.assertNotIn('release=', out)

    def test_install_sh_resolves_before_the_questions(self):
        src = (TOP / 'install.sh').read_text()
        self.assertLess(src.index('release_resolve'), src.index("say \"$(t 'What should be installed?')\""))
        self.assertLess(src.index("say \"$(t 'What should be installed?')\""), src.index('slot_release_ok "$RELEASE"'))
        self.assertNotIn('/releases/latest"', src)      # one place looks it up: tools/releases.sh


if __name__ == '__main__':
    unittest.main()
