"""The Android vendor daemons' own account.

Android's system user is uid 1000, which is also the first login account a system makes: the nodes go to the vendor
account mu300vnd (uid 1000 only where there is no account at all), nothing is writable by everyone, shared memory is
the video group's, the daemons start as mu300vnd where setpriv can switch to it, and updated systems keep the account.
The generator and the initramfs' copy are the kernel tree's (its test_from_openwrt_vendor_ids); the root filesystem's
copy is self-contained.
"""
import fnmatch
import os
import re
import shutil

from helpers import BIN, TOP, ShellTest

PERMS = BIN / 'ueventd-perms.sh'
# the script as it was until v2026.10.18: Android's numbers taken over
BEFORE = TOP / "openwrt" / "tests" / 'fixtures' / 'ueventd-perms-v2026.10.18.sh'
# a system's accounts with the vendor account made at boot
IDS = {'$V': 999, '$VG': 991, '$GPU': 44}
# who must not gain anything: the login user (uid 1000, its groups on the image), a second login user in the usual
# groups and everyone else; the vendor account itself is new and is not compared
PEOPLE = {'a login user': (1000, {1000, 4, 20, 24, 25, 27, 29, 30, 44, 46}),
          'a second user': (1001, {1001, 100, 44, 20, 29, 46, 993, 996}),
          'others': (65534, {65534})}
# what Linux starts a node with that neither script names (the RTC: root:root 0660, as udev leaves it here)
LINUX = (0, 0, 0o660)
LINE = re.compile(r'for n in (.+?); do \[ -e "\$n" \] && (?:chown (\S+):(\S+) "\$n"(?: && chmod ([0-7]+) "\$n")?'
                  r'|chmod ([0-7]+) "\$n"); done')


def rules(text):
    """[(path, owner, group, mode), ...] of every node line (owner/group None for a chmod alone)"""
    out = []
    for line in text.splitlines():
        m = LINE.fullmatch(line)
        if m:
            out.append((m.group(1), m.group(2), m.group(3), m.group(4) or m.group(5)))
    return out


def final_states(text):
    """{node: (owner, group, mode)}: every node a line of TEXT names (a glob as one concrete name), after all lines"""
    lines = rules(text)
    names = set()
    for paths, _, _, _ in lines:
        for p in paths.split():
            names.add(p.replace('*', 'X0'))
    out = {}
    for n in names:
        o, g, m = LINUX
        for paths, owner, group, mode in lines:
            if any(fnmatch.fnmatchcase(n, p) for p in paths.split()):
                if owner is not None:
                    o, g = int(IDS.get(owner, owner)), int(IDS.get(group, group))
                if mode is not None:
                    m = int(mode, 8)
        out[n] = (o, g, m)
    return out


def access(state, uid, groups):
    """the rwx bits STATE gives a process of UID in GROUPS"""
    o, g, m = state
    if uid == o:
        return (m >> 6) & 7
    if g in groups:
        return (m >> 3) & 7
    return m & 7


