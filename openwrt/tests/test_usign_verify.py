"""openwrt/tools/usign-verify.py and openwrt/build-rootfs.sh's use of it: OpenWrt's sha256sums counts only with a valid
usign signature by the release series' pinned key, before any hash is taken from it.

The real 25.12.5 sha256sums of armsr/armv8 and its signature (fixtures/openwrt-25.12.5) verify against the pinned key;
a changed byte in either does not. build-rootfs.sh itself runs in a scratch tree with curl and docker stubbed and a
throwaway key in the place of the pinned one, so that the refusals can be seen to stop the build before anything is
built or imported."""
import base64
import hashlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import TOP

TOOL = TOP / 'openwrt' / 'tools' / 'usign-verify.py'
KEYS = TOP / 'openwrt' / 'keys' / 'usign'
FIX = TOP / 'openwrt' / 'tests' / 'fixtures' / 'openwrt-25.12.5'
OPENWRT_KEY = 'b5043e70f9a75cde'      # OpenWrt's 25.12 release builds

spec = importlib.util.spec_from_file_location('usign_verify', TOOL)
uv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uv)


def compress(pt):
    zi = pow(pt[2], uv.P - 2, uv.P)
    x, y = pt[0] * zi % uv.P, pt[1] * zi % uv.P
    return (y | ((x & 1) << 255)).to_bytes(32, 'little')


def keypair(seed):
    """(secret scalar, prefix, public key) of RFC 8032 5.1.5."""
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], 'little')
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:], compress(uv._mul(a, uv._G))


def sign(seed, msg):
    a, prefix, pub = keypair(seed)
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), 'little') % uv.Q
    R = compress(uv._mul(r, uv._G))
    k = int.from_bytes(hashlib.sha512(R + pub + msg).digest(), 'little') % uv.Q
    return R + ((r + k * a) % uv.Q).to_bytes(32, 'little')


def usign_file(path, blob, comment):
    with open(path, 'w') as f:
        f.write('untrusted comment: %s\n%s\n' % (comment, base64.b64encode(blob).decode()))


def run_tool(*args):
    return subprocess.run(['python3', '-I', str(TOOL)] + [str(a) for a in args], capture_output=True, text=True)


class Ed25519(unittest.TestCase):
    # RFC 8032 7.1, TEST 1 and TEST 2
    VECTORS = [
        ('d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a', '',
         'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b'),
        ('3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c', '72',
         '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00'),
    ]

    def test_rfc8032_vectors(self):
        for pub, msg, sig in self.VECTORS:
            pub, msg, sig = bytes.fromhex(pub), bytes.fromhex(msg), bytes.fromhex(sig)
            self.assertTrue(uv.ed25519_verify(pub, msg, sig))
            self.assertFalse(uv.ed25519_verify(pub, msg + b'x', sig))
            self.assertFalse(uv.ed25519_verify(pub, msg, sig[:-1] + bytes([sig[-1] ^ 1])))

    def test_signer_here_matches_the_rfc(self):
        seed = bytes.fromhex('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60')
        self.assertEqual(keypair(seed)[2].hex(), self.VECTORS[0][0])
        self.assertEqual(sign(seed, b'').hex(), self.VECTORS[0][2])

    def test_s_not_below_the_group_order_is_refused(self):
        pub, _, sig = (bytes.fromhex(v) for v in self.VECTORS[0])
        s = int.from_bytes(sig[32:], 'little') + uv.Q
        self.assertFalse(uv.ed25519_verify(pub, b'', sig[:32] + s.to_bytes(32, 'little')))


class PinnedKeys(unittest.TestCase):
    def test_each_key_file_is_named_by_its_number(self):
        names = sorted(p.name for p in KEYS.iterdir())
        self.assertEqual(names, [OPENWRT_KEY])
        for name in names:
            raw = uv.usign_blob(KEYS / name, 42)
            self.assertEqual(raw[2:10].hex(), name)

    def test_build_rootfs_pins_it_for_the_series(self):
        t = (TOP / 'openwrt' / 'build-rootfs.sh').read_text()
        self.assertIn('    25.12.*) USIGN_KEY=%s ;;' % OPENWRT_KEY, t)
        # the base is checked before the SAE hostapd build and before docker sees it
        check = t.index('python3 "$TOP/openwrt/tools/usign-verify.py"')
        self.assertLess(check, t.index('sh "$HOSTAPD_BUILD"'))
        self.assertLess(check, t.index('docker import'))


