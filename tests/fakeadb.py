"""Not a test: an `adb` for the tests of the installers' answers mode and of the graphical installer. It answers
what install.sh, install.ps1 and the uninstallers send to a rooted F50 or U30 Air in Android - from a description
of the device in a JSON file - and writes nothing anywhere but its own log, so a run of an installer against it
shows every question it asks and every command it would have sent.

    FAKEADB=<state.json> python fakeadb.py devices -l
    python fakeadb.py --new <state.json> [key=value ...]     write a state file (see DEFAULT for the keys)
    python fakeadb.py --stub <dir> <state.json>              put an `adb` (and adb.cmd) there that runs this file

Every call is appended to <state.json>.log as one JSON line: {"argv": [...], "kind": ..., "cmd": ..., "answers":
[the MU300_ANS_* variables in this adb's environment]}. kind is
"read" for what only reads, "write" for a device command that would have written (dd of=, rm, a redirection into a
file, sh of a pushed script), "push", "pull", "reboot", and "unknown" for a command this file has no answer for -
which a test should treat as a mistake.
"""
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fakegpt  # noqa: E402

DEFAULT = {
    'serial': '324950664950',
    'model': 'F50',              # ro.product.model; 'U30_Air' for the other device, anything else is unknown
    'device': 'MU300',           # ro.product.device
    'platform': 'ums9620',       # ro.board.platform (the V50 is told apart by it too)
    'adb_state': 'device',       # device | unauthorized | offline | none (nothing attached)
    'extra_devices': [],         # more lines of `adb devices -l`
    'root': True,                # su works
    'su_version': '27.0:MAGISKSU',
    'magisk': '27.0:MAGISK',
    'build': 'MU300_ZYV1.0.0B09',
    'locked': '0',               # ro.boot.flash.locked
    'verified': 'orange',        # ro.boot.verifiedbootstate
    'device_state': 'unlocked',  # ro.boot.vbmeta.device_state
    'boot_completed': '1',
    'slot': 'a',                 # the slot Android runs from
    'layout': 'factory64',       # tests/fakegpt.py: factory64 (the gap), vendor64 (userdata fills the disk), ...
    'existing': False,           # a mu300root filesystem in the free region
    'existing_blocks': 8388608,  # its size in 4096-byte blocks
    'dirty': 0,                  # of 16 samples of the free region with data
    'sd': None,                  # None, or {"dev": "mmcblk1", "sectors": 124735488, "partition": true, "type": "SD",
                                 #           "state": "no" | "yes" | "foreign"}
    'bootchain': 'BOOTCHAIN-PLAN a b',
    'firmware': True,            # the Wi-Fi/Bluetooth firmware files are there
    'vpn_on': '',                # what the VPN probe of an existing installation prints
    'boot_image_bytes': 4096,    # size of the fake boot image a pull gives
    'linux_json': '',            # what `mu300-device --json` says on a device whose serial is MU300LINUX-...
    'pull': True,                # adb pull works; false: every pull fails, as with a cable that drops out;
                                 # "gpt": only the two pieces of the partition table can be pulled
}
T = '/data/local/tmp'


def state_path():
    p = os.environ.get('FAKEADB')
    if not p:
        sys.stderr.write('fakeadb: FAKEADB is not set\n')
        sys.exit(1)
    return p


def load():
    with open(state_path(), encoding='utf-8') as f:
        st = dict(DEFAULT, **json.load(f))
    return st


def log(kind, argv, cmd=''):
    with open(state_path() + '.log', 'a', encoding='utf-8') as f:
        # 'answers': the answer variables this adb was started with - a secret must never be among them
        f.write(json.dumps({'kind': kind, 'argv': argv, 'cmd': cmd,
                            'answers': sorted(k for k in os.environ if k.upper().startswith('MU300_ANS_'))}) + '\n')


def boot_image(st, slot):
    """The fake boot image of a slot: the same bytes every time, so its hash can be compared."""
    seed = ('boot_%s of %s' % (slot, st['serial'])).encode()
    out = b''
    while len(out) < st['boot_image_bytes']:
        seed = hashlib.sha256(seed).digest()
        out += seed
    return out[:st['boot_image_bytes']]