class Generated(ShellTest):
    def test_nobody_gains_anything(self):
        before, after = final_states(BEFORE.read_text()), final_states(PERMS.read_text())
        for n in sorted(set(before) | set(after)):
            for who, (uid, groups) in PEOPLE.items():
                was = access(before.get(n, LINUX), uid, groups)
                now = access(after.get(n, LINUX), uid, groups)
                self.assertEqual(now & ~was, 0, f'{n}: {who} gains {now & ~was:o} ({before.get(n)} -> {after.get(n)})')

    def test_the_modem_calibration_was_the_login_users_and_is_not(self):
        before, after = final_states(BEFORE.read_text()), final_states(PERMS.read_text())
        for n in ('/dev/block/by-name/nr_X0', '/dev/block/by-name/persist'):
            self.assertEqual(access(before[n], *PEOPLE['a login user']), 6)
            self.assertEqual(access(after[n], *PEOPLE['a login user']), 0)



    def test_every_line_is_understood(self):
        text = PERMS.read_text()
        lines = [x for x in text.splitlines() if x.startswith('for n in ')]
        self.assertEqual(len(rules(text)), len(lines))
        self.assertGreater(len(lines), 500)

    def test_nothing_writable_by_everyone(self):
        for path, _, _, mode in rules(PERMS.read_text()):
            if mode is None:
                continue
            m = int(mode, 8)
            self.assertEqual(m & 0o002, 0, (path, mode))
            if path.startswith('/dev/'):
                self.assertEqual(m & 0o007, 0, (path, mode))     # 0666/0646 nodes are 0660 here

    def test_no_numeric_account(self):
        # no Android number reaches chown: 1000 (a login account), 1001 (the next one), 1003, ...
        for path, owner, group, _ in rules(PERMS.read_text()):
            if owner is None:
                continue
            self.assertIn(owner, ('0', '$V'), path)
            self.assertIn(group, ('0', '$VG', '$GPU'), path)

    def test_partitions(self):
        r = {p: (o, g, m) for p, o, g, m in rules(PERMS.read_text()) if p.startswith('/dev/block/')}
        # the modem's calibration NV and persist: the vendor account's, never a login account's
        for p in ('/dev/block/by-name/nr_*', '/dev/block/by-name/persist', '/dev/block/by-name/calinv'):
            self.assertEqual(r[p], ('$V', '$VG', '0660'), p)
        for p, (o, g, m) in r.items():
            self.assertIn(o, ('0', '$V'), p)
            self.assertNotEqual(g, '$GPU', p)

    def test_shared_memory_is_the_video_groups(self):
        r = {p: (o, g, m) for p, o, g, m in rules(PERMS.read_text()) if o is not None}
        self.assertNotIn('/dev/mali0', r)
        for p in ('/dev/ion', '/dev/sprd_dmabuf', '/dev/dma_heap/carveout_fd'):
            self.assertEqual(r[p], ('0', '$GPU', '0660'), p)

    def test_root_only(self):
        # keys pressed, the serial console and the firmware search path were the first human's: root's alone now
        r = {p: (o, g, m) for p, o, g, m in rules(PERMS.read_text()) if o is not None}
        for p in ('/sys/module/firmware_class/parameters/path', '/dev/input/event*', '/dev/ttyGS*'):
            self.assertEqual(r[p], ('0', '0', '0600'), p)
        self.assertNotIn('chmod 660 /sys/module/firmware_class', PERMS.read_text())
        # the RTC stays root's; refnotify reaches it through the group
        r = {p: (o, g, m) for p, o, g, m in rules(PERMS.read_text()) if o is not None}
        self.assertEqual(r['/dev/rtc*'], ('0', '$VG', '0660'))


    def ids(self, shell, passwd, group):
        etc = self.tmp / 'etc'
        etc.mkdir(exist_ok=True)
        for name, text in (('passwd', passwd), ('group', group)):
            if text is None:
                (etc / name).unlink(missing_ok=True)
            else:
                (etc / name).write_text(text)
        text = PERMS.read_text()
        header = text[:text.index('\nfor n in ')]
        r = self.sh(shell, header + '\necho "$V $VG $GPU"', MU300_ETC=etc)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_ids_at_run_time(self):
        for shell in self.each_shell():
            # the vendor account and a video group: a login account at 1000 owns nothing
            self.assertEqual(self.ids(shell, 'root:x:0:0::/root:/bin/bash\nuser:x:1000:1000::/home/user:/bin/bash\n'
                                             'mu300vnd:x:991:988:MU300:/nonexistent:/usr/sbin/nologin\n',
                                      'root:x:0:\nvideo:x:44:user\nuser:x:1000:\nmu300vnd:x:988:'), '991 988 44')
            # OpenWrt: no such account, no video group: the uid the daemons switch to themselves; shared memory root's
            self.assertEqual(self.ids(shell, 'root:x:0:0:root:/root:/bin/ash\nnetwork:x:101:101::/var:/bin/false\n',
                                      'root:x:0:\nnetwork:x:101:\n'), '1000 1000 0')
            # the initramfs: no /etc/passwd at all
            self.assertEqual(self.ids(shell, None, None), '1000 1000 0')
            # a name that only starts like it is not it
            self.assertEqual(self.ids(shell, 'mu300vndx:x:5:5::/:/bin/false\n', 'mu300vndx:x:5:\n'), '1000 1000 0')


# util-linux's setpriv (its --help names --reuid), and busybox's applet, which has no --reuid (OpenWrt)
UTIL_LINUX_SETPRIV = ('case $1 in --help) printf " --reuid <uid|user>  set real and effective uid\\n"; exit 0 ;; esac\n'
                      'echo "setpriv $*" >> "$STUBLOG/calls"')
BUSYBOX_SETPRIV = ('case $1 in --help) printf "Usage: setpriv [OPTIONS] PROG ARGS\\n\\t--nnp\\n\\t--inh-caps\\n" >&2; '
                   'exit 1 ;; esac\necho "setpriv: unrecognized option $1" >&2; exit 1')


