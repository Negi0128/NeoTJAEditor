"""同居 Player(neotja/player/host.py)の入口・キーの遮断・保存済み楽曲の扱い。"""
import os
import tempfile
import unittest
from pathlib import Path

from gui_support import editor_session, real_qt_test


class _SilentCore:
    """試聴の係の代わり。この試験は選曲の画面を見るもので、音声デバイスを
    開く本物の PlayerCore(裏に PreviewDock をもう1つ持つ)は要らない。
    試聴そのものは test_player_preview.py で見る。"""

    def __init__(self, cfg):
        self.dock = type('D', (), {'set_master_volume': lambda self, v: None})()

    def play_demo(self, path, demo):
        pass

    def stop_audio(self):
        pass

    def shutdown(self):
        pass


_ORIG_FACTORY = None


def setUpModule():
    global _ORIG_FACTORY
    from neotja.player.host import PlayerHost
    _ORIG_FACTORY = PlayerHost.core_factory
    PlayerHost.core_factory = _SilentCore


def tearDownModule():
    from neotja.player.host import PlayerHost
    PlayerHost.core_factory = _ORIG_FACTORY


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


def _box(directory, text, encoding):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'box.def').write_bytes(text.encode(encoding))
    return str(directory)


class GenreAndCategoryTests(unittest.TestCase):
    def test_box_def_utf8_and_cp932(self):
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory(prefix='neotja-player-box-') as d:
            root = Path(d)
            u = _box(root / '01 pop', '﻿#TITLE:ポップス\n#GENRE:J-POP\n#BGCOLOR:#FF8800\n', 'utf-8')
            c = _box(root / '02 anime', '#GENRE:アニメ\r\n#BOXCOLOR:#3366cc\r\n#BGCOLOR:zzz\r\n', 'cp932')
            a = _box(root / '03 argb', '#TITLE:ARGB\n#BGCOLOR:#FF112233\n', 'utf-8')
            self.assertEqual(song_lists.parse_box_def(u),
                             {'title': 'ポップス', 'genre': 'J-POP', 'color': '#ff8800'})
            # 題が無ければフォルダ名。BGCOLOR が壊れていれば BOXCOLOR。
            self.assertEqual(song_lists.parse_box_def(c),
                             {'title': '02 anime', 'genre': 'アニメ', 'color': '#3366cc'})
            self.assertEqual(song_lists.parse_box_def(a)['color'], '#112233')
            self.assertIsNone(song_lists.parse_box_def(str(root)))
            self.assertEqual(song_lists.display_name(u), 'ポップス')

    def test_list_dir_splits_genres_folders_songs(self):
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory(prefix='neotja-player-list-') as d:
            root = Path(d)
            g = _box(root / 'g', '#TITLE:G\n', 'utf-8')
            _tja(Path(g) / 'song' / 'song.tja', 'in genre')
            _tja(root / 'plain' / 'sub' / 'x' / 'x.tja', 'deep')   # 中にフォルダ → ふつうのフォルダ
            s = _tja(root / 'songdir' / 'song.tja', 'song')         # TJA だけ → 曲として並ぶ
            top = _tja(root / 'top.tja', 'top')
            (root / 'empty').mkdir()
            genres, dirs, tjas = song_lists.list_dir(str(root))
            self.assertEqual([p for p, _ in genres], [g])
            self.assertEqual(genres[0][1]['title'], 'G')
            self.assertEqual(dirs, [str(root / 'plain')])
            self.assertEqual(sorted(tjas), sorted([s, top]))

    def test_folder_categories_and_migration(self):
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory(prefix='neotja-player-cat-') as d:
            a, b, c = (str(Path(d) / n) for n in ('a', 'b', 'c'))
            cfg = {'player_folders': [a, {'simulator': 'TNDE', 'path': b}, a]}
            self.assertTrue(song_lists.migrate_folders(cfg))
            self.assertEqual(cfg['player_folders'],
                             [{'simulator': 'その他', 'path': a}, {'simulator': 'TNDE', 'path': b}])
            self.assertFalse(song_lists.migrate_folders(cfg))
            self.assertTrue(song_lists.add_folder(cfg, c, 'OpenTaiko'))
            self.assertFalse(song_lists.add_folder(cfg, c.upper(), 'X'))
            self.assertEqual(song_lists.simulators(cfg), ['OpenTaiko', 'TNDE', 'その他'])
            self.assertTrue(song_lists.set_folder_simulator(cfg, a, 'TNDE'))
            self.assertEqual(song_lists.simulators(cfg), ['OpenTaiko', 'TNDE'])
            self.assertTrue(song_lists.rename_simulator(cfg, 'TNDE', '虹色'))
            self.assertEqual(song_lists.folder_simulator(cfg, b), '虹色')
            self.assertTrue(song_lists.remove_folder(cfg, a))
            self.assertEqual(len(song_lists.folders(cfg)), 2)


