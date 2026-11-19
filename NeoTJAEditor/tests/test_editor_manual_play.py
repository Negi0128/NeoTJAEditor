"""Editor: F1 でオート ON/OFF。OFF なら演奏モードで D F J K を判定、作譜モードでは叩けない。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_editor_manual_play.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_support import editor_session, real_qt_test  # noqa: E402  (設定は一時ファイルへ)


class EditorAutoTests(unittest.TestCase):
    @real_qt_test
    def test_f1_toggles_auto_and_pads_only_outside_edit(self):
        with editor_session() as (win, _root):
            dock = win.preview_dock
            dock._last_preview_data = {'notes': [(1.0, '1'), (2.0, '2')]}
            self.assertTrue(dock.editor_auto)
            self.assertIsNone(dock.chart_preview.play_state())
            win._on_f1()
            self.assertFalse(dock.editor_auto)
            self.assertIsNotNone(dock.chart_preview.play_state())
            self.assertEqual(dock.hit_sounds_schedule_len() if hasattr(dock, 'hit_sounds_schedule_len') else 0, 0)
            dock.set_bottom_mode(dock.MODE_EDIT)
            self.assertFalse(dock.chart_preview.pads_enabled)    # 作譜では叩けない
            dock.set_bottom_mode(dock.MODE_TITLE)
            self.assertTrue(dock.chart_preview.pads_enabled)
            self.assertEqual(dock.MODE_DISPLAY['通常再生'], '演奏モード')
            win._on_f1()
            self.assertTrue(dock.editor_auto)
            self.assertIsNone(dock.chart_preview.play_state())

class KeyTests(unittest.TestCase):
    @real_qt_test
    def test_keys_stopped_vs_playing(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with editor_session() as (win, _root):
            dock = win.preview_dock
            cp = dock.chart_preview
            dock.set_bottom_mode(dock.MODE_TITLE)   # 演奏モード
            dock._last_preview_data = {'notes': [(1.0, '1')]}
            win._on_f1()
            calls = []
            cp._queue_press = lambda *a: calls.append(('pad',) + a)
            cp.play_from_current_measure = lambda **k: calls.append('play')
            cp._measure_key = lambda d: calls.append(('measure', d))
            cp.pause = lambda: calls.append('pause')
            cp._feedback = lambda *a: None
            cp._state = 'stopped'
            for k in (Qt.Key_F, Qt.Key_D, Qt.Key_K):
                QTest.keyClick(cp, k)
            self.assertEqual(calls, ['play', ('measure', -1), ('measure', 1)])
            calls.clear()
            cp._state = 'playing'
            for k in (Qt.Key_D, Qt.Key_F, Qt.Key_J, Qt.Key_K, Qt.Key_Space):
                QTest.keyClick(cp, k)
            self.assertEqual([c[0] if isinstance(c, tuple) else c for c in calls],
                             ['pad', 'pad', 'pad', 'pad', 'pause'])
            cp._state = 'stopped'


if __name__ == '__main__':
    unittest.main()
