import os
import unittest
from pathlib import Path

from gui_support import editor_session, open_test_chart, real_qt_test


class LyricBandTests(unittest.TestCase):
    @real_qt_test
    def test_drag_stop_insert_move_remove_and_undo(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        with editor_session() as (win, root):
            win.show()
            original = open_test_chart(win, root,
                '#LYRIC 最初の歌詞\n1111,\n1111,\n#LYRIC 次の歌詞\n1111,\n#LYRIC\n1111,')
            dock = win.preview_dock
            dock.set_game_preview_visible(True)
            dock.show_chart_edit()
            ce = dock.chart_edit
            ce.set_grid(16)
            ce.set_follow_window(8)
            ce.scroll_view_to(-ce.offset, animate=False)
            QTest.qWait(250)
            top, h = ce._row_rects()['lyric']
            def lyrics():
                return win.analyzer.build_preview_timeline(win.editor.toPlainText())['lyrics']
            def drag(start, dest):
                y = top + h // 2
                p = QPoint(ce._sec_to_x(start - ce.offset) - 1, y)
                q = QPoint(ce._sec_to_x(dest - ce.offset), y)
                QTest.mousePress(ce, Qt.LeftButton, pos=p)
                self.assertIsNotNone(ce._lyric_drag)
                QTest.mouseMove(ce, q, delay=20)
                QTest.mouseRelease(ce, Qt.LeftButton, pos=q)
                QTest.qWait(300)
            drag(4, 3)
            self.assertEqual(lyrics(), [(0, '最初の歌詞'), (3, ''), (4, '次の歌詞'), (6, '')])
            output = os.environ.get('NEOTJA_LYRIC_CAPTURE')
            if output:
                from PySide6.QtGui import QPainter, QPixmap
                pm = QPixmap(ce.size())
                painter = QPainter(pm)
                try:
                    ce.paint_pane(painter)
                finally:
                    painter.end()
                self.assertTrue(pm.save(str(Path(output))))
            drag(3, 3.5)
            self.assertEqual(lyrics()[1], (3.5, ''))
            drag(3.5, 5)
            self.assertEqual(lyrics(), [(0, '最初の歌詞'), (4, '次の歌詞'), (6, '')])
            win.editor.undo()
            self.assertEqual(lyrics()[1], (3.5, ''))
            win.editor.undo()
            win.editor.undo()
            self.assertEqual(win.editor.toPlainText(), original)
            # 最後の歌詞は停止行を曲末まで引くと自然終了になる。
            QTest.qWait(300)
            drag(6, 8)
            self.assertEqual(lyrics(), [(0, '最初の歌詞'), (4, '次の歌詞')])
            win.editor.undo()
            self.assertEqual(win.editor.toPlainText(), original)


if __name__ == '__main__':
    unittest.main()
