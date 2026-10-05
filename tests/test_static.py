"""Checks over every script without running it: syntax under each shell that runs it, executable bits, and rules
that past bugs taught (see each test)."""
import re
import shutil
import subprocess
import unittest

from helpers import BIN, TOP, shells

OPENWRT = TOP / 'openwrt' / 'overlay'
LUCI_OVERLAY = TOP / 'openwrt' / 'luci-overlay'


def shebang(p):
    try:
        with open(p, 'rb') as f:
            first = f.readline()
    except OSError:
        return ''
    return first.decode(errors='replace').strip() if first.startswith(b'#!') else ''


def shell_scripts():
    """(path, 'sh'|'bash') of every shell script the project ships or runs."""
    cands = list(BIN.iterdir()) + list((TOP / 'tools').glob('*.sh')) + [
        TOP / 'install.sh', TOP / 'uninstall.sh', TOP / 'boot' / 'init', TOP / 'rootfs' / 'assemble.sh',
        TOP / 'kernel' / 'build-all.sh', TOP / 'tools' / 'i18n.sh', TOP / 'tools' / 'self-update.sh',
        TOP / 'tools' / 'linux-mode.sh', TOP / 'tools' / 'storage.sh', TOP / 'android-vendor' / 'ueventd-perms.sh']
    cands += [p for p in OPENWRT.rglob('*') if p.is_file()]
    cands += [p for p in LUCI_OVERLAY.rglob('*') if p.is_file()]
    out = []
    for p in sorted(set(cands)):
        if not p.is_file():
            continue
        sb = shebang(p)
        if 'bash' in sb:
            out.append((p, 'bash'))
        elif sb.endswith('sh') or 'rc.common' in sb or (not sb and p.suffix == '.sh'):
            out.append((p, 'sh'))
    return out


class Syntax(unittest.TestCase):
    def test_every_script_parses(self):
        sh_shells = [s for s in shells() if s[0] != 'bash'] or shells()
        scripts = shell_scripts()
        self.assertGreater(len(scripts), 40)
        for p, kind in scripts:
            if kind == 'bash' and not shutil.which('bash'):
                continue
            for s in ([['bash']] if kind == 'bash' else sh_shells):
                with self.subTest(script=str(p.relative_to(TOP)), shell=' '.join(s)):
                    r = subprocess.run(s + ['-n', str(p)], capture_output=True, text=True)
                    self.assertEqual(r.returncode, 0, r.stderr)

    def test_device_programs_are_executable(self):
        for p in BIN.iterdir():
            if p.is_file() and shebang(p):
                with self.subTest(p=p.name):
                    self.assertTrue(p.stat().st_mode & 0o111, 'not executable')


