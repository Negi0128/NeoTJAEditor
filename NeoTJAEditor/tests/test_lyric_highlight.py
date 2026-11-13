"""左の TJA 本文で #LYRIC の行だけ緑になる(利用者の指定 2026-11-13)。"""
import unittest

from PySide6.QtGui import QColor, QTextDocument
from PySide6.QtWidgets import QApplication

from neotja import theme
from neotja.highlighter import TJAHighlighter, compute_highlight_data

TEXT = ("TITLE:x\n#START\n#LYRIC 歌う所\n1111,\n#BPMCHANGE 200\n"
        "#LYRIC\n1111,\n#END\n")


def _fg(doc, line):
    """その行(1始まり)の頭の文字色。"""
    block = doc.findBlockByNumber(line - 1)
    for r in block.layout().formats():
        if r.start == 0:
            return r.format.foreground().color().name()
    return None


class LyricHighlightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _run(self, name):
        saved = dict(theme.COLORS)
        try:
            theme.COLORS.update(theme.THEMES[name])
            doc = QTextDocument()
            doc.setPlainText(TEXT)
            hl = TJAHighlighter(doc)
            hl.rebuild_formats()
            hl.apply_data(compute_highlight_data(TEXT, []))
            hl.rehighlight()
            return {ln: _fg(doc, ln) for ln in (3, 5, 6)}
        finally:
            theme.COLORS.update(saved)

    def test_lyric_green_both_themes(self):
        for name in ("dark", "light"):
            got = self._run(name)
            want_lyric = QColor(theme.THEMES[name]["lyric"]).name()
            want_cmd = QColor(theme.THEMES[name]["cmd"]).name()
            with self.subTest(theme=name):
                self.assertEqual(got[3], want_lyric)   # 歌詞あり
                self.assertEqual(got[6], want_lyric)   # 文字の無い #LYRIC
                self.assertEqual(got[5], want_cmd)     # ほかの命令は今までどおり
        self.assertEqual(theme.THEMES["dark"]["lyric"], "#7fd6a0")
        self.assertEqual(theme.THEMES["light"]["lyric"], "#198754")

    def test_signature_sees_lyric(self):
        data = compute_highlight_data(TEXT, [])
        self.assertIn(3, data.lyric_lines)
        self.assertNotIn(5, data.lyric_lines)
        self.assertNotEqual(TJAHighlighter._line_signature(data, 3)[:2],
                            TJAHighlighter._line_signature(data, 5)[:2])


if __name__ == "__main__":
    unittest.main()
