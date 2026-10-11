"""adb over USB to Linux: the adbd build (tools/adbd), the gadget side (mu300-adbd, against a fake configfs gadget
whose UDC is a plain file) and its switch and keys (mu300-usb adb), the kernel option and the images."""
import os
import signal
import subprocess
import time
import unittest

from helpers import BIN, TOP, ShellTest

ADBD_DIR = TOP / 'tools/adbd'
PATCH = (ADBD_DIR / 'mu300-adbd.patch').read_text()
KEY = 'QAAAA' + 'A' * 694 + '='          # the shape of an Android RSA-2048 public key (adbkey.pub)
KEY2 = 'QAAAA' + 'B' * 694 + '='


def added(patch, path):
    """The lines PATCH adds to PATH (a/PATH b/PATH), joined."""
    out, cur = [], None
    for line in patch.splitlines():
        if line.startswith('+++ '):
            cur = line[6:]
        elif cur == path and line.startswith('+') and not line.startswith('+++'):
            out.append(line[1:])
    return '\n'.join(out)


class AdbdBuild(unittest.TestCase):
    """What tools/adbd builds: the security of the daemon is in its patch, so the patch is checked line by line."""

    def test_source_is_pinned(self):
        b = (ADBD_DIR / 'build.sh').read_text()
        self.assertIn('https://github.com/nmeum/android-tools/releases/download/$VER/android-tools-$VER.tar.xz', b)
        self.assertRegex(b, r'\nSHA=[0-9a-f]{64}\n')
        self.assertIn("sha256sum -c -", b)
        self.assertIn('patch -p1 < /adbd/mu300-adbd.patch', b)

    def test_authentication_is_always_required(self):
        main = added(PATCH, 'adb/daemon/main.cpp')
        self.assertIn('auth_required = true;', main)
        # Debian's adbd sets it false on Linux; nothing here may
        self.assertNotIn('auth_required = false', main)
        self.assertNotRegex(PATCH, r'\n\+.*auth_required = false')

    def test_keys_only_from_root_owned_files(self):
        auth = added(PATCH, 'native/libs/adbd_auth/adbd_auth.cpp')
        self.assertIn('{"/etc/mu300/adb_keys", "/root/.android/adb_keys"}', auth)
        # no Android framework socket that could approve a key (it would come from the environment)
        self.assertIn('sock_fd_.reset(-1);', auth)

    def test_usb_only_never_tcp(self):
        main = added(PATCH, 'adb/daemon/main.cpp')
        self.assertIn('#if defined(__linux__)', main)        # FunctionFS on Linux
        self.assertIn('return 1;', main)                    # no FunctionFS: exit, no TCP fallback
        self.assertIn('(void)setup_adb;', main)
        # the TCP/vsock listeners stay inside #else (Android only): the patch opens that #else ...
        self.assertRegex(PATCH, r'\(void\)setup_adb;\n\+#else\n')

    def test_files_and_shell_are_not_world_writable(self):
        self.assertIn('umask(022);', added(PATCH, 'adb/daemon/main.cpp'))
        self.assertIn('mode &= ~022;', added(PATCH, 'adb/daemon/file_sync_service.cpp'))
        sh = added(PATCH, 'adb/daemon/shell_service.cpp')
        self.assertIn('env["TMPDIR"] = "/tmp";', sh)
        self.assertIn('login_shell', sh)

    def test_left_out_services(self):
        stubs = (ADBD_DIR / 'stubs.cpp').read_text()
        for f in ('init_jdwp', 'create_jdwp_service_socket', 'adbd_wifi_init', 'setup_mdns', 'b64_pton'):
            self.assertIn(f, stubs)
        cm = (ADBD_DIR / 'CMakeLists.txt').read_text()
        self.assertIn('-static', cm)
        for f in ('daemon/adb_wifi.cpp', 'daemon/jdwp_service.cpp', 'daemon/mdns.cpp', 'protobuf'):
            self.assertNotIn(f, cm)

    def test_binary_is_not_tracked(self):
        self.assertIn('\ntools/adbd/adbd\n', (TOP / '.gitignore').read_text())


