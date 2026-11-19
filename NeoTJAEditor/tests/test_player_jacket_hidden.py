"""曲別「ジャケットを指定したまま表示しない」(jacket_hidden)。

Player の「背景・ジャケットを指定」の「表示しない」で決め、道は残す。表示しない
曲は Player の画面(難易度選択など)に No Image の枠を出し、録画にも入れない。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_jacket_hidden.py`
"""
import copy
import os
import tempfile
import unittest
from pathlib import Path

import test_player_record as record_tests
from test_player_record import _png, _wait, real_qt_test


class ChartArtHiddenTests(unittest.TestCase):
    def test_round_trip_keeps_path(self):
        from neotja.chart_art import (chart_art, existing_art, jacket_hidden,
                                      set_chart_art, set_jacket_hidden, record_background)
        with tempfile.TemporaryDirectory() as d:
            tja = Path(d) / 'a.tja'
            tja.write_text('TITLE:a\n', encoding='utf-8')
            jk = _png(Path(d) / 'jk.png')
            cfg = {}
            set_chart_art(cfg, str(tja), jacket=jk)
            self.assertFalse(jacket_hidden(cfg, str(tja)))
            self.assertEqual(existing_art(cfg, str(tja), 'jacket'), jk)
            set_jacket_hidden(cfg, str(tja), True)
            self.assertTrue(jacket_hidden(cfg, str(tja)))
            # 再生画面(respect_hidden=True)だけ出さない。ほかはいつも出す。
            self.assertEqual(existing_art(cfg, str(tja), 'jacket', respect_hidden=True), '')
            self.assertEqual(existing_art(cfg, str(tja), 'jacket'), jk)
            self.assertEqual(chart_art(cfg, str(tja))['jacket'], jk)
            self.assertEqual(record_background(cfg, str(tja)).art, jk)
            # 背景は関係ない。
            self.assertEqual(existing_art(cfg, str(tja), 'background'), '')
            # 保存して読み直しても同じ(設定の JSON に入る)。
            import json
            cfg2 = json.loads(json.dumps(cfg))
            self.assertTrue(jacket_hidden(cfg2, str(tja)))
            set_jacket_hidden(cfg2, str(tja), False)
            self.assertFalse(jacket_hidden(cfg2, str(tja)))
            self.assertEqual(existing_art(cfg2, str(tja), 'jacket'), jk)
            entry = list(cfg2['player_chart_art'].values())[0]
            self.assertNotIn('jacket_hidden', entry)

    def test_recording_drops_jacket_when_hidden(self):
        from neotja.chart_art import set_chart_art, set_jacket_hidden
        from neotja.dialogs.record_dialog import RecordDialog
        from neotja.recorder import RecordBackground
        with tempfile.TemporaryDirectory() as d:
            tja = str(Path(d) / 'a.tja')
            cfg = {}
            set_chart_art(cfg, tja, save_jacket=False, jacket='x.png')

            class Stub:
                _layout = 'game'
                _chart_path = tja
                _mw = type('M', (), {'config_data': cfg})()
                _bg = RecordBackground('', art='x.png', art_track=[(0.0, 'x.png')])

                def _bg_now(self):
                    return self._bg
            s = Stub()
            self.assertEqual(RecordDialog._bg_used(s).art, 'x.png')
            set_jacket_hidden(cfg, tja, True)
            used = RecordDialog._bg_used(s)
            self.assertEqual(used.art, '')
            self.assertEqual(used.art_track, [])
            self.assertEqual(s._bg.art, 'x.png')          # 覚えている値は変えない


class DialogHiddenTests(unittest.TestCase):
    setUp = record_tests.HostRecordTests.setUp
    tearDown = record_tests.HostRecordTests.tearDown
    _host = record_tests.HostRecordTests._host

    @real_qt_test
    def test_checkbox_only_in_player_and_saved_on_ok(self):
        from neotja.chart_art import jacket_hidden, chart_art
        from neotja.dialogs.record_bg_dialog import RecordBackgroundDialog
        from neotja.player.record import build_art_dialog
        from neotja.recorder import RecordBackground
        with tempfile.TemporaryDirectory() as folder:
            host, path, core = self._host(folder)
            jk = chart_art(host.cfg, path)['jacket']
            self.assertTrue(jk)
            self.assertIsNotNone(host.difficulty._jacket)
            dlg = build_art_dialog(core, path, host.cfg, host)
            dlg.show()
            _wait(80)
            self.assertTrue(dlg.chk_art_hidden.isVisible())
            if os.environ.get('NEOTJA_SHOT'):
                c = dlg.chk_art_hidden
                c.setChecked(True); _wait(30); c.parentWidget().grab().save(os.environ['NEOTJA_SHOT'] + '_on.png')
                c.setChecked(False); _wait(30); c.parentWidget().grab().save(os.environ['NEOTJA_SHOT'] + '_off.png')
            self.assertFalse(dlg.chk_art_hidden.isChecked())
            dlg.chk_art_hidden.setChecked(True)
            self.assertTrue(dlg.jacket_hidden())
            self.assertEqual(dlg.background().art, jk)        # 道はそのまま返す
            # 取消なら覚えない。
            host._art_dialog = dlg
            host._on_art_closed(dlg, path, 0)
            self.assertFalse(jacket_hidden(host.cfg, path))
            # OK で覚え、難易度選択は No Image の枠になる。
            dlg = build_art_dialog(core, path, host.cfg, host)
            dlg.chk_art_hidden.setChecked(True)
            from PySide6.QtWidgets import QDialog
            host._on_art_closed(dlg, path, QDialog.Accepted)
            self.assertTrue(jacket_hidden(host.cfg, path))
            self.assertEqual(chart_art(host.cfg, path)['jacket'], jk)
            self.assertIsNotNone(host.difficulty._jacket)   # 難易度選択には出す
            # 開き直すと印が付いている。外して OK で戻る。
            dlg = build_art_dialog(core, path, host.cfg, host)
            self.assertTrue(dlg.chk_art_hidden.isChecked())
            dlg.chk_art_hidden.setChecked(False)
            host._on_art_closed(dlg, path, QDialog.Accepted)
            self.assertFalse(jacket_hidden(host.cfg, path))
            self.assertIsNotNone(host.difficulty._jacket)
            # 録画(既定)からは出さない。
            rec = RecordBackgroundDialog(host, lambda: None, 10.0, RecordBackground(art=jk))
            rec.show()
            _wait(50)
            self.assertFalse(rec.chk_art_hidden.isVisible())
            self.assertFalse(rec.jacket_hidden())
            rec._closed = True
            rec.reject()
            rec.deleteLater()
            host.shutdown()
            host.close()
            host.deleteLater()

    @real_qt_test
    def test_hidden_only_affects_play_screen(self):
        from neotja.chart_art import set_jacket_hidden
        with tempfile.TemporaryDirectory() as folder:
            host, path, core = self._host(folder)
            set_jacket_hidden(host.cfg, path, True)
            host.intro.set_song(path, 'Oni', 9)
            self.assertIsNotNone(host.intro._jacket)
            host.results.set_result(path, 'Oni', 9, {}, None, {})
            self.assertIsNotNone(host.results._jacket)
            host.difficulty.set_song(path)
            self.assertIsNotNone(host.difficulty._jacket)
            from neotja.player.backdrop import player_background
            self.assertEqual(player_background(host.cfg, path).art, '')   # 再生画面だけ出さない
            self.assertIsNotNone(host.difficulty._bg)      # 背景は出す
            host.shutdown()
            host.close()
            host.deleteLater()


if __name__ == '__main__':
    unittest.main()
