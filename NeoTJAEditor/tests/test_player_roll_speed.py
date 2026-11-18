"""連打秒速: Player の難易度設定から Editor と同じ roll_speed を変える。
次に譜面を読むときに効き、Editor へ戻ると Editor の欄も同じ値になる。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_roll_speed.py`
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

_TMP = tempfile.mkdtemp(prefix='neotja-roll-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import editor_session, real_qt_test  # noqa: E402


def _wait(ms):
    from PySide6.QtTest import QTest
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QTest.qWait(10)
        time.sleep(0.002)


class _SilentCore:
    def __init__(self, cfg):
        self.dock = type('D', (), {'set_master_volume': lambda s, v: None,
                                   'cancel_predecode': lambda s: None})()

    def __getattr__(self, name):
        return lambda *a, **k: None


def _tja(folder, name='a'):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / (name + '.tja')
    p.write_text('TITLE:%s\nBPM:120\nCOURSE:Oni\nLEVEL:8\n#START\n5000,\n0008,\n#END\n' % name,
                 encoding='cp932')
    return str(p)


class StepperTests(unittest.TestCase):
    @real_qt_test
    def test_stepper_row_edits_shared_roll_speed(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from neotja.player.difficulty import DifficultyPage, Stepper
        cfg = {'roll_speed': 45}
        saves = []
        page = DifficultyPage(cfg, lambda: saves.append(1))
        page.resize(1600, 900)
        page.show()
        page.open_settings()
        _wait(300)
        st = page.settings.steppers['roll_speed']
        self.assertIsInstance(st, Stepper)
        self.assertEqual(st.value(), 45)
        r = st.inner()
        QTest.mouseClick(st, Qt.LeftButton, pos=QPoint(int(r.right() - 8), int(r.center().y())))
        self.assertEqual(cfg['roll_speed'], 46)
        QTest.mouseClick(st, Qt.LeftButton, pos=QPoint(int(r.left() + 8), int(r.center().y())))
        QTest.mouseClick(st, Qt.LeftButton, pos=QPoint(int(r.left() + 8), int(r.center().y())))
        self.assertEqual(cfg['roll_speed'], 44)
        QTest.keyClick(st, Qt.Key_Right)
        self.assertEqual(cfg['roll_speed'], 45)
        # 範囲は Editor の欄と同じ 1〜100。
        st.setValue(100)
        st.step(1)
        self.assertEqual(st.value(), 100)
        st.setValue(1)
        st.step(-1)
        self.assertEqual(st.value(), 1)
        # 保存は連続で変えてもまとめて1回。
        self.assertEqual(saves, [])
        _wait(600)
        self.assertEqual(len(saves), 1)
        # 開き直すと設定の値を出す。
        cfg['roll_speed'] = 30
        page.settings.set_values(cfg)
        self.assertEqual(st.value(), 30)
        self.assertEqual(st.sizeHint().height(), page.settings.switches['player_show_fps'].sizeHint().height())
        page.close()
        page.deleteLater()

    def test_roll_speed_is_a_player_key_and_feeds_the_analyzer(self):
        from neotja.player.core import PLAYER_KEYS
        from neotja.tja_analyzer import TJACourseAnalyzer
        self.assertIn('roll_speed', PLAYER_KEYS)
        cfg = {'roll_speed': 45}
        an = TJACourseAnalyzer(cfg)
        text = 'TITLE:a\nBPM:120\nCOURSE:Oni\nLEVEL:8\n#START\n5000,\n0008,\n#END\n'
        self.assertEqual(an.build_preview_timeline(text, None, 'Oni')['roll_hit_speed'], 45.0)
        cfg['roll_speed'] = 12                # 同じ辞書を書き換える(Player の設定)
        self.assertEqual(an.build_preview_timeline(text, None, 'Oni')['roll_hit_speed'], 12.0)


class EditorSyncTests(unittest.TestCase):
    @real_qt_test
    def test_editor_spin_follows_after_leaving_player(self):
        from neotja.player.host import PlayerHost
        orig = PlayerHost.core_factory
        PlayerHost.core_factory = _SilentCore
        try:
            with editor_session() as (win, root):
                win.show()
                start = win.roll_speed_spin.value()
                win.start_screen.btn_player.click()
                host = win.player_page
                host.library.choose(_tja(root / 'songs'))
                host.difficulty.open_settings()
                st = host.difficulty.settings.steppers['roll_speed']
                self.assertEqual(st.value(), start)
                st.step(-5)
                self.assertEqual(win.config_data['roll_speed'], start - 5)
                host.back_requested.emit()
                self.assertFalse(win.is_player_visible())
                self.assertEqual(win.roll_speed_spin.value(), start - 5)
        finally:
            PlayerHost.core_factory = orig


if __name__ == '__main__':
    unittest.main()