class AdbImages(unittest.TestCase):
    def test_kernel_has_functionfs(self):
        self.assertIn('\nCONFIG_USB_CONFIGFS_F_FS=y\n', (TOP / 'upstream/mu300-mainline.config').read_text())

    def test_ubuntu_image(self):
        a = (TOP / 'rootfs/assemble.sh').read_text()
        self.assertIn('install -m755 /adbd $R/opt/mu300/bin/adbd', a)
        self.assertIn('mu300-adbd.service:multi-user.target', a)
        unit = (TOP / 'rootfs/overlay/etc/systemd/system/mu300-adbd.service').read_text()
        self.assertIn('ExecStart=/opt/mu300/bin/mu300-adbd run', unit)
        self.assertIn('ExecStopPost=/opt/mu300/bin/mu300-adbd stop', unit)
        self.assertIn('KillMode=mixed', unit)
        self.assertIn('Before=mu300-lan.service', unit)

    def test_openwrt_image(self):
        b = (TOP / 'openwrt/build-rootfs.sh').read_text()
        self.assertIn('$(opt tools/adbd/adbd adbd)', b)
        self.assertIn('cp /in/adbd $R/opt/mu300/bin/adbd', b)
        self.assertRegex(b, r'for s in mu300-accounts [^\n]* mu300-adbd; do')
        init = (TOP / 'openwrt/overlay/etc/init.d/mu300-adbd').read_text()
        self.assertIn('[ "$(cat /etc/mu300/adb 2>/dev/null)" = on ] || return 0', init)
        self.assertIn('procd_set_param command /opt/mu300/bin/mu300-adbd run', init)
        self.assertNotIn('respawn', init)   # the loop restarts adbd; a respawned loop would rebind the gadget again

    def test_release_builds_it(self):
        r = (TOP / 'tools/make-release.sh').read_text()
        self.assertIn('tools/adbd/build.sh', r)
        self.assertIn('/adbd:ro', r)


