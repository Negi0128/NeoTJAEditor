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
            self.assertEqual(dock.fps_label.pos().x(),
                             round(dock.VIEW_FPS_MARGIN[0] * dock.view_fps_ratio()))
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


class DockViewFpsScaleTests(unittest.TestCase):
    """fps 表示の字と箱は、ゲーム画面の幅に対して決まった比(CPU・GPU とも)。"""

    def _run(self, **gpu):
        from PySide6.QtGui import QFontInfo
        from PySide6.QtWidgets import QWidget
        from neotja.player.core import PlayerCore
        cfg = settings.default_settings()
        cfg.update(master_volume=0.0, **gpu)
        core = PlayerCore(cfg)
        dock = core.dock
        gpw = dock.game_preview_window
        # Player の画面(PlayerHost)は QSS で「QWidget { font-size: 13px }」を
        # 当てている(StartScreen._qss)。その中では setFont だけでは字が
        # 変わらず 13px のままだった。同じ QSS の親の中に置いて確かめる。
        holder = QWidget()
        holder.setStyleSheet('QWidget { font-size: 13px; }')
        holder.resize(2600, 1500)
        gpw.setParent(holder)
        dpi = None
        try:
            for detail in (False, True):
                gpw.set_view_only(True, with_bottom=detail)
                dock.set_view_fps(True)
                holder.show()
                gpw.show()
                rows = []
                for w, h in ((480, 270), (1280, 720), (1920, 1009), (2560, 1440)):
                    gpw.fit_view_only(w, h)
                    _wait(20)
                    lbl, host = dock.fps_label, gpw.scaled_host
                    dpi = float(lbl.logicalDpiY() or 96)
                    font_px = lbl.font().pointSizeF() * dpi / 72.0
                    # 実際に使われる字(丸めた px)も比どおり。
                    self.assertLessEqual(
                        abs(QFontInfo(lbl.font()).pixelSize() - font_px), 1.0)
                    rows.append((host.width(), font_px, lbl.height(), lbl.x()))
                base = dock.VIEW_FPS_BASE_W
                for hw, font_px, lh, lx in rows:
                    k = hw / base
                    # 字は丸めずに比のまま。
                    self.assertAlmostEqual(font_px / hw, dock.VIEW_FPS_FONT_PX / base,
                                           places=5)
                    # 箱と余白は1px の丸めだけ。
                    self.assertLessEqual(abs(lh - dock.VIEW_FPS_BOX[1] * k), 0.51)
                    self.assertLessEqual(abs(lx - dock.VIEW_FPS_MARGIN[0] * k), 0.51)
                # 小さい画面では原寸(24px)より小さく、比どおりに縮む。
                self.assertAlmostEqual(rows[0][1], 24.0 * rows[0][0] / 1280.0, places=3)
                self.assertEqual(dock.VIEW_FPS_FONT_PX, 24.0)          # 2倍(利用者の指定)
            # 外すと Editor と同じ作り(字の大きさを QSS に書かない)へ戻る。
            dock.set_view_fps(False)
            dock._scale_lane_overlays(1.0)
            self.assertNotIn('font-size', dock.fps_label.styleSheet())
        finally:
            core.stop_audio()
            core.shutdown()
            dock.chart_preview._timer.stop()
            gpw.close()
            dock.deleteLater()
            gpw.setParent(None)
            holder.close()
            holder.deleteLater()

    @real_qt_test
    def test_ratio_cpu(self):
        self._run(gpu_render=False)

    @real_qt_test
    def test_ratio_gpu_widget(self):
        self._run(gpu_render=True, gpu_screen_window=False)

    @real_qt_test
    def test_ratio_gpu_native_window(self):
        self._run(gpu_render=True, gpu_screen_window=True)


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
