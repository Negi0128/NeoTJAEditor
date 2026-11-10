import os
import unittest
from pathlib import Path

from gui_support import editor_session, open_test_chart, real_qt_test


class CommandPanelScrollTests(unittest.TestCase):
    @real_qt_test
    def test_typing_requires_enter_and_targets_current_cursor(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        with editor_session() as (win, root):
            win.show()
            open_test_chart(win, root, '0,\n0,\n0,', bpm=150)
            pd = win.preview_dock
            pd.set_game_preview_visible(True)
            pd.show_chart_edit()
            chart, panel = pd.chart_edit, pd.command_panel
            chart.set_cursor_address(0, 0)
            pd._sync_command_panel()
            spin = panel.sp_hs
            spin.setFocus()
            spin.selectAll()
            QTest.keyClicks(spin, '2')
            chart.setFocus()
            chart.set_cursor_address(1, 0)
            QTest.qWait(50)
            self.assertNotIn('#SCROLL', win.editor.toPlainText())
            spin.setFocus()
            spin.selectAll()
            QTest.keyClicks(spin, '3')
            QTest.keyClick(spin, Qt.Key_Return)
            self.assertIn('0,\n#SCROLL 3\n0,', win.editor.toPlainText())
            chart.set_cursor_address(2, 0)
            QTest.keyClick(spin, Qt.Key_Enter)
            self.assertIn('#SCROLL 3\n0,\n#SCROLL 3\n0,', win.editor.toPlainText())

    @real_qt_test
    def test_spin_buttons_apply_new_and_existing_scroll_without_add(self):
        from PySide6.QtWidgets import QStyle, QStyleOptionSpinBox
        from PySide6.QtCore import Qt, QPoint
        from PySide6.QtTest import QTest
        with editor_session() as (win, root):
            win.show()
            open_test_chart(win, root, '0,\n0,', bpm=150)
            pd = win.preview_dock
            pd.set_game_preview_visible(True)
            pd.show_chart_edit()
            pd.chart_edit.set_cursor_address(0, 0)
            pd._sync_command_panel()
            panel = pd.command_panel
            def step_up(spin):
                option = QStyleOptionSpinBox()
                spin.initStyleOption(option)
                rect = spin.style().subControlRect(QStyle.CC_SpinBox, option,
                                                   QStyle.SC_SpinBoxUp, spin)
                QTest.mouseClick(spin, Qt.LeftButton, pos=rect.center())
                QTest.qWait(40)
            step_up(panel.sp_hs)
            self.assertIn('#SCROLL 1.05', win.editor.toPlainText())
            step_up(panel.sp_hs)
            self.assertIn('#SCROLL 1.1\n', win.editor.toPlainText())
            self.assertEqual(win.editor.toPlainText().count('#SCROLL'), 1)
            previous = panel.sp_hs_bpm.value()
            step_up(panel.sp_hs_bpm)
            self.assertEqual(panel.sp_hs_bpm.value(), previous + 1)
            text = win.editor.toPlainText()
            self.assertEqual(text.count('#SCROLL'), 1)
            value = float(next(line.split()[1] for line in text.splitlines()
                               if line.startswith('#SCROLL ')))
            self.assertAlmostEqual(value * 150, previous + 1, places=3)
            QTest.mouseClick(panel.sp_hs_bpm.lineEdit(), Qt.LeftButton, pos=QPoint(8, 8))
            QTest.qWait(10)
            self.assertEqual(panel.sp_hs_bpm.lineEdit().selectedText(), panel.sp_hs_bpm.text())
            output = os.environ.get('NEOTJA_TEST_OUTPUT')
            if output:
                panel.grab().save(str(Path(output) / 'command-panel.png'))


if __name__ == '__main__':
    unittest.main()
