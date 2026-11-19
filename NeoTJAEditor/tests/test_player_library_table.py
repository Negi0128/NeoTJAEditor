"""選曲の曲の表(作り直し 2026-11-19): 40px の行・列の見出しで並べる・列がそろう・
フォルダの欄・ジャンルの色・読める色。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_library_table.py`
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-table-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402


def _wait(ms):
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QApplication.processEvents(QEventLoop.AllEvents, 10)
        time.sleep(0.002)


def _tja(path, title, bpm, level):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('TITLE:%s\nBPM:%d\nCOURSE:Oni\nLEVEL:%d\n#START\n1,\n#END\n'
                    % (title, bpm, level), encoding='cp932')
    return str(path)


def _contrast(a, b):
    def lum(h):
        h = h.lstrip('#')
        c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


class TableTests(unittest.TestCase):
    def test_folder_text_and_genre_colour(self):
        from neotja.player import library_view as lv
        with tempfile.TemporaryDirectory() as d:
            g = Path(d) / 'Songs' / '02 pops'
            g.mkdir(parents=True)
            (g / 'box.def').write_text('#TITLE:ポップス\n#BGCOLOR:#43a5ff\n', encoding='cp932')
            a = _tja(g / '夜明け' / '夜明け.tja', '夜明け', 120, 5)
            # 曲と同じ名前のフォルダは省き、ジャンルは box.def の題で。
            self.assertEqual(lv.folder_text(a), 'Songs  ›  ポップス')
            self.assertEqual(lv.genre_color(a), '#43a5ff')
            self.assertIsNone(lv.genre_color(str(Path(d) / 'x' / 'y.tja')))

    def test_secondary_text_is_readable(self):
        from neotja.player.library_view import secondary_color
        from neotja.theme import COLORS, is_dark_theme
        if is_dark_theme(COLORS):
            self.assertGreaterEqual(_contrast(secondary_color(), COLORS['bg']), 4.5)
            self.assertGreaterEqual(_contrast(secondary_color(), '#1b2030'), 4.5)

    @real_qt_test
    def test_header_sorts_and_columns_line_up(self):
        from neotja.player.library_view import ROW_HEIGHT, LibraryView, SongRow
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'Songs'
            a = _tja(root / 'b_dir' / 'Alpha.tja', 'Alpha', 200, 3)
            b = _tja(root / 'a_dir' / 'Charlie.tja', 'Charlie', 100, 9)
            c = _tja(root / 'c_dir' / 'Bravo.tja', 'Bravo', 150, 6)
            cfg = {'master_volume': 0.0,
                   'player_folders': [{'simulator': 'X', 'path': str(root)}],
                   'player_recent': [{'path': p, 'title': '', 'opened': time.time() - i}
                                     for i, p in enumerate((a, b, c))]}
            lib = LibraryView(cfg, lambda: None)
            lib.resize(1600, 900)
            lib.show()
            lib.show_section('folder', str(root))
            for _ in range(200):
                if len(lib.folder_list.rows()) == 3 and all(
                        r.info.get('courses') for r in lib.folder_list.rows()):
                    break
                _wait(10)
            _wait(100)
            titles = lambda: [r.info['title'] for r in lib.folder_list.rows()
                              if isinstance(r, SongRow)]
            head = lib.page_folder.sort
            self.assertEqual(set(head.buttons), {'title', 'name', 'level', 'bpm'})
            self.assertEqual(titles(), ['Charlie', 'Alpha', 'Bravo'])     # フォルダ名順
            head.buttons['title'][0].click()
            self.assertEqual(titles(), ['Alpha', 'Bravo', 'Charlie'])
            self.assertIn('▴', head.buttons['title'][0].text())
            head.buttons['title'][0].click()                               # 向きを逆に
            self.assertEqual(titles(), ['Charlie', 'Bravo', 'Alpha'])
            self.assertIn('▾', head.buttons['title'][0].text())
            head.buttons['bpm'][0].click()
            self.assertEqual(titles(), ['Charlie', 'Bravo', 'Alpha'])      # BPM 100,150,200
            head.buttons['level'][0].click()
            self.assertEqual(titles(), ['Charlie', 'Bravo', 'Alpha'])      # ★9,6,3
            self.assertEqual(cfg['player_sort']['mode'], 'level')
            # 行は 40px、列の見出しと行の欄の左がそろう。
            row = lib.folder_list.rows()[0]
            self.assertEqual(row.height(), ROW_HEIGHT)
            for key, cell in (('name', row.sub), ('level', row.course_cell)):
                hb = head.buttons[key][0]
                hx = hb.mapTo(lib, hb.rect().topLeft()).x()
                cx = cell.mapTo(lib, cell.rect().topLeft()).x()
                self.assertLessEqual(abs(hx - cx), 1, key)
            # 最近開いた楽曲は最近順が既定・日付の列あり。
            lib.show_section('recent')
            self.assertEqual(lib.page_recent.sort.mode, 'recent')
            self.assertIn('recent', lib.page_recent.sort.buttons)
            rr = lib.recent_list.rows()[0]
            self.assertTrue(hasattr(rr, 'date'))
            lib.shutdown()
            lib.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
