"""Player の難易度選択(neotja/player/difficulty.py)。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_difficulty.py`
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

# 実設定を読み書きしないよう、import より先に一時ファイルへ向ける。
_TMP = tempfile.mkdtemp(prefix='neotja-difficulty-test-')
_CFG = Path(_TMP) / 'settings.json'
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402


def _chart(path, courses, title='テスト曲'):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['TITLE:' + title, 'SUBTITLE:--副題', 'BPM:150', '']
    for c, lv in courses:
        lines += ['COURSE:' + c, 'LEVEL:%d' % lv, '#START', '1111,', '#END', '']
    path.write_text('\n'.join(lines), encoding='cp932')
    return str(path)


def _png(path, color='#3366cc'):
    from PySide6.QtGui import QColor, QPixmap
    pm = QPixmap(40, 40)
    pm.fill(QColor(color))
    pm.save(str(path))
    return str(path)


class ArtStoreTests(unittest.TestCase):
    def test_chart_art_by_path_and_missing_files(self):
        from neotja.player import song_lists as sl
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a' / 'same.tja', [('Oni', 9)], 'A')
            b = _chart(Path(d) / 'b' / 'same.tja', [('Oni', 9)], 'A')
            img = Path(d) / 'j.png'
            img.write_bytes(b'x')
            cfg = {}
            sl.set_chart_art(cfg, a, jacket=str(img))
            # 同じ題・同じファイル名でも別の譜面とは取り違えない。
            self.assertEqual(sl.chart_art(cfg, b), {'jacket': '', 'background': ''})
            self.assertEqual(sl.existing_art(cfg, a.upper(), 'jacket'), os.path.normpath(str(img)))
            img.unlink()
            self.assertEqual(sl.existing_art(cfg, a, 'jacket'), '')


class DifficultyPageTests(unittest.TestCase):
    def _page(self, cfg=None, saves=None):
        from neotja.player.difficulty import DifficultyPage
        cfg = {} if cfg is None else cfg
        saves = [] if saves is None else saves
        page = DifficultyPage(cfg, lambda: saves.append(dict(cfg)))
        page.resize(1600, 900)
        page.show()
        return page, cfg, saves

    @real_qt_test
    def test_missing_courses_are_dimmed_and_not_selectable(self):
        with tempfile.TemporaryDirectory() as d:
            path = _chart(Path(d) / 'x.tja', [('Oni', 10)])
            page, _cfg, _ = self._page()
            page.set_song(path)
            self.assertEqual(page.current_course(), 'Oni')
            for slot in ('Easy', 'Normal', 'Hard'):
                card = page.cards[slot]
                self.assertFalse(card.available)
                self.assertEqual(card.level_text(), '—')
            self.assertEqual(page.cards['Oni'].level_text(), '★10')
            page.select_slot('Easy')                      # 選べない
            self.assertEqual(page.current_course(), 'Oni')
            self.assertFalse(page.btn_swap.isVisibleTo(page))
            got = []
            page.play_requested.connect(lambda p, c: got.append((p, c)))
            page.record_requested.connect(lambda p, c: got.append(('rec', c)))
            page.btn_play.click()
            page.btn_record.click()
            self.assertEqual(got, [(path, 'Oni'), ('rec', 'Oni')])
            page.deleteLater()

    @real_qt_test
    def test_ura_toggle_switches_target_and_keeps_mark_slot(self):
        with tempfile.TemporaryDirectory() as d:
            path = _chart(Path(d) / 'u.tja', [('Edit', 10), ('Oni', 9), ('Hard', 7)])
            page, _cfg, _ = self._page()
            page.set_song(path)
            self.assertTrue(page.btn_swap.isVisibleTo(page))
            self.assertEqual(page.current_course(), 'Oni')          # 最初は おに
            page.select_slot('Hard')
            card = page.cards['Oni']
            geo = card.geometry()
            page.btn_swap.click()
            self.assertEqual(page.current_course(), 'Edit')        # 対象も裏へ
            self.assertEqual(card.key, 'Edit')
            self.assertEqual(card.level_text(), '★10')
            self.assertEqual(card.geometry(), geo)                 # 位置・大きさは同じ
            self.assertTrue(card.selected)
            # ⇔ はカードの右上の角からはみ出す(押す場所は 48px 以上)。
            sw = page.btn_swap.geometry()
            self.assertGreaterEqual(min(sw.width(), sw.height()), 48)
            self.assertGreater(sw.right(), geo.right() - 8)
            self.assertLess(sw.top(), geo.top() + 8)
            page.btn_swap.click()
            self.assertEqual(page.current_course(), 'Oni')
            self.assertEqual(card.level_text(), '★9')
            page.deleteLater()

    @real_qt_test
    def test_only_ura_is_selectable_directly(self):
        with tempfile.TemporaryDirectory() as d:
            path = _chart(Path(d) / 'o.tja', [('Edit', 8), ('Normal', 4)])
            page, _cfg, _ = self._page()
            page.set_song(path)
            self.assertEqual(page.current_course(), 'Edit')
            self.assertFalse(page.btn_swap.isVisibleTo(page))
            self.assertEqual(page.cards['Oni'].key, 'Edit')
            self.assertTrue(page.cards['Oni'].available)
            page.select_slot('Normal')
            self.assertEqual(page.current_course(), 'Normal')
            page.deleteLater()

    @real_qt_test
    def test_settings_toggles_persist(self):
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as d:
            path = _chart(Path(d) / 'x.tja', [('Oni', 10)])
            page, cfg, saves = self._page({'player_show_bg': False})
            page.set_song(path)
            page.show()
            page.open_settings()
            QTest.qWait(250)
            self.assertTrue(page.settings_open())
            self.assertFalse(page.settings.switches['player_show_bg'].isChecked())
            self.assertTrue(page.settings.switches['player_show_jacket'].isChecked())
            # 後ろの並びは縮めない。
            self.assertEqual(page.cards['Oni'].width(), int(236))
            page.settings.switches['player_show_jacket'].click()
            page.settings.switches['player_show_lyric'].click()
            self.assertIs(cfg['player_show_jacket'], False)
            self.assertTrue(page.show_jacket())   # OFF は再生画面の下のジャケットだけ
            self.assertIs(cfg['player_show_lyric'], False)
            self.assertEqual(len(saves), 2)
            page.settings.btn_close.click()
            QTest.qWait(250)
            self.assertFalse(page.settings.isVisible())
            page.deleteLater()

    @real_qt_test
    def test_missing_images_fall_back(self):
        from neotja.player import song_lists as sl
        with tempfile.TemporaryDirectory() as d:
            path = _chart(Path(d) / 'x.tja', [('Oni', 10)])
            cfg = {}
            sl.set_chart_art(cfg, path, jacket=str(Path(d) / 'gone.png'),
                             background=_png(Path(d) / 'bg.png'))
            page, cfg, _ = self._page(cfg)
            page.set_song(path)
            self.assertIsNone(page._jacket)                 # 消えた絵は No Image
            self.assertIsNotNone(page._bg)
            img = page.grab().toImage()
            # 背景あり: 上のほうは背景の青。背景 OFF なら濃紺。
            jr = page.jacket_rect()
            self.assertEqual(page.jacket_rect(), jr)
            cfg['player_show_bg'] = False
            img2 = page.grab().toImage()
            c1 = img.pixelColor(800, 420)
            c2 = img2.pixelColor(800, 420)
            self.assertNotEqual(c1.name(), c2.name())
            self.assertLess(abs(c2.red() - 0x13) + abs(c2.green() - 0x22) + abs(c2.blue() - 0x3d), 40)
            # 背景が無いときは 曲名〜帯 を寄せる(真ん中を空けない)。
            page._layout()
            self.assertGreater(page.jacket_rect().top(), jr.top())
            self.assertLess(page.panel_bottom(), page.height())
            cfg['player_show_bg'] = True
            # 題の位置はジャケットの有無で変わらない。
            sl.set_chart_art(cfg, path, jacket=_png(Path(d) / 'j.png', '#ff0000'))
            page.set_song(path)
            self.assertIsNotNone(page._jacket)
            self.assertEqual(page.jacket_rect(), jr)
            page.deleteLater()


class DifficultyKeysTests(unittest.TestCase):
    _page = DifficultyPageTests._page

    @real_qt_test
    def test_up_down_toggle_ura_and_enter_plays(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as d:
            path = _chart(Path(d) / 'u.tja', [('Edit', 10), ('Oni', 9), ('Hard', 7)])
            page, _cfg, _ = self._page()
            page.set_song(path)
            page.setFocus()
            QTest.keyClick(page, Qt.Key_Down)
            self.assertEqual(page.current_course(), 'Edit')
            self.assertEqual(page.cards['Oni'].level_text(), '★10')
            QTest.keyClick(page, Qt.Key_Up)
            self.assertEqual(page.current_course(), 'Oni')
            got = []
            page.play_requested.connect(lambda p, c: got.append(c))
            page.setFocus()
            QTest.keyClick(page, Qt.Key_Return)
            self.assertEqual(got, ['Oni'])
            # 設定を開いているあいだは Play・録画を隠し、Enter でも始めない。
            page.open_settings()
            self.assertFalse(page.btn_play.isVisible())
            self.assertFalse(page.btn_record.isVisible())
            QTest.keyClick(page, Qt.Key_Return)
            self.assertEqual(got, ['Oni'])
            # 設定の中: ↑↓ で行を移り、←→ でスイッチを切る/入れる(表裏は変えない)。
            panel = page.settings
            rows = panel.rows()
            rows[0].setFocus()
            QTest.keyClick(rows[0], Qt.Key_Down)
            self.assertTrue(rows[1].hasFocus())
            self.assertEqual(page.current_course(), 'Oni')
            sw = panel.switches['player_show_bg']
            self.assertTrue(sw.isChecked())
            QTest.keyClick(sw, Qt.Key_Left)
            self.assertFalse(sw.isChecked())
            QTest.keyClick(sw, Qt.Key_Right)
            self.assertTrue(sw.isChecked())
            self.assertEqual(panel.lbl_auto.text(), 'オート演奏')
            page.deleteLater()


class UiFontTests(unittest.TestCase):
    @real_qt_test
    def test_ui_font_falls_back_without_seurat(self):
        from neotja.player import difficulty as d
        orig = d.seurat_file
        d._font_cache.pop("ui", None)
        d.seurat_file = lambda: ""            # 書体が無い環境
        try:
            fam = d.ui_family()
            if fam != d.SEURAT_FAMILY:          # この機械に入っていなければ
                self.assertIn(fam, d.UI_FAMILIES + ("",))
            self.assertTrue(d.ui_css_family())
        finally:
            d.seurat_file = orig
            d._font_cache.pop("ui", None)


class HostFlowTests(unittest.TestCase):
    @real_qt_test
    def test_song_choice_opens_difficulty_and_back_keeps_library(self):
        from neotja.player.host import PlayerHost

        class SilentCore:      # 試聴は test_player_preview.py で見る
            def __init__(self, cfg):
                self.dock = type('D', (), {'set_master_volume': lambda self, v: None})()

            def play_demo(self, *a):
                pass

            def stop_audio(self):
                pass

            def shutdown(self):
                pass

        orig = PlayerHost.core_factory
        PlayerHost.core_factory = SilentCore
        self.addCleanup(lambda: setattr(PlayerHost, 'core_factory', orig))
        with tempfile.TemporaryDirectory() as d:
            path = _chart(Path(d) / 'x.tja', [('Oni', 10)])
            cfg = {'player_saved': [{'path': path, 'title': 'テスト曲'}]}
            host = PlayerHost(cfg, lambda: None)
            host.resize(1600, 900)
            host.show()
            host.on_shown()
            host.library.show_section('saved')
            host.library.page_saved.search.setText('テスト')
            # 1回目は選ぶだけ(試聴)、もう一度押すと難易度選択へ。
            host.library.saved_list.rows()[0].chosen.emit(path)
            self.assertIs(host.body.currentWidget(), host.library)
            host.library._last_select = 0.0
            host.library.saved_list.rows()[0].chosen.emit(path)
            self.assertIs(host.body.currentWidget(), host.difficulty)
            self.assertFalse(host.top_bar.isVisible())
            self.assertEqual(host.difficulty.current_course(), 'Oni')
            host.difficulty.btn_back.click()
            self.assertIs(host.body.currentWidget(), host.library)
            self.assertTrue(host.top_bar.isVisible())
            self.assertEqual(host.library.current_section(), 'saved')
            self.assertEqual(host.library.page_saved.search.text(), 'テスト')
            host.shutdown()
            host.deleteLater()


if __name__ == '__main__':
    unittest.main()
