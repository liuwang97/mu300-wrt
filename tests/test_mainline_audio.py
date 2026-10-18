"""Call audio on the mainline kernels (FINDINGS 188): the Unisoc stack built out of tree (upstream/modules/sprd_audio),
the memory it needs reserved, nothing of it loaded by udev, the DMA restart that panicked 5.4 (FINDINGS 24e) gone,
and the userspace finding the drivers where a release puts them."""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TOP

UP = TOP / 'upstream'
MOD = UP / 'modules' / 'sprd_audio'
DSP = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-audio-dsp'
VOICE = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-voice'
PCM = MOD / 'sprd' / 'platform' / 'dmaengine_pcm' / 'sprd-dmaengine-pcm.c'


def c_function(src, name):
    """The body of a C function definition, by brace matching."""
    m = re.search(r'(?m)^[a-z].*\b%s\([^;{]*\)\s*\{' % re.escape(name), src)
    assert m, name
    i, depth = m.end() - 1, 0
    for j in range(i, len(src)):
        depth += {'{': 1, '}': -1}.get(src[j], 0)
        if depth == 0:
            return src[m.start():j + 1]
    raise AssertionError(name)


class Build(unittest.TestCase):
    def test_every_source_is_built_and_every_object_exists(self):
        kbuild = (MOD / 'Kbuild').read_text()
        objs = set(re.findall(r'([\w/.-]+)\.o\b', kbuild.split('obj-m', 1)[1]))
        sources = {str(p.relative_to(MOD))[:-2] for p in MOD.rglob('*.c')}
        # virt-dma.c is copied in from the kernel being built against (build-modules.sh)
        self.assertEqual(sources - objs, set())
        composite = {o for o in objs if '/' in o}
        self.assertEqual(composite - sources - {'dma/virt-dma'}, set())

    def test_the_modules_are_the_ones_mu300_audio_dsp_loads(self):
        kbuild = (MOD / 'Kbuild').read_text()
        built = set(re.findall(r'([\w-]+)\.o', ' '.join(re.findall(r'obj-m \+= ([^\n]*(?:\\\n[^\n]*)*)', kbuild))))
        src = DSP.read_text()
        listed = set(re.search(r'MODULES="([^"]*)"', src.split('CORE_MODULES=', 1)[1]).group(1).split())
        dma = set(re.search(r'DMA_MODULES="([^"]*)"', src).group(1).split())
        # every module built is loaded by name (the 5.4-only ones may stay in the list: present() skips them)
        self.assertEqual(built - listed - dma, set())

    def test_nothing_loads_by_itself(self):
        # udev would load a module whose alias matches a vendor device tree node, without the parameters audio_mem
        # and the card need, and most of them cannot be unloaded again (FINDINGS 24b)
        for p in MOD.rglob('*.c'):
            for line in p.read_text(errors='replace').splitlines():
                code = re.sub(r'/\*.*?\*/', '', line)
                self.assertNotRegex(code, r'\bMODULE_(DEVICE_TABLE|ALIAS)\(', p)

    def test_built_after_the_modem_stack_whose_exports_it_uses(self):
        src = (UP / 'build-modules.sh').read_text()
        mods = re.search(r'mods=\$\{\*:-([^}]*)\}', src).group(1).split()
        self.assertLess(mods.index('sprd_modem'), mods.index('sprd_audio'))
        self.assertIn('drivers/dma/virt-dma.c', src)

    def test_kernel_config(self):
        cfg = (UP / 'mu300-mainline.config').read_text()
        for opt in ('CONFIG_SND_SOC=m', 'CONFIG_SND_SOC_SPRD=m', 'CONFIG_SND_DYNAMIC_MINORS=y', 'CONFIG_DMADEVICES=y',
                    'CONFIG_OF_RESERVED_MEM_ADD="0xaf600000,3M;0xaf900000,7M"'):
            self.assertIn(opt + '\n', cfg)
        # the 5.4 kernel reserves the same ranges
        self.assertIn('CONFIG_OF_RESERVED_MEM_ADD="0xaf600000,3M;0xaf900000,7M"',
                      (TOP / 'kernel' / 'mu300-linux.fragment').read_text())
        exclude = (UP / 'all-modules.exclude').read_text()
        self.assertRegex(exclude, r'(?m)^dir sound/soc$')

    def test_reservation_patch(self):
        patch = (UP / 'patches' / '0022-of-reserved-mem-add.patch').read_text()
        self.assertIn('+config OF_RESERVED_MEM_ADD', patch)
        self.assertIn('+	reserved_mem_add_regions();', patch)
        # pages kept (memblock_reserve, not no-map): audio_mem maps them with vmap() like the modem's smem
        self.assertIn('memblock_reserve(base, size)', patch)


