"""openwrt/overlay/lib/netifd/proto/mu300cell.sh: the netifd protocol handler, sourced with the netifd functions stubbed.

The script is sourced with INCLUDE_ONLY=1 (it then defines only the proto_mu300cell_* functions). The netifd
functions, `ip`, `mobile-data`, `mu300-led`, `sleep`, `logger` and `fw4` record their arguments in $STUBLOG/calls.
The script's absolute paths (/opt/mu300/bin, /tmp, /proc/sys) are pointed into the scratch directory in a copy.
"""
import re
import unittest
from pathlib import Path

from helpers import TOP, ShellTest

PROTO = TOP / 'openwrt' / 'overlay' / 'lib' / 'netifd' / 'proto' / 'mu300cell.sh'

HARNESS = r'''
rec() { printf '%s\n' "$*" >> "$STUBLOG/calls"; }
proto_init_update() { rec proto_init_update "$@"; }
proto_add_ipv4_address() { rec proto_add_ipv4_address "$@"; }
proto_add_ipv4_route() { rec proto_add_ipv4_route "$@"; }
proto_add_dns_server() { rec proto_add_dns_server "$@"; }
proto_send_update() { rec proto_send_update "$@"; }
proto_kill_command() { rec proto_kill_command "$@"; }
proto_notify_error() { rec proto_notify_error "$@"; }
proto_block_restart() { rec proto_block_restart "$@"; }
proto_setup_failed() { rec proto_setup_failed "$@"; }
proto_add_dynamic_defaults() { rec proto_add_dynamic_defaults; }
json_get_vars() { apn=$T_APN; pdptype=$T_PDPTYPE; peerdns=$T_PEERDNS; }
json_init() { rec json_init; }
json_add_string() { rec json_add_string "$@"; }
json_add_boolean() { rec json_add_boolean "$@"; }
json_close_object() { rec json_close_object; }
json_dump() { echo '{}'; }
ubus() { rec ubus "$@"; }
INCLUDE_ONLY=1
. "$SCRIPT"
'''

# What mobile-data's netifd path prints (MU300_NETIFD=1 mobile-data up): six KEY=VALUE lines, in this order.
UP_V4 = 'IFACE=sipa_eth0\\nIP=10.20.30.40\\nPREFIX=30\\nDNS1=1.1.1.1\\nDNS2=8.8.8.8\\nIID6=\\n'
UP_V6 = 'IFACE=sipa_eth0\\nIP=10.20.30.40\\nPREFIX=30\\nDNS1=1.1.1.1\\nDNS2=\\nIID6=0000:0000:1234:5678\\n'


