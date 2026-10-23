"""The root filesystem's partition lookups that lead to writes - early-recorder's log partition, mu300-update's kernel
partition - and Android's by-name links: all of them on the eMMC only (the initramfs follows the same rule). A card can
carry an eMMC-style GPT (a raw clone of a device backup) with partitions named misc, boot_a and boot_b, and must never be
written to, whichever of mmcblk0 and mmcblk1 it is (the card slot's host can take mmc0)."""
import re
import shutil

from helpers import BIN, ShellTest

CARD_GPT = ['misc', 'boot_a', 'boot_b']                            # the card's p1..p3
EMMC_GPT = ['splloader', 'uboot_a', 'uboot_b', 'l_fixnv1_a', 'misc', 'boot_a', 'boot_b']   # misc is the eMMC's p5


def block(text, name):
    m = re.search(rf'# --- {name} begin\n(.*?)# --- {name} end', text, re.S)
    assert m, f'no {name} block'
    return m.group(1)


class EmmcLookup(ShellTest):
    def fake(self, layout):
        """layout: {disk: (type or None, [partition names])}; a fake / (self.tmp/root) with sys/block and the same
        partitions under sys/class/block, as the kernel has them."""
        root = self.tmp / 'root'
        for disk, (typ, parts) in layout.items():
            d = root / 'sys' / 'block' / disk
            (d / 'device').mkdir(parents=True)
            if typ:
                (d / 'device' / 'type').write_text(typ + '\n')
            for i, name in enumerate(parts, 1):
                p = f'{disk}p{i}'
                for where in (d / p, root / 'sys' / 'class' / 'block' / p):
                    where.mkdir(parents=True, exist_ok=True)
                    (where / 'uevent').write_text(f'MAJOR=179\nDEVNAME={p}\nDEVTYPE=partition\nPARTN={i}\nPARTNAME={name}\n')
        return root

    def lookups(self, shell, root, name):
        """What each of the three finds for partition NAME: early-recorder, mu300-update, android-vendor-start"""
        sysd = root / 'sys'
        rec = self.sh(shell, f'PART={name}\n' + block((BIN / 'early-recorder').read_text(), 'part')
                      + '\necho "$LOGDEV"', MU300_SYS=sysd).stdout.strip()
        upd = self.sh(shell, f'. "{BIN}/mu300-update"; part_dev {name}', MU300_LIB=1, MU300_BIN=BIN,
                      MU300_SYSROOT=root).stdout.strip()
        avs = self.sh(shell, block((BIN / 'android-vendor-start').read_text(), 'emmc')
                      + f'\nfor u in "$SYS/block/$E/$E"p*/uevent; do grep -qx PARTNAME={name} "$u" && '
                      'p=${u%/uevent} && echo /dev/${p##*/}; done', MU300_SYS=sysd).stdout.strip()
        return {'early-recorder': rec, 'mu300-update': upd, 'android-vendor-start': avs}

    def test_a_card_with_an_emmc_gpt_is_never_chosen(self):
        for card, emmc in (('mmcblk0', 'mmcblk1'), ('mmcblk1', 'mmcblk0')):
            layout = {card: ('SD', CARD_GPT), emmc: ('MMC', EMMC_GPT)}
            for shell in self.each_shell():
                for name, idx in (('misc', 5), ('boot_a', 6), ('boot_b', 7)):
                    root = self.fake(layout)
                    got = self.lookups(shell, root, name)
                    self.assertEqual(got, dict.fromkeys(got, f'/dev/{emmc}p{idx}'), (shell, card, name))
                    shutil.rmtree(root)

    def test_only_the_card_there_finds_nothing(self):
        # the eMMC's host has not bound (yet): the card, mmcblk0 or mmcblk1, holds the only partitions named so
        for card in ('mmcblk0', 'mmcblk1'):
            for shell in self.each_shell():
                root = self.fake({card: ('SD', CARD_GPT)})
                got = self.lookups(shell, root, 'boot_b')
                self.assertEqual(got, dict.fromkeys(got, ''), (shell, card))
                shutil.rmtree(root)

    def test_without_a_type_the_first_disk_that_is_not_a_card(self):
        # no disk says MMC: the eMMC is the first mmcblk disk that is not SD - never a card on mmcblk0
        root = self.fake({'mmcblk0': ('SD', CARD_GPT), 'mmcblk1': (None, EMMC_GPT)})
        for shell in self.each_shell():
            got = self.lookups(shell, root, 'misc')
            self.assertEqual(got, dict.fromkeys(got, '/dev/mmcblk1p5'), shell)

    def test_a_disk_without_a_type_does_not_end_android_vendor_start(self):
        # android-vendor-start runs under set -e: a disk with no device/type is a lookup that goes on, not the end
        # of the script (and of the modem's start)
        src = (BIN / 'android-vendor-start').read_text()
        self.assertRegex(src, r'(?m)^set -e')
        for layout, want in ((({'mmcblk0': (None, EMMC_GPT)}), 'mmcblk0'),
                             ({'mmcblk0': ('SD', CARD_GPT), 'mmcblk1': (None, EMMC_GPT)}, 'mmcblk1')):
            root = self.fake(layout)
            for shell in self.each_shell():
                r = self.sh(shell, 'set -e\n' + block(src, 'emmc') + '\necho "E=$E"', MU300_SYS=root / 'sys')
                self.assertEqual((r.returncode, r.stdout), (0, f'E={want}\n'), (shell, r.stderr))
            shutil.rmtree(root)


class UeventdPerms(ShellTest):
    def test_no_block_device_by_number(self):
        # mmcblk1p* is the card by number - or the eMMC, when the card slot's host took mmc0
        f = BIN / 'ueventd-perms.sh'
        if not f.exists():
            self.skipTest('no ueventd-perms.sh on the root filesystem')
        t = f.read_text()
        self.assertNotRegex(t, r'mmcblk[0-9]')
        self.assertIn('for n in /dev/block/mmcblk*rpmb;', t)     # the eMMC's: a card has no RPMB


if __name__ == '__main__':
    import unittest
    unittest.main()