class Hardening(unittest.TestCase):
    """FINDINGS 188: what userspace can hand the vendor code."""

    def test_the_kernel_checks_every_control_write(self):
        self.assertIn('CONFIG_SND_CTL_INPUT_VALIDATION=y\n', (UP / 'mu300-mainline.config').read_text())

    def test_enum_puts_check_the_value_they_use(self):
        for p in MOD.rglob('*.c'):
            src = p.read_text(errors='replace')
            self.assertNotRegex(src, r'value\.integer\.value\[0\] >= [\w>-]*items', p)

    def test_no_control_declares_a_maximum_below_zero(self):
        dai = (MOD / 'sprd' / 'dai' / 'vbc' / 'v4' / 'vbc_dai' / 'vbc-dai.c').read_text()
        self.assertIn('#define MAX_32_BIT (0x7fffffff)', dai)
        self.assertIn('#define VBC_PROFILE_CNT_MAX 0x7fffffff',
                      (MOD / 'sprd' / 'dai' / 'vbc' / 'v4' / 'vbc_dai' / 'vbc-phy-v4.h').read_text())

    def test_profiles_are_stored_checked(self):
        dai = (MOD / 'sprd' / 'dai' / 'vbc' / 'v4' / 'vbc_dai' / 'vbc-dai.c').read_text()
        store = c_function(dai, 'vbc_profile_store')
        for check in ('size < sizeof(hdr)', 'check_mul_overflow', 'VBC_PROFILE_MAX_BYTES', 'kfree(p_profile_setting->data'):
            self.assertIn(check, store)
        for user in ('vbc_turning_profile_loading', 'vbc_profile_loading'):
            self.assertIn('vbc_profile_store(', c_function(dai, user))
        self.assertIn('mode_offset >= p_profile_setting->hdr[profile_id].num_mode', c_function(dai, 'vbc_profile_try_apply'))

    def test_offload_is_off_unless_asked_for(self):
        compr = (MOD / 'sprd' / 'platform' / 'compr_2stage_dma' / 'sprd-compr-2stage-dma.c').read_text()
        self.assertIn('static bool allow_offload;', compr)
        self.assertIn('if (!allow_offload)\n\t\treturn -EPERM;', c_function(compr, 'sprd_cc_open'))


class DmaRestart(unittest.TestCase):
    """FINDINGS 24e: a stream prepared and started again after an xrun submitted the same descriptor twice."""

    def test_trigger_submits_each_descriptor_once(self):
        trig = c_function(PCM.read_text(), 'sprd_pcm_trigger')
        start = trig.split('case SNDRV_PCM_TRIGGER_START:', 1)[1].split('case SNDRV_PCM_TRIGGER_STOP:', 1)[0]
        self.assertIn('if (rtd->submitted[i]) {', start)
        self.assertIn('dmaengine_resume(rtd->dma_chn[i])', start)
        self.assertIn('rtd->submitted[i] = true;', start)
        # the submit is behind the check
        self.assertLess(start.index('if (rtd->submitted[i]) {'), start.index('dmaengine_submit('))

    def test_prepare_rebuilds_the_descriptors_of_a_stream_that_ran(self):
        src = PCM.read_text()
        prep = c_function(src, 'sprd_pcm_prepare')
        self.assertIn('dmaengine_terminate_sync(', prep)
        self.assertIn('sprd_pcm_prep_desc(substream)', prep)
        self.assertLess(prep.index('dmaengine_terminate_sync('), prep.index('sprd_pcm_prep_desc(substream)'))
        build = c_function(src, 'sprd_pcm_prep_desc')
        self.assertIn('device_prep_slave_sg(', build)
        self.assertIn('rtd->submitted[i] = false;', build)

    def test_every_place_that_drops_a_descriptor_forgets_it_was_submitted(self):
        src = PCM.read_text()
        for line_no, line in enumerate(src.splitlines()):
            if re.search(r'rtd->dma_tx_des\[i\] = NULL;', line):
                near = '\n'.join(src.splitlines()[line_no:line_no + 3])
                self.assertIn('submitted[i] = false', near)


