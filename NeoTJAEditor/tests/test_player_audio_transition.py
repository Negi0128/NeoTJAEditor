"""難易度選択 → 曲名表示 → 再生 の移り目で音が途切れないための手当て。

- Player のあいだ GIL の持ち替えを 1ms に(出たら戻す)
- 試聴が同じ音源を読んでいる最中は、本編の先読みを2本目として始めない
- どんちゃんのコマを一度に QPixmap にしない(少しずつ)
- クロスフェードは重ねた絵だけで塗りきる(下の画面を毎コマ描き直さない)

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_audio_transition.py`
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

_TMP = tempfile.mkdtemp(prefix='neotja-audio-transition-test-')
_CFG = Path(_TMP) / 'settings.json'
_CFG.write_text(json.dumps({'master_volume': 0.0}), encoding='utf-8')
settings.settings_path = lambda: _CFG
settings._primary_settings_path = lambda: _CFG
settings._fallback_settings_path = lambda: _CFG

from gui_support import real_qt_test  # noqa: E402


def _wait(ms):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


class SwitchIntervalTests(unittest.TestCase):
    def test_on_and_off_restore(self):
        from neotja.player import core
        before = sys.getswitchinterval()
        try:
            core.audio_friendly_gil(True)
            self.assertAlmostEqual(sys.getswitchinterval(), core.AUDIO_SWITCH_INTERVAL, places=6)
            core.audio_friendly_gil(True)      # 2回目でも元の値を覚え直さない
            core.audio_friendly_gil(False)
            self.assertAlmostEqual(sys.getswitchinterval(), before, places=6)
        finally:
            sys.setswitchinterval(before)


class PredecodeTests(unittest.TestCase):
    def test_waits_for_running_audition_decode(self):
        from neotja.player import core as core_mod

        class Dock:
            def __init__(self):
                self._decoding_path = None
                self._pre = None
                self.calls = []

            def predecode_wave(self, w):
                self.calls.append(('pre', w))

            def cancel_predecode(self):
                self.calls.append(('cancel',))

        with tempfile.TemporaryDirectory() as d:
            tja = Path(d) / 'a.tja'
            tja.write_text('TITLE:a\nWAVE:a.ogg\n', encoding='cp932')
            wave = os.path.join(d, 'a.ogg')
            c = core_mod.PlayerCore.__new__(core_mod.PlayerCore)
            c.dock = Dock()
            c._pending = None
            c._segment_live = None
            # 試聴が同じ音源を読んでいる最中: 2本目を始めない。
            c.dock._decoding_path = wave
            c.predecode(str(tja))
            self.assertEqual(c.dock.calls, [])
            # 読み終わったら(持っているので読み直さない)先読みへ回す。
            c._on_song_ready(wave)
            self.assertEqual(c.dock.calls, [('pre', wave)])
            # 読んでいなければすぐ。
            c.dock.calls.clear()
            c.dock._decoding_path = None
            c.predecode(str(tja))
            self.assertEqual(c.dock.calls, [('pre', wave)])
            # やめたら、あとで読み終わっても先読みしない。
            c.dock.calls.clear()
            c.dock._decoding_path = wave
            c.predecode(str(tja))
            c.cancel_predecode()
            c._on_song_ready(wave)
            self.assertEqual(c.dock.calls, [('cancel',)])


class CharaAbsorbTests(unittest.TestCase):
    @real_qt_test
    def test_absorbs_in_slices_and_frame_takes_pending(self):
        from PySide6.QtGui import QColor, QImage
        from neotja import chara
        with tempfile.TemporaryDirectory() as d:
            n = 40
            for state in chara.STATES[:1]:
                sd = Path(d) / state
                sd.mkdir()
                for i in range(n):
                    img = QImage(64, 64, QImage.Format_ARGB32)
                    img.fill(QColor(i, 0, 0, 255))
                    img.save(str(sd / ('%d.png' % i)))
            sp = chara.CharaSprites(base_dir=d)
            sp.ABSORB_SLICE_SEC = 0.0      # 1回に1枚ずつ
            done = []
            sp.preload(on_done=lambda: done.append(1))
            for _ in range(200):
                if sp._pending_order:
                    break
                _wait(10)
            state = chara.STATES[0]
            self.assertTrue(sp._pending_order)          # 一度に全部は直さない
            self.assertFalse(sp._preloaded)
            pm = sp.frame(state, n - 1)                 # 直す前に要ったコマはその場で
            self.assertIsNotNone(pm)
            for _ in range(300):
                if done:
                    break
                _wait(5)
            self.assertEqual(done, [1])
            self.assertTrue(sp._preloaded)
            self.assertEqual(sum(1 for i in range(n) if sp._cache.get((state, i)) is not None), n)


class CrossFadeTests(unittest.TestCase):
    @real_qt_test
    def test_cross_overlay_is_opaque_and_grabs_target_once(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QWidget
        from neotja.player.transitions import FadeStack

        class Page(QWidget):
            def __init__(self):
                super().__init__()
                self.paints = 0

            def paintEvent(self, ev):
                self.paints += 1

        st = FadeStack()
        a, b = Page(), Page()
        st.addWidget(a)
        st.addWidget(b)
        st.resize(400, 300)
        st.show()
        _wait(50)
        grabs = []
        orig = b.grab
        b.grab = lambda *x: (grabs.append(1), orig(*x))[1]
        done = []
        st.fade_to(b, mode='cross', ms=300, done=lambda: done.append(1))
        ov = st._overlay
        self.assertTrue(ov.testAttribute(Qt.WA_OpaquePaintEvent))
        _wait(150)
        b.paints = 0
        _wait(100)
        self.assertEqual(len(grabs), 1)
        self.assertEqual(b.paints, 0)            # 下の画面は描き直さない
        _wait(250)
        self.assertEqual(done, [1])
        self.assertFalse(ov.testAttribute(Qt.WA_OpaquePaintEvent))
        self.assertIsNone(ov.under)
        st.close()
        st.deleteLater()


if __name__ == '__main__':
    unittest.main()
