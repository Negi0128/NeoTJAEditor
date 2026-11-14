"""ストロボ生成の3つの方式(描画優先・パフォーマンス・理論値)。"""
import math
import os
import re
import unittest
from fractions import Fraction

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from neotja.dialogs import strobe_dialog as S

BPMS = ("170", "260", "150", "173.5")
LENGTHS = ("1/16小節", "1小節", "2小節", "4小節")


def _measures(text):
    """ストロボ部分の (#MEASURE の値, 小節の本数) と、全体の長さ。"""
    cur = None
    total = Fraction(0)
    units = []
    for line in text.splitlines():
        if line == "// --- ストロボ終了 ---":
            break
        m = re.match(r"#MEASURE (\d+)/(\d+)$", line)
        if m:
            cur = Fraction(int(m[1]), int(m[2]))
            units.append(cur)
        if line == "0,":
            total += cur
    return units, total


class StrobeModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = QWidget()
        self.d = S.StrobeGeneratorDialog(self.host, "170", lambda t: None)

    def tearDown(self):
        self.d.deleteLater()
        self.host.deleteLater()

    def _gen(self, mode, bpm, length, fps="120"):
        self.d.cb_mode.setCurrentText(mode)
        self.d.cb_fps.setCurrentText(fps)
        self.d.ed_bpm.setText(bpm)
        self.d.cb_length.setCurrentText(length)
        return self.d.txt_after.toPlainText()

    def test_default_mode_is_draw(self):
        self.assertEqual(self.d.cb_mode.currentText(), S.MODE_DRAW)
        self.assertEqual(self.d.lbl_mode.text(), S.MODE_NOTES[S.MODE_DRAW])

    def test_known_divisors(self):
        self.assertEqual(S.draw_divisor(Fraction(170)), 1412)
        self.assertEqual(S.draw_divisor(Fraction(260)), 923)
        self.assertEqual(S.perf_divisor(Fraction(170)), 1103)
        self.assertEqual(S.perf_divisor(Fraction(260)), 722)

    def test_draw_mode(self):
        for bpm in BPMS:
            want = round(240000 / Fraction(bpm))
            for length in LENGTHS:
                with self.subTest(bpm=bpm, length=length):
                    units, _ = _measures(self._gen(S.MODE_DRAW, bpm, length))
                    self.assertEqual(units[0], Fraction(1, want))

    def test_perf_mode(self):
        for bpm in BPMS:
            b = Fraction(bpm)
            n = 1
            while Fraction(240000) / (b * n) > Fraction(128, 100):
                n += 1
            self.assertGreaterEqual(Fraction(240000) / (b * n), 1)
            # 1つ小さい N だと 1.28ms を超える(= n が一番小さい)
            self.assertGreater(Fraction(240000) / (b * (n - 1)), Fraction(128, 100))
            for length in LENGTHS:
                with self.subTest(bpm=bpm, length=length):
                    units, _ = _measures(self._gen(S.MODE_PERF, bpm, length))
                    self.assertEqual(units[0], Fraction(1, n))

    def test_theory_mode_matches_old_frame_length(self):
        units, _ = _measures(self._gen(S.MODE_THEORY, "170", "1小節", "120"))
        self.assertEqual(units[0], Fraction(17, 2880))
        for bpm in BPMS:
            for fps in ("60", "120", "144", "240"):
                units, _ = _measures(self._gen(S.MODE_THEORY, bpm, "1小節", fps))
                self.assertEqual(units[0], Fraction(bpm) / (240 * int(fps)))

    def test_total_length_is_exact(self):
        for mode in S.MODES:
            for bpm in BPMS:
                for length in LENGTHS:
                    with self.subTest(mode=mode, bpm=bpm, length=length):
                        text = self._gen(mode, bpm, length)
                        units, total = _measures(text)
                        self.assertEqual(total, Fraction(length.replace("小節", "")))
                        # 端数の小節は約分済み
                        for u in units:
                            self.assertEqual(math.gcd(u.numerator, u.denominator), 1)

    def test_header_footer(self):
        for mode in S.MODES:
            lines = self._gen(mode, "170", "1小節").splitlines()
            self.assertEqual(lines[0], "// --- ストロボ開始 ---")
            self.assertIn("// --- ストロボ終了 ---", lines)
            self.assertEqual(lines[lines.index("// --- ストロボ終了 ---") + 1],
                             "#MEASURE 4/4")

    def test_fps_enabled_only_in_theory(self):
        for mode in S.MODES:
            self.d.cb_mode.setCurrentText(mode)
            self.assertEqual(self.d.cb_fps.isEnabled(), mode == S.MODE_THEORY)
            self.assertEqual(self.d.lbl_mode.text(), S.MODE_NOTES[mode])
        self.assertEqual(self.d.cb_fps.toolTip(), "理論値のときだけ使います")

    def test_fps_does_not_change_tnde_modes(self):
        for mode in (S.MODE_DRAW, S.MODE_PERF):
            a = self._gen(mode, "170", "1小節", "60")
            b = self._gen(mode, "170", "1小節", "240")
            self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
