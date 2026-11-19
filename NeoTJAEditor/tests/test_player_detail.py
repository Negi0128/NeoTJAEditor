"""Player の詳細モード: 作譜ペインを読むだけで出す・通常プレイと行き来する。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_detail.py`
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-detail-test-')
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


def _key(widget, key, mods=None, text=''):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    mods = Qt.NoModifier if mods is None else mods
    for kind in (QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease):
        QApplication.sendEvent(widget, QKeyEvent(kind, key, mods, text))


def _mouse(widget, kind, x, y, button):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication
    p = QPointF(x, y)
    buttons = button if kind != 'release' else Qt.NoButton
    ev = {'press': 'MouseButtonPress', 'release': 'MouseButtonRelease',
          'move': 'MouseMove', 'double': 'MouseButtonDblClick'}[kind]
    from PySide6.QtCore import QEvent
    QApplication.sendEvent(widget, QMouseEvent(getattr(QEvent, ev), p, widget.mapToGlobal(p),
                                               button if kind != 'move' else Qt.NoButton,
                                               buttons, Qt.NoModifier))


BODY = '\n'.join(['#BPMCHANGE 120', '1020,', '2010,', '#GOGOSTART', '1111,', '#GOGOEND',
                  '1000,', '5000,', '0008,', '1010,', '1000,'])


# ----------------------------------------------------------------------
# 本物の PlayerCore: 作譜ペインは読むだけ。どの道でも譜面は変わらない
# ----------------------------------------------------------------------
class ReadOnlyPaneTests(unittest.TestCase):
    @real_qt_test
    def test_every_edit_path_is_closed_and_tja_unchanged(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMenu
        from neotja.player.core import PlayerCore
        with editor_session() as (win, root):
            open_test_chart(win, root, BODY, bpm=120)
            tja = root / 'test.tja'
            raw0 = tja.read_bytes()
            ed_text0 = win.editor.toPlainText()
            ed_mod0 = win.isWindowModified()
            cfg = dict(win.config_data)
            cfg.update(master_volume=0.0, gpu_render=False)
            core = PlayerCore(cfg)
            menus = []
            orig_exec = QMenu.exec
            QMenu.exec = lambda self, *a, **k: menus.append(self) or None
            try:
                d = core.dock
                # Editor へ書き込む受け口は最初から1つも渡していない。
                for name in ('note_edit_cb', 'chart_op_cb', 'header_edit_cb', 'demostart_cb',
                             'undo_cb', 'redo_cb'):
                    self.assertIsNone(getattr(d, name), name)
                self.assertTrue(d.is_read_only())
                self.assertTrue(core.load(str(tja), course_key='Oni'))
                for _ in range(300):
                    if core.chart_ready():
                        break
                    _wait(20)
                self.assertTrue(core.use_detail_mode())
                gpw = d.game_preview_window
                gpw.set_view_only(True, with_bottom=True)
                d.set_speed_row_visible(True)
                gpw.show()
                _wait(300)
                ce = d.chart_edit
                self.assertTrue(ce.isVisible())
                self.assertTrue(ce.read_only())
                # 書き換えにつながる道を見張る。
                hits = []
                ce.noteEdited.connect(lambda *a: hits.append(('noteEdited', a)))
                ce.measuresDeleteRequested.connect(lambda *a: hits.append(('measures', a)))
                ce.offsetCommitted.connect(lambda *a: hits.append(('offset', a)))
                ce.offsetPreview.connect(lambda *a: hits.append(('offsetPreview', a)))
                ce.demoStartRequested.connect(lambda: hits.append(('demo',)))
                real_op = ce._op_cb
                ce._op_cb = lambda op: (hits.append(('op', op.get('kind')))
                                        if not str(op.get('kind', '')).startswith('peek')
                                        else None) or real_op(op)
                d.apply_offset_cb = lambda text: hits.append(('apply_offset', text))
                # キー: 音符・連打・変換・ゴーゴー・小節線・歌詞・削除・範囲・
                # コピー/切り取り/貼り付け・取り消し/やり直し。
                plain = [getattr(Qt, 'Key_%d' % i) for i in range(10)] + [
                    Qt.Key_F, Qt.Key_J, Qt.Key_D, Qt.Key_K, Qt.Key_W, Qt.Key_Q,
                    Qt.Key_G, Qt.Key_S, Qt.Key_L, Qt.Key_Delete, Qt.Key_Backspace,
                    Qt.Key_Tab, Qt.Key_B, Qt.Key_R, Qt.Key_Y, Qt.Key_U, Qt.Key_I, Qt.Key_O]
                for k in plain:
                    _key(ce, k)
                    _key(ce, k, Qt.AltModifier)
                    _key(ce, k, Qt.ShiftModifier)
                for k in (Qt.Key_C, Qt.Key_X, Qt.Key_V, Qt.Key_Z, Qt.Key_Y, Qt.Key_D,
                          Qt.Key_T, Qt.Key_M):
                    _key(ce, k, Qt.ControlModifier)
                _key(ce, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
                _key(ce, Qt.Key_Right, Qt.ShiftModifier)        # 引きずって置く
                # マウス: 右ドラッグの囲み・右クリックのメニュー・音符をつかんで
                # 動かす・上の目盛りで小節を選ぶ・ダブルクリック。
                top, hh = ce._row_rects()['note']
                y_note = int(top + hh / 2)
                xs = [ce._sec_to_x(t) for t, *_r in (ce._notes_raw or [])[:3]] or [400]
                for x in xs:
                    _mouse(ce, 'press', x, y_note, Qt.RightButton)
                    _mouse(ce, 'move', x + 120, y_note + 30, Qt.RightButton)
                    _mouse(ce, 'release', x + 120, y_note + 30, Qt.RightButton)
                    _mouse(ce, 'press', x, y_note, Qt.RightButton)
                    _mouse(ce, 'release', x, y_note, Qt.RightButton)
                    _mouse(ce, 'press', x, y_note, Qt.LeftButton)
                    _mouse(ce, 'move', x + 80, y_note, Qt.LeftButton)
                    _mouse(ce, 'release', x + 80, y_note, Qt.LeftButton)
                    _mouse(ce, 'double', x, y_note, Qt.LeftButton)
                    _mouse(ce, 'press', x, 5, Qt.LeftButton)
                    _mouse(ce, 'release', x, 5, Qt.LeftButton)
                # 直に呼んでも効かない(二重の守り)。
                self.assertFalse(ce.paste_at_cursor())
                self.assertFalse(ce.copy_selection(cut=True))
                self.assertIsNone(ce._run_op({'kind': 'key', 'a': (0, 0), 'char': '1'}))
                self.assertIsNone(ce.open_command_input('BPMCHANGE'))
                self.assertIsNone(ce.open_lyric_popup())
                ce.toggle_gogo_at_cursor()
                ce.toggle_barline_at_cursor()
                ce.apply_transform('flip')
                ce._place('1')
                ce._sel_measures = {0}
                self.assertFalse(ce._delete_selected_measures())
                ce._sel_measures = set()
                ce.set_offset_mode(True)
                self.assertFalse(ce.offset_mode)
                ce.btn_offset.click()
                self.assertFalse(ce.offset_mode)
                ce.begin_offset_drag(100.0)
                ce.nudge_offset(0.01)
                ce.end_offset_drag()
                d._on_note_edited(0, 0, 4, '1')
                self.assertIsNone(d._on_chart_op({'kind': 'delete', 'a': (0, 0), 'b': None}))
                d._on_waveform_offset_committed(1.0)
                d._on_apply_offset()
                d.demostart_here()
                d._on_header_edit()
                d._do_undo(False)
                d._do_undo(True)
                _wait(200)
                self.assertEqual(hits, [])
                self.assertEqual(menus, [])
                self.assertEqual(ce._sel, set() if isinstance(ce._sel, set) else ce._sel)
                self.assertFalse(ce._sel)
                self.assertIsNone(ce._note_drag)
                self.assertEqual(ce._pending, {})                # 暫定表示も出さない
                self.assertFalse(ce.btn_offset.isVisible())
                self.assertFalse(ce.btn_demo.isVisible())
                self.assertFalse(d.btn_header.isVisible())
                self.assertFalse(d.btn_keys.isVisible())
                if d.command_panel is not None:
                    self.assertFalse(d.command_panel.isVisible())
                self.assertEqual(tja.read_bytes(), raw0)
                self.assertEqual(win.editor.toPlainText(), ed_text0)
                self.assertEqual(win.isWindowModified(), ed_mod0)
                # 見る・動かすは効く: ← → で位置、Space で再生。
                seeks = []
                ce.cursorMoved.connect(lambda t: seeks.append(t))
                _key(ce, Qt.Key_Right)
                _key(ce, Qt.Key_Right)
                self.assertEqual(len(seeks), 2)
                cx = ce._sec_to_x(ce.cursor_time() + 1.5)      # 今の位置から離れた所
                _mouse(ce, 'press', cx, y_note, Qt.LeftButton)
                _mouse(ce, 'release', cx, y_note, Qt.LeftButton)
                self.assertEqual(len(seeks), 3)
                toggled = []
                ce._toggle_play_cb = lambda: toggled.append(1)
                _key(ce, Qt.Key_Space)
                self.assertEqual(toggled, [1])
                self.assertEqual(hits, [])
            finally:
                QMenu.exec = orig_exec
                core.stop_audio()
                core.shutdown()
                core.dock.chart_preview._timer.stop()
                core.dock.game_preview_window.hide()
                core.dock.game_preview_window.close()
                core.dock.deleteLater()

    @real_qt_test
    def test_editor_pane_still_edits(self):
        from PySide6.QtCore import Qt
        with editor_session() as (win, root):
            open_test_chart(win, root, BODY, bpm=120)
            d = win.preview_dock
            self.assertFalse(d.is_read_only())
            self.assertFalse(d.chart_edit.read_only())
            self.assertFalse(d._pane_follow_scale)
            self.assertEqual(d.chart_edit.height(), d.PANE_BASE_H)
            d.show_chart_edit()
            ops = []
            real = d.chart_op_cb
            d.chart_op_cb = lambda op: (ops.append(op.get('kind')), real(op))[1]
            _key(d.chart_edit, Qt.Key_1)
            self.assertIn('place', ops)
            # Editor のレーンの Esc は何もしない(回し先が無い)。
            self.assertIsNone(d.chart_preview._locked_key_cb)


# ----------------------------------------------------------------------
# PlayerHost の行き来(偽の PlayerCore)
# ----------------------------------------------------------------------
def _chart(folder, name):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'song.ogg').write_bytes(b'x')
    p = folder / (name + '.tja')
    p.write_text('TITLE:%s\nBPM:120\nWAVE:song.ogg\nCOURSE:Oni\nLEVEL:9\n#START\n1111,\n#END\n'
                 % name, encoding='cp932')
    return str(p)


def _fake_core_class():
    from PySide6.QtWidgets import QWidget

    class FakeGpw(QWidget):
        def __init__(self):
            super().__init__()
            self.locks = []
            self.views = []
            self.key_cb = None
            self.resize(640, 360)

        def set_view_only(self, on, with_bottom=False):
            self.views.append((bool(on), bool(with_bottom)))

        def set_controls_locked(self, on, key_cb=None):
            self.locks.append(bool(on))
            self.key_cb = key_cb

        def fit_view_only(self, w, h):
            self.resize(min(w, int(h * 16 / 9)), min(h, int(w * 9 / 16)))

    class FakeDock:
        def __init__(self):
            self.game_preview_window = FakeGpw()
            self.game_screen = QWidget()
            self.chart_edit = QWidget()
            self.speed_row = []
            self.chart_preview = type('C', (), {'reset_to_start': lambda s: None})()

        def set_volume(self, v):
            pass

        def set_master_volume(self, v):
            pass

        def cancel_predecode(self):
            pass

        def set_speed_row_visible(self, on):
            self.speed_row.append(bool(on))

    class FakeCore:
        instances = []

        def __init__(self, cfg):
            self.dock = FakeDock()
            self.log = []
            self.finished = False
            FakeCore.instances.append(self)

        def play_demo(self, path, demo):
            self.log.append('demo')

        def stop_audio(self):
            self.log.append('stop')

        def shutdown(self):
            pass

        def predecode(self, path):
            pass

        def load(self, path, course_key=None, play_mode=None):
            self.log.append(('load', course_key))
            return True

        def use_normal_play_mode(self):
            self.log.append('normal_mode')

        def use_detail_mode(self):
            self.log.append('detail_mode')
            return True

        def drop_hold(self):
            pass

        def chart_ready(self):
            return True

        def play_chart(self, at=0.0):
            self.log.append(('play_chart', at))

        def pause_chart(self):
            self.log.append('pause_chart')

        def resume_chart(self):
            self.log.append('resume_chart')

        def chart_finished(self):
            return self.finished
    return FakeCore


class HostDetailTests(unittest.TestCase):
    def setUp(self):
        from neotja.player.host import PlayerHost
        self.FakeCore = _fake_core_class()
        self._orig = PlayerHost.core_factory
        PlayerHost.core_factory = self.FakeCore

    def tearDown(self):
        from neotja.player.host import PlayerHost
        PlayerHost.core_factory = self._orig

    def _playing_host(self, d):
        from neotja.player.host import PlayerHost
        a = _chart(Path(d) / 'a', 'a')
        host = PlayerHost({'master_volume': 0.0, 'preview_volume': 0.5}, lambda: None)
        host.resize(1600, 900)
        host.show()
        host.on_shown()
        host.library.choose(a)
        host.sounds.play = lambda kind: None
        host.sounds.pi = lambda: None
        core = self.FakeCore.instances[-1]
        host._enter_play(a, 'Oni')
        _wait(700)
        core.log.clear()
        return host, a, core

    @real_qt_test
    def test_switch_to_detail_and_back(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QWidget
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._playing_host(d)
            gpw = core.dock.game_preview_window
            menu = host.play_page.menu
            QTest.keyClick(host.play_page, Qt.Key_Escape)
            self.assertTrue(menu.item('detail').isEnabled())
            self.assertEqual(menu.item('detail').text(), '詳細モードに移行する')
            QTest.keyClick(menu, Qt.Key_3)
            self.assertFalse(menu.is_open())
            self.assertEqual(host.play_mode, 'detail')
            self.assertIn('detail_mode', core.log)
            self.assertNotIn('resume_chart', core.log)          # 切り替えたら止まったまま
            self.assertEqual(gpw.locks[-1], False)               # 止める・シークを許す
            self.assertEqual(gpw.views[-1], (True, True))        # 下に作譜ペイン
            self.assertEqual(core.dock.speed_row[-1], True)
            self.assertIsNotNone(gpw.key_cb)                     # Esc はメニューへ
            # 詳細モードでも Esc でメニュー。3番目は「通常演奏に移行する」。
            gpw.key_cb(_esc_event())
            self.assertTrue(menu.is_open())
            self.assertEqual(menu.item('detail').text(), '通常演奏に移行する')
            # 詳細では終わりに来ても画面を離れない。
            core.finished = True
            QTest.keyClick(menu, Qt.Key_1)                       # 続ける
            self.assertEqual(core.log[-1], 'resume_chart')
            _wait(400)
            self.assertIs(host.body.currentWidget(), host.play_page)
            self.assertTrue(host.in_play())
            core.finished = False
            # 通常へ戻る: 位置はそのまま・そこから流し直す・また締める
            # (止めたままだと通常プレイでは続けられなかった 2026-11-20)。
            QTest.keyClick(host.play_page, Qt.Key_Escape)
            core.log.clear()
            QTest.keyClick(menu, Qt.Key_3)
            self.assertEqual(host.play_mode, 'normal')
            self.assertEqual(core.log, ['pause_chart', 'normal_mode', 'resume_chart'])
            self.assertEqual(gpw.locks[-1], True)
            self.assertEqual(gpw.views[-1], (True, False))
            self.assertEqual(core.dock.speed_row[-1], False)
            QTest.keyClick(host.play_page, Qt.Key_Escape)
            self.assertEqual(menu.item('detail').text(), '詳細モードに移行する')
            QTest.keyClick(menu, Qt.Key_Escape)                  # 続ける
            self.assertEqual(core.log[-1], 'resume_chart')
            # 通常に戻ったので、終わりで結果画面へ。
            core.finished = True
            _wait(900)
            self.assertIs(host.body.currentWidget(), host.results)
            del QWidget
            host.shutdown()
            host.deleteLater()

    @real_qt_test
    def test_restart_and_library_from_detail_return_to_normal(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with tempfile.TemporaryDirectory() as d:
            host, a, core = self._playing_host(d)
            gpw = core.dock.game_preview_window
            host.open_pause_menu()
            host._on_menu('detail')
            self.assertEqual(host.play_mode, 'detail')
            host.open_pause_menu()
            QTest.keyClick(host.play_page.menu, Qt.Key_2)        # やり直す
            self.assertEqual(host.play_mode, 'normal')
            self.assertIs(host.body.currentWidget(), host.intro)
            self.assertEqual(core.dock.speed_row[-1], False)
            seq = host._seq
            seq._t0 -= 6.0
            seq.tick()
            seq.tick()
            _wait(700)
            self.assertIs(host.body.currentWidget(), host.play_page)
            self.assertEqual(gpw.locks[-1], True)
            self.assertEqual(gpw.views[-1], (True, False))
            self.assertFalse(host.play_page.detail)
            host.open_pause_menu()
            host._on_menu('detail')
            host.open_pause_menu()
            host._on_menu('library')
            _wait(700)
            self.assertIs(host.body.currentWidget(), host.library)
            self.assertEqual(host.play_mode, 'normal')
            self.assertEqual(gpw.locks[-1], False)
            host.shutdown()
            host.deleteLater()


def _esc_event():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    return QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)


# ----------------------------------------------------------------------
# 共通部品: 締めていないときの Esc の回し先・窓のキー
# ----------------------------------------------------------------------
class SharedPartsTests(unittest.TestCase):
    @real_qt_test
    def test_unlocked_lane_routes_only_esc_and_view_only_disables_window_keys(self):
        from PySide6.QtCore import Qt
        with editor_session() as (win, root):
            open_test_chart(win, root, BODY, bpm=120)
            d = win.preview_dock
            lane, gpw = d.chart_preview, d.game_preview_window
            got = []
            calls = []
            lane._play_cb = lambda: calls.append('play')
            gpw.set_controls_locked(False, lambda ev: got.append(ev.key()))
            _key(lane, Qt.Key_Escape)
            _key(lane, Qt.Key_Space)
            self.assertEqual(got, [Qt.Key_Escape])
            self.assertIn('play', calls)                        # ほかは今までどおり
            gpw.set_controls_locked(False)
            self.assertIsNone(lane._locked_key_cb)


if __name__ == '__main__':
    unittest.main()