class Rules(unittest.TestCase):
    def test_powershell_device_commands_have_no_double_quotes(self):
        # Windows PowerShell 5.1 drops the double quotes inside an argument to a native program: `tr -d "\000"`
        # reached the device as tr -d \000 ("delete the character 0"), and every empty region was "not empty".
        # A command for the device (SuDo, adb shell) may therefore contain no `" and no "".
        for name in ('install.ps1', 'uninstall.ps1'):
            for n, line in enumerate((TOP / name).read_text().splitlines(), 1):
                code = line.split('#', 1)[0] if not line.lstrip().startswith('#') else ''
                if re.search(r'\bSuDo(ToFile)?\s+"|adb shell\s+"', code) and ('`"' in code or '""' in code):
                    self.fail(f'{name}:{n}: double quote inside a device command: {line.strip()}')

    def test_powershell_scripts_are_ascii(self):
        # Windows PowerShell 5.1 reads a file without a BOM as ANSI: non-ASCII text in the script is garbled
        for name in ('install.ps1', 'uninstall.ps1'):
            data = (TOP / name).read_bytes()
            bad = [i for i, b in enumerate(data) if b > 127]
            self.assertFalse(bad, f'{name}: non-ASCII byte at offset {bad[:1]}')

    def test_windows_pushes_text_with_lf(self):
        # a CRLF clone pushed its scripts as they were and Android's sh ran none of them (issue #7): the Windows
        # installers send text files through PushUnix, never a plain adb push
        for name in ('install.ps1', 'uninstall.ps1'):
            for n, line in enumerate((TOP / name).read_text().splitlines(), 1):
                if 'adb push' in line:
                    self.assertNotRegex(line, r'\.(sh|prop)\b|mu300-linux"|\$f"', f'{name}:{n}')
        self.assertIn('eol=lf', (TOP / '.gitattributes').read_text())

    def test_single_quoted_scripts_have_no_apostrophes(self):
        # A script handed to `sh -c '...'` (docker run) ends at its first apostrophe: "the U30 Air's charger" in a
        # comment there broke OpenWrt's build at release time, while every parser still saw balanced quotes.
        for name in ('openwrt/build-rootfs.sh', 'tools/make-release.sh'):
            lines = (TOP / name).read_text().splitlines()
            for n, line in enumerate(lines):
                if not line.rstrip().endswith("-c '"):
                    continue
                for m in range(n + 1, len(lines)):
                    if "'" in lines[m]:
                        self.assertTrue(lines[m].rstrip().endswith("'") and lines[m].count("'") == 1,
                                        f'{name}:{m + 1}: an apostrophe inside the script of line {n + 1}')
                        break

    def test_every_tool_that_mounts_the_filesystem_knows_the_card(self):
        for f in ('uninstall.sh', 'uninstall.ps1', 'tools/reset-password.sh', 'tools/android-import-hotspot.sh'):
            self.assertIn('mu300sd', (TOP / f).read_text(), f)

    def test_every_system_list_has_openwrt_luci(self):
        # every place that names the systems knows the third one, by its name or by the OpenWrt kind pattern
        files = ('boot/init', 'rootfs/overlay/opt/mu300/bin/mu300-update', 'rootfs/overlay/opt/mu300/bin/mu300-os',
                 'tools/android-install.sh', 'tools/reset-password.sh', 'tools/vendor-overlay.py', 'install.sh',
                 'install.ps1', 'tools/make-release.sh')
        for f in files:
            with self.subTest(file=f):
                text = (TOP / f).read_text()
                self.assertTrue('openwrt-luci' in text or 'openwrt-*' in text or 'openwrt|openwrt-' in text, f)

    def test_openwrt_luci_build_wiring(self):
        # MU300_SYSTEM=openwrt-luci: the same build as plain OpenWrt plus the panel, its catalogs compiled from po/
        # and Aurora pinned by hash (D3, D5); ImmortalWrt with the panel was never tested by anyone, so refused
        text = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        for s in ('MU300_SYSTEM', '05f9015e0a4e2859f6a153f69e472f2984481490d4ce6db19b8a41bba7264f1e', 'po2lmo.py',
                  'luci-overlay', 'packages.txt', 'luci-i18n-base-zh-cn', 'luci-i18n-firewall-tr'):
            self.assertIn(s, text)
        arm = re.search(r'^\s*openwrt-luci\)(.*?);;', text, re.M | re.S)
        self.assertIsNotNone(arm, 'no openwrt-luci arm in the MU300_SYSTEM case')
        self.assertRegex(arm.group(1), r'"\$FLAVOUR" = openwrt \]', 'openwrt-luci does not refuse immortalwrt')
        self.assertIn('mu300-$SYSTEM-$VER-rootfs.tar.gz', text)
        # ImmortalWrt keeps the name it had; the Aurora hash is pinned in the script, only the file may be overridden
        self.assertIn('mu300-immortalwrt-$VER-rootfs.tar.gz', text)
        self.assertNotIn('MU300_LUCI_THEME_SHA256', text)
        self.assertRegex(text, r'(?m)^\s*THEME_SHA=05f9015e0a4e2859f6a153f69e472f2984481490d4ce6db19b8a41bba7264f1e$')
        mk = (TOP / 'openwrt' / 'luci-app-mu300' / 'Makefile').read_text()
        self.assertRegex(mk, r'set -e; \$\(foreach', 'a failing catalog must fail the compile')
        f = LUCI_OVERLAY / 'etc' / 'uci-defaults' / '91-mu300-luci'
        self.assertTrue(f.is_file() and f.stat().st_mode & 0o111, f'{f} missing or not executable')
        self.assertIn('/luci-static/aurora', f.read_text())
        self.assertIn(f, [p for p, _ in shell_scripts()])   # so test_every_script_parses parses it

    def test_uninstallers_have_no_per_system_list(self):
        # they remove the whole Linux filesystem; a per-system case arm added later would forget the third name
        for f in ('uninstall.sh', 'uninstall.ps1'):
            text = (TOP / f).read_text()
            self.assertNotRegex(text, r'(^|\s)(ubuntu|openwrt)\)', f)

    def test_init_finds_partitions_after_the_modules(self):
        # the eMMC driver is one of the vendor modules: misc and boot_b cannot be found before they are loaded
        init = (TOP / 'boot' / 'init').read_text()
        calls = [l.strip() for l in init.splitlines() if l.strip() in ('load_vendor_modules', 'find_partitions')]
        self.assertEqual(calls, ['load_vendor_modules', 'find_partitions'])

    def test_every_device_has_its_files(self):
        # a device the installers know needs its module order; its modules come from kernel/build-<device>.sh
        for dev in ('u30air',):
            self.assertTrue((TOP / 'boot' / f'module-order-{dev}.txt').is_file())
            self.assertTrue((TOP / 'kernel' / f'{dev}.fragment').is_file())
            self.assertIn(dev, (TOP / 'install.sh').read_text())
            self.assertIn(dev, (TOP / 'install.ps1').read_text())

    def test_mainline_keeps_the_sd_host(self):
        # the SD card can hold the Linux filesystem: the port lets the card slot's host probe next to the eMMC, and
        # still keeps any other sdhci host (the stock DT's sdio_wifi; Wi-Fi is on PCIe) out
        port = (TOP / 'upstream' / 'port' / 'install.py').read_text()
        self.assertIn('MU300: only the eMMC and the card slot', port)
        self.assertIn('if (!of_property_read_bool(pdev->dev.of_node, "non-removable") &&', port)
        self.assertIn('of_property_match_string(pdev->dev.of_node, "sprd,name", "sdio_sd") < 0)', port)
        self.assertIn('MU300: CD GPIO deferred', port)
        # a deferral that is only masked leaves the rest of mmc_of_parse() undone (UHS modes, no-sdio, no-mmc): the
        # unresolvable cd-gpios is dropped and the parse run again
        self.assertIn('of_remove_property(pdev->dev.of_node, cd)', port)
        self.assertIn("'MU300: only the eMMC and the card slot', 'MU300: CD GPIO deferred', "
                      "'of_remove_property(pdev->dev.of_node, cd)'", port)
        self.assertRegex((TOP / 'upstream' / 'make-bundle.sh').read_text(), r"printf 'sdcard\\n' > \"\$W/b/features\"")

    def test_every_release_kernel_bundle_has_the_sd_host(self):
        # 5.4 reads the card as well (FINDINGS 31j): its bundle says so, and the release audit fails when any of the
        # three bundles does not (mu300-update refuses such a bundle for a system on the card)
        mr = (TOP / 'tools' / 'make-release.sh').read_text()
        self.assertIn("printf 'sdcard\\n' > \"$K/features\"", mr)
        self.assertLess(mr.index('$K/features'), mr.index('tar -C "$K" -czf "$D/mu300-kernel.tar.gz" .'))
        self.assertIn('for a in mu300-kernel mu300-kernel-6.18 mu300-kernel-7.2; do\n'
                      '    tar -xzOf "$D/$a.tar.gz" ./features 2>/dev/null | grep -qx sdcard', mr)

    def test_quiet_console_sysctl_on_both_systems(self):
        # K24: both images carry the same drop-in (systemd-sysctl on Ubuntu, procd's /etc/init.d/sysctl on OpenWrt)
        a = (TOP / 'rootfs' / 'overlay' / 'etc' / 'sysctl.d' / '99-mu300-console.conf').read_text()
        b = (TOP / 'openwrt' / 'overlay' / 'etc' / 'sysctl.d' / '99-mu300-console.conf').read_text()
        self.assertEqual(a, b)
        self.assertRegex(a, r'(?m)^kernel\.printk = 1$')


if __name__ == '__main__':
    unittest.main()
