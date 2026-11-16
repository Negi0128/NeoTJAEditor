"""通常プレイの操作の締め切り・Esc の途中メニュー・譜面の終わり。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_pause.py`
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-pause-test-')
_CFG = Path(_TMP) / 'settings.json'
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import editor_session, open_test_chart, real_qt_test  # noqa: E402


def _wait(ms):
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QTest.qWait(10)
        time.sleep(0.002)


def _key(widget, key, release=False):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    ev = QKeyEvent(QEvent.KeyRelease if release else QEvent.KeyPress, key, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)
    return ev


def _wheel(widget, dy):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(0, 0), QPoint(0, dy),
                     Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    widget.wheelEvent(ev)
    return ev


def _all_keys():
    from PySide6.QtCore import Qt
    keys = [Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter, Qt.Key_F, Qt.Key_J, Qt.Key_D,
            Qt.Key_K, Qt.Key_S, Qt.Key_L, Qt.Key_Left, Qt.Key_Right, Qt.Key_Up,
            Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End,
            Qt.Key_Z, Qt.Key_C, Qt.Key_P, Qt.Key_Q, Qt.Key_Tab, Qt.Key_F11]
    keys += [getattr(Qt, 'Key_%d' % i) for i in range(10)]
    return keys


BODY = '\n'.join(['1000,'] * 12)


# ----------------------------------------------------------------------
# 共通部品(GamePreviewWindow / レーン / ゲーム画面)の締め切り
# ----------------------------------------------------------------------
class ControlsLockTests(unittest.TestCase):
    @real_qt_test
    def test_locked_blocks_keys_wheel_clicks_and_editor_default_is_unlocked(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        with editor_session() as (win, root):
            open_test_chart(win, root, BODY, bpm=120)
            win._force_update()
            pd = win.preview_dock
            lane, gpw, gs = pd.chart_preview, pd.game_preview_window, pd.game_screen
            # Editor は締めない(既定)。
            self.assertFalse(gpw.controls_locked())
            self.assertFalse(lane.controls_locked())
            lane.scroll_to_seconds(4.0)
            _wait(400)
            calls = []
            lane._seek_seconds_cb = lambda t: calls.append(('seek', t))
            lane._play_cb = lambda: calls.append(('play',))
            lane._pause_cb = lambda: calls.append(('pause',))
            lane._set_speed_cb = lambda r: calls.append(('speed', r))
            lane._fadein_play_cb = lambda t: calls.append(('fadein', t))
            got = []
            gpw.set_controls_locked(True, lambda ev: got.append(ev.key()))
            self.assertTrue(lane.controls_locked())
            pos0 = lane.scroll_target_time()
            cs0 = lane._constant_speed
            cps0 = list(lane._checkpoints)
            for target in (lane, gs, gpw.scaled_host):
                for k in _all_keys():
                    _key(target, k)
                    _key(target, k, release=True)
            for target in (lane, gs, gpw):
                for dy in (120, -120, 360):
                    _wheel(target, dy)
            QTest.mouseClick(gs, Qt.LeftButton, pos=QPoint(400, 200))
            QTest.mouseClick(lane, Qt.LeftButton, pos=QPoint(100, 50))
            _wait(300)
            self.assertEqual(calls, [])
            self.assertAlmostEqual(lane.scroll_target_time(), pos0, places=6)
            self.assertEqual(lane._constant_speed, cs0)        # Tab(等速)も効かない
            self.assertEqual(lane._checkpoints, cps0)          # P も効かない
            self.assertNotEqual(lane._state, 'playing')
            self.assertFalse(gpw.is_fullscreen())             # F11 も効かない
            self.assertTrue(all(not sc.isEnabled() for sc in gpw._own_shortcuts))
            _key(lane, Qt.Key_Escape)
            self.assertIn(Qt.Key_Escape, got)                  # キーは置いた側へ回る
            self.assertIn(Qt.Key_Space, got)
            # 外すと元どおり(Editor の動き)。
            gpw.set_controls_locked(False)
            self.assertTrue(all(sc.isEnabled() for sc in gpw._own_shortcuts))
            _key(lane, Qt.Key_Space)
            self.assertIn(('play',), calls)
            calls.clear()
            lane._state = 'paused'
            _wheel(lane, 120)
            _wait(300)
            self.assertTrue(any(c[0] == 'seek' for c in calls))


# ----------------------------------------------------------------------
# PlayerHost の流れ(偽の PlayerCore)
# ----------------------------------------------------------------------
def _chart(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'song.ogg').write_bytes(b'x')
    p = folder / (name + '.tja')
    p.write_text('TITLE:%s\nBPM:120\nWAVE:song.ogg\nDEMOSTART:3\n'
                 'COURSE:Oni\nLEVEL:9\n#START\n1111,\n#END\n'
                 'COURSE:Edit\nLEVEL:10\n#START\n1111,\n#END\n' % name, encoding='cp932')
    return str(p)


def _fake_core_class():
    from PySide6.QtWidgets import QWidget

    class FakeGpw(QWidget):
        def __init__(self):
            super().__init__()
            self.view_only = None
            self.locks = []
            self.resize(640, 360)

        def set_view_only(self, on):
            self.view_only = on

        def set_controls_locked(self, on, key_cb=None):
            self.locks.append(bool(on))
            self.key_cb = key_cb

        def fit_view_only(self, w, h):
            self.resize(min(w, int(h * 16 / 9)), min(h, int(w * 9 / 16)))

    class FakeDock:
        def __init__(self):
            self.game_preview_window = FakeGpw()
            self.game_screen = QWidget()
            self.chart_preview = type('C', (), {'reset_to_start': lambda s: None})()

        def set_volume(self, v):
            pass

        def set_master_volume(self, v):
            pass

        def cancel_predecode(self):
            pass

    class FakeCore:
        instances = []

        def __init__(self, cfg):
            self.dock = FakeDock()
            self.log = []
            self.ready = True
            self.finished = False
            self.pos = 0.0
            self.playing = False
            FakeCore.instances.append(self)

        def play_demo(self, path, demo):
            self.log.append('demo')

        def stop_audio(self):
            self.log.append('stop')
            self.playing = False

        def shutdown(self):
            self.log.append('shutdown')

        def predecode(self, path):
            self.log.append('predecode')

        def load(self, path, course_key=None, play_mode=None):
            self.log.append(('load', course_key))
            return True

        def use_normal_play_mode(self):
            pass

        def chart_ready(self):
            return self.ready

        def play_chart(self, at=0.0):
            self.log.append(('play_chart', at))
            self.playing = True

        def pause_chart(self):
            self.log.append('pause_chart')
            self.playing = False

        def resume_chart(self):
            self.log.append('resume_chart')
            self.playing = True

        def chart_finished(self):
            return self.finished

        def drop_hold(self):
            self.log.append('drop_hold')

        def use_detail_mode(self):
            self.log.append('detail_mode')
            return True
    return FakeCore


class HostPauseTests(unittest.TestCase):
    def setUp(self):
        from neotja.player.host import PlayerHost
        self.FakeCore = _fake_core_class()
        self._orig = PlayerHost.core_factory
        PlayerHost.core_factory = self.FakeCore

    def tearDown(self):
        from neotja.player.host import PlayerHost
        PlayerHost.core_factory = self._orig

    def _playing_host(self, d, course='Oni'):
        """選曲 → 難易度選択 → 再生画面まで(曲名表示は飛ばして直接入る)。"""
        from neotja.player.host import PlayerHost
        a = _chart(Path(d) / 'a', 'a')
        host = PlayerHost({'master_volume': 0.0, 'preview_volume': 0.5}, lambda: None)
        host.resize(1600, 900)
        host.show()
        host.on_shown()
        host.library.choose(a)
        self.sounds = []
        host.sounds.play = lambda kind: self.sounds.append(kind)
        host.sounds.pi = lambda: self.sounds.append('pi')
        core = self.FakeCore.instances[-1]
        host._enter_play(a, course)
        _wait(700)
        self.assertIs(host.body.currentWidget(), host.play_page)
        self.assertEqual(core.log[-1], ('play_chart', 0.0))
        core.log.clear()
        return host, a, core

    @real_qt_test
    def test_normal_play_locks_and_only_esc_reacts(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._playing_host(d)
            gpw = core.dock.game_preview_window
            self.assertEqual(gpw.locks[-1], True)
            self.assertIsNotNone(gpw.key_cb)
            for k in _all_keys():
                QTest.keyClick(host.play_page, k)
            _wheel(host.play_page, 120)
            self.assertEqual(core.log, [])
            self.assertFalse(host.play_page.menu_open())
            # 締めたレーン経由で来た Esc もメニューを開く。
            gpw.key_cb(_key(QWidget_dummy(), Qt.Key_Escape))
            self.assertTrue(host.play_page.menu_open())
            self.assertEqual(core.log, ['pause_chart'])
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_esc_pauses_and_continue_resumes(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._playing_host(d)
            QTest.keyClick(host.play_page, Qt.Key_Escape)
            menu = host.play_page.menu
            self.assertTrue(menu.is_open())
            self.assertEqual(core.log, ['pause_chart'])
            self.assertFalse(core.playing)
            self.assertEqual([it.text() for it in menu.items],
                             ['演奏を続ける', '初めからやり直す', '詳細モードに移行する',
                              '選曲画面にもどる'])
            self.assertEqual(menu.current, 0)
            self.assertIn('pi', self.sounds)
            # 2回目の Esc は「続ける」。
            QTest.keyClick(menu, Qt.Key_Escape)
            self.assertFalse(menu.is_open())
            self.assertEqual(core.log, ['pause_chart', 'resume_chart'])
            self.assertTrue(core.playing)
            self.assertTrue(host.in_play())
            # ↑↓ + Enter、数字キー、クリック。
            QTest.keyClick(host.play_page, Qt.Key_Escape)
            n = len(self.sounds)
            QTest.keyClick(menu, Qt.Key_Down)
            self.assertEqual(menu.current, 1)
            QTest.keyClick(menu, Qt.Key_Down)
            self.assertEqual(menu.current, 2)
            QTest.keyClick(menu, Qt.Key_Down)
            self.assertEqual(menu.current, 3)
            QTest.keyClick(menu, Qt.Key_Down)
            self.assertEqual(menu.current, 0)          # 下の端から上へ回る
            QTest.keyClick(menu, Qt.Key_Up)
            self.assertEqual(menu.current, 3)
            self.assertGreaterEqual(len(self.sounds) - n, 5)
            QTest.keyClick(menu, Qt.Key_Down)
            QTest.keyClick(menu, Qt.Key_Return)
            self.assertFalse(menu.is_open())
            self.assertEqual(core.log[-1], 'resume_chart')
            QTest.keyClick(host.play_page, Qt.Key_Escape)
            QTest.keyClick(menu, Qt.Key_1)
            self.assertFalse(menu.is_open())
            QTest.keyClick(host.play_page, Qt.Key_Escape)
            QTest.mouseClick(menu.items[0], Qt.LeftButton)
            self.assertFalse(menu.is_open())
            self.assertEqual(core.log.count('resume_chart'), core.log.count('pause_chart'))
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_restart_goes_back_to_intro_and_resets(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._playing_host(d, course='Edit')
            QTest.keyClick(host.play_page, Qt.Key_Escape)
            QTest.keyClick(host.play_page.menu, Qt.Key_2)
            self.assertFalse(host.play_page.menu_open())
            self.assertIs(host.body.currentWidget(), host.intro)
            self.assertIsNotNone(host._seq)               # 5 秒の曲名表示から
            self.assertFalse(host.in_play())
            self.assertIn('stop', core.log)
            self.assertEqual(core.dock.game_preview_window.locks[-1], False)
            # 5 秒たったら同じコースで読み直して流す(集計は読み直しで初めから)。
            seq = host._seq
            seq._t0 -= 6.0
            seq.tick()
            seq.tick()
            _wait(700)
            self.assertIn(('load', 'Edit'), core.log)
            self.assertIs(host.body.currentWidget(), host.play_page)
            self.assertEqual(core.log[-1], ('play_chart', 0.0))
            self.assertTrue(host.in_play())
            self.assertEqual(core.dock.game_preview_window.locks[-1], True)
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_back_to_library_stops_and_keeps_library_state(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._playing_host(d)
            lib_page = host.library.pages.currentWidget()
            QTest.keyClick(host.play_page, Qt.Key_Escape)
            menu = host.play_page.menu
            for _ in range(3):
                QTest.keyClick(menu, Qt.Key_Down)
            QTest.keyClick(menu, Qt.Key_Return)
            _wait(700)
            self.assertIs(host.body.currentWidget(), host.library)
            self.assertIs(host.library.pages.currentWidget(), lib_page)
            self.assertIn('stop', core.log)
            self.assertFalse(core.playing)
            self.assertFalse(host.in_play())
            self.assertTrue(host.top_bar.isVisible())
            self.assertEqual(core.dock.game_preview_window.locks[-1], False)
            # 選曲へ戻ったあと、終わりの見張りは動いていない。
            core.finished = True
            _wait(300)
            self.assertIs(host.body.currentWidget(), host.library)
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_end_of_chart_emits_and_goes_to_results(self):
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._playing_host(d, course='Edit')
            done = []
            host.play_finished.connect(lambda p, c: done.append((p, c)))
            _wait(300)
            self.assertEqual(done, [])
            # 止めているあいだは終わりにならない。
            host.open_pause_menu()
            core.finished = True
            _wait(300)
            self.assertEqual(done, [])
            host._on_menu('resume')
            _wait(900)
            self.assertEqual(done, [(a, 'Edit')])
            self.assertIs(host.body.currentWidget(), host.results)   # 結果画面へ
            self.assertEqual(host.results.course, 'Edit')
            self.assertIn('stop', core.log)
            self.assertNotIn('demo', core.log)
            self.assertFalse(host.in_play())
            host.shutdown()
            host.deleteLater()


def QWidget_dummy():
    from PySide6.QtWidgets import QWidget
    return QWidget()


# ----------------------------------------------------------------------
# 本物の PlayerCore: 止めると音源・時計がまとめて止まり、同じ所から続く
# ----------------------------------------------------------------------
class RealCorePauseTests(unittest.TestCase):
    @real_qt_test
    def test_pause_freezes_audio_and_clock_resume_continues(self):
        from neotja.player.core import PlayerCore
        with editor_session() as (win, root):
            text = open_test_chart(win, root, BODY, bpm=120)
            del text
            cfg = dict(win.config_data)
            cfg.update(master_volume=0.0, gpu_render=False)
            core = PlayerCore(cfg)
            try:
                self.assertTrue(core.load(str(root / 'test.tja'), course_key='Oni'))
                for _ in range(300):
                    if core.chart_ready():
                        break
                    _wait(20)
                self.assertTrue(core.chart_ready())
                core.play_chart(0.0)
                _wait(600)
                self.assertTrue(core.chart_playing())
                core.pause_chart()
                _wait(100)
                lane = core.dock.chart_preview
                p1 = core.chart_position()
                t1 = lane._current_audio_time()
                _wait(500)
                self.assertFalse(core.chart_playing())
                self.assertAlmostEqual(core.chart_position(), p1, delta=0.01)
                self.assertAlmostEqual(lane._current_audio_time(), t1, delta=0.01)
                self.assertNotEqual(lane._state, 'playing')
                self.assertFalse(core.chart_finished())
                core.resume_chart()
                _wait(300)
                self.assertTrue(core.chart_playing())
                p2 = core.chart_position()
                self.assertGreater(p2, p1)
                self.assertLess(p2, p1 + 0.8)             # 頭へ戻っていない
            finally:
                core.stop_audio()
                core.shutdown()
                core.dock.chart_preview._timer.stop()
                core.dock.game_preview_window.hide()
                core.dock.game_preview_window.close()
                core.dock.deleteLater()


if __name__ == '__main__':
    unittest.main()