class DaemonsStartAsTheAccount(ShellTest):
    def setUp(self):
        super().setUp()
        self.stub('mountpoint', 'exit 0')
        self.stub('setpriv', UTIL_LINUX_SETPRIV)
        self.stub('chroot', 'echo "chroot $*" >> "$STUBLOG/calls"')
        self.etc = self.tmp / 'etc'
        self.etc.mkdir()

    def run_daemon(self, shell, name):
        calls = self.tmp / 'calls'
        calls.unlink(missing_ok=True)
        r = self.script(shell, BIN / 'android-run', name, MU300_ETC=self.etc, MU300_ANDROID=self.tmp / 'a')
        self.assertEqual(r.returncode, 0, r.stderr)
        return calls.read_text().strip()

    def test_with_the_account(self):
        (self.etc / 'passwd').write_text('user:x:1000:1000::/home/user:/bin/bash\nmu300vnd:x:991:988::/:/x\n')
        a = self.tmp / 'a'
        for shell in self.each_shell():
            c = '-all,+sys_chroot,+chown,+block_suspend'
            self.assertEqual(self.run_daemon(shell, 'modem_control'),
                             f'setpriv --reuid=mu300vnd --regid=mu300vnd --clear-groups --no-new-privs --inh-caps={c} '
                             f'--ambient-caps={c} --bounding-set={c} chroot {a} /vendor/bin/modem_control')
            c = '-all,+sys_chroot,+sys_time'
            self.assertIn(f'--inh-caps={c} --ambient-caps={c} --bounding-set={c} chroot {a} /vendor/bin/refnotify',
                          self.run_daemon(shell, 'refnotify'))
            # the NV server never switches to system on Android either: root, as before
            self.assertEqual(self.run_daemon(shell, 'cp_diskserver'), f'chroot {a} /vendor/bin/cp_diskserver')

    def test_without_the_account(self):
        # OpenWrt: no account at all - the daemons switch to uid 1000 themselves, as they always did
        (self.etc / 'passwd').write_text('root:x:0:0:root:/root:/bin/ash\n')
        for shell in self.each_shell():
            self.assertEqual(self.run_daemon(shell, 'modem_control'),
                             f'chroot {self.tmp / "a"} /vendor/bin/modem_control')

    def test_vendor_start_execs_android_run(self):
        text = (BIN / 'android-vendor-start').read_text()
        self.assertTrue(text.rstrip().endswith('exec /opt/mu300/bin/android-run modem_control'))
        self.assertNotIn('exec chroot', text)
        # the account is there before the nodes are handed out
        self.assertLess(text.index('# --- account end'), text.index('sh /opt/mu300/bin/ueventd-perms.sh'))


