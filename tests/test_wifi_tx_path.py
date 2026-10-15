"""The SC2355 Wi-Fi TX path of the mainline kernels (upstream/modules/sprd_wlan_combo, wcn_bsp): four defects of the
vendor's code that kanoqwq found and measured on an F50 (clean-tf-7.2 149c9d2a), fixed here in our own words
(FINDINGS 103). These read the source: the modules are built by the release (and were built for 6.18.56 and 7.2.10
without a new warning), and the behaviour needs a device (the checklist in the section)."""
import unittest

from helpers import TOP

WLAN = TOP / 'upstream' / 'modules' / 'sprd_wlan_combo' / 'sc2355'
WCN = TOP / 'upstream' / 'modules' / 'wcn_bsp'


def body(src, sig):
    b = src[src.index(sig):]
    return b[:b.index('\n}\n')]


class WifiTxPath(unittest.TestCase):
    def test_the_descriptor_fill_keeps_the_caller_s_skb(self):
        # skb_realloc_headroom() + kfree_skb() replaced only the local pointer: every caller went on with a freed skb
        for f, fn in (('pcie.c', 'int sc2355_pcie_hif_fill_msdu_dscr('), ('sdio.c', 'int sc2355_hif_fill_msdu_dscr(')):
            with self.subTest(file=f):
                b = body((WLAN / f).read_text(), fn)
                self.assertNotIn('skb_realloc_headroom(skb', b)
                self.assertNotIn('kfree_skb(', b)
                self.assertIn('skb_header_cloned(skb)', b)
                self.assertIn('if (skb_cow_head(skb, DSCR_LEN + offset + hif->hif_offset + MSG_PTR_LEN + dscr_rsvd)) {', b)
                # the Ethernet header is read again after the data may have moved, before anything uses it
                self.assertLess(b.index('skb_cow_head'), b.index('ethhdr = (struct ethhdr *)skb->data;\t/*'))
                self.assertLess(b.index('ethhdr = (struct ethhdr *)skb->data;\t/*'), b.index('memcpy(hif->skb_da'))

    def test_the_group_tid_is_set_in_the_descriptor_not_the_pcie_prefix(self):
        b = body((WLAN / 'cmdevt.c').read_text(), 'int sprd_xmit_data2cmd_wq(')
        pick = b.index('dscr = (struct tx_msdu_dscr *)(skb->data +\n\t\t(hif->hw_type == SPRD_HW_SC2355_PCIE ? MSDU_DSCR_RSVD : 0));')
        self.assertLess(pick, b.index('skb_push(skb,FLAG_SIZE);'))
        self.assertLess(pick, b.index('dscr->buffer_info.msdu_tid = prio_1;'))
        self.assertEqual(b.count('dscr = '), 1)
        self.assertNotIn('pr_info(', b)

    def test_ap_ipv6_multicast_takes_the_data_queue_on_pcie(self):
        tx = (WLAN / 'tx.c').read_text()
        b = body(tx, 'static int tx_mc_pkt(')
        # before anything sends it by command; SDIO keeps the vendor's path
        self.assertLess(b.index('if (hif->hw_type == SPRD_HW_SC2355_PCIE)\n\t\treturn 1;'), b.index('sprd_xmit_data2cmd_wq('))
        # 1 means "not handled": the caller goes on to tx_filter_ip_pkt, which still sends DHCPv6/DNS/VoWiFi by command
        f = body(tx, 'int sprd_tx_filter_packet(')
        self.assertLess(f.index('!tx_mc_pkt(skb, ndev)'), f.index('return tx_filter_ip_pkt(skb, ndev);'))
        ip = body(tx, 'static int tx_filter_ip_pkt(')
        self.assertIn('is_data2cmd = (is_ipv4_dhcp || is_ipv6_dhcp || is_vowifi2cmd ||', ip)

    def test_a_full_edma_pending_ring_is_backpressure(self):
        edma = body((WCN / 'pcie' / 'edma_engine.c').read_text(), 'static int edma_pending_q_buffer(')
        self.assertIn('return -EAGAIN;', edma)
        self.assertNotIn('return ERROR;', edma)
        pcie = (WLAN / 'pcie.c').read_text()
        # both TX data channels get the completion callback that wakes the TX thread
        self.assertEqual(pcie.count('NULL, sc2355_pcie_tx_data_complete, NULL),'), 2)
        done = body(pcie, 'static int sc2355_pcie_tx_data_complete(')
        self.assertIn('atomic_xchg(&tx_mgmt->pcie_tx_waiting, 0)', done)
        self.assertIn('sc2355_tx_up(tx_mgmt);', done)
        push = body(pcie, 'static int sc2355_pcie_tx_data_push(')
        # armed before the submission (the completion can come first), disarmed unless it was refused
        self.assertLess(push.index('atomic_set(&tx_mgmt->pcie_tx_waiting, 1);'), push.index('sc2355_pcie_push_link('))
        self.assertIn('if (ret != -EAGAIN)\n\t\tatomic_set(&tx_mgmt->pcie_tx_waiting, 0);', push)
        lst = body(pcie, 'int sc2355_pcie_hif_tx_list(')
        self.assertIn('ret = sc2355_pcie_tx_data_push(hif,', lst)
        self.assertIn('if (ret != -EAGAIN)\t/* MU300: a full pending ring is not a failure */\n\t\t\t\t\thif->pushfail_count++;', lst)
        tx = (WLAN / 'tx.c').read_text()
        h = body(tx, 'static int tx_handle_to_send_list(')
        # a refusal returns before the failure count (more than 50 flushed the queue)
        self.assertLess(h.index('if (ret == -EAGAIN)\n\t\t\t\treturn ret;'), h.index('failcount++'))
        self.assertIn('atomic_set(&tx_mgmt->pcie_tx_waiting, 0);', body(tx, 'int sc2355_tx_init('))
        self.assertIn('\tatomic_t pcie_tx_waiting;\n', (WLAN / 'tx.h').read_text())


if __name__ == '__main__':
    unittest.main()