def _info(title, courses=(), bpm=None):
    return {'title': title, 'subtitle': '', 'courses': list(courses), 'bpm': bpm}


class SortAndSearchTests(unittest.TestCase):
    def test_level_order_with_ura_fallback_ties_and_missing(self):
        from neotja.player import song_lists as sl
        items = [
            ('d/none/n.tja', _info('レベルなし', [('Oni', None)]), 0),
            ('d/b/b.tja', _info('B', [('Oni', 9), ('Hard', 7)]), 1),
            ('d/ura/u.tja', _info('裏だけ', [('Edit', 10)]), 2),
            ('d/a/a.tja', _info('A', [('Edit', 3), ('Oni', 9)]), 3),   # おにが優先
            ('d/h/h.tja', _info('むずかしいまで', [('Hard', 7), ('Easy', 3)]), 4),
        ]
        titles = lambda r: [t[1]['title'] for t in r]
        self.assertEqual(sl.sort_level(items[3][1]), 9)
        self.assertEqual(sl.sort_level(items[4][1]), 7)
        # 難易度順の素直な向きは高い順。同じレベルは題名順、無いものは最後。
        self.assertEqual(titles(sl.sort_songs(items, 'level')),
                         ['裏だけ', 'A', 'B', 'むずかしいまで', 'レベルなし'])
        self.assertEqual(titles(sl.sort_songs(items, 'level', reverse=True)),
                         ['むずかしいまで', 'A', 'B', '裏だけ', 'レベルなし'])

    def test_bpm_name_and_recent_orders(self):
        from neotja.player import song_lists as sl
        items = [
            ('r/Song10/x.tja', _info('X', bpm=200.0), 0),
            ('r/Song2/y.tja', _info('Y', bpm=120.0), 1),
            ('r/song1/z.tja', _info('Z', bpm=None), 2),
            ('r/Song3/w.tja', _info('W', bpm=120.0), 3),
        ]
        titles = lambda r: [t[1]['title'] for t in r]
        self.assertEqual(titles(sl.sort_songs(items, 'bpm')), ['W', 'Y', 'X', 'Z'])
        self.assertEqual(titles(sl.sort_songs(items, 'bpm', reverse=True)), ['X', 'W', 'Y', 'Z'])
        # フォルダ名順は数を数として(Song2 → Song3 → Song10)、大文字小文字を区別しない。
        self.assertEqual(titles(sl.sort_songs(items, 'name')), ['Z', 'Y', 'W', 'X'])
        self.assertEqual(titles(sl.sort_songs(items, 'recent')), ['X', 'Y', 'Z', 'W'])
        self.assertEqual(titles(sl.sort_songs(items, 'recent', reverse=True)), ['W', 'Z', 'Y', 'X'])
        self.assertEqual(sl.simulators({'player_folders': [
            {'simulator': 'songs10', 'path': 'a'}, {'simulator': 'Songs2', 'path': 'b'},
            {'simulator': 'TNDE', 'path': 'c'}]}), ['Songs2', 'songs10', 'TNDE'])

    def test_sort_state_saved_and_bpm_read(self):
        from neotja.player import song_lists as sl
        cfg = {}
        self.assertEqual(sl.sort_state(cfg), ('name', False))
        self.assertEqual(sl.sort_state(cfg, recent_view=True), ('recent', False))
        sl.set_sort_state(cfg, 'level', True)
        self.assertEqual(sl.sort_state(cfg), ('level', True))
        self.assertEqual(sl.sort_state(cfg, recent_view=True), ('recent', True))
        with tempfile.TemporaryDirectory(prefix='neotja-player-bpm-') as d:
            p = Path(d) / 'b.tja'
            p.write_text('TITLE:b\nBPM:133.5\nCOURSE:Oni\nLEVEL:9\n#START\n1,\n#END\n', encoding='cp932')
            self.assertEqual(sl.song_info(str(p))['bpm'], 133.5)

    def test_nfkc_partial_match(self):
        from neotja.player import song_lists as sl
        info = {'title': 'ＡＢＣ　テスト曲', 'subtitle': 'Sub Title'}
        self.assertTrue(sl.matches('abc', info, 'x.tja'))
        self.assertTrue(sl.matches('ﾃｽﾄ', info, 'x.tja'))          # 半角カナ
        self.assertTrue(sl.matches('ｓｕｂ ｔｉｔ', info, 'x.tja'))  # 全角英字
        self.assertTrue(sl.matches('FILE', info, 'c:/a/My_File.tja'))
        self.assertFalse(sl.matches('zzz', info, 'x.tja'))


