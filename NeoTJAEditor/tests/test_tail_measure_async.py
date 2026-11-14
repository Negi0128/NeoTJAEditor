"""末尾の空の小節を足す1音でも同期の解析をしない。色の塗り直しは変わった行だけ。"""
import random
import unittest
from unittest.mock import patch

from PySide6.QtGui import QTextCursor, QTextDocument

from gui_support import editor_session, open_test_chart, real_qt_test


def _formats(doc):
    out = []
    b = doc.firstBlock()
    while b.isValid():
        row = []
        for r in b.layout().formats():
            f = r.format
            row.append((r.start, r.length, f.foreground().color().name(),
                        f.fontWeight(), f.background().color().name()
                        if f.background().style() else None))
        out.append(sorted(row))
        b = b.next()
    return out


class TrackedHighlightTests(unittest.TestCase):
    """行が増えた/減った所が何か所あっても、全部塗り直したのと同じ色になる。"""

    @real_qt_test
    def test_matches_full_rehighlight(self):
        from neotja.highlighter import TJAHighlighter, compute_highlight_data
        from neotja.tja_analyzer import TJACourseAnalyzer
        an = TJACourseAnalyzer({})
        rnd = random.Random(1115)

        def data_for(text):
            return compute_highlight_data(text, an.parse_courses(text))

        body = []
        for i in range(120):
            body.append(rnd.choice(("1000,", "0000,", "1020,", "5000,", "0008,",
                                    "#SCROLL 1.5", "#BPMCHANGE 150", "// メモ",
                                    "7000,", "0080,", "12121212,")))
        text = ("TITLE:t\nBPM:120\nCOURSE:Oni\nBALLOON:5,5,5\n#START\n"
                + "\n".join(body) + "\n#END\n")
        doc = QTextDocument()
        doc.documentLayout()        # 編集画面と同じく、変化の知らせが出るように
        doc.setPlainText(text)
        hl = TJAHighlighter(doc)
        hl.apply_data(data_for(text))
        hl.rehighlight()
        for _round in range(300):
            tc = QTextCursor(doc)
            steps = rnd.randrange(1, 4)
            for k in range(steps):
                n = doc.characterCount() - 1
                a = rnd.randrange(0, n)
                b = min(n, a + rnd.randrange(0, 8))
                ins = "".join(rnd.choice("0125,\n#/ ") for _ in range(rnd.randrange(0, 6)))
                if k == 0:
                    tc.beginEditBlock()
                else:
                    tc.joinPreviousEditBlock()
                tc.setPosition(a)
                tc.setPosition(b, QTextCursor.KeepAnchor)
                tc.insertText(ins)
                tc.endEditBlock()
            now = doc.toPlainText()
            hl.apply_data(data_for(now))
            got = _formats(doc)
            ref_doc = QTextDocument()
            ref_doc.setPlainText(now)
            ref = TJAHighlighter(ref_doc)
            ref.data = hl.data
            ref.rehighlight()
            self.assertEqual(got, _formats(ref_doc), _round)


class TailMeasureAsyncTests(unittest.TestCase):
    @real_qt_test
    def test_tail_measure_does_not_force_update(self):
        with editor_session() as (win, root):
            open_test_chart(win, root, '1000,\n0000,')
            before = win.editor.toPlainText()
            with patch.object(win, '_force_update') as force:
                res = win._apply_chart_op(
                    {"kind": "key", "a": (1, 0), "grid": 16, "char": "1"})
                self.assertIsNotNone(res)
                self.assertEqual(force.call_count, 0)
            text = win.editor.toPlainText()
            # 打った小節の後ろに空の小節が1つ足されている
            self.assertEqual(text.count(","), before.count(",") + 1)
            self.assertTrue(win._heavy_timer.isActive())
            # Undo は1回で両方戻る
            win.editor.undo()
            self.assertEqual(win.editor.toPlainText(), before)


if __name__ == "__main__":
    unittest.main()
