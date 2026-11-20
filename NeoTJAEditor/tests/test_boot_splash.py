"""起動の札は、案内の窓を含めどの窓が出てもすぐ閉じる(札が案内を隠さない)。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_boot_splash.py`
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_support import real_qt_test  # noqa: E402


class SplashTests(unittest.TestCase):
    @real_qt_test
    def test_first_window_closes_splash(self):
        from PySide6.QtWidgets import QApplication, QMessageBox
        closed = []
        sys.modules['pyi_splash'] = types.SimpleNamespace(close=lambda: closed.append(1))
        try:
            import neotja.__main__ as m
            closer = m._SplashCloser(QApplication.instance())
            box = QMessageBox()
            box.setText('素材が見つかりません')
            box.show()
            QApplication.processEvents()
            self.assertEqual(closed, [1])
            box.close()
            del closer
        finally:
            del sys.modules['pyi_splash']


if __name__ == '__main__':
    unittest.main()
