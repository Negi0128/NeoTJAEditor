"""難易度選択の試聴(DEMOSTART から流してループ)。音声デバイスには頼らない。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_preview.py`
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-preview-test-')
_CFG = Path(_TMP) / 'settings.json'
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402


def _chart(folder, name, demostart=None, wave=True):
    folder.mkdir(parents=True, exist_ok=True)
    lines = ['TITLE:' + name, 'BPM:120']
    if wave:
        (folder / (name + '.ogg')).write_bytes(b'not really audio')
        lines.append('WAVE:%s.ogg' % name)
    if demostart is not None:
        lines.append('DEMOSTART:%s' % demostart)
    lines += ['COURSE:Oni', 'LEVEL:9', '#START', '1111,', '#END']
    p = folder / (name + '.tja')
    p.write_text('\n'.join(lines), encoding='cp932')
    return str(p)


# ----------------------------------------------------------------------
# PlayerCore の予約・折り返しを、音声エンジンの代わりの偽物で確かめる
# ----------------------------------------------------------------------
class FakeAudio:
    def __init__(self):
        self.calls = []
        self.pos = 0

    def seek(self, ms):
        self.calls.append(('seek', ms))
        self.pos = ms

    def play(self):
        self.calls.append(('play',))

    def pause(self):
        self.calls.append(('pause',))

    def position(self):
        return self.pos


class FakeDock:
    def __init__(self, duration=60.0):
        self.audio = FakeAudio()
        self.duration = duration
        self.decoding = None
        self.loaded = []

    def load_wave_only(self, path):
        if not path or not os.path.exists(path):
            return False
        self.loaded.append(path)
        self.decoding = path
        return True

    def is_decoding(self, wave_path=None):
        return self.decoding is not None and (wave_path is None or wave_path == self.decoding)

    def duration_seconds(self):
        return self.duration

    def set_audition(self, on):
        pass

    def set_volume(self, v):
        pass

    def set_master_volume(self, v):
        pass


class FakeTimer:
    def __init__(self):
        self.running = False

    def start(self, ms):
        self.running = True

    def stop(self):
        self.running = False


def _core(duration=60.0):
    from neotja.player.core import PlayerCore
    core = PlayerCore.__new__(PlayerCore)      # PreviewDock は作らない
    core.cfg = {}
    core._loop = None
    core._pending = None
    core._ready = None
    core._loop_timer = FakeTimer()
    core.dock = FakeDock(duration)
    return core


def _ready(core, path):
    core.dock.decoding = None
    core._on_song_ready(path)


class CoreDemoTests(unittest.TestCase):
    def test_demostart_parse(self):
        from neotja.player.core import demo_start_seconds
        self.assertEqual(demo_start_seconds('DEMOSTART:12.5\n'), 12.5)
        self.assertEqual(demo_start_seconds('DEMOSTART:abc\n'), 0.0)     # 読めない
        self.assertEqual(demo_start_seconds('DEMOSTART:-5\n'), 0.0)      # 負
        self.assertEqual(demo_start_seconds('TITLE:x\n'), 0.0)           # 無い

    def test_starts_at_demostart_after_ready_and_loops_back(self):
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a', 12.5)
            core = _core(60.0)
            wave = os.path.join(os.path.dirname(a), 'a.ogg')
            core.play_demo(a, 12.5)
            self.assertEqual(core.dock.audio.calls, [])      # 読み終わるまで鳴らさない
            _ready(core, wave)
            self.assertEqual(core.dock.audio.calls, [('seek', 12500), ('play',)])
            self.assertTrue(core._loop_timer.running)
            # 音源の終わり(の 0.05 秒手前)まで来たら DEMOSTART へ戻る。
            core.dock.audio.calls.clear()
            core.dock.audio.pos = 59980
            core._tick_loop()
            self.assertEqual(core.dock.audio.calls, [('seek', 12500), ('play',)])

    def test_demostart_beyond_length_starts_from_zero(self):
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a', 999)
            core = _core(30.0)
            core.play_demo(a, 999)
            _ready(core, os.path.join(os.path.dirname(a), 'a.ogg'))
            self.assertEqual(core.dock.audio.calls, [('seek', 0), ('play',)])
            self.assertEqual(core._loop[0], 0.0)

    def test_negative_demostart_is_zero(self):
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a')
            core = _core()
            core.play_demo(a, -3)
            _ready(core, os.path.join(os.path.dirname(a), 'a.ogg'))
            self.assertEqual(core.dock.audio.calls[0], ('seek', 0))

    def test_stale_ready_of_previous_song_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a', 5)
            b = _chart(Path(d) / 'b', 'b', 7)
            wa = os.path.join(os.path.dirname(a), 'a.ogg')
            wb = os.path.join(os.path.dirname(b), 'b.ogg')
            core = _core()
            core.play_demo(a, 5)
            core.stop_audio()
            core.play_demo(b, 7)
            _ready(core, wa)                          # 前の曲の知らせが遅れて届く
            self.assertNotIn(('play',), core.dock.audio.calls)
            _ready(core, wb)
            self.assertIn(('seek', 7000), core.dock.audio.calls)
            # 止めたあとに届いた知らせでも鳴らさない。
            core.dock.audio.calls.clear()
            core.play_demo(a, 5)
            core.stop_audio()
            _ready(core, wa)
            self.assertEqual(core.dock.audio.calls, [('pause',)])


# ----------------------------------------------------------------------
# PlayerHost: どの出口でも試聴が止まるか
# ----------------------------------------------------------------------
class FakeCore:
    instances = []

    def __init__(self, cfg):
        self.cfg = cfg
        self.log = []
        self.dock = FakeDock()
        FakeCore.instances.append(self)

    def play_demo(self, path, demo):
        self.log.append(('play', os.path.basename(path), demo))

    def stop_audio(self):
        self.log.append(('stop',))

    def shutdown(self):
        self.log.append(('shutdown',))


class HostPreviewTests(unittest.TestCase):
    def setUp(self):
        from neotja.player.host import PlayerHost
        FakeCore.instances.clear()
        self._orig = PlayerHost.core_factory
        PlayerHost.core_factory = FakeCore

    def tearDown(self):
        from neotja.player.host import PlayerHost
        PlayerHost.core_factory = self._orig

    @real_qt_test
    def test_preview_starts_and_stops_on_every_exit(self):
        from neotja.player.host import PlayerHost
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a', 12.5)
            b = _chart(Path(d) / 'b', 'b', 'xyz')
            host = PlayerHost({'master_volume': 0.0}, lambda: None)
            host.resize(1600, 900)
            host.show()
            host.on_shown()
            self.assertEqual(FakeCore.instances, [])   # 開くまで作らない
            host.library.choose(a)
            core = FakeCore.instances[0]
            self.assertEqual(core.log, [('stop',), ('play', 'a.tja', 12.5)])
            # 別の曲: 先に止めてから。DEMOSTART が読めなければ 0。
            core.log.clear()
            host.library.choose(b)
            self.assertEqual(core.log, [('stop',), ('play', 'b.tja', 0.0)])
            for click, name in ((host.difficulty.btn_play, 'play'),
                                (host.difficulty.btn_record, 'record'),
                                (host.difficulty.btn_back, 'back')):
                host.show_difficulty(a)
                core.log.clear()
                got = []
                host.play_requested.connect(lambda *x: got.append('play'))
                host.record_requested.connect(lambda *x: got.append('record'))
                click.click()
                self.assertEqual(core.log[0], ('stop',), name)
                host.play_requested.disconnect()
                host.record_requested.disconnect()
            host.show_difficulty(a)
            core.log.clear()
            host.shutdown()
            self.assertEqual(core.log, [('stop',), ('shutdown',)])
            self.assertEqual(len(FakeCore.instances), 1)
            host.deleteLater()

    @real_qt_test
    def test_leaving_player_stops_preview(self):
        from gui_support import editor_session
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a', 3)
            with editor_session() as (win, _root):
                win.show()
                win.start_screen.btn_player.click()
                win.player_page.library.choose(a)
                core = FakeCore.instances[0]
                core.log.clear()
                win.player_page.btn_back.click()    # ランチャーへ
                self.assertEqual(core.log, [('stop',)])
                self.assertFalse(win.is_player_visible())


if __name__ == '__main__':
    unittest.main()
