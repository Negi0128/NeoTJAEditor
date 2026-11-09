import unittest


class VisibleBpmStepTests(unittest.TestCase):
    def test_unchanged_visible_bpm_does_not_repaint(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication
        from neotja.command_panel import CommandPanel
        self.app = QApplication.instance() or QApplication([])
        panel = CommandPanel()
        try:
            panel.set_values(bpm=150, scroll=1.5)
            with patch.object(panel.sp_hs_bpm, 'setValue', wraps=panel.sp_hs_bpm.setValue) as put:
                for _ in range(10):
                    panel.set_values(bpm=150, scroll=1.5)
                put.assert_not_called()
                panel.set_values(bpm=160, scroll=1.5)
                self.assertEqual(panel.sp_hs_bpm.value(), 240)
                self.assertEqual(put.call_count, 1)
        finally:
            panel.close()

    def test_one_bpm_steps_keep_precision_and_emit_scroll(self):
        from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSpinBox
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from neotja.command_panel import CommandPanel
        self.app = QApplication.instance() or QApplication([])
        panel = CommandPanel()
        panel.show()
        values = []
        panel.placeCommand.connect(lambda name, value: values.append((name, value)))
        try:
            for base in (120, 150, 4000):
                with self.subTest(base=base):
                    panel.sp_hs_bpm.clearFocus()
                    panel.set_values(bpm=base, scroll=1)
                    values.clear()
                    spin = panel.sp_hs_bpm
                    option = QStyleOptionSpinBox()
                    spin.initStyleOption(option)
                    rect = spin.style().subControlRect(QStyle.CC_SpinBox, option,
                                                       QStyle.SC_SpinBoxUp, spin)
                    QTest.mouseClick(spin, Qt.LeftButton, pos=rect.center())
                    QTest.qWait(10)
                    self.assertEqual(spin.value(), base + 1)
                    self.assertEqual(len(values), 1)
                    self.assertEqual(values[0][0], 'SCROLL')
                    self.assertAlmostEqual(values[0][1] * base, base + 1, places=7)
        finally:
            panel.close()


if __name__ == '__main__':
    unittest.main()
