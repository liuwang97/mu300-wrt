"""Call audio is the audio extra's (github.com/dikeckaan/mu300-audio): no program or service of the Unisoc call-audio
stack is in the image; the kernel keeps only the DSP's memory reservation and the ALSA core it links against."""
import unittest

from helpers import BIN, TOP

OVERLAY = TOP / 'openwrt' / 'overlay'


class NotInTheImage(unittest.TestCase):
    def test_no_program_or_service_of_the_stack(self):
        for name in ('mu300-audio-dsp', 'mu300-voice', 'pcmhost', 'ctlset', 'mu300-hfp'):
            self.assertFalse((BIN / name).exists(), name)
        for f in ('etc/init.d/mu300-audio', 'etc/init.d/mu300-voice', 'etc/modprobe.d/mu300-audio.conf'):
            self.assertFalse((OVERLAY / f).exists(), f)
        self.assertNotIn('mu300-voice', (BIN.parent / 'lib' / 'path-commands').read_text().split())


if __name__ == '__main__':
    unittest.main()