class Gadget(ShellTest):
    """mu300-adbd run/stop on a fake gadget: NCM (or RNDIS) + ACM in configs/c.1, bound to 25100000.dwc3."""

    UDC = '25100000.dwc3'

    def setUp(self):
        super().setUp()
        g = self.g = self.tmp / 'gadget'
        for d in ('functions/ncm.usb0', 'functions/acm.GS0', 'functions/rndis.rn0', 'configs/c.1', 'os_desc'):
            (g / d).mkdir(parents=True)
        (g / 'configs/c.1/f1').symlink_to(g / 'functions/ncm.usb0')
        (g / 'configs/c.1/f2').symlink_to(g / 'functions/acm.GS0')
        (g / 'UDC').write_text(self.UDC + '\n')
        (g / 'bcdDevice').write_text('0x0301\n')
        (g / 'os_desc/use').write_text('0\n')
        self.ffs = self.tmp / 'ffs'
        self.mounts = self.tmp / 'mounts'
        self.mounts.write_text('')
        self.conf = self.tmp / 'adb'
        self.conf.write_text('on\n')
        self.role = self.tmp / 'role'
        self.role.write_text('device\n')
        udc = self.tmp / 'udc'
        (udc / self.UDC).mkdir(parents=True)
        self.builtin = self.tmp / 'modules.builtin'
        self.builtin.write_text('kernel/drivers/usb/gadget/function/usb_f_fs.ko\n')
        # adbd: writes its descriptors (ep1 appears), then waits to be stopped
        self.adbd = self.tmp / 'adbd'
        self.adbd.write_text('#!/bin/sh\necho start >> "$STUBLOG/adbd.log"\n: > "$MU300_FFS/ep1"\n'
                             'trap \'rm -f "$MU300_FFS/ep1"; echo stop >> "$STUBLOG/adbd.log"; exit 0\' TERM\n'
                             'while :; do sleep 0.05; done\n')
        self.adbd.chmod(0o755)
        self.stub('mount', 'echo "mount $*" >> "$STUBLOG/calls"; '
                           '[ "$2" = functionfs ] && echo "adb $4 functionfs rw 0 0" >> "$MU300_PROC_MOUNTS"; :')
        self.stub('umount', 'echo "umount $*" >> "$STUBLOG/calls"; : > "$MU300_PROC_MOUNTS"')
        self.stub('ip', 'echo "ip $*" >> "$STUBLOG/calls"')
        self.stub('id', 'echo 0')
        self.envs = dict(MU300_GADGET=self.g, MU300_FFS=self.ffs, MU300_ADBD_BIN=self.adbd, MU300_ADB_CONF=self.conf,
                         MU300_PROC_MOUNTS=self.mounts, MU300_PROC_FILESYSTEMS=self.tmp / 'nofs',
                         MU300_MODULES_BUILTIN=self.builtin, MU300_UDC_CLASS=udc, MU300_USB_ROLE=self.role,
                         MU300_USB_HOOK=self.tmp / 'nohook', MU300_ADBD_TICK='0.1', MU300_ADBD_GRACE='3')

    def start(self, shell, **env):
        e = dict(self.envs)
        e.update(env)
        return subprocess.Popen(shell + [str(BIN / 'mu300-adbd'), 'run'], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=self.env(**e))

    def until(self, cond, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def stop(self, p):
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=20)
        return p.returncode, out + err

    def read(self, name):
        return (self.g / name).read_text().strip()

    def linked(self):
        return os.path.islink(self.g / 'configs/c.1/f3')

    def calls(self):
        p = self.tmp / 'calls'
        return p.read_text() if p.exists() else ''

    def test_off_leaves_the_gadget_alone(self):
        self.conf.write_text('off\n')
        for shell in self.each_shell():
            r = self.script(shell, BIN / 'mu300-adbd', 'run', **self.envs)
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertIn('adb is off', r.stdout)
            self.assertFalse((self.g / 'functions/ffs.adb').exists())
            self.assertEqual('', self.calls())

    def test_no_adbd_or_no_functionfs_leaves_the_gadget_alone(self):
        for shell in self.each_shell():
            r = self.script(shell, BIN / 'mu300-adbd', 'run', **dict(self.envs, MU300_ADBD_BIN=self.tmp / 'none'))
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertIn('no adbd in this system', r.stderr)
            # a kernel without FunctionFS: not in the filesystems, not built in, and the function cannot be made
            # (here: something in its way, which stops root too)
            self.builtin.write_text('kernel/drivers/usb/gadget/function/usb_f_ncm.ko\n')
            (self.g / 'functions/ffs.adb').write_text('')
            try:
                r = self.script(shell, BIN / 'mu300-adbd', 'run', **self.envs)
            finally:
                (self.g / 'functions/ffs.adb').unlink()
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertIn('no FunctionFS', r.stderr)
            self.assertEqual(self.UDC, self.read('UDC'))
            self.assertFalse(self.linked())
            self.builtin.write_text('kernel/drivers/usb/gadget/function/usb_f_fs.ko\n')

    def test_joins_the_gadget_and_leaves_it_whole(self):
        for shell in self.each_shell():
            p = self.start(shell)
            try:
                self.assertTrue(self.until(self.linked), 'adb never joined the gadget')
                self.assertTrue(self.until(lambda: self.read('UDC') == self.UDC))
                self.assertEqual(str(self.g / 'functions/ffs.adb'), os.readlink(self.g / 'configs/c.1/f3'))
                # f1/f2 untouched: the network and the console keep their interface numbers
                self.assertEqual(str(self.g / 'functions/ncm.usb0'), os.readlink(self.g / 'configs/c.1/f1'))
                self.assertEqual('0x0303', self.read('bcdDevice'))
                self.assertEqual('1', self.read('os_desc/use'))
                self.assertEqual('0xcd', self.read('os_desc/b_vendor_code'))
                self.assertTrue(os.path.islink(self.g / 'os_desc/c.1'))
                self.assertIn(f'mount -t functionfs adb {self.ffs}', self.calls())
            finally:
                rc, out = self.stop(p)
            self.assertEqual(0, rc, out)
            self.assertFalse(self.linked())
            self.assertEqual(self.UDC, self.read('UDC'))
            self.assertEqual('0x0301', self.read('bcdDevice'))
            self.assertEqual('0', self.read('os_desc/use'))
            self.assertFalse(os.path.lexists(self.g / 'os_desc/c.1'))
            self.assertFalse((self.g / 'functions/ffs.adb').exists())
            self.assertIn(f'umount {self.ffs}', self.calls())
            self.assertEqual(['start', 'stop'], (self.tmp / 'adbd.log').read_text().split())
            (self.tmp / 'adbd.log').unlink()
            (self.tmp / 'calls').unlink()

    def test_rndis_keeps_its_ms_os_descriptors(self):
        (self.g / 'configs/c.1/f1').unlink()
        (self.g / 'configs/c.1/f1').symlink_to(self.g / 'functions/rndis.rn0')
        (self.g / 'bcdDevice').write_text('0x0302\n')
        (self.g / 'os_desc/use').write_text('1\n')
        for shell in self.each_shell():
            p = self.start(shell)
            try:
                self.assertTrue(self.until(self.linked))
                self.assertEqual('0x0304', self.read('bcdDevice'))
            finally:
                rc, out = self.stop(p)
            self.assertEqual(0, rc, out)
            self.assertEqual('0x0302', self.read('bcdDevice'))
            self.assertEqual('1', self.read('os_desc/use'))   # RNDIS needs them: boot/init's, left as they were

    def test_binds_again_when_functionfs_unbound_the_gadget(self):
        for shell in self.each_shell():
            p = self.start(shell, MU300_ADBD_GRACE='20')   # 20 ticks of 0.1 s: a 2 s grace, a margin under load
            try:
                # in the gadget and bound (linked comes first, inside the rebind: its bind must be over)
                self.assertTrue(self.until(self.linked))
                self.assertTrue(self.until(lambda: self.read('UDC') == self.UDC))
                time.sleep(0.3)
                # an adb connection ended: FunctionFS takes the gadget off the controller
                (self.g / 'UDC').write_text('\n')
                time.sleep(0.5)
                self.assertEqual('', self.read('UDC'))   # not at once (the grace for mu300-usb-reset)
                self.assertTrue(self.until(lambda: self.read('UDC') == self.UDC, timeout=15), 'never bound again')
                self.assertTrue(self.linked())
            finally:
                self.stop(p)

    def test_unready_adbd_never_costs_the_network(self):
        # adbd that never writes its descriptors: the gadget stays as it is, bound, without adb
        self.adbd.write_text('#!/bin/sh\ntrap "exit 0" TERM\nwhile :; do sleep 0.05; done\n')
        for shell in self.each_shell():
            p = self.start(shell)
            try:
                time.sleep(1)
                self.assertFalse(self.linked())
                self.assertEqual(self.UDC, self.read('UDC'))
                (self.g / 'UDC').write_text('\n')    # and when the gadget is unbound, it is bound without adb
                self.assertTrue(self.until(lambda: self.read('UDC') == self.UDC, timeout=15))
                self.assertFalse(self.linked())
            finally:
                self.stop(p)

    def test_host_role_is_left_alone(self):
        self.role.write_text('host\n')
        for shell in self.each_shell():
            (self.g / 'UDC').write_text('\n')
            p = self.start(shell)
            try:
                time.sleep(1.5)
                self.assertEqual('', self.read('UDC'))
                self.assertFalse(self.linked())
            finally:
                self.stop(p)

    def test_status(self):
        for shell in self.each_shell():
            r = self.script(shell, BIN / 'mu300-adbd', 'status', **self.envs)
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertIn('adb: on', r.stdout)
            self.assertIn('gadget: without adb', r.stdout)


