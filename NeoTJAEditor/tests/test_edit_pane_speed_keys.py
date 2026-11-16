# 作譜ペインでも z / c で再生速度が 1 段階変わる(レーンと同じ)。
# 利用者の報告 2026-11-16。Ctrl+Z / Ctrl+C は取り消し・コピーのまま。
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from neotja import settings as S


def _key(widget, key, mods=None):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    ev = QKeyEvent(QEvent.KeyPress, key, mods or Qt.NoModifier)
    widget.keyPressEvent(ev)


class PaneKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_z_c_call_cb_and_ctrl_does_not(self):
        from PySide6.QtCore import Qt
        from neotja.chart_edit_widget import ChartEditWaveform
        got = []
        ce = ChartEditWaveform(show_demo=False, show_stereo=False,
                               step_speed_cb=got.append)
        self.addCleanup(ce.deleteLater)
        _key(ce, Qt.Key_Z)
        _key(ce, Qt.Key_C)
        _key(ce, Qt.Key_Z, Qt.ControlModifier)
        _key(ce, Qt.Key_C, Qt.ControlModifier)
        _key(ce, Qt.Key_Z, Qt.AltModifier)
        self.assertEqual(got, [-1, 1])

    def test_lane_step_speed_key_respects_play_mode(self):
        from neotja.chart_preview_widget import (ChartPreviewWidget,
                                                  SPEED_STEPS)
        lane = ChartPreviewWidget()
        self.addCleanup(lane.deleteLater)
        lane.set_playback_rate(1.0)
        i = SPEED_STEPS.index(1.0)
        lane.step_speed_key(-1)
        self.assertEqual(lane._playback_rate, SPEED_STEPS[max(0, i - 1)])
        self.assertIn("再生速度", lane._toast_text)
        lane.set_playback_rate(1.0)
        lane._play_state = object()            # 演奏モード中は変えない
        try:
            lane.step_speed_key(-1)
        finally:
            lane._play_state = None
        self.assertEqual(lane._playback_rate, 1.0)


class DockWiringTests(unittest.TestCase):
    def test_pane_is_wired_to_lane(self):
        from gui_support import editor_session, real_qt_test
        from PySide6.QtCore import Qt
        tmp = tempfile.TemporaryDirectory(prefix="neotja_test_")
        self.addCleanup(tmp.cleanup)
        sp = Path(tmp.name) / "settings.json"
        cfg = S.default_settings()
        cfg.update(master_volume=0.0)
        import json
        sp.write_text(json.dumps(cfg), encoding="utf-8")

        @real_qt_test
        def run():
            with patch.object(S, "_primary_settings_path", lambda: sp), \
                    editor_session() as (win, _root):
                lane = win.preview_dock.chart_preview
                ce = win.preview_dock.chart_edit
                calls = []
                with patch.object(lane, "step_speed_key", calls.append):
                    _key(ce, Qt.Key_C)
                    _key(ce, Qt.Key_Z)
                self.assertEqual(calls, [1, -1])
        run()


if __name__ == "__main__":
    unittest.main()
