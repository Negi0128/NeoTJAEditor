# 録画の背景で「なし」を選んでも、画面があればサムネを PNG で保存できる。
# 利用者の報告 2026-11-16: 背景なしだと「この画面をPNGで保存…」が押せない。
# まとめ録画(画面が無い)は今までどおり、背景かジャケットが要る。
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from neotja import settings as S

_tmp = None
_saved = None


def setUpModule():
    global _tmp, _saved
    _tmp = tempfile.mkdtemp(prefix="neotja_test_")
    sp = Path(_tmp) / "settings.json"
    sp.write_text(json.dumps({"master_volume": 0.0}), encoding="utf-8")
    _saved = (S.settings_path, S._primary_settings_path,
              S._fallback_settings_path)
    S.settings_path = lambda: sp
    S._primary_settings_path = lambda: sp
    S._fallback_settings_path = lambda: sp


def tearDownModule():
    (S.settings_path, S._primary_settings_path,
     S._fallback_settings_path) = _saved
    shutil.rmtree(_tmp, ignore_errors=True)


class PlainPngTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, make_widget):
        from neotja import recorder
        from neotja.dialogs.record_bg_dialog import RecordBackgroundDialog
        d = RecordBackgroundDialog(None, make_widget, 10.0,
                                   recorder.RecordBackground())
        self.addCleanup(d.close)
        self.addCleanup(setattr, d, "_closed", True)
        return d

    def test_no_bg_with_screen_can_save(self):
        from neotja.chart_preview_widget import ChartPreviewWidget
        from neotja.game_screen import GameScreenWidget
        cp = ChartPreviewWidget()
        cp.game_state = lambda: (0.0, 0, None)
        gs = GameScreenWidget(cp)
        gs.resize(1280, 720)
        gs.set_chart({}, course_key="oni")
        self.addCleanup(gs.close)
        d = self._dialog(lambda: gs)
        d._sync_enabled()
        self.assertTrue(d.btn_png.isEnabled())
        self.assertTrue(d.sl_at.isEnabled())
        img, msg = d._frame()
        self.assertIsNotNone(img)
        self.assertEqual((img.width(), img.height()), (1280, 720))

    def test_no_bg_without_screen_keeps_message(self):
        d = self._dialog(lambda: None)
        d._sync_enabled()
        self.assertFalse(d.btn_png.isEnabled())
        img, msg = d._frame()
        self.assertIsNone(img)
        self.assertIn("背景かジャケット", msg)


if __name__ == "__main__":
    unittest.main()
