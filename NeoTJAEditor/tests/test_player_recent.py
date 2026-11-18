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
    def test_record_dialog_announces_start(self):
        from neotja.dialogs.record_dialog import RecordDialog
        self.assertTrue(hasattr(RecordDialog, 'recording_started'))


if __name__ == '__main__':
    unittest.main()
