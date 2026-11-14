"""スクロール欄の Enter は、置いてある命令を書き換える(行を増やさない)。"""
import unittest

from gui_support import editor_session, open_test_chart, real_qt_test


def _type_and_enter(spin, text):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    spin.setFocus()
    spin.selectAll()
    QTest.keyClicks(spin, text)
    QTest.keyClick(spin, Qt.Key_Return)


class CommandPanelEnterTests(unittest.TestCase):
    @real_qt_test
    def test_enter_emits_edit_only_while_editing(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent
        from neotja.command_panel import CommandPanel
        panel = CommandPanel()
        panel.show()
        try:
            for spin, text, want in ((panel.sp_hs, '2', 2.0),
                                     (panel.sp_hs_bpm, '240', 2.0)):
                panel.sp_bpm.setValue(120.0)
                edits, places = [], []
                panel.editCommand.connect(lambda n, v: edits.append((n, v)))
                panel.placeCommand.connect(lambda n, v: places.append((n, v)))
                panel.set_editing({"hs"})
                _type_and_enter(spin, text)
                self.assertEqual(len(edits), 1)
                self.assertEqual(edits[0][0], "SCROLL")
                self.assertAlmostEqual(edits[0][1], want, places=3)
                self.assertEqual(places, [])
                panel.set_editing(set())
                edits.clear()
                _type_and_enter(spin, text)
                self.assertEqual(edits, [])
                self.assertEqual(len(places), 1)
                panel.editCommand.disconnect()
                panel.placeCommand.disconnect()
            # Enter は欄で使い切る(親へ流さない)。
            ev = QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)
            panel.sp_hs.keyPressEvent(ev)
            self.assertTrue(ev.isAccepted())
        finally:
            panel.close()
            panel.deleteLater()

    @real_qt_test
    def test_enter_on_existing_scroll_rewrites_that_line(self):
        from PySide6.QtTest import QTest
        with editor_session() as (win, root):
            win.show()
            open_test_chart(win, root, '0,\n#SCROLL 1.5\n0,\n0,', bpm=150)
            pd = win.preview_dock
            pd.set_game_preview_visible(True)
            pd.show_chart_edit()
            chart, panel = pd.chart_edit, pd.command_panel
            chart.set_cursor_address(1, 0)
            pd._sync_command_panel()
            self.assertIn("hs", pd._panel_targets)
            _type_and_enter(panel.sp_hs, '2')
            QTest.qWait(30)
            text = win.editor.toPlainText()
            self.assertEqual(text.count('#SCROLL'), 1, text)
            self.assertIn('0,\n#SCROLL 2\n0,', text)
            # パネルの合わせ(30ms 待ち)が済む前に Enter が来ても増やさない。
            chart.set_cursor_address(0, 0)
            pd._sync_command_panel()
            chart.set_cursor_address(1, 0)
            self.assertNotIn("hs", panel._editing)
            _type_and_enter(panel.sp_hs, '3')
            QTest.qWait(30)
            text = win.editor.toPlainText()
            self.assertEqual(text.count('#SCROLL'), 1, text)
            self.assertIn('0,\n#SCROLL 3\n0,', text)


if __name__ == '__main__':
    unittest.main()
