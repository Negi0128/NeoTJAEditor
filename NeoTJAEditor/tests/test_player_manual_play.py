"""Player で自分で叩く: オート演奏 OFF なら演奏モードで読み、D F J K を打面へ。
プレイリストはいつもオート。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_manual_play.py`
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-manual-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402


class ManualPlayTests(unittest.TestCase):
    @real_qt_test
    def test_mode_and_pads(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from neotja.constants import KIND_DON, KIND_KA, PLAY_MODE_PLAY, PLAY_MODE_WATCH
        from neotja.player.host import PlayerHost
        loads = []

        class Core:
            def __init__(self, cfg):
                self.dock = type('D', (), {'cancel_predecode': lambda s: None})()

            def load(self, path, course_key=None, play_mode=None):
                loads.append(play_mode)
                return True

            def __getattr__(self, name):
                return lambda *a, **k: None

        orig = PlayerHost.core_factory
        PlayerHost.core_factory = Core
        try:
            cfg = {'master_volume': 0.0}
            host = PlayerHost(cfg, lambda: None)
            host._ensure_core()
            self.assertFalse(host.manual_play())            # 既定はオート
            host._begin_load('x.tja', 'Oni')
            cfg['player_auto'] = False
            self.assertTrue(host.manual_play())
            host._begin_load('x.tja', 'Oni')
            host._pl_session = {'index': 0}                 # プレイリストはいつもオート
            self.assertFalse(host.manual_play())
            host._begin_load('x.tja', 'Oni')
            self.assertEqual(loads, [PLAY_MODE_WATCH, PLAY_MODE_PLAY, PLAY_MODE_WATCH])
            host._pl_session = None
            # 打面: D=左の縁 F=左の面 J=右の面 K=右の縁。
            got = []
            page = host.play_page
            page.pad_cb = lambda kind, side: got.append((kind, side))
            for k in (Qt.Key_D, Qt.Key_F, Qt.Key_J, Qt.Key_K, Qt.Key_A):
                QTest.keyClick(page, k)
            self.assertEqual(got, [(KIND_KA, 0), (KIND_DON, 0), (KIND_DON, 1), (KIND_KA, 1)])
            host.shutdown()
            host.deleteLater()
        finally:
            PlayerHost.core_factory = orig


class MissNoPopupTests(unittest.TestCase):
    def test_passed_note_counts_bad_but_no_judge_text(self):
        from neotja.player.play_state import PlayState
        st = PlayState({'notes': [(1.0, '1'), (2.0, '1')]})
        st.advance(1.5)
        self.assertEqual(st.bad, 1)
        self.assertIsNone(st.last_judge)                    # 見送りでは「不可」を出さない

class RightEdgeTests(unittest.TestCase):
    def test_window_reaches_past_right_edge_by_note_radius(self):
        """右端から半分だけ見えている音符も拾う(急に現れない)。"""
        from neotja.chart_preview_widget import ChartPreviewWidget as W

        class Fake:
            NOTE_R_BIG = W.NOTE_R_BIG
            _min_vis_speed = 400.0
        _, fut = W._visible_window(Fake(), 10.0, 1000, 100)
        # 中心が右端から大音符の半径ぶん外にある音符の時刻まで入っている。
        self.assertGreaterEqual(fut, 10.0 + (1000 - 100 + W.NOTE_R_BIG) / 400.0)

class LagOffsetTests(unittest.TestCase):
    def test_values(self):
        from neotja.player.core import input_lag_sec, player_offset_ms
        self.assertEqual(input_lag_sec({}), 0.0)
        self.assertAlmostEqual(input_lag_sec({'player_input_lag': 30}), 0.030)
        self.assertEqual(player_offset_ms({'player_global_offset': -15}), -15.0)

    @real_qt_test
    def test_steppers_in_panel_and_saved(self):
        from neotja.player.difficulty import DifficultyPage
        cfg = {'master_volume': 0.0}
        saves = []
        page = DifficultyPage(cfg, lambda: saves.append(1))
        page.settings.set_values(cfg)
        st = page.settings.steppers
        self.assertIn('player_input_lag', st)
        self.assertIn('player_global_offset', st)
        st['player_input_lag'].step(-1)
        st['player_global_offset'].step(1)
        page._flush_number_save() if hasattr(page, '_flush_number_save') else None
        self.assertEqual(cfg['player_input_lag'], -1)
        self.assertEqual(cfg['player_global_offset'], 1)
        page.deleteLater()

    def test_press_time_minus_lag(self):
        from neotja.chart_preview_widget import ChartPreviewWidget as W

        class Fake:
            input_lag_sec = 0.05
            _play_state = object()
            _chord_timer = None
            def _current_chart_time(self):
                return 2.0
        f = Fake()
        f._pending_presses = [(0, 0, 0)]          # 2つ目なのでタイマーは起こさない
        W._queue_press(f, 'don', 0)
        self.assertAlmostEqual(f._pending_presses[-1][0], 1.95)

class ScoreTests(unittest.TestCase):
    def test_good_ok_bad_points_and_gain_events(self):
        from neotja.player.play_state import PlayState
        st = PlayState({'notes': [(1.0, '1'), (2.0, '1'), (3.0, '1')]})
        per = st._per_note
        st.press(1.0, 'don')                 # 良
        st.press(2.05, 'don')                # 可
        st.press(3.1, 'don')                 # 不可
        self.assertEqual((st.good, st.ok, st.bad), (1, 1, 1))
        self.assertEqual(st.score, per + per // 2 // 10 * 10)
        self.assertEqual([g for _t, g in st.gains_in(0, 9)], [per, per // 2 // 10 * 10])

class WheelTests(unittest.TestCase):
    @real_qt_test
    def test_wheel_does_not_change_settings(self):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtWidgets import QApplication
        from neotja.player.difficulty import DifficultyPage
        cfg = {'master_volume': 0.5}
        page = DifficultyPage(cfg, lambda: None)
        page.settings.set_values(cfg)
        widgets = list(page.settings.steppers.values()) + list(page.settings.volume_sliders.values())             if hasattr(page.settings, 'volume_sliders') else list(page.settings.steppers.values())
        for w in widgets:
            before = w.value()
            ev = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
                             Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
            QApplication.sendEvent(w, ev)
            self.assertEqual(w.value(), before)
        page.deleteLater()


if __name__ == '__main__':
    unittest.main()
