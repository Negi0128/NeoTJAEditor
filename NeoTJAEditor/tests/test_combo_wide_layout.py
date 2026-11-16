# 4桁(1000〜)のコンボは字送り 0.86 倍・字の横幅 0.88 倍に詰め、中心は
# 詰める前と同じ(game_screen._combo_wide_layout)。利用者の報告 2026-11-16。
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _Sheet:
    def __init__(self, w):
        self._w = w

    def width(self):
        return self._w


class ComboWideLayoutTests(unittest.TestCase):
    def test_narrower_and_same_centre(self):
        from neotja import game_screen as G
        sheet = _Sheet(1000)                 # 1枠 100px
        adv = 0.8
        cw = 100 * G.COMBO_SCALE
        for combo in (1000, 1234, 9999, 12345):
            n = len(str(combo))
            step, gw = cw * adv, cw
            right = 300.0
            left0 = right - step * n
            span0 = step * (n - 1) + gw
            r2, adv2, sx = G._GameScreenBase._combo_wide_layout(
                sheet, combo, right, adv)
            self.assertAlmostEqual(adv2, adv * G.COMBO_WIDE_PITCH)
            self.assertEqual(sx, G.COMBO_WIDE_SCALE_X)
            step2, gw2 = cw * adv2, cw * sx
            left2 = r2 - step2 * n
            span2 = step2 * (n - 1) + gw2
            self.assertLess(span2, span0)
            self.assertAlmostEqual(left2 + span2 / 2, left0 + span0 / 2)

    def test_glyph_cache_keys_scale_x(self):
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QApplication
        from neotja import game_screen as G
        QApplication.instance() or QApplication([])

        class _Host:
            _digit_glyphs = G._GameScreenBase._digit_glyphs
            _dpr = 1.0

            def __init__(self):
                self._digit_cache = {}

        sheet = QPixmap(500, 60)
        sheet.fill()
        h = _Host()
        a = h._digit_glyphs(sheet, 10, 1, 0, 1.0)
        b = h._digit_glyphs(sheet, 10, 1, 0, 1.0, None, 0.88)
        self.assertEqual(a[0].width(), 51)
        self.assertEqual(b[0].width(), int(50 * 0.88) + 1)
        self.assertEqual(a[0].height(), b[0].height())


if __name__ == "__main__":
    unittest.main()
