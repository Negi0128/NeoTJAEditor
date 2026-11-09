import unittest

from gui_support import editor_session, open_test_chart


class LongInputGridTests(unittest.TestCase):
    def test_roll_and_balloon_input_keep_grid_until_key_release(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with editor_session() as (win, root):
            win.show()
            for key, result_key in ((Qt.Key_R, 'rolls'), (Qt.Key_E, 'balloons')):
                with self.subTest(result_key=result_key):
                    open_test_chart(win, root, '0,\n0,')
                    pd = win.preview_dock
                    pd.set_game_preview_visible(True)
                    pd.show_chart_edit()
                    ce = pd.chart_edit
                    ce.set_grid(16)
                    ce.set_cursor_address(0, 4)
                    QTest.keyPress(ce, key)
                    for _ in range(3):
                        QTest.keyClick(ce, Qt.Key_Right)
                    time = ce.cursor_time()
                    ce.set_grid(24)
                    QTest.keyClick(ce, Qt.Key_Up)
                    QTest.keyClick(ce, Qt.Key_Down)
                    self.assertEqual(ce._grid, 16)
                    self.assertEqual(ce.cursor_time(), time)
                    QTest.keyRelease(ce, key)
                    result = win.analyzer.build_preview_timeline(win.editor.toPlainText(), 5)
                    self.assertEqual(len(result[result_key]), 1)
                    start, end = result[result_key][0][:2]
                    self.assertAlmostEqual(start, 0.5)
                    self.assertAlmostEqual(end, 0.875)
                    ce.set_grid(24)
                    self.assertEqual(ce._grid, 24)


if __name__ == '__main__':
    unittest.main()
