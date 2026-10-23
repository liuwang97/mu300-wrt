"""The USB gadget's network functions: f_ncm and f_rndis forget the os_desc_table they free at unbind (patch 0027), so
a bind without MS OS descriptors after one with them (the USB network mode changing between ncm, ecm and rndis) leaves
no stale pointer for the next unbind to free a second time."""
import unittest

from helpers import TOP

KPATCH = TOP / 'kernel/patches/0027-usb-gadget-ncm-rndis-os-desc-table-double-free.patch'


class GadgetOsDescTable(unittest.TestCase):
    def test_both_unbinds_forget_the_table_they_freed(self):
        p = KPATCH.read_text()
        for f in ('f_ncm.c', 'f_rndis.c'):
            hunk = p.split(f'+++ b/drivers/usb/gadget/function/{f}\n', 1)[1].split('\n--- ', 1)[0]
            self.assertIn('\n \tkfree(f->os_desc_table);\n+\tf->os_desc_table = NULL;\n \tf->os_desc_n = 0;\n', hunk)

    def test_one_patch_with_its_number(self):
        names = [q.name for q in (TOP / 'kernel/patches').rglob('0027-*.patch')]
        self.assertEqual([KPATCH.name], names)


if __name__ == '__main__':
    unittest.main()
