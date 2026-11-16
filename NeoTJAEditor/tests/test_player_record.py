"""Player の「録画」: Editor と同じ録画の窓を開く・試聴を止めて戻す・2つ開かない。

書き出しそのものはしない(窓を開いて閉じるだけ)。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_record.py`
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-record-test-')
_CFG = Path(_TMP) / 'settings.json'
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import editor_session, open_test_chart, real_qt_test  # noqa: E402


def _wait(ms):
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QTest.qWait(10)
        time.sleep(0.002)


def _png(path, color='#3366cc'):
    from PySide6.QtGui import QColor, QImage
    img = QImage(64, 64, QImage.Format_RGB32)
    img.fill(QColor(color))
    img.save(str(path))
    return str(path)


def _chart(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'song.ogg').write_bytes(b'x')
    p = folder / (name + '.tja')
    p.write_text('TITLE:%s\nBPM:120\nWAVE:song.ogg\nCOURSE:Oni\nLEVEL:9\n#START\n1111,\n#END\n'
                 'COURSE:Hard\nLEVEL:6\n#START\n1010,\n#END\n' % name, encoding='cp932')
    return str(p)


def _fakes(wave):
    from PySide6.QtWidgets import QDialog, QWidget

    class FakeDialog(QDialog):
        made = []

        def __init__(self, ctx, preview, offset, song, secs, out_dir, parent=None,
                     layout='game', title='', bg_path=None, multi_previews=None):
            super().__init__(parent)
            self.ctx, self.preview, self.offset, self.song = ctx, preview, offset, song
            self.secs, self.out_dir, self.layout = secs, out_dir, layout
            self.title_text, self.bg_path = title, bg_path
            self.raised = 0
            FakeDialog.made.append(self)

        def raise_(self):
            self.raised += 1
            super().raise_()

        def is_busy(self):
            return False

    class FakeDock:
        def __init__(self):
            self.game_preview_window = QWidget()
            self.game_screen = QWidget()
            self.chart_preview = type('C', (), {'reset_to_start': lambda s: None})()
            self.spin_offset = type('S', (), {'value': lambda s: -1.25})()

        def set_volume(self, v):
            pass

        def set_master_volume(self, v):
            pass

        def cancel_predecode(self):
            pass

        def wave_path(self):
            return wave

        def song_duration_seconds(self):
            return 42.0

        def is_read_only(self):
            return True

    class FakeCore:
        instances = []

        def __init__(self, cfg):
            self.dock = FakeDock()
            self.log = []
            self.ready_after = 0
            FakeCore.instances.append(self)

        def play_demo(self, path, demo):
            self.log.append('demo')

        def stop_audio(self):
            self.log.append('stop')

        def shutdown(self):
            pass

        def load(self, path, course_key=None, play_mode=None):
            self.log.append(('load', course_key, str(play_mode)))
            return True

        def chart_ready(self):
            if self.ready_after > 0:
                self.ready_after -= 1
                return False
            return True

        def build_preview(self, content):
            return {'notes': [], 'course_key': 'x', 'built': True}
    return FakeDialog, FakeCore


class HostRecordTests(unittest.TestCase):
    def setUp(self):
        from neotja.player.host import PlayerHost
        self._orig = (PlayerHost.core_factory, PlayerHost.record_dialog_cls)

    def tearDown(self):
        from neotja.player.host import PlayerHost
        PlayerHost.core_factory, PlayerHost.record_dialog_cls = self._orig

    def _host(self, d, top=None):
        from neotja.player import song_lists
        from neotja.player.host import PlayerHost
        a = _chart(Path(d) / 'a', 'a')
        wave = str(Path(d) / 'a' / 'song.ogg')
        FakeDialog, FakeCore = _fakes(wave)
        FakeDialog.made = []
        PlayerHost.core_factory = FakeCore
        PlayerHost.record_dialog_cls = FakeDialog
        self.FakeDialog, self.FakeCore = FakeDialog, FakeCore
        cfg = {'master_volume': 0.0, 'preview_volume': 0.5}
        self.jacket = _png(Path(d) / 'jacket.png')
        self.bg = _png(Path(d) / 'bg.png', '#226633')
        song_lists.set_chart_art(cfg, a, jacket=self.jacket, background=self.bg)
        host = PlayerHost(cfg, lambda: None)
        host.show_record_errors = False
        if top is not None:
            from PySide6.QtWidgets import QVBoxLayout
            QVBoxLayout(top).addWidget(host)
            top.resize(1600, 900)
            top.show()
        else:
            host.resize(1600, 900)
            host.show()
        host.on_shown()
        host.library.choose(a)
        core = FakeCore.instances[-1]
        core.log.clear()
        return host, a, core

    @real_qt_test
    def test_record_opens_one_dialog_and_restarts_preview_after_close(self):
        from neotja.constants import PLAY_MODE_WATCH
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._host(d)
            core.ready_after = 3                    # 音源の読み込みを少し待つ
            host.difficulty.select_slot('Hard')
            host.difficulty.btn_record.click()
            self.assertEqual(core.log[:2], ['stop', ('load', 'Hard', str(PLAY_MODE_WATCH))])
            self.assertEqual(self.FakeDialog.made, [])
            _wait(400)
            self.assertEqual(len(self.FakeDialog.made), 1)
            dlg = self.FakeDialog.made[0]
            self.assertTrue(dlg.isVisible())
            self.assertIs(host.record_dialog(), dlg)
            self.assertEqual(dlg.ctx.current_file, a)
            self.assertTrue(dlg.preview.get('built'))          # 再生と同じ組み立て
            self.assertEqual(dlg.song, str(Path(d) / 'a' / 'song.ogg'))
            self.assertEqual((dlg.offset, dlg.secs, dlg.layout), (-1.25, 42.0, 'game'))
            self.assertEqual(dlg.title_text, 'a')
            self.assertEqual(os.path.normcase(dlg.bg_path), os.path.normcase(self.bg))
            self.assertEqual(os.path.normcase(dlg.ctx.lyric_jackets.base),
                             os.path.normcase(self.jacket))
            # Editor の譜面ごとのジャケットへ書く口は持たせない。
            self.assertFalse(hasattr(dlg.ctx, 'set_jacket_base'))
            self.assertNotIn('demo', core.log)                 # 開いているあいだは止めたまま
            # もう一度押しても2つ目は作らない(前へ出すだけ)。
            host.difficulty.btn_record.click()
            _wait(200)
            self.assertEqual(len(self.FakeDialog.made), 1)
            self.assertGreaterEqual(dlg.raised, 1)
            # 閉じたら置き場を空けて、試聴をやり直す。
            dlg.reject()
            _wait(100)
            self.assertIsNone(host.record_dialog())
            self.assertEqual(core.log[-1], 'demo')
            self.assertIs(host.body.currentWidget(), host.difficulty)
            self.assertEqual(os.listdir(str(Path(d) / 'a')), ['a.tja', 'song.ogg'])  # .jacket.json は作らない
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_editor_dialog_already_open_is_raised_instead(self):
        from PySide6.QtWidgets import QDialog, QWidget
        with tempfile.TemporaryDirectory() as d:
            top = QWidget()
            editor_dlg = QDialog(top)
            editor_dlg.show()
            top._record_dialog = editor_dlg                    # Editor の録画の窓
            host, a, core = self._host(d, top=top)
            self.assertIs(host._record_owner(), top)
            host.difficulty.btn_record.click()
            _wait(200)
            self.assertEqual(self.FakeDialog.made, [])
            self.assertNotIn('stop', core.log)                 # 試聴も止めない
            self.assertIs(top._record_dialog, editor_dlg)
            # Editor の窓が閉じていれば、Player の窓は同じ置き場に入る。
            top._record_dialog = None
            editor_dlg.close()
            host.difficulty.btn_record.click()
            _wait(200)
            self.assertEqual(len(self.FakeDialog.made), 1)
            self.assertIs(top._record_dialog, self.FakeDialog.made[0])
            self.FakeDialog.made[0].reject()
            _wait(100)
            self.assertIsNone(top._record_dialog)
            host.shutdown()
            top.close()
            top.deleteLater()

    @real_qt_test
    def test_missing_audio_gives_up_and_restarts_preview(self):
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._host(d)
            core.dock.wave_path = lambda: ''
            host.difficulty.btn_record.click()
            _wait(200)
            self.assertEqual(self.FakeDialog.made, [])
            self.assertIn('WAVE', host.record_failed)
            self.assertEqual(core.log[-1], 'demo')
            host.shutdown()
            host.deleteLater()


# ----------------------------------------------------------------------
# 本物の PlayerCore と RecordDialog(書き出しはしない)
# ----------------------------------------------------------------------
class RealDialogTests(unittest.TestCase):
    @real_qt_test
    def test_real_dialog_gets_chart_art_and_dock_stays_read_only(self):
        from neotja.player import song_lists
        from neotja.player.core import PlayerCore
        from neotja.player.record import build_dialog
        with editor_session() as (win, root):
            open_test_chart(win, root, '1111,\n1111,\n1111,\n1111,', bpm=120)
            tja = root / 'test.tja'
            raw0 = tja.read_bytes()
            cfg = win.config_data
            cfg.update(master_volume=0.0, gpu_render=False)
            jacket = _png(root / 'jk.png')
            bg = _png(root / 'bg.png', '#552222')
            song_lists.set_chart_art(cfg, str(tja), jacket=jacket, background=bg)
            rec_bg0 = (cfg.get('rec_bg_path'), cfg.get('rec_bg_art'))
            core = PlayerCore(cfg)
            dlg = None
            try:
                self.assertTrue(core.load(str(tja), course_key='Oni'))
                for _ in range(300):
                    if core.chart_ready():
                        break
                    _wait(20)
                starts = []
                dlg, why = build_dialog(core, str(tja), cfg, win)
                self.assertIsNotNone(dlg, why)
                dlg._start = lambda *a: starts.append(1)        # 書き出しはしない
                dlg.show()
                _wait(300)
                self.assertEqual(os.path.normcase(dlg._bg.path), os.path.normcase(bg))
                self.assertEqual(os.path.normcase(dlg._bg.art), os.path.normcase(jacket))
                self.assertEqual(dlg._mw.current_file, str(tja))
                self.assertFalse(dlg.is_busy())
                self.assertTrue(core.dock.is_read_only())
                self.assertTrue(core.dock.chart_edit.read_only())
                dlg.reject()
                _wait(200)
                self.assertEqual(starts, [])
                self.assertEqual((cfg.get('rec_bg_path'), cfg.get('rec_bg_art')), rec_bg0)
                self.assertEqual([n for n in os.listdir(str(root)) if n.endswith('.jacket.json')], [])
                self.assertEqual(tja.read_bytes(), raw0)
            finally:
                if dlg is not None:
                    dlg.deleteLater()
                core.stop_audio()
                core.shutdown()
                core.dock.chart_preview._timer.stop()
                core.dock.game_preview_window.hide()
                core.dock.game_preview_window.close()
                core.dock.deleteLater()


if __name__ == '__main__':
    unittest.main()
