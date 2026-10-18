"""A killed mu300-voice leaves nothing behind, and a DSP reply left over from it does not fail the next open
(docs/FINDINGS.md 188).

* mu300-voice runs every aplay/arecord as a child it ends on TERM/INT/HUP/exit, finds and ends a leftover of an
  earlier run that holds a voice PCM, and opens a PCM once more after EIO.
* audio-sipc's aud_recv_cmd() drops a reply for another command and waits on within the same timeout: compiled here
  against a fake message cache (the same function in the mainline tree and in the 5.4 patch)."""
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from helpers import TOP

VOICE = TOP / 'rootfs' / 'overlay' / 'opt' / 'mu300' / 'bin' / 'mu300-voice'
SIPC = TOP / 'upstream' / 'modules' / 'sprd_audio' / 'sprd_audio' / 'audiosipc' / 'audio-sipc.c'
PATCH54 = TOP / 'kernel' / 'patches' / 'audio-sipc-stale-reply.patch'
BUILD_AUDIO = TOP / 'kernel' / 'build-audio.sh'


def func(src, name):
    m = re.search(r'(?ms)^%s\(\) \{[^\n]*\n.*?\n\}\n' % re.escape(name), src)
    if not m:
        m = re.search(r'(?m)^%s\(\) \{.*\}$' % re.escape(name), src)
    assert m, name
    return m.group(0).rstrip('\n') + '\n'


def voice_lib(*names, audio_stop='audio_stop() { echo stopped >> "$TMP/stop.log"; }\n'):
    src = VOICE.read_text()
    return ('nap() { sleep "$1"; }\n' + audio_stop +
            ''.join(func(src, n) for n in names))


CHILD_FUNCS = ('alive', 'reap', 'child', 'cleanup', 'on_signal', 'guard_children')


def gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:
        state = Path('/proc/%d/stat' % pid).read_text().rsplit(') ', 1)[1][0]
        return state in 'ZX'
    except OSError:
        return False


class Children(unittest.TestCase):
    """The orphaned `aplay -D mu300_uplink` of the owner's test call: a signal to the script ends its players."""

    def run_and_signal(self, sig, starting=False):
        with tempfile.TemporaryDirectory() as d:
            pidf = Path(d) / 'player.pid'
            script = ('TMP=%s\nCHILDREN=\nSCENE_STARTING=%s\n' % (d, '1' if starting else '') +
                      voice_lib(*CHILD_FUNCS) +
                      'guard_children\n'
                      # a player that holds on like aplay on a stalled PCM: its pid, then a long wait
                      'child sh -c \'echo $$ > %s; exec sleep 60\'\n'
                      'echo returned\n' % pidf)
            p = subprocess.Popen(['sh', '-c', script], stdout=subprocess.PIPE, text=True)
            for _ in range(100):
                if pidf.exists() and pidf.read_text().strip():
                    break
                time.sleep(0.05)
            player = int(pidf.read_text())
            t0 = time.time()
            p.send_signal(sig)
            out, _ = p.communicate(timeout=10)
            took = time.time() - t0
            try:
                self.assertTrue(gone(player), 'the player outlived the script')
            finally:
                if not gone(player):
                    os.kill(player, signal.SIGKILL)
            self.assertNotIn('returned', out)
            self.assertLess(took, 5)
            stopped = (Path(d) / 'stop.log').exists()
            return p.returncode, stopped

    def test_term_ends_the_player(self):
        rc, stopped = self.run_and_signal(signal.SIGTERM)
        self.assertEqual(rc, 143)
        self.assertFalse(stopped, 'a scene that had started is the call\'s and stays')

    def test_int_and_hup_too(self):
        self.assertEqual(self.run_and_signal(signal.SIGINT)[0], 130)
        self.assertEqual(self.run_and_signal(signal.SIGHUP)[0], 129)

    def test_a_scene_still_starting_is_stopped(self):
        rc, stopped = self.run_and_signal(signal.SIGTERM, starting=True)
        self.assertTrue(stopped)

    def test_child_returns_the_status_and_forgets_the_pid(self):
        script = ('CHILDREN=\n' + voice_lib(*CHILD_FUNCS) +
                  'child sh -c "exit 3"; echo "rc=$? [$CHILDREN]"\n'
                  'child true; echo "rc=$? [$CHILDREN]"\n')
        out = subprocess.run(['sh', '-c', script], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.split('\n')[:2], ['rc=3 []', 'rc=0 []'])

    def test_every_player_goes_through_pcm_run(self):
        src = VOICE.read_text()
        code = '\n'.join(l for l in src.splitlines() if not l.lstrip().startswith('#'))
        for m in re.finditer(r'.*\b(aplay|arecord) -D.*', code):
            self.assertIn('pcm_run ', m.group(0), m.group(0))
        self.assertNotIn('err=$(aplay', code)
        # the commands that start players install the guard first
        self.assertRegex(src, r'answer\|call\|demo\|tone\|echo\|audio\) guard_children ;;')
        # and the scene's own start is marked, so a signal in the middle of it puts the controls back
        body = func(src, 'audio_start')
        self.assertLess(body.index('SCENE_STARTING=1'), body.index('pcmhost "'))
        self.assertIn('    SCENE_STARTING=\n', body)

    def test_nothing_restarts_a_stopped_stream(self):
        # 5.4 panics on PREPARE + START after an xrun (FINDINGS 24e): ending a player is TERM (it closes its PCM),
        # and a retry is a fresh open, only after an open error
        body = func(VOICE.read_text(), 'pcm_run')
        self.assertIn("'audio open error: Input/output error'", body)
        self.assertIn("'audio open error:.*[Bb]usy'", body)
        self.assertNotIn('Input/output error\'"', body.replace("'audio open error: Input/output error'", ''))


