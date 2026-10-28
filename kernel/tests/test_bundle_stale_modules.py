"""make-bundle.sh refuses a vendor module whose sources differ from those build-modules.sh built it from: the
vermagic is right and the code is old. By content (module-sources.sh): a checkout and the release's cache of
kernel/out set any file times."""
import os
import re

from helpers import TOP, ShellTest

K = TOP / 'kernel'
BUNDLE = K / 'make-bundle.sh'


class StaleModules(ShellTest):
    def setUp(self):
        super().setUp()
        self.k = self.tmp / 'kernel'
        self.out = self.k / 'out'
        (self.out / 'modules').mkdir(parents=True)
        for n in ('wcn_bsp', 'sprd_wlan_combo'):
            d = self.k / 'modules' / n / 'common'
            d.mkdir(parents=True)
            (d / 'x.c').write_text('int x;\n')
            (d.parent / 'Makefile').write_text('obj-m += x.o\n')

    def built(self, shell, n=None):
        """the source lists build-modules.sh keeps, of the tree as it is now"""
        for d in (self.k / 'modules').iterdir():
            if n in (None, d.name):
                r = self.sh(shell, f'. "{K}/module-sources.sh"; module_sources "{d}" > "{self.out}/modules/{d.name}.sources"')
                self.assertEqual(r.returncode, 0, r.stderr)

    def check(self, shell):
        m = re.search(r'# --- stale begin\n(.*?)# --- stale end', BUNDLE.read_text(), re.S)
        self.assertIsNotNone(m, 'make-bundle.sh has no stale block')
        code = (f'set -eu\n. "{K}/module-sources.sh"\nK="{self.k}" UO="{self.out}" krel=7.2.13-mu300\n' + m.group(1) +
                'echo fine\n')
        return self.sh(shell, code)

    def test_the_built_sources_pass(self):
        for shell in self.each_shell():
            self.built(shell)
            # times say nothing: a source touched after the build, its contents the same, still passes
            os.utime(self.k / 'modules' / 'wcn_bsp' / 'common' / 'x.c', None)
            r = self.check(shell)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'fine'), r.stderr)

    def test_a_changed_source_is_refused(self):
        for shell in self.each_shell():
            self.built(shell)
            (self.k / 'modules' / 'sprd_wlan_combo' / 'common' / 'x.c').write_text('int x = 1;\n')
            r = self.check(shell)
            self.assertEqual(r.returncode, 1)
            self.assertIn('kernel/modules/sprd_wlan_combo/common/x.c changed after sprd_wlan_combo was built', r.stderr)
            self.assertIn('build-modules.sh sprd_wlan_combo', r.stderr)
            (self.k / 'modules' / 'sprd_wlan_combo' / 'common' / 'x.c').write_text('int x;\n')

    def test_an_added_or_removed_source_is_refused(self):
        for shell in self.each_shell():
            self.built(shell)
            new = self.k / 'modules' / 'wcn_bsp' / 'common' / 'y.c'
            new.write_text('int y;\n')
            r = self.check(shell)
            self.assertEqual(r.returncode, 1)
            self.assertIn('kernel/modules/wcn_bsp/common/y.c changed after wcn_bsp was built', r.stderr)
            new.unlink()
            (self.k / 'modules' / 'wcn_bsp' / 'Makefile').unlink()
            r = self.check(shell)
            self.assertEqual(r.returncode, 1)
            self.assertIn('kernel/modules/wcn_bsp/Makefile changed after wcn_bsp was built', r.stderr)
            (self.k / 'modules' / 'wcn_bsp' / 'Makefile').write_text('obj-m += x.o\n')

    def test_a_module_never_built_is_refused(self):
        for shell in self.each_shell():
            self.built(shell)
            (self.out / 'modules' / 'wcn_bsp.sources').unlink()
            r = self.check(shell)
            self.assertEqual(r.returncode, 1)
            self.assertIn('wcn_bsp was not built for this kernel', r.stderr)

    def test_build_modules_keeps_the_lists_and_the_bundle_checks_them(self):
        s = BUNDLE.read_text()
        self.assertLess(s.index('. "$K/module-sources.sh"'), s.index('# --- stale begin'))
        self.assertLess(s.index('# --- stale end'), s.index('tar -C "$W/b"'))
        mods = (K / 'build-modules.sh').read_text()
        loop = mods[mods.index('for m in $mods; do'):mods.index('\ndone\n', mods.index('for m in $mods; do'))]
        # listed before the copy that is built, kept only once the module is built
        self.assertLess(loop.index('module_sources "$W/modules/$m" > "$SRC/$m.sources"'), loop.index('cp -r "$W/modules/$m"'))
        self.assertLess(loop.index('| tee "$OUT/modules/$m.log"'), loop.index('mv "$SRC/$m.sources" "$OUT/modules/$m.sources"'))
        self.assertIn('"$OUT"/modules/*.sources', mods)
        # every module directory is built by default, so every one has its list
        listed = set(re.search(r'mods=\$\{\*:-([^}]*)\}', mods).group(1).split())
        self.assertEqual(listed, {d.name for d in (K / 'modules').iterdir() if d.is_dir()})
