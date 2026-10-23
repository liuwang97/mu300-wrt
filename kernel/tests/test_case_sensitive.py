"""Kernel modules whose names differ only in case: xt_HL.ko and xt_hl.ko, xt_TCPMSS.ko and xt_tcpmss.ko, xt_DSCP.ko
and xt_dscp.ko, xt_RATEEST.ko and xt_rateest.ko. A tree on a file system that ignores case keeps one file of each
pair; the build scripts refuse such a directory, and the audit finds a build that lost one."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

TOP = Path(__file__).resolve().parents[2]
PAIRS = ('xt_HL.ko', 'xt_hl.ko', 'xt_TCPMSS.ko', 'xt_tcpmss.ko', 'xt_DSCP.ko', 'xt_dscp.ko', 'xt_RATEEST.ko',
         'xt_rateest.ko')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, TOP / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def folds(d):
    """whether d ignores case, asked the same way case_sensitive does"""
    probe = Path(tempfile.mkdtemp(dir=d))
    try:
        (probe / 'mu300-case').write_text('')
        (probe / 'MU300-CASE').write_text('')
        return len(os.listdir(probe)) != 2
    finally:
        for p in probe.iterdir():
            p.unlink()
        probe.rmdir()


@unittest.skipIf(os.name == 'nt', 'the build scripts run on a Unix host')
class CaseSensitive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name)

    def run_check(self, path=None):
        env = dict(os.environ)
        if path:
            env['PATH'] = path + os.pathsep + env['PATH']
        return subprocess.run(['sh', '-c', '. "$1" && case_sensitive "$2"', 'sh', str(TOP / 'kernel/case-sensitive.sh'),
                               str(self.d)], capture_output=True, text=True, env=env)

    def test_the_host_directory(self):
        # whatever this host's temporary directory is, the check says the same as Python does, and leaves nothing
        r = self.run_check()
        self.assertEqual(r.returncode, 1 if folds(self.d) else 0, r.stderr)
        self.assertEqual(os.listdir(self.d), [])

    def test_a_directory_that_ignores_case(self):
        # a file system that keeps one of the two names, made here with an ls that shows one
        bin_ = self.d / 'bin'
        bin_.mkdir()
        (bin_ / 'ls').write_text('#!/bin/sh\necho mu300-case\n')
        (bin_ / 'ls').chmod(0o755)
        self.d = self.d / 'stage'
        self.d.mkdir()
        r = self.run_check(str(bin_))
        self.assertEqual(r.returncode, 1)
        self.assertIn(f'{self.d} is on a file system that ignores case', r.stderr)
        self.assertIn("-fs 'Case-sensitive APFS'", r.stderr)
        self.assertEqual(os.listdir(self.d), [])

    def test_a_directory_it_cannot_write(self):
        self.d = self.d / 'missing'
        r = self.run_check()
        self.assertEqual(r.returncode, 1)
        self.assertIn('cannot make a directory in', r.stderr)


class Scripts(unittest.TestCase):
    def test_the_build_scripts_check_and_honour_tmpdir(self):
        bundle = (TOP / 'kernel/make-bundle.sh').read_text()
        self.assertIn('W=$(mktemp -d "${TMPDIR:-/tmp}/mu300-bundle.XXXXXX")', bundle)
        self.assertIn('case_sensitive "$UO/modules" || exit 1', bundle)
        self.assertIn('case_sensitive "$W" || exit 1', bundle)
        self.assertLess(bundle.index('case_sensitive "$W"'), bundle.index('cp "$UO"/modules/*.ko "$W/b/modules/"'))
        mods = (TOP / 'kernel/build-modules.sh').read_text()
        self.assertLess(mods.index('case_sensitive "$OUT/modules" || exit 1'), mods.index('modules_install'))
        for f in ('kernel/make-bundle.sh', 'kernel/initramfs/tools.sh'):
            src = (TOP / f).read_text()
            self.assertNotRegex(src, r'mktemp( -d)?\)', f)


class BundleAudit(unittest.TestCase):
    def test_a_build_that_lost_one_of_a_pair(self):
        # the tree as a file system that ignores case leaves it: modules.in-tree names both, the directory has one
        # (is_file() of the other is true there, so the audit asks the listing)
        audit = load('bundle_audit_case', 'kernel/audit-bundle.py')
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / 'Image').write_bytes(b'IKCFG_ST' + __import__('gzip').compress(b'CONFIG_KVM=y\n') + b'IKCFG_ED')
            (out / 'fragment').write_text('CONFIG_KVM=y\n')
            (out / 'modules').mkdir()
            (out / 'modules/xt_HL.ko').write_bytes(b'module')
            (out / 'modules.in-tree').write_text('xt_HL.ko\nxt_hl.ko\n')
            with self.assertRaisesRegex(ValueError, r'missing in-tree module: xt_hl\.ko \(only xt_HL\.ko'):
                audit.audit(out, out / 'fragment')


if __name__ == '__main__':
    unittest.main()
