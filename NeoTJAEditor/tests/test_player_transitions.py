"""画面の切り替え(FadeStack): 初めて出す画面も、最初に塗る時点で大きさと
配置が決まっている(結果画面のボタンが一瞬ずれて見えない)。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_transitions.py`
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-transitions-test-')
_CFG = Path(_TMP) / 'settings.json'
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402


def _wait(ms):
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QTest.qWait(10)
        time.sleep(0.002)


def _spy_results():
    """最初に塗られた瞬間の大きさとボタンの位置を控える結果画面。"""
    from neotja.player.results import ResultsPage

    class Spy(ResultsPage):
        first = None

        def paintEvent(self, ev):
            if self.first is None:
                self.first = (self.size(), self.btn_retry.geometry(), self.btn_back.geometry())
            super().paintEvent(ev)
    return Spy({})


class FadeStackSizeTests(unittest.TestCase):
    @real_qt_test
    def test_first_paint_has_final_geometry(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QWidget
        from neotja.player.transitions import FadeStack
        for how, native in (('direct', False), ('cross', False), ('black', False),
                            ('direct', True), ('cross', True), ('black', True)):
            with self.subTest(how=how, native=native):
                stack = FadeStack()
                stack.resize(1600, 900)
                first = QWidget()
                stack.addWidget(first)
                if native:
                    # 再生画面が GL の窓(native)を持つと、兄弟の画面も OS の窓に
                    # なり、出した瞬間に塗られる(配置の前に1コマ出る)。
                    first.setAttribute(Qt.WA_NativeWindow)
                stack.show()
                _wait(50)
                page = _spy_results()
                if native:
                    page.setAttribute(Qt.WA_NativeWindow)
                stack.addWidget(page)
                if how == 'direct':
                    stack.setCurrentWidget(page)
                else:
                    stack.fade_to(page, mode=how, ms=120)
                _wait(400)
                self.assertIs(stack.currentWidget(), page)
                self.assertIsNotNone(page.first)
                size, retry, back = page.first
                self.assertEqual(size, stack.contentsRect().size())
                self.assertEqual(retry, page.btn_retry.geometry())
                self.assertEqual(back, page.btn_back.geometry())
                # 下に置かれている(既定の 640x480 の位置ではない。結果画面の
                # ボタンは左下へ移った)。
                self.assertGreater(retry.bottom(), 700)
                stack.close()
                stack.deleteLater()

    @real_qt_test
    def test_hidden_pages_follow_size_when_switched(self):
        from PySide6.QtWidgets import QWidget
        from neotja.player.transitions import FadeStack
        stack = FadeStack()
        stack.resize(1200, 700)
        first = QWidget()
        stack.addWidget(first)
        page = _spy_results()
        stack.addWidget(page)
        stack.show()
        _wait(50)
        stack.resize(1500, 850)        # 隠れているあいだに窓の大きさが変わる
        _wait(50)
        stack.fade_to(page, mode='cross', ms=60)
        _wait(200)
        self.assertEqual(page.first[0], stack.contentsRect().size())
        self.assertEqual(page.first[1], page.btn_retry.geometry())
        stack.close()
        stack.deleteLater()


if __name__ == '__main__':
    unittest.main()