class UsbAdb(ShellTest):
    """mu300-usb adb: the setting, the service (a stub), the keys."""

    def setUp(self):
        super().setUp()
        self.conf = self.tmp / 'etc/mu300/adb'
        self.keys = self.tmp / 'etc/mu300/adb_keys'
        self.stub('id', 'echo 0')
        self.stub('mu300-adbd', 'case $1 in usable) [ -e "$STUBLOG/unusable" ] && { echo "no adbd"; exit 1; }; exit 0 ;; '
                                'status) echo "adb status" ;; esac')
        self.svc = self.tmp / 'svc'
        self.svc.write_text('#!/bin/sh\necho "$1" >> "$STUBLOG/svc.log"\n')
        self.svc.chmod(0o755)

    def usb(self, shell, *args, stdin=None):
        return self.script(shell, BIN / 'mu300-usb', 'adb', *args, stdin=stdin, MU300_ADB_CONF=self.conf,
                           MU300_ADB_KEYS=self.keys, MU300_ADB_SERVICE=self.svc)

    def svc_log(self):
        p = self.tmp / 'svc.log'
        return p.read_text().split() if p.exists() else []

    def test_on_and_off(self):
        for shell in self.each_shell():
            (self.tmp / 'svc.log').unlink(missing_ok=True)
            r = self.usb(shell, 'on')
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual('on\n', self.conf.read_text())
            self.assertIn('root shell', r.stdout)
            self.assertIn('no key is authorised yet', r.stdout)
            r = self.usb(shell, 'off')
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual('off\n', self.conf.read_text())
            self.assertEqual(['restart', 'stop'], self.svc_log())

    def test_on_refused_where_it_cannot_run(self):
        (self.tmp / 'unusable').write_text('')
        for shell in self.each_shell():
            self.conf.parent.mkdir(parents=True, exist_ok=True)
            self.conf.write_text('off\n')
            r = self.usb(shell, 'on')
            self.assertEqual(1, r.returncode)
            self.assertIn('no adbd', r.stderr)
            self.assertEqual('off\n', self.conf.read_text())
            self.assertEqual([], self.svc_log())

    def test_root_only(self):
        self.stub('id', 'echo 1000')
        for shell in self.each_shell():
            for a in (['on'], ['off'], ['key', '-'], ['keys']):
                r = self.usb(shell, *a, stdin='')
                self.assertEqual(1, r.returncode, a)
            self.assertFalse(self.conf.exists())
            self.assertFalse(self.keys.exists())

    def test_keys(self):
        pub = self.tmp / 'adbkey.pub'
        pub.write_text(f'{KEY} me@my-mac.local')        # adbkey.pub has no newline at the end
        for shell in self.each_shell():
            self.keys.unlink(missing_ok=True)
            r = self.usb(shell, 'key', pub)
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual(f'{KEY} me@my-mac.local\n', self.keys.read_text())
            self.assertEqual(0o600, self.keys.stat().st_mode & 0o777)
            # once only
            r = self.usb(shell, 'key', pub)
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertIn('already authorised', r.stdout)
            self.assertEqual(1, self.keys.read_text().count('\n'))
            # from stdin; a comment cannot carry anything but a name
            r = self.usb(shell, 'key', '-', stdin=f'{KEY2} evil$(reboot);`x`\n')
            self.assertEqual(0, r.returncode, r.stderr)
            self.assertEqual(f'{KEY2} evilrebootx', self.keys.read_text().splitlines()[1])
            r = self.usb(shell, 'keys')
            self.assertIn('me@my-mac.local', r.stdout)

    def test_not_a_key(self):
        for shell in self.each_shell():
            self.keys.unlink(missing_ok=True)
            for bad in ('ssh-rsa AAAAB3NzaC1yc2E user', 'QAAAA' + 'A' * 100 + '=', KEY + 'x', KEY[:-1],
                        'QAAAA' + 'A' * 693 + '+/'):
                r = self.usb(shell, 'key', '-', stdin=bad + '\n')
                self.assertEqual(1, r.returncode, bad)
                self.assertIn('no key added', r.stderr)
            self.assertFalse(self.keys.exists() and self.keys.read_text().strip())


if __name__ == '__main__':
    unittest.main()
