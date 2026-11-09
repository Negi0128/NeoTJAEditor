"""Focused real-Qt drag check. Never constructs MainWindow with user settings."""
import hashlib
import json
import os
import tempfile
import unittest
import wave
from fractions import Fraction
from pathlib import Path

from neotja import settings
from gui_support import real_qt_test, close_test_editor


class MoveGuiTests(unittest.TestCase):
    @real_qt_test
    def test_drag_across_meter_preserves_notes_and_undo(self):
        real = {settings.settings_path(), settings._primary_settings_path(), settings._fallback_settings_path()}
        def snapshot():
            return {str(p): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                    if p.exists() else None for p in real}
        before = snapshot()
        original_path = settings.settings_path
        original_fallback = settings._fallback_settings_path
        with tempfile.TemporaryDirectory(prefix='neotja-move-test-') as scratch:
            path = Path(scratch) / 'settings.json'
            cfg = settings.default_settings()
            cfg.update(master_volume=0.0, check_updates_on_startup=False, auto_save_enabled=False,
                       gpu_render=False)
            path.write_text(json.dumps(cfg), encoding='utf-8')
            settings.settings_path = lambda: path
            settings._fallback_settings_path = lambda: path
            from PySide6.QtCore import Qt, QPoint
            from PySide6.QtTest import QTest
            from PySide6.QtWidgets import QApplication
            from neotja.main_window import MainWindow
            self.app = QApplication.instance() or QApplication([])
            win = MainWindow(self.app)
            try:
                win.show()
                text = ('TITLE:移動の確認\nBPM:120\nCOURSE:oni\n#START\n'
                        + '0' * 20 + '2221,\n#MEASURE 3/4\n0,\n#MEASURE 4/4\n0,\n#END\n')
                with wave.open(str(Path(scratch) / 'test.wav'), 'wb') as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(44100)
                    wav.writeframes(b'\x00\x00' * 44100 * 10)
                text = 'WAVE:test.wav\n' + text
                tja = Path(scratch) / 'move.tja'
                tja.write_text(text, encoding='cp932')
                win._open_path(str(tja))
                pd = win.preview_dock
                pd.set_game_preview_visible(True)
                pd.show_chart_edit()
                QTest.qWait(250)
                ce = pd.chart_edit
                ce.set_grid(16)
                ce.set_follow_window(6.0)
                ce.set_selection({('note', Fraction(i, 24)) for i in range(20, 24)})
                ce.view_start = 3.0
                QTest.qWait(50)
                output = os.environ.get('NEOTJA_TEST_OUTPUT')
                if output:
                    Path(output).mkdir(parents=True, exist_ok=True)
                    pd.game_preview_window.grab().save(str(Path(output) / 'move-before.png'))
                notes = win.analyzer.build_preview_timeline(text, 5)['notes']
                start = ce._sec_to_x(notes[0][0] - ce.offset)
                target = ce._sec_to_x(ce._address_time(1, 1, 12))
                top, height = ce._row_rects()['note']
                y = top + (height - int(height * ce.WAVE_FRAC)) // 2
                QTest.mousePress(ce, Qt.LeftButton, pos=QPoint(start, y))
                QTest.mouseMove(ce, QPoint(target, y), delay=30)
                QTest.mouseRelease(ce, Qt.LeftButton, pos=QPoint(target, y))
                QTest.qWait(200)
                moved = win.editor.toPlainText()
                new_notes = win.analyzer.build_preview_timeline(moved, 5)['notes']
                self.assertNotEqual(text, moved)
                self.assertEqual([n[1] for n in new_notes], ['2', '2', '2', '1'])
                shifts = [b[0] - a[0] for a, b in zip(notes, new_notes)]
                self.assertGreater(shifts[0], 0)
                for shift in shifts:
                    self.assertAlmostEqual(shift, shifts[0], places=9)
                expected = {('note', ce._pos_of_time(n[0] - ce.offset)) for n in new_notes}
                self.assertEqual(ce._sel, expected)
                if output:
                    pd.game_preview_window.grab().save(str(Path(output) / 'move-after.png'))
                win.editor.undo()
                self.assertEqual(win.editor.toPlainText(), text)
            finally:
                close_test_editor(win)
                settings.settings_path = original_path
                settings._fallback_settings_path = original_fallback
                self.assertEqual(before, snapshot())


if __name__ == '__main__':
    unittest.main()

