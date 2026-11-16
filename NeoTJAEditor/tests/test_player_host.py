"""同居 Player(neotja/player/host.py)の入口・キーの遮断・保存済み楽曲の扱い。"""
import os
import tempfile
import unittest
from pathlib import Path

from gui_support import editor_session, real_qt_test


def _tja(path, title):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('TITLE:%s\nBPM:120\nCOURSE:Oni\nLEVEL:8\n#START\n1111,\n#END\n' % title,
                    encoding='cp932')
    return str(path)


class SavedListTests(unittest.TestCase):
    def test_dedupe_remove_relink_never_touch_files(self):
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory(prefix='neotja-player-saved-') as d:
            a = _tja(Path(d) / 'a' / 'a.tja', '曲A')
            b = _tja(Path(d) / 'b' / 'b.tja', '曲B')
            cfg = {}
            self.assertTrue(song_lists.add_saved(cfg, a))
            # 区切りと大文字小文字が違うだけの同じファイルは重ねない。
            self.assertFalse(song_lists.add_saved(cfg, a.upper().replace('\\', '/')))
            self.assertEqual(len(song_lists.saved(cfg)), 1)
            self.assertEqual(song_lists.saved(cfg)[0]['title'], '曲A')
            # 外しても元の TJA は残る。
            self.assertTrue(song_lists.remove_saved(cfg, a))
            self.assertEqual(song_lists.saved(cfg), [])
            self.assertTrue(os.path.isfile(a))
            # 見つからない項目の再指定。差し替え先が登録済みなら重ねない。
            gone = str(Path(d) / 'gone.tja')
            cfg['player_saved'] = [{'path': gone, 'title': '消えた'}, {'path': b, 'title': '曲B'}]
            self.assertTrue(song_lists.relink_saved(cfg, gone, b))
            self.assertEqual([e['path'] for e in song_lists.saved(cfg)], [b])
            cfg['player_saved'] = [{'path': gone, 'title': '消えた'}]
            self.assertTrue(song_lists.relink_saved(cfg, gone, a))
            self.assertEqual(song_lists.saved(cfg)[0]['title'], '曲A')

    def test_recent_moves_to_front(self):
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory(prefix='neotja-player-recent-') as d:
            a = _tja(Path(d) / 'a.tja', 'A')
            b = _tja(Path(d) / 'b.tja', 'B')
            cfg = {}
            song_lists.add_recent(cfg, a)
            song_lists.add_recent(cfg, b)
            song_lists.add_recent(cfg, a)
            self.assertEqual([e['title'] for e in song_lists.recent(cfg)], ['A', 'B'])


class PlayerHostTests(unittest.TestCase):
    @real_qt_test
    def test_enter_and_leave_player(self):
        from PySide6.QtTest import QTest
        with editor_session() as (win, root):
            win.show()
            self.assertTrue(win.is_start_screen_visible())
            win.start_screen.btn_player.click()
            QTest.qWait(50)
            self.assertTrue(win.is_player_visible())
            self.assertFalse(win.menuBar().isVisible())
            self.assertFalse(win.toolbar_top.isVisible())
            self.assertEqual(win.windowTitle(), 'NeoTJAPlayer')
            # 窓の肩書きを組み直す道を通っても Player の名前のまま。
            win._refresh_title()
            self.assertEqual(win.windowTitle(), 'NeoTJAPlayer')

            # 曲を選ぶと song_chosen が出て、最近開いた楽曲に入る(設定は一時ファイル)。
            got = []
            win.player_page.song_chosen.connect(got.append)
            song = _tja(root / 'songs' / 's.tja', '選曲の確認')
            win.player_page.library.choose(song)
            self.assertEqual(got, [song])
            self.assertEqual(win.config_data['player_recent'][0]['path'], os.path.normpath(song))

            # 保存済みから外しても元ファイルは消えない(画面の操作から)。
            lib = win.player_page.library
            lib._on_save_toggled(song, True)
            lib.show_section('saved')
            self.assertEqual(len(lib.saved_list.rows()), 1)
            lib.saved_list.rows()[0].btn_remove.click()
            self.assertEqual(lib.saved_list.rows(), [])
            self.assertTrue(os.path.isfile(song))

            win.player_page.btn_back.click()
            QTest.qWait(50)
            self.assertFalse(win.is_player_visible())
            self.assertTrue(win.is_start_screen_visible())
            self.assertTrue(win.menuBar().isVisible())
            self.assertNotEqual(win.windowTitle(), 'NeoTJAPlayer')

    @real_qt_test
    def test_editor_shortcuts_do_nothing_in_player(self):
        from PySide6.QtWidgets import QMessageBox
        with editor_session() as (win, _root):
            win.show()
            win.editor.insertPlainText('XYZ')
            win.start_screen.btn_player.click()
            self.assertTrue(win.is_player_visible())
            calls = []
            win.save_file = lambda: calls.append('save')
            win.preview_dock.toggle_hit_sounds = lambda: calls.append('f1')
            orig_warning = QMessageBox.warning
            QMessageBox.warning = staticmethod(lambda *a, **k: calls.append('warn'))
            try:
                win._on_save_shortcut()
                win._on_f1()
                win.run_simulator('F2')
                win._undo_here(False)
                self.assertEqual(calls, [])
                self.assertIn('XYZ', win.editor.toPlainText())
                # 戻ったら効く。
                win._leave_player()
                win._on_save_shortcut()
                win._on_f1()
                self.assertEqual(calls, ['save', 'f1'])
            finally:
                QMessageBox.warning = orig_warning


if __name__ == '__main__':
    unittest.main()