class Mu300cell(ShellTest):
    def setUp(self):
        super().setUp()
        bindir = self.tmp / 'bin'
        bindir.mkdir()
        (self.tmp / 'proc').mkdir()
        text = PROTO.read_text()
        text = text.replace('/opt/mu300/bin', str(bindir)).replace('/tmp/mu300cell.err', str(self.tmp / 'err')) \
                   .replace('/proc/sys', str(self.tmp / 'proc'))
        self.script_copy = self.tmp / 'mu300cell.sh'
        self.script_copy.write_text(text)
        self.stub('sleep', 'echo "sleep $*" >> "$STUBLOG/calls"')
        self.stub('logger', 'echo "logger $*" >> "$STUBLOG/calls"')
        self.stub('fw4', 'exit 1')
        # ip: records; `-4 -o addr show` lists the global v4 addresses the carrier "left behind" ($T_LEFT).
        self.stub('ip', '''echo "ip $*" >> "$STUBLOG/calls"
case "$*" in
  "-4 -o addr show dev "*) for a in $T_LEFT; do echo "2: sipa_eth0    inet $a scope global sipa_eth0"; done ;;
esac
exit 0''')
        self.mobile = bindir / 'mobile-data'
        self.set_mobile('up', 0)
        for n, body in (('mu300-led', 'echo "mu300-led $*" >> "$STUBLOG/calls"'),):
            p = bindir / n
            p.write_text('#!/bin/sh\n' + body + '\n')
            p.chmod(0o755)

    def set_mobile(self, out, rc, err=''):
        self.mobile.write_text('#!/bin/sh\necho "mobile-data $* env:$MU300_NETIFD:$MU300_PDP_TYPE" >> "$STUBLOG/calls"\n'
                               f'[ "$1" = up ] || exit 0\nprintf \'{out}\'\necho \'{err}\' >&2\nexit {rc}\n')
        self.mobile.chmod(0o755)

    def run_proto(self, shell, func, out=UP_V4, rc=0, err='', ok=0, **env):
        self.set_mobile(out, rc, err)
        (self.tmp / 'calls').write_text('')
        e = dict(T_APN='internet', T_PDPTYPE='IP', T_PEERDNS='1', T_LEFT='', SCRIPT=self.script_copy)
        e.update(env)
        r = self.sh(shell, HARNESS + f'{func} wan\n', **e)
        self.assertEqual(r.returncode, ok, r.stderr)
        return (self.tmp / 'calls').read_text().splitlines()

    def test_setup_update_is_external(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup')
            self.assertIn('proto_init_update sipa_eth0 1 1', calls)
            self.assertIn('proto_add_ipv4_address 10.20.30.40 30', calls)
            self.assertIn('proto_send_update wan', calls)
            self.assertIn('mobile-data up internet env:1:IP', calls)

    def test_setup_installs_v4_address_and_route_itself(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup', T_LEFT='9.9.9.9/32 10.20.30.40/30 10.1.1.1/24')
            self.assertIn('ip -4 addr replace 10.20.30.40/30 dev sipa_eth0', calls)
            self.assertIn('ip -4 addr del 9.9.9.9/32 dev sipa_eth0', calls)
            self.assertIn('ip -4 addr del 10.1.1.1/24 dev sipa_eth0', calls)
            self.assertNotIn('ip -4 addr del 10.20.30.40/30 dev sipa_eth0', calls)
            self.assertIn('ip -4 route replace default dev sipa_eth0', calls)
            # installed before netifd is told about it
            self.assertLess(calls.index('ip -4 addr replace 10.20.30.40/30 dev sipa_eth0'),
                            calls.index('proto_send_update wan'))

    def test_setup_flushes_v6_first(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup')
            a = calls.index('ip -6 addr flush dev sipa_eth0 scope global')
            r = calls.index('ip -6 route flush dev sipa_eth0')
            self.assertLess(max(a, r), calls.index('proto_send_update wan'))

    def test_teardown(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_teardown')
            self.assertEqual(calls[0], 'proto_kill_command wan')
            self.assertEqual(calls[1].split(' env:')[0], 'mobile-data down')
            for c in ('ip -4 addr flush dev sipa_eth0 scope global', 'ip -6 addr flush dev sipa_eth0 scope global',
                      'ip -6 route flush dev sipa_eth0'):
                self.assertIn(c, calls[2:])

    def test_attach_failure_still_sleeps_20(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup', out='', rc=1, err='no carrier', ok=1)
            self.assertIn('proto_notify_error wan ATTACH_FAILED', calls)
            self.assertIn('sleep 20', calls)
            self.assertLess(calls.index('sleep 20'), calls.index('proto_setup_failed wan'))
            self.assertFalse(any(c.startswith('proto_send_update') for c in calls))

    def test_no_modem_blocks_restart(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup', out='', rc=3, err='no modem', ok=1)
            self.assertIn('proto_notify_error wan NO_MODEM', calls)
            self.assertIn('proto_block_restart wan', calls)
            self.assertNotIn('sleep 20', calls)

    def test_ipv6_bearer_still_adds_extendprefix_interface(self):
        for shell in self.each_shell():
            calls = self.run_proto(shell, 'proto_mu300cell_setup', out=UP_V6, T_PDPTYPE='IPV4V6')
            self.assertIn('ip -6 addr add fe80::0000:0000:1234:5678/64 dev sipa_eth0', calls)
            self.assertIn('json_add_string name wan_6', calls)
            self.assertIn('json_add_boolean extendprefix 1', calls)
            self.assertIn('proto_add_dynamic_defaults', calls)
            self.assertTrue(any(c.startswith('ubus call network add_dynamic') for c in calls))
            # the link-local is added after the flush
            self.assertLess(calls.index('ip -6 addr flush dev sipa_eth0 scope global'),
                            calls.index('ip -6 addr add fe80::0000:0000:1234:5678/64 dev sipa_eth0'))

    def test_no_relay_code_in_this_port(self):
        text = PROTO.read_text()
        self.assertNotIn('mu300cell-v6', text)
        self.assertNotIn('proto_mu300cell_renew', text)


if __name__ == '__main__':
    unittest.main()
