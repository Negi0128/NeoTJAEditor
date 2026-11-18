"""難易度選択の試聴(本物の PlayerCore): 開き直すたびに DEMOSTART から・
試聴中は譜面(風船の破裂音)を動かさない。

実行: アプリのフォルダで `PYTHONPATH=. .venv/Scripts/python.exe tests/test_player_audition.py`
"""
import json
import os
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from neotja import settings  # noqa: E402

_TMP = tempfile.mkdtemp(prefix='neotja-audition-test-')
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


def _until(cond, ms=8000):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        if cond():
            return True
        _wait(20)
    return cond()


def _song(folder, name='a', demostart=10, seconds=20, body='1111,\n7000,\n0008,\n1111,'):
    folder.mkdir(parents=True, exist_ok=True)
    with wave.open(str(folder / (name + '.wav')), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b'\x00\x00' * 22050 * seconds)
    text = ('TITLE:%s\nBPM:120\nOFFSET:0\nWAVE:%s.wav\nDEMOSTART:%s\nBALLOON:3\n'
            'COURSE:Oni\nLEVEL:9\n#START\n%s\n#END\n' % (name, name, demostart, body))
    p = folder / (name + '.tja')
    p.write_text(text, encoding='cp932')
    return str(p), str(folder / (name + '.wav'))


def _host():
    from neotja.player.host import PlayerHost
    cfg = settings.default_settings()
    cfg.update(master_volume=0.0, gpu_render=False, player_show_bg=False)
    host = PlayerHost(cfg, lambda: None)
    host.resize(1600, 900)
    host.show()
    host.on_shown()
    return host


def _close(host):
    core = host._core
    host.shutdown()
    if core is not None:
        core.dock.chart_preview._timer.stop()
        core.dock.game_preview_window.close()
        core.dock.deleteLater()
    host.deleteLater()


class AuditionLaneTests(unittest.TestCase):
    @real_qt_test
    def test_audition_never_runs_the_lane_or_pops_balloons(self):
        with tempfile.TemporaryDirectory() as d:
            a, _wa = _song(Path(d) / 'a', 'a', demostart=0)
            host = _host()
            try:
                host.library.choose(a)
                core = host._core
                self.assertTrue(_until(core.chart_playing))
                dock = core.dock
                cp = dock.chart_preview
                pops = []
                cp._pop_sound = type('S', (), {'play': lambda _s: pops.append(1)})()
                cp._pop_times = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
                ce = dock.chart_edit
                ce_pos = getattr(ce, '_pos_sec', None)
                for ms in range(0, 4000, 40):
                    dock._on_position_changed(ms, manual=True)
                    cp._on_tick()
                self.assertFalse(cp._playing)
                self.assertEqual(pops, [])
                self.assertEqual(getattr(ce, '_pos_sec', None), ce_pos)
                # 本編(試聴を解いたあと)は今までどおり走る。
                dock.set_audition(False)
                dock._on_position_changed(1000, manual=True)
                self.assertTrue(cp._playing)
            finally:
                _close(host)


if __name__ == '__main__':
    unittest.main()
