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
            # F1 の項目も出さない(F1 キーは打音の切替)。
            self.assertFalse([t for t in texts if 'NeoTJAPlayer' in t or t.startswith('F1')],
                             texts)
            self.assertEqual(sorted(win._run_actions), ['F2', 'F3'])
            # 古い設定にある run_config["F1"] はそのまま読めている。
            self.assertIn('F1', win.config_data['run_config'])
            for name in ('launch_player', '_offer_player_download',
                         '_find_player', 'check_player_pending'):
                self.assertFalse(hasattr(win, name), name)

    @real_qt_test
    def test_f1_only_toggles_hit_sounds(self):
        from unittest.mock import patch
        with editor_session() as (win, _root):
            with patch.object(win.preview_dock, 'toggle_hit_sounds') as toggle, \
                 patch.object(win.preview_dock, 'set_game_preview_visible') as show:
                win._on_f1()
                toggle.assert_called_once_with()
                show.assert_not_called()


if __name__ == '__main__':
    unittest.main()
