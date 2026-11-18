"""最近開いた楽曲: 見て回るだけでは入らない・演奏/録画を始めたら入る・
選曲へ戻ったら一覧が新しくなる。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_recent.py`
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

_TMP = tempfile.mkdtemp(prefix='neotja-recent-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402


def _wait(ms):
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QTest.qWait(10)
        time.sleep(0.002)


def _tja(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / (name + '.tja')
    p.write_text('TITLE:%s\nBPM:120\nCOURSE:Oni\nLEVEL:8\n#START\n1111,\n#END\n' % name,
                 encoding='cp932')
    return str(p)


class FakeDock:
    def set_master_volume(self, v):
        pass

    def set_volume(self, v):
        pass

    def cancel_predecode(self):
        pass


class FakeCore:
    def __init__(self, cfg):
        self.dock = FakeDock()
        self.loaded = []

    def play_demo(self, path, demo):
        pass

    def stop_audio(self):
        pass

    def shutdown(self):
        pass

    def predecode(self, path):
        pass

    def chart_ready(self):
        return True

    def load(self, path, course_key=None, play_mode=None):
        self.loaded.append(path)
        return True


def _host(cfg):
    from neotja.player.host import PlayerHost
    host = PlayerHost(cfg, lambda: None)
    host.resize(1600, 900)
    host.show()
    host.on_shown()
    return host


def _recent_titles(host):
    from neotja.player.library_view import SongRow
    return [r.title.text() for r in host.library.recent_list.rows() if isinstance(r, SongRow)]


class RecentTests(unittest.TestCase):
    def setUp(self):
        from neotja.player.host import PlayerHost
        self._orig = PlayerHost.core_factory
        PlayerHost.core_factory = FakeCore

    def tearDown(self):
        from neotja.player.host import PlayerHost
        PlayerHost.core_factory = self._orig

    @real_qt_test
    def test_browsing_does_not_fill_recent_but_play_does(self):
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory() as d:
            a = _tja(Path(d) / 'a', 'A')
            b = _tja(Path(d) / 'b', 'B')
            c = _tja(Path(d) / 'c', 'C')
            cfg = {'master_volume': 0.0}
            song_lists.add_recent(cfg, c)
            saves = []
            host = _host(cfg)
            host._save_cb = lambda: saves.append(1)
            host.library._save_cb = host._save_cb
            self.assertIs(host.library.pages.currentWidget(), host.library.page_recent)
            self.assertEqual(_recent_titles(host), ['C'])
            # 見て回る(難易度選択を開いて戻る)だけでは入らない。
            for p in (a, b, a):
                host.library.choose(p)
                host.difficulty.btn_back.click()
            self.assertEqual([e['title'] for e in song_lists.recent(cfg)], ['C'])
            self.assertEqual(_recent_titles(host), ['C'])
            # Play で入る(保存もする)。選曲へ戻ると一覧も新しくなる。
            host.library.choose(b)
            host.difficulty.btn_play.click()
            self.assertEqual([e['title'] for e in song_lists.recent(cfg)], ['B', 'C'])
            self.assertTrue(saves)
            host.stop_preview()
            host.show_library()
            self.assertEqual(_recent_titles(host), ['B', 'C'])
            from neotja.player.library_view import SongRow
            rows = [r for r in host.library.recent_list.rows() if isinstance(r, SongRow)]
            self.assertTrue(rows[0]._accent)              # 選んでいた行の印は残る
            self.assertFalse(rows[1]._accent)
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_recording_start_adds_recent(self):
        from PySide6.QtCore import Signal
        from PySide6.QtWidgets import QDialog
        from neotja.player import record as record_mod
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory() as d:
            a = _tja(Path(d) / 'a', 'A')
            cfg = {'master_volume': 0.0}

            class FakeDialog(QDialog):
                recording_started = Signal()
            made = []

            def build(core, path, cfg_, parent, cls=None):
                dlg = FakeDialog(parent)
                made.append(dlg)
                return dlg, ''
            orig = record_mod.build_dialog
            record_mod.build_dialog = build
            host = _host(cfg)
            try:
                host.library.choose(a)
                host.difficulty.btn_record.click()
                self.assertTrue(made)
                self.assertEqual(song_lists.recent(cfg), [])        # 開いただけでは入らない
                made[0].recording_started.emit()
                self.assertEqual([e['title'] for e in song_lists.recent(cfg)], ['A'])
                made[0].reject()
                _wait(50)
            finally:
                record_mod.build_dialog = orig
                host.shutdown()
                host.deleteLater()

    @real_qt_test
    def test_clear_history_asks_then_clears(self):
        from neotja.player import song_lists
        from neotja.player.library_view import ConfirmDialog
        with tempfile.TemporaryDirectory() as d:
            a = _tja(Path(d) / 'a', 'A')
            b = _tja(Path(d) / 'b', 'B')
            cfg = {'master_volume': 0.0}
            song_lists.add_recent(cfg, a)
            song_lists.add_recent(cfg, b)
            host = _host(cfg)
            lib = host.library
            self.assertIs(lib.pages.currentWidget(), lib.page_recent)
            btn = lib.btn_clear_recent
            self.assertTrue(btn.isVisible() and btn.isEnabled())
            self.assertEqual(btn.text(), '履歴を消す')
            # キャンセル: 何も消えない。窓は閉じるのを待たない(試験が止まらない)。
            btn.click()
            dlg = lib._clear_dialog
            self.assertIsInstance(dlg, ConfirmDialog)
            self.assertTrue(dlg.isVisible())
            self.assertTrue(dlg.btn_cancel.isDefault())
            dlg.btn_cancel.click()
            _wait(30)
            self.assertIsNone(lib._clear_dialog)
            self.assertEqual(len(song_lists.recent(cfg)), 2)
            self.assertEqual(len(_recent_titles(host)), 2)
            # 消す: 一覧も空になり、ボタンは押せなくなる。元の TJA は残る。
            btn.click()
            lib._clear_dialog.btn_ok.click()
            _wait(30)
            self.assertEqual(song_lists.recent(cfg), [])
            self.assertEqual(cfg['player_recent'], [])
            self.assertEqual(_recent_titles(host), [])
            self.assertFalse(btn.isEnabled())
            self.assertTrue(os.path.isfile(a) and os.path.isfile(b))
            btn.click()                                   # 空なら窓も出ない
            self.assertIsNone(lib._clear_dialog)
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_dragging_tja_lights_open_card_and_restores(self):
        from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
        from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
        from PySide6.QtWidgets import QApplication
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory() as d:
            a = _tja(Path(d) / 'a', 'A')
            cfg = {'master_volume': 0.0}
            song_lists.add_recent(cfg, a)
            host = _host(cfg)
            lib = host.library
            self.assertIs(lib.pages.currentWidget(), lib.page_recent)

            def mime(path):
                m = QMimeData()
                m.setUrls([QUrl.fromLocalFile(path)])
                return m
            pos = QPoint(400, 400)

            def enter(m):
                ev = QDragEnterEvent(pos, Qt.CopyAction, m, Qt.LeftButton, Qt.NoModifier)
                QApplication.sendEvent(host, ev)
                return ev
            m = mime(a)
            ev = enter(m)
            self.assertTrue(ev.isAccepted())
            self.assertIs(lib.pages.currentWidget(), lib.page_open)
            self.assertTrue(lib.nav_open.isChecked())
            self.assertTrue(lib.open_card.drop_hot)
            QApplication.sendEvent(host, QDragLeaveEvent())
            self.assertFalse(lib.open_card.drop_hot)
            self.assertIs(lib.pages.currentWidget(), lib.page_recent)
            self.assertTrue(lib.nav_recent.isChecked())
            # TJA でないものは光らせない。
            txt = Path(d) / 'cover.png'
            txt.write_bytes(b'x')
            enter(mime(str(txt)))
            self.assertFalse(lib.open_card.drop_hot)
            self.assertIs(lib.pages.currentWidget(), lib.page_recent)
            # 落とすと今までどおり難易度選択へ。選曲の画面は元に戻してある。
            enter(m)
            QApplication.sendEvent(host, QDropEvent(QPointF(pos), Qt.CopyAction, m,
                                                    Qt.LeftButton, Qt.NoModifier))
            self.assertFalse(lib.open_card.drop_hot)
            self.assertIs(host.body.currentWidget(), host.difficulty)
            self.assertIs(lib.pages.currentWidget(), lib.page_recent)
            # 難易度選択の上では画面を切り替えない(落とせば今までどおり開く)。
            enter(m)
            self.assertFalse(lib.drop_hint_active())
            self.assertIs(host.body.currentWidget(), host.difficulty)
            QApplication.sendEvent(host, QDragLeaveEvent())
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_record_dialog_announces_start(self):
        from neotja.dialogs.record_dialog import RecordDialog
        self.assertTrue(hasattr(RecordDialog, 'recording_started'))


if __name__ == '__main__':
    unittest.main()
