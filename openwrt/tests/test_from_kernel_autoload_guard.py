"""mu300-modprobe-guard, the kernel's modprobe helper on the device: it refuses on-demand loads of the modules the
kernel bundle lists in autoload-blocked (docs/contract.md), by name and by every alias, and hands everything else
to modprobe. The list and a sample of the 7.2.11 build's module index are copies in fixtures/from-kernel."""
import fnmatch
import os
import pathlib
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import BIN, TOP

FIXTURES = TOP / 'openwrt' / 'tests' / 'fixtures' / 'from-kernel'
MUST_PASS = ['nf_tables', 'nft_chain_nat', 'nft_ct', 'nft_masq', 'wireguard', 'overlay', 'usb_storage', 'vfat', 'tun',
             'bridge', 'veth', 'br_netfilter', 'xt_nat', 'xt_conntrack', 'xt_MASQUERADE', 'xt_addrtype',
             'vhost_net', 'l2tp_core', 'l2tp_ppp', 'l2tp_ip', 'macvtap', 'kvm', 'can_dev',
             'gs_usb', 'hfsplus', 'ntfs3', 'exfat', 'btrfs', 'fuse', 'iptable_nat', 'ip6_tables', 'xfrm_user', 'esp4',
             'sch_cake', 'nls_cp437', 'cdc_mbim', 'qmi_wwan', 'btusb', 'snd_aloop', 'dm_crypt', 'loop', 'zram']


def norm(s):
    return s.replace('-', '_')


def load(out):
    dep, aliases = {}, {}
    for line in (out / 'modules.dep').read_text().splitlines():
        k, v = line.split(':', 1)
        name = lambda p: norm(os.path.basename(p).split('.ko')[0])
        dep[name(k)] = {name(x) for x in v.split()}
    for line in (out / 'modules.alias').read_text().splitlines():
        if line.startswith('alias '):
            _, a, m = line.split(None, 2)
            aliases.setdefault(norm(m.strip()), []).append(a)
    return dep, aliases


def check_guard(out, listfile, guard, shell=('sh',)):
    out = pathlib.Path(out)
    dep, aliases = load(out)
    blocked = {l.split('#')[0].strip() for l in pathlib.Path(listfile).read_text().splitlines()} - {''}
    errors = []
    with tempfile.TemporaryDirectory() as d:
        d = pathlib.Path(d)
        moddir = d / 'modules'
        moddir.mkdir()
        # each module file carries its aliases as a real one does: NUL-separated strings of its .modinfo
        for m in dep:
            strings = ['license=GPL', 'name=' + m] + ['alias=' + a for a in aliases.get(m, [])]
            (moddir / (m + '.ko')).write_bytes(b'\x7fELF\0' + '\0'.join(strings).encode() + b'\0')
        # depmod's modules.dep next to the modules (the guard takes in their dependants from it)
        (moddir / 'modules.dep').write_text((out / 'modules.dep').read_text())
        lines = ''.join('%s\t%s\n' % (m, a) for m, al in aliases.items() for a in al)
        (d / 'aliases').write_text(lines)
        fake = d / 'modinfo'
        fake.write_text('#!/bin/sh\nm=${1##*/}; m=${m%%.ko*}\nawk -F"\\t" -v m="$m" \'$1 == m { print "alias:\\t\\t" $2 }\' '
                        '"%s"\n' % (d / 'aliases'))
        fake.chmod(0o755)
        log = d / 'modprobe.log'
        mp = d / 'modprobe'
        mp.write_text('#!/bin/sh\necho "$@" >> "%s"\n' % log)
        mp.chmod(0o755)
        env = dict(os.environ, MU300_AUTOLOAD_BLOCKED=str(listfile), MU300_MODPROBE=str(mp), MU300_MODINFO=str(fake),
                   MU300_MODDIR=str(moddir), MU300_KREL='test', MU300_GUARD_CACHE=str(d / 'cache'),
                   MU300_KMSG=str(d / 'kmsg'), MU300_AUTOLOAD_ALLOW=str(d / 'allow'))
        # the guard takes one request per run, as the kernel calls it; a batch keeps this quick
        def run(reqs):
            sh = ' '.join(shlex.quote(x) for x in shell)
            script = ''.join('%s %s -q -- %s && echo PASS %s || echo REFUSED %s\n'
                             % (sh, shlex.quote(str(guard)), shlex.quote(r), shlex.quote(r), shlex.quote(r))
                             for r in reqs)
            res = subprocess.run(['sh', '-c', script], env=env, capture_output=True, text=True)
            return dict(l.split(' ', 1)[::-1] for l in res.stdout.splitlines() if l.startswith(('PASS ', 'REFUSED ')))
        want_refused = []
        for m in sorted(blocked & set(dep)):
            want_refused += [m, m.replace('_', '-')] + aliases.get(m, [])
        got = run(want_refused)
        errors += ['not refused: %s' % r for r in want_refused if got.get(r) != 'REFUSED']
        if log.exists():
            errors.append('modprobe was called for a blocked request: ' + log.read_text().splitlines()[0])
        log.unlink(missing_ok=True)
        want_pass = []
        for m in MUST_PASS:
            if m in dep:
                want_pass += [m] + aliases.get(m, [])[:20]
        got = run(want_pass)
        errors += ['refused: %s' % r for r in want_pass if got.get(r) != 'PASS']
        called = log.read_text().splitlines() if log.exists() else []
        if len(called) != len(want_pass):
            errors.append('modprobe called %d times for %d allowed requests' % (len(called), len(want_pass)))
    return errors



