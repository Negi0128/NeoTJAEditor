import unittest
from unittest.mock import patch

from gui_support import editor_session, open_test_chart


class HeaderOffsetTests(unittest.TestCase):
    def test_same_wave_header_change_updates_all_preview_offsets(self):
        with editor_session() as (win, root):
            open_test_chart(win, root)
            pd = win.preview_dock
            lead = pd._lead_sec
            with patch.object(pd, '_start_waveform_decode', wraps=pd._start_waveform_decode) as decode:
                win.apply_header_values({'OFFSET': '1.25'})
                self.assertEqual(win.header_values()['OFFSET'], '1.25')
                self.assertEqual(pd.spin_offset.value(), 1.25)
                self.assertAlmostEqual(pd.chart_edit.offset, 1.25 - lead)
                self.assertAlmostEqual(pd.chart_preview._offset, 1.25 - lead)
                self.assertAlmostEqual(pd.waveform.offset, 1.25 - lead)
                self.assertEqual(decode.call_count, 0)
                pd.spin_offset.setValue(-0.5)
                win._force_update()
                self.assertEqual(pd.spin_offset.value(), -0.5)
                self.assertAlmostEqual(pd.chart_edit.offset, -0.5 - lead)


if __name__ == '__main__':
    unittest.main()
