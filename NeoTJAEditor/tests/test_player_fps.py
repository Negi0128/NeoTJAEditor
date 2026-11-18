"""Player の fps 表示: 詳細モードでは常に、通常プレイでは設定「FPS表示」
(既定 OFF)のときだけ。絵だけの表示でも左上に出る。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_fps.py`
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

_TMP = tempfile.mkdtemp(prefix='neotja-fps-test-')
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


def _chart(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'song.ogg').write_bytes(b'x')
    p = folder / 'a.tja'
    p.write_text('TITLE:a\nBPM:120\nWAVE:song.ogg\nCOURSE:Oni\nLEVEL:9\n#START\n1111,\n#END\n',
                 encoding='cp932')
    return str(p)


class DockViewFpsTests(unittest.TestCase):
    @real_qt_test
    def test_fps_label_survives_view_only_and_sits_top_left(self):
        from neotja.player.core import PlayerCore
        from neotja.ui_scale import px
        cfg = settings.default_settings()
        cfg.update(master_volume=0.0, gpu_render=False)
        core = PlayerCore(cfg)
        dock = core.dock
        gpw = dock.game_preview_window
        try:
            gpw.set_view_only(True)
            self.assertTrue(dock.fps_label.isHidden())          # 既定は出さない
            dock.set_view_fps(True)
            self.assertFalse(dock.fps_label.isHidden())
            s = dock._lane_scale
            self.assertEqual(dock.fps_label.pos().x(), px(dock.LANE_OVERLAY_X0, s))
            # 詳細モード(下の段つき)へ移っても残る。ほかのボタンは隠れたまま。
            gpw.set_view_only(True, with_bottom=True)
            self.assertFalse(dock.fps_label.isHidden())
            self.assertTrue(dock.mode_button.isHidden())
            self.assertTrue(dock.record_button.isHidden())
            dock.set_view_fps(False)
            self.assertTrue(dock.fps_label.isHidden())
            # 外したあとは Editor と同じ並び(左上の隅ではない)。
            dock._scale_lane_overlays(dock._lane_scale)
            self.assertGreater(dock.fps_label.pos().x(), px(dock.LANE_OVERLAY_X0, s))
        finally:
            core.stop_audio()
            core.shutdown()
            dock.chart_preview._timer.stop()
            gpw.close()
            dock.deleteLater()


class FakeDock:
    def __init__(self, log):
        from PySide6.QtWidgets import QWidget

        class Gpw(QWidget):
            def set_view_only(self, on, with_bottom=False):
                pass

            def set_controls_locked(self, on, key_cb=None):
                pass

            def fit_view_only(self, w, h):
                self.resize(640, 360)
        self.game_preview_window = Gpw()
        self.chart_preview = type('C', (), {'reset_to_start': lambda s: None})()
        self.log = log

    def set_view_fps(self, on):
        self.log.append(bool(on))

    def set_speed_row_visible(self, on):
        pass

    def set_master_volume(self, v):
        pass

    def set_volume(self, v):
        pass

    def cancel_predecode(self):
        pass


class FakeCore:
    def __init__(self, cfg):
        self.fps = []
        self.dock = FakeDock(self.fps)

    def __getattr__(self, name):
        return lambda *a, **k: True


class HostFpsTests(unittest.TestCase):
    @real_qt_test
    def test_detail_always_normal_only_when_setting_on(self):
        from neotja.player.host import PlayerHost
        orig = PlayerHost.core_factory
        PlayerHost.core_factory = FakeCore
        try:
            with tempfile.TemporaryDirectory() as d:
                a = _chart(Path(d))
                cfg = {'master_volume': 0.0}
                host = PlayerHost(cfg, lambda: None)
                host.resize(1600, 900)
                host.show()
                host.library.choose(a)
                core = host._core
                host._enter_play(a, 'Oni')
                self.assertEqual(core.fps[-1], False)            # 既定 OFF
                host.switch_to_detail()
                self.assertEqual(core.fps[-1], True)             # 詳細は常に
                host.switch_to_normal()
                self.assertEqual(core.fps[-1], False)
                # 設定パネルの「FPS表示」(他と同じ ON/OFF)。
                page = host.difficulty
                sw = page.settings.switches['player_show_fps']
                page.settings.set_values(cfg)
                self.assertFalse(sw.isChecked())
                sw.click()
                self.assertTrue(cfg['player_show_fps'])
                host._enter_play(a, 'Oni')
                self.assertEqual(core.fps[-1], True)
                host._end_play()
                host.shutdown()
                host.deleteLater()
        finally:
            PlayerHost.core_factory = orig

    def test_setting_is_written_back_by_player_core(self):
        from neotja.player.core import PLAYER_KEYS
        self.assertIn('player_show_fps', PLAYER_KEYS)


if __name__ == '__main__':
    unittest.main()
