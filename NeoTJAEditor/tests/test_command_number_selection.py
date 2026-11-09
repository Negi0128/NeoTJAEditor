import unittest


class CommandNumberSelectionTests(unittest.TestCase):
    def test_single_click_selects_the_whole_number_in_every_field(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt, QPoint
        from PySide6.QtTest import QTest
        from neotja.command_panel import CommandPanel
        self.app = QApplication.instance() or QApplication([])
        panel = CommandPanel()
        panel.show()
        try:
            for name in ('sp_bpm', 'sp_num', 'sp_den', 'sp_hs', 'sp_hs_bpm'):
                with self.subTest(name=name):
                    spin = getattr(panel, name)
                    QTest.mouseClick(spin.lineEdit(), Qt.LeftButton, pos=QPoint(7, 7))
                    QTest.qWait(10)
                    self.assertEqual(spin.lineEdit().selectedText(), spin.lineEdit().text())
        finally:
            panel.close()


if __name__ == '__main__':
    unittest.main()
