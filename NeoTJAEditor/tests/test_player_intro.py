"""曲名表示(5 秒・飛ばせない)と再生画面への移り方。音声デバイスには頼らない。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_intro.py`
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-intro-test-')
_CFG = Path(_TMP) / 'settings.json'
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402


def _chart(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'song.ogg').write_bytes(b'x')
    p = folder / (name + '.tja')
    p.write_text('TITLE:%s\nSUBTITLE:--副題\nBPM:120\nWAVE:song.ogg\nDEMOSTART:3\n'
                 'COURSE:Oni\nLEVEL:9\n#START\n1111,\n#END\n' % name, encoding='cp932')
    return str(p)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class EnvelopeTests(unittest.TestCase):
    def test_volume_holds_two_seconds_then_fades_to_zero_by_five(self):
        from neotja.player.intro import intro_volume
        self.assertEqual(intro_volume(0.0, 0.8), 0.8)
        self.assertEqual(intro_volume(2.0, 0.8), 0.8)
        self.assertAlmostEqual(intro_volume(3.5, 0.8), 0.4)
        self.assertAlmostEqual(intro_volume(4.9, 0.8), 0.8 * 0.1 / 3.0, places=6)
        self.assertEqual(intro_volume(5.0, 0.8), 0.0)
        self.assertEqual(intro_volume(9.0, 0.8), 0.0)


class SequenceTests(unittest.TestCase):
    def _seq(self, ready_at=None, load_ok=True):
        from neotja.player.intro import IntroSequence
        clock = Clock()
        log = []
        state = {'ready': False}
        seq = IntroSequence(
            set_volume=lambda v: log.append(('vol', round(v, 4))),
            base_volume=0.8,
            stop_demo=lambda: log.append(('stop',)),
            begin_load=lambda: (log.append(('load',)), load_ok)[1],
            is_ready=lambda: state['ready'], clock=clock)
        fired = []
        seq.finished.connect(lambda: fired.append(('finished', round(clock.t - 100.0, 3))))
        seq.failed.connect(lambda: fired.append(('failed', round(clock.t - 100.0, 3))))
        return seq, clock, log, state, fired

    @real_qt_test
    def test_exactly_five_seconds_and_demo_stops_once(self):
        seq, clock, log, state, fired = self._seq()
        state['ready'] = True            # 準備は先に終わっている
        seq.start()
        for t in (0.5, 1.9, 2.0, 3.5, 4.99):
            clock.t = 100.0 + t
            seq.tick()
        self.assertEqual(fired, [])      # 5 秒までは出し続ける
        self.assertNotIn(('stop',), log)
        self.assertIn(('vol', 0.4), log)
        vols = [v for k, v in log if k == 'vol']
        self.assertEqual(vols[:3], [0.8, 0.8, 0.8])
        self.assertTrue(all(a >= b for a, b in zip(vols, vols[1:])))   # 上がらない
        clock.t = 105.0
        seq.tick()
        seq.tick()
        self.assertEqual(log.count(('stop',)), 1)
        self.assertEqual(log.count(('load',)), 1)
        self.assertEqual(fired, [('finished', 5.0)])
        self.assertEqual(log[-3:], [('vol', 0.0), ('stop',), ('load',)])

    @real_qt_test
    def test_waits_for_late_load(self):
        seq, clock, log, state, fired = self._seq()
        seq.start()
        clock.t = 105.0
        seq.tick()
        clock.t = 106.0
        seq.tick()
        self.assertEqual(fired, [])      # 間に合っていなければ待つ(飛ばさない)
        state['ready'] = True
        clock.t = 106.7
        seq.tick()
        self.assertEqual(fired, [('finished', 6.7)])
        self.assertEqual(log.count(('load',)), 1)

    @real_qt_test
    def test_failed_load_and_abort(self):
        seq, clock, log, state, fired = self._seq(load_ok=False)
        seq.start()
        clock.t = 105.0
        seq.tick()
        self.assertEqual(fired, [('failed', 5.0)])
        seq2, clock2, log2, _s, fired2 = self._seq()
        seq2.start()
        clock2.t = 101.0
        seq2.tick()
        seq2.abort()
        clock2.t = 106.0
        seq2.tick()
        self.assertEqual(fired2, [])
        self.assertEqual(log2.count(('stop',)), 1)
        self.assertNotIn(('load',), log2)


# ----------------------------------------------------------------------
# PlayerHost の流れ(偽の PlayerCore)
# ----------------------------------------------------------------------
def _fake_core_class():
    from PySide6.QtWidgets import QWidget

    class FakeGpw(QWidget):
        def __init__(self):
            super().__init__()
            self.view_only = None
            self.resize(640, 360)

        def set_view_only(self, on):
            self.view_only = on

        def fit_view_only(self, w, h):
            self.resize(min(w, int(h * 16 / 9)), min(h, int(w * 9 / 16)))

    class FakeDock:
        def __init__(self):
            self.volumes = []
            self.cancelled = 0
            self.game_preview_window = FakeGpw()
            self.game_screen = QWidget()
            self.chart_preview = type('C', (), {'reset_to_start': lambda s: None})()

        def set_volume(self, v):
            self.volumes.append(round(v, 4))

        def set_master_volume(self, v):
            pass

        def cancel_predecode(self):
            self.cancelled += 1

    class FakeCore:
        instances = []

        def __init__(self, cfg):
            self.dock = FakeDock()
            self.log = []
            self.ready = False
            FakeCore.instances.append(self)

        def play_demo(self, path, demo):
            self.log.append('demo')

        def stop_audio(self):
            self.log.append('stop')

        def shutdown(self):
            self.log.append('shutdown')

        def predecode(self, path):
            self.log.append('predecode')

        def load(self, path, course_key=None, play_mode=None):
            self.log.append(('load', course_key, play_mode))
            return True

        def chart_ready(self):
            return self.ready

        def play_chart(self, at=0.0):
            self.log.append(('play_chart', at))
    return FakeCore


class HostIntroTests(unittest.TestCase):
    def setUp(self):
        from neotja.player.host import PlayerHost
        self.FakeCore = _fake_core_class()
        self._orig = PlayerHost.core_factory
        PlayerHost.core_factory = self.FakeCore

    def tearDown(self):
        from neotja.player.host import PlayerHost
        PlayerHost.core_factory = self._orig

    def _wait(self, ms):
        from PySide6.QtTest import QTest
        end = time.monotonic() + ms / 1000.0
        while time.monotonic() < end:
            QTest.qWait(10)

    def _host(self, d):
        from neotja.player.host import PlayerHost
        a = _chart(Path(d) / 'a', 'a')
        host = PlayerHost({'master_volume': 0.0, 'preview_volume': 0.5}, lambda: None)
        host.resize(1600, 900)
        host.show()
        host.on_shown()
        host.library.choose(a)
        return host, a

    @real_qt_test
    def test_play_shows_unskippable_intro_then_plays(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as d:
            host, a = self._host(d)
            core = self.FakeCore.instances[0]
            core.log.clear()
            host.difficulty.btn_play.click()
            self.assertIs(host.body.currentWidget(), host.intro)
            self.assertEqual(core.log, ['predecode'])            # 試聴は止めない
            seq = host._seq
            clock = Clock()
            seq._clock = clock
            seq._t0 = clock.t
            # 何を押しても進まない(Esc も)。
            for key in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Space):
                QTest.keyClick(host.intro, key)
            QTest.mouseClick(host.intro, Qt.LeftButton, pos=QPoint(400, 300))
            clock.t += 3.5
            seq.tick()
            self.assertIs(host.body.currentWidget(), host.intro)
            self.assertFalse(seq.done)
            self.assertEqual(core.dock.volumes[-1], 0.25)        # 0.5 の半分
            clock.t += 1.6                                       # 5.1 秒
            seq.tick()
            self.assertIn('stop', core.log)
            self.assertIn(('load', 'Oni', 'watch'), [(x[0], x[1], str(x[2]).lower()) if isinstance(x, tuple) and x[0] == 'load' else x for x in core.log])
            self.assertIs(host.body.currentWidget(), host.intro)   # まだ読めていない
            core.ready = True
            clock.t += 0.4
            seq.tick()
            self._wait(700)                                      # 黒を挟む切り替え
            self.assertIs(host.body.currentWidget(), host.play_page)
            self.assertTrue(core.dock.game_preview_window.view_only)
            self.assertIs(core.dock.game_preview_window.parentWidget(), host.play_page)
            self.assertEqual(core.log[-1], ('play_chart', 0.0))
            self.assertFalse(host.top_bar.isVisible())
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_leaving_during_intro_stops_everything(self):
        with tempfile.TemporaryDirectory() as d:
            host, a = self._host(d)
            core = self.FakeCore.instances[0]
            host.difficulty.btn_play.click()
            seq = host._seq
            core.log.clear()
            host.stop_preview()                       # Player を出た
            self.assertTrue(seq.aborted)
            self.assertIn('stop', core.log)
            self.assertGreaterEqual(core.dock.cancelled, 1)
            self.assertIsNone(host._seq)
            seq.tick()
            self._wait(200)
            self.assertNotIn(('play_chart', 0.0), core.log)
            # 窓を閉じたときも同じ道で止まる。
            host.difficulty.btn_play.click() if host.body.currentWidget() is host.difficulty else None
            host.shutdown()
            self.assertIn('shutdown', core.log)
            host.deleteLater()


if __name__ == '__main__':
    unittest.main()
