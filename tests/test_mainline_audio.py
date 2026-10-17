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
        self.assertEqual(self.audio_dir(['audio/sprd_audcp_boot.ko']), (0, 'K/audio'))      # 5.4, private stack
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

    def test_the_guard_never_blocks_what_the_stack_needs(self):
        blocked = {l.split('#')[0].strip() for l in
                   (TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'lib' / 'autoload-blocked').read_text().splitlines()}
        for m in ('snd_soc_core', 'snd_compress', 'snd_pcm', 'snd_timer', 'snd'):
            self.assertNotIn(m, blocked)


if __name__ == '__main__':
    unittest.main()
