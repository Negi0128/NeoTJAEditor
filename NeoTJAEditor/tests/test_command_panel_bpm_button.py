import os
import unittest
from pathlib import Path

from gui_support import editor_session, open_test_chart, real_qt_test


class CommandPanelBpmButtonTests(unittest.TestCase):
    """命令パネルの BPM 欄の「追加」(利用者の報告 2026-11-13「BPM追加ボタンが
    右上のパネルから消えちゃった」)。"""

    @real_qt_test
    def test_bpm_add_button_places_and_changes_at_cursor(self):
        from PySide6.QtCore import QRect, Qt
        from PySide6.QtTest import QTest
        with editor_session() as (win, root):
            win.show()
            open_test_chart(win, root, '0,\n0,\n0,', bpm=150)
            pd = win.preview_dock
            pd.set_game_preview_visible(True)
            pd.show_chart_edit()
            chart, panel = pd.chart_edit, pd.command_panel
            box, btn = panel._boxes['bpm']
            self.assertIsNotNone(btn)
            for width in (1920, 1400):
                win.resize(width, 1000)
                QTest.qWait(80)
                pd._place_command_panel(True)
                QTest.qWait(30)
                self.assertTrue(panel.isVisible(), width)
                self.assertTrue(btn.isVisible(), width)
                # ボタンが枠の中に、枠がパネルの中におさまっている。
                inner = QRect(btn.mapTo(box, btn.rect().topLeft()), btn.size())
                self.assertTrue(box.rect().contains(inner), (width, inner, box.rect()))
                outer = QRect(box.mapTo(panel, box.rect().topLeft()), box.size())
                self.assertTrue(panel.rect().contains(outer), (width, outer))
                # 拍子記号の「追加」と同じ大きさ。
                self.assertEqual(btn.size(), panel._boxes['measure'][1].size())
            # 今と同じ BPM でも、押せばカーソルの所へ置ける(欄をいじる形では
            # 値が変わらないので入らない)。
            chart.set_cursor_address(1, 0)
            pd._sync_command_panel()
            QTest.qWait(30)
            self.assertEqual(btn.text(), '追加')
            QTest.mouseClick(btn, Qt.LeftButton)
            QTest.qWait(30)
            self.assertIn('0,\n#BPMCHANGE 150\n0,', win.editor.toPlainText())
            # 置いた所では「変更」になり、押しても増えずにその行を書き換える。
            pd._sync_command_panel()
            QTest.qWait(30)
            self.assertEqual(btn.text(), '変更')
            panel._syncing = True
            panel.sp_bpm.setValue(180.0)
            panel._syncing = False
            QTest.mouseClick(btn, Qt.LeftButton)
            QTest.qWait(30)
            text = win.editor.toPlainText()
            self.assertEqual(text.count('#BPMCHANGE'), 1, text)
            self.assertIn('0,\n#BPMCHANGE 180\n0,', text)
            output = os.environ.get('NEOTJA_TEST_OUTPUT')
            if output:
                panel.grab().save(str(Path(output) / 'command-panel-bpm.png'))


if __name__ == '__main__':
    unittest.main()
