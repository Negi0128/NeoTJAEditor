"""詳細モードの右上の数字(BPM・HS・見た目BPM)。値が変わったら縁が青く光る。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_value_readout.py`
"""
import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-readout-test-')
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


class Clock:
    def __init__(self):
        self.t = 10.0

    def __call__(self):
        return self.t


class ReadoutWidgetTests(unittest.TestCase):
    @real_qt_test
    def test_first_value_is_quiet_then_changes_flash_and_fade(self):
        from neotja.value_readout import ValueReadout
        rd = ValueReadout()
        clock = Clock()
        rd.clock = clock
        rd.set_values(120.0, 1.0)
        self.assertEqual(rd.texts, ('120', '1', '120'))
        self.assertEqual(rd.flash_level(), 0.0)
        self.assertFalse(rd.set_values(120.0, 1.0))          # 同じなら何もしない
        self.assertTrue(rd.set_values(180.0, 1.5))
        self.assertEqual(rd.texts, ('180', '1.5', '270'))
        self.assertEqual(rd.flash_level(), 1.0)
        self.assertEqual(rd.border_color().name(), rd.FLASH.name())
        clock.t += rd.FLASH_MS / 2000.0
        self.assertAlmostEqual(rd.flash_level(), 0.5, places=3)
        clock.t += 1.0
        self.assertEqual(rd.flash_level(), 0.0)
        self.assertEqual(rd.border_color(), rd.BORDER)
        rd.set_values(None, None)
        self.assertEqual(rd.texts, ('-', '-', '-'))
        rd.deleteLater()


BODY = '\n'.join(['1111,', '1111,', '#BPMCHANGE 180', '#SCROLL 1.5', '1111,', '1111,'])


class DockReadoutTests(unittest.TestCase):
    @real_qt_test
    def test_follows_cursor_in_detail_mode(self):
        from neotja.player.core import PlayerCore
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with wave.open(str(root / 'test.wav'), 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(b'\x00\x00' * 44100 * 12)
            tja = root / 'test.tja'
            tja.write_text('TITLE:t\nBPM:120\nOFFSET:0\nWAVE:test.wav\nCOURSE:Oni\nLEVEL:5\n'
                           '#START\n%s\n#END\n' % BODY, encoding='cp932')
            cfg = settings.default_settings()
            cfg.update(master_volume=0.0, gpu_render=False)
            core = PlayerCore(cfg)
            try:
                dock = core.dock
                self.assertIsNone(dock.value_readout())           # 既定では作らない
                self.assertTrue(core.load(str(tja), course_key='Oni'))
                for _ in range(300):
                    if core.chart_ready():
                        break
                    _wait(20)
                self.assertTrue(core.use_detail_mode())
                gpw = dock.game_preview_window
                gpw.set_view_only(True, with_bottom=True)
                gpw.show()
                _wait(200)
                dock.set_value_readout(True)
                rd = dock.value_readout()
                self.assertTrue(rd.isVisible())
                self.assertEqual(rd.texts, ('120', '1', '120'))
                self.assertEqual(rd.flash_level(), 0.0)
                # 命令パネルは出さない(読むだけ)。数字の表示は右上。
                if dock.command_panel is not None:
                    self.assertFalse(dock.command_panel.isVisible())
                host = gpw.scaled_host
                self.assertGreater(rd.x(), host.width() // 2)
                self.assertLess(rd.y() + rd.height(), host.height() // 2)
                # カーソルを3小節目(BPM 180・HS 1.5)へ。
                ce = dock.chart_edit
                for _ in range(80):
                    ce.move_cursor(1)
                    if ce._cur_measure >= 2:
                        break
                _wait(150)
                self.assertEqual(rd.texts, ('180', '1.5', '270'))
                self.assertGreater(rd.flash_level(), 0.0)
                dock.set_value_readout(False)
                self.assertFalse(rd.isVisible())
                self.assertFalse(dock._value_readout_timer.isActive())
            finally:
                core.stop_audio()
                core.shutdown()


class HostReadoutTests(unittest.TestCase):
    @real_qt_test
    def test_detail_turns_readout_on_and_normal_off(self):
        from PySide6.QtWidgets import QWidget
        from neotja.player.host import PlayerHost
        calls = []

        class Gpw(QWidget):
            def set_view_only(self, on, with_bottom=False):
                pass

            def set_controls_locked(self, on, key_cb=None):
                pass

            def fit_view_only(self, w, h):
                pass

        class Dock:
            def __init__(self):
                self.game_preview_window = Gpw()
                self.game_screen = None
                self.chart_edit = QWidget()
                self.chart_preview = type('C', (), {'reset_to_start': lambda s: None})()

            def set_value_readout(self, on):
                calls.append(on)

            def set_speed_row_visible(self, on):
                pass

            def cancel_predecode(self):
                pass

            def set_volume(self, v):
                pass

        class Core:
            def __init__(self, cfg):
                self.dock = Dock()

            def __getattr__(self, name):
                return lambda *a, **k: None

        orig = PlayerHost.core_factory
        PlayerHost.core_factory = Core
        try:
            host = PlayerHost({'master_volume': 0.0}, lambda: None)
            host.resize(1200, 800)
            host.show()
            host._ensure_core()
            host._enter_play('x.tja', 'Oni')
            self.assertEqual(calls, [])
            host.switch_to_detail()
            self.assertEqual(calls, [True])
            host.switch_to_normal()
            self.assertEqual(calls, [True, False])
            host.switch_to_detail()
            host._end_play()
            self.assertEqual(calls, [True, False, True, False])
            host.shutdown()
            host.deleteLater()
        finally:
            PlayerHost.core_factory = orig


if __name__ == '__main__':
    unittest.main()
