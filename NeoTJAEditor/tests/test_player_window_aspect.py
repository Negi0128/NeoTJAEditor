"""再生画面のあいだ、窓の大きさを 16:9 に保つ(player/window_aspect.py)。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_window_aspect.py`
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-aspect-test-')
_CFG = Path(_TMP) / 'settings.json'
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402
from neotja.player import window_aspect as wa  # noqa: E402


def _inner(rect, ew, eh):
    l, t, r, b = rect
    return (r - l) - ew, (b - t) - eh


def _wait(ms):
    import time
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QTest.qWait(5)


class SizingRectTests(unittest.TestCase):
    EW, EH = 16, 39            # 枠とタイトルバー

    def test_side_edges_fix_height_and_keep_top(self):
        for edge in (wa.WMSZ_LEFT, wa.WMSZ_RIGHT):
            r = wa.sizing_rect((100, 50, 100 + 1296, 50 + 500), edge, self.EW, self.EH)
            iw, ih = _inner(r, self.EW, self.EH)
            self.assertEqual((iw, ih), (1280, 720))
            self.assertEqual(r[1], 50)                 # 上は動かさない
            self.assertEqual(r[0], 100)
            self.assertEqual(r[2], 1396)

    def test_top_bottom_edges_fix_width(self):
        r = wa.sizing_rect((100, 50, 900, 50 + 720 + 39), wa.WMSZ_BOTTOM, self.EW, self.EH)
        self.assertEqual(_inner(r, self.EW, self.EH), (1280, 720))
        self.assertEqual(r[0], 100)                    # 左は動かさない
        r = wa.sizing_rect((100, 50, 900, 50 + 720 + 39), wa.WMSZ_TOP, self.EW, self.EH)
        self.assertEqual(_inner(r, self.EW, self.EH), (1280, 720))
        self.assertEqual(r[3], 50 + 720 + 39)          # 下は動かさない

    def test_corners_anchor_opposite_corner_and_take_larger(self):
        rect = (200, 200, 200 + 1616, 200 + 300 + 39)   # 幅が勝つ
        for edge, fixed in ((wa.WMSZ_BOTTOMRIGHT, (0, 1)), (wa.WMSZ_TOPLEFT, (2, 3)),
                            (wa.WMSZ_TOPRIGHT, (0, 3)), (wa.WMSZ_BOTTOMLEFT, (2, 1))):
            r = wa.sizing_rect(rect, edge, self.EW, self.EH)
            self.assertEqual(_inner(r, self.EW, self.EH), (1600, 900), edge)
            for i in fixed:
                self.assertEqual(r[i], rect[i], edge)
        rect = (0, 0, 400 + 16, 900 + 39)               # 高さが勝つ
        r = wa.sizing_rect(rect, wa.WMSZ_BOTTOMRIGHT, self.EW, self.EH)
        self.assertEqual(_inner(r, self.EW, self.EH), (1600, 900))

    def test_minimum(self):
        r = wa.sizing_rect((0, 0, 20, 20), wa.WMSZ_RIGHT, self.EW, self.EH)
        iw, ih = _inner(r, self.EW, self.EH)
        self.assertGreaterEqual(iw, 160)
        self.assertEqual(ih, round(iw * 9 / 16))

    def test_snap_client_size(self):
        # 中身 1600x1000、ゲーム部分も同じ → 高さを 900 に。
        self.assertEqual(wa.snap_client_size(1600, 1000, 1600, 1000), (1600, 900))
        # ほかの部品のぶん(上に 40)はそのまま足す。
        self.assertEqual(wa.snap_client_size(1600, 1040, 1600, 1000), (1600, 940))
        # 画面に入らないときは高さを止めて幅を縮める。
        self.assertEqual(wa.snap_client_size(1920, 800, 1920, 800, max_client_h=800),
                         (1422, 800))


class WindowsFilterTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows only')
    @real_qt_test
    def test_wm_sizing_is_rewritten_only_while_active(self):
        import ctypes
        import ctypes.wintypes
        from PySide6.QtWidgets import QApplication, QWidget
        state = {'on': True}

        class Win(QWidget):
            def nativeEvent(self, et, message):
                if wa.handle_native(self, message, lambda: state['on'], lambda: self):
                    return True, 1
                return False, 0
        win = Win()
        win.resize(800, 500)
        win.show()
        QApplication.processEvents()
        try:
            hwnd = int(win.winId())
            cur = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(cur))
            ew = (cur.right - cur.left) - round(win.width() * win.devicePixelRatioF())
            eh = (cur.bottom - cur.top) - round(win.height() * win.devicePixelRatioF())
            r = ctypes.wintypes.RECT(cur.left, cur.top, cur.left + 1280 + ew, cur.top + 300)
            ctypes.windll.user32.SendMessageW(hwnd, wa.WM_SIZING, wa.WMSZ_RIGHT,
                                              ctypes.byref(r))
            self.assertEqual((r.right - r.left - ew, r.bottom - r.top - eh), (1280, 720))
            state['on'] = False
            r2 = ctypes.wintypes.RECT(cur.left, cur.top, cur.left + 1280 + ew, cur.top + 300)
            ctypes.windll.user32.SendMessageW(hwnd, wa.WM_SIZING, wa.WMSZ_RIGHT,
                                              ctypes.byref(r2))
            self.assertEqual(r2.bottom - r2.top, 300)    # 外れているときは触らない
        finally:
            win.close()
            win.deleteLater()


class MainWindowSnapTests(unittest.TestCase):
    @real_qt_test
    def test_snap_once_in_normal_window_but_not_maximized(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
        from neotja.main_window import MainWindow

        class Host(QWidget):
            def __init__(self):
                super().__init__()
                lay = QVBoxLayout(self)
                lay.setContentsMargins(0, 0, 0, 0)
                self.body = QWidget()
                lay.addWidget(self.body)
                self.lock = True

            def wants_aspect_lock(self):
                return self.lock

        class Win(QMainWindow):
            _player_aspect_active = MainWindow._player_aspect_active
            _snap_player_aspect = MainWindow._snap_player_aspect
            _player_aspect_widget = MainWindow._player_aspect_widget
            nativeEvent = MainWindow.nativeEvent
            changeEvent = MainWindow.changeEvent
            _player_aspect_on = True

            def is_player_visible(self):
                return True
        w = Win()
        w.player_page = Host()
        w.setCentralWidget(w.player_page)
        w.resize(1000, 700)
        w.show()
        QApplication.processEvents()
        w._snap_player_aspect()
        QApplication.processEvents()
        b = w.player_page.body
        self.assertEqual(b.height(), round(b.width() * 9 / 16))
        # 詳細モードなど(保たない)では動かさない。
        w.player_page.lock = False
        w.resize(1000, 700)
        QApplication.processEvents()
        w._snap_player_aspect()
        QApplication.processEvents()
        self.assertEqual(w.size().height(), 700)
        # 最大化中は何もしない。
        w.player_page.lock = True
        w.showMaximized()
        QApplication.processEvents()
        self.assertFalse(w._player_aspect_active())
        # 最大化から戻ったら、戻った大きさが 16:9 でなくても合わせる。
        w.showNormal()
        w.resize(1100, 500)
        w.setWindowState(Qt.WindowMaximized)
        _wait(100)
        w.setWindowState(Qt.WindowNoState)
        _wait(150)
        self.assertFalse(w.isMaximized())
        self.assertLessEqual(abs(b.height() - round(b.width() * 9 / 16)), 1)
        w.close()
        w.deleteLater()


class HostLockTests(unittest.TestCase):
    @real_qt_test
    def test_lock_in_every_player_screen_and_signal_on_enter(self):
        from PySide6.QtWidgets import QWidget
        from neotja.player.host import PlayerHost

        class Gpw(QWidget):
            def set_view_only(self, on, with_bottom=False):
                pass

            def fit_view_only(self, w, h):
                pass

        class Dock:
            def __init__(self):
                self.game_preview_window = Gpw()
                self.game_screen = None
                self.chart_preview = type('C', (), {'reset_to_start': lambda s: None})()

            def set_volume(self, v):
                pass

            def cancel_predecode(self):
                pass

        class Core:
            def __init__(self, cfg):
                self.dock = Dock()

            def stop_audio(self):
                pass

            def shutdown(self):
                pass

            def play_chart(self, at=0.0):
                pass

            def pause_chart(self):
                pass

            def drop_hold(self):
                pass

            def chart_finished(self):
                return False

        orig = PlayerHost.core_factory
        PlayerHost.core_factory = Core
        try:
            host = PlayerHost({'master_volume': 0.0}, lambda: None)
            host.resize(1200, 800)
            host.show()
            host.on_shown()
            # Player を出しているあいだはどの画面でも保つ(選曲から)。
            self.assertTrue(host.wants_aspect_lock())
            self.assertIs(host.aspect_widget(), host)
            hits = []
            host.play_entering.connect(lambda: hits.append(1))
            host._ensure_core()
            host._enter_play('x.tja', 'Oni')
            self.assertEqual(hits, [1])
            self.assertTrue(host.wants_aspect_lock())
            host.switch_to_detail()
            self.assertTrue(host.wants_aspect_lock())
            host.switch_to_normal()
            self.assertTrue(host.wants_aspect_lock())
            host._end_play()
            self.assertTrue(host.wants_aspect_lock())
            host.hide()
            self.assertFalse(host.wants_aspect_lock())
            host.shutdown()
            host.deleteLater()
        finally:
            PlayerHost.core_factory = orig


if __name__ == '__main__':
    unittest.main()