class RealRelease(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_openwrt_25_12_5_sha256sums_verifies(self):
        r = run_tool(KEYS / OPENWRT_KEY, FIX / 'sha256sums.sig', FIX / 'sha256sums')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, 'valid'), r.stderr)

    def test_a_changed_hash_does_not(self):
        bad = os.path.join(self.tmp, 'sha256sums')
        data = (FIX / 'sha256sums').read_bytes()
        line = next(l for l in data.split(b'\n') if l.endswith(b' *openwrt-25.12.5-armsr-armv8-rootfs.tar.gz'))
        with open(bad, 'wb') as f:
            f.write(data.replace(line, b'0' * 64 + line[64:]))
        r = run_tool(KEYS / OPENWRT_KEY, FIX / 'sha256sums.sig', bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn('does not verify', r.stderr)

    def test_a_changed_signature_does_not(self):
        lines = (FIX / 'sha256sums.sig').read_text().splitlines()
        raw = bytearray(base64.b64decode(lines[1]))
        raw[-1] ^= 0x40
        bad = os.path.join(self.tmp, 'sig')
        usign_file(bad, bytes(raw), 'changed')
        r = run_tool(KEYS / OPENWRT_KEY, bad, FIX / 'sha256sums')
        self.assertEqual(r.returncode, 1)

    def test_another_key_is_named_and_refused(self):
        other = os.path.join(self.tmp, 'other')
        usign_file(other, b'Ed' + bytes.fromhex('0123456789abcdef') + keypair(b'\x33' * 32)[2], 'another key')
        r = run_tool(other, FIX / 'sha256sums.sig', FIX / 'sha256sums')
        self.assertEqual(r.returncode, 1)
        self.assertIn('signed by key %s, not by 0123456789abcdef' % OPENWRT_KEY, r.stderr)

    def test_not_a_signature_file(self):
        junk = os.path.join(self.tmp, 'sig')
        with open(junk, 'w') as f:
            f.write('<html>404</html>\n')
        r = run_tool(KEYS / OPENWRT_KEY, junk, FIX / 'sha256sums')
        self.assertEqual(r.returncode, 1)
        self.assertIn('not a usign file', r.stderr)


VER = '25.12.5'
TARBALL = 'openwrt-%s-armsr-armv8-rootfs.tar.gz' % VER


class BuildRootfs(unittest.TestCase):
    """build-rootfs.sh in a scratch tree, up to the step after the base's check (the theme, named here as missing):
    the pinned key's file holds a throwaway key with the pinned number, curl serves files from a directory, docker and
    the SAE hostapd build say they were reached and stop the build."""

    SEED = b'\x11' * 32

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        top = self.top = os.path.join(self.tmp, 'top')
        for d in ('openwrt/overlay/lib/netifd/proto', 'openwrt/luci-overlay/lib/netifd/proto',
                  'openwrt/overlay/usr/share/ucode/mu300', 'openwrt/patches', 'openwrt/hostapd',
                  'openwrt/keys/usign', 'openwrt/tools'):
            os.makedirs(os.path.join(top, d))
        shutil.copy(TOP / 'openwrt' / 'build-rootfs.sh', os.path.join(top, 'openwrt'))
        shutil.copy(TOOL, os.path.join(top, 'openwrt', 'tools'))
        for f in ('overlay/lib/netifd/proto/mu300cell.sh', 'luci-overlay/lib/netifd/proto/mu300cell-v6.sh',
                  'overlay/usr/share/ucode/mu300/cellparse.uc', 'patches/fw4-sipa-offload.patch',
                  'patches/wifi-scripts-no-dfs.patch'):
            with open(os.path.join(top, 'openwrt', f), 'w') as fh:
                fh.write('x\n')
        with open(os.path.join(top, 'openwrt', 'hostapd', 'build-hostapd.sh'), 'w') as fh:
            fh.write('echo HOSTAPD-REACHED >&2\nexit 98\n')
        self.tarball = b'not really a rootfs\n'
        with open(os.path.join(top, 'openwrt', TARBALL), 'wb') as fh:
            fh.write(self.tarball)
        usign_file(os.path.join(top, 'openwrt', 'keys', 'usign', OPENWRT_KEY),
                   b'Ed' + bytes.fromhex(OPENWRT_KEY) + keypair(self.SEED)[2], 'test key')
        self.srv = os.path.join(self.tmp, 'srv')
        os.makedirs(self.srv)
        self.bin = os.path.join(self.tmp, 'bin')
        os.makedirs(self.bin)
        self.stub('curl', '#!/bin/sh\n# the last argument is the URL; -o FILE\nout=\nwhile [ $# -gt 1 ]; do\n'
                  '  case $1 in -o) out=$2; shift ;; esac; shift\ndone\nf=%s/${1##*/}\n'
                  '[ -f "$f" ] || exit 22\nif [ -n "$out" ]; then cp "$f" "$out"; else cat "$f"; fi\n' % self.srv)
        self.stub('docker', '#!/bin/sh\necho DOCKER-REACHED "$@" >&2\nexit 99\n')

    def stub(self, name, body):
        p = os.path.join(self.bin, name)
        with open(p, 'w') as f:
            f.write(body)
        os.chmod(p, 0o755)

    def publish(self, sums, number=OPENWRT_KEY, seed=None, signed=None):
        with open(os.path.join(self.srv, 'sha256sums'), 'wb') as f:
            f.write(sums)
        sig = sign(seed or self.SEED, sums if signed is None else signed)
        usign_file(os.path.join(self.srv, 'sha256sums.sig'), b'Ed' + bytes.fromhex(number) + sig, 'signed')

    def sums(self, tarball=None):
        h = hashlib.sha256(self.tarball if tarball is None else tarball).hexdigest()
        return ('%s *openwrt-%s-armsr-armv8-generic-ext4-combined.img.gz\n%s *%s\n'
                % ('a' * 64, VER, h, TARBALL)).encode()

    def build(self, **env):
        e = dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'], MU300_WRT_VER=VER,
                 MU300_LUCI_THEME_APK=os.path.join(self.tmp, 'no-theme.apk'))
        e.update(env)
        e.pop('MU300_DOCKER', None)
        e.pop('MU300_INPUTS', None)
        return subprocess.run(['sh', os.path.join(self.top, 'openwrt', 'build-rootfs.sh'), 'out.tar.gz'],
                              capture_output=True, text=True, env=e, timeout=120)

    def refused(self, r, why):
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(why, r.stderr)
        for reached in ('theme package missing', 'HOSTAPD-REACHED', 'DOCKER-REACHED'):
            self.assertNotIn(reached, r.stderr)

    def test_a_signed_hash_that_matches_goes_on(self):
        self.publish(self.sums())
        r = self.build()
        self.assertIn('theme package missing', r.stderr)
        self.assertNotIn('no valid signature', r.stderr)

    def test_sums_changed_after_signing_stop_the_build(self):
        # what someone who can change the download would do: a tarball of theirs, and its hash in sha256sums
        self.publish(self.sums(b'theirs\n'), signed=self.sums())
        self.refused(self.build(), 'no valid signature')

    def test_a_signature_by_another_key_stops_the_build(self):
        self.publish(self.sums(), number='0123456789abcdef', seed=b'\x22' * 32)
        self.refused(self.build(), 'no valid signature')

    def test_the_right_number_with_another_key_stops_the_build(self):
        self.publish(self.sums(), seed=b'\x22' * 32)
        self.refused(self.build(), 'no valid signature')

    def test_no_signature_stops_the_build(self):
        self.publish(self.sums())
        os.remove(os.path.join(self.srv, 'sha256sums.sig'))
        self.refused(self.build(), 'cannot download')

    def test_a_tarball_missing_from_the_signed_list_stops(self):
        self.publish(b'%s *something-else.tar.gz\n' % (b'b' * 64))
        self.refused(self.build(), 'not in the signed sha256sums')

    def test_a_signed_hash_of_another_tarball_stops(self):
        self.publish(self.sums(b'another\n'))
        self.refused(self.build(), 'checksum mismatch')

    def test_a_release_series_without_a_pinned_key_stops(self):
        self.publish(self.sums())
        self.refused(self.build(MU300_WRT_VER='24.10.4'), 'no pinned usign key for OpenWrt 24.10.4')


if __name__ == '__main__':
    unittest.main()
