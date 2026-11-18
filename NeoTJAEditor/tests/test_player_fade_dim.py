"""再生画面へのフェード: GL の窓の上には黒を重ねられないので、ゲーム画面
自身が黒を描く(_fade_dim)。入るときは黒から明けて、明け切ってから曲を流す。
出るとき(結果・選曲へ)も画面自身が暗くなる。録画の画面は触らない。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_fade_dim.py`
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-fade-dim-test-')
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
    p.write_text('TITLE:%s\nBPM:120\nWAVE:song.ogg\nDEMOSTART:3\n'
                 'COURSE:Oni\nLEVEL:9\n#START\n1111,\n#END\n' % name, encoding='cp932')
    return str(p)


def _dim_page_class():
    from PySide6.QtWidgets import QWidget

    class DimPage(QWidget):
        """画面自身が黒を描ける相手(再生画面の代わり)。"""
        def __init__(self, native=False):
            super().__init__()
            self.native_gl = native
            self.dims = []
            self.dim = 0.0
            self.visible_dims = []

        def supports_fade_dim(self):
            return True

        def set_fade_dim(self, v):
            self.dim = float(v)
            self.dims.append(round(self.dim, 3))
            if self.isVisible():
                self.visible_dims.append(round(self.dim, 3))
    return DimPage


class GameScreenDimTests(unittest.TestCase):
    @real_qt_test
    def test_dim_paints_black_over_cpu_screen_and_defaults_off(self):
        from PySide6.QtGui import QImage, QPainter
        from neotja.chart_preview_widget import ChartPreviewWidget
        from neotja.game_screen import GameScreenWidget, _GameScreenBase
        self.assertEqual(_GameScreenBase._fade_dim, 0.0)
        cp = ChartPreviewWidget()
        cp.game_state = lambda: (0.0, 0, None)
        gs = GameScreenWidget(cp)
        self.assertEqual(gs.fade_dim(), 0.0)

        def shot():
            sz = gs.logical_size()
            img = QImage(sz, QImage.Format_RGB32)
            img.fill(0xff808080)
            p = QPainter(img)
            gs.paint_screen(p)
            p.end()
            return img

        def mean(img):
            tot = 0
            n = 0
            for y in range(0, img.height(), 37):
                for x in range(0, img.width(), 41):
                    c = img.pixelColor(x, y)
                    tot += c.red() + c.green() + c.blue()
                    n += 3
            return tot / n
        base = mean(shot())
        self.assertGreater(base, 5)
        gs.set_fade_dim(0.5)
        half = mean(shot())
        gs.set_fade_dim(1.0)
        full = mean(shot())
        self.assertLess(half, base * 0.6)
        self.assertEqual(full, 0)
        gs.set_fade_dim(5)            # 範囲外は 1 に丸める
        self.assertEqual(gs.fade_dim(), 1.0)
        gs.set_fade_dim(0.0)
        self.assertAlmostEqual(mean(shot()), base, delta=1.0)
        gs.deleteLater()


class FadeStackDimTests(unittest.TestCase):
    @real_qt_test
    def test_into_dim_page_starts_black_and_ends_clear(self):
        from PySide6.QtWidgets import QWidget
        from neotja.player.transitions import FadeStack
        DimPage = _dim_page_class()
        for native in (False, True):
            with self.subTest(native=native):
                stack = FadeStack()
                stack.resize(800, 450)
                a = QWidget()
                page = DimPage(native)
                stack.addWidget(a)
                stack.addWidget(page)
                stack.show()
                _wait(30)
                done = []
                stack.fade_to(page, mode='black', ms=200,
                              done=lambda: done.append((page.dim, stack.is_fading())))
                _wait(450)
                self.assertIs(stack.currentWidget(), page)
                # 出た最初のコマから真っ黒で、だんだん明けて 0 で終わる。
                self.assertEqual(page.visible_dims[0], 1.0)
                self.assertEqual(page.dims[-1], 0.0)
                mids = [d for d in page.visible_dims if 0.0 < d < 1.0]
                self.assertTrue(mids, page.visible_dims)
                self.assertEqual(page.visible_dims, sorted(page.visible_dims, reverse=True))
                # 明け切ってから done(曲はここで流す)。
                self.assertEqual(done, [(0.0, False)])
                stack.deleteLater()

    @real_qt_test
    def test_out_of_dim_page_darkens_page_itself(self):
        from PySide6.QtWidgets import QWidget
        from neotja.player.transitions import FadeStack
        DimPage = _dim_page_class()
        stack = FadeStack()
        stack.resize(800, 450)
        page = DimPage(True)
        b = QWidget()
        stack.addWidget(page)
        stack.addWidget(b)
        stack.setCurrentWidget(page)
        stack.show()
        _wait(30)
        page.visible_dims.clear()
        done = []
        stack.fade_to(b, mode='black', ms=200, done=lambda: done.append(1))
        _wait(60)
        self.assertFalse(stack._overlay.isVisible())     # 重ねた黒は使わない
        _wait(400)
        self.assertIs(stack.currentWidget(), b)
        ups = page.visible_dims
        self.assertTrue(ups and ups == sorted(ups))
        self.assertEqual(ups[-1], 1.0)
        self.assertEqual(page.dim, 0.0)                  # 離れたら戻す
        self.assertEqual(done, [1])
        stack.deleteLater()

    @real_qt_test
    def test_cancel_resets_dim(self):
        from PySide6.QtWidgets import QWidget
        from neotja.player.transitions import FadeStack
        DimPage = _dim_page_class()
        stack = FadeStack()
        stack.resize(800, 450)
        a = QWidget()
        page = DimPage()
        stack.addWidget(a)
        stack.addWidget(page)
        stack.show()
        stack.fade_to(page, mode='black', ms=400)
        _wait(300)                     # 明けている途中
        self.assertGreater(page.dim, 0.0)
        stack.cancel()
        self.assertEqual(page.dim, 0.0)
        self.assertIs(stack.currentWidget(), page)
        stack.deleteLater()


def _fake_core_class():
    from PySide6.QtWidgets import QWidget

    class FakeGpw(QWidget):
        def __init__(self):
            super().__init__()
            self.resize(640, 360)

        def set_view_only(self, on, with_bottom=False):
            pass

        def fit_view_only(self, w, h):
            self.resize(min(w, int(h * 16 / 9)), min(h, int(w * 9 / 16)))

    class FakeScreen:
        def __init__(self, log):
            self.log = log
            self.dim = 0.0

        def set_fade_dim(self, v):
            self.dim = float(v)
            self.log.append(('dim', round(self.dim, 3)))

    class FakeDock:
        def __init__(self, log):
            self.game_preview_window = FakeGpw()
            self.game_screen = FakeScreen(log)
            self.chart_preview = type('C', (), {'reset_to_start': lambda s: None})()

        def set_volume(self, v):
            pass

        def cancel_predecode(self):
            pass

    class FakeCore:
        instances = []

        def __init__(self, cfg):
            self.log = []
            self.dock = FakeDock(self.log)
            FakeCore.instances.append(self)

        def play_demo(self, path, demo):
            pass

        def stop_audio(self):
            self.log.append('stop')

        def shutdown(self):
            pass

        def predecode(self, path):
            pass

        def chart_ready(self):
            return True

        def pause_chart(self):
            pass

        def chart_finished(self):
            return False

        def play_chart(self, at=0.0):
            self.log.append(('play_chart', at, self.dock.game_screen.dim))
    return FakeCore


class HostFadeTests(unittest.TestCase):
    @real_qt_test
    def test_music_starts_after_fade_in_and_leaving_darkens_screen(self):
        from neotja.player.host import PlayerHost
        FakeCore = _fake_core_class()
        orig = PlayerHost.core_factory
        PlayerHost.core_factory = FakeCore
        try:
            with tempfile.TemporaryDirectory() as d:
                a = _chart(Path(d) / 'a', 'a')
                host = PlayerHost({'master_volume': 0.0}, lambda: None)
                host.resize(1600, 900)
                host.show()
                host.on_shown()
                host._ensure_core()
                core = FakeCore.instances[-1]
                host._enter_play(a, 'Oni')
                _wait(80)
                self.assertNotIn('play_chart', [x[0] if isinstance(x, tuple) else x
                                                for x in core.log])
                _wait(700)
                self.assertIs(host.body.currentWidget(), host.play_page)
                plays = [x for x in core.log if isinstance(x, tuple) and x[0] == 'play_chart']
                self.assertEqual(len(plays), 1)
                self.assertEqual(plays[0][2], 0.0)          # 明け切ってから
                i = core.log.index(plays[0])
                dims = [x[1] for x in core.log[:i] if isinstance(x, tuple) and x[0] == 'dim']
                self.assertEqual(dims[0], 1.0)
                self.assertEqual(dims[-1], 0.0)
                # 選曲へ: 画面自身が暗くなってから切り替わり、離れたら 0。
                core.log.clear()
                host._on_menu('library')
                _wait(700)
                self.assertIs(host.body.currentWidget(), host.library)
                dims = [x[1] for x in core.log if isinstance(x, tuple) and x[0] == 'dim']
                self.assertIn(1.0, dims)
                self.assertEqual(dims[-1], 0.0)
                self.assertEqual(core.dock.game_screen.dim, 0.0)
                host.shutdown()
                host.deleteLater()
        finally:
            PlayerHost.core_factory = orig


if __name__ == '__main__':
    unittest.main()