class EchoEndsWithTheCall(unittest.TestCase):
    """The owner's 7.2 test call (2026-10-18): after the hangup `mu300-voice call` kept echoing, for ever."""

    def run_loop(self, scene_slices, call_slices=None):
        # scene_running/in_call answer from a counter: they turn false after that many slices
        with tempfile.TemporaryDirectory() as d:
            script = ('TMP=%s\nPLAY=hw:1,53\nPLAY_RATE=16000\nCAP=hw:1,16\n' % d +
                      'n=0\nscene_running() { [ "$n" -lt %d ]; }\n' % scene_slices +
                      'in_call() { [ "$n" -lt %d ]; }\n' % (call_slices if call_slices is not None else 99) +
                      'pcm_run() { n=$((n + 1)); echo "$*" >> "$TMP/runs"; }\n' +
                      'nap() { sleep 30 </dev/null >/dev/null 2>&1; }\nCHILDREN=\n' +   # the watcher sleeps through these
                      func(VOICE.read_text(), 'uplink_conf') + func(VOICE.read_text(), 'echo_loop') +
                      ('ECHO_UNTIL_HANGUP=1 ' if call_slices is not None else '') +
                      'echo_loop 1\necho "rc=$?"\nkill $CHILDREN 2>/dev/null\n')
            r = subprocess.run(['sh', '-c', script], capture_output=True, text=True, timeout=10)
            runs = (Path(d) / 'runs').read_text().count('arecord') if (Path(d) / 'runs').exists() else 0
            return r, runs

    def test_a_stopped_scene_ends_it(self):
        r, runs = self.run_loop(scene_slices=4)
        self.assertIn('rc=0', r.stdout)
        self.assertIn('the call scene stopped', r.stderr)
        self.assertEqual(runs, 2)   # each slice is one capture and one playback

    def test_a_call_that_ended_ends_it(self):
        r, runs = self.run_loop(scene_slices=99, call_slices=2)
        self.assertIn('rc=0', r.stdout)
        self.assertIn('the call ended', r.stderr)
        self.assertEqual(runs, 1)

    def test_without_a_call_of_its_own_only_the_scene_counts(self):
        # `mu300-voice echo` on its own does not ask the modem: it runs as long as the scene does
        r, runs = self.run_loop(scene_slices=6)
        self.assertEqual(runs, 3)

    def test_a_blocked_player_is_ended_by_the_watcher(self):
        # the F50-C case: the player blocks once the call is gone, so the loop never gets back to its own check
        with tempfile.TemporaryDirectory() as d:
            scene = Path(d) / 'scene'
            scene.touch()
            pidf = Path(d) / 'player.pid'
            script = ('TMP=%s\nCHILDREN=\nSCENE_STARTING=\nPLAY=hw:1,53\nPLAY_RATE=16000\nCAP=hw:1,16\n' % d +
                      voice_lib(*CHILD_FUNCS) + func(VOICE.read_text(), 'uplink_conf') +
                      func(VOICE.read_text(), 'echo_loop') +
                      'scene_running() { [ -f "$TMP/scene" ]; }\n'
                      'pcm_run() { child sh -c \'echo $$ > "$0"; exec sleep 60\' "%s"; }\n' % pidf +
                      'guard_children\necho_loop 1\n')
            p = subprocess.Popen(['sh', '-c', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(50):
                if pidf.exists() and pidf.read_text().strip():
                    break
                time.sleep(0.1)
            player = int(pidf.read_text())
            scene.unlink()
            out, err = p.communicate(timeout=15)
            self.assertEqual(p.returncode, 0, err)
            self.assertIn('the call is over', err)
            for _ in range(30):
                if gone(player):
                    break
                time.sleep(0.1)
            self.assertTrue(gone(player))

    def test_call_and_answer_bind_it_to_the_call(self):
        src = VOICE.read_text()
        self.assertEqual(src.count('ECHO_UNTIL_HANGUP=1 demo'), 2)


class Retry(unittest.TestCase):
    def run_pcm_run(self, errors, busy_frees=False):
        """errors: what each try of the fake player prints (empty: success)."""
        with tempfile.TemporaryDirectory() as d:
            for i, e in enumerate(errors):
                (Path(d) / ('err%d' % i)).write_text(e)
            player = Path(d) / 'player'
            player.write_text('#!/bin/sh\nn=$(cat %s/n 2>/dev/null || echo 0); echo $((n + 1)) > %s/n\n'
                              'e=$(cat %s/err$n 2>/dev/null); [ -z "$e" ] && exit 0; echo "$e" >&2; exit 1\n'
                              % (d, d, d))
            player.chmod(0o755)
            script = ('CHILDREN=\nTMP=%s\n' % d + voice_lib(*CHILD_FUNCS) +
                      'pcm_busy() { echo "busy $1" >&2; %s; }\n' % ('return 0' if busy_frees else 'return 1') +
                      func(VOICE.read_text(), 'pcm_run') +
                      'pcm_run %s/e /dev/snd/pcmC0D53p %s; echo "rc=$?"\n' % (d, player))
            r = subprocess.run(['sh', '-c', script], capture_output=True, text=True)
            tries = int((Path(d) / 'n').read_text())
            return r.stdout.strip(), tries, r.stderr

    def test_eio_at_open_is_tried_once_more(self):
        out, tries, err = self.run_pcm_run(['arecord: main:831: audio open error: Input/output error', ''])
        self.assertEqual((out, tries), ('rc=0', 2))
        self.assertIn('trying once more', err)

    def test_only_once(self):
        eio = 'arecord: main:831: audio open error: Input/output error'
        out, tries, _ = self.run_pcm_run([eio, eio, ''])
        self.assertEqual((out, tries), ('rc=1', 2))

    def test_an_error_after_the_open_is_not_retried(self):
        out, tries, _ = self.run_pcm_run(['aplay: pcm_write:2146: write error: Input/output error', ''])
        self.assertEqual((out, tries), ('rc=1', 1))

    def test_busy_retried_only_when_a_leftover_was_ended(self):
        busy = 'aplay: main:831: audio open error: Device or resource busy'
        out, tries, err = self.run_pcm_run([busy, ''], busy_frees=False)
        self.assertEqual((out, tries), ('rc=1', 1))
        self.assertIn('busy /dev/snd/pcmC0D53p', err)
        out, tries, _ = self.run_pcm_run([busy, ''], busy_frees=True)
        self.assertEqual((out, tries), ('rc=0', 2))


@unittest.skipUnless(Path('/proc/self/fd').is_dir() and shutil.which('sleep'), 'needs Linux /proc')
class Holders(unittest.TestCase):
    """pcm_holders/pcm_busy find who has the node open; only a player of this script's own files is ended."""

    def test_leftover_ended_stranger_only_reported(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / 'run'
            (tmp / 'bin').mkdir(parents=True)
            node = Path(d) / 'pcmC0D53p'
            node.write_text('')
            # an "aplay" whose command line names a file of ours, and a stranger holding the same node
            aplay = tmp / 'bin' / 'aplay'
            shutil.copy(shutil.which('sleep'), aplay)
            with open(node) as f1, open(node) as f2:
                ours = subprocess.Popen([str(aplay), '60'], stdin=f1)
                stranger = subprocess.Popen(['sleep', '60'], stdin=f2)
            try:
                time.sleep(0.2)
                script = ('TMP=%s\n' % tmp + voice_lib('alive', 'reap', 'pcm_holders', 'pcm_busy') +
                          'pcm_holders %s | tr "\\n" " "; echo\npcm_busy %s; echo "rc=$?"\n' % (node, node))
                r = subprocess.run(['sh', '-c', script], capture_output=True, text=True, timeout=20)
                holders = r.stdout.splitlines()[0].split()
                self.assertEqual(sorted(holders), sorted([str(ours.pid), str(stranger.pid)]))
                self.assertIn('rc=0', r.stdout)
                self.assertIn('left over from an earlier mu300-voice: ending it', r.stderr)
                self.assertIn('held by pid %d (sleep)' % stranger.pid, r.stderr)
                ours.wait(timeout=5)
                self.assertIsNone(stranger.poll())
            finally:
                for p in (ours, stranger):
                    if p.poll() is None:
                        p.kill()
                        p.wait()


FAKE = r'''
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;
#define AUD_IPC_AGDSP 0
#define SMSG_CACHE_NR 256
#define pr_warn(...) do { } while (0)
#define pr_err(...) do { } while (0)
#define sp_asoc_pr_dbg(...) do { } while (0)
struct aud_smsg { uint16_t command; uint16_t channel; u32 parameter0, parameter1, parameter2, parameter3; };
static void aud_smsg_set(struct aud_smsg *m, uint16_t ch, uint16_t cmd, u32 a, u32 b, u32 c, int d)
{ m->channel = ch; m->command = cmd; m->parameter0 = a; m->parameter1 = b; m->parameter2 = c; m->parameter3 = d; }

/* the channel's cache: messages with the jiffy they arrive at */
static unsigned long jiffies;
static struct { u16 cmd; unsigned long at; } q[400];
static int qn, qr, waits[64], nwaits;
static void put(u16 cmd, unsigned long at) { q[qn].cmd = cmd; q[qn].at = at; qn++; }
static int aud_smsg_recv(u8 dst, struct aud_smsg *msg, int timeout)
{
	if (timeout > 0 && nwaits < 64) waits[nwaits++] = timeout;
	if (qr < qn && (q[qr].at <= jiffies || timeout < 0 ||
	                (timeout > 0 && q[qr].at <= jiffies + (unsigned long)timeout))) {
		if (q[qr].at > jiffies) jiffies = q[qr].at;
		msg->command = q[qr].cmd; msg->parameter3 = qr; qr++;
		return 0;
	}
	if (timeout == 0) return -ENODATA;
	jiffies += timeout;            /* the real one also resets the DSP here */
	return -ETIME;
}
static void reset(void) { jiffies = 1000; qn = qr = nwaits = 0; }
'''

MAIN = r'''
#define CH 1
#define CHECK(c) do { if (!(c)) { printf("FAIL %d: %s\n", __LINE__, #c); return 1; } } while (0)
int main(void)
{
	struct aud_smsg r;
	int i;

	/* the drain before a send: a stale KCTL_SET reply no longer stops it, a STARTUP reply is returned */
	reset(); put(9, 0); put(4, 0);
	CHECK(aud_recv_cmd(CH, 4, &r, 0) == 0 && r.command == 4 && r.parameter3 == 1);
	reset(); put(9, 0); put(9, 0); put(9, 0);
	CHECK(aud_recv_cmd(CH, 4, &r, 0) == -ENODATA && qr == 3);

	/* the wait: the stale reply is dropped and the wait goes on with what is left of the timeout */
	reset(); put(9, 1100); put(4, 1200);
	CHECK(aud_recv_cmd(CH, 4, &r, 300) == 0 && r.command == 4);
	CHECK(nwaits == 2 && waits[0] == 300 && waits[1] == 200 && jiffies == 1200);

	/* and never longer than the timeout in all */
	reset(); put(9, 1100); put(4, 1500);
	CHECK(aud_recv_cmd(CH, 4, &r, 300) == -EIO);
	CHECK(jiffies <= 1000 + 300 + 1);

	/* a deadline already passed while a stale reply was read: still a real wait of one jiffy, never 0 */
	reset(); put(9, 1300);
	CHECK(aud_recv_cmd(CH, 4, &r, 300) == -EIO && waits[1] == 1);

	/* the plain case is unchanged */
	reset(); put(5, 1010);
	CHECK(aud_recv_cmd(CH, 5, &r, 300) == 0 && r.command == 5 && nwaits == 1);

	/* waiting forever (-1) skips a stale reply the same way */
	reset(); put(9, 2000); put(4, 3000);
	CHECK(aud_recv_cmd(CH, 4, &r, -1) == 0 && r.command == 4);

	/* a cache full of the wrong replies ends, it does not spin */
	reset(); for (i = 0; i < 300; i++) put(9, 0);
	CHECK(aud_recv_cmd(CH, 4, &r, 0) == -EIO && qr == SMSG_CACHE_NR);

	puts("ok");
	return 0;
}
'''


def recv_cmd(src):
    m = re.search(r'(?ms)^int aud_recv_cmd\(.*?\n\}\n', src)
    assert m
    return m.group(0)


class StaleReply(unittest.TestCase):
    def test_same_function_in_both_trees(self):
        # the 5.4 patch adds exactly the mainline function
        patched = '\n'.join(l[1:] for l in PATCH54.read_text().splitlines()
                            if l.startswith(('+', ' ')) and not l.startswith('+++'))
        self.assertEqual(recv_cmd(patched + '\n'), recv_cmd(SIPC.read_text()))
        self.assertIn('audio-sipc-stale-reply:dropped:sprd_audio/audiosipc', BUILD_AUDIO.read_text())
        self.assertNotRegex(PATCH54.read_text(), r'\bSIP\b|[Gg]ateway')

    @unittest.skipUnless(shutil.which('cc'), 'needs a C compiler')
    def test_aud_recv_cmd(self):
        with tempfile.TemporaryDirectory() as d:
            c = Path(d) / 't.c'
            c.write_text(FAKE + recv_cmd(SIPC.read_text()) + MAIN)
            subprocess.run(['cc', '-Wall', '-Werror', '-o', str(Path(d) / 't'), str(c)], check=True,
                           capture_output=True)
            r = subprocess.run([str(Path(d) / 't')], capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, 'ok'), r.stdout)


if __name__ == '__main__':
    unittest.main()
