"""選曲の行の左のジャケット(player/thumbs.py)。読むのはワーカー、QPixmap は GUI。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_library_thumbs.py`
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-thumbs-test-')
_CFG = Path(_TMP) / 'settings.json'
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402


def _wait(ms):
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QTest.qWait(5)
        time.sleep(0.001)


def _chart(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / (name + '.tja')
    p.write_text('TITLE:%s\nBPM:120\nCOURSE:Oni\nLEVEL:9\n#START\n1111,\n#END\n' % name,
                 encoding='cp932')
    return str(p)


def _png(path, w=200, h=100, color='#3366cc'):
    from PySide6.QtGui import QColor, QImage
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(color))
    img.save(str(path))
    return str(path)


class ThumbUnitTests(unittest.TestCase):
    @real_qt_test
    def test_cache_is_lru_and_images_are_square(self):
        from PySide6.QtGui import QImage
        from neotja.player import thumbs
        c = thumbs.ThumbCache(limit=3)
        img = QImage(8, 8, QImage.Format_RGB32)
        img.fill(0)
        for k in 'abc':
            c.put(k, img)
        c.get('a')                     # a を新しくする
        c.put('d', img)
        self.assertEqual(set(c.keys()), {'a', 'c', 'd'})
        self.assertIsNone(c.get(None))
        with tempfile.TemporaryDirectory() as d:
            p = _png(Path(d) / 'w.png', 400, 200)
            out = thumbs.load_thumb_image(p)
            self.assertEqual((out.width(), out.height()),
                             (thumbs.THUMB_LOAD_PX, thumbs.THUMB_LOAD_PX))
            self.assertIsNone(thumbs.load_thumb_image(str(Path(d) / 'none.png')))

    @real_qt_test
    def test_thumb_for_song_respects_hidden_and_known(self):
        from neotja.chart_art import set_chart_art, set_jacket_hidden
        from neotja.player import thumbs
        with tempfile.TemporaryDirectory() as d:
            tja = _chart(Path(d) / 'a', 'a')
            jk = _png(Path(d) / 'jk.png')
            cfg = {}
            self.assertEqual(thumbs.thumb_for_song(cfg, tja), (None, None))
            set_chart_art(cfg, tja, jacket=jk)
            key, img = thumbs.thumb_for_song(thumbs.art_snapshot(cfg), tja)
            self.assertEqual(key, thumbs.thumb_key(jk))
            self.assertFalse(img.isNull())
            self.assertEqual(thumbs.thumb_for_song(cfg, tja, known={key}), (key, None))
            set_jacket_hidden(cfg, tja, True)
            self.assertEqual(thumbs.thumb_for_song(cfg, tja), (None, None))


class LibraryRowThumbTests(unittest.TestCase):
    @real_qt_test
    def test_rows_get_thumbs_or_empty_frame(self):
        from neotja.chart_art import set_chart_art, set_jacket_hidden
        from neotja.player import song_lists, thumbs
        from neotja.player.library_view import LibraryView
        thumbs.CACHE.clear()
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a')
            b = _chart(Path(d) / 'b', 'b')
            c = _chart(Path(d) / 'c', 'c')
            cfg = {'master_volume': 0.0}
            set_chart_art(cfg, a, jacket=_png(Path(d) / 'ja.png'))
            set_chart_art(cfg, c, jacket=_png(Path(d) / 'jc.png'))
            set_jacket_hidden(cfg, c, True)
            for p in (a, b, c):
                song_lists.add_recent(cfg, p)
            lib = LibraryView(cfg, lambda: None)
            lib.resize(1200, 800)
            lib.show()
            lib.show_default()
            for _ in range(100):
                _wait(20)
                rows = {r.path: r for r in lib.recent_list.rows()}
                if rows.get(a) is not None and rows[a].thumb.pixmap is not None:
                    break
            rows = {r.path: r for r in lib.recent_list.rows()}
            self.assertIsNotNone(rows[a].thumb.pixmap)
            self.assertIsNone(rows[b].thumb.pixmap)        # 無い → 空の枠
            self.assertIsNone(rows[c].thumb.pixmap)        # 表示しない → 空の枠
            # 枠はどの行も同じ所(題名の左端がそろう)。
            xs = {r.title.mapTo(r, r.title.rect().topLeft()).x() for r in rows.values()}
            self.assertEqual(len(xs), 1)
            self.assertEqual(len(thumbs.CACHE), 1)
            # 表示しないを外したら、その曲だけ出し直せる。
            set_jacket_hidden(cfg, c, False)
            lib.refresh_thumb(c)
            self.assertIsNotNone(rows[c].thumb.pixmap)
            lib.shutdown()
            lib.deleteLater()


if __name__ == '__main__':
    unittest.main()