class Account(ShellTest):
    def block(self):
        text = (BIN / 'android-vendor-start').read_text()
        return re.search(r'# --- account begin\n(.*?)# --- account end', text, re.S).group(1)

    def test_made_where_it_is_missing(self):
        self.stub('useradd', 'echo "useradd $*" >> "$STUBLOG/calls"')
        self.stub('setpriv', UTIL_LINUX_SETPRIV)
        etc = self.tmp / 'etc'
        etc.mkdir()
        calls = self.tmp / 'calls'
        for shell in self.each_shell():
            calls.unlink(missing_ok=True)
            (etc / 'passwd').write_text('user:x:1000:1000::/home/user:/bin/bash\n')
            r = self.sh(shell, 'set -e\n' + self.block(), MU300_ETC=etc, MU300_ANDROID_RUN=BIN / 'android-run')
            self.assertEqual(r.returncode, 0, r.stderr)
            call = calls.read_text()
            for opt in ('--system', '--user-group', '--no-create-home', '--home-dir /nonexistent', 'mu300vnd\n'):
                self.assertIn(opt, call)
            self.assertRegex(call, r'--shell /usr/s?bin/nologin')
            # there already: nothing
            calls.unlink()
            (etc / 'passwd').write_text('mu300vnd:x:991:988::/nonexistent:/usr/sbin/nologin\n')
            self.assertEqual(self.sh(shell, 'set -e\n' + self.block(), MU300_ETC=etc,
                                     MU300_ANDROID_RUN=BIN / 'android-run').returncode, 0)
            self.assertFalse(calls.exists())

    def test_a_failed_useradd_does_not_stop_the_modem(self):
        self.stub('useradd', 'exit 1')
        self.stub('setpriv', UTIL_LINUX_SETPRIV)
        etc = self.tmp / 'etc'
        etc.mkdir()
        (etc / 'passwd').write_text('user:x:1000:1000::/home/user:/bin/bash\n')
        for shell in self.each_shell():
            r = self.sh(shell, 'set -e\n' + self.block() + 'echo went-on', MU300_ETC=etc,
                        MU300_ANDROID_RUN=BIN / 'android-run')
            self.assertEqual(r.stdout.strip(), 'went-on', (shell, r.stderr))
            self.assertIn('uid 1000', r.stderr)


    def test_an_update_brings_it(self):
        # mu300-update's merge: the new image's passwd with the old system's human accounts - the image's mu300vnd
        # is in the updated system, at the image's uid, next to the device's login accounts
        upd = (BIN / 'mu300-update').read_text()
        fn = re.search(r'^merge_accounts\(\) \{.*?^\}\n', upd, re.S | re.M).group(0)
        old, new = self.tmp / 'old', self.tmp / 'new'
        for d, pw, gr in ((old, 'root:x:0:0::/root:/bin/bash\nuser:x:1000:1000::/home/user:/bin/bash\n'
                                'kaan:x:1001:1001::/home/kaan:/bin/bash\n',
                           'root:x:0:\nvideo:x:44:user,kaan\nuser:x:1000:\nkaan:x:1001:\n'),
                          (new, 'root:x:0:0::/root:/bin/bash\nuser:x:1000:1000::/home/user:/bin/bash\n'
                                'mu300vnd:x:991:988:MU300 Android vendor daemons:/nonexistent:/usr/sbin/nologin\n',
                           'root:x:0:\nvideo:x:44:user\nuser:x:1000:\nmu300vnd:x:988:\n')):
            (d / 'etc').mkdir(parents=True)
            (d / 'etc' / 'passwd').write_text(pw)
            (d / 'etc' / 'group').write_text(gr)
            (d / 'etc' / 'shadow').write_text(''.join(f'{x.split(":")[0]}:!:19000::::::\n' for x in pw.splitlines()))
            (d / 'etc' / 'gshadow').write_text(''.join(f'{x.split(":")[0]}:!::\n' for x in gr.splitlines()))
        for shell in self.each_shell():
            for f in ('passwd', 'group', 'shadow', 'gshadow'):
                (self.tmp / f).write_text((new / 'etc' / f).read_text())
            r = self.sh(shell, f'ACCOUNTS_MARK=/x\n{fn}\nmerge_accounts {old} {new}')
            self.assertEqual(r.returncode, 0, r.stderr)
            pw = (new / 'etc' / 'passwd').read_text()
            self.assertIn('mu300vnd:x:991:988:', pw)
            self.assertIn('user:x:1000:1000:', pw)
            self.assertIn('kaan:x:1001:1001:', pw)
            self.assertIn('mu300vnd:x:988:', (new / 'etc' / 'group').read_text())
            self.assertIn('mu300vnd:', (new / 'etc' / 'shadow').read_text())
            for f in ('passwd', 'group', 'shadow', 'gshadow'):
                (new / 'etc' / f).write_text((self.tmp / f).read_text())