class AutoloadGuard(unittest.TestCase):
    """The kernel's on-demand loads go through mu300-modprobe-guard, which refuses what the bundle's autoload-blocked
    lists (review of #256)."""
    GUARD = BIN / 'mu300-modprobe-guard'
    LIST = FIXTURES / 'autoload-blocked'
    SAMPLE = FIXTURES / 'autoload-7.2.11'   # a subset of the 7.2.11 build's modules.dep and modules.alias

    def run_guard(self, name, allow=False, shell=('sh',), moddir=None):
        with tempfile.TemporaryDirectory() as d:
            log, kmsg = Path(d) / 'log', Path(d) / 'kmsg'
            fake = Path(d) / 'modprobe'
            fake.write_text('#!/bin/sh\necho "$@" > "%s"\n' % log)
            fake.chmod(0o755)
            env = {'PATH': '/usr/bin:/bin', 'MU300_MODPROBE': str(fake), 'MU300_AUTOLOAD_BLOCKED': str(self.LIST),
                   'MU300_KMSG': str(kmsg), 'MU300_AUTOLOAD_ALLOW': str(Path(d) / 'allow'), 'MU300_KREL': 't',
                   'MU300_MODDIR': str(moddir or Path(d) / 'none'), 'MU300_GUARD_CACHE': str(Path(d) / 'cache')}
            if allow:
                (Path(d) / 'allow').write_text('')
            r = subprocess.run([*shell, str(self.GUARD), '-q', '--', name], env=env, capture_output=True, text=True)
            return r.returncode, log.read_text() if log.exists() else None, kmsg.read_text() if kmsg.exists() else ''

    def test_the_list_holds_module_names(self):
        names = [l.split('#')[0].strip() for l in self.LIST.read_text().splitlines()]
        names = [n for n in names if n]
        self.assertEqual(len(names), len(set(names)))
        for n in names:
            self.assertRegex(n, r'^[a-z0-9_]+$')
        for n in ('tipc', 'sctp', 'can', 'can_raw', 'can_bcm', 'nfc', 'ieee802154', 'slcan', 'hfs', 'jfs', 'nilfs2'):
            self.assertIn(n, names)
        for n in ('l2tp_core', 'l2tp_ppp', 'can_dev', 'hfsplus', 'wireguard', 'vhost_net'):
            self.assertNotIn(n, names)

    def test_every_spelling_of_a_blocked_module_in_the_build_sample(self):
        # every alias of every blocked module of the 7.2.11 build, and its name either way, refused; the closure of
        # their dependants checked; the daily ones (nft, wireguard, overlay, usb-storage, vfat, tun, bridge, veth,
        # br_netfilter, xt_*, vhost_net, kvm, l2tp...) handed on - under every shell the systems run it with
        for shell in (['sh'], ['busybox', 'sh'], ['dash']):
            if not shutil.which(shell[0]):
                continue
            with self.subTest(shell=shell[0]):
                self.assertEqual(check_guard(self.SAMPLE, self.LIST, self.GUARD, shell=shell), [])

    def test_a_refusal_is_logged_and_never_reaches_modprobe(self):
        with tempfile.TemporaryDirectory() as d:
            moddir = Path(d)
            (moddir / 'tipc.ko').write_text('')
            rc, called, kmsg = self.run_guard('tipc', moddir=moddir)
            self.assertEqual((rc, called), (1, None))
            self.assertIn('on-demand loading of tipc (tipc) refused (root: modprobe tipc', kmsg)
            # without a table (no modinfo answer) the names still hold, in either spelling
            self.assertEqual(self.run_guard('can-raw')[:2], (1, None))
            self.assertEqual(self.run_guard('wireguard')[:2], (0, '-q -- wireguard\n'))

    def sample_moddir(self, d):
        """A module directory made from the build sample: every module file carries its aliases in its .modinfo."""
        aliases = {}
        for line in (self.SAMPLE / 'modules.alias').read_text().splitlines():
            if line.startswith('alias '):
                _, a, m = line.split(None, 2)
                aliases.setdefault(m, []).append(a)
        names = {os.path.basename(l.split(':')[0]).split('.ko')[0].replace('-', '_')
                 for l in (self.SAMPLE / 'modules.dep').read_text().splitlines()}
        for m in names:
            (d / (m + '.ko')).write_bytes(b'\x7fELF\0' + '\0'.join('alias=' + a for a in aliases.get(m, [])).encode() + b'\0')
        return aliases

    # the requests the first (request-string) list refused, made concrete
    OLD_REQUESTS = ['net-pf-29', 'net-pf-29-proto-1', 'net-pf-30', 'net-pf-30-proto-0', 'net-pf-36', 'net-pf-39',
                    'net-pf-39-proto-1', 'net-pf-40', 'net-pf-2-proto-132', 'net-pf-2-proto-132-type-1',
                    'net-pf-10-proto-132', 'net-pf-10-proto-132-type-5', 'net-pf-16-proto-16-family-TIPCv2',
                    'net-pf-16-proto-16-family-nfc', 'net-pf-16-proto-16-family-nl802154',
                    'net-pf-16-proto-16-family-802.15.4 MAC', 'net-pf-16-proto-16-family-j1939', 'rtnl-family-29'] + [
                    'fs-' + f for f in ('adfs affs befs bfs coda cramfs efs gfs2 gfs2meta hfs hpfs jfs minix nilfs2 ocfs2 '
                                        'ocfs2_dlmfs omfs orangefs qnx4 qnx6 romfs sysv v7 ufs vxfs ceph').split()]

    def test_what_the_old_list_refused_is_still_refused_or_not_built(self):
        with tempfile.TemporaryDirectory() as d:
            aliases = self.sample_moddir(Path(d))
            pairs = [(a.replace('-', '_'), m) for m, al in aliases.items() for a in al]
            for req in self.OLD_REQUESTS:
                answering = {m for a, m in pairs if fnmatch.fnmatchcase(req.replace('-', '_'), a)}
                rc, called, _ = self.run_guard(req, moddir=Path(d))
                if answering:
                    self.assertEqual((rc, called), (1, None), (req, answering))
                else:
                    # nothing in the build answers it (sysv went upstream in 6.15; no genetlink family by that name):
                    # the guard hands it on, and modprobe finds nothing either
                    self.assertEqual(rc, 0, req)

    def test_a_module_that_depends_on_a_listed_one_is_refused_too(self):
        # modules.dep: the table takes in every module whose dependencies reach a listed one, whatever the list
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / 'tipc.ko').write_bytes(b'\0alias=net-pf-30\0')
            (d / 'evil-helper.ko').write_bytes(b'\0alias=net-pf-99\0alias=rtnl-link-evil\0')
            (d / 'other.ko').write_bytes(b'\0alias=net-pf-98\0')
            (d / 'modules.dep').write_text('kernel/net/tipc/tipc.ko:\nkernel/x/evil-helper.ko: kernel/net/tipc/tipc.ko\n'
                                           'kernel/x/other.ko:\n')
            for req in ('net-pf-99', 'rtnl-link-evil', 'evil_helper', 'evil-helper'):
                self.assertEqual(self.run_guard(req, moddir=d)[:2], (1, None), req)
            self.assertEqual(self.run_guard('net-pf-98', moddir=d)[:2], (0, '-q -- net-pf-98\n'))

    def test_without_a_table_it_fails_closed_for_the_families(self):
        with tempfile.TemporaryDirectory() as d:
            env_cache = Path(d) / 'elsewhere'
            env_cache.mkdir()
            link = Path(d) / 'cache-link'
            link.symlink_to(env_cache)   # not a directory of root's own making: no table
            log, kmsg = Path(d) / 'log', Path(d) / 'kmsg'
            fake = Path(d) / 'modprobe'
            fake.write_text('#!/bin/sh\necho "$@" > "%s"\n' % log)
            fake.chmod(0o755)
            env = {'PATH': '/usr/bin:/bin', 'MU300_MODPROBE': str(fake), 'MU300_AUTOLOAD_BLOCKED': str(self.LIST),
                   'MU300_KMSG': str(kmsg), 'MU300_AUTOLOAD_ALLOW': str(Path(d) / 'allow'), 'MU300_KREL': 't',
                   'MU300_MODDIR': str(Path(d)), 'MU300_GUARD_CACHE': str(link)}
            run = lambda req: subprocess.run(['sh', str(self.GUARD), '-q', '--', req], env=env).returncode
            for req in ('net-pf-99', 'net-pf-16-proto-16-family-x', 'fs-foo', 'tty-ldisc-21', 'rtnl-link-x'):
                self.assertEqual(run(req), 1, req)
                # the hint names what root would load (on F50-B it said "root: modprobe no alias table")
                self.assertIn('on-demand loading of %s (no alias table) refused (root: modprobe %s;' % (req, req),
                              kmsg.read_text())
            self.assertEqual(run('can-raw'), 1)          # a listed name, still known without a table
            self.assertFalse(log.exists())
            for req in ('crypto-gcm(aes)', 'nft-chain-2-nat', 'char-major-10-200', 'wireguard'):
                self.assertEqual(run(req), 0, req)

    def test_the_switch_turns_it_off(self):
        self.assertEqual(self.run_guard('tipc', allow=True)[:2], (0, '-q -- tipc\n'))

    def test_prepare_makes_the_table_then_takes_over(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            helper = d / 'helper'
            helper.write_text('/sbin/modprobe\n')
            (d / 'mods').mkdir()
            (d / 'mods' / 'tipc.ko').write_bytes(b'\x7fELF\0name=tipc\0alias=net-pf-30\0alias=net-pf-16-proto-16-family-TIPCv2\0')
            env = {'PATH': '/usr/bin:/bin', 'MU300_AUTOLOAD_BLOCKED': str(self.LIST), 'MU300_KREL': '7.2.11',
                   'MU300_MODDIR': str(d / 'mods'), 'MU300_GUARD_CACHE': str(d / 'cache'),
                   'MU300_HELPER_SYSCTL': str(helper), 'MU300_AUTOLOAD_ALLOW': str(d / 'allow')}
            r = subprocess.run(['sh', str(self.GUARD), '--prepare'], env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(helper.read_text(), '/opt/mu300/bin/mu300-modprobe-guard\n')
            table = (d / 'cache' / '7.2.11').read_text().splitlines()
            self.assertIn('net_pf_30 tipc', table)
            self.assertIn('net_pf_16_proto_16_family_TIPCv2 tipc', table)
            self.assertEqual((d / 'cache').stat().st_mode & 0o777, 0o700)
            # with the switch file the helper stays the system's
            helper.write_text('/sbin/modprobe\n')
            (d / 'allow').write_text('')
            subprocess.run(['sh', str(self.GUARD), '--prepare'], env=env, capture_output=True, text=True)
            self.assertEqual(helper.read_text(), '/sbin/modprobe\n')

    def test_the_system_installs_it(self):
        conf = (TOP / 'openwrt/overlay/etc/sysctl.d/98-mu300-modules.conf').read_text()
        self.assertIn('dev.tty.ldisc_autoload = 0', conf)
        hw = (TOP / 'openwrt/overlay/etc/init.d/mu300-hw').read_text()
        self.assertIn('/opt/mu300/bin/mu300-modprobe-guard --prepare', hw)
        self.assertTrue(self.GUARD.stat().st_mode & 0o111)


if __name__ == '__main__':
    unittest.main()
