"""選曲の左の列: 登録フォルダの中のジャンル(box.def)を直接並べる。
直下にほかの曲・フォルダもあれば最後に「その他」。右クリックは登録フォルダへ。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_sidebar_genres.py`
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-sidebar-test-')
_CFG = Path(_TMP) / 'settings.json'
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402


def _wait(ms):
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QTest.qWait(5)
        time.sleep(0.001)


def _tja(path, title):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('TITLE:%s\nBPM:120\nCOURSE:Oni\nLEVEL:5\n#START\n1,\n#END\n' % title,
                    encoding='cp932')
    return str(path)


def _box(folder, title, color):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'box.def').write_text('#TITLE:%s\n#BGCOLOR:%s\n' % (title, color),
                                    encoding='cp932')
    return str(folder)


def _songs_root(d):
    root = Path(d) / 'Songs'
    g1 = _box(root / '01 pops', 'ポップス', '#ff8800')
    g2 = _box(root / '02 anime', 'アニメ', '#3366ff')
    _tja(Path(g1) / 'a' / 'a.tja', 'A')
    _tja(Path(g2) / 'b' / 'b.tja', 'B')
    _tja(root / 'plain' / 'c' / 'c.tja', 'C')
    _tja(root / 'loose.tja', 'L')
    (root / 'empty').mkdir()
    return str(root), g1, g2


class EntriesTests(unittest.TestCase):
    def test_entries(self):
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory() as d:
            root, g1, g2 = _songs_root(d)
            got = song_lists.sidebar_entries(root)
            self.assertEqual([(k, n, c) for k, _p, n, c in got],
                             [('genre', 'ポップス', '#ff8800'), ('genre', 'アニメ', '#3366ff'),
                              ('other', 'その他', None)])
            self.assertEqual(got[0][1], g1)
            self.assertEqual(got[2][1], root)
            # ジャンルだけ(空のフォルダは数えない)なら「その他」は無い。
            only = Path(d) / 'Only'
            _box(only / 'g', 'G', '#00ff00')
            (only / 'empty').mkdir()
            self.assertEqual([k for k, *_r in song_lists.sidebar_entries(str(only))], ['genre'])
            # ジャンルの無いフォルダは今までどおり1つ。
            plain = Path(d) / 'Plain'
            _tja(plain / 'x.tja', 'X')
            self.assertEqual(song_lists.sidebar_entries(str(plain)),
                             [('root', str(plain), 'Plain', None)])
            missing = str(Path(d) / 'nope')
            self.assertEqual(song_lists.sidebar_entries(missing)[0][0], 'root')


class SidebarTests(unittest.TestCase):
    @real_qt_test
    def test_sidebar_lists_genres_and_actions_target_root(self):
        from neotja.player import song_lists
        from neotja.player.library_view import GenreRow, LibraryView
        with tempfile.TemporaryDirectory() as d:
            root, g1, g2 = _songs_root(d)
            cfg = {'master_volume': 0.0,
                   'player_folders': [{'simulator': 'TNDE', 'path': root}]}
            lib = LibraryView(cfg, lambda: None)
            lib.resize(1400, 900)
            lib.show()
            lib.show_default()
            navs = lib.folder_navs
            self.assertEqual([b.text() for b in navs], ['ポップス', 'アニメ', 'その他'])
            self.assertEqual(navs[0].dot_color.name(), '#ff8800')
            self.assertIsNone(navs[2].dot_color)
            self.assertTrue(all(song_lists.path_key(b.root) == song_lists.path_key(root)
                                for b in navs))
            # ジャンルを開く: パンくずはジャンルから始まる。
            navs[0].click()
            self.assertEqual(lib.current_section(), ('folder', g1))
            crumbs = [lib.page_folder.crumbs.itemAt(i).widget()
                      for i in range(lib.page_folder.crumbs.count())]
            crumbs = [w.text() for w in crumbs if w is not None and hasattr(w, 'text')
                      and w.objectName().startswith('crumb') and w.objectName() != 'crumbSep']
            self.assertEqual(crumbs, ['ポップス'])
            # 「その他」は登録フォルダそのもの。ジャンルの行は出さない。
            navs[2].click()
            self.assertEqual(lib.current_section(), ('folder', root))
            for _ in range(200):
                if len(lib.folder_list.rows()) >= 2:
                    break
                _wait(10)
            _wait(100)
            kinds = [type(r).__name__ for r in lib.folder_list.rows()]
            self.assertNotIn(GenreRow.__name__, kinds)
            self.assertIn('FolderRow', kinds)
            self.assertIn('SongRow', kinds)
            # ジャンルの中で開いた下の段ではジャンルを隠さない(その他のときだけ)。
            navs[0].click()
            self.assertEqual(lib._hide_genres_in, '')
            # 右クリックの「カテゴリを変更」「外す」は登録フォルダへ。
            calls = []
            lib.change_folder_category = lambda p: calls.append(('cat', p))
            lib.folder_action(navs[1], 'category')
            lib.folder_action(navs[1], 'remove')
            self.assertEqual(calls, [('cat', root)])
            self.assertEqual(song_lists.folders_of(cfg, 'TNDE'), [])
            self.assertEqual(lib.folder_navs, [])
            self.assertNotIsInstance(lib.current_section(), tuple)   # 開いていたジャンルも閉じる
            # 足し直すと読み直して、最初のジャンルを開く。
            lib.add_folder_path(root, 'TNDE')
            self.assertEqual([b.text() for b in lib.folder_navs], ['ポップス', 'アニメ', 'その他'])
            self.assertEqual(lib.current_section(), ('folder', g1))
            lib.shutdown()
            lib.deleteLater()


    @real_qt_test
    def test_category_head_collapses_its_genres(self):
        """シミュレーターの見出しを押すと、その下のジャンルを畳む・開く。
        畳んだ名前は player_collapsed に覚え、作り直しても畳んだまま。"""
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        from neotja.player.library_view import CategoryHead, LibraryView
        with tempfile.TemporaryDirectory() as d:
            root, g1, g2 = _songs_root(d)
            other = Path(d) / 'Other'
            _tja(other / 'x.tja', 'X')
            saves = []
            cfg = {'master_volume': 0.0,
                   'player_folders': [{'simulator': 'TNDE', 'path': root},
                                      {'simulator': 'OpenTaiko', 'path': str(other)}]}
            lib = LibraryView(cfg, lambda: saves.append(1))
            lib.resize(1400, 900)
            lib.show()
            lib.show_default()
            heads = lib.category_labels
            self.assertTrue(all(isinstance(h, CategoryHead) for h in heads))
            self.assertEqual(sorted(h.text() for h in heads), ['OpenTaiko', 'TNDE'])
            tnde = next(h for h in heads if h.name == 'TNDE')
            ot = next(h for h in heads if h.name == 'OpenTaiko')
            self.assertEqual(len(tnde.navs), 3)
            self.assertTrue(all(b.isVisible() for b in lib.folder_navs))
            QTest.mouseClick(tnde, Qt.LeftButton, pos=QPoint(20, tnde.height() // 2))
            self.assertTrue(tnde.collapsed)
            self.assertTrue(all(not b.isVisible() for b in tnde.navs))
            self.assertTrue(all(b.isVisible() for b in ot.navs))
            self.assertEqual(cfg['player_collapsed'], ['TNDE'])
            self.assertTrue(saves)
            # 作り直しても畳んだまま。
            lib.refresh_sidebar()
            tnde = next(h for h in lib.category_labels if h.name == 'TNDE')
            self.assertTrue(tnde.collapsed)
            self.assertTrue(all(not b.isVisible() for b in tnde.navs))
            QTest.mouseClick(tnde, Qt.LeftButton, pos=QPoint(20, tnde.height() // 2))
            self.assertFalse(tnde.collapsed)
            self.assertTrue(all(b.isVisible() for b in tnde.navs))
            self.assertEqual(cfg['player_collapsed'], [])
            lib.shutdown()
            lib.deleteLater()


if __name__ == '__main__':
    unittest.main()
