#!/usr/bin/env python3
"""The list of modules the kernel must not load on demand (autoload-blocked), against a real module set
(make-bundle.sh; tests/test_all_modules.py with a sample).

    python3 check-autoload-blocked.py BUILD_OUT LIST

BUILD_OUT holds what build-modules.sh keeps of depmod's work on the kernel's own modules (modules.dep). Checked:
  * the list holds module names, each once;
  * every module that depends on a blocked one is blocked too (otherwise a request for it would load the blocked one
    as a dependency);
  * the modules the system loads on demand every day (nft, wireguard, overlay, usb-storage, vfat, tun, bridge, veth,
    br_netfilter, xt_*, vhost_net, l2tp...) are neither listed nor depend on a listed one.
The system's modprobe helper, which enforces the list, is tested on the root filesystem's side.
"""
import os
import pathlib
import re
import sys

MUST_PASS = ['nf_tables', 'nft_chain_nat', 'nft_ct', 'nft_masq', 'wireguard', 'overlay', 'usb_storage', 'vfat', 'tun',
             'bridge', 'veth', 'br_netfilter', 'xt_nat', 'xt_conntrack', 'xt_MASQUERADE', 'xt_addrtype',
             'vhost_net', 'l2tp_core', 'l2tp_ppp', 'l2tp_ip', 'macvtap', 'kvm', 'can_dev',
             'gs_usb', 'hfsplus', 'ntfs3', 'exfat', 'btrfs', 'fuse', 'iptable_nat', 'ip6_tables', 'xfrm_user', 'esp4',
             'sch_cake', 'nls_cp437', 'cdc_mbim', 'qmi_wwan', 'btusb', 'snd_aloop', 'dm_crypt', 'loop', 'zram']


def norm(s):
    return s.replace('-', '_')


def names(listfile):
    """The module names of LIST, in order (comments and blank lines dropped)."""
    return [n for n in (l.split('#')[0].strip() for l in pathlib.Path(listfile).read_text().splitlines()) if n]


def load_dep(out):
    dep = {}
    for line in (pathlib.Path(out) / 'modules.dep').read_text().splitlines():
        k, v = line.split(':', 1)
        name = lambda p: norm(os.path.basename(p).split('.ko')[0])
        dep[name(k)] = {name(x) for x in v.split()}
    return dep


def check(out, listfile):
    listed = names(listfile)
    blocked = set(listed)
    errors = ['listed twice: ' + n for n in sorted({n for n in listed if listed.count(n) > 1})]
    errors += ['not a module name: %r' % n for n in listed if not re.fullmatch(r'[a-z0-9_]+', n)]
    dep = load_dep(out)
    for m, ds in sorted(dep.items()):
        if m not in blocked and ds & blocked:
            errors.append('%s depends on %s but is not in the list' % (m, ' '.join(sorted(ds & blocked))))
    for m in MUST_PASS:
        if m in blocked:
            errors.append('%s is loaded on demand every day and must not be listed' % m)
        elif dep.get(m, set()) & blocked:
            errors.append('%s is loaded on demand every day and depends on %s' % (m, ' '.join(sorted(dep[m] & blocked))))
    if errors:
        sys.exit('check-autoload-blocked: ' + '\n  '.join(errors[:40]))
    print('check-autoload-blocked: %d listed, %d of them built; the dependants of every one are listed too' %
          (len(blocked), len(blocked & set(dep))))


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit('usage: check-autoload-blocked.py BUILD_OUT LIST')
    check(*sys.argv[1:3])
