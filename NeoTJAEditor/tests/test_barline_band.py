import os
import unittest
from pathlib import Path

from gui_support import editor_session, open_test_chart, real_qt_test


class BarlineBandTests(unittest.TestCase):
    @real_qt_test
    def test_band_ends_at_on_command_inside_a_measure(self):
        from PySide6.QtTest import QTest
        with editor_session() as (win, root):
            win.show()
            open_test_chart(win, root, '1111,\n#BARLINEOFF\n11\n#BARLINEON\n11,\n1111,')
            pd = win.preview_dock
            ce = pd.chart_edit
            self.assertEqual(ce._barline_raw, [(2.0, 3.0)])
            self.assertEqual(ce._barline_audio, [(2.0 - ce.offset, 3.0 - ce.offset)])
            pd.set_game_preview_visible(True)
            pd.show_chart_edit()
            ce.set_follow_window(6)
            ce.view_start = 3
            QTest.qWait(100)
            output = os.environ.get('NEOTJA_TEST_OUTPUT')
            if output:
                pd.game_preview_window.grab().save(str(Path(output) / 'barline-band.png'))


if __name__ == '__main__':
    unittest.main()
