"""The vendor modules' lines that repeat all day on an idle board print at debug level: at their stock levels they fill
the system's log in minutes.

* the IPA (sipa_eth, sipa_rm, sipa core, sipa_dele), WCN loopcheck and Wi-Fi TX path lines print at debug level, and
  the errors next to them stay errors;
* -EINPROGRESS from sipa_nic_tx() (the IPA resuming) is not an error and not "don't have this flow ctrl err type"."""
import re
import unittest

from helpers import TOP

MODEM = TOP / 'kernel/modules/sprd_modem'
WCN = TOP / 'kernel/modules/wcn_bsp'
WLAN = TOP / 'kernel/modules/sprd_wlan_combo'

DEBUG = re.compile(r'\b(pr_debug|pr_debug_ratelimited|dev_dbg|netdev_dbg|WCN_DBG)\s*$')
# (the file in kernel/modules, the format the line prints)
SIPA_LINES = [
    (MODEM / 'sipa_v3/sipa_eth.c', '"wakeup queue on dev %s\\n"'),
    (MODEM / 'sipa_v3/sipa_eth.c', '"dev %s SIPA LEAVE FLOWCTRL\\n"'),
    (MODEM / 'sipa_v3/sipa_eth.c', '"stop queue on dev %s\\n"'),
    (MODEM / 'sipa_v3/sipa_usb.c', '"SIPA LEAVE FLOWCTRL\\n"'),
    (MODEM / 'sipa_v3/sipa_rm_res.c', '"%s state changed %d->%d\\n"'),
    (MODEM / 'sipa_v3/sipa_rm_res.c', '"EXIT with %d\\n"'),
    (MODEM / 'sipa_v3/sipa_rm_res.c', '"calling driver CB\\n"'),
    (MODEM / 'sipa_v3/sipa_rm_res.c', '"driver CB returned with %d\\n"'),
    (MODEM / 'sipa_v3/sipa_core.c', '"sipa resume hrtimer start\\n"'),
    (MODEM / 'sipa_v3/sipa_core.c',
     '"sipa ready to suspend and change to single\\n"'),
    (MODEM / 'sipa_v3/sipa_core.c', '"sipa prepare suspend finished\\n"'),
    (MODEM / 'sipa_delegate/sipa_dele_cmn.c', '"smsg_recv, smsg_cnt='),
    (MODEM / 'sipa_delegate/sipa_dele_cmn.c', '"smsg_send, dst='),
    (MODEM / 'sipa_delegate/sipa_dele_cp_in_ap.c',
     '"prod_id:%d, on_cmd, flag = %d\\n"'),
    (MODEM / 'sipa_delegate/sipa_dele_cp_in_ap.c',
     '"sipa_dele get pd success ret = %d\\n"'),
    (MODEM / 'sipa_delegate/sipa_dele_cp_in_ap.c',
     '"sipa pd_eb_flag = %d, pd_get_flag = %d\\n"'),
    (WCN / 'platform/wcn_txrx.c', '"%s, chn:%d\\n"'),
    (WCN / 'pcie/edma_engine.c', '"edma-tp:%d/%d (byte/us)\\n"'),
    (WCN / 'platform/loopcheck.c', '"tx:%s in %s\\n"'),
    (WCN / 'platform/loopcheck.c', '"tx:%s\\n"'),
    (WCN / 'platform/wcn_procfs.c', '"rx:%s\\n"'),
]
WLAN_LINES = [
    ('sc2355/pcie.c', '"%s, total free num:%lu; total tx_num:%lu\\n"'),
    ('sc2355/pcie.c', '"%s, free_num: %d, to_free_list num: %d\\n"'),
    ('sc2355/pcie.c', '"tx_cmd_pop num: %d,cmd_poped=%d, cmd_send=%d\\n"'),
    ('sc2355/pcie.c', '"%s,%d,debug: %d\\n"'),
    ('sc2355/pcie.c', '"%s, free_num=%d, data_num=%d\\n"'),
    ('sc2355/pcie.c', '"%s,%d free_num=%d, data_num=%d\\n"'),
    ('sc2355/pcie.c', '"%s: to free list empty\\n"'),
    ('sc2355/cmdevt.c', '"ctx_id %d recv rsp[%s]\\n"'),
    ('sc2355/tx.c', '"%s,AP mode, multicast bssid:'),
    ('common/cfg80211.c', '"%s\\n", __func__);\n\n\treturn cfg80211_set_beacon_ies'),
]


def printk_calls(text, fmt):
    """the printk-style macro in front of every occurrence of FMT in TEXT: pr_info("..., dev_info(dev, "..., a call
    split over two lines (pr_info\\n    ("...) too"""
    out = []
    for m in re.finditer(re.escape(fmt), text):
        mm = re.search(r'([A-Za-z_]\w*)\s*\(\s*(?:[A-Za-z_][\w>.-]*\s*,\s*)?$',
                       text[max(0, m.start() - 200):m.start()])
        if mm:
            out.append(mm.group(1))
    return out


class KernelLines(unittest.TestCase):
    def assert_debug(self, text, fmt, where):
        calls = printk_calls(text, fmt)
        self.assertTrue(calls, f'{fmt} not found in {where}')
        for c in calls:
            self.assertTrue(DEBUG.match(c), f'{fmt} printed with {c} in {where}')

    def test_sipa_and_wcn_lines_are_debug(self):
        for path, fmt in SIPA_LINES:
            self.assert_debug(path.read_text(), fmt, str(path.relative_to(TOP)))

    def test_wifi_tx_path_lines_are_debug(self):
        # (some of these lines are not in this driver's version at all)
        found = 0
        for path, fmt in WLAN_LINES:
            calls = printk_calls((WLAN / path).read_text(), fmt)
            found += len(calls)
            for c in calls:
                self.assertTrue(DEBUG.match(c), f'{fmt} printed with {c} in sprd_wlan_combo/{path}')
        self.assertGreater(found, 3)

    def test_einprogress_is_not_an_error_but_other_failures_are(self):
        text = (MODEM / 'sipa_v3/sipa_eth.c').read_text()
        i = text.index('if (ret == -EINPROGRESS)')
        block = text[i:i + 200]
        self.assertRegex(block, r'if \(ret == -EINPROGRESS\)\s*pr_debug\("fail to send, ret %d\\n", ret\);\s*'
                                r'else\s*pr_err_ratelimited\("fail to send, ret %d\\n", ret\);')
        for text in ((MODEM / 'sipa_v3/sipa_nic.c').read_text(),):
            self.assertRegex(text, r'case -EINPROGRESS:\n(\s*/\*.*\*/\n)?\s*break;\n\s*default:\n\s*dev_warn\(ipa->dev, '
                                   r'"don\'t have this flow ctrl err type')

    def test_the_errors_beside_them_stay_errors(self):
        keep = [(MODEM / 'sipa_delegate/sipa_dele_cp_in_ap.c', '"sipa_dele get pd fail ret = %d, ENABLE answered with FAIL\\n"', 'pr_err'),
                (MODEM / 'sipa_delegate/sipa_dele_cmn.c', '"smsg send fail %d prod_id = %d\\n"', 'pr_err'),
                (MODEM / 'sipa_v3/sipa_rm_res.c', '"driver CB returned error %d\\n"', 'pr_err'),
                (MODEM / 'sipa_v3/sipa_eth.c', '"called when %s is down\\n"', 'pr_err')]
        for path, fmt, level in keep:
            self.assertEqual(set(printk_calls(path.read_text(), fmt)), {level}, (path.name, fmt))


if __name__ == '__main__':
    unittest.main()
