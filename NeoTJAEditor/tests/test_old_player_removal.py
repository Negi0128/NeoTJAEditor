"""旧 NeoTJAPlayer(別 exe)を外したことの確認(v15-design/PLAYER_REDESIGN.md 6章)。"""
import unittest

from gui_support import editor_session, real_qt_test


def _menu_texts(win, title):
    for action in win.menuBar().actions():
        if action.text() == title:
            return [a.text() for a in action.menu().actions()]
    raise AssertionError('menu not found: ' + title)


class EditorEntryPointTests(unittest.TestCase):
    @real_qt_test
    def test_run_menu_has_no_old_player(self):
        with editor_session() as (win, _root):
            texts = _menu_texts(win, '起動')
            self.assertNotIn('NeoTJAPlayer を別ウィンドウで開く', texts)
            self.assertNotIn('NeoTJAPlayer を入れ直す', texts)
            for name in ('launch_player', '_offer_player_download',
                         '_find_player', 'check_player_pending'):
                self.assertFalse(hasattr(win, name), name)


if __name__ == '__main__':
    unittest.main()
