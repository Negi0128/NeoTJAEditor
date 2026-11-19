"""どんちゃんの連番の先読み(chara._PreloadWorker)の寿命。

走っている QThread が誰からも指されなくなる(スキンを読み直して CharaAnimator を
作り直した・窓をすぐ閉じた)と、GC やプロセスの終わりで
"QThread: Destroyed while thread is still running" になり、Windows では 0xC0000409
(bash では code 127)でプロセスごと落ちていた。test_player_host の
test_enter_and_leave_player が単独でも毎回落ちていた原因。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_chara_preload_lifetime.py`
"""
import gc
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-chara-test-')
_CFG = Path(_TMP) / 'settings.json'
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

APP_DIR = Path(__file__).resolve().parent.parent


class PreloadLifetimeTests(unittest.TestCase):
    def test_orphaned_worker_is_kept_until_done_and_joined_at_exit(self):
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication
        from neotja import chara
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d:
            normal = Path(d) / chara.STATE_NORMAL
            normal.mkdir()
            img = QImage(4, 4, QImage.Format_ARGB32)
            img.fill(0)
            for i in range(40):
                img.save(str(normal / ('%d.png' % i)))
            orig = chara.QImage

            def slow(path):              # 復号を遅くして、走っている最中を作る
                time.sleep(0.02)
                return orig(path)
            chara.QImage = slow
            try:
                s = chara.CharaSprites(base_dir=d)
                s.preload()
                w = s._preloader
                self.assertIn(w, chara._RUNNING)
                # 持ち主を捨てる(スキンを読み直したときと同じ)。
                del s
                gc.collect()
                self.assertTrue(w.isRunning())
                self.assertIn(w, chara._RUNNING)
                # プロセスの終わり(atexit)で止めて合流する。
                chara._stop_all()
                self.assertFalse(w.isRunning())
                self.assertEqual(chara._RUNNING, set())
                # 走り終えたものは自分で外れる。
                s2 = chara.CharaSprites(base_dir=d)
                s2.preload()
                w2 = s2._preloader
                end = time.monotonic() + 10
                while w2 in chara._RUNNING and time.monotonic() < end:
                    app.processEvents()
                    time.sleep(0.01)
                self.assertNotIn(w2, chara._RUNNING)
                self.assertFalse(w2.isRunning())
            finally:
                chara.QImage = orig

    def test_editor_closed_right_after_start_exits_cleanly(self):
        """窓を出してすぐ閉じても、プロセスが正常に終わる(以前は毎回 0xC0000409)。"""
        code = textwrap.dedent('''
            import os, sys
            sys.path.insert(0, os.path.join(os.getcwd(), 'tests')); sys.path.insert(0, os.getcwd())
            from gui_support import editor_session, real_qt_test
            @real_qt_test
            def run():
                from PySide6.QtTest import QTest
                with editor_session() as (win, root):
                    win.show()
                    QTest.qWait(0)
            run()
            print('closed', flush=True)
        ''')
        env = dict(os.environ, PYTHONPATH=str(APP_DIR))
        for _ in range(2):
            r = subprocess.run([sys.executable, '-c', code], cwd=str(APP_DIR), env=env,
                               capture_output=True, text=True, timeout=180)
            self.assertIn('closed', r.stdout, r.stderr[-2000:])
            self.assertEqual(r.returncode, 0, 'exit 0x%08X' % (r.returncode & 0xFFFFFFFF))


if __name__ == '__main__':
    unittest.main(verbosity=2)
