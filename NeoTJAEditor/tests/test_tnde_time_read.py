"""再現FPS: TNDE と同じく、描く時刻を整数ミリ秒で読む(利用者の指定 2026-11-15)。

TNDE は毎フレーム曲の再生位置を整数ミリ秒で読む。120fps では読める時刻が
8ms か 9ms ずつしか進まず、1小節 8.333ms のストロボの線が前後にぶれる。
Neo のシミュも同じ読み方をして、ストロボの見え方を TNDE とそろえる。
"""
import math
import unittest
from types import SimpleNamespace

from neotja.chart_preview_widget import (ChartPreviewWidget, normalize_sim_fps,
                                         tnde_ms_floor)


def view_time(now, offset, tnde=True, fps=120):
    """ChartPreviewWidget._view_time を、ウィジェットを作らずに呼ぶ。
    格子(_strobe_time)はストロボ区間の中とみなして 1/fps へ落とす。"""
    fake = SimpleNamespace(
        _strobe_time=lambda t: math.floor(t * fps) / fps,
        _tnde_ms=tnde, _offset=offset)
    return ChartPreviewWidget._view_time(fake, now)


class SimFpsChoiceTests(unittest.TestCase):
    def test_only_60_and_120(self):
        self.assertEqual(normalize_sim_fps(60), 60)
        self.assertEqual(normalize_sim_fps(120), 120)
        # 昔の MAX(0)・一覧に無い値・壊れた値は 120
        for v in (0, 144, 240, None, "x", -1):
            self.assertEqual(normalize_sim_fps(v), 120, v)


class TndeMsFloorTests(unittest.TestCase):
    def test_floor_to_whole_ms(self):
        self.assertAlmostEqual(tnde_ms_floor(0.0089999), 0.008)
        self.assertAlmostEqual(tnde_ms_floor(1.2345678), 1.234)
        # 浮動小数の誤差で 9ms が 8ms へ落ちない
        self.assertAlmostEqual(tnde_ms_floor(0.009), 0.009)
        self.assertAlmostEqual(tnde_ms_floor(3 * 0.003), 0.009)

    def test_120fps_frames_advance_8_or_9_ms(self):
        reads = [tnde_ms_floor(k / 120.0) for k in range(121)]
        steps = {round((b - a) * 1000) for a, b in zip(reads, reads[1:])}
        self.assertEqual(steps, {8, 9})


class ViewTimeTests(unittest.TestCase):
    def test_floor_is_on_song_clock_not_chart_clock(self):
        # 譜面時刻 = 曲の時刻 + OFFSET。切り捨てるのは曲の時刻のほう。
        off = -0.0123
        audio = 2.0 + 5 / 120.0          # 1コマの時刻(曲の時計)
        got = view_time(audio + off, off)
        self.assertAlmostEqual(got - off, tnde_ms_floor(
            math.floor((audio + off) * 120) / 120 - off), places=9)
        self.assertAlmostEqual(((got - off) * 1000) % 1.0 + 0.0, 0.0, places=6)

    def test_8_333ms_strobe_jitters_and_1ms_strobe_is_still(self):
        # 判定枠に一番近い小節線の「1小節ぶんの中での位置」をコマごとに追う。
        def phases(measure_ms, tnde):
            out = []
            for k in range(240):
                t = view_time(10.0 + k / 120.0 + 0.0037, 0.0, tnde) * 1000.0
                out.append(round(-t % measure_ms, 6))
            return out
        def moves(measure_ms, tnde):
            ph = phases(measure_ms, tnde)
            m = measure_ms
            return [((b - a + m / 2) % m) - m / 2 for a, b in zip(ph, ph[1:])]
        # 8.333ms: 格子だけなら止まる。ミリ秒で読むと前後にぶれる(TNDE と同じ)。
        grid = moves(1000 / 120.0, False)
        self.assertLess(max(abs(v) for v in grid), 1e-6)
        tnde = moves(1000 / 120.0, True)
        self.assertGreater(max(tnde), 0.2)
        self.assertLess(min(tnde), -0.2)
        # 約1ms(BPM170 #MEASURE 1/1412): ミリ秒で読めば1コマごとの動きが
        # ほぼ 0(SCROLL10 で 0.01px 程度)。
        self.assertLess(max(abs(v) for v in moves(240000 / 170 / 1412, True)), 0.005)

    def test_off_returns_grid_time(self):
        self.assertAlmostEqual(view_time(1.23456, 0.0, tnde=False),
                               math.floor(1.23456 * 120) / 120)


if __name__ == "__main__":
    unittest.main()
