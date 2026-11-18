"""「TJAを開く」のあとの画面(player/opened.py): 曲を見せて、保存済みに追加・
すぐに再生・戻る。ファイルを選んだときも落としたときも同じ。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_opened.py`
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-opened-test-')
_CFG = Path(_TMP) / 'settings.json'
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


def _chart(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'song.ogg').write_bytes(b'x')
    p = folder / (name + '.tja')
    p.write_text('TITLE:%s\nSUBTITLE:--副題\nBPM:120\nWAVE:song.ogg\nDEMOSTART:3\n'
                 'COURSE:Oni\nLEVEL:9\n#START\n1111,\n#END\n'
                 'COURSE:Hard\nLEVEL:6\n#START\n1111,\n#END\n' % name, encoding='cp932')
    return str(p)


class Core:
    instances = []

    def __init__(self, cfg):
        self.log = []
        self.dock = type('D', (), {'cancel_predecode': lambda s: None})()
        Core.instances.append(self)

    def play_demo(self, path, demo):
        self.log.append(('demo', path))

    def stop_audio(self):
        self.log.append('stop')

    def demo_active_for(self, path):
        return False

    def __getattr__(self, name):
        return lambda *a, **k: None


class OpenedTests(unittest.TestCase):
    def setUp(self):
        from neotja.player.host import PlayerHost
        self._orig = PlayerHost.core_factory
        PlayerHost.core_factory = Core

    def tearDown(self):
        from neotja.player.host import PlayerHost
        PlayerHost.core_factory = self._orig

    def _host(self, d):
        from neotja.player.host import PlayerHost
        a = _chart(Path(d) / 'a', 'a')
        saves = []
        host = PlayerHost({'master_volume': 0.0}, lambda: saves.append(1))
        host.resize(1600, 900)
        host.show()
        host.on_shown()
        return host, a, saves

    @real_qt_test
    def test_open_file_shows_song_then_add_and_play(self):
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory() as d:
            host, a, saves = self._host(d)
            # ファイルを選んだ(窓は開かない: 選んだあとの合図を直に出す)。
            host.library.file_opened.emit(a)
            _wait(500)
            self.assertIs(host.body.currentWidget(), host.opened)
            self.assertFalse(host.top_bar.isVisible())
            op = host.opened
            self.assertEqual(op.title, 'a')
            self.assertEqual(op.subtitle, '副題')
            self.assertEqual([c for c, _l in op.courses], ['Oni', 'Hard'])
            self.assertEqual([t for _n, t, _r in op.chip_rects()], ['おに 9', 'むずかしい 6'])
            self.assertTrue(op.btn_add.isEnabled())
            self.assertEqual(op.btn_add.text(), op.ADD_TEXT)
            # 保存済みに追加 → 「保存済み」・押せない・この画面のまま。
            n = len(saves)
            op.btn_add.click()
            self.assertTrue(song_lists.is_saved(host.cfg, a))
            self.assertGreater(len(saves), n)
            self.assertFalse(op.btn_add.isEnabled())
            self.assertEqual(op.btn_add.text(), op.ADDED_TEXT)
            self.assertIs(host.body.currentWidget(), host.opened)
            # 同じ曲を開き直しても「保存済み」のまま。
            host.show_opened(a)
            _wait(450)
            self.assertTrue(op.is_added())
            # すぐに再生 → 難易度選択(試聴つき)。
            core = Core.instances[-1]
            core.log.clear()
            op.btn_play.click()
            _wait(500)
            self.assertIs(host.body.currentWidget(), host.difficulty)
            self.assertEqual(host.difficulty.path, a)
            self.assertIn(('demo', a), core.log)
            self.assertEqual(host.library.selected_path(), a)
            # 難易度選択の「戻る」は選曲へ。
            host.difficulty.btn_back.click()
            self.assertIs(host.body.currentWidget(), host.library)
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_back_and_drop(self):
        from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
        from PySide6.QtGui import QDropEvent
        from PySide6.QtWidgets import QApplication
        from neotja.player import song_lists
        with tempfile.TemporaryDirectory() as d:
            host, a, saves = self._host(d)
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(a)])
            ev = QDropEvent(QPointF(200, 200), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            host.dropEvent(ev)
            _wait(500)
            self.assertIs(host.body.currentWidget(), host.opened)
            self.assertEqual(host.opened.path, a)
            self.assertTrue(host.opened.btn_add.isEnabled())
            host.opened.btn_back.click()
            _wait(500)
            self.assertIs(host.body.currentWidget(), host.library)
            self.assertTrue(host.top_bar.isVisible())
            self.assertFalse(song_lists.is_saved(host.cfg, a))
            # Esc でも戻る。
            host.show_opened(a)
            _wait(450)
            from PySide6.QtTest import QTest
            QTest.keyClick(host.opened, Qt.Key_Escape)
            _wait(450)
            self.assertIs(host.body.currentWidget(), host.library)
            host.shutdown()
            host.deleteLater()


if __name__ == '__main__':
    unittest.main()