class OneDecision(ShellTest):
    """On OpenWrt with shadow-useradd installed, android-vendor-start could make mu300vnd and ueventd-perms.sh
    give it the modem's nodes (uid 999) while android-run has no setpriv that can switch users (OpenWrt has none;
    busybox's applet has no --reuid): chroot runs as root and the daemons switch to uid 1000 themselves, EACCES on
    /dev/modem, no modem. The account the nodes go to and the uid the daemons run as come from one decision, android-run's, for every combination of account, useradd and setpriv."""

    SETPRIVS = {'util-linux': UTIL_LINUX_SETPRIV, 'busybox': BUSYBOX_SETPRIV, 'none': None}

    def setUp(self):
        super().setUp()
        # a PATH with only what the scripts need here, so a setpriv or useradd of this machine never answers
        self.path = self.tmp / 'path'
        self.path.mkdir()
        for tool in ('grep', 'sed', 'cat', 'seq', 'tr', 'sh', 'echo', 'printf'):
            w = shutil.which(tool)
            if w:
                (self.path / tool).symlink_to(w)
        self.stub('mountpoint', 'exit 0')
        self.stub('chroot', 'echo "chroot $*" >> "$STUBLOG/calls"')
        self.etc = self.tmp / 'etc'
        self.etc.mkdir()
        text = (BIN / 'android-vendor-start').read_text()
        self.block = re.search(r'# --- account begin\n(.*?)# --- account end', text, re.S).group(1)
        perms = PERMS.read_text()
        self.header = perms[:perms.index('\nfor n in ')]

    def setup(self, account, useradd, setpriv):
        for f in self.stubs.iterdir():
            if f.name in ('setpriv', 'useradd'):
                f.unlink()
        if self.SETPRIVS[setpriv]:
            self.stub('setpriv', self.SETPRIVS[setpriv])
        if useradd:
            # what shadow-useradd does: the account at 999, its group at 999
            self.stub('useradd', 'echo "mu300vnd:x:999:999::/nonexistent:/bin/false" >> "$ETC/passwd"; '
                                 'echo "mu300vnd:x:999:" >> "$ETC/group"')
        pw = 'root:x:0:0:root:/root:/bin/ash\nnetwork:x:101:101::/var:/bin/false\n'
        gr = 'root:x:0:\nnetwork:x:101:\n'
        if account:
            pw += 'mu300vnd:x:999:999::/nonexistent:/bin/false\n'
            gr += 'mu300vnd:x:999:\n'
        (self.etc / 'passwd').write_text(pw)
        (self.etc / 'group').write_text(gr)

    def run_env(self):
        return dict(MU300_ETC=self.etc, ETC=self.etc, MU300_ANDROID_RUN=BIN / 'android-run',
                    MU300_ANDROID=self.tmp / 'a', PATH=f'{self.stubs}{os.pathsep}{self.path}')

    def node_owner(self, shell):
        """the uid:gid ueventd-perms.sh gives the modem's nodes after android-vendor-start's account block"""
        r = self.sh(shell, 'set -e\n' + self.block + self.header + '\necho "$V:$VG"', **self.run_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip().splitlines()[-1]

    def daemon_uid(self, shell):
        """the uid modem_control ends up as: the account setpriv starts it as, else 1000 (its own setuid)"""
        calls = self.tmp / 'calls'
        calls.unlink(missing_ok=True)
        r = self.script(shell, BIN / 'android-run', 'modem_control', **self.run_env())
        self.assertEqual(r.returncode, 0, r.stderr)
        call = calls.read_text().strip()
        if call.startswith('setpriv '):
            self.assertIn('--reuid=mu300vnd', call)
            uid = next(l.split(':')[2] for l in (self.etc / 'passwd').read_text().splitlines()
                       if l.startswith('mu300vnd:'))
            return uid
        self.assertTrue(call.startswith('chroot '), call)
        return '1000'

    def test_nodes_and_daemons_agree(self):
        for shell in self.each_shell():
            for account in (False, True):
                for useradd in (False, True):
                    for setpriv in self.SETPRIVS:
                        with self.subTest(account=account, useradd=useradd, setpriv=setpriv):
                            self.setup(account, useradd, setpriv)
                            owner = self.node_owner(shell)
                            uid = self.daemon_uid(shell)
                            self.assertEqual(owner.split(':')[0], uid)
                            # the vendor account only where setpriv can switch to it, uid 1000 everywhere else
                            vendor = setpriv == 'util-linux' and (account or useradd)
                            self.assertEqual(owner, '999:999' if vendor else '1000:1000')
                            # no account made that nothing would run as (OpenWrt with shadow-useradd)
                            if not account and setpriv != 'util-linux':
                                self.assertNotIn('mu300vnd', (self.etc / 'passwd').read_text())

    def test_the_reporters_device(self):
        # OpenWrt 25.12.5 with shadow-useradd, and mu300vnd already made by an earlier boot: no setpriv
        self.setup(account=True, useradd=True, setpriv='none')
        for shell in self.each_shell():
            r = self.script(shell, BIN / 'android-run', '--user', **self.run_env())
            self.assertEqual((r.returncode, r.stdout), (0, ''))
            self.assertEqual(self.node_owner(shell), '1000:1000')
            self.assertEqual(self.daemon_uid(shell), '1000')

    def test_without_the_variable_the_script_is_as_before(self):
        # ueventd-perms.sh run by itself (as the initramfs runs its copy) looks the account up by name
        self.setup(account=True, useradd=False, setpriv='none')
        for shell in self.each_shell():
            r = self.sh(shell, self.header + '\necho "$V:$VG"', MU300_ETC=self.etc)
            self.assertEqual(r.stdout.strip(), '999:999')