class Userspace(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def audio_dir(self, files):
        k = Path(tempfile.mkdtemp(dir=self.tmp)) / 'lib-modules' / 'rel'
        for f in files:
            (k / f).parent.mkdir(parents=True, exist_ok=True)
            (k / f).write_text('')
        src = DSP.read_text()
        func = re.search(r'(?ms)^audio_dir\(\) \{\n.*?\n\}\n', src).group(0)
        r = subprocess.run(['sh', '-c', 'K=%s\n%saudio_dir' % (k, func)], capture_output=True, text=True)
        return r.returncode, r.stdout.strip().replace(str(k), 'K')

    def test_the_drivers_are_found_where_each_kernel_has_them(self):
        self.assertEqual(self.audio_dir(['audio/sprd_audcp_boot.ko']), (0, 'K/audio'))      # 5.4, mu300-audio
        self.assertEqual(self.audio_dir(['extra/sprd_audcp_boot.ko']), (0, 'K/extra'))      # mainline, Ubuntu
        self.assertEqual(self.audio_dir(['sprd_audcp_boot.ko']), (0, 'K'))                  # mainline, OpenWrt

    def test_no_drivers(self):
        self.assertEqual(self.audio_dir(['extra/sipc-core.ko'])[0], 1)

    def voice(self, files):
        k = Path(tempfile.mkdtemp(dir=self.tmp)) / 'lib-modules'
        k.mkdir()
        for f in files:
            (k / f).parent.mkdir(parents=True, exist_ok=True)
            (k / f).write_text('')
        src = VOICE.read_text()
        block = re.search(r'(?ms)^if \[ -z "\$card" \]; then\n    _k=.*?\nfi\n', src).group(0)
        block = block.replace('/lib/modules/$(uname -r)', str(k))
        r = subprocess.run(['sh', '-c', 'card=\n' + block + 'echo past'], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_mu300_voice_no_longer_refuses_mainline(self):
        self.assertEqual(self.voice(['extra/sprd_audcp_boot.ko'])[0], 0)
        self.assertEqual(self.voice(['sprd_audcp_boot.ko'])[0], 0)
        self.assertEqual(self.voice(['audio/sprd_audcp_boot.ko'])[0], 0)
        rc, out = self.voice([])
        self.assertEqual(rc, 1)
        self.assertIn('no audio drivers', out)

    def test_mainline_loads_the_asoc_core_first(self):
        src = DSP.read_text()
        self.assertIn('CORE_MODULES="snd-soc-core snd-compress"', src)
        load = re.search(r'(?ms)^load_all\(\) \{\n.*?\n\}\n', src).group(0)
        self.assertLess(load.index('$CORE_MODULES'), load.index('$DMA_MODULES'))
        self.assertLess(load.index('$DMA_MODULES'), load.index('for m in $MODULES'))

    def test_echo_reaches_the_uplink_at_its_own_rate(self):
        # the first call that ran the echo replayed the 48 kHz stereo dump into the 16 kHz mono tap through
        # "plughw:", which opened the tap at 48 kHz stereo: slow motion and crackle (FINDINGS 188)
        src = VOICE.read_text()
        loop = re.search(r'(?ms)^echo_loop\(\) \{\n.*?\n\}\n', src).group(0)
        self.assertNotIn('plug$PLAY', loop)
        self.assertIn('-D mu300_uplink', loop)
        conf = re.search(r'(?ms)^uplink_conf\(\) \{\n.*?\n\}\n', src).group(0)
        with tempfile.TemporaryDirectory() as d:
            out = subprocess.run(['sh', '-c', 'TMP=%s PLAY=hw:1,53 PLAY_RATE=16000\n%suplink_conf\ncat $TMP/uplink.conf'
                                  % (d, conf)], capture_output=True, text=True, check=True).stdout
        self.assertIn('</usr/share/alsa/alsa.conf>', out)
        self.assertRegex(out, r'slave \{ pcm "hw:1,53" rate 16000 channels 1 format S16_LE \}')

    def test_ubuntu_leaves_the_card_state_alone(self):
        # alsa-utils restored every control when the card appeared, before the DSP was started: each DSP control
        # failed with -5, each codec read waited for the power domain, and on 5.4 mu300-audio ran past its timeout
        # and was killed in its start (FINDINGS 193). The image masks the service and the udev rule; run its lines.
        src = (TOP / 'rootfs' / 'assemble.sh').read_text()
        lines = [l for l in src.splitlines()
                 if ('alsa' in l or l == 'mkdir -p $R/etc/udev/rules.d') and not l.lstrip().startswith('#')]
        self.assertEqual(len(lines), 4)
        r = self.tmp / 'root'
        subprocess.run(['sh', '-ec', 'mkdir -p $R/etc/systemd/system\n' + '\n'.join(lines)],
                       env={'R': str(r), 'PATH': '/usr/bin:/bin'}, check=True)
        for f in ('etc/systemd/system/alsa-restore.service', 'etc/systemd/system/alsa-state.service',
                  'etc/udev/rules.d/90-alsa-restore.rules'):
            self.assertTrue((r / f).is_symlink(), f)
            self.assertEqual(str((r / f).readlink()), '/dev/null', f)
        # after the tarball is made, nothing would carry them
        self.assertLess(src.index('90-alsa-restore.rules'), src.index('tar --numeric-owner'))

    def test_the_guard_never_blocks_what_the_stack_needs(self):
        blocked = {l.split('#')[0].strip() for l in
                   (TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'lib' / 'autoload-blocked').read_text().splitlines()}
        for m in ('snd_soc_core', 'snd_compress', 'snd_pcm', 'snd_timer', 'snd'):
            self.assertNotIn(m, blocked)


if __name__ == '__main__':
    unittest.main()
