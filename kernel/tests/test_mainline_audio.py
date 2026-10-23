"""Call audio and the kernel: the Unisoc call-audio stack is the audio extra's (github.com/dikeckaan/mu300-audio),
built there out of tree against this kernel's build. What stays here is what no module can bring: the DSP's memory
reserved at boot (patch 0022), and the ALSA/ASoC/dmaengine core options the extra's drivers link against. Nothing of the
stack is built or shipped here."""
import re
import unittest

from helpers import TOP

UP = TOP / 'kernel'


class StaysInTheKernel(unittest.TestCase):
    def test_kernel_config(self):
        cfg = (UP / 'mu300-mainline.config').read_text()
        for opt in ('CONFIG_SND_SOC=m', 'CONFIG_SND_SOC_SPRD=m', 'CONFIG_SND_DYNAMIC_MINORS=y', 'CONFIG_DMADEVICES=y',
                    'CONFIG_OF_RESERVED_MEM_ADD="0xaf600000,3M;0xaf900000,7M"'):
            self.assertIn(opt + '\n', cfg)
        exclude = (UP / 'all-modules.exclude').read_text()
        self.assertRegex(exclude, r'(?m)^dir sound/soc$')

    def test_reservation_patch(self):
        patch = (UP / 'patches' / '0022-of-reserved-mem-add.patch').read_text()
        self.assertIn('+config OF_RESERVED_MEM_ADD', patch)
        self.assertIn('+	reserved_mem_add_regions();', patch)
        # pages kept (memblock_reserve, not no-map): audio_mem maps them with vmap() like the modem's smem
        self.assertIn('memblock_reserve(base, size)', patch)

    def test_the_kernel_checks_every_control_write(self):
        # the vendor ASoC code indexes tables with control values it does not always check
        self.assertIn('CONFIG_SND_CTL_INPUT_VALIDATION=y\n', (UP / 'mu300-mainline.config').read_text())

    def test_the_guard_never_blocks_what_the_stack_needs(self):
        blocked = {l.split('#')[0].strip() for l in (UP / 'autoload-blocked').read_text().splitlines()}
        for m in ('snd_soc_core', 'snd_compress', 'snd_pcm', 'snd_timer', 'snd'):
            self.assertNotIn(m, blocked)


class NotHereAnyMore(unittest.TestCase):
    def test_no_driver_of_the_stack_is_built(self):
        self.assertFalse((UP / 'modules' / 'sprd_audio').exists())
        self.assertFalse((UP / 'build-audio.sh').exists())
        src = (UP / 'build-modules.sh').read_text()
        mods = re.search(r'mods=\$\{\*:-([^}]*)\}', src).group(1).split()
        self.assertNotIn('sprd_audio', mods)
        self.assertNotIn('virt-dma', src)
        for p in ('audio-*.patch', 'mcdt-enable-clock.patch', 'sprd-card-dummy-on-defer.patch'):
            self.assertEqual(list((UP / 'patches').glob(p)), [], p)


if __name__ == '__main__':
    unittest.main()