def misc_head():
    """The first block of misc with a valid bootloader_control block at 0x800 (slot a active)."""
    import struct
    import zlib
    bc = bytearray(32)
    bc[0:4] = b'_a\0\0'
    bc[4:8] = b'BCAB'
    bc[8] = 1
    bc[12] = 0x9f
    bc[14] = 0x1e
    bc[28:32] = struct.pack('<I', zlib.crc32(bytes(bc[:28])))
    return bytes(2048) + bytes(bc) + bytes(4096 - 2048 - 32)


def geometry(st):
    disk, usable_last, parts = fakegpt.layout(st['layout'])
    last_end = max(last for _, _, last in parts) + 1
    start = (last_end // 4096 + 1) * 4096
    end = ((disk - 34) // 4096 - 1) * 4096
    if end <= start:
        end = start
    return disk, parts, last_end, start * 512, (end - start) * 512


def props(st):
    return {
        'ro.product.model': st['model'], 'ro.product.device': st['device'], 'ro.build.display.id': st['build'],
        'ro.boot.flash.locked': st['locked'], 'ro.boot.verifiedbootstate': st['verified'],
        'ro.boot.vbmeta.device_state': st['device_state'],
        'ro.boot.slot_suffix': '_' + st['slot'], 'sys.boot_completed': st['boot_completed'],
        'ro.serialno': st['serial'], 'ro.board.platform': st.get('platform', 'ums9620'),
    }


def prop(st, name):
    return props(st).get(name, '')


def pending(st, content=None):
    """What the next pull of mu300-pull.bin gives: kept in a file next to the state between two calls."""
    p = state_path() + '.pull'
    if content is None:
        try:
            with open(p, 'rb') as f:
                return f.read()
        except OSError:
            return b''
    with open(p, 'wb') as f:
        f.write(content)
    return content


def pending_is_gpt(flag=None):
    """Whether what waits in mu300-pull.bin is a piece of the partition table (pull: "gpt" lets only those through)."""
    p = state_path() + '.pull-gpt'
    if flag is None:
        return os.path.exists(p)
    if flag:
        open(p, 'w').close()
    elif os.path.exists(p):
        os.remove(p)
    return flag


def su(st, cmd, argv):
    """The answer of the device to `su -c 'cmd'`; (text, kind)."""
    disk, parts, last_end, off, size = geometry(st)
    sd = st['sd']
    if cmd == 'id -u':
        return '0', 'read'
    m = re.fullmatch(r'getprop (\S+)', cmd)
    if m:
        return prop(st, m.group(1)), 'read'
    m = re.fullmatch(r'readlink -f /dev/block/by-name/(\S+)', cmd)
    if m:
        names = [n for n, _, _ in parts] + ['boot_a', 'boot_b']
        return '/dev/block/mmcblk0p%d' % (names.index(m.group(1)) + 1 if m.group(1) in names else 99), 'read'
    if cmd.startswith('e=0; for p in /sys/block/mmcblk0/mmcblk0p*'):
        return '%d %d' % (last_end, disk), 'read'
    if cmd.startswith('for p in /sys/block/mmcblk0/mmcblk0p*; do echo $(sed -n s/^PARTNAME=//p'):
        return '\n'.join('%s:%d:%d' % (n, first, last - first + 1) for n, first, last in parts), 'read'
    if cmd.startswith('for b in /sys/block/mmcblk[1-9]'):
        if not sd:
            return '', 'read'
        dev = '/dev/block/' + sd['dev'] + ('p1' if sd.get('partition', True) else '')
        return '%s %d %s' % (dev, sd['sectors'], sd.get('type', 'SD')), 'read'
    m = re.fullmatch(r'dd if=(\S+) bs=1 skip=(\d+) count=(\d+) 2>/dev/null( \| od -An -t(x1|u4))?', cmd)
    if m:
        dev, skip, od = m.group(1), int(m.group(2)), m.group(5)
        on_sd = 'mmcblk0' not in dev
        base = 0 if on_sd else off
        there = (sd and sd.get('state', 'no') in ('yes', 'foreign')) if on_sd else st['existing']
        label = ('mu300sd' if sd and sd.get('state') == 'yes' else 'data') if on_sd else 'mu300root'
        if (skip + 2048) // 512 > disk and not on_sd:
            log('unknown', argv, 'READ PAST THE END OF THE eMMC: ' + cmd)
            return '', 'read'
        if not there or skip - base not in (1080, 1144, 1028):
            return '', 'read'
        if skip - base == 1080:
            return ' 53 ef', 'read'
        if skip - base == 1144:
            return label, 'read'
        return ' %d' % st['existing_blocks'], 'read'
    if cmd == 'dd if=/dev/block/by-name/misc bs=1 skip=2048 count=32 2>/dev/null | od -An -tx1 -v':
        return ' ' + ' '.join('%02x' % b for b in misc_head()[2048:2080]), 'read'
    if cmd.startswith('n=0; for s in '):
        return str(st['dirty']), 'read'
    if re.fullmatch(r'dd if=/dev/block/mmcblk0 bs=1048576 skip=\d+ count=1 2>/dev/null \| od -An -tx1 -N32', cmd):
        return ' de ad be ef' + ' 00' * 28, 'read'
    if cmd == 'sh %s/android-bootchain.sh check' % T:
        return st['bootchain'], 'read'
    m = re.fullmatch(r'sha256sum /dev/block/by-name/boot_([ab])', cmd)
    if m:
        return hashlib.sha256(boot_image(st, m.group(1))).hexdigest() + '  /dev/block/by-name/boot_' + m.group(1), 'read'
    if cmd == 'magisk -v':
        return st['magisk'], 'read'
    m = re.fullmatch(r'\[ -f (\S+) \] && echo y', cmd)
    if m:
        return ('y' if st['firmware'] and m.group(1).startswith('/odm/firmware/') else ''), 'read'
    m = re.fullmatch(r'\[ -d (\S+) \] && echo y', cmd)
    if m:
        return '', 'read'
    if 'android-mount-mu300root.sh' in cmd and 'MU300_RO=1' in cmd:
        return st['vpn_on'], 'read'
    # what a pull of the file written on the device will give
    m = re.fullmatch(r'cat (\S+) > %s/mu300-pull\.bin' % T, cmd)
    if m:
        pending_is_gpt(False)
        src = m.group(1)
        b = re.fullmatch(r'/dev/block/by-name/boot_([ab])', src)
        pending(st, boot_image(st, b.group(1)) if b else b'fake ' + src.encode() + b'\n')
        return '', 'read'
    if cmd == 'dd if=/dev/block/by-name/misc bs=4096 count=1 2>/dev/null > %s/mu300-pull.bin' % T:
        pending_is_gpt(False)
        pending(st, misc_head())
        return '', 'read'
    m = re.fullmatch(r'dd if=/dev/block/mmcblk0 bs=512 (?:skip=(\d+) )?count=(34|33) 2>/dev/null > %s/(gpt\.head|gpt\.tail|mu300-pull\.bin)' % T, cmd)
    if m:
        head, tail = fakegpt.make(*fakegpt.layout(st['layout']))
        data = head if m.group(2) == '34' else tail
        with open(state_path() + '.' + m.group(3), 'wb') as f:
            f.write(data)
        if m.group(3) == 'mu300-pull.bin':     # install.ps1 reads the table through the same file as everything else
            pending(st, data)
            pending_is_gpt(True)
        return '', 'read'
    if re.fullmatch(r'rm -f( %s/\S+)+' % T, cmd) or re.fullmatch(r'rm -rf %s/mu300-bootchain-backup' % T, cmd):
        return '', 'read'       # the installer's own leftovers in /data/local/tmp
    if re.search(r'\bof=/dev/|\bdd\b.*\bof=|>\s*/|\brm\b|\bsh \S+android-install\.sh|\bmkdir\b|\bcp\b|\btouch\b|blkdiscard', cmd):
        return '', 'write'
    return '', 'unknown'


def devices(st):
    lines = []
    if st['adb_state'] != 'none':
        tail = (' usb:1 product:%s model:%s device:%s transport_id:1' % (st['device'], st['model'], st['device'])
                if st['adb_state'] == 'device' else ' usb:1 transport_id:1')
        lines.append('%-22s %s%s' % (st['serial'], st['adb_state'], tail))
    return 'List of devices attached\n' + ''.join(l + '\n' for l in lines + list(st['extra_devices'])) + '\n'


def main(argv):
    if argv[:1] == ['--new']:
        st = {}
        for kv in argv[2:]:
            k, v = kv.split('=', 1)
            try:
                st[k] = json.loads(v)
            except ValueError:
                st[k] = v
        unknown = [k for k in st if k not in DEFAULT]
        if unknown:
            sys.exit('fakeadb: no such key: %s' % ', '.join(unknown))
        with open(argv[1], 'w', encoding='utf-8') as f:
            json.dump(st, f)
        return 0
    if argv[:1] == ['--stub']:
        d, state = argv[1], os.path.abspath(argv[2])
        os.makedirs(d, exist_ok=True)
        me = os.path.abspath(__file__)
        sh = os.path.join(d, 'adb')
        with open(sh, 'w', encoding='utf-8', newline='\n') as f:
            f.write('#!/bin/sh\nFAKEADB="${FAKEADB:-%s}" exec "%s" "%s" "$@"\n' % (state, sys.executable, me))
        os.chmod(sh, 0o755)
        with open(os.path.join(d, 'adb.cmd'), 'w', encoding='utf-8', newline='\r\n') as f:
            f.write('@echo off\nif "%%FAKEADB%%"=="" set "FAKEADB=%s"\n"%s" "%s" %%*\n' % (state, sys.executable, me))
        return 0

    st = load()
    # -s SERIAL in front of the command, as adb takes it
    while argv[:1] == ['-s'] and len(argv) >= 2:
        argv = argv[2:]
    if not argv:
        return 1
    cmd, rest = argv[0], argv[1:]
    out = sys.stdout
    if cmd in ('start-server', 'kill-server', 'wait-for-device'):
        log('read', argv)
        return 0
    if cmd == 'devices':
        log('read', argv)
        out.write(devices(st))
        return 0
    serial = os.environ.get('ANDROID_SERIAL')
    usable = st['adb_state'] == 'device' and (not serial or serial == st['serial'])
    if cmd == 'get-state':
        log('read', argv)
        if not usable:
            sys.stderr.write('error: no devices/emulators found\n')
            return 1
        out.write('device\n')
        return 0
    if not usable:
        log('read', argv)
        sys.stderr.write('adb: no devices/emulators found\n' if st['adb_state'] == 'none' else
                         'adb: device %s\n' % st['adb_state'])
        return 1
    if cmd == 'shell':
        line = ' '.join(rest)
        m = re.fullmatch(r"su -c '(.*)'", line, re.S)
        if m:
            if not st['root']:
                log('read', argv, m.group(1))
                sys.stderr.write('/system/bin/sh: su: inaccessible or not found\n')
                return 127
            text, kind = su(st, m.group(1), argv)
            log(kind, argv, m.group(1))
            if text:
                out.write(text + '\n')
            return 0
        m = re.fullmatch(r'getprop (\S+)', line)
        if m:
            log('read', argv, line)
            out.write(prop(st, m.group(1)) + '\n')
            return 0
        if line == 'ls -d /opt/mu300/bin 2>/dev/null' or line.startswith('test -x /opt/mu300/bin/mu300-next-boot && echo mu300'):
            # how the installers and the window tell our Linux from Android
            log('read', argv, line)
            if st['serial'].upper().startswith('MU300LINUX'):
                out.write('/opt/mu300/bin\n' if line.startswith('ls') else 'mu300\n')
            return 0
        if line == 'getprop':
            log('read', argv, line)
            out.write(''.join('[%s]: [%s]\n' % kv for kv in sorted(props(st).items())))
            return 0
        if line.startswith('/opt/mu300/bin/') and st['serial'].upper().startswith('MU300LINUX'):
            # a device in our Linux: its adb shell is root, and these are the project's own commands
            log('read' if ('mu300-device' in line or line.endswith('mu300-update check')) else 'write', argv, line)
            if 'mu300-device --json' in line:
                out.write(st.get('linux_json', '') + '\n')
            return 0
        if line == 'su -v':
            log('read', argv, line)
            if not st['root']:
                return 127
            out.write(st['su_version'] + '\n')
            return 0
        log('write' if re.search(r'\brm\b|\bmkdir\b', line) else 'unknown', argv, line)
        return 0
    if cmd == 'push':
        log('push', argv)
        return 0
    if cmd == 'pull':
        log('pull', argv)
        src, dst = rest[0], rest[1]
        name = src.rsplit('/', 1)[-1]
        if not st['pull'] or (st['pull'] == 'gpt' and name not in ('gpt.head', 'gpt.tail') and not pending_is_gpt()):
            sys.stderr.write('adb: error: failed to copy: remote object does not exist\n')
            return 1
        if name == 'mu300-pull.bin':
            data = pending(st)
        elif name in ('gpt.head', 'gpt.tail'):
            with open(state_path() + '.' + name, 'rb') as f:
                data = f.read()
        else:
            return 1
        with open(dst, 'wb') as f:
            f.write(data)
        return 0
    if cmd == 'reboot':
        log('reboot', argv)
        return 0
    log('unknown', argv)
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
