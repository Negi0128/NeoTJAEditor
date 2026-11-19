"""Player で自分で叩く: オート演奏 OFF なら演奏モードで読み、D F J K を打面へ。
プレイリストはいつもオート。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_manual_play.py`
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-manual-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402


class ManualPlayTests(unittest.TestCase):
    @real_qt_test
    def test_mode_and_pads(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from neotja.constants import KIND_DON, KIND_KA, PLAY_MODE_PLAY, PLAY_MODE_WATCH
        from neotja.player.host import PlayerHost
        loads = []

        class Core:
            def __init__(self, cfg):
                self.dock = type('D', (), {'cancel_predecode': lambda s: None})()

            def load(self, path, course_key=None, play_mode=None):
                loads.append(play_mode)
                return True

            def __getattr__(self, name):
                return lambda *a, **k: None

        orig = PlayerHost.core_factory
        PlayerHost.core_factory = Core
        try:
            cfg = {'master_volume': 0.0}
            host = PlayerHost(cfg, lambda: None)
            host._ensure_core()
            self.assertFalse(host.manual_play())            # 既定はオート
            host._begin_load('x.tja', 'Oni')
            cfg['player_auto'] = False
            self.assertTrue(host.manual_play())
            host._begin_load('x.tja', 'Oni')
            host._pl_session = {'index': 0}                 # プレイリストはいつもオート
            self.assertFalse(host.manual_play())
            host._begin_load('x.tja', 'Oni')
            self.assertEqual(loads, [PLAY_MODE_WATCH, PLAY_MODE_PLAY, PLAY_MODE_WATCH])
            host._pl_session = None
            # 打面: D=左の縁 F=左の面 J=右の面 K=右の縁。
            got = []
            page = host.play_page
            page.pad_cb = lambda kind, side: got.append((kind, side))
            for k in (Qt.Key_D, Qt.Key_F, Qt.Key_J, Qt.Key_K, Qt.Key_A):
                QTest.keyClick(page, k)
            self.assertEqual(got, [(KIND_KA, 0), (KIND_DON, 0), (KIND_DON, 1), (KIND_KA, 1)])
            host.shutdown()
            host.deleteLater()
        finally:
            PlayerHost.core_factory = orig


class MissNoPopupTests(unittest.TestCase):
    def test_passed_note_counts_bad_but_no_judge_text(self):
        from neotja.player.play_state import PlayState
        st = PlayState({'notes': [(1.0, '1'), (2.0, '1')]})
        st.advance(1.5)
        self.assertEqual(st.bad, 1)
        self.assertIsNone(st.last_judge)                    # 見送りでは「不可」を出さない


if __name__ == '__main__':
    unittest.main()
