"""選曲で試聴: 1回押すと選んで DEMOSTART から試聴、もう一度押すと難易度選択へ
(試聴は続けたまま)。戻っても止めない。別の曲を選ぶとその曲の DEMOSTART から。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_library_preview.py`
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

_TMP = tempfile.mkdtemp(prefix='neotja-libprev-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402
from test_player_preview import _chart  # noqa: E402


def _wait(ms):
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QTest.qWait(5)
        time.sleep(0.001)


class Core:
    instances = []

    def __init__(self, cfg):
        self.cfg = cfg
        self.log = []
        self.current = None
        self.dock = type('D', (), {'cancel_predecode': lambda s: None})()
        Core.instances.append(self)

    def demo_active_for(self, path):
        return self.current == path

    def play_demo(self, path, demo):
        self.current = path
        self.log.append(('play', os.path.basename(os.path.dirname(path)), demo))

    def stop_audio(self):
        self.current = None
        self.log.append(('stop',))

    def __getattr__(self, name):
        return lambda *a, **k: None


class LibraryPreviewTests(unittest.TestCase):
    def setUp(self):
        from neotja.player.host import PlayerHost
        Core.instances.clear()
        self._orig = PlayerHost.core_factory
        PlayerHost.core_factory = Core

    def tearDown(self):
        from neotja.player.host import PlayerHost
        PlayerHost.core_factory = self._orig

    @real_qt_test
    def test_select_then_enter_keeps_preview(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from neotja.player.host import PlayerHost
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a', 12.5)
            b = _chart(Path(d) / 'b', 'b', 3)
            c = _chart(Path(d) / 'c', 'c', 7)
            cfg = {'master_volume': 0.0}
            for p in (c, b, a):
                song_lists.add_recent(cfg, p)
            host = PlayerHost(cfg, lambda: None)
            host.resize(1600, 900)
            host.show()
            host.on_shown()
            try:
                self.assertEqual(Core.instances, [])       # 起動しただけでは作らない
                _wait(50)
                self.assertEqual(len(Core.instances), 1)   # 選曲が出たら裏で用意
                core = Core.instances[0]
                self.assertEqual(core.log, [])              # 用意しただけでは鳴らさない

                def row(path):
                    for r in host.library.recent_list.rows():
                        if getattr(r, 'path', None) == path:
                            return r
                    raise AssertionError(path)

                # 1回目: 選ぶだけ。少し待ってから DEMOSTART で試聴。
                QTest.mouseClick(row(a), Qt.LeftButton)
                self.assertIs(host.body.currentWidget(), host.library)
                self.assertTrue(row(a)._accent)
                self.assertEqual(core.log, [])
                _wait(250)
                self.assertEqual(core.log, [('stop',), ('play', 'a', 12.5)])
                # すぐの2回目(ダブルクリック)は無視。
                host.library._last_select = time.monotonic()
                QTest.mouseClick(row(a), Qt.LeftButton)
                self.assertIs(host.body.currentWidget(), host.library)
                # 少し置いてもう一度: 難易度選択へ。試聴は続けたまま。
                host.library._last_select = 0.0
                core.log.clear()
                QTest.mouseClick(row(a), Qt.LeftButton)
                self.assertIs(host.body.currentWidget(), host.difficulty)
                self.assertEqual(core.log, [])
                # 戻っても止めない。同じ曲を押すとまた難易度選択(続きのまま)。
                host.difficulty.btn_back.click()
                self.assertIs(host.body.currentWidget(), host.library)
                self.assertEqual(core.current, a)
                self.assertNotIn(('stop',), core.log)
                QTest.mouseClick(row(a), Qt.LeftButton)
                self.assertIs(host.body.currentWidget(), host.difficulty)
                self.assertNotIn(('stop',), core.log)
                host.difficulty.btn_back.click()
                # 別の曲: その曲の DEMOSTART から。
                core.log.clear()
                QTest.mouseClick(row(b), Qt.LeftButton)
                self.assertTrue(row(b)._accent)
                self.assertFalse(row(a)._accent)
                _wait(250)
                self.assertEqual(core.log, [('stop',), ('play', 'b', 3.0)])
                # 続けて押したら最後の曲だけ読む。
                core.log.clear()
                host.library.click_song(c)
                host.library.click_song(a)
                host.library.click_song(c)
                _wait(250)
                self.assertEqual(core.log, [('stop',), ('play', 'c', 7.0)])
                # Enter は2回目の押下と同じ(選んでいる曲なら難易度選択へ)。
                host.library._last_select = 0.0
                row(c).setFocus()
                QTest.keyClick(row(c), Qt.Key_Return)
                self.assertIs(host.body.currentWidget(), host.difficulty)
                self.assertEqual(host.difficulty.path, c)
                host.difficulty.btn_back.click()
                # 演奏などの流れは今までどおり(Play → 曲名表示)。
                host.library._last_select = 0.0
                host.library.click_song(c)
                host.difficulty.btn_play.click()
                self.assertIs(host.body.currentWidget(), host.intro)
                # Player を出たら止める。
                core.log.clear()
                host.stop_preview()
                self.assertIn(('stop',), core.log)
            finally:
                host.shutdown()
                host.close()
                host.deleteLater()

    @real_qt_test
    def test_leaving_before_delay_does_not_start(self):
        from neotja.player.host import PlayerHost
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'a', 'a', 12.5)
            host = PlayerHost({'master_volume': 0.0}, lambda: None)
            host.resize(1600, 900)
            host.show()
            host.on_shown()
            try:
                host.library.click_song(a)
                host.stop_preview()                    # Player を出た
                _wait(250)
                core = Core.instances[0]
                self.assertNotIn(('play', 'a', 12.5), core.log)
            finally:
                host.shutdown()
                host.close()
                host.deleteLater()


    @real_qt_test
    def test_switching_list_stops_preview(self):
        """一覧を替えたら(最近/保存済み・ジャンル・フォルダの段・検索の結果)
        試聴を止める。難易度選択から戻ったときは止めない(上の試験)。"""
        from neotja.player.host import PlayerHost
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory() as d:
            a = _chart(Path(d) / 'songs' / 'a', 'a', 12.5)
            sub = Path(d) / 'songs' / 'sub'
            _chart(sub / 'b', 'b', 3)
            cfg = {'master_volume': 0.0,
                   'player_folders': [{'simulator': 'TNDE', 'path': str(Path(d) / 'songs')}]}
            song_lists.add_recent(cfg, a)
            host = PlayerHost(cfg, lambda: None)
            host.resize(1600, 900)
            host.show()
            host.on_shown()
            lib = host.library
            try:
                _wait(50)
                core = Core.instances[0]

                def preview():
                    lib._last_select = 0.0
                    lib._chosen = ''
                    lib.click_song(a)
                    _wait(250)
                    self.assertEqual(core.current, a)
                    core.log.clear()

                preview()
                lib.show_section('saved')
                self.assertIsNone(core.current)
                self.assertIn(('stop',), core.log)
                # 同じ一覧をもう一度押しただけなら替わっていない。
                lib.show_section('recent')
                preview()
                lib.show_section('recent')
                self.assertEqual(core.current, a)
                # 登録フォルダ → その下の段 → 検索の結果。
                lib.show_section('folder', str(Path(d) / 'songs'))
                self.assertIsNone(core.current)
                preview()
                lib.open_dir(str(sub))
                self.assertIsNone(core.current)
                preview()
                lib.page_folder.search.setText('b')
                _wait(400)
                self.assertIsNone(core.current)
                # 選んでから押すまでの間に替えたら、試聴は始まらない。
                lib._chosen = ''
                lib._last_select = 0.0
                lib.click_song(a)
                lib.show_section('saved')
                _wait(250)
                self.assertIsNone(core.current)
            finally:
                host.shutdown()
                host.close()
                host.deleteLater()


if __name__ == '__main__':
    unittest.main()
