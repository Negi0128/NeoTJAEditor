"""v15.5.0: Player は鍵を掛けて配る。環境設定「Player（実験的）」で開ける。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_lock.py`
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_support import editor_session, real_qt_test  # noqa: E402

SHOT = os.environ.get('NEOTJA_SHOT')


class LockTests(unittest.TestCase):
    @real_qt_test
    def test_locked_launcher_does_nothing_until_unlocked(self):
        from neotja import settings
        self.assertFalse(settings.default_settings()['player_unlocked'])
        with editor_session() as (win, _root):
            win.config_data['player_unlocked'] = False
            win.start_screen.set_player_locked(True)
            win.show()
            if SHOT:
                win.resize(1600, 900)
                from PySide6.QtTest import QTest
                QTest.qWait(200)
                win.start_screen.grab().save(SHOT)
            self.assertTrue(win.start_screen.player_locked)
            self.assertFalse(win.start_screen.btn_player.icon().isNull())
            win.start_screen.btn_player.click()
            self.assertFalse(win.is_player_visible())
            win._show_player()                      # ほかの道からも入れない
            self.assertFalse(win.is_player_visible())
            win.config_data['player_unlocked'] = True
            win.start_screen.set_player_locked(False)
            self.assertTrue(win.start_screen.btn_player.icon().isNull())
            win.start_screen.btn_player.click()
            self.assertTrue(win.is_player_visible())
            win._leave_player()


if __name__ == '__main__':
    unittest.main()
