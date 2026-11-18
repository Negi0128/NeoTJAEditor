"""試聴を早く鳴らす: DEMOSTART から先だけ先に読んで鳴らし、全体が読めたら
位置を保って差し替える。読み終わった曲は少し持っておき、試聴・曲名表示の
先読み・本編で読み直さない。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_fast_preview.py`
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-fastpreview-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

import numpy as np  # noqa: E402

from gui_support import real_qt_test  # noqa: E402


def _ffmpeg():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def _song(folder, name, secs=60, demostart=21.5):
    ff = _ffmpeg()
    d = Path(folder) / name
    d.mkdir(parents=True)
    ogg = d / 'song.ogg'
    subprocess.run([ff, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'sine=f=330:d=%d:r=44100' % secs, '-f', 'lavfi', '-i',
                    'anoisesrc=d=%d:c=pink:a=0.2:r=44100' % secs,
                    '-filter_complex', '[0][1]amerge=inputs=2', '-ac', '2',
                    '-c:a', 'libvorbis', '-q:a', '4', str(ogg)], check=True)
    tja = d / 'song.tja'
    tja.write_text('TITLE:%s\nBPM:120\nWAVE:song.ogg\nDEMOSTART:%s\nCOURSE:Oni\nLEVEL:5\n'
                   '#START\n1111,\n#END\n' % (name, demostart), encoding='cp932')
    return str(tja), str(ogg)


def _wait_until(cond, timeout=10.0):
    from PySide6.QtTest import QTest
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        QTest.qWait(2)
        time.sleep(0.002)
    return cond()


class _Mips:
    chains = []


class CacheTests(unittest.TestCase):
    def test_lru_by_count_and_bytes(self):
        from neotja.audio_engine import DecodeCache
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i in range(4):
                p = os.path.join(d, '%d.ogg' % i)
                Path(p).write_bytes(b'x' * (i + 1))
                paths.append(p)
            mb = np.zeros((1024 * 1024 // 8, 2), dtype=np.float32)      # 1MB
            c = DecodeCache(max_entries=3, max_bytes=int(2.5 * 1024 * 1024))
            for p in paths[:2]:
                c.put(p, (mb, 44100, [], 1.0, _Mips()))
            self.assertEqual(len(c), 2)
            self.assertIsNotNone(c.get(paths[0]))      # 0 を最近使ったことに
            c.put(paths[2], (mb, 44100, [], 1.0, _Mips()))
            # 2.5MB を越えるので一番古い(1)を捨てる。
            self.assertEqual(len(c), 2)
            self.assertIsNone(c.get(paths[1]))
            self.assertIsNotNone(c.get(paths[0]))
            # 大きすぎるものは持たない。
            big = np.zeros((1024 * 1024, 2), dtype=np.float32)          # 8MB
            c.put(paths[3], (big, 44100, [], 1.0, _Mips()))
            self.assertIsNone(c.get(paths[3]))
            # 書き換わったら別物(古い版は捨てる)。
            time.sleep(0.01)
            Path(paths[0]).write_bytes(b'changed!')
            self.assertIsNone(c.get(paths[0]))
            c.clear()
            self.assertEqual(len(c), 0)

    def test_mixer_swap_keeps_position_and_voices(self):
        from neotja.mixer_engine import MixerCore
        core = MixerCore(48000)
        a = np.zeros((48000 * 10, 2), dtype=np.float32)
        core.post(('song', a, 48000))
        core.post(('seek', 48000 * 3))
        core.post(('play',))
        core.render(480)
        core._spawn(np.ones((4800, 2), dtype=np.float32), 0, False)
        pos = core.read_pos
        b = np.full((48000 * 10, 2), 0.1, dtype=np.float32)
        core.post(('song_swap', b, 48000))
        core.render(480)
        self.assertIs(core.song, b)
        self.assertAlmostEqual(core.read_pos, pos + 480, places=3)
        self.assertEqual(len(core.voices), 1)


class SegmentDecodeTests(unittest.TestCase):
    def test_segment_matches_full_decode(self):
        if _ffmpeg() is None:
            self.skipTest('ffmpeg が無い')
        from neotja.audio_engine import decode_segment_ffmpeg, decode_stereo_ffmpeg
        with tempfile.TemporaryDirectory() as d:
            _tja, ogg = _song(d, 'a', secs=30)
            full, sr = decode_stereo_ffmpeg(ogg)
            seg, sr2, total = decode_segment_ffmpeg(ogg, 12.34, 3.0)
            self.assertEqual(sr, sr2)
            self.assertAlmostEqual(total, len(full) / sr, delta=0.05)
            self.assertAlmostEqual(len(seg) / sr, 3.0, delta=0.01)
            off = int(round(12.34 * sr))
            # 位置が合っているか(ずらして一番近いところが 0 か)。頭の数 ms は
            # 飛んだ直後の復号の揺れがあり得るので、少し先から比べる。
            a = seg[4096:4096 + 8192, 0]
            best = min(range(-64, 65),
                       key=lambda k: float(np.abs(full[off + 4096 + k:off + 4096 + k + 8192, 0] - a).mean()))
            self.assertEqual(best, 0)
            diff = np.abs(full[off + 4096:off + 4096 + 8192] - seg[4096:4096 + 8192]).max()
            self.assertLess(diff, 1e-3)


class PlayerCoreFastStartTests(unittest.TestCase):
    @real_qt_test
    def test_segment_first_then_swap_and_cache(self):
        if _ffmpeg() is None:
            self.skipTest('ffmpeg が無い')
        from neotja.player.core import PlayerCore
        with tempfile.TemporaryDirectory() as d:
            a, ogg_a = _song(d, 'a', secs=180)
            b, _ogg_b = _song(d, 'b', secs=180)
            core = PlayerCore({'master_volume': 0.0})
            try:
                audio = core.dock.audio
                if not hasattr(audio, 'core'):
                    self.skipTest('ミキサー経路でない')
                t0 = time.perf_counter()
                core.play_demo(a, 21.5)
                self.assertTrue(_wait_until(audio.is_playing, 10))
                first = time.perf_counter() - t0
                # 先読みで鳴り始めた: 全体はまだ読み中、試聴は「準備できた」扱い。
                if core.dock.is_decoding(ogg_a):
                    self.assertEqual(core.dock._mixer_holds, ('segment', ogg_a))
                    self.assertTrue(core.demo_ready())
                    self.assertAlmostEqual(core.dock.duration_seconds(), 180.0, delta=0.1)
                p0 = core.demo_position()
                self.assertTrue(_wait_until(lambda: core.dock._mixer_holds == ('audition', ogg_a), 15))
                p1 = core.demo_position()
                # 差し替えで頭へ戻らない(位置は進み続ける)。
                self.assertGreaterEqual(p1, p0 - 0.05)
                self.assertGreater(p1, 21.0)
                self.assertTrue(core.dock.decode_cache.has(ogg_a))
                self.assertLess(first, 1.0, 'first sound %.0fms' % (first * 1000))
                # 別の曲へ移って戻る: 読み直さずにすぐ鳴る。
                core.stop_audio()
                core.play_demo(b, 21.5)
                self.assertTrue(_wait_until(lambda: core.dock._mixer_holds == ('audition', _ogg_b), 15))
                core.stop_audio()
                t1 = time.perf_counter()
                core.play_demo(a, 21.5)
                self.assertTrue(_wait_until(audio.is_playing, 5))
                again = time.perf_counter() - t1
                self.assertIsNone(core._segment_worker)       # 先読みも要らない
                self.assertLess(again, 0.3, 'cached %.0fms' % (again * 1000))
                # 本編: 持っている曲から助走・余韻を足して渡す(元の PCM は変えない)。
                cached = core.dock.decode_cache.get(ogg_a)[0]
                n_cached = len(cached)
                core.stop_audio()
                t2 = time.perf_counter()
                core.predecode(a)
                self.assertTrue(_wait_until(
                    lambda: core.dock._pre is not None and core.dock._pre[1] is not None, 5))
                pre_t = time.perf_counter() - t2
                self.assertLess(pre_t, 0.3, 'predecode %.0fms' % (pre_t * 1000))
                self.assertTrue(core.load(a, course_key='Oni'))
                self.assertTrue(_wait_until(core.chart_ready, 10))
                self.assertGreater(core.dock.duration_seconds(), 180.0 + 1.0)
                self.assertEqual(len(core.dock.decode_cache.get(ogg_a)[0]), n_cached)
                core.release_cache()
                self.assertEqual(len(core.dock.decode_cache), 0)
            finally:
                core.stop_audio()
                core.shutdown()

    @real_qt_test
    def test_stale_segment_is_dropped(self):
        if _ffmpeg() is None:
            self.skipTest('ffmpeg が無い')
        from neotja.player.core import PlayerCore
        with tempfile.TemporaryDirectory() as d:
            a, ogg_a = _song(d, 'a', secs=120)
            b, ogg_b = _song(d, 'b', secs=120, demostart=5)
            core = PlayerCore({'master_volume': 0.0})
            try:
                if not hasattr(core.dock.audio, 'core'):
                    self.skipTest('ミキサー経路でない')
                core.play_demo(a, 21.5)
                core.stop_audio()
                core.play_demo(b, 5)        # すぐ別の曲へ
                self.assertTrue(_wait_until(lambda: core.dock._mixer_holds == ('audition', ogg_b), 15))
                _wait_until(lambda: False, 0.3)
                self.assertEqual(core._loop[1], ogg_b)
                self.assertEqual(core.dock._mixer_holds, ('audition', ogg_b))
                self.assertGreater(core.demo_position(), 4.9)
            finally:
                core.stop_audio()
                core.shutdown()


if __name__ == '__main__':
    unittest.main()