class FolderSearchGuiTests(unittest.TestCase):
    @real_qt_test
    def test_recursive_search_stale_cancel_and_clear(self):
        import time
        from PySide6.QtTest import QTest
        from neotja.player.library_view import LibraryView

        def wait_until(cond, ms=4000):
            end = time.monotonic() + ms / 1000
            while time.monotonic() < end and not cond():
                QTest.qWait(10)
                time.sleep(0.005)   # ワーカーにも実行時間を渡す

        with tempfile.TemporaryDirectory(prefix='neotja-player-search-') as d:
            root = Path(d) / 'Songs'
            g = _box(root / 'g', '#TITLE:ジャンル\n', 'utf-8')
            deep = _tja(Path(g) / 'sub' / 'song' / 'deep.tja', '深い曲')
            other = _tja(root / 'plain' / 'x' / 'y' / 'other.tja', 'Other Song')
            _tja(root / 'top' / 'top.tja', '上の曲')
            cfg = {}
            lib = LibraryView(cfg, lambda: None)
            lib._folder_root = str(root)
            lib.open_dir(str(root))
            lib.page_folder.search.setText('深い')
            self.assertTrue(lib._search_timer.isActive())   # 打鍵はまとめてから探す
            lib._search_timer.stop()
            lib._run_folder_search()
            first = lib._search_worker
            # 打ち直したら前の検索は止め、その結果は捨てる。
            lib.page_folder.search.setText('ｏｔｈｅｒ')
            lib._run_folder_search()
            self.assertTrue(first._cancelled)
            wait_until(lambda: lib._search_worker.isFinished() and lib.search_list.rows())
            rows = lib.search_list.rows()
            self.assertEqual([r.path for r in rows], [other])
            self.assertIs(lib.folder_stack.currentWidget(), lib.search_list)
            self.assertIn('plain', rows[0].sub.text())
            # ジャンルの奥の曲も見つかる。
            lib.page_folder.search.setText('深い')
            lib._run_folder_search()
            wait_until(lambda: lib._search_worker.isFinished() and lib.search_list.rows())
            self.assertEqual([r.path for r in lib.search_list.rows()], [deep])
            self.assertTrue(lib.search_list.rows()[0].sub.text().startswith('Songs  ›  ジャンル  ›  sub'))
            # 消せば一段ぶんの一覧へ戻る。
            lib.page_folder.search.setText('')
            lib._run_folder_search()
            self.assertIs(lib.folder_stack.currentWidget(), lib.folder_list)
            # 並べ方は設定に残る。
            lib.page_folder.sort._pick('level')
            self.assertEqual(cfg['player_sort']['mode'], 'level')
            self.assertEqual(lib.page_saved.sort.mode, 'level')
            from neotja.worker_util import LINGERING_WORKERS
            lib.shutdown()
            wait_until(lambda: not LINGERING_WORKERS)
            lib.deleteLater()
            QTest.qWait(50)


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

    @real_qt_test
    def test_sidebar_groups_folders_by_category(self):
        from PySide6.QtWidgets import QFileDialog
        with editor_session() as (win, root):
            win.show()
            pop = root / 'sims' / 'tnde'
            pop.mkdir(parents=True)
            old = root / 'sims' / 'old'
            old.mkdir(parents=True)
            win.config_data['player_folders'] = [str(old)]   # 古い形式
            win.start_screen.btn_player.click()
            lib = win.player_page.library
            self.assertEqual([l.text() for l in lib.category_labels], ['その他'])
            orig = QFileDialog.getExistingDirectory
            QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: str(pop))
            lib.ask_category = lambda **k: 'TNDE'
            try:
                lib.add_folder()
            finally:
                QFileDialog.getExistingDirectory = orig
            self.assertEqual([l.text() for l in lib.category_labels], ['TNDE', 'その他'])
            self.assertEqual([b.simulator for b in lib.folder_navs], ['TNDE', 'その他'])
            self.assertEqual(lib.current_section(), ('folder', os.path.normpath(str(pop))))
            lib.ask_category = lambda **k: 'TNDE'
            lib.change_folder_category(str(old))
            self.assertEqual([l.text() for l in lib.category_labels], ['TNDE'])

    @real_qt_test
    def test_folder_view_rows_are_built_on_gui_thread(self):
        """走査の結果は GUI スレッドで行になる(ワーカーのスレッドで部品を
        作らない)。ジャンル・ふつうのフォルダ・曲の順に並ぶ。"""
        import time
        from PySide6.QtCore import QThread
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from neotja.player import library_view
        with editor_session() as (win, root):
            top = root / 'lib'
            g = _box(top / 'g', '#TITLE:ジャンル\n#BGCOLOR:#ff8800\n', 'cp932')
            _tja(Path(g) / 's' / 's.tja', 'in genre')
            _tja(top / 'plain' / 'sub' / 'x' / 'x.tja', 'deep')
            _tja(top / 'song' / 'song.tja', '曲')
            win.config_data['player_folders'] = [{'simulator': 'X', 'path': str(top)}]
            win.start_screen.btn_player.click()
            lib = win.player_page.library
            threads = []
            orig = library_view.GenreRow.__init__
            def spy(self, *a, **k):
                threads.append(QThread.currentThread() is QApplication.instance().thread())
                orig(self, *a, **k)
            library_view.GenreRow.__init__ = spy
            try:
                lib.show_section('folder', str(top))
                for _ in range(200):
                    if len(lib.folder_list.rows()) >= 3:
                        break
                    QTest.qWait(10)
                    time.sleep(0.005)   # ワーカーにも実行時間を渡す
            finally:
                library_view.GenreRow.__init__ = orig
            kinds = [type(r).__name__ for r in lib.folder_list.rows()]
            self.assertEqual(kinds, ['GenreRow', 'FolderRow', 'SongRow'])
            self.assertEqual(threads, [True])
            self.assertEqual(lib.folder_list.rows()[0].color().name(), '#ff8800')


if __name__ == '__main__':
    unittest.main()
